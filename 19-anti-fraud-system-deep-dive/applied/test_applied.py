# Companion code for "The Backend of Luck" - Chapter 19, Anti-Fraud System Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Syntactic validation for chapter-19 applied production artifacts."""

from __future__ import annotations

import py_compile
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET

APPLIED = Path(__file__).parent


def test_python_modules_compile() -> None:
    for module in APPLIED.glob("*.py"):
        if module.name.startswith("test_"):
            continue
        py_compile.compile(str(module), doraise=True)


def test_xml_files_parse() -> None:
    for xml in APPLIED.glob("*.xml"):
        wrapped = f"<root>{xml.read_text()}</root>"
        ET.fromstring(wrapped)


def test_shell_scripts_parse() -> None:
    for sh in APPLIED.glob("*.sh"):
        result = subprocess.run(["bash", "-n", str(sh)], capture_output=True)
        assert result.returncode == 0, f"{sh.name}: {result.stderr.decode()}"
