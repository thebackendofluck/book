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
Batch withdrawal processor -- standalone script entry point.

Runs as a scheduled batch process (cron job / Kubernetes CronJob).
Processes self-excluded players with a positive balance and exactly one
withdrawal option, returning their funds automatically.

Usage:
    python main.py [--cutoff-date YYYY-MM-DD] [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import structlog
from sqlalchemy import create_engine

if __package__:
    from .models import UserWithdraw, WithdrawOptionsResponse  # ty: ignore[unresolved-import]
    from .service import PlatformServiceClient, WithdrawDAO, WithdrawProcessor  # ty: ignore[unresolved-import]
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from models import UserWithdraw, WithdrawOptionsResponse
    from service import PlatformServiceClient, WithdrawDAO, WithdrawProcessor

log = structlog.get_logger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://user:pass@localhost:5432/platform")
PLATFORM_SERVICE_URL = os.getenv(
    "PLATFORM_SERVICE_URL",
    "http://platform.internal.acmetocasino.com/platform/usergateway",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch withdrawal processor for self-excluded players")
    parser.add_argument(
        "--cutoff-date",
        default=(date.today() - timedelta(days=1)).isoformat(),
        help="Only process self-exclusions that started before this date (default: yesterday)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Query eligible players without performing withdrawals",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cutoff = datetime.fromisoformat(args.cutoff_date)

    log.info(
        "withdraw_processor.init",
        cutoff_date=args.cutoff_date,
        dry_run=args.dry_run,
    )

    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    dao = WithdrawDAO(engine)

    if args.dry_run:
        players = dao.list_players_to_withdraw(cutoff_date=cutoff)
        log.info("dry_run.eligible_players", count=len(players))
        for p in players:
            log.info(
                "dry_run.player",
                user_id=p.user_id,
                balance=p.balance,
                currency=p.currency,
                brand=p.brand_name,
            )
        sys.exit(0)

    platform = PlatformServiceClient(PLATFORM_SERVICE_URL)
    processor = WithdrawProcessor(dao, platform)
    total, succeeded, skipped = processor.run(cutoff_date=cutoff)

    log.info(
        "withdraw_processor.complete",
        total=total,
        succeeded=succeeded,
        skipped_multi_option=skipped,
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
