# Companion code for "The Backend of Luck" - Chapter 28a, Distributed Systems Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Syntactic validation for chapter-28a applied production artifacts."""

from __future__ import annotations

import py_compile
from pathlib import Path

APPLIED = Path(__file__).parent


def test_python_modules_compile() -> None:
    for module in APPLIED.glob("*.py"):
        if module.name.startswith("test_"):
            continue
        py_compile.compile(str(module), doraise=True)


def test_sql_files_nonempty() -> None:
    for sql in APPLIED.glob("*.sql"):
        text = sql.read_text()
        assert ";" in text, f"{sql.name} has no statement terminator"


def test_typescript_files_nonempty() -> None:
    for ts in APPLIED.glob("*.ts"):
        text = ts.read_text()
        assert "export" in text or "function" in text, f"{ts.name} has no exports"
