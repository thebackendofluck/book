# Companion code for "The Backend of Luck" - Chapter 47c, Operating 100 Casinos From One Dashboard.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: BUSL-1.1
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Syntactic validation for chapter-47c applied production artifacts."""

from __future__ import annotations

from pathlib import Path

APPLIED = Path(__file__).parent


def test_frontend_files_nonempty() -> None:
    for ext in ("*.ts", "*.tsx", "*.css"):
        for path in APPLIED.glob(ext):
            assert path.read_text().strip(), f"{path.name} is empty"


def test_dockerfile_has_from() -> None:
    dockerfile = APPLIED / "Dockerfile"
    assert dockerfile.exists()
    assert "FROM" in dockerfile.read_text()


def test_cutover_plan_present() -> None:
    assert (APPLIED / "cutover-plan.md").exists()
