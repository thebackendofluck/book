# Companion code for "The Backend of Luck" - Chapter 25, GLI-GSF Compliance Framework.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""TDD: FastAPI HTTP layer."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app(tmp_path: Path):
    fake = tmp_path / "fake_check.py"
    fake.write_text(
        "#!/usr/bin/env python3\nimport sys; print('ok'); sys.exit(0)\n",
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)

    from runner.server import build_app

    return build_app(
        check_argv={
            "jackpot": [str(fake)],
            "mcs": [str(fake)],
            "recon": [str(fake)],
            "gli28": [str(fake)],
        },
        check_timeout_s=5,
    )


def test_healthz_returns_200(app) -> None:
    client = TestClient(app)
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_metrics_endpoint_returns_prometheus_format(app) -> None:
    client = TestClient(app)
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "gli_check_runs_total" in r.text or "# HELP" in r.text


def test_run_check_executes_subprocess(app) -> None:
    client = TestClient(app)
    r = client.post("/run/jackpot")
    assert r.status_code == 200
    body = r.json()
    assert body["check"] == "jackpot"
    assert body["success"] is True
    assert body["return_code"] == 0


def test_run_unknown_check_returns_404(app) -> None:
    client = TestClient(app)
    r = client.post("/run/nonexistent")
    assert r.status_code == 404


def test_run_check_increments_metrics(app) -> None:
    client = TestClient(app)
    client.post("/run/jackpot")
    r = client.get("/metrics")
    assert 'gli_check_runs_total{check="jackpot",outcome="success"} 1.0' in r.text
