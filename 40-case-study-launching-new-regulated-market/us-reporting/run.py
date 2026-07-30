# Companion code for "The Backend of Luck" - Chapter 40, Case Study.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# US Regulatory Report Runner
# Source: Production casino platform (sanitized)
# Chapter 40 - Case Study
#
# Entry point for US regulatory report generation.
# Parses CLI arguments for date, jurisdiction, timezone, and casino day
# boundaries, then delegates to MainReportingSuite.
#
# Usage:
#   python run.py -date 20200122 -jurisdiction PA -casinoDay 0600 -tz America/New_York
# =============================================================================

from __future__ import annotations

import logging
import sys
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from main_reporting_suite import MainReportingSuite
from kambi_supplier import KambiSupplier
from models import FailedStep

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

DATE_FMT   = "%Y%m%d"
US_DATE_FMT = "%m%d%Y"


def parse_args(argv: list[str]) -> dict[str, str]:
    if len(argv) % 2 != 0:
        _usage()
    return {argv[i].lstrip("-"): argv[i + 1] for i in range(0, len(argv), 2)}


def _usage() -> None:
    msg = (
        "Usage: python run.py [-date <yyyymmdd>] [-env <env>] [-suite <name>] "
        "[-jurisdiction <name>] [-template <path>] [-casinoDay <HHmm>] [-tz <tzdata-name>]\n"
        "Example: python run.py -date 20200122 -jurisdiction PA "
        "-casinoDay 0600 -tz America/New_York"
    )
    logger.error(msg)
    sys.exit(1)


def main() -> None:
    params = parse_args(sys.argv[1:])
    logger.debug("Parameters: %s", params)

    # Parse reporting date (default: yesterday)
    from_date_str = params.get("from") or params.get("date")
    if from_date_str:
        from_date = datetime.strptime(from_date_str, DATE_FMT)
    else:
        from_date = datetime.combine(date.today() - __import__("datetime").timedelta(days=1), time(0, 0))

    # US jurisdictions typically use Eastern Time
    tz_name = params.get("tz", "America/New_York")
    timezone = ZoneInfo(tz_name)

    # Casino day start (many US states: 06:00 AM local)
    casino_day_str = params.get("casinoDay", "0600")
    casino_day_start = time(int(casino_day_str[:2]), int(casino_day_str[2:]))

    jurisdiction = params.get("jurisdiction", "PA")
    template_path = params.get("template", f"acmesports_template_{jurisdiction}.xlsx")
    env = params.get("env", "stage")

    us_date = from_date.strftime(US_DATE_FMT)

    logger.info(
        "Running report: date=%s jurisdiction=%s gaming_day_start=%s tz=%s env=%s",
        from_date.date(), jurisdiction, casino_day_start, tz_name, env,
    )

    # Build supplier list
    suppliers = [KambiSupplier(casino_day_start, timezone)]

    # Database connection (replace with real DSN from config)
    db = None  # In production: DatabaseConnection(env)

    suite = MainReportingSuite(suppliers)

    suite_name = params.get("suite", "main")
    if suite_name == "main":
        csv_path  = f"AcmeSports_RegulatoryReport_{jurisdiction}_{us_date}-RAW"
        xlsx_path = f"AcmeSports_RegulatoryReport_{jurisdiction}_{us_date}.xlsx"
        result = suite.run(db, from_date, casino_day_start, timezone, csv_path, xlsx_path, template_path, jurisdiction)
        if isinstance(result, FailedStep):
            logger.error("Report failed [%s]: %s", result.step_name, result.details)
            sys.exit(1)
        else:
            logger.info("Report generated correctly")
    else:
        logger.error("Unknown suite: %s", suite_name)
        sys.exit(1)

    logger.info("Finished")


if __name__ == "__main__":
    main()
