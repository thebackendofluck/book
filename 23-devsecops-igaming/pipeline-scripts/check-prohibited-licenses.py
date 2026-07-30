#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 23, DevSecOps for iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Fail CI when dependency licenses are in a prohibited list."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def normalize(raw: str) -> str:
    return raw.strip().lower().replace(" ", "-")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check prohibited dependency licenses")
    parser.add_argument("licenses_json")
    parser.add_argument("--prohibited", required=True, help="Comma-separated license names")
    args = parser.parse_args()

    prohibited = {normalize(item) for item in args.prohibited.split(",") if item.strip()}
    payload = json.loads(Path(args.licenses_json).read_text(encoding="utf-8"))

    violations: list[str] = []
    for package in payload:
        name = package.get("Name") or package.get("name") or "<unknown>"
        license_name = package.get("License") or package.get("license") or ""
        if normalize(license_name) in prohibited:
            violations.append(f"{name}: {license_name}")

    if violations:
        print("Prohibited licenses found:", file=sys.stderr)
        print("\n".join(violations), file=sys.stderr)
        return 1

    print("No prohibited licenses found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
