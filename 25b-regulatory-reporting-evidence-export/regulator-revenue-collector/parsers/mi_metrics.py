# Companion code for "The Backend of Luck" - Chapter 25b, Regulatory Reporting and Evidence Export.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Michigan Gaming Control Board (MGCB) GovDelivery bulletin parser.

Parses the HTML body of a monthly iGaming + online sports betting bulletin
published at:

    https://content.govdelivery.com/accounts/MIGCB/bulletins/<hexid>

Example bulletin:
  Title: "Michigan iGaming, online sports betting operators report $313M in
          February revenue"
  Sent:  "03/17/2026 10:44 AM EDT"  (data-month = month named in body)

Lines extracted (illustrative — exact wording varies slightly by year):
  "February iGaming gross receipts totaled $273.1 million"
      → MetricFact(vertical='igaming',        metric_name='ggr')
  "online sports betting gross receipts totaled $39.9 million"
      → MetricFact(vertical='sports-wagering', metric_name='ggr')
  "online sports betting handle totaled $384.7 million"
      → MetricFact(vertical='sports-wagering', metric_name='handle')
  "$262.1 million from iGaming"
      → MetricFact(vertical='igaming',        metric_name='agr')
  "$25.4 million from online sports betting"
      → MetricFact(vertical='sports-wagering', metric_name='agr')
  "iGaming taxes and fees = $53.9 million"
      → MetricFact(vertical='igaming',        metric_name='tax_paid')
  "Online sports betting taxes and fees = $1.7 million"
      → MetricFact(vertical='sports-wagering', metric_name='tax_paid')

The 'ggr' metric is the primary output; other metrics are emitted when
found and are clearly labelled in the bulletin.

All dollar values are in USD millions in the source; this parser converts
them to integer cents (× 1_000_000 × 100).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from bs4 import BeautifulSoup

from .metrics_model import MetricFact

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MONTHS: dict[str, str] = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
}

_MONTH_NAMES_RE = "|".join(_MONTHS)  # e.g. "january|february|..."

# Sent-date header inside the bulletin HTML — two formats observed:
#   "03/17/2026 10:44 AM EDT"
#   "11/19/2024 11:59 AM EST"
_SENT_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4})\s+\d{2}:\d{2}\s+[AP]M")

# ---------------------------------------------------------------------------
# USD-millions → integer cents
# ---------------------------------------------------------------------------

_DOLLAR_RE = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)\s*(?:million|billion)?", re.I)


def _parse_millions(text: str) -> float | None:
    """Extract the first dollar figure from *text* and return as USD millions.

    Handles:
      "$273.1 million"  → 273.1
      "$1.7 million"    → 1.7
      "$384.7 million"  → 384.7
      "$46.1 million"   → 46.1
      "$267,881"        → 0.000267881  (raw dollars, no 'million' qualifier)
    """
    m = _DOLLAR_RE.search(text)
    if not m:
        return None
    raw_num = m.group(1).replace(",", "")
    try:
        value = float(raw_num)
    except ValueError:
        return None
    # Check if 'billion' or 'million' qualifier follows
    qualifier = m.group(0).lower()
    if "billion" in qualifier:
        return value * 1000.0
    if "million" in qualifier:
        return value
    # No qualifier — assume raw dollars (e.g. "$267,881")
    return value / 1_000_000.0


def _millions_to_cents(millions: float) -> int:
    """Convert USD millions to integer cents (round half-even)."""
    return int(round(millions * 1_000_000 * 100))


# ---------------------------------------------------------------------------
# Period detection
# ---------------------------------------------------------------------------

def _detect_period(text: str, sent_year: int | None) -> str | None:
    """Return 'YYYY-MM' for the data period named in the bulletin body.

    The data month is stated explicitly ("February iGaming gross receipts …").
    The year is derived from the bulletin send date (send month is always
    data_month + 1, so if sent in January we subtract 1 from the year).

    Args:
        text: Full plaintext of the bulletin body.
        sent_year: Calendar year extracted from the send-date header, or None.

    Returns:
        'YYYY-MM' string, or None if no month name is found.
    """
    # Pattern: "<Month> iGaming gross receipts …" or
    #          "<Month> gross sports betting receipts …"
    mo_re = re.compile(
        rf"({_MONTH_NAMES_RE})\s+(?:igaming|gross\s+sports|online\s+sports|internet\s+sports|"
        rf"internet\s+gaming|combined)\b",
        re.I,
    )
    m = mo_re.search(text)
    if not m:
        # Fallback: look for any month name near the start of the bulletin
        fallback = re.search(rf"\b({_MONTH_NAMES_RE})\b", text, re.I)
        if not fallback:
            return None
        month_name = fallback.group(1).lower()
    else:
        month_name = m.group(1).lower()

    month_num = _MONTHS.get(month_name)
    if not month_num:
        return None

    if sent_year is None:
        return None

    # The bulletin is sent in the month AFTER the data month.  If the data
    # month is December (12), the bulletin is sent in January of the next
    # year, so we subtract 1 from sent_year.
    data_month_int = int(month_num)
    year = sent_year
    # If the data month appears to be ahead of the send month in the same
    # year, the data is from the previous year (e.g. Dec data, Jan bulletin).
    # sent_month = bulletin send month; we infer it as data_month + 1.
    sent_month = data_month_int % 12 + 1  # wraps Dec(12) → 1
    if sent_month == 1 and data_month_int == 12:
        # Bulletin sent in January → data year = sent_year - 1
        year = sent_year - 1

    return f"{year:04d}-{month_num}"


def _extract_sent_year(text: str) -> int | None:
    """Return the 4-digit year from the bulletin's sent-date header."""
    m = _SENT_RE.search(text)
    if not m:
        return None
    return int(m.group(3))


# ---------------------------------------------------------------------------
# Line-level metric extraction
# ---------------------------------------------------------------------------

# Each tuple: (compiled regex, vertical, metric_name)
# Regexes must match a line containing the described dollar figure.
# The first capture group in each regex should contain the month name
# (or is unused). The dollar value is extracted separately via _parse_millions.

_LINE_RULES: list[tuple[re.Pattern[str], str, str]] = [
    # iGaming GGR: "February iGaming gross receipts totaled $273.1 million"
    (
        re.compile(
            rf"({_MONTH_NAMES_RE})\s+igaming\s+gross\s+receipts\s+totaled",
            re.I,
        ),
        "igaming",
        "ggr",
    ),
    # Sports GGR (2026+): "online sports betting gross receipts totaled $39.9M"
    (
        re.compile(
            r"online\s+sports\s+betting\s+gross\s+receipts\s+totaled",
            re.I,
        ),
        "sports-wagering",
        "ggr",
    ),
    # Sports GGR (pre-2026): "October gross sports betting receipts totaled $33.0M"
    (
        re.compile(
            rf"({_MONTH_NAMES_RE})\s+gross\s+sports\s+betting\s+receipts\s+totaled",
            re.I,
        ),
        "sports-wagering",
        "ggr",
    ),
    # Sports GGR alt: "internet sports betting gross receipts totaled $X"
    (
        re.compile(
            r"internet\s+sports\s+(?:betting\s+)?gross\s+(?:receipts|revenue)\s+totaled",
            re.I,
        ),
        "sports-wagering",
        "ggr",
    ),
    # Sports handle (2026+): "online sports betting handle totaled $384.7M"
    (
        re.compile(
            r"online\s+sports\s+betting\s+handle\s+totaled",
            re.I,
        ),
        "sports-wagering",
        "handle",
    ),
    # Sports handle (pre-2026): "Total October internet sports betting handle of $X"
    (
        re.compile(
            r"(?:total\s+(?:internet|online)\s+sports\s+betting\s+handle"
            r"|internet\s+sports\s+betting\s+handle\s+(?:totaled|of|was))",
            re.I,
        ),
        "sports-wagering",
        "handle",
    ),
    # iGaming AGR: "$262.1 million from iGaming"
    (
        re.compile(
            r"from\s+igaming",
            re.I,
        ),
        "igaming",
        "agr",
    ),
    # Sports AGR: "$25.4 million from online sports betting"
    (
        re.compile(
            r"from\s+(?:online|internet)\s+sports\s+betting",
            re.I,
        ),
        "sports-wagering",
        "agr",
    ),
    # iGaming taxes: "iGaming taxes and fees = $53.9 million"
    (
        re.compile(
            r"igaming\s+taxes\s+and\s+fees\s*[=:]",
            re.I,
        ),
        "igaming",
        "tax_paid",
    ),
    # Sports taxes: "Online sports betting taxes and fees = $1.7 million"
    (
        re.compile(
            r"(?:online|internet)\s+sports\s+betting\s+taxes\s+and\s+fees\s*[=:]",
            re.I,
        ),
        "sports-wagering",
        "tax_paid",
    ),
]


def _nearest_dollar(text: str, match_start: int, match_end: int, window: int = 200) -> float | None:
    """Return the dollar value (in USD millions) closest to the match in *text*.

    Scans both a short window after and a short window before the match, then
    returns the value whose dollar-sign is closest to the match boundary.
    This correctly handles:
      - "$273.1 million" appearing AFTER "totaled" → use after-window
      - "$262.1 million from iGaming" where dollar is BEFORE keyword → use
        the last dollar in the before-window (closest to the keyword start)
    """
    after_text = text[match_end: match_end + window]
    before_text = text[max(0, match_start - window): match_start]

    # Find position of first dollar in after-window
    after_m = _DOLLAR_RE.search(after_text)
    after_dist = after_m.start() if after_m else None

    # Find position of last dollar in before-window (closest to match)
    before_dist = None
    before_m_last = None
    for bm in _DOLLAR_RE.finditer(before_text):
        dist = len(before_text) - bm.end()
        if before_dist is None or dist < before_dist:
            before_dist = dist
            before_m_last = bm

    # Prefer whichever dollar is closer to the match boundary
    if after_dist is None and before_dist is None:
        return None
    if after_dist is None:
        raw = before_m_last.group(1).replace(",", "")  # type: ignore[union-attr]
        qualifier = before_m_last.group(0).lower()     # type: ignore[union-attr]
    elif before_dist is None or after_dist <= before_dist:
        raw = after_m.group(1).replace(",", "")  # type: ignore[union-attr]
        qualifier = after_m.group(0).lower()      # type: ignore[union-attr]
    else:
        raw = before_m_last.group(1).replace(",", "")  # type: ignore[union-attr]
        qualifier = before_m_last.group(0).lower()     # type: ignore[union-attr]

    try:
        value = float(raw)
    except ValueError:
        return None
    if "billion" in qualifier:
        return value * 1000.0
    if "million" in qualifier:
        return value
    return value / 1_000_000.0


def _extract_facts_from_text(
    text: str,
    period: str,
    source_url: str,
) -> list[MetricFact]:
    """Scan *text* and emit MetricFacts per _LINE_RULES.

    For each rule, finds every occurrence of the pattern in the full text and
    resolves the dollar value nearest to (i.e. after) the matched keyword.
    This correctly handles:
      - lines with a single clause  ("February iGaming gross receipts totaled $273.1M")
      - compound lines joined by semicolons
        ("iGaming taxes and fees = $53.9M; Online sports betting taxes and fees = $1.7M")
      - lines where the dollar appears BEFORE the label
        ("including $262.1 million from iGaming and $25.4 million from online sports betting")
    """
    facts: list[MetricFact] = []
    seen: set[tuple[str, str]] = set()   # (vertical, metric_name) dedup

    for pat, vertical, metric_name in _LINE_RULES:
        key = (vertical, metric_name)
        if key in seen:
            continue
        for m in pat.finditer(text):
            millions = _nearest_dollar(text, m.start(), m.end())
            if millions is None:
                continue
            if key in seen:
                break
            seen.add(key)
            facts.append(MetricFact(
                state="MI",
                operator="MI Statewide",
                vertical=vertical,
                period=period,
                metric_name=metric_name,
                value_usd_cents=_millions_to_cents(millions),
                source_url=source_url,
            ))
            break
    return facts


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_mi_bulletin(path: Path, source_url: str) -> list[MetricFact]:
    """Parse a locally-cached MGCB GovDelivery bulletin HTML file.

    The bulletin contains both iGaming and online sports betting statewide
    figures.  Two 'ggr' MetricFacts are emitted per bulletin (one per
    vertical) plus optional handle, agr, and tax_paid facts.

    Args:
        path:       Local path to the downloaded bulletin HTML.
        source_url: Canonical bulletin URL used as provenance on every fact.

    Returns:
        List of MetricFact instances with state='MI',
        operator='MI Statewide', and metric_name in:
        'ggr', 'handle', 'agr', 'tax_paid'.
        Returns [] on any parse error — never raises.
    """
    try:
        html = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("mi_metrics: cannot read %s: %s", path, exc)
        return []

    try:
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text("\n")
    except Exception as exc:  # noqa: BLE001
        logger.warning("mi_metrics: BeautifulSoup failed for %s: %s", path, exc)
        return []

    sent_year = _extract_sent_year(text)
    if sent_year is None:
        # Try to infer from source_url as last resort (not reliable)
        m = re.search(r"/(\d{4})/", source_url)
        if m:
            sent_year = int(m.group(1))

    period = _detect_period(text, sent_year)
    if period is None:
        logger.warning(
            "mi_metrics: could not determine data period from %s (sent_year=%s)",
            path, sent_year,
        )
        return []

    facts = _extract_facts_from_text(text, period, source_url)

    if not facts:
        logger.warning(
            "mi_metrics: no metric lines found in %s (period=%s)", path, period,
        )

    facts.sort(key=lambda f: (f.period, f.vertical, f.metric_name))
    return facts
