# Companion code for "The Backend of Luck" - Chapter 25b, Regulatory Reporting and Evidence Export.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Greece HGC (ΕΕΕΠ) annual PDF parser.

HGC source PDFs
---------------
* **Annual Activity Reports** (``AnnualReport{YYYY}GR.pdf``, 2016–2024) —
  Greek-language; contain GGR tables expressed in EUR millions.
  Key table labels (transliterated): "Ακαθάριστα Έσοδα Παιγνίων" (GGR),
  "Στοιχήματα διαδικτύου" (online betting), "Καζίνο" (casino).

* **2019 Gambling Market Statistics Report**
  (``2019_Gambling_Market_Statistics_Report_March_2020_EN_FINAL.pdf``) —
  English; structured tables per vertical (online betting, casino, VLTs).

Parsing strategy
----------------
pdfplumber extract_text() is used per page.  The parser looks for EUR
numeric values (format "NNN,N" or "NNN.N" or plain "NNN" followed by
optional "εκατ." / "mln" / "εκ." / "million") in context of vertical
keywords.  Results are mapped to MetricFact rows at period "YYYY-12" and
then spread evenly across 4 quarters (Q1–Q4) as synthetic quarterly rows to
align with the dashboard quarterly chart granularity.

FX
--
EUR → USD at a fixed rate of 1.08 (approximate mid-rate; replace with a
live-lookup when a stable FX feed is wired in).
  value_usd_cents = int(eur_value_eur * 1.08 * 100)

Graceful degradation
--------------------
If the PDF cannot be parsed (binary, scanned image, unsupported layout)
the function returns [] rather than raising.
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

# Greek keywords that signal a vertical context (lower-case match)
_ONLINE_BETTING_KW = re.compile(
    r"(στοιχήματ|online.{0,15}bet|sports.{0,10}wager|internet.{0,10}bet|"
    r"online sport|αθλητικ)",
    re.IGNORECASE,
)
_CASINO_KW = re.compile(
    r"(καζίνο|casino|online.{0,10}casino|ηλεκτρονικ.{0,10}καζίνο)",
    re.IGNORECASE,
)
_VLT_KW = re.compile(
    r"(vlt|βιντεολαχειομηχαν|video.{0,10}lottery|slot)",
    re.IGNORECASE,
)

# EUR value patterns — covers:
#   "353,4 εκατ." / "353.4 million" / "353 mln" / "1.113" (raw millions) etc.
_EUR_VALUE_RE = re.compile(
    r"€?\s*(\d[\d\.,]*)\s*"
    r"(?:εκατ\.?|εκ\.?|mln\.?|million|millions|δισ\.?|δισεκ\.?|billion|"
    r"blrd\.?)?",
    re.IGNORECASE,
)

# Scale word → multiplier to normalise to EUR millions
_SCALE: dict[str, float] = {
    "δισ": 1_000.0, "δισεκ": 1_000.0, "billion": 1_000.0, "blrd": 1_000.0,
    "εκατ": 1.0, "εκ": 1.0, "mln": 1.0, "million": 1.0, "millions": 1.0,
}

# Minimum / maximum plausible GGR range for Greece (EUR millions)
# Online betting ~300–600 M, casino ~20–200 M, VLT total ~1 000–2 500 M
_MIN_GGR_MLN = 5.0
_MAX_GGR_MLN = 5_000.0

# Period helpers — detect year from filename
_YEAR_RE = re.compile(r"(\d{4})")


# ---------------------------------------------------------------------------
# FX helper
# ---------------------------------------------------------------------------

def _eur_millions_to_usd_cents(eur_mln: float) -> int:
    return int(eur_mln * 1_000_000 * _EUR_TO_USD * 100)


# ---------------------------------------------------------------------------
# Period detection
# ---------------------------------------------------------------------------

def _year_from_filename(filename: str) -> Optional[int]:
    """Extract report year from filename.

    AnnualReport2023GR.pdf → 2023
    2019_Gambling_Market_Statistics_Report... → 2019
    """
    # Prefer the year that appears directly before "GR" or at the start
    m = re.search(r"(\d{4})(?:GR|EN|_)", filename)
    if m:
        return int(m.group(1))
    # Fall back to first 4-digit year in range 2010–2030
    for m in _YEAR_RE.finditer(filename):
        y = int(m.group(1))
        if 2010 <= y <= 2030:
            return y
    return None


def _year_from_first_page(text: str) -> Optional[int]:
    """Scan first-page text for a plausible report year."""
    for m in re.finditer(r"20(1[0-9]|2[0-9])", text):
        y = int(m.group(0))
        if 2011 <= y <= 2030:
            return y
    return None


# ---------------------------------------------------------------------------
# Value extraction helpers
# ---------------------------------------------------------------------------

def _parse_eur_value_and_scale(
    val_str: str, scale_word: str
) -> Optional[float]:
    """Convert matched val_str + scale_word to EUR millions."""
    val_str = val_str.replace(" ", "")
    # Greek/Dutch convention: comma = decimal, dot = thousands separator
    if "," in val_str and "." in val_str:
        # e.g. "1.234,5" → 1234.5
        val_str = val_str.replace(".", "").replace(",", ".")
    elif "," in val_str:
        # e.g. "353,4" → 353.4
        val_str = val_str.replace(",", ".")
    else:
        # e.g. "1.234" — if only dots, treat as thousands separator when ≥4 digits
        if len(val_str.replace(".", "")) >= 4:
            val_str = val_str.replace(".", "")
    try:
        value = float(val_str)
    except ValueError:
        return None

    scale_key = scale_word.rstrip(".").lower() if scale_word else ""
    multiplier = _SCALE.get(scale_key, 1.0)  # assume millions if no unit
    return value * multiplier


# ---------------------------------------------------------------------------
# Per-page scanner
# ---------------------------------------------------------------------------

# Full EUR-with-scale pattern for context scanning
_FULL_EUR_RE = re.compile(
    r"€?\s*(\d[\d\.,]*)\s*"
    r"(εκατ\.?|εκ\.?|mln\.?|million\.?|millions\.?|"
    r"δισ\.?|δισεκ\.?|billion\.?|blrd\.?)?",
    re.IGNORECASE,
)


def _extract_ggr_from_context(
    text: str,
    keyword_re: re.Pattern,
    window: int = 300,
) -> Optional[float]:
    """Find the first plausible EUR-million GGR value near a vertical keyword.

    Scans `window` characters before and after each keyword match.
    Returns the best (most plausible) value found, or None.
    """
    candidates: list[float] = []
    for km in keyword_re.finditer(text):
        start = max(0, km.start() - window)
        end = min(len(text), km.end() + window)
        snippet = text[start:end]
        for vm in _FULL_EUR_RE.finditer(snippet):
            val_str = vm.group(1)
            scale_word = vm.group(2) or ""
            val = _parse_eur_value_and_scale(val_str, scale_word)
            if val is None:
                continue
            if _MIN_GGR_MLN <= val <= _MAX_GGR_MLN:
                candidates.append(val)
    # Return the median-ish value to avoid noise from small footnote numbers
    if not candidates:
        return None
    candidates.sort()
    return candidates[len(candidates) // 2]


# ---------------------------------------------------------------------------
# Quarterly spread helper
# ---------------------------------------------------------------------------

def _spread_quarterly(
    eur_mln: float,
    year: int,
    vertical: str,
    source_url: str,
) -> list[MetricFact]:
    """Spread annual EUR GGR evenly across 4 quarters as synthetic monthly rows.

    Q1 → YYYY-03, Q2 → YYYY-06, Q3 → YYYY-09, Q4 → YYYY-12.
    Total cents spread equally; remainder added to Q4.
    """
    total_cents = _eur_millions_to_usd_cents(eur_mln)
    per_quarter = total_cents // 4
    remainder = total_cents - per_quarter * 4
    quarters = ["03", "06", "09", "12"]
    facts: list[MetricFact] = []
    for i, q_month in enumerate(quarters):
        cents = per_quarter + (remainder if i == 3 else 0)
        facts.append(MetricFact(
            state="GR",
            operator="GR Statewide",
            vertical=vertical,
            period=f"{year:04d}-{q_month}",
            metric_name="ggr",
            value_usd_cents=cents,
            source_url=source_url,
        ))
    return facts


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_gr_pdf(path: Path, source_url: str) -> list[MetricFact]:
    """Parse a HGC annual PDF and return MetricFact rows.

    Parameters
    ----------
    path:
        Local ``pathlib.Path`` to the downloaded PDF.
    source_url:
        Original download URL; stored verbatim in every MetricFact.

    Returns
    -------
    list[MetricFact]
        Zero or more facts with:
          state           = "GR"
          operator        = "GR Statewide"
          vertical        = "sports-wagering" | "igaming" | "vlt"
          period          = "YYYY-{03|06|09|12}"   (synthetic quarterly spread)
          metric_name     = "ggr"
          value_usd_cents = int(eur_ggr × 1.08 × 100)

    Returns [] gracefully on any parse failure.

    FX note:
        EUR → USD fixed at 1.08; replace with live lookup when available.
    """
    try:
        filename = path.name

        # ── Determine report year ─────────────────────────────────────────
        year = _year_from_filename(filename)

        try:
            with pdfplumber.open(str(path)) as pdf:
                if not pdf.pages:
                    return []

                # Try first page text to get year if still unknown
                first_text = pdf.pages[0].extract_text() or ""
                if year is None:
                    year = _year_from_first_page(first_text)
                if year is None:
                    return []

                # Concatenate all page text for scanning
                full_text = "\n".join(
                    (page.extract_text() or "") for page in pdf.pages
                )
        except Exception:  # noqa: BLE001 — pdfplumber can raise on bad PDFs
            return []

        if not full_text.strip():
            return []

        facts: list[MetricFact] = []

        # ── Online sports wagering ────────────────────────────────────────
        sports_mln = _extract_ggr_from_context(full_text, _ONLINE_BETTING_KW)
        if sports_mln is not None and sports_mln > 0:
            facts.extend(_spread_quarterly(sports_mln, year, "sports-wagering",
                                           source_url))

        # ── Online casino ─────────────────────────────────────────────────
        casino_mln = _extract_ggr_from_context(full_text, _CASINO_KW)
        if casino_mln is not None and casino_mln > 0:
            facts.extend(_spread_quarterly(casino_mln, year, "igaming",
                                           source_url))

        # ── VLT / slot arcades ────────────────────────────────────────────
        vlt_mln = _extract_ggr_from_context(full_text, _VLT_KW)
        if vlt_mln is not None and vlt_mln > 0:
            facts.extend(_spread_quarterly(vlt_mln, year, "vlt", source_url))

        return facts

    except Exception:  # noqa: BLE001 — never crash the collector loop
        return []
