#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 20b, OpenBao Operations.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Validate a logrotate configuration against a jurisdiction's minimum
retention requirement for the OpenBao audit log.

The jurisdiction table is derived from the table in chapter 20b. If your
compliance team has adjusted the numbers (which they sometimes will), edit
MINIMUM_RETENTION_DAYS directly -- the script deliberately keeps the policy
as code so it can be diffed, reviewed and code-owned.

Usage:
    validate-retention-policy.py <jurisdiction> [--config PATH]

Exits 0 if the configured `rotate` count (times the rotation period)
meets or exceeds the jurisdiction's minimum. Exits 1 if not, with an
explanation suitable for a CI log.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

# Binding minima from the chapter 20b retention matrix. These are
# conservative -- where a jurisdiction has both an AML and a gambling rule
# with different retention periods, we encode the longer of the two.
MINIMUM_RETENTION_DAYS: dict[str, int] = {
    "pci-dss":        365,    # PCI DSS 4.0 Req 10.5.1 / 10.7
    "brazil-sigap":  1825,    # Lei 14.790 + SIGAP (5y after customer relationship)
    "lgpd":          1825,    # LGPD inherits mandatory retention when applicable
    "ukgc":          1825,    # LCCP Social Responsibility 4.1.1
    "uk-mlr":        1825,    # MLR 2017 Reg 40 (AML records)
    "mga":           1825,    # MGA Directive 4/2018
    "eu-amld":       1825,    # AMLD5 / 6AMLD (KYC)
    "nj-dge":        1825,    # NJ DGE 13.4 (10y for AML is handled separately)
    "nj-dge-aml":    3650,    # NJ DGE AML-specific
    "gli-19":         180,    # GLI-19 / GLI-33 RNG logs (6m minimum)
    "iso-27001":      365,    # Policy-defined; 1y is a common default
}

# Rotation period in days for each `logrotate` frequency keyword.
# `rotate N` with `daily` means N days of retention; with `weekly`
# it means N*7 days; with `monthly` it means N*30 days (approximation).
ROTATION_PERIOD_DAYS = {
    "hourly": 1/24,
    "daily": 1,
    "weekly": 7,
    "monthly": 30,
    "yearly": 365,
}


def parse_logrotate(text: str) -> tuple[int, str]:
    """Return (rotate_count, period_keyword) from a logrotate stanza.

    Raises ValueError if the stanza is missing either directive.
    """
    rotate_match = re.search(r"^\s*rotate\s+(\d+)\b", text, flags=re.MULTILINE)
    if not rotate_match:
        raise ValueError("no `rotate N` directive found")
    rotate = int(rotate_match.group(1))

    period = None
    for keyword in ROTATION_PERIOD_DAYS:
        if re.search(rf"^\s*{keyword}\b", text, flags=re.MULTILINE):
            period = keyword
            break
    if period is None:
        raise ValueError("no rotation period (daily/weekly/monthly/...) found")

    return rotate, period


def retention_days(rotate: int, period: str) -> float:
    return rotate * ROTATION_PERIOD_DAYS[period]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("jurisdiction",
                        choices=sorted(MINIMUM_RETENTION_DAYS.keys()),
                        help="Regulatory jurisdiction to validate against")
    parser.add_argument("--config",
                        type=pathlib.Path,
                        default=pathlib.Path("/etc/logrotate.d/openbao"),
                        help="Path to the logrotate config (default: /etc/logrotate.d/openbao)")
    args = parser.parse_args()

    if not args.config.exists():
        print(f"ERROR: config file not found: {args.config}", file=sys.stderr)
        return 2

    try:
        rotate, period = parse_logrotate(args.config.read_text(encoding="utf-8"))
    except ValueError as err:
        print(f"ERROR: could not parse {args.config}: {err}", file=sys.stderr)
        return 2

    actual = retention_days(rotate, period)
    required = MINIMUM_RETENTION_DAYS[args.jurisdiction]

    if actual >= required:
        print(
            f"OK: {args.config} gives {actual:.0f} days retention "
            f"({rotate} {period}), which meets the {args.jurisdiction} "
            f"minimum of {required} days."
        )
        return 0

    print(
        f"FAIL: {args.config} gives only {actual:.0f} days retention "
        f"({rotate} {period}); {args.jurisdiction} requires at least "
        f"{required} days.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
