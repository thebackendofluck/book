# Companion code for "The Backend of Luck" - Chapter 39, Case Study.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

from __future__ import annotations

import py_compile
from pathlib import Path


CHAPTER_DIR = Path(__file__).resolve().parents[1]


def test_python_files_compile() -> None:
    py_files = [
        path
        for path in CHAPTER_DIR.rglob("*.py")
        if "tests" not in path.parts and "__pycache__" not in path.parts
    ]
    assert py_files, "No Python files found to validate"

    for path in py_files:
        py_compile.compile(str(path), doraise=True)
