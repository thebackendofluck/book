# Companion code for "The Backend of Luck" - Chapter 25b, Regulatory Reporting and Evidence Export.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Romania ONJN annual activity report PDF parser.

ONJN publishes yearly "Raport de Activitate" PDFs that contain GGR (venituri
brute din jocuri de noroc) broken down by segment. The key Romanian terms:

  venituri            = revenue / GGR
  venituri brute      = gross gaming revenue (GGR)
  pariuri sportive    = sports betting
  cazino online       = online casino / iGaming
  slot-machine        = land-based slots / VGT
  aparate de joc      = gaming machines / VGT
  poker online        = online poker
  jocuri de noroc     = gambling / games of chance

Currency: Romanian Leu (RON). FX rate: 1 RON ≈ 0.22 USD (fixed approximation).
  value_usd_cents = int(ron_value * 0.22 * 100)

Typical GGR values in the annual reports (in millions RON):
  2022: online casino ~2 200 M RON, sports ~1 100 M RON
  2023: online casino ~2 700 M RON, sports ~1 300 M RON

The parser is intentionally lenient — it attempts multiple regex strategies in
order and returns an empty list (never raises) when it cannot extract data.
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

_RON_TO_USD: float = 0.22

# Vertical keyword → canonical vertical name
_VERTICAL_KEYWORDS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"cazino\s+online|casino\s+online", re.IGNORECASE),   "igaming"),
    (re.compile(r"pariuri\s+sportive|sport(?:ive)?(?:\s+betting)?",
                re.IGNORECASE),                                        "sports-wagering"),
    (re.compile(r"slot[- ]machine|aparate?\s+de\s+joc|vlt|vgt",
                re.IGNORECASE),                                        "land-based-casino"),
    (re.compile(r"poker\s+online",                                     re.IGNORECASE), "online-poker"),
    (re.compile(r"loterie|lottery",                                    re.IGNORECASE), "lottery"),
]

# Romanian number format: "2.153,4" (period = thousands separator, comma = decimal)
# Also handles plain integers and values like "1 153" (space as thousands separator)
_RON_VALUE_RE = re.compile(
    r"(\d[\d\s.]*(?:,\d+)?)\s*(?:mil(?:ioane)?\.?\s*)?(?:RON|lei)",
    re.IGNORECASE,
)

# Milioane RON / miliarde RON explicit
_MILIOANE_RE = re.compile(
    r"(\d[\d\s.,]*)\s+(?:mil(?:ioane)\s+)?RON",
    re.IGNORECASE,
)

# Year from filename or first-page title: "raport de activitate 2023"
_YEAR_FROM_FILENAME_RE = re.compile(r"(\d{4})")
_YEAR_FROM_TEXT_RE = re.compile(
    r"(?:raport\s+de\s+activitate|activitate\s+(?:onjn\s+)?)(\d{4})",
    re.IGNORECASE,
)

# Generic "YYYY" inside a sentence like "în anul 2023"
_YEAR_IN_SENTENCE_RE = re.compile(r"în\s+an(?:ul)?\s+(\d{4})", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_ron_value(raw: str) -> Optional[float]:
    """Parse a Romanian-formatted number string to a float (in RON).

    Romanian convention: "2.153.400,50" → 2_153_400.50
    Also handles "2 153 400" (space separators) and plain "1234567".
    The parser converts to raw RON, NOT millions.
    """
    s = raw.strip().replace("\xa0", " ")
    # Strip trailing/leading spaces
    s = s.strip()
    if not s:
        return None
    # Determine if last comma is decimal separator:
    # "2.153,4" → decimal; "2,153,400" → unusual; treat comma as decimal when
    # there is exactly one comma and it appears after a dot or digit group.
    has_dot = "." in s
    has_comma = "," in s
    if has_comma and has_dot:
        # Romanian: dots are thousands seps, comma is decimal sep
        s = s.replace(".", "").replace(",", ".")
    elif has_comma and not has_dot:
        # Could be decimal-only comma: "2,4" → 2.4
        # or thousands-only: "2,153" — ambiguous; treat as decimal if < 4 digits
        # after comma
        parts = s.split(",")
        if len(parts) == 2 and len(parts[1]) <= 2:
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    elif has_dot and not has_comma:
        # Dots could be thousands seps: "2.153.400" → remove dots
        # or a single decimal: "2.4" → keep
        dot_parts = s.split(".")
        if len(dot_parts) > 2:
            s = s.replace(".", "")
        elif len(dot_parts) == 2 and len(dot_parts[1]) <= 2:
            pass  # decimal dot, keep as-is
        else:
            s = s.replace(".", "")
    # Remove remaining whitespace (space thousands separators)
    s = s.replace(" ", "")
    try:
        return float(s)
    except ValueError:
        return None


def _ron_to_usd_cents(ron_value: float) -> int:
    """Convert raw RON to USD cents at fixed rate 1 RON = 0.22 USD."""
    return int(ron_value * _RON_TO_USD * 100)


def _detect_year(path: Path, first_page_text: str) -> Optional[int]:
    """Detect the reporting year from filename then first-page text."""
    # Filename: prefer the last 4-digit year in the name
    years_in_name = _YEAR_FROM_FILENAME_RE.findall(path.stem)
    if years_in_name:
        # Filter plausible gambling-report years (2013–current+1)
        candidates = [int(y) for y in years_in_name if 2013 <= int(y) <= 2030]
        if candidates:
            return max(candidates)

    # First page: "Raport de Activitate 2023"
    m = _YEAR_FROM_TEXT_RE.search(first_page_text)
    if m:
        yr = int(m.group(1))
        if 2013 <= yr <= 2030:
            return yr

    # "în anul 2023"
    m = _YEAR_IN_SENTENCE_RE.search(first_page_text)
    if m:
        yr = int(m.group(1))
        if 2013 <= yr <= 2030:
            return yr

    return None


def _detect_vertical(sentence: str) -> Optional[str]:
    """Map a sentence to a canonical vertical by keyword matching."""
    for pattern, vertical in _VERTICAL_KEYWORDS:
        if pattern.search(sentence):
            return vertical
    return None


# ---------------------------------------------------------------------------
# Core value extraction strategies
# ---------------------------------------------------------------------------

# Strategy A: structured table rows like:
#   "Cazino online  |  2.153.400.000 RON"
_TABLE_ROW_RE = re.compile(
    r"(cazino\s+online|pariuri\s+sportive|slot[- ]machine|poker\s+online|aparate?\s+de\s+joc)"
    r".{0,120}?"
    r"(\d[\d\s.,]{2,})\s*(?:RON|lei|milioane?\s+RON)",
    re.IGNORECASE | re.DOTALL,
)

# Strategy B: "venituri brute … X RON" after a vertical label in the same paragraph
_VENITURI_RE = re.compile(
    r"venituri\s+brute[^.]{0,200}?(\d[\d\s.,]{2,})\s*(?:RON|lei)",
    re.IGNORECASE | re.DOTALL,
)

# Strategy C: "X mil(?:ioane)? RON" (values stated in millions directly)
_MILLIONS_STATED_RE = re.compile(
    r"(\d[\d\s.,]*)\s+mil(?:ioane)?\s+(?:RON|lei)",
    re.IGNORECASE,
)


def _extract_facts_from_text(text: str, year: int, source_url: str) -> list[MetricFact]:
    """Extract MetricFacts from a block of PDF text using multiple strategies."""
    facts: list[MetricFact] = []
    seen: set[str] = set()  # dedup by vertical

    # Strategy A: regex over labelled table rows
    for m in _TABLE_ROW_RE.finditer(text):
        vertical = _detect_vertical(m.group(1))
        if vertical is None or vertical in seen:
            continue
        raw = m.group(2)
        ron = _parse_ron_value(raw)
        if ron is None or ron <= 0:
            continue
        # If value looks like it is in millions (< 100_000 raw), scale up
        if ron < 100_000:
            ron *= 1_000_000
        period = f"{year}-12"
        facts.append(MetricFact(
            state="RO",
            operator="RO Statewide",
            vertical=vertical,
            period=period,
            metric_name="ggr",
            value_usd_cents=_ron_to_usd_cents(ron),
            source_url=source_url,
        ))
        seen.add(vertical)

    # Strategy B: "milioane RON" — values stated explicitly in millions
    if not seen:
        # Walk sentences, look for vertical keyword close to a million-RON value
        sentences = re.split(r"[.\n]", text)
        for i, sentence in enumerate(sentences):
            vertical = _detect_vertical(sentence)
            if vertical is None or vertical in seen:
                continue
            # Look in this sentence and the next two for a RON value
            window = " ".join(sentences[i:i+3])
            for mm in _MILLIONS_STATED_RE.finditer(window):
                raw = mm.group(1)
                mln = _parse_ron_value(raw)
                if mln is None or mln <= 0:
                    continue
                ron = mln * 1_000_000
                period = f"{year}-12"
                facts.append(MetricFact(
                    state="RO",
                    operator="RO Statewide",
                    vertical=vertical,
                    period=period,
                    metric_name="ggr",
                    value_usd_cents=_ron_to_usd_cents(ron),
                    source_url=source_url,
                ))
                seen.add(vertical)
                break

    return facts


def _spread_annual_to_monthly(facts: list[MetricFact]) -> list[MetricFact]:
    """Distribute each annual fact evenly across 12 months ending in December.

    This avoids a single tall spike in monthly charts and is consistent with
    the approach used by the NL and SE parsers for annual reports.
    """
    out: list[MetricFact] = []
    for f in facts:
        yr = int(f.period[:4])
        monthly_cents = f.value_usd_cents // 12
        for mo in range(1, 13):
            out.append(MetricFact(
                state=f.state,
                operator=f.operator,
                vertical=f.vertical,
                period=f"{yr}-{mo:02d}",
                metric_name=f.metric_name,
                value_usd_cents=monthly_cents,
                source_url=f.source_url,
            ))
    return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def parse_ro_pdf(path: Path, source_url: str) -> list[MetricFact]:
    """Parse an ONJN annual activity report PDF and return MetricFact rows.

    Parameters
    ----------
    path:
        Local path to the downloaded PDF.
    source_url:
        URL the file was fetched from; stored in every MetricFact.source_url.

    Returns
    -------
    list[MetricFact]
        One MetricFact per (vertical, period) with:
            state           = "RO"
            operator        = "RO Statewide"
            vertical        = "igaming" | "sports-wagering" |
                              "land-based-casino" | "online-poker" | "lottery"
            period          = "YYYY-MM"  (monthly spread ending 12)
            metric_name     = "ggr"
            value_usd_cents = int(ron_ggr × 0.22 × 100)

        Returns [] gracefully if extraction fails — never raises.

    Currency conversion
    -------------------
    RON → USD: fixed rate 1 RON = 0.22 USD.
    Annual aggregates are spread evenly across 12 months for smooth charting.
    """
    try:
        with pdfplumber.open(str(path)) as pdf:
            if not pdf.pages:
                return []

            first_text = pdf.pages[0].extract_text() or ""
            year = _detect_year(path, first_text)
            if year is None:
                return []

            # Concatenate text from all pages
            full_text = "\n".join(
                (page.extract_text() or "") for page in pdf.pages
            )

            annual_facts = _extract_facts_from_text(full_text, year, source_url)
            if not annual_facts:
                return []

            return _spread_annual_to_monthly(annual_facts)

    except Exception:  # noqa: BLE001 — never let parser errors crash the collector
        return []
