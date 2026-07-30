# Companion code for "The Backend of Luck" - Chapter 25b, Regulatory Reporting and Evidence Export.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Illinois Gaming Board (IGB) — Sports Wagering CSV parser.

The IGB publishes per-operator monthly sports-wagering data via a stateful
ASP.NET WebForms application at:

    https://igbapps.illinois.gov/SportsReports_AEM.aspx

Two report types are supported by this parser:

1. **Tax Summary** (``SearchType=TypeAccrual``, ``SearchAccrual=Tax Summary Report``)
   Header row: ``"Location Type","Licensee","State AGR","State Tax",...``
   Title row:  ``"Completed Events Tax Summary"``
   → Emits ``metric_name='ggr'`` facts using the ``State AGR`` column.
     Only ``Location Type == "Total"`` rows are used to avoid double-counting
     In-Person + Online sub-totals.

2. **AllActivitySportDetail** (``SearchType=TypeCash``, ``SearchCash=Sport Detail Report``)
   Header row: ``"Detail Type","Location Type","Licensee","Baseball",...``
   Title row:  ``"All Wagering Activity Sport Detail"``
   → These files contain per-sport wager counts and dollar handle broken
     out by Detail Type ("Wager Details" / "Handle Details") and
     Location Type ("In-Person Wagering" / "Online Wagering" / "Total").
     There is **no GGR / Net Receipts / AGR column** in this format.
     We emit ``metric_name='handle'`` facts by summing all sport columns
     from ``Detail Type == "Handle Details"`` + ``Location Type == "Total"``
     rows.

The IL collector fabricates a per-month source URL of the form:
    ``https://igbapps.illinois.gov/SportsReports_AEM.aspx?period=YYYY-MM&rtype=<type>``

where ``rtype=tax-summary`` identifies Tax Summary files and ``rtype=sport-detail``
(or absent) identifies AllActivitySportDetail files.  The parser uses this
parameter to pick the right parse path.

Only 'sports-wagering' vertical is emitted.
"""
from __future__ import annotations

import csv
import io
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterator

from .metrics_model import MetricFact

# ---------------------------------------------------------------------------
# Period and report-type extraction from fabricated source_url
# ---------------------------------------------------------------------------

_PERIOD_RE = re.compile(r"period=(\d{4}-\d{2})")
_RTYPE_RE = re.compile(r"rtype=([\w-]+)")


def _period_from_url(source_url: str) -> str | None:
    """Extract YYYY-MM from the fabricated source_url ?period= parameter."""
    m = _PERIOD_RE.search(source_url)
    return m.group(1) if m else None


def _rtype_from_url(source_url: str) -> str:
    """Extract report type tag from the fabricated source_url, default 'sport-detail'."""
    m = _RTYPE_RE.search(source_url)
    return m.group(1) if m else "sport-detail"


# ---------------------------------------------------------------------------
# Common helpers
# ---------------------------------------------------------------------------

def _to_cents(raw: str | None) -> int | None:
    """Parse a dollar-amount string to integer cents.

    Handles:
        "1,234,567.89"   → 123456789
        "$1,234,567.89"  → 123456789
        "(1,234,567.89)" → -123456789   (parenthetical negative)
        "-1234567.89"    → -123456789
        ""  / "-" / "N/A" → None
    """
    if raw is None:
        return None
    s = raw.strip()
    if not s or s in ("-", "--", "N/A", "n/a", ""):
        return None
    neg = (s.startswith("(") and s.endswith(")")) or s.startswith("-")
    s = s.lstrip("-").strip("()").replace("$", "").replace(",", "").strip()
    if not s:
        return None
    try:
        cents = int((Decimal(s) * 100).to_integral_value())
    except InvalidOperation:
        return None
    return -cents if neg else cents


def _read_csv_text(path: Path) -> str | None:
    """Read file bytes and decode to string; return None on I/O error."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1", errors="replace")
    # Strip BOM.
    text = text.lstrip("﻿")
    # Reject HTML error pages.
    stripped = text.lstrip()
    if stripped.startswith("<") or stripped.lower().startswith("<!"):
        return None
    return text


def _skip_preamble(reader: Iterator[list[str]]) -> list[str] | None:
    """Advance past the IGB title/date preamble rows; return the first header row.

    IGB CSVs begin with 2-3 metadata rows before the actual column header:
        "All Wagering Activity Sport Detail"
        "January 2025"
        "Report Date: ..."
        (blank)
        "Detail Type","Location Type",...   ← this is the header row

    We skip rows until we hit one whose first cell is NOT a known metadata
    sentinel AND the row has more than 1 cell, then treat it as the header.
    """
    _META_RE = re.compile(
        r"^(all wagering|completed events|report date|january|february|march|april|"
        r"may|june|july|august|september|october|november|december|\d{4}$)",
        re.I,
    )
    for row in reader:
        if not any(c.strip() for c in row):
            continue
        first = row[0].strip().strip('"')
        if _META_RE.match(first):
            continue
        # This row has a recognisable header pattern.
        return row
    return None


# ---------------------------------------------------------------------------
# Tax Summary parser  (State AGR → ggr)
# ---------------------------------------------------------------------------

def _parse_tax_summary(text: str, period: str, source_url: str) -> list[MetricFact]:
    """Parse an IGB 'Completed Events Tax Summary' CSV into GGR MetricFacts.

    Expected header (after preamble):
        "Location Type","Licensee","State AGR","State Tax","Cook County AGR",
        "Cook County Tax","Sports Wager Tax","Expired Tickets","Total Payment",

    Only rows with Location Type == "Total" are used so each operator is
    counted once (In-Person + Online already summed by IGB in the Total rows).
    Rows where State AGR is zero are retained (zero is a valid reported value).
    Rows where State AGR is missing/unparseable are skipped.
    """
    facts: list[MetricFact] = []
    try:
        reader = csv.reader(io.StringIO(text))
        headers = _skip_preamble(reader)
        if headers is None:
            return []

        # Build column index map (case-insensitive, strip quotes/whitespace).
        norm = [h.strip().strip('"').lower() for h in headers]

        def _idx(name: str) -> int | None:
            try:
                return norm.index(name)
            except ValueError:
                return None

        loc_idx = _idx("location type")
        lic_idx = _idx("licensee")
        agr_idx = _idx("state agr")

        if loc_idx is None or lic_idx is None or agr_idx is None:
            return []

        for row in reader:
            if not any(c.strip() for c in row):
                continue
            try:
                loc_type = row[loc_idx].strip().strip('"')
            except IndexError:
                continue

            # Only process the per-operator Total rows.
            if loc_type.lower() != "total":
                continue

            try:
                operator = row[lic_idx].strip().strip('"')
            except IndexError:
                continue
            if not operator:
                continue

            try:
                agr_raw = row[agr_idx].strip()
            except IndexError:
                continue

            agr_cents = _to_cents(agr_raw)
            if agr_cents is None:
                continue

            facts.append(MetricFact(
                state="IL",
                operator=operator,
                vertical="sports-wagering",
                period=period,
                metric_name="ggr",
                value_usd_cents=agr_cents,
                source_url=source_url,
            ))

    except csv.Error:
        return []

    facts.sort(key=lambda f: (f.period, f.operator))
    return facts


# ---------------------------------------------------------------------------
# AllActivitySportDetail parser  (Handle Details → handle)
# ---------------------------------------------------------------------------

def _parse_sport_detail(text: str, period: str, source_url: str) -> list[MetricFact]:
    """Parse an IGB 'AllActivitySportDetail' CSV into handle MetricFacts.

    Column layout (after preamble):
        "Detail Type","Location Type","Licensee","Baseball","Basketball",...

    Strategy: collect rows where Detail Type == "Handle Details" AND
    Location Type == "Total", then sum all sport columns to get the
    per-operator monthly handle.

    Rows where the summed handle rounds to zero cents are skipped (operator
    had no activity that month).
    """
    facts: list[MetricFact] = []
    try:
        reader = csv.reader(io.StringIO(text))
        headers = _skip_preamble(reader)
        if headers is None:
            return []

        norm = [h.strip().strip('"').lower() for h in headers]

        def _idx(name: str) -> int | None:
            try:
                return norm.index(name)
            except ValueError:
                return None

        dtype_idx = _idx("detail type")
        loc_idx = _idx("location type")
        lic_idx = _idx("licensee")

        if dtype_idx is None or loc_idx is None or lic_idx is None:
            return []

        # Sport columns: everything after "Licensee"
        sport_indices = [
            i for i, h in enumerate(norm)
            if i > lic_idx and h not in ("", "detail type", "location type", "licensee")
        ]

        for row in reader:
            if not any(c.strip() for c in row):
                continue
            try:
                dtype = row[dtype_idx].strip().strip('"')
                loc_type = row[loc_idx].strip().strip('"')
            except IndexError:
                continue

            if dtype.lower() != "handle details":
                continue
            if loc_type.lower() != "total":
                continue

            try:
                operator = row[lic_idx].strip().strip('"')
            except IndexError:
                continue
            if not operator:
                continue

            # Sum across all sport columns.
            total_cents = 0
            for si in sport_indices:
                try:
                    v = _to_cents(row[si])
                except IndexError:
                    continue
                if v is not None:
                    total_cents += v

            if total_cents == 0:
                continue

            facts.append(MetricFact(
                state="IL",
                operator=operator,
                vertical="sports-wagering",
                period=period,
                metric_name="handle",
                value_usd_cents=total_cents,
                source_url=source_url,
            ))

    except csv.Error:
        return []

    facts.sort(key=lambda f: (f.period, f.operator))
    return facts


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

_TAX_SUMMARY_TITLE_RE = re.compile(r"completed events tax summary", re.I)
_SPORT_DETAIL_TITLE_RE = re.compile(r"all wagering activity sport detail", re.I)


def _detect_format(text: str) -> str:
    """Return 'tax-summary' or 'sport-detail' based on the CSV title row."""
    # Inspect only the first 200 chars for speed.
    sample = text[:200]
    if _TAX_SUMMARY_TITLE_RE.search(sample):
        return "tax-summary"
    return "sport-detail"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_il_csv(path: Path, source_url: str) -> list[MetricFact]:
    """Parse an IGB sports-wagering CSV into MetricFacts.

    Two formats are handled automatically:

    * **Tax Summary** (``rtype=tax-summary`` in source_url, or title row
      contains "Completed Events Tax Summary"):
      Emits ``metric_name='ggr'`` using ``State AGR`` from ``Total`` rows.

    * **AllActivitySportDetail** (default):
      Emits ``metric_name='handle'`` by summing sport-column handle totals
      from ``Handle Details`` + ``Total`` rows.

    Args:
        path:        Local path to the cached CSV file.
        source_url:  Fabricated URL carrying ``?period=YYYY-MM`` (and
                     optionally ``&rtype=tax-summary``).

    Returns:
        List of MetricFacts sorted by (period, operator).  Returns [] if the
        period cannot be determined, the file is unreadable, or the CSV
        structure is not recognised.
    """
    period = _period_from_url(source_url)
    if period is None:
        return []

    text = _read_csv_text(path)
    if text is None:
        return []

    # Let the title row in the file drive format detection; fall back to
    # the rtype= URL hint if the title is absent or ambiguous.
    fmt = _detect_format(text)
    if fmt == "sport-detail":
        rtype = _rtype_from_url(source_url)
        if rtype == "tax-summary":
            fmt = "tax-summary"

    if fmt == "tax-summary":
        return _parse_tax_summary(text, period, source_url)
    return _parse_sport_detail(text, period, source_url)
