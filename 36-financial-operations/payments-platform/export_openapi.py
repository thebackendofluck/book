# Companion code for "The Backend of Luck" - Chapter 36, Financial Operations.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Export the FastAPI OpenAPI schema for the chapter 36 payments platform.
"""

from __future__ import annotations

import json
from pathlib import Path

from main import app


def export_openapi(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(app.openapi(), indent=2), encoding="utf-8")
    return target
