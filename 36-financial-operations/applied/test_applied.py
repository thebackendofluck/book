# Companion code for "The Backend of Luck" - Chapter 36, Financial Operations.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Syntactic validation for chapter-36 applied production artifacts.

These scripts are production-only; the tests verify that every file in
``applied/`` is parseable so book builds cannot go out with a broken snippet.
"""

from __future__ import annotations

import py_compile
from pathlib import Path

APPLIED = Path(__file__).parent


def test_python_modules_compile() -> None:
    for module in APPLIED.glob("*.py"):
        if module.name.startswith("test_"):
            continue
        py_compile.compile(str(module), doraise=True)


def test_sql_files_nonempty_and_terminated() -> None:
    for sql in APPLIED.glob("*.sql"):
        text = sql.read_text()
        assert text.strip(), f"{sql.name} is empty"
        assert ";" in text, f"{sql.name} has no statement terminator"


def test_readme_present() -> None:
    assert (APPLIED / "README.md").exists()
