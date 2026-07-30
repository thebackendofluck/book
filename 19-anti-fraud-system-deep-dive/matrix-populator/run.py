# Companion code for "The Backend of Luck" - Chapter 19, Anti-Fraud System Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
run.py — Entry point for the matrix-populator batch job.

Mirrors Run.scala from the matrix-populator.

Usage:
    python run.py [-from YYYYMMDD] [-to YYYYMMDD] [-env <env>] [-score <id>]

Environment variables:
    DB_URL_TEST / DB_URL_PROD   PostgreSQL connection string
                                (e.g. postgresql://user:pass@host:5432/dbname)
    DB_USER, DB_PASSWORD        Override credentials inline if not in URL.

The job:
  1. Loads all scoring rules from matrix_score_type (excluding 'daily-stats')
  2. For each rule, generates and executes a windowed-aggregation INSERT
  3. Skips rules that require external API calls (paymentOptionsCreated)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, time

import psycopg
import structlog

from model import CalculateOn, load_matrix_score_types
from populator import Populator

structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.INFO))
log = structlog.get_logger()

DATE_FMT = "%Y%m%d"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Matrix Populator — risk score batch job")
    parser.add_argument("-from", dest="from_date", help="Start date YYYYMMDD")
    parser.add_argument("-to",   dest="to_date",   help="End date YYYYMMDD")
    parser.add_argument("-env",  dest="env",        default="test", help="Environment name (test|prod)")
    parser.add_argument("-score", dest="score",     type=int,       help="Run only this score type ID")
    return parser.parse_args()


def get_connection(env: str) -> psycopg.Connection:
    url = os.environ.get(f"DB_URL_{env.upper()}", "")
    user = os.environ.get("DB_USER", "")
    password = os.environ.get("DB_PASSWORD", "")
    if not url:
        raise RuntimeError(f"DB_URL_{env.upper()} environment variable not set")
    # If credentials aren't already encoded in the URL, libpq accepts a
    # keyword/value style fallback via separate kwargs.
    if user and "://" in url and "@" not in url.split("://", 1)[1]:
        return psycopg.connect(url, user=user, password=password)
    return psycopg.connect(url)


def main() -> None:
    args = parse_args()

    from_dt: datetime | None = None
    to_dt:   datetime | None = None

    if args.from_date:
        from_dt = datetime.combine(datetime.strptime(args.from_date, DATE_FMT).date(), time.min)
    if args.to_date:
        to_dt = datetime.combine(datetime.strptime(args.to_date, DATE_FMT).date(), time(23, 59, 59))

    conn = get_connection(args.env)

    scores = load_matrix_score_types(conn)
    log.info("loaded score types", count=len(scores))

    for s in scores:
        if args.score is not None and s.id != args.score:
            continue

        # Skip scores requiring external payment provider API calls
        if (s.calculate_on == CalculateOn.DEPOSIT_CONFIRMED
                and "paymentOptionsCreated" in s.condition):
            log.info("skipping score — requires payment provider API",
                     score_id=s.id, label=s.label)
            continue

        log.info("processing score", score_id=s.id, label=s.label)
        p = Populator(conn, s)
        p.populate(from_dt, to_dt)

    conn.close()
    log.info("matrix-populator finished")


if __name__ == "__main__":
    main()
