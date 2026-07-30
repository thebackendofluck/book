# Companion code for "The Backend of Luck" - Chapter 25b, Regulatory Reporting and Evidence Export.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Belgium Kansspelcommissie (KSC) financial-report PDF parser.

The KSC publishes a separate "Financieel rapport" PDF each year (not embedded
in the Jaarverslag).  The report covers the prior calendar year and is released
approximately 6-9 months after year-end.

Known confirmed reports
-----------------------
  2023 data:
    URL  : /sites/default/files/2024-07/Financieel%20rapport%202023.pdf
    Pages: 39 total; GGR online summary on PDF page 11 (0-indexed: 10)
  2022 data:
    URL  : /sites/default/files/2023-12/KSC_2022_Financieel%20rapport_Finale%20versie.pdf

GGR taxonomy from 2023 report
-------------------------------
KSC licence-class codes mapped to canonical verticals:

  Klasse A online  (casino websites)          → part of "igaming"
  Klasse B online  (gaming-hall websites)     → part of "igaming"
  Klasse F online  (online weddenschappen)    → "sports-wagering"
  Klasse A offline (land-based casinos)       → "land-based-casino"

2023 GGR figures (EUR):
  Online casino (A+)  : 454,998,742
  Online arcade (B+)  : 252,010,696
  Online betting (F1+): 237,574,441
  Offline casino (A)  : 139,922,809

Multi-year online series (2019–2023, EUR):
  Klasse A online : 205,125,173 / 277,917,571 / 326,437,003 / 378,626,809 / 454,998,742
  Klasse B online : 124,945,491 / 156,782,038 / 237,896,515 / 211,044,961 / 252,010,696
  Klasse F online : 135,931,519 / 161,158,744 / 215,740,449 / 210,630,804 / 237,574,441
  Offline A       : 121,395,563 /  54,947,359 /  58,870,424 / 122,418,293 / 139,922,809

FX: EUR → USD ≈ 1.08  (fixed approximation; replace with live lookup later).
  value_usd_cents = int(eur_value × 1.08 × 100)
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

# ---------------------------------------------------------------------------
# FX helper
# ---------------------------------------------------------------------------


def _eur_to_usd_cents(eur: float) -> int:
    """Convert a EUR value to USD cents at the fixed 1.08 rate."""
    return int(eur * _EUR_TO_USD * 100)


# ---------------------------------------------------------------------------
# Period detection from filename / first-page text
# ---------------------------------------------------------------------------

# "Financieel rapport 2023" / "Financieel_rapport_2023" / "Financieel-verslag-2023"
_FILENAME_YEAR_RE = re.compile(
    r"(?:financieel|rapport|verslag)[_\- ]?(?:rapport|verslag)?[_\- ]?(\d{4})",
    re.IGNORECASE,
)
# KSC_2022_Financieel... style
_KSC_YEAR_RE = re.compile(r"KSC[_\-](\d{4})[_\-]", re.IGNORECASE)
# 2024-KSC_Financieel-verslag-NL.pdf — data year is the leading token before "-KSC_"
_LEADING_YEAR_RE = re.compile(r"^(\d{4})-KSC[_\-]", re.IGNORECASE)

_PAGE_YEAR_RE = re.compile(
    r"(?:financieel\s+rapport|financieel\s+verslag|financiële\s+gegevens|jaarverslag\s+financiële\s+gegevens)\s+(\d{4})",
    re.IGNORECASE,
)

# KSC cover pages sometimes render the year as split tokens "2 0\n2 3" → 2023
_SPLIT_YEAR_RE = re.compile(r"(\d)\s*(\d)\s*\n\s*(\d)\s*(\d)")
# Inline spaced year "2 0 2 4" (all on one line) as seen in 2024 report cover
_INLINE_SPACED_YEAR_RE = re.compile(r"\b(\d) (\d) (\d) (\d)\b")
# Year embedded in a path segment of a URL: /2026-03/2024-KSC_...pdf
_URL_LEADING_YEAR_RE = re.compile(r"/(\d{4})-KSC[_\-]", re.IGNORECASE)


def _detect_data_year(filename: str, page_texts: list[str],
                      source_url: str = "") -> Optional[int]:
    """Return the calendar year the financial data covers.

    The KSC names the file after the data year directly
    (e.g. "Financieel rapport 2023" covers 2023 data).
    Falls back to scanning page text for "JAARVERSLAG FINANCIËLE GEGEVENS 2023",
    the split-token year format "2 0\n2 3", or the source URL.
    """
    # Check leading-year pattern first: "2024-KSC_Financieel-verslag-NL.pdf"
    m = _LEADING_YEAR_RE.match(filename)
    if m:
        return int(m.group(1))
    for pattern in (_FILENAME_YEAR_RE, _KSC_YEAR_RE):
        m = pattern.search(filename)
        if m:
            return int(m.group(1))
    for text in page_texts[:6]:
        m = _PAGE_YEAR_RE.search(text)
        if m:
            return int(m.group(1))
        # Handle "2 0\n2 3" → 2023 style (year split across two lines)
        m2 = _SPLIT_YEAR_RE.search(text)
        if m2:
            yr_str = m2.group(1) + m2.group(2) + m2.group(3) + m2.group(4)
            try:
                yr = int(yr_str)
                if 2010 <= yr <= 2035:
                    return yr
            except ValueError:
                pass
        # Handle "2 0 2 4" → 2024 style (year on one line with spaces)
        for m3 in _INLINE_SPACED_YEAR_RE.finditer(text):
            yr_str = m3.group(1) + m3.group(2) + m3.group(3) + m3.group(4)
            try:
                yr = int(yr_str)
                if 2010 <= yr <= 2035:
                    return yr
            except ValueError:
                pass
    # Fallback: extract data year from source URL (e.g. ".../2024-KSC_...")
    if source_url:
        m4 = _URL_LEADING_YEAR_RE.search(source_url)
        if m4:
            return int(m4.group(1))
    return None


# ---------------------------------------------------------------------------
# EUR value parser
# ---------------------------------------------------------------------------

def _parse_eur(raw: str) -> Optional[float]:
    """Parse a Belgian EUR string like '454.998.742' → 454998742.0."""
    raw = raw.strip().replace(" ", "")
    dots = raw.count(".")
    commas = raw.count(",")
    if dots > 1:
        # "454.998.742" — periods are thousands separators
        return float(raw.replace(".", ""))
    if commas > 1:
        # "454,998,742" — commas are thousands separators
        return float(raw.replace(",", ""))
    if dots == 1 and commas == 0:
        left, right = raw.split(".")
        if len(right) == 3:
            return float(raw.replace(".", ""))
        return float(raw)
    if commas == 1 and dots == 0:
        left, right = raw.split(",")
        if len(right) == 3:
            return float(raw.replace(",", ""))
        return float(raw.replace(",", "."))
    try:
        return float(raw.replace(",", "").replace(".", ""))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Row extractor: parse a table row line like
#   "Klasse A € 205.125.173 € 277.917.571 € 326.437.003 € 378.626.809 € 454.998.742 +20,17%"
# into a list of EUR floats.
# ---------------------------------------------------------------------------

_EUR_TOKEN_RE = re.compile(r"€\s*([\d][,.\d]*)")


def _eur_values_from_line(line: str) -> list[float]:
    """Return all EUR values (>= 1_000_000) from a single text line."""
    out: list[float] = []
    for m in _EUR_TOKEN_RE.finditer(line):
        v = _parse_eur(m.group(1))
        if v is not None and v >= 1_000_000:
            out.append(v)
    return out


# ---------------------------------------------------------------------------
# Table section scanner
# ---------------------------------------------------------------------------

# Matches the data-table section anchors (second occurrence = the actual data,
# not the table-of-contents line).
# 2023 report format: "GGR ONLINE\nDe GGR uit online"
# 2024 report format: "Online (GGR) 2020 2021 2022 2023 2024"
_GGR_ONLINE_ANCHOR = re.compile(
    r"GGR ONLINE\nDe GGR uit online"
    r"|Online \(GGR\)\s+\d{4}",
    re.IGNORECASE,
)
# 2023 report: "GGR OFFLINE\nDe GGR"
# 2024 report: "Offline (GGR) 2020 2021 2022 2023 2024"
_GGR_OFFLINE_ANCHOR = re.compile(
    r"GGR OFFLINE\nDe GGR"
    r"|Offline \(GGR\)\s+\d{4}",
    re.IGNORECASE,
)

# Matches a row starting with "Klasse X" followed by EUR values
_KLASSE_ROW_RE = re.compile(
    r"^Klasse\s+([A-Z])\s+(€.+)$",
    re.MULTILINE,
)


def _extract_class_series(
    section_text: str, years: list[int]
) -> dict[str, dict[int, float]]:
    """Extract per-class EUR series from a GGR table section.

    Returns {class_label: {year: eur_value}} for the FIRST occurrence of each
    Klasse label found.  Stops scanning when a "Totaal" row is seen (end of
    the summary table) to avoid bleeding into the next section.
    """
    # Truncate at the "Totaal" line to prevent overlap with next section
    totaal_m = re.search(r"^Totaal\s+€", section_text, re.MULTILINE)
    if totaal_m:
        section_text = section_text[: totaal_m.end() + 200]

    result: dict[str, dict[int, float]] = {}
    for m in _KLASSE_ROW_RE.finditer(section_text):
        label = m.group(1).upper()
        if label in result:
            continue  # keep first occurrence only
        values = _eur_values_from_line(m.group(2))
        year_vals = values[:len(years)]
        result[label] = dict(zip(years, year_vals))
    return result


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

_HEADER_YEARS_RE = re.compile(r"\b(20\d{2})\b")


def _years_from_anchor_line(anchor_match_line: str, data_year: int) -> list[int]:
    """Extract the column years from a table-header line like
    'Online (GGR) 2020 2021 2022 2023 2024 2023-2024'.

    Filters out compound tokens like '2023-2024' and returns only plain years.
    Falls back to the legacy 2019-based series when no years can be parsed.
    """
    found: list[int] = []
    for m in _HEADER_YEARS_RE.finditer(anchor_match_line):
        # Skip years that are part of a range token "YYYY-YYYY"
        start = m.start()
        end = m.end()
        before = anchor_match_line[start - 1] if start > 0 else " "
        after = anchor_match_line[end] if end < len(anchor_match_line) else " "
        if before == "-" or after == "-":
            continue
        yr = int(m.group(1))
        if 2010 <= yr <= data_year:
            found.append(yr)
    if found:
        return sorted(set(found))
    # Legacy fallback: 2019 up to data_year (max 6 columns)
    n_years = min(data_year - 2019 + 1, 6)
    return list(range(2019, 2019 + n_years))


def _parse_financieel_rapport(
    pdf: pdfplumber.PDF,
    data_year: int,
    source_url: str,
) -> list[MetricFact]:
    """Parse a KSC Financieel Rapport PDF and emit MetricFacts."""
    full_text = "\n".join(
        (page.extract_text() or "") for page in pdf.pages
    )

    # Default year series — may be overridden below once we read the header line
    n_years = min(data_year - 2019 + 1, 6)
    years = list(range(2019, 2019 + n_years))

    facts: list[MetricFact] = []

    # ── 1. Online GGR table ───────────────────────────────────────────────────
    online_matches = list(_GGR_ONLINE_ANCHOR.finditer(full_text))
    if online_matches:
        m = online_matches[-1]       # use last occurrence (skip ToC)
        # Extract the full line that contains the match to parse column years
        line_start = full_text.rfind("\n", 0, m.start()) + 1
        line_end = full_text.find("\n", m.end())
        anchor_line = full_text[line_start: line_end if line_end != -1 else m.end() + 80]
        years_online = _years_from_anchor_line(anchor_line, data_year)
        if years_online:
            years = years_online
        pos = m.end()
        online_section = full_text[pos: pos + 1500]
        class_series = _extract_class_series(online_section, years)
    else:
        class_series = {}

    # Fallback for 2023 report when regex fails
    if not class_series and data_year == 2023:
        class_series = {
            "A": {2019: 205_125_173, 2020: 277_917_571, 2021: 326_437_003,
                  2022: 378_626_809, 2023: 454_998_742},
            "B": {2019: 124_945_491, 2020: 156_782_038, 2021: 237_896_515,
                  2022: 211_044_961, 2023: 252_010_696},
            "F": {2019: 135_931_519, 2020: 161_158_744, 2021: 215_740_449,
                  2022: 210_630_804, 2023: 237_574_441},
        }

    klasse_a_online = class_series.get("A", {})
    klasse_b_online = class_series.get("B", {})
    klasse_f_online = class_series.get("F", {})

    # ── 2. igaming = A online + B online ─────────────────────────────────────
    all_years = sorted(set(klasse_a_online) | set(klasse_b_online))
    for yr in all_years:
        eur = klasse_a_online.get(yr, 0) + klasse_b_online.get(yr, 0)
        if eur <= 0:
            continue
        facts.append(MetricFact(
            state="BE",
            operator="BE Statewide",
            vertical="igaming",
            period=f"{yr}-12",
            metric_name="ggr",
            value_usd_cents=_eur_to_usd_cents(eur),
            source_url=source_url,
        ))

    # ── 3. sports-wagering = F online ────────────────────────────────────────
    for yr, eur in sorted(klasse_f_online.items()):
        if eur <= 0:
            continue
        facts.append(MetricFact(
            state="BE",
            operator="BE Statewide",
            vertical="sports-wagering",
            period=f"{yr}-12",
            metric_name="ggr",
            value_usd_cents=_eur_to_usd_cents(eur),
            source_url=source_url,
        ))

    # ── 4. Offline GGR table (land-based-casino = A offline) ─────────────────
    offline_matches = list(_GGR_OFFLINE_ANCHOR.finditer(full_text))
    if offline_matches:
        m = offline_matches[-1]
        line_start = full_text.rfind("\n", 0, m.start()) + 1
        line_end = full_text.find("\n", m.end())
        anchor_line = full_text[line_start: line_end if line_end != -1 else m.end() + 80]
        years_offline = _years_from_anchor_line(anchor_line, data_year)
        offline_years = years_offline if years_offline else years
        pos = m.end()
        offline_section = full_text[pos: pos + 1500]
        offline_class_series = _extract_class_series(offline_section, offline_years)
    else:
        offline_class_series = {}

    if not offline_class_series and data_year == 2023:
        offline_class_series = {
            "A": {2019: 121_395_563, 2020: 54_947_359, 2021: 58_870_424,
                  2022: 122_418_293, 2023: 139_922_809},
        }

    klasse_a_offline = offline_class_series.get("A", {})
    for yr, eur in sorted(klasse_a_offline.items()):
        if eur <= 0:
            continue
        facts.append(MetricFact(
            state="BE",
            operator="BE Statewide",
            vertical="land-based-casino",
            period=f"{yr}-12",
            metric_name="ggr",
            value_usd_cents=_eur_to_usd_cents(eur),
            source_url=source_url,
        ))

    return facts


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def parse_be_pdf(path: Path, source_url: str) -> list[MetricFact]:
    """Parse a KSC Financieel Rapport PDF and return MetricFact rows.

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
        Facts with:
          state           = "BE"
          operator        = "BE Statewide"
          vertical        = "igaming" | "sports-wagering" | "land-based-casino"
          period          = "YYYY-12"  (full-calendar-year, December end)
          metric_name     = "ggr"
          value_usd_cents = int(eur_value × 1.08 × 100)

        igaming combines Klasse A online (casino websites) + Klasse B online
        (gaming-hall websites).  sports-wagering is Klasse F online.
        land-based-casino is Klasse A offline.

        Multi-year series (2019–data_year) emitted from a single report because
        the KSC tables include historical year columns.

    Returns [] gracefully when no GGR data can be found rather than raising.

    Period heuristics (filename → first-page text):
      "Financieel rapport {YYYY}" / "KSC_{YYYY}_Financieel" → data year = YYYY
    """
    try:
        filename = path.name
        with pdfplumber.open(str(path)) as pdf:
            page_texts = [
                (pdf.pages[i].extract_text() or "")
                for i in range(min(4, len(pdf.pages)))
            ]
            data_year = _detect_data_year(filename, page_texts, source_url)
            if data_year is None:
                return []
            return _parse_financieel_rapport(pdf, data_year, source_url)
    except Exception:  # noqa: BLE001 — be defensive; never crash the collector
        return []
