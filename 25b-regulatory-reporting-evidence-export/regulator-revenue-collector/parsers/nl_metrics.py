# Companion code for "The Backend of Luck" - Chapter 25b, Regulatory Reporting and Evidence Export.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Netherlands Kansspelautoriteit (KSA) PDF parser.

KSA publishes two PDF series:

1. **Marktscan kansspelen** (annual) — released Nov/Dec of following year.
   Filename pattern: ``{YYYY}{MM}{DD}_ksa_marktscan_kansspelen_{YYYY}.pdf``
   Contains full-year BSR for online casino and sports wagering in the *text*
   body (not structured tables). pdfplumber ``extract_tables()`` returns only
   chart-artefact rows (all None) because the BSR figures are embedded inside
   vector chart objects. The parser therefore uses regex over ``extract_text()``
   output on a per-page basis to locate BSR values quoted in narrative sentences
   such as "online sportweddenschappen … €353 miljoen" and
   "online casinospelen … 46% … €2,4 miljard".
   Period mapping: ``marktscan_*_{YYYY}`` → ``{YYYY}-12``.

2. **Monitoringsrapportage online kansspelen** (semi-annual, online only).
   Filename pattern includes ``voorjaar_{YYYY}`` (spring, covers H2 prior year)
   or ``najaar_{YYYY}`` (autumn, covers H1 same year).
   This report's BSR chart caption text is extracted by pdfplumber as a table
   row with the pattern::

       ['', "S1'22:\\n€484mln", "S2'22:\\n€600mln", ...]

   The parser scans all tables across all pages for cells matching
   ``S[12]'\\d{2}:\\n€\\d+mln`` and derives semi-annual totals from them.
   Period mapping: ``voorjaar_{YYYY}`` → H2 of prior year → ``{YYYY-1}-12``;
   ``najaar_{YYYY}`` → H1 of same year → ``{YYYY}-06``.

Actual BSR values captured (as of the two sample PDFs fetched 2026-04):

Marktscan 2025 (year 2024 data):
  - Online casino:  €1 113 M  (46 % of total casino BSR €2 420 M)
  - Online sports:  €353 M

Monitoringsrapportage voorjaar 2026 (H2 2025 data, online total):
  - S1'22: €484 M, S2'22: €600 M
  - S1'23: €692 M, S2'23: €696 M
  - S1'24: €777 M, S2'24: €697 M
  - S1'25: €600 M, S2'25: €602 M
  Segment split applied: casino 78 %, sports 20 %, remainder omitted (<2 %).

FX: EUR → USD ≈ 1.08  (fixed approximation; replace with live lookup later).
value_usd_cents = int(eur_value * 1.08 * 100)
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

import pdfplumber

from .metrics_model import MetricFact

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EUR_TO_USD: float = 1.08

# Semi-annual segment splits observed in monitoringsrapportage (stable across
# multiple editions; casino-vs-players 1.8 % and horse racing 0.2 % omitted).
_ONLINE_CASINO_SHARE: float = 0.78
_ONLINE_SPORTS_SHARE: float = 0.20

# ---------------------------------------------------------------------------
# FX helper
# ---------------------------------------------------------------------------


def _eur_millions_to_usd_cents(eur_millions: float) -> int:
    """Convert a BSR value in EUR millions to USD cents."""
    return int(eur_millions * 1_000_000 * _EUR_TO_USD * 100)


# ---------------------------------------------------------------------------
# Filename → report-type and period detection
# ---------------------------------------------------------------------------

# marktscan: 20251121_ksa_marktscan_kansspelen_2025.pdf
_MARKTSCAN_RE = re.compile(
    r"marktscan[_\-]kansspelen[_\-]?(\d{4})", re.IGNORECASE
)
# voorjaar / najaar: monitoringsrapportage_online_kansspelen_voorjaar_2026.pdf
_VOORJAAR_RE = re.compile(r"voorjaar[_\- ]?(\d{4})", re.IGNORECASE)
_NAJAAR_RE = re.compile(r"najaar[_\- ]?(\d{4})", re.IGNORECASE)


def _detect_report_type(
    filename: str,
) -> tuple[str, Optional[str]]:
    """Return (report_type, period) from filename.

    report_type: "marktscan" | "voorjaar" | "najaar" | "unknown"
    period:      "YYYY-MM" or None if undetermined.
    """
    m = _MARKTSCAN_RE.search(filename)
    if m:
        year = int(m.group(1))
        # The marktscan published in year Y covers year Y-1.
        # File "marktscan_kansspelen_2025" → data year 2024 → period 2024-12.
        return "marktscan", f"{year - 1}-12"

    m = _VOORJAAR_RE.search(filename)
    if m:
        year = int(m.group(1))
        # voorjaar YYYY → covers H2 of year YYYY-1 → period YYYY-1-12
        return "voorjaar", f"{year - 1}-12"

    m = _NAJAAR_RE.search(filename)
    if m:
        year = int(m.group(1))
        # najaar YYYY → covers H1 of year YYYY → period YYYY-06
        return "najaar", f"{year}-06"

    return "unknown", None


# ---------------------------------------------------------------------------
# First-page period detection (fallback for odd filenames)
# ---------------------------------------------------------------------------

# "Voorjaar 2026" / "Najaar 2025" / "Marktscan kansspelen 2025"
_PAGE1_VOORJAAR_RE = re.compile(r"voorjaar\s+(\d{4})", re.IGNORECASE)
_PAGE1_NAJAAR_RE = re.compile(r"najaar\s+(\d{4})", re.IGNORECASE)
_PAGE1_MARKTSCAN_RE = re.compile(
    r"marktscan\s+kansspelen\s+(\d{4})", re.IGNORECASE
)


def _period_from_first_page(text: str) -> tuple[str, Optional[str]]:
    m = _PAGE1_VOORJAAR_RE.search(text)
    if m:
        year = int(m.group(1))
        return "voorjaar", f"{year - 1}-12"
    m = _PAGE1_NAJAAR_RE.search(text)
    if m:
        year = int(m.group(1))
        return "najaar", f"{year}-06"
    m = _PAGE1_MARKTSCAN_RE.search(text)
    if m:
        year = int(m.group(1))
        return "marktscan", f"{year - 1}-12"
    return "unknown", None


# ---------------------------------------------------------------------------
# Marktscan parser: extract BSR from narrative text
# ---------------------------------------------------------------------------

# "online sportweddenschappen … €353 miljoen"  or  "€353 miljoen … online"
# Captures a EUR value (possibly with comma decimal) and "miljoen"/"miljard"
_EUR_VALUE_RE = re.compile(
    r"€\s*(\d[\d\.,]*)\s*(miljoen|miljard)", re.IGNORECASE
)

# "46%" followed by text relating to online casino share of casino total
_CASINO_ONLINE_PCT_RE = re.compile(
    r"(\d+)\s*%\s*(?:van\s+(?:de\s+)?(?:het\s+)?(?:totale?\s+)?)?(?:markt\s+voor\s+)?(?:casino|casino\s*spelen?\s+online)",
    re.IGNORECASE,
)


def _parse_eur_value(val_str: str, unit: str) -> float:
    """Convert a matched value string + unit to EUR millions."""
    # Dutch number format: "2,4" = 2.4; "1.113" = 1113 (thousands separator)
    val_str = val_str.replace(" ", "")
    # If comma present: treat as decimal separator
    if "," in val_str:
        val_str = val_str.replace(".", "").replace(",", ".")
    else:
        val_str = val_str.replace(".", "")
    value = float(val_str)
    if unit.lower() == "miljard":
        value *= 1000  # convert to millions
    return value


def _parse_marktscan(pdf: pdfplumber.PDF, period: str, source_url: str) -> list[MetricFact]:
    """Extract online casino and sports-wagering BSR from the annual marktscan.

    Strategy:
    1. Online sports: locate the chapter 5 BSR section (section 5.2.1) and
       extract the first plausible EUR-millions value for online sports
       ("online sport- en paardenweddenschappen (€NNN miljoen)").
    2. Online casino: locate section 4.2.1 in chapter 4, extract total casino
       BSR (€2.x miljard), and derive online share via:
       - explicit "aandeel voor online casinospelen … NN%" sentence, or
       - complement of the landgebonden share ("54% … landgebonden" → 46% online).
       Fallback: fixed 46 % share as observed in 2024 edition.

    The PDF does not provide per-quarter data — annual totals only.
    """
    full_text = "\n".join(
        (page.extract_text() or "") for page in pdf.pages
    )

    # ── Online sports wagering ──────────────────────────────────────────────
    # Section 5.2.1: "online sport- en paardenweddenschappen (€353 miljoen)"
    # The most reliable anchor: capture €NNN mil* immediately after "online sport"
    online_sports_mln: Optional[float] = None
    for m in re.finditer(
        r"online\s+sport[^.]{0,250}?€\s*(\d[\d\.,]*)\s*(miljoen|miljard)",
        full_text,
        re.IGNORECASE | re.DOTALL,
    ):
        val = _parse_eur_value(m.group(1), m.group(2))
        # Online sports for NL is in the range 100M–2000M; reject noise
        if 100 <= val <= 2_000:
            online_sports_mln = val
            break

    # Fallback: first EUR value in 50–1500 M range inside the sports section
    if online_sports_mln is None:
        ch5_start = full_text.lower().find("5.2.1")
        if ch5_start == -1:
            ch5_start = full_text.lower().find("5 sportweddenschappen")
        if ch5_start != -1:
            ch5_text = full_text[ch5_start : ch5_start + 2000]
            for m in _EUR_VALUE_RE.finditer(ch5_text):
                val = _parse_eur_value(m.group(1), m.group(2))
                if 50 <= val <= 1_500:
                    online_sports_mln = val
                    break

    # ── Online casino ───────────────────────────────────────────────────────
    # Section 4.2.1: "casinospelen in 2024 €2,42 miljard … Landgebonden … 54%"
    # Then "aandeel voor online casinospelen … 46%"
    online_casino_mln: Optional[float] = None

    # Anchor on section 4.2.1 (Brutospelresultaat for casino)
    ch4_start = full_text.lower().find("4.2.1")
    if ch4_start == -1:
        ch4_start = full_text.lower().find("4.2")
    if ch4_start == -1:
        ch4_start = full_text.lower().find("casinospelen")

    if ch4_start != -1:
        ch4_text = full_text[ch4_start : ch4_start + 3000]

        # Step 1: find total casino BSR (first EUR value in range 1000–5000 M)
        total_casino_mln: Optional[float] = None
        for m in _EUR_VALUE_RE.finditer(ch4_text):
            val = _parse_eur_value(m.group(1), m.group(2))
            if 1_000 <= val <= 5_000:
                total_casino_mln = val
                break

        if total_casino_mln is not None:
            online_pct: Optional[float] = None

            # Signal A: explicit "aandeel voor online casinospelen … NN%"
            sig_a = re.search(
                r"aandeel\s+voor\s+online\s+casino[^.]{0,100}?(\d+)\s*%",
                full_text,
                re.IGNORECASE | re.DOTALL,
            )
            if sig_a:
                pct = int(sig_a.group(1))
                if 10 <= pct <= 90:
                    online_pct = pct / 100.0

            # Signal B: landgebonden share stated, online = complement
            # e.g. "Landgebonden casinospelen … 54% van het totale BSR"
            if online_pct is None:
                sig_b = re.search(
                    r"landgebonden\s+casino[^.]{0,150}?(\d+)\s*%\s+van\s+het\s+totale",
                    ch4_text,
                    re.IGNORECASE | re.DOTALL,
                )
                if sig_b:
                    lb_pct = int(sig_b.group(1))
                    if 10 <= lb_pct <= 90:
                        online_pct = 1.0 - (lb_pct / 100.0)

            # Fallback: use 2024-edition observed split (46 %)
            if online_pct is None:
                online_pct = 0.46

            online_casino_mln = total_casino_mln * online_pct

    facts: list[MetricFact] = []

    # Annual marktscan → spread across 12 months for smooth monthly chart.
    if online_casino_mln is not None and online_casino_mln > 0:
        facts.extend(_spread_monthly(online_casino_mln, period, "igaming",
                                     source_url, span_months=12))

    if online_sports_mln is not None and online_sports_mln > 0:
        facts.extend(_spread_monthly(online_sports_mln, period, "sports-wagering",
                                     source_url, span_months=12))

    return facts


def _spread_monthly(value_eur_mln: float, end_period: str, vertical: str,
                    source_url: str, span_months: int) -> list[MetricFact]:
    """Distribute a single aggregate value across `span_months` ending at `end_period`.

    Sum across the synthetic monthly facts equals the original aggregate; the
    monthly chart line is smooth instead of a single tall spike.
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
            state="NL", operator="NL Statewide", vertical=vertical,
            period=f"{y:04d}-{m:02d}", metric_name="ggr",
            value_usd_cents=monthly_cents, source_url=source_url,
        ))
    return out


# ---------------------------------------------------------------------------
# Monitoringsrapportage parser: extract semi-annual BSR series from chart captions
# ---------------------------------------------------------------------------

# The BSR chart caption is captured by pdfplumber as a table cell value.
# pdfplumber renders the chart annotation text with a Unicode RIGHT SINGLE
# QUOTATION MARK U+2019 as the apostrophe, e.g.:
#   "S1’22:\n€484mln"  (actual newline, year 2022, H1)
#   "S2’25:\n€602mln"  (actual newline, year 2025, H2)
# Plain-text page extraction may produce ASCII apostrophe instead.
# Pattern handles both U+2019 and ASCII ' as the separator.
_SEMI_ANNUAL_CELL_RE = re.compile(
    r"S([12])[’'](\d{2})[:：][\s\\]*€(\d[\d\.,]*)mln",
    re.IGNORECASE,
)


def _parse_semi_annual_values(raw: str) -> list[tuple[str, float]]:
    """Parse all 'S[12]'YY: €NNNmln' tokens from a string.

    Returns list of (period, eur_millions) tuples.
    period is 'YYYY-06' for S1 (H1 ending June) and 'YYYY-12' for S2 (H2 ending Dec).
    """
    results: list[tuple[str, float]] = []
    for m in _SEMI_ANNUAL_CELL_RE.finditer(raw):
        half = int(m.group(1))   # 1 or 2
        yy = int(m.group(2))     # e.g. 22 → 2022
        year = 2000 + yy
        val_str = m.group(3).replace(",", ".")
        try:
            eur_mln = float(val_str)
        except ValueError:
            continue
        period = f"{year}-06" if half == 1 else f"{year}-12"
        results.append((period, eur_mln))
    return results


def _parse_monitoringsrapportage(
    pdf: pdfplumber.PDF, source_url: str
) -> list[MetricFact]:
    """Extract semi-annual online BSR series from a monitoringsrapportage PDF.

    The key data lives in the BSR chart caption on the 'Omvang van de legale
    markt' page. pdfplumber captures the annotation labels as table cell values
    in the form "S1'22:\\n€484mln".  The parser also scans raw page text for
    the same pattern.

    Each total semi-annual figure is split into:
      - igaming:        78 % of total online BSR
      - sports-wagering: 20 % of total online BSR
    (based on the stable segment split stated in all editions: ~78 % casino,
    ~20 % sports, ~2 % other; see report section 2.1.)
    """
    # Collect all candidate strings from tables and text across every page
    raw_candidates: list[str] = []

    for page in pdf.pages:
        # From tables
        for table in page.extract_tables():
            for row in table:
                for cell in row:
                    if cell is not None:
                        raw_candidates.append(str(cell))
        # From page text
        text = page.extract_text()
        if text:
            raw_candidates.append(text)

    # Deduplicate and parse
    seen_periods: set[str] = set()
    facts: list[MetricFact] = []

    for raw in raw_candidates:
        for period, eur_mln in _parse_semi_annual_values(raw):
            if period in seen_periods:
                continue
            seen_periods.add(period)

            casino_mln = eur_mln * _ONLINE_CASINO_SHARE
            sports_mln = eur_mln * _ONLINE_SPORTS_SHARE

            # Semi-annual data → spread across 6 months ending at the period.
            facts.extend(_spread_monthly(casino_mln, period, "igaming",
                                         source_url, span_months=6))
            facts.extend(_spread_monthly(sports_mln, period, "sports-wagering",
                                         source_url, span_months=6))

    return facts


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def parse_nl_pdf(path: Path, source_url: str) -> list[MetricFact]:
    """Parse a KSA PDF (marktscan or monitoringsrapportage) and return MetricFact rows.

    Parameters
    ----------
    path:
        ``pathlib.Path`` to the local PDF file.
    source_url:
        The URL the file was fetched from; stored verbatim in every
        ``MetricFact.source_url``.

    Returns
    -------
    list[MetricFact]
        One or more facts with:
          state           = "NL"
          operator        = "NL Statewide"
          vertical        = "igaming" | "sports-wagering"
          period          = "YYYY-MM"  (end-month of the reporting window)
          metric_name     = "ggr"
          value_usd_cents = int(eur_value × 1.08 × 100)

    Returns [] gracefully if no GGR data can be found rather than raising.

    Period heuristics (applied in order: cache slug → source_url → first-page text):
      - ``marktscan_kansspelen_{YYYY}``  → annual, period = ``{YYYY-1}-12``
      - ``voorjaar_{YYYY}``              → H2 prior year, period = ``{YYYY-1}-12``
      - ``najaar_{YYYY}``               → H1 same year,  period = ``{YYYY}-06``
    """
    try:
        filename = path.name
        report_type, period = _detect_report_type(filename)

        # Cache slugs lose the original filename (e.g. "NL-Statewide-igaming-annual-<hash>.pdf").
        # Try detecting from source_url (URL-decoded) before opening the PDF — it preserves
        # the real filename like "monitoringsrapportage_najaar_2025_1.pdf".
        if (report_type == "unknown" or period is None) and source_url:
            url_filename = unquote(source_url.rsplit("/", 1)[-1])
            report_type, period = _detect_report_type(url_filename)

        with pdfplumber.open(str(path)) as pdf:
            # If both filename and URL detection failed, try first page text
            if report_type == "unknown" or period is None:
                first_text = (pdf.pages[0].extract_text() or "") if pdf.pages else ""
                report_type, period = _period_from_first_page(first_text)

            if period is None:
                # Cannot determine period — bail out gracefully
                return []

            if report_type == "marktscan":
                return _parse_marktscan(pdf, period, source_url)
            else:
                # voorjaar / najaar / unknown-but-period-found
                return _parse_monitoringsrapportage(pdf, source_url)

    except Exception:  # noqa: BLE001 — be defensive; never crash the collector
        return []
