# Companion code for "The Backend of Luck" - Chapter 25, GLI-GSF Compliance Framework.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Registry and execution of the GLI compliance CLI scripts."""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class CheckSpec:
    name: str
    gli_standard: str
    schedule_hint: str  # cron expression hint, used by k8s manifests as documentation


@dataclass(frozen=True)
class CheckResult:
    name: str
    success: bool
    return_code: int
    duration_s: float
    stdout: str
    stderr: str
    timed_out: bool


def run_check(
    *,
    name: str,
    argv: list[str],
    env: dict[str, str],
    timeout_s: int,
) -> CheckResult:
    full_env = {**os.environ, **env}
    start = time.monotonic()
    timed_out = False
    return_code = -1
    stdout = ""
    stderr = ""
    try:
        proc = subprocess.run(
            argv,
            env=full_env,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        return_code = proc.returncode
        stdout = proc.stdout
        stderr = proc.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = (exc.stdout or b"").decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = (exc.stderr or b"").decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
    duration_s = time.monotonic() - start
    return CheckResult(
        name=name,
        success=(return_code == 0 and not timed_out),
        return_code=return_code,
        duration_s=duration_s,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
    )


_REGISTRY: dict[str, CheckSpec] = {
    "jackpot": CheckSpec(
        name="jackpot",
        gli_standard="GLI-12 v3.0",
        schedule_hint="*/5 * * * *",
    ),
    "mcs": CheckSpec(
        name="mcs",
        gli_standard="GLI-13 v3.0",
        schedule_hint="*/1 * * * *",
    ),
    "recon": CheckSpec(
        name="recon",
        gli_standard="GLI-16 v3.0",
        schedule_hint="0 3 * * *",
    ),
    "gli28": CheckSpec(
        name="gli28",
        gli_standard="GLI-28 v1.0",
        schedule_hint="0 4 * * 0",
    ),
}


def registry() -> dict[str, CheckSpec]:
    return dict(_REGISTRY)
