#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 32, Testing and QA in Gambling.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""GLI-28 v1.0 unified test runner — CI gate entrypoint.

Orchestrates the three GLI-28 checks in the right order and emits a single
JUnit XML report for the CI system to consume:

    1. gli-28-disclosure-check.py  (DOM disclosures)
    2. gli-28-counter-drift.py     (session timer / loss counter)
    3. gli-28-a11y.sh              (axe-core WCAG 2.1 AA)

Each check becomes one <testcase> in a <testsuite name="GLI-28-v1.0">.
Failures cause exit 1, which the CI gate treats as deploy-blocking.

Designed to plug into the existing class-based ComplianceTestingFramework
in `compliance_testing.py` (see `register()` below) without rewriting the
sibling scripts as Python modules — they remain CLIs runnable in isolation
during local debugging.

Usage (standalone):
    BASE_URL=https://staging.acmetocasino.com \\
    GAMES_FILE=games.json \\
    PLAYER_JWT=... GAME_SLUG=demo-slot \\
        uv run gli28_runner.py --out-dir ./gli28-out --duration-min 5

Usage (from compliance_testing.py):
    from gli28_runner import register
    register(framework)  # adds gli28_disclosure / drift / a11y test categories

Exit codes:
    0  every check passed
    1  one or more checks failed (JUnit report still written)
    2  config / dependency error
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
GLI28_DIR = THIS_DIR.parent / "gli-28"


@dataclass
class StepResult:
    name: str
    elapsed_s: float
    return_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.return_code == 0


def run_step(name: str, argv: list[str], env: dict[str, str]) -> StepResult:
    start = time.monotonic()
    proc = subprocess.run(
        argv,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    return StepResult(
        name=name,
        elapsed_s=time.monotonic() - start,
        return_code=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


def emit_junit(steps: list[StepResult], out_path: Path) -> None:
    failures = sum(1 for s in steps if not s.ok)
    suite = ET.Element(
        "testsuite",
        attrib={
            "name": "GLI-28-v1.0",
            "tests": str(len(steps)),
            "failures": str(failures),
            "time": f"{sum(s.elapsed_s for s in steps):.3f}",
        },
    )
    for s in steps:
        case = ET.SubElement(
            suite,
            "testcase",
            attrib={
                "classname": "GLI-28",
                "name": s.name,
                "time": f"{s.elapsed_s:.3f}",
            },
        )
        if not s.ok:
            failure = ET.SubElement(
                case,
                "failure",
                attrib={
                    "type": "GLI-28-control-breach",
                    "message": f"return code {s.return_code}",
                },
            )
            failure.text = (s.stdout + "\n" + s.stderr).strip()
        else:
            sysout = ET.SubElement(case, "system-out")
            sysout.text = s.stdout.strip()
    out_path.write_bytes(ET.tostring(suite, encoding="utf-8", xml_declaration=True))


def env_required(*keys: str) -> dict[str, str]:
    env = os.environ.copy()
    missing = [k for k in keys if not env.get(k)]
    if missing:
        print(f"missing env vars: {missing}", file=sys.stderr)
        sys.exit(2)
    return env


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--duration-min", type=int, default=5)
    p.add_argument("--skip-drift", action="store_true", help="skip the long-running counter drift step")
    args = p.parse_args()

    if not GLI28_DIR.is_dir():
        print(f"error: cannot find {GLI28_DIR}", file=sys.stderr)
        return 2
    args.out_dir.mkdir(parents=True, exist_ok=True)

    env = env_required("BASE_URL", "GAMES_FILE", "PLAYER_JWT", "GAME_SLUG")

    steps: list[StepResult] = []

    disclosure_report = args.out_dir / "disclosure-report.json"
    steps.append(
        run_step(
            "disclosure_check",
            [
                sys.executable,
                str(GLI28_DIR / "gli-28-disclosure-check.py"),
                "--games-file", env["GAMES_FILE"],
                "--base-url", env["BASE_URL"],
                "--report", str(disclosure_report),
            ],
            env=env,
        )
    )

    if not args.skip_drift:
        drift_csv = args.out_dir / "counter-drift.csv"
        steps.append(
            run_step(
                "counter_drift",
                [
                    sys.executable,
                    str(GLI28_DIR / "gli-28-counter-drift.py"),
                    "--duration-min", str(args.duration_min),
                    "--out", str(drift_csv),
                ],
                env=env,
            )
        )

    axe_dir = args.out_dir / "axe-reports"
    axe_dir.mkdir(exist_ok=True)
    axe_env = {**env, "OUT_DIR": str(axe_dir)}
    a11y_script = GLI28_DIR / "gli-28-a11y.sh"
    if not shutil.which("bash"):
        print("error: bash not on PATH", file=sys.stderr)
        return 2
    steps.append(
        run_step("a11y_axe", ["bash", str(a11y_script)], env=axe_env)
    )

    junit = args.out_dir / "gli28-junit.xml"
    emit_junit(steps, junit)

    failed = [s for s in steps if not s.ok]
    if failed:
        print(f"FAIL: {len(failed)}/{len(steps)} GLI-28 checks failed", file=sys.stderr)
        for s in failed:
            print(f"  - {s.name} (rc={s.return_code})", file=sys.stderr)
        return 1
    print(f"OK: {len(steps)} GLI-28 checks passed; junit at {junit}")
    return 0


def register(framework: object) -> None:
    """Hook for `compliance_testing.py`'s ComplianceTestingFramework.

    Adds three test categories to the framework so that running the
    full suite executes the GLI-28 checks alongside the existing
    UKGC / MGA / NJ / Spelinspektionen suites. The framework should
    expose an `add_test_category(name, callable)` API; we degrade
    gracefully if the API differs by version.
    """
    add = getattr(framework, "add_test_category", None)
    if not callable(add):
        return
    add("gli28_disclosures", lambda: run_step("disclosure_check", [
        sys.executable, str(GLI28_DIR / "gli-28-disclosure-check.py"),
        "--games-file", os.environ["GAMES_FILE"],
        "--base-url",   os.environ["BASE_URL"],
        "--report",     "/tmp/gli28-disclosure.json",
    ], env=os.environ.copy()))
    add("gli28_counter_drift", lambda: run_step("counter_drift", [
        sys.executable, str(GLI28_DIR / "gli-28-counter-drift.py"),
        "--duration-min", os.environ.get("GLI28_DRIFT_MIN", "5"),
        "--out", "/tmp/gli28-drift.csv",
    ], env=os.environ.copy()))
    add("gli28_a11y", lambda: run_step("a11y_axe", [
        "bash", str(GLI28_DIR / "gli-28-a11y.sh"),
    ], env={**os.environ, "OUT_DIR": "/tmp/gli28-axe"}))


if __name__ == "__main__":
    sys.exit(main())
