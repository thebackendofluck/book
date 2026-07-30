# Companion code for "The Backend of Luck" - Chapter 37, Marketing Technology and CRM Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Suppression sync -- standalone CLI script.

Synchronises email suppression lists from SilverPop (Acoustic) to ExactTarget
(Salesforce Marketing Cloud).

Usage:
    python main.py <sp_settings_id>[,<id2>] \\
        [-dateStart YYYYMMDD] \\
        [-dateEnd YYYYMMDD]

Environment variables:
    DATABASE_URL  PostgreSQL connection string
    ET_FTP_HOST   ExactTarget FTP host
    ET_FTP_USER   ExactTarget FTP username
    ET_FTP_PASS   ExactTarget FTP password
    ET_FTP_DIR    ExactTarget FTP import directory
    SP_CSV_DIR    Local directory for SilverPop CSV downloads
    ET_CSV_DIR    Local directory for ExactTarget CSV output
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta

import structlog
from sqlalchemy import create_engine

from .models import AppConfig
from .service import SPSettingsDAO, SuppressionSyncProcessor

log = structlog.get_logger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:pass@localhost/platform")
DATE_FORMAT = "%Y%m%d"


def parse_date(s: str) -> datetime:
    return datetime.strptime(s, DATE_FORMAT)


def main() -> None:
    args = sys.argv[1:]

    if not args:
        print("Usage: suppression-sync <id[,id2,...]> [-dateStart YYYYMMDD] [-dateEnd YYYYMMDD]")
        sys.exit(1)

    sp_settings_ids = [int(x) for x in args[0].split(",") if x]

    # Parse optional date arguments
    date_start: datetime
    date_end: datetime

    if "-dateStart" in args:
        idx = args.index("-dateStart")
        date_start = parse_date(args[idx + 1])
    else:
        date_start = datetime.combine(date.today() - timedelta(days=1), datetime.min.time())

    if "-dateEnd" in args:
        idx = args.index("-dateEnd")
        date_end = parse_date(args[idx + 1])
    else:
        date_end = datetime.now()

    log.info(
        "suppression_sync.init",
        settings_ids=sp_settings_ids,
        date_start=date_start.isoformat(),
        date_end=date_end.isoformat(),
    )

    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    config = AppConfig(
        sp_csv_directory=os.getenv("SP_CSV_DIR", "/tmp/sp_csv"),
        et_csv_directory=os.getenv("ET_CSV_DIR", "/tmp/et_csv"),
        et_ftp_host=os.environ["ET_FTP_HOST"],
        et_ftp_username=os.environ["ET_FTP_USER"],
        et_ftp_password=os.environ["ET_FTP_PASS"],
        et_ftp_import_dir=os.getenv("ET_FTP_DIR", "/import"),
    )

    dao = SPSettingsDAO(engine)
    processor = SuppressionSyncProcessor(dao, config)
    processor.run(sp_settings_ids, date_start, date_end)

    log.info("suppression_sync.done")
    sys.exit(0)


if __name__ == "__main__":
    main()
