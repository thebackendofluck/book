# Companion code for "The Backend of Luck" - Chapter 25b, Regulatory Reporting and Evidence Export.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Virginia Lottery sports-wagering board meeting PDF parser.

VA does not publish per-operator monthly CSVs or XLS files.  The closest
machine-readable source is the quarterly "Gaming Update / Gaming Compliance"
board-meeting slide decks published at:

  https://cdnprodpaasmedia-valottery-com.azureedge.net/-/media/val/images/
  promos/casinos-sports-betting/board-meetings/<slug>.pdf

Layout reality (confirmed across 2023-2026 decks):
  - The per-operator monthly Handle / AGR / Promotional Deduction /
    Taxable AGR / Tax rows are rendered as vector-image slides; they do NOT
    produce extractable table rows via pdfplumber.
  - Some decks contain a text-layer table on a "Sports Wagering Financial
    Summary" slide that CAN be extracted; the layout varies between meetings.

Strategy implemented here:
  1. Scan every page for tables that look like a sports-wagering financial
     summary (operator column + month-labelled numeric columns).
  2. Identify metric rows by matching row-label keywords
     (Handle, AGR, Promo*, Taxable, Tax).
  3. Parse dollar values and emit one MetricFact per (operator, period,
     metric_name) triple.
  4. If no parseable table is found, return [] — never raise.

Metric name mapping (VA → canonical):
  handle            → "handle"
  agr / gross rev   → "ggr"
  promotional*      → "promotional_play"
  taxable agr       → "taxable_ggr"   (emitted as-is; callers may alias)
  tax (15%)         → "tax_paid"
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pdfplumber

from .metrics_model import MetricFact

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Operators that appear across VA decks (brand-name only, lower-case)
_KNOWN_OPERATORS: frozenset[str] = frozenset({
    "fanduel", "draftkings", "betmgm", "caesars", "betrivers", "rivers",
    "espn bet", "espn", "bally", "fanatics", "hard rock", "hard rock bet",
    "bet365", "betr", "superbook", "sporttrade", "betway", "wynn", "888",
    "unibet", "betfred", "dc sports",
})

# Row-label → canonical metric_name
_METRIC_LABELS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bhandle\b", re.I),                          "handle"),
    (re.compile(r"\btaxable\s+agr\b", re.I),                   "taxable_ggr"),
    (re.compile(r"\b(agr|adjusted\s+gross\s+rev)", re.I),      "ggr"),
    (re.compile(r"\bpromo(tional)?\b", re.I),                  "promotional_play"),
    (re.compile(r"\btax\b", re.I),                             "tax_paid"),
]

# Minimum table geometry to be considered a candidate financial table
_MIN_COLS = 3   # at least label col + 2 month cols
_MIN_ROWS = 3   # at least header + 2 data rows

# How many rows to scan for column headers (operator names / month labels)
_HEADER_SCAN = 6

# ---------------------------------------------------------------------------
# Period helper (specified in the task description)
# ---------------------------------------------------------------------------

def _period_from_label(s: object) -> str | None:
    """Return 'YYYY-MM' from a short month label like 'Mar 2026' or 'Mar-26'."""
    if not s:
        return None
    months = {
        "jan": "01", "feb": "02", "mar": "03", "apr": "04",
        "may": "05", "jun": "06", "jul": "07", "aug": "08",
        "sep": "09", "oct": "10", "nov": "11", "dec": "12",
    }
    m = re.search(r"([A-Za-z]{3,9})\s*[-/]?\s*(\d{2,4})", str(s))
    if not m:
        return None
    mon = m.group(1)[:3].lower()
    yr = m.group(2)
    if len(yr) == 2:
        yr = "20" + yr
    return f"{yr}-{months[mon]}" if mon in months else None


# ---------------------------------------------------------------------------
# Dollar parsing
# ---------------------------------------------------------------------------

_MONEY_STRIP = re.compile(r"[\$,\s\xa0]")
_NUM_RE = re.compile(r"-?\d[\d.]*")


def _to_cents(raw: object) -> int | None:
    """Parse a cell value like '$1,234,567' or '(500,000)' to integer cents.

    Returns None for empty, dash, or unparseable cells.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if s in ("", "-", "–", "—", "N/A", "n/a"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    s = _MONEY_STRIP.sub("", s)
    if not s:
        return None
    m = _NUM_RE.search(s)
    if not m:
        return None
    try:
        val = float(m.group(0))
    except ValueError:
        return None
    return int(round(val * 100)) * (-1 if neg else 1)


# ---------------------------------------------------------------------------
# Operator name normalisation
# ---------------------------------------------------------------------------

_DBA_RE = re.compile(r"d/b/a\s+", re.I)
_LLC_RE = re.compile(
    r"\b(llc|inc|corp|ltd|plc|interactive|virginia|gaming|group|sports|"
    r"interactive\s+us|betfair|penn\s+sports)\b.*",
    re.I,
)

def _normalise_operator(raw: str) -> str:
    """Strip legal suffixes and return a clean brand name."""
    s = raw.strip()
    # Prefer the d/b/a name when present
    m = _DBA_RE.search(s)
    if m:
        s = s[m.end():]
    # Remove trailing legal boilerplate
    s = _LLC_RE.sub("", s).strip(" ,.")
    return s or raw.strip()


def _is_operator_label(text: str) -> bool:
    """True if the normalised text matches a known VA sports-book brand."""
    low = text.lower()
    return any(op in low for op in _KNOWN_OPERATORS)


# ---------------------------------------------------------------------------
# Table structure detection
# ---------------------------------------------------------------------------

def _norm_cell(v: object) -> str:
    if v is None:
        return ""
    return re.sub(r"\s+", " ", str(v)).strip()


def _find_month_columns(rows: list[list[object]]) -> dict[int, str]:
    """Scan the first _HEADER_SCAN rows for month labels; return col_idx→period."""
    result: dict[int, str] = {}
    for row in rows[:_HEADER_SCAN]:
        for ci, cell in enumerate(row):
            p = _period_from_label(cell)
            if p:
                result[ci] = p
        if result:
            return result
    return result


def _classify_metric(label: str) -> str | None:
    for pat, name in _METRIC_LABELS:
        if pat.search(label):
            return name
    return None


# ---------------------------------------------------------------------------
# Two-axis layout: rows = operators, cols = months, sub-rows = metrics
# ---------------------------------------------------------------------------
# VA slides typically have one of two layouts:
#
#  Layout A — operator-major (one block per operator)
#    Row 0: "FanDuel"
#    Row 1:  Handle   | $M1 | $M2 | ...
#    Row 2:  AGR      | $M1 | $M2 | ...
#    ...
#    Row N: "DraftKings"
#    ...
#
#  Layout B — month-major (one row per metric, columns per operator)
#    Col 0: metric label
#    Col 1: FanDuel handle  | FanDuel AGR | ...  (month blocks)
#    ...
#
# Because the tables are largely image-rendered in recent decks this code
# is primarily forward-compatible for future decks that may gain text tables.

def _parse_operator_major(table: list[list[object]], source_url: str) -> list[MetricFact]:
    """Parse layout A: operator label rows followed by metric sub-rows."""
    facts: list[MetricFact] = []
    month_cols = _find_month_columns(table)
    if not month_cols:
        return facts

    current_op: str = ""
    for row in table:
        if not row:
            continue
        first = _norm_cell(row[0])
        if not first:
            continue

        # Operator label row: first col is operator name, rest are empty/None
        rest_vals = [_norm_cell(c) for c in row[1:] if _norm_cell(c)]
        if _is_operator_label(first) and len(rest_vals) == 0:
            current_op = _normalise_operator(first)
            continue

        # Metric row
        metric = _classify_metric(first)
        if metric is None or not current_op:
            continue

        for col_idx, period in month_cols.items():
            if col_idx >= len(row):
                continue
            cents = _to_cents(row[col_idx])
            if cents is None:
                continue
            facts.append(MetricFact(
                state="VA",
                operator=current_op,
                vertical="sports-wagering",
                period=period,
                metric_name=metric,
                value_usd_cents=cents,
                source_url=source_url,
            ))

    return facts


def _parse_metric_major(table: list[list[object]], source_url: str) -> list[MetricFact]:
    """Parse layout B: first row = operator names, first col = metric label,
    subsequent columns = values per operator, possibly with month sub-columns.

    Also handles a variant where the header row contains month names and
    operator blocks span multiple columns.
    """
    facts: list[MetricFact] = []
    if len(table) < _MIN_ROWS:
        return facts

    # Determine header row: the first row with >= 2 non-empty cells
    header_row_idx = 0
    for i, row in enumerate(table[:_HEADER_SCAN]):
        non_empty = [_norm_cell(c) for c in row if _norm_cell(c)]
        if len(non_empty) >= 2:
            header_row_idx = i
            break

    header = table[header_row_idx]

    # Build operator→col-index mapping from header
    op_cols: dict[int, str] = {}
    for ci, cell in enumerate(header):
        label = _norm_cell(cell)
        if label and _is_operator_label(label):
            op_cols[ci] = _normalise_operator(label)

    if not op_cols:
        return facts

    # Find period: look for a month label in the header or a preceding row
    period: str | None = None
    for row in table[:header_row_idx + 2]:
        for cell in row:
            p = _period_from_label(cell)
            if p:
                period = p
                break
        if period:
            break

    if not period:
        return facts

    # Data rows: col 0 = metric label, remaining cols = values per operator
    for row in table[header_row_idx + 1:]:
        if not row:
            continue
        label = _norm_cell(row[0])
        metric = _classify_metric(label)
        if metric is None:
            continue
        for ci, op in op_cols.items():
            if ci >= len(row):
                continue
            cents = _to_cents(row[ci])
            if cents is None:
                continue
            facts.append(MetricFact(
                state="VA",
                operator=op,
                vertical="sports-wagering",
                period=period,
                metric_name=metric,
                value_usd_cents=cents,
                source_url=source_url,
            ))

    return facts


# ---------------------------------------------------------------------------
# Page-level dispatcher
# ---------------------------------------------------------------------------

_SPORTS_KEYWORDS = re.compile(
    r"(sports\s+wager|sports\s+bet|handle|adjusted\s+gross|taxable\s+agr|"
    r"promotional\s+deduct|tax\s+revenue|15%\s+tax)",
    re.I,
)


def _page_is_candidate(page) -> bool:
    """Quick pre-filter: skip pages with no sports-wagering financial keywords."""
    text = page.extract_text() or ""
    return bool(_SPORTS_KEYWORDS.search(text))


def _parse_page(page, source_url: str) -> list[MetricFact]:
    facts: list[MetricFact] = []
    tables = page.extract_tables() or []
    for table in tables:
        if not table or len(table) < _MIN_ROWS:
            continue
        if max((len(r) for r in table), default=0) < _MIN_COLS:
            continue

        # Try operator-major first (most common VA layout)
        found = _parse_operator_major(table, source_url)
        if found:
            facts.extend(found)
            continue

        # Fall back to metric-major
        found = _parse_metric_major(table, source_url)
        facts.extend(found)

    return facts


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_va_pdf(path: Path, source_url: str) -> list[MetricFact]:
    """Parse a Virginia Lottery board-meeting "Gaming Update" PDF.

    Iterates all pages, locates sports-wagering financial summary tables,
    and returns one MetricFact per (operator, period, metric_name) triple.

    Returns [] if no parseable table is found — never raises.

    Args:
        path:       Local path to the downloaded PDF.
        source_url: CDN URL used as provenance on every MetricFact.

    Returns:
        List of MetricFact instances with state="VA",
        vertical="sports-wagering", and metric_name in:
        "handle", "ggr", "promotional_play", "taxable_ggr", "tax_paid".
    """
    try:
        pdf_path = Path(path)
        if not pdf_path.exists():
            logger.warning("va_metrics: file not found: %s", pdf_path)
            return []

        # Reject non-PDF bytes early (some CDN URLs return HTML 302/404 pages)
        with pdf_path.open("rb") as fh:
            magic = fh.read(4)
        if magic != b"%PDF":
            logger.warning("va_metrics: not a PDF (magic=%r): %s", magic, pdf_path)
            return []

        facts: list[MetricFact] = []
        seen: set[tuple[str, str, str]] = set()

        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages:
                if not _page_is_candidate(page):
                    continue
                try:
                    page_facts = _parse_page(page, source_url)
                except Exception:
                    logger.exception(
                        "va_metrics: error parsing page %d of %s",
                        page.page_number,
                        pdf_path.name,
                    )
                    continue

                for fact in page_facts:
                    dedup_key = (fact.operator, fact.period, fact.metric_name)
                    if dedup_key in seen:
                        continue
                    seen.add(dedup_key)
                    facts.append(fact)

        facts.sort(key=lambda f: (f.period, f.operator, f.metric_name))
        return facts

    except Exception:
        logger.exception("va_metrics: failed to parse %s", path)
        return []
