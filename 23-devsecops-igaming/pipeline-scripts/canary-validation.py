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

"""Validate canary metrics against release thresholds."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load_metrics(path: str | None) -> dict[str, float]:
    if not path:
        return {"error_rate": 0.0, "p95_latency_ms": 0.0}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        "error_rate": float(payload.get("error_rate", 0.0)),
        "p95_latency_ms": float(payload.get("p95_latency_ms", 0.0)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Canary release validation")
    parser.add_argument("--duration", type=int, required=True)
    parser.add_argument("--error-threshold", type=float, required=True)
    parser.add_argument("--latency-threshold", type=float, required=True)
    parser.add_argument("--metrics-file")
    args = parser.parse_args()

    metrics = load_metrics(args.metrics_file)
    if metrics["error_rate"] > args.error_threshold:
        print(f"Canary error rate too high: {metrics['error_rate']}%", file=sys.stderr)
        return 1
    if metrics["p95_latency_ms"] > args.latency_threshold:
        print(f"Canary latency too high: {metrics['p95_latency_ms']}ms", file=sys.stderr)
        return 1

    print(f"Canary accepted after {args.duration}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
