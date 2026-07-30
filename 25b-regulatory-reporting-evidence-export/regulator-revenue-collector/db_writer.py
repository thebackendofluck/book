# Companion code for "The Backend of Luck" - Chapter 25b, Regulatory Reporting and Evidence Export.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Optional Postgres writer for the regulator-revenue-collector.

If DATABASE_URL is set, run.py calls write_snapshot_to_db() after producing
the JSON snapshot. The collector's primary output remains the JSON files;
the database is an additive time-series sink that powers the dashboard.

Schema lives in dashboard-stack/db/schema.sql and is applied automatically
by the postgres container's docker-entrypoint-initdb.d on first start.
"""
from __future__ import annotations

import json
import os
from typing import Iterable

import psycopg

from models import StateSnapshot


def write_snapshot_to_db(snap: StateSnapshot, dsn: str | None = None) -> None:
    """Insert one collection_runs row + N report_snapshots rows. Idempotent per run."""
    dsn = dsn or os.environ.get("DATABASE_URL")
    if not dsn:
        return  # silently skip if not configured — JSON output still happens

    # psycopg uses libpq; accept the libpq URL form. Strip SQLAlchemy prefix.
    if dsn.startswith("postgresql+asyncpg://"):
        dsn = "postgresql://" + dsn.split("://", 1)[1]
    if dsn.startswith("postgresql+psycopg://"):
        dsn = "postgresql://" + dsn.split("://", 1)[1]

    success_count = sum(1 for r in snap.reports if not r.fetch_error)
    failure_count = len(snap.reports) - success_count
    total_bytes = sum(r.size_bytes for r in snap.reports if not r.fetch_error)

    with psycopg.connect(dsn, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO collection_runs
                  (state, regulator, source_url, collected_at,
                   report_count, success_count, failure_count, total_bytes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    snap.state,
                    snap.regulator,
                    snap.source_url,
                    snap.collected_at,
                    len(snap.reports),
                    success_count,
                    failure_count,
                    total_bytes,
                ),
            )
            run_id = cur.fetchone()[0]

            for r in snap.reports:
                cur.execute(
                    """
                    INSERT INTO report_snapshots
                      (run_id, state, operator, vertical, cadence, format,
                       source_url, sha256, size_bytes, fetch_error,
                       summary, retrieved_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                    ON CONFLICT (run_id, state, operator, cadence, format)
                    WHERE sha256 IS NOT NULL DO NOTHING
                    """,
                    (
                        run_id,
                        snap.state,
                        r.operator,
                        r.vertical,
                        r.cadence,
                        r.format,
                        r.source_url,
                        r.sha256,
                        r.size_bytes,
                        r.fetch_error,
                        json.dumps([s.model_dump() for s in r.summary]),
                        r.retrieved_at,
                    ),
                )
        conn.commit()


def write_all(snapshots: Iterable[StateSnapshot]) -> int:
    """Convenience helper: write a batch and return how many runs were inserted."""
    n = 0
    for s in snapshots:
        try:
            write_snapshot_to_db(s)
            n += 1
        except Exception as e:  # noqa: BLE001 — DB failure must never sink the JSON output
            import structlog
            structlog.get_logger().warning("db write failed", state=s.state, err=str(e))
    return n
