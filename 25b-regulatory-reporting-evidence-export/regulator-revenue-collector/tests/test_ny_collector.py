# Companion code for "The Backend of Luck" - Chapter 25b, Regulatory Reporting and Evidence Export.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Unit tests for the NY collector — no network."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collectors.ny import NewYorkCollector  # noqa: E402


def _list_reports() -> list:
    coll = NewYorkCollector(output_root=Path("/tmp/never-used"))
    # list_reports does not touch the network — the coroutine returns the catalog.
    return asyncio.run(coll.list_reports(client=None))  # type: ignore[arg-type]


def test_url_pattern_matches_ny_drupal_slugs() -> None:
    reports = _list_reports()
    sample = next(r for r in reports
                  if r.operator == "Del Lago Resort and Casino"
                  and r.cadence == "monthly"
                  and r.format == "pdf")
    assert sample.source_url == (
        "https://gaming.ny.gov/del-lago-resort-and-casino-monthly-report-pdf"
    )


def test_each_operator_has_four_files_weekly_monthly_pdf_excel() -> None:
    reports = _list_reports()
    by_operator: dict[str, list] = {}
    for r in reports:
        by_operator.setdefault(r.operator, []).append(r)
    for op, files in by_operator.items():
        kinds = {(f.cadence, f.format) for f in files}
        assert kinds == {
            ("weekly",  "pdf"), ("weekly",  "xlsx"),
            ("monthly", "pdf"), ("monthly", "xlsx"),
        }, f"operator {op} missing files: {kinds}"


def test_no_icasino_vertical_for_ny() -> None:
    """NY does not permit online casino; the collector must not invent the vertical."""
    reports = _list_reports()
    verticals = {r.vertical for r in reports}
    assert "igaming" not in verticals
    assert verticals == {"commercial-casino", "sports-wagering", "video-gaming"}


def test_total_report_count_is_predictable() -> None:
    """Catch accidental duplicate operator entries that would inflate the catalog."""
    reports = _list_reports()
    # 5 commercial + 10 sports + 13 video gaming = 28 operators × 4 files = 112
    assert len(reports) == 28 * 4
