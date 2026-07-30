#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 22b, Developer Inner-Loop Experience in Containerized iGaming Pla.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Patch a Kubernetes deployment with a QA approval annotation."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="QA gate annotation patcher")
    parser.add_argument("--service", required=True, help="Deployment name")
    parser.add_argument("--namespace", required=True, help="Kubernetes namespace")
    parser.add_argument("--image-tag", required=True, help="Image tag approved by QA")
    parser.add_argument("--pipeline-run-id", required=True, help="CI pipeline run id")
    parser.add_argument(
        "--results",
        default="test-results.json",
        help="JSON test summary; must exist and show a clean run",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print kubectl command only")
    return parser.parse_args()


class QAGateError(Exception):
    """The gate cannot prove the tests passed, so it must not approve."""


def check_test_results(path: Path) -> None:
    """Raise unless the results file proves a clean run.

    Absent, unreadable or malformed evidence is a failure, not a pass. A wrong
    --results path or a crashed earlier pipeline step would otherwise annotate
    the deployment qa/approved=true without a single test having run.
    """
    if not path.exists():
        raise QAGateError(
            f"test results not found at {path}. The gate approves a deployment "
            "only against a results file it can read; check the path and that "
            "the test step actually ran."
        )

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QAGateError(f"cannot read test results at {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise QAGateError(f"test results at {path} are not a JSON object")

    # A summary with no test count is not evidence of a passing run either.
    if "total" not in data and "tests" not in data:
        raise QAGateError(
            f"test results at {path} report no test count "
            "(expected a 'total' or 'tests' field)"
        )

    try:
        total = int(data.get("total", data.get("tests", 0)))
        failures = int(data.get("failures", 0)) + int(data.get("errors", 0))
    except (TypeError, ValueError) as exc:
        raise QAGateError(f"test results at {path} have non-numeric counts: {exc}") from exc

    if total <= 0:
        raise QAGateError(f"test results at {path} report {total} tests executed")

    if failures:
        raise QAGateError(f"test results at {path} report {failures} failures/errors")


def build_annotation_command(args: argparse.Namespace) -> list[str]:
    timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
    return [
        "kubectl",
        "annotate",
        "deployment",
        args.service,
        "qa/approved=true",
        f"qa/image-tag={args.image_tag}",
        f"qa/pipeline-run-id={args.pipeline_run_id}",
        f"qa/approved-at={timestamp}",
        "--overwrite",
        "-n",
        args.namespace,
    ]


def main() -> int:
    args = parse_args()
    try:
        check_test_results(Path(args.results))
    except QAGateError as exc:
        print(f"QA gate failed: {exc}", file=sys.stderr)
        return 1

    command = build_annotation_command(args)
    if args.dry_run:
        print(" ".join(command))
        return 0

    subprocess.run(command, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
