# Companion code for "The Backend of Luck" - Chapter 23, DevSecOps for iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

from pathlib import Path
import py_compile


MODULE_DIR = Path(__file__).resolve().parents[1]


def test_devsecops_python_files_compile():
    python_files = sorted(
        path for path in MODULE_DIR.glob("*.py")
        if path.name != "__init__.py"
    )

    assert python_files

    for path in python_files:
        py_compile.compile(str(path), doraise=True)
