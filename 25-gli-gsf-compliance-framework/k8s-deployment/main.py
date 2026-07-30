# Companion code for "The Backend of Luck" - Chapter 25, GLI-GSF Compliance Framework.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Entrypoint — wires CLI script paths from env vars and exposes the FastAPI app."""

from __future__ import annotations

import os

from runner.server import build_app

SCRIPTS_ROOT = os.environ.get("GLI_SCRIPTS_ROOT", "/app/scripts")

CHECK_ARGV: dict[str, list[str]] = {
    "jackpot": [
        "python",
        f"{SCRIPTS_ROOT}/chapter-15/gli-12/jackpot-reserve-check.py",
        "--config", os.environ.get("JACKPOT_CONFIG", "/etc/gli/jackpot-config.json"),
        "--ledger", os.environ.get("JACKPOT_LEDGER", "/etc/gli/jackpot-ledger.csv"),
    ],
    "mcs": [
        "bash",
        f"{SCRIPTS_ROOT}/chapter-25/gli-13/mcs-connector-test.sh",
    ],
    "recon": [
        "python",
        f"{SCRIPTS_ROOT}/chapter-06/gli-16/wallet-reconciliation-check.py",
        "--provider", os.environ.get("RECON_PROVIDER", "/etc/gli/provider.csv"),
        "--operator", os.environ.get("RECON_OPERATOR", "/etc/gli/operator.csv"),
        "--report",   os.environ.get("RECON_REPORT", "/var/lib/gli-evidence/recon-latest.json"),
    ],
    "gli28": [
        "python",
        f"{SCRIPTS_ROOT}/chapter-32/testing-qa/gli28_runner.py",
        "--out-dir", os.environ.get("GLI28_OUT_DIR", "/var/lib/gli-evidence/gli28"),
        "--duration-min", os.environ.get("GLI28_DURATION_MIN", "1"),
        "--skip-drift",
    ],
}

app = build_app(
    check_argv=CHECK_ARGV,
    check_timeout_s=int(os.environ.get("CHECK_TIMEOUT_S", "300")),
)
