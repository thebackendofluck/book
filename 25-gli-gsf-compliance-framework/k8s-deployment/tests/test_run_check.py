# Companion code for "The Backend of Luck" - Chapter 25, GLI-GSF Compliance Framework.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""TDD: run_check should invoke the GLI script via subprocess and return a structured result."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest


@pytest.fixture
def fake_script(tmp_path: Path) -> Path:
    """A trivial executable that prints to stdout/stderr and exits with the requested code."""
    p = tmp_path / "fake_check.py"
    p.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        "print('OK from fake', flush=True)\n"
        "print('warning to stderr', file=sys.stderr, flush=True)\n"
        "sys.exit(int(os.environ.get('FAKE_EXIT', '0')))\n",
        encoding="utf-8",
    )
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return p


def test_run_check_captures_success(fake_script: Path) -> None:
    from runner.checks import run_check

    result = run_check(
        name="jackpot",
        argv=[str(fake_script)],
        env={"FAKE_EXIT": "0"},
        timeout_s=5,
    )

    assert result.success is True
    assert result.return_code == 0
    assert "OK from fake" in result.stdout
    assert "warning to stderr" in result.stderr
    assert 0 <= result.duration_s < 5
    assert result.name == "jackpot"


def test_run_check_captures_failure(fake_script: Path) -> None:
    from runner.checks import run_check

    result = run_check(
        name="mcs",
        argv=[str(fake_script)],
        env={"FAKE_EXIT": "2"},
        timeout_s=5,
    )

    assert result.success is False
    assert result.return_code == 2


def test_run_check_handles_timeout(tmp_path: Path) -> None:
    from runner.checks import run_check

    slow = tmp_path / "slow.py"
    slow.write_text("import time; time.sleep(10)\n", encoding="utf-8")
    slow.chmod(slow.stat().st_mode | stat.S_IXUSR)

    result = run_check(
        name="recon",
        argv=["python3", str(slow)],
        env={},
        timeout_s=1,
    )

    assert result.success is False
    assert result.timed_out is True
    assert result.duration_s >= 1.0
