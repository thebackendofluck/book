# Companion code for "The Backend of Luck" - Chapter 44, Deploying iGaming Platforms on Cloudflare Workers.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Syntactic validation for chapter-44 applied production artifacts."""

from __future__ import annotations

from pathlib import Path

APPLIED = Path(__file__).parent


def test_sql_files_nonempty() -> None:
    for sql in APPLIED.glob("*.sql"):
        text = sql.read_text()
        assert ";" in text, f"{sql.name} has no statement terminator"


def test_typescript_files_nonempty() -> None:
    for ts in APPLIED.glob("*.ts"):
        text = ts.read_text()
        assert text.strip(), f"{ts.name} is empty"
