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

"""Validate RTP measurements against an approved percentage range."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def parse_range(raw: str) -> tuple[float, float]:
    lower, upper = raw.split("-", 1)
    return float(lower), float(upper)


def load_actual_rtp(result_file: str | None, actual_rtp: float | None) -> float:
    if actual_rtp is not None:
        return actual_rtp

    if result_file:
        payload = json.loads(Path(result_file).read_text(encoding="utf-8"))
        return float(payload["rtp"])

    if "ACTUAL_RTP" in os.environ:
        return float(os.environ["ACTUAL_RTP"])

    raise ValueError("provide --actual-rtp, --result-file, or ACTUAL_RTP")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate simulated RTP output")
    parser.add_argument("--min-rounds", type=int, required=True)
    parser.add_argument("--expected-rtp-range", required=True)
    parser.add_argument("--actual-rtp", type=float)
    parser.add_argument("--result-file")
    args = parser.parse_args()

    lower, upper = parse_range(args.expected_rtp_range)
    try:
        actual = load_actual_rtp(args.result_file, args.actual_rtp)
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        print(f"RTP validation failed: {exc}", file=sys.stderr)
        return 1

    if not lower <= actual <= upper:
        print(f"RTP {actual:.4f}% outside approved range {lower:.2f}-{upper:.2f}%", file=sys.stderr)
        return 1

    print(f"RTP {actual:.4f}% accepted after minimum {args.min_rounds:,} rounds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
