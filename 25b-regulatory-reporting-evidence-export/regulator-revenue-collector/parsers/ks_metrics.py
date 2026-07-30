# Companion code for "The Backend of Luck" - Chapter 25b, Regulatory Reporting and Evidence Export.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Kansas Racing and Gaming Commission monthly PDF parser.

Each KRGC monthly revenue PDF ("{Month}_{YYYY}_Revenue_Report.pdf") has
one page per state-owned commercial casino:

  BOOT HILL CASINO & RESORT
  Lottery Gaming Facility Revenue-Unaudited*
  September September Fiscal YTD Fiscal YTD
  2025       2024       2026        2025
  Electronic gaming machines   2,897,811.25 3,455,617.98 9,913,001.22 10,669,036.13
  Table games                    424,484.85   420,952.53 1,351,250.92  1,164,185.49
  Other #                          2,935.84     3,039.25     3,228.18      3,245.51
  Total Lottery Gaming Facility
  Revenue                      3,325,231.94 3,879,609.76 11,267,480.32 11,836,467.13
  State Share 24** 798,055.67           -  2,704,195.28          -
  Casino Share 71%** 2,360,914.68       -  7,999,911.03          -
  Local Share 3%      99,756.96 116,388.29 338,024.41   355,094.01
  Problem Gambling Share 2% 66,504.64 77,592.20 225,349.61 236,729.34

For each page we emit:
  ggr       = Total Lottery Gaming Facility Revenue  (current month column)
  tax_paid  = State Share + Local Share + Problem Gambling Share  (current month)

State-share percentages changed mid-history (Dec 2024: 22 %→24 %; Casino
share 73 %→71 %) so rows match on the LABEL PREFIX ("State Share",
"Local Share", "Problem Gambling Share") rather than the percentage.

Period (YYYY-MM) is derived from the PDF filename.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pdfplumber

from .metrics_model import MetricFact

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MONTHS = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
}

# Number-with-commas; also captures a bare "-" placeholder.
_NUM_RE = re.compile(r"-?\d{1,3}(?:,\d{3})*(?:\.\d+)?|-")

# Row label patterns — match on prefix so percentage suffixes don't matter.
_TOTAL_REV_RE = re.compile(r"^\s*Revenue\b", re.I)           # follows "Total Lottery Gaming Facility"
_STATE_SHARE_RE = re.compile(r"^\s*State\s+Share\b", re.I)
_LOCAL_SHARE_RE = re.compile(r"^\s*Local\s+Share\b", re.I)
_PROB_SHARE_RE  = re.compile(r"^\s*Problem\s+Gambling\s+Share\b", re.I)
_CASINO_NAME_RE = re.compile(r"^[A-Z][A-Z &\.,'\-]+(?:CASINO|RESORT)[A-Z &\.,'\-]*$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MONTH_ABBREV = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "may": "05", "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}


def _period_from_url(url: str) -> str | None:
    # Pattern 1: full month name followed by year (historical wp-content path,
    #   and also Revenue-December-2025.pdf style).
    #   /January_2026_Revenue_Report.pdf  or  /Revenue-December-2025.pdf
    m = re.search(
        r"(?:^|[/_\-])(january|february|march|april|may|june|july|august|"
        r"september|october|november|december)[_\-](20\d{2})(?:[/_\-.]|$)",
        url, re.I,
    )
    if m:
        return f"{m.group(2)}-{_MONTHS[m.group(1).lower()]}"

    # Pattern 2: abbreviated month + year (e.g. revenue-jan-2026.pdf,
    #   Revenue-Jan-2026.pdf, revenue-feb-2026).
    m = re.search(
        r"(?:^|[/_\-])(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
        r"[_\-](20\d{2})(?:[/_\-.]|$)",
        url, re.I,
    )
    if m:
        return f"{m.group(2)}-{_MONTH_ABBREV[m.group(1).lower()]}"

    # Pattern 3: new 2026 naming: /{Month}-Revenue-Reports.pdf with year in
    #   the directory component (e.g. /2026/5May/April-Revenue-Reports.pdf).
    #   Extract the month from the filename and the year from the path.
    m_month = re.search(
        r"/(january|february|march|april|may|june|july|august|"
        r"september|october|november|december)-revenue-reports",
        url, re.I,
    )
    m_year = re.search(r"/(20\d{2})/", url)
    if m_month and m_year:
        return f"{m_year.group(1)}-{_MONTHS[m_month.group(1).lower()]}"

    return None


def _to_cents(raw: str | None) -> int | None:
    if raw is None:
        return None
    s = raw.strip().replace(",", "").replace("$", "")
    if not s or s == "-":
        return None
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    try:
        return int(round(float(Decimal(s)) * 100)) * (-1 if neg else 1)
    except (InvalidOperation, ValueError):
        return None


def _current_month_value(line: str) -> int | None:
    """Return the current-month column value (column 1 of 4) as integer cents.

    KRGC row layout (after the label) is exactly four space-separated tokens:
        curr-month  prior-month  FYTD-curr  FYTD-prior

    Each token is either a number (e.g. ``798,055.67``) or a literal dash
    (``-``) marking "n/a for this column". The label can include percentage
    suffixes (``22% **``, ``24**``, ``3%``, ``2%``) that we have to skip
    over before reading column 1.

    The 22 %→24 % state-share split (Dec 2024) means a single page may
    contain both a 22 % row (curr-month = ``-``) and a 24 % row
    (curr-month = ``798,055.67``). Returning None for the dash row lets
    the caller silently fall through to whichever row carries the value.
    """
    # Anything to the right of the last "%" sign in the line is the value
    # block. When no "%" is present (e.g. the Total Revenue row, or the
    # "State Share 24**" replacement row), we walk forward and skip any
    # leading non-numeric tokens (label words, footnote markers).
    if "%" in line:
        tail = line.rsplit("%", 1)[1]
    else:
        tail = line
    # Split on whitespace; dashes survive as their own tokens.
    tokens = tail.split()
    # Walk to the first token that is either a dash sentinel or a parseable
    # number. Anything in between (label words, "**", "$", parenthetical
    # footnotes) is skipped.
    for tok in tokens:
        if tok in ("-", "--"):
            return None
        v = _to_cents(tok)
        if v is not None:
            return v
    return None


def _casino_name_from_page(text: str) -> str | None:
    """Return the ALL-CAPS casino heading found near the top of the page."""
    for line in text.splitlines():
        stripped = line.strip()
        if _CASINO_NAME_RE.match(stripped):
            # Normalise double-spaces and trailing whitespace.
            return re.sub(r"\s+", " ", stripped)
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_ks_pdf(path: Path, source_url: str) -> list[MetricFact]:
    """Parse a KRGC monthly casino revenue PDF into MetricFacts."""
    period = _period_from_url(source_url) or _period_from_url(str(path))
    if period is None:
        return []

    try:
        with open(path, "rb") as fh:
            if fh.read(4) != b"%PDF":
                return []
    except OSError:
        return []

    facts: list[MetricFact] = []
    try:
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                if not text:
                    continue
                op = _casino_name_from_page(text)
                if not op:
                    continue

                ggr_cents: int | None = None
                tax_components: list[int] = []

                lines = text.splitlines()
                for idx, line in enumerate(lines):
                    # Total revenue spans two printed lines:
                    #   "Total Lottery Gaming Facility"
                    #   "Revenue   3,325,231.94 3,879,609.76 ..."
                    if _TOTAL_REV_RE.match(line):
                        # Make sure we're actually in the Revenue continuation
                        # (previous non-blank line contains "Total Lottery").
                        prior = ""
                        for back in range(idx - 1, max(-1, idx - 4), -1):
                            if lines[back].strip():
                                prior = lines[back]
                                break
                        if "Total Lottery" in prior:
                            v = _current_month_value(line)
                            if v is not None:
                                ggr_cents = v
                        continue

                    if _STATE_SHARE_RE.match(line):
                        v = _current_month_value(line)
                        if v is not None:
                            tax_components.append(v)
                    elif _LOCAL_SHARE_RE.match(line):
                        v = _current_month_value(line)
                        if v is not None:
                            tax_components.append(v)
                    elif _PROB_SHARE_RE.match(line):
                        v = _current_month_value(line)
                        if v is not None:
                            tax_components.append(v)

                if ggr_cents is not None:
                    facts.append(MetricFact(
                        state="KS",
                        operator=op,
                        vertical="commercial-casino",
                        period=period,
                        metric_name="ggr",
                        value_usd_cents=ggr_cents,
                        source_url=source_url,
                    ))

                if tax_components:
                    facts.append(MetricFact(
                        state="KS",
                        operator=op,
                        vertical="commercial-casino",
                        period=period,
                        metric_name="tax_paid",
                        value_usd_cents=sum(tax_components),
                        source_url=source_url,
                    ))
    except Exception:  # noqa: BLE001 - be forgiving to malformed PDFs
        return []

    facts.sort(key=lambda f: (f.period, f.operator, f.metric_name))
    return facts
