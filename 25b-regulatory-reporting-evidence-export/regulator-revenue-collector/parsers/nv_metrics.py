# Companion code for "The Backend of Luck" - Chapter 25b, Regulatory Reporting and Evidence Export.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Nevada Gaming Control Board Monthly Gaming Revenue Report (GRI) parser.

Extracts statewide aggregates from a single-PDF monthly GRI.

Sample URL pattern:
    https://gaming.nv.gov/siteassets/content/about/gaming-revenue/{YYYY}{mon}-gri.pdf

The GRI is a fixed-layout PDF with:
  - One page titled "Statewide - All Nonrestricted Locations" (always "Page 1 of N"
    in the footer) containing win amounts by game type for current month / 3-month /
    12-month rolling windows.
  - One "Taxable Revenue by Area" appendix page (last populated page) containing
    the statewide taxable GGR base.

Win amounts are reported in thousands of USD (footnote: "** Win Amounts are in
thousands ('000)").

Sports-pool handle is not directly published in the GRI — only win and win%.
We back-calculate it as: handle = win / (win_pct / 100).  Omitted when win_pct
is unavailable or zero.

Emits two vertical rows per PDF:
  - vertical="commercial-casino": slot_win, table_win, race_win, ggr, tax_paid
  - vertical="sports-wagering"  : sports_win, sports_handle (when calculable),
                                   sports_mobile_win

PDF format variations
---------------------
Nevada's site has published GRIs in two physical layouts:

Format A — text-header (most reports from 2024+):
  The header text "Statewide - All Nonrestricted Locations" and footer
  "Page 1 of N" are embedded as searchable text.

Format B — image-header (some siteassets downloads, especially 2025-07):
  Pages 1-3 are blank or scanned images; the header/footer text is not
  available to pdfplumber.  Detection falls back to finding the page with
  the highest "Total Gaming" value among pages that also carry a Sports
  Pool summary line and a Mobile Sports breakdown line.
"""
from __future__ import annotations

import re
from pathlib import Path

import pdfplumber

from .metrics_model import MetricFact

# ---------------------------------------------------------------------------
# Period helpers
# ---------------------------------------------------------------------------

_MONTHS = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "may": "05", "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}

_MONTH_LONG = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
}


def _period_from_url(url: str) -> str | None:
    """'...2025jul-gri.pdf' → '2025-07'."""
    m = re.search(r"/(\d{4})([a-z]{3})-gri", url, re.I)
    if not m:
        return None
    mon = _MONTHS.get(m.group(2).lower())
    return f"{m.group(1)}-{mon}" if mon else None


def _period_from_text(text: str) -> str | None:
    """Scan text for 'Month Year' header, e.g. 'May-2025' or 'January 2024'."""
    m = re.search(
        r"(?:Current Month\s*[-–]\s*)?([A-Za-z]+)[-\s](\d{4})\b",
        text,
    )
    if not m:
        return None
    mon_str = m.group(1).lower()
    year = m.group(2)
    mon = _MONTH_LONG.get(mon_str) or _MONTHS.get(mon_str[:3])
    return f"{year}-{mon}" if mon else None


# ---------------------------------------------------------------------------
# Numeric helpers
# ---------------------------------------------------------------------------

_TOK_RE = re.compile(r"\([\d,]+(?:\.\d+)?\)|-?[\d,]+(?:\.\d+)?")


def _parse_val(tok: str) -> float | None:
    s = tok.strip()
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace(",", "")
    try:
        v = float(s)
        return -v if neg else v
    except ValueError:
        return None


def _extract_nums(s: str) -> list[float]:
    """Return list of floats parsed from all numeric tokens in *s*."""
    return [v for tok in _TOK_RE.findall(s) if (v := _parse_val(tok)) is not None]


def _thousands_to_cents(val_thousands: float) -> int:
    """Convert value in $thousands to integer USD cents."""
    return int(round(val_thousands * 1_000 * 100))


# ---------------------------------------------------------------------------
# Line-level parsers (current-month column only)
# ---------------------------------------------------------------------------

# Row types and their (win_index, pct_index) into the numeric token list
# extracted from the remainder of the line after stripping the label prefix.
#
# "standard"     : locs  units  win  chg  pct  [3mo ...] [12mo ...]
# "mobile"       : locs  win    chg  pct  [...]       (no units column)
# "total_gaming" : win   chg    [3mo ...]              (no locs/units/pct)
#
# All positions 0-based.
_KIND_WIN_PCT: dict[str, tuple[int, int | None]] = {
    "standard":     (2, 4),
    "mobile":       (1, 3),
    "total_gaming": (0, None),
}

# Label patterns → (strip_re, kind) — ordered most-specific first
_LABEL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\*+\s*Sports\s*-\s*Mobile\s+", re.I),                          "mobile"),
    (re.compile(r"(?:\(\d+\)\s*)?Sports\s*[-–]\s*\w+(?:\s+\w+)?\s+", re.I),      "standard"),
    (re.compile(r"Sports\s+Parlay\s+Cards\s+", re.I),                             "standard"),
    (re.compile(r"(?:\(\d+\)\s*)?Sports\s+Pool\s*(?:\(\d+\))?\s+", re.I),        "standard"),
    (re.compile(r"(?:\(\d+\)\s*)?Race\s+Book(?:\s+Parimutuel)?\s*(?:\(\d+\))?\s+", re.I), "standard"),
    (re.compile(r"Total\s+Gaming\s+", re.I),                                      "total_gaming"),
    (re.compile(r"Total\s+", re.I),                                               "standard"),
]


def _win_and_pct(line: str) -> tuple[float | None, float | None, str | None]:
    """Return (win_thousands, win_pct, kind) for the current-month column."""
    for pat, kind in _LABEL_PATTERNS:
        m = pat.match(line.strip())
        if not m:
            continue
        rest = line.strip()[m.end():]
        nums = _extract_nums(rest)
        win_idx, pct_idx = _KIND_WIN_PCT[kind]
        win = nums[win_idx] if win_idx < len(nums) else None
        pct = nums[pct_idx] if (pct_idx is not None and pct_idx < len(nums)) else None
        return win, pct, kind
    return None, None, None


# ---------------------------------------------------------------------------
# Page identification (two-pass)
# ---------------------------------------------------------------------------

def _page_total_gaming(text: str) -> float:
    """Return the 'Total Gaming' current-month value from *text*, or 0."""
    m = re.search(r"Total Gaming\s+([\d,]+)", text)
    return float(m.group(1).replace(",", "")) if m else 0.0


def _identify_pages(pdf) -> tuple[int | None, int | None]:
    """
    Scan all pages and return (statewide_idx, taxable_idx).

    Statewide detection
    -------------------
    Format A: page contains the literal header "Statewide - All Nonrestricted
      Locations" (with a dash) AND footer "Page 1 of N".
    Format B: no readable header; pick the page with the highest
      "Total Gaming" value among pages that contain a Sports Pool summary
      line AND a Mobile Sports breakdown line.

    Taxable detection
    -----------------
    Format A: page contains the literal header "Taxable Revenue by Area".
    Format B: page starts with "Statewide All Nonrestricted Locations NNNN"
      where NNNN >= 1000 (ruling out ToC page numbers), has "$1,000,000 and
      Over Revenue Range" lines, and has NO game-type rows.
    """
    sw_idx: int | None = None          # statewide page
    tx_idx: int | None = None          # taxable page
    best_tg: float = 0.0               # for Format B statewide detection
    best_tg_idx: int | None = None

    for i, page in enumerate(pdf.pages):
        text = page.extract_text() or ""
        if not text.strip():
            continue

        # --- Statewide: Format A ---
        if sw_idx is None:
            if re.search(r"Statewide\s*-\s*All\s+Nonrestricted\s+Locations", text, re.I) \
                    and re.search(r"Page\s+1\s+of\s+\d+", text, re.I):
                sw_idx = i

        # --- Statewide: Format B accumulator ---
        if sw_idx is None:
            if re.search(r"Sports Pool\s*\(\d+\)", text, re.I) \
                    and re.search(r"Sports\s*-\s*Mobile", text, re.I):
                tg = _page_total_gaming(text)
                if tg > best_tg:
                    best_tg = tg
                    best_tg_idx = i

        # --- Taxable: Format A ---
        if tx_idx is None and re.search(r"Taxable\s+Revenue\s+by\s+Area", text, re.I):
            tx_idx = i

        # --- Taxable: Format B ---
        if tx_idx is None:
            m = re.search(
                r"Statewide\s+All\s+Nonrestricted\s+Locations\s+([\d,]+)",
                text, re.I,
            )
            if m:
                statewide_val = float(m.group(1).replace(",", ""))
                is_revenue = statewide_val >= 1_000  # rules out page-number "1" in ToC
                has_range = bool(re.search(
                    r"\$1,000,000\s+and\s+Over\s+Revenue\s+Range", text, re.I
                ))
                has_games = bool(re.search(
                    r"Twenty One|Craps|Roulette|Baccarat", text, re.I
                ))
                if is_revenue and has_range and not has_games:
                    tx_idx = i

    if sw_idx is None:
        sw_idx = best_tg_idx  # Format B fallback

    return sw_idx, tx_idx


# ---------------------------------------------------------------------------
# Statewide page parser
# ---------------------------------------------------------------------------

def _parse_statewide(text: str, period: str, source_url: str) -> list[MetricFact]:
    """
    Parse the "Statewide - All Nonrestricted Locations" page.

    Emits MetricFact rows for:
      - table_win         Table / Counter / Card Games total (current month)
      - slot_win          Slot Machine total (current month)
      - ggr               Total Gaming win = table + slot (current month)
      - race_win          Race Book win (current month)
      - sports_win        Sports Pool win (current month)
      - sports_handle     Sports Pool handle, derived from win / (win_pct/100)
      - sports_mobile_win Mobile sports win (current month)
    """
    facts: list[MetricFact] = []
    # section tracks whether we are inside the Table or Slot subsection
    section: str | None = None
    sports_pool_win: float | None = None
    sports_pool_pct: float | None = None

    def _fact(metric: str, val_thousands: float, vertical: str) -> MetricFact:
        return MetricFact(
            state="NV",
            operator="NV Statewide",
            vertical=vertical,
            period=period,
            metric_name=metric,
            value_usd_cents=_thousands_to_cents(val_thousands),
            source_url=source_url,
        )

    # Infer section if page starts directly with game-type rows (image-header
    # format) by checking whether the page contains an explicit "Table, Counter"
    # section header.  If not, the page implicitly starts in the table section.
    if not re.search(r"Table,\s+Counter", text, re.I):
        section = "table"

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # Section boundaries (Format A — explicit section headers)
        if re.match(r"Table,\s+Counter", line, re.I):
            section = "table"
            continue
        if line == "Slot Machines":
            section = "slot"
            continue

        # Slot section detection for Format B (no "Slot Machines" header):
        # the first slot-denomination line switches the section.
        if section == "table" and re.match(
            r"\d+\s+(?:Cent|Dollar)\s+\d+", line, re.I
        ):
            section = "slot"

        # Sports Pool summary (gives win + win_pct for handle derivation)
        if re.match(r"Sports Pool\s*\(\d+\)", line, re.I):
            win, pct, _ = _win_and_pct(line)
            if win is not None:
                sports_pool_win = win
                sports_pool_pct = pct
                facts.append(_fact("sports_win", win, "sports-wagering"))
            continue

        # Race Book summary
        if re.match(r"Race Book\s*\(\d+\)", line, re.I):
            win, _, _ = _win_and_pct(line)
            if win is not None:
                facts.append(_fact("race_win", win, "commercial-casino"))
            continue

        # Mobile sports
        if re.match(r"\*+\s*Sports", line, re.I):
            win, _, _ = _win_and_pct(line)
            if win is not None:
                facts.append(_fact("sports_mobile_win", win, "sports-wagering"))
            continue

        # Sports breakdown lines (Football, Basketball, etc.) — informational only
        if re.match(r"(?:\(\d+\)\s*)?Sports\s*[-–]\s*\w+", line, re.I) or \
                re.match(r"Sports\s+Parlay\s+Cards", line, re.I):
            continue  # already captured via Sports Pool summary

        # Total Gaming (table + slot aggregate)
        if re.match(r"Total\s+Gaming\b", line, re.I):
            win, _, _ = _win_and_pct(line)
            if win is not None:
                facts.append(_fact("ggr", win, "commercial-casino"))
            continue

        # Section subtotals (Table total / Slot total)
        if re.match(r"Total\b", line, re.I) and section in ("table", "slot"):
            win, _, _ = _win_and_pct(line)
            if win is not None:
                metric = "table_win" if section == "table" else "slot_win"
                facts.append(_fact(metric, win, "commercial-casino"))
            if section == "slot":
                section = None
            continue

    # Derive sports handle from win / win_pct
    if sports_pool_win is not None and sports_pool_pct is not None \
            and sports_pool_pct != 0:
        handle_thousands = sports_pool_win / (sports_pool_pct / 100.0)
        facts.append(_fact("sports_handle", handle_thousands, "sports-wagering"))

    return facts


# ---------------------------------------------------------------------------
# Taxable page parser
# ---------------------------------------------------------------------------

# Nevada Gross Revenue Tax — top marginal rate (NRS 463.370).
# The statewide aggregate is entirely within the top tier (>$134K/month per
# licensee), so applying 6.75% to the statewide taxable base gives the correct
# estimate of aggregate tax remitted.  Individual licensees on lower tiers
# are a negligible fraction of the statewide total.
_NV_GRT_RATE = 0.0675


def _parse_taxable(text: str, period: str, source_url: str) -> list[MetricFact]:
    """Extract statewide taxable revenue base from the appendix page and
    compute the estimated aggregate gross revenue tax at 6.75% (NRS 463.370).

    The GRI "Taxable Revenue by Area" appendix reports the taxable GGR base
    in thousands of USD — NOT the tax dollars remitted.  Storing that raw base
    as ``tax_paid`` inflated the figure to ~$1.3 B/month (≈ GGR) instead of
    the correct ~$83–91 M/month.  We apply the top-tier NV GRT rate here to
    produce an accurate tax estimate.
    """
    for line in text.splitlines():
        line = line.strip()
        if not re.match(r"Statewide\s+All\s+Nonrestricted\s+Locations\b", line, re.I):
            continue
        rest = re.sub(
            r"Statewide\s+All\s+Nonrestricted\s+Locations\s*", "", line, flags=re.I
        )
        nums = _extract_nums(rest)
        if not nums or nums[0] < 1_000:  # guard against ToC page number
            continue
        taxable_base_thousands = nums[0]
        # tax_paid = taxable base × 6.75% (NV Gross Revenue Tax, top tier)
        tax_thousands = taxable_base_thousands * _NV_GRT_RATE
        return [MetricFact(
            state="NV",
            operator="NV Statewide",
            vertical="commercial-casino",
            period=period,
            metric_name="tax_paid",
            value_usd_cents=_thousands_to_cents(tax_thousands),
            source_url=source_url,
        )]
    return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_nv_pdf(path: Path, source_url: str) -> list[MetricFact]:
    """Parse a Nevada GRI monthly PDF and return a list of MetricFact rows.

    Parameters
    ----------
    path:
        Local path to the downloaded GRI PDF.
    source_url:
        Canonical URL the PDF was fetched from (stored in every MetricFact).

    Returns
    -------
    list[MetricFact]
        One row per metric.  All dollar values are in USD cents (integer).
        Win amounts in the GRI are published in thousands; this function
        converts them to cents.

    Metrics emitted
    ---------------
    vertical="commercial-casino":
        - table_win      — Table / Counter / Card Games total win (current month)
        - slot_win       — Slot Machines total win (current month)
        - race_win       — Race Book total win (current month)
        - ggr            — Total Gaming win (table + slot, current month)
        - tax_paid       — Statewide taxable revenue base (appendix page)

    vertical="sports-wagering":
        - sports_win        — Sports Pool total win (current month)
        - sports_handle     — Sports Pool handle = win / (win_pct/100).
                              Omitted when win_pct is unavailable or zero.
        - sports_mobile_win — Mobile sports win (subset of sports_win)
    """
    period = _period_from_url(source_url)

    with pdfplumber.open(str(path)) as pdf:
        # First pass: locate the two pages of interest
        sw_idx, tx_idx = _identify_pages(pdf)

        if period is None:
            # Try to extract period from the first page that has text
            for page in pdf.pages:
                txt = page.extract_text() or ""
                if txt.strip():
                    period = _period_from_text(txt)
                    if period:
                        break

        fallback_period = period or "0000-00"
        facts: list[MetricFact] = []

        if sw_idx is not None:
            facts.extend(_parse_statewide(
                pdf.pages[sw_idx].extract_text() or "",
                fallback_period, source_url,
            ))

        if tx_idx is not None:
            facts.extend(_parse_taxable(
                pdf.pages[tx_idx].extract_text() or "",
                fallback_period, source_url,
            ))

    # Deduplicate: keep last occurrence by (metric_name, vertical)
    seen: dict[tuple[str, str], MetricFact] = {}
    for f in facts:
        seen[(f.metric_name, f.vertical)] = f

    return list(seen.values())
