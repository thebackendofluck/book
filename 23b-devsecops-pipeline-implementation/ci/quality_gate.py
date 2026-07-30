#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 23b, DevSecOps Pipeline Implementation.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Parse SARIF files and enforce severity thresholds."""

from __future__ import annotations

import argparse
import glob
import json
import sys
from collections import defaultdict
from pathlib import Path


SEVERITY_ORDER = {
    "critical": 4,
    "error": 3,
    "high": 3,
    "warning": 2,
    "medium": 2,
    "note": 1,
    "low": 1,
}


def result_severity(result: dict) -> str:
    properties = result.get("properties", {})
    raw = properties.get("security-severity") or properties.get("severity") or result.get("level", "note")
    if isinstance(raw, (int, float)):
        return "critical" if raw >= 9 else "high" if raw >= 7 else "medium" if raw >= 4 else "low"
    return str(raw).lower()


def parse_sarif(path: Path) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    payload = json.loads(path.read_text(encoding="utf-8"))
    for run in payload.get("runs", []):
        for result in run.get("results", []):
            counts[result_severity(result)] += 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Centralized SARIF quality gate")
    parser.add_argument("--sarif-dir", required=True)
    parser.add_argument("--critical-threshold", type=int, default=0)
    parser.add_argument("--high-threshold", type=int, default=5)
    parser.add_argument("--medium-threshold", type=int, default=20)
    args = parser.parse_args()

    totals: dict[str, int] = defaultdict(int)
    for filename in glob.glob(str(Path(args.sarif_dir) / "*.sarif")):
        for severity, count in parse_sarif(Path(filename)).items():
            totals[severity] += count

    Path("quality-gate-report.json").write_text(json.dumps(totals, indent=2), encoding="utf-8")
    failed = (
        totals["critical"] > args.critical_threshold
        or totals["high"] > args.high_threshold
        or totals["medium"] > args.medium_threshold
    )
    if failed:
        print(f"Quality gate failed: {dict(totals)}", file=sys.stderr)
        return 1

    print(f"Quality gate passed: {dict(totals)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
