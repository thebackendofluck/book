# Companion code for "The Backend of Luck" - Chapter 36, Financial Operations.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Balance mismatch fix tool -- standalone CLI script.

Fetches failed Kafka messages from Elasticsearch and re-publishes them
to repair balance mismatches caused by lost Kafka messages.

Usage:
    python main.py \\
        --from-date 2024-01-01T00:00:00+00:00 \\
        --to-date   2024-01-08T00:00:00+00:00 \\
        --user-ids  12345,67890 \\
        [--dry-run]

Environment variables:
    ELASTICSEARCH_URL        Kibana/ES URL (default: https://localhost:9200)
    KAFKA_BOOTSTRAP_SERVERS  Kafka bootstrap (default: localhost:9092)
    DATABASE_URL             PostgreSQL connection string
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

import structlog
from sqlalchemy import create_engine

from .service import (
    BalanceMismatchProcessor,
    ElasticsearchClient,
    KafkaRepublisher,
    UserRepository,
)

log = structlog.get_logger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:pass@localhost/platform")
ELASTICSEARCH_URL = os.getenv("ELASTICSEARCH_URL", "https://localhost:9200")
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Balance mismatch fix -- re-publishes failed Kafka messages")
    parser.add_argument(
        "--from-date",
        required=True,
        help="Start of date range (ISO 8601 with timezone, e.g. 2024-01-01T00:00:00+00:00)",
    )
    parser.add_argument(
        "--to-date",
        required=True,
        help="End of date range (ISO 8601 with timezone)",
    )
    parser.add_argument(
        "--user-ids",
        required=True,
        help="Comma-separated list of user IDs to process",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and parse messages but do not publish to Kafka",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    from_dt = datetime.fromisoformat(args.from_date)
    to_dt = datetime.fromisoformat(args.to_date)
    user_ids = [int(uid.strip()) for uid in args.user_ids.split(",") if uid.strip()]

    log.info(
        "balance_mismatch_fix.init",
        from_dt=from_dt.isoformat(),
        to_dt=to_dt.isoformat(),
        user_ids=user_ids,
        dry_run=args.dry_run,
    )

    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    elastic = ElasticsearchClient(kibana_host=ELASTICSEARCH_URL)
    user_repo = UserRepository(engine)
    kafka = KafkaRepublisher(KAFKA_BOOTSTRAP)

    processor = BalanceMismatchProcessor(
        elastic_client=elastic,
        user_repository=user_repo,
        kafka_republisher=kafka,
        dry_run=args.dry_run,
    )
    processor.run(from_dt=from_dt, to_dt=to_dt, user_id_list=user_ids)

    log.info("balance_mismatch_fix.complete")
    sys.exit(0)


if __name__ == "__main__":
    main()
