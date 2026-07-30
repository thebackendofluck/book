# Companion code for "The Backend of Luck" - Chapter 25b, Regulatory Reporting and Evidence Export.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Model invariants for the regulator-revenue-collector."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models import ReportFile, StateSnapshot, SummaryRow  # noqa: E402


def test_report_file_round_trips_through_json() -> None:
    r = ReportFile(
        operator="Test Casino",
        vertical="commercial-casino",
        cadence="weekly",
        format="pdf",
        source_url="https://example.com/file.pdf",
        size_bytes=1024,
        sha256="a" * 64,
        retrieved_at=datetime(2026, 4, 22, tzinfo=timezone.utc),
        summary=[SummaryRow(label="GGR", value="$1,234,567")],
    )
    js = r.model_dump_json()
    again = ReportFile.model_validate_json(js)
    assert again == r


def test_state_snapshot_serialises_with_iso_timestamp() -> None:
    snap = StateSnapshot(
        state="NY",
        regulator="NY State Gaming Commission",
        source_url="https://gaming.ny.gov/revenue-reports",
        collected_at=datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc),
        reports=[],
    )
    js = snap.model_dump_json()
    assert "2026-04-22T12:00:00Z" in js or "2026-04-22T12:00:00+00:00" in js
