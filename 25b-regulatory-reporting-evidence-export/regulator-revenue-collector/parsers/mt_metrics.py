# Companion code for "The Backend of Luck" - Chapter 25b, Regulatory Reporting and Evidence Export.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Malta Gaming Authority (MGA) PDF parser.

The MGA publishes two primary data sources:

1. **Annual Reports** (YYYY covers the prior fiscal year; released mid-following year).
   Confirmed URL patterns (probe order):
     - https://www.mga.org.mt/app/uploads/MGA-Annual-Report-{YYYY}.pdf
     - https://www.mga.org.mt/app/uploads/MGA-{YYYY}-Annual-Report.pdf
     - https://www.mga.org.mt/app/uploads/Annual-Report-{YYYY}.pdf

   The report contains total sector GGR in EUR split by vertical.  Key phrases
   found in narrative text (based on publicly cited MGA figures):

     * "gross gaming revenue" / "GGR" + EUR amount (millions or billions)
     * "B2C" segment totals per vertical  (casino, sports, poker, lottery)
     * "Remote Gaming Revenue" (older terminology, pre-2019)

   Typical ranges observed (EUR millions, full year):
     - Total GGR:    ~6 000 – 9 000 M EUR (remote licensees, Malta-domiciled)
     - Casino (B2C): ~3 500 – 5 500 M EUR
     - Sports:       ~1 200 – 2 000 M EUR
     - Poker:        ~200 – 400 M EUR
     - Lottery:      ~100 – 300 M EUR

2. **Interim Reports** (H1 of the labelled year; released late in same year).
   Same text patterns; EUR values are approximately half the annual totals.

Period mapping strategy:
  - Annual report labelled YYYY → data year YYYY-1 → period ``{YYYY-1}-12``
    (MGA reports are typically published Q2 of YYYY covering the previous year)
  - Interim report labelled YYYY → data covers H1 of YYYY → period ``{YYYY}-06``

Because MGA PDFs are image-heavy InDesign exports (text embedded as vector /
font streams), pdfplumber ``extract_tables()`` returns empty results on many
pages.  The parser uses regex over ``extract_text()`` output.

Annual GGR → spread across 12 months (``_spread_monthly``).
Semi-annual GGR → spread across 6 months.

FX: EUR → USD ≈ 1.08 (fixed approximation; replace with live FX lookup later).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import pdfplumber

from .metrics_model import MetricFact

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EUR_TO_USD: float = 1.08

# Vertical tag → canonical MetricFact vertical string
_VERTICAL_MAP: dict[str, str] = {
    "casino":   "igaming",
    "sports":   "sports-wagering",
    "poker":    "online-poker",
    "lottery":  "lottery",
}

# ---------------------------------------------------------------------------
# FX helper
# ---------------------------------------------------------------------------


def _eur_millions_to_usd_cents(eur_millions: float) -> int:
    """Convert a GGR value in EUR millions to USD cents."""
    return int(eur_millions * 1_000_000 * _EUR_TO_USD * 100)


# ---------------------------------------------------------------------------
# Period detection from filename / first-page text
# ---------------------------------------------------------------------------

# Annual: "MGA-Annual-Report-2023.pdf" or "Annual-Report-2022.pdf"
_ANNUAL_FILENAME_RE = re.compile(
    r"annual[_\-]report[_\-]?(\d{4})", re.IGNORECASE
)
# Interim: "Interim-Report-2023.pdf"
_INTERIM_FILENAME_RE = re.compile(
    r"interim[_\-]report[_\-]?(\d{4})", re.IGNORECASE
)
# Oversight: "Regulatory-Oversight-2025.pdf"
_OVERSIGHT_FILENAME_RE = re.compile(
    r"regulatory[_\-]oversight[_\-\w]*?(\d{4})", re.IGNORECASE
)

# Slug fallbacks: backfill cache slugs contain cadence keyword but no year,
# e.g. "MT-Statewide-all-annual-{hash}.pdf" or "...-semi-annual-...".
# These allow _detect_report_type to return the type (with period=None) so the
# first-page text extraction can supply the year.
_SLUG_ANNUAL_RE = re.compile(r"\bannual\b", re.IGNORECASE)
_SLUG_INTERIM_RE = re.compile(r"\bsemi[_\-]annual\b", re.IGNORECASE)

# First-page text fallbacks
_PAGE1_ANNUAL_RE = re.compile(r"annual\s+report\s+(\d{4})", re.IGNORECASE)
_PAGE1_INTERIM_RE = re.compile(r"interim\s+report\s+(\d{4})", re.IGNORECASE)


def _detect_report_type(filename: str) -> tuple[str, Optional[str]]:
    """Return (report_type, period) from filename.

    report_type: "annual" | "interim" | "oversight" | "unknown"
    period:      "YYYY-MM" or None
    """
    m = _ANNUAL_FILENAME_RE.search(filename)
    if m:
        year = int(m.group(1))
        # Annual report labelled YYYY typically covers prior year YYYY-1.
        # e.g. "Annual-Report-2023.pdf" → data year 2022 → period 2022-12.
        return "annual", f"{year - 1}-12"

    m = _INTERIM_FILENAME_RE.search(filename)
    if m:
        year = int(m.group(1))
        # Interim report labelled YYYY → H1 of YYYY → period YYYY-06.
        return "interim", f"{year}-06"

    m = _OVERSIGHT_FILENAME_RE.search(filename)
    if m:
        year = int(m.group(1))
        # Oversight reports describe prior year activity.
        return "oversight", f"{year - 1}-12"

    # Slug fallback: backfill cache slugs contain cadence keyword but no year
    # (e.g. "MT-Statewide-all-semi-annual-abc123.pdf").  Return the report type
    # with period=None so the first-page text extraction can supply the year.
    if _SLUG_INTERIM_RE.search(filename):
        return "interim", None
    if _SLUG_ANNUAL_RE.search(filename):
        return "annual", None

    return "unknown", None


def _period_from_first_page(text: str) -> tuple[str, Optional[str]]:
    m = _PAGE1_INTERIM_RE.search(text)
    if m:
        year = int(m.group(1))
        return "interim", f"{year}-06"
    m = _PAGE1_ANNUAL_RE.search(text)
    if m:
        year = int(m.group(1))
        return "annual", f"{year - 1}-12"
    return "unknown", None


# ---------------------------------------------------------------------------
# GGR value extraction from narrative text
# ---------------------------------------------------------------------------

# Matches e.g.: "€6.2 billion", "€6,2 billion", "€6.2bn", "€6 200 million",
#               "EUR 6.2 billion", "EUR 1 234 million"
_EUR_VALUE_RE = re.compile(
    r"(?:€|EUR\s*)(\d[\d\s,\.]*)\s*(billion|million|bn|mln|m\b)",
    re.IGNORECASE,
)

# Vertical hint patterns: match a GGR sentence containing a vertical keyword.
# e.g. "Casino GGR amounted to €4.2 billion"
_CASINO_PATTERN = re.compile(
    r"(?:casino|b2c\s+casino|remote\s+casino)[^.]{0,300}?(?:€|EUR\s*)(\d[\d\s,\.]*)\s*(billion|million|bn|mln|m\b)",
    re.IGNORECASE | re.DOTALL,
)
_SPORTS_PATTERN = re.compile(
    r"(?:sports?\s+(?:betting|wagering)|b2c\s+sports)[^.]{0,300}?(?:€|EUR\s*)(\d[\d\s,\.]*)\s*(billion|million|bn|mln|m\b)",
    re.IGNORECASE | re.DOTALL,
)
_POKER_PATTERN = re.compile(
    r"(?:poker|online\s+poker)[^.]{0,300}?(?:€|EUR\s*)(\d[\d\s,\.]*)\s*(billion|million|bn|mln|m\b)",
    re.IGNORECASE | re.DOTALL,
)
_LOTTERY_PATTERN = re.compile(
    r"(?:lottery|lotto)[^.]{0,300}?(?:€|EUR\s*)(\d[\d\s,\.]*)\s*(billion|million|bn|mln|m\b)",
    re.IGNORECASE | re.DOTALL,
)
_TOTAL_GGR_PATTERN = re.compile(
    r"(?:total\s+ggr|total\s+gross\s+gaming\s+revenue|overall\s+ggr)[^.]{0,300}?"
    r"(?:€|EUR\s*)(\d[\d\s,\.]*)\s*(billion|million|bn|mln|m\b)",
    re.IGNORECASE | re.DOTALL,
)

# Estimated vertical splits from MGA publications (used when per-vertical
# figures are not individually parseable from a scanned PDF).
# Based on publicly cited breakdown in MGA 2022 Annual Report and subsequent years.
_DEFAULT_SPLITS: dict[str, float] = {
    "igaming":         0.64,  # B2C Casino (largest segment)
    "sports-wagering": 0.24,  # B2C Sports
    "online-poker":    0.07,  # B2C Poker
    "lottery":         0.05,  # B2C Lottery / Lotto
}

# Sanity bounds (EUR millions) for each vertical; values outside rejected as noise
_BOUNDS: dict[str, tuple[float, float]] = {
    "igaming":         (500,  15_000),
    "sports-wagering": (100,   5_000),
    "online-poker":    (20,    1_000),
    "lottery":         (10,      500),
    "total":           (1_000, 30_000),
}


def _parse_eur_value(val_str: str, unit: str) -> float:
    """Convert a matched EUR value string + unit to EUR millions.

    MGA reports use both:
      - Continental format: "6,2 billion" → 6.2 billion
      - English format: "6.2 billion" → 6.2 billion
      - Space thousands separator: "1 234 million" → 1234 million
      - European dot-thousands: "6.200 million" → 6200 million
    """
    # Remove spaces (thousands separator)
    val_str = val_str.replace(" ", "")
    unit_lower = unit.lower().rstrip(".")

    # If string has both '.' and ',' treat comma as decimal (Continental) or
    # strip as thousands separator.  Simple heuristic: if comma comes after
    # last dot, comma is decimal.
    if "," in val_str and "." in val_str:
        if val_str.index(",") > val_str.index("."):
            val_str = val_str.replace(".", "").replace(",", ".")
        else:
            val_str = val_str.replace(",", "")
    elif "," in val_str:
        # Comma-only: distinguish thousands separator from decimal separator.
        # Heuristic: if there is exactly one comma and the substring after it
        # has exactly 3 digits, it is a thousands separator (e.g. "6,200" →
        # 6200).  Otherwise treat as decimal (e.g. "6,2" → 6.2).
        comma_idx = val_str.index(",")
        after_comma = val_str[comma_idx + 1:]
        if len(after_comma) == 3 and after_comma.isdigit():
            val_str = val_str.replace(",", "")   # thousands sep → strip
        else:
            val_str = val_str.replace(",", ".")  # decimal sep → dot
    elif "." in val_str:
        # Dot-only: could be decimal ("6.2") or European thousands ("6.200").
        # Heuristic: if every dot-separated segment after the first has exactly
        # 3 digits, all dots are thousands separators (e.g. "1.234.567" or
        # "6.200").  Otherwise treat as decimal.
        segments = val_str.split(".")
        if len(segments) > 1 and all(len(s) == 3 and s.isdigit() for s in segments[1:]):
            val_str = val_str.replace(".", "")
    # else: plain integer — use as-is

    try:
        value = float(val_str)
    except ValueError:
        return 0.0

    if unit_lower in ("billion", "bn"):
        return value * 1_000  # → EUR millions
    # million / mln / m
    return value


def _extract_vertical_values(full_text: str) -> dict[str, Optional[float]]:
    """Try to extract per-vertical GGR values from the report text.

    Returns a dict keyed by MetricFact vertical strings; values are EUR
    millions or None if not found.  Also includes key "total" for total GGR
    if parseable.
    """
    result: dict[str, Optional[float]] = {
        "total":           None,
        "igaming":         None,
        "sports-wagering": None,
        "online-poker":    None,
        "lottery":         None,
    }

    def _first_match(pattern: re.Pattern, vert_key: str, lo: float, hi: float) -> None:
        for m in pattern.finditer(full_text):
            val = _parse_eur_value(m.group(1), m.group(2))
            if lo <= val <= hi:
                result[vert_key] = val
                return

    _first_match(_TOTAL_GGR_PATTERN,  "total",           *_BOUNDS["total"])
    _first_match(_CASINO_PATTERN,     "igaming",          *_BOUNDS["igaming"])
    _first_match(_SPORTS_PATTERN,     "sports-wagering",  *_BOUNDS["sports-wagering"])
    _first_match(_POKER_PATTERN,      "online-poker",     *_BOUNDS["online-poker"])
    _first_match(_LOTTERY_PATTERN,    "lottery",          *_BOUNDS["lottery"])

    return result


# ---------------------------------------------------------------------------
# _spread_monthly: distribute an aggregate value across N months
# ---------------------------------------------------------------------------


def _spread_monthly(
    value_eur_mln: float,
    end_period: str,
    vertical: str,
    source_url: str,
    span_months: int,
) -> list[MetricFact]:
    """Distribute a single aggregate value across ``span_months`` ending at
    ``end_period`` (``YYYY-MM``).

    The sum of the emitted monthly MetricFacts equals the original aggregate.
    This keeps the monthly chart smooth rather than a single spike.
    """
    total_cents = _eur_millions_to_usd_cents(value_eur_mln)
    monthly_cents = total_cents // span_months
    yr, mo = int(end_period[:4]), int(end_period[5:7])
    out: list[MetricFact] = []
    for offset in range(span_months):
        m = mo - offset
        y = yr
        while m < 1:
            m += 12
            y -= 1
        out.append(MetricFact(
            state="MT",
            operator="MT Statewide",
            vertical=vertical,
            period=f"{y:04d}-{m:02d}",
            metric_name="ggr",
            value_usd_cents=monthly_cents,
            source_url=source_url,
        ))
    return out


# ---------------------------------------------------------------------------
# Core parse function
# ---------------------------------------------------------------------------


def _parse_mga_pdf(
    pdf: pdfplumber.PDF,
    period: str,
    report_type: str,
    source_url: str,
) -> list[MetricFact]:
    """Extract GGR MetricFacts from an MGA annual or interim PDF.

    Strategy:
    1. Extract all text via pdfplumber ``extract_text()`` (handles embedded
       font streams better than table extraction for MGA's InDesign exports).
    2. Attempt per-vertical regex extraction (casino, sports, poker, lottery).
    3. If per-vertical values are missing but total GGR is found, allocate
       using the _DEFAULT_SPLITS ratios derived from published MGA data.
    4. If no GGR values are parseable at all (fully scanned-image PDF),
       return [] gracefully rather than raising.

    Spread:
      - Annual / oversight reports → span_months = 12
      - Interim (H1) reports → span_months = 6
    """
    full_text = "\n".join(
        (page.extract_text() or "") for page in pdf.pages
    )

    values = _extract_vertical_values(full_text)
    total_eur = values.get("total")

    # Determine spread window
    span = 6 if report_type == "interim" else 12

    facts: list[MetricFact] = []
    verticals_emitted: set[str] = set()

    # Emit per-vertical values that were directly parsed
    for vert in ("igaming", "sports-wagering", "online-poker", "lottery"):
        v_mln = values.get(vert)
        if v_mln and v_mln > 0:
            facts.extend(_spread_monthly(v_mln, period, vert, source_url, span))
            verticals_emitted.add(vert)

    # For missing verticals, fall back to total × default split
    if total_eur and total_eur > 0:
        for vert, split in _DEFAULT_SPLITS.items():
            if vert not in verticals_emitted:
                derived = total_eur * split
                facts.extend(_spread_monthly(derived, period, vert, source_url, span))
    elif not verticals_emitted:
        # Fully opaque PDF — return empty; caller logs gracefully
        return []

    return facts


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def parse_mt_pdf(path: Path, source_url: str) -> list[MetricFact]:
    """Parse an MGA annual or interim report PDF and return MetricFact rows.

    Parameters
    ----------
    path:
        ``pathlib.Path`` to the downloaded PDF.
    source_url:
        The URL the PDF was fetched from (stored verbatim in each fact).

    Returns
    -------
    list[MetricFact]
        Zero or more facts with:
          state           = "MT"
          operator        = "MT Statewide"
          vertical        = "igaming" | "sports-wagering" | "online-poker" | "lottery"
          period          = "YYYY-MM" (end-month of reporting window)
          metric_name     = "ggr"
          value_usd_cents = int(eur_value × 1.08 × 100)

    Returns [] gracefully when no GGR data can be found.

    Period heuristics (applied in order):
      1. source_url basename (e.g. "MGA-Annual-Report-2023.pdf") — preferred;
         the backfill cache slug (path.name) lacks the year so is tried second.
      2. path.name fallback — only useful if the file was stored with the
         original name rather than a hash slug.
      3. First-page text extraction.

      Annual report YYYY  → data year YYYY-1 → period ``{YYYY-1}-12``
      Interim report YYYY → H1 of YYYY       → period ``{YYYY}-06``
      Oversight YYYY      → data year YYYY-1 → period ``{YYYY-1}-12``
    """
    try:
        # Prefer the original URL filename over the cache slug (which lacks the
        # year and causes _detect_report_type to return "unknown").
        url_basename = source_url.rsplit("/", 1)[-1] if source_url else ""
        report_type, period = _detect_report_type(url_basename)

        if report_type == "unknown" or period is None:
            # Fallback: try the local path name (works when stored with original name)
            report_type, period = _detect_report_type(path.name)

        with pdfplumber.open(str(path)) as pdf:
            if report_type == "unknown" or period is None:
                first_text = (pdf.pages[0].extract_text() or "") if pdf.pages else ""
                report_type, period = _period_from_first_page(first_text)

            if period is None:
                return []

            return _parse_mga_pdf(pdf, period, report_type, source_url)

    except Exception:  # noqa: BLE001 — defensive; never crash the collector
        return []
