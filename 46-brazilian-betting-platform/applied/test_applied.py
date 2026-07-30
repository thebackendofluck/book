# Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Chapter 46 references artifacts from sibling chapters; verify the links resolve."""

from __future__ import annotations

from pathlib import Path

APPLIED = Path(__file__).parent
REFERENCED = [
    APPLIED.parent.parent / "chapter-36" / "applied" / "idempotency.py",
    APPLIED.parent.parent / "chapter-36" / "applied" / "003-add-unique-wallet-events.sql",
    APPLIED.parent.parent / "chapter-36" / "applied" / "pii_crypto.py",
]


def test_cross_chapter_references_exist() -> None:
    for path in REFERENCED:
        assert path.exists(), f"missing referenced artifact: {path}"


def test_readme_present() -> None:
    assert (APPLIED / "README.md").exists()
