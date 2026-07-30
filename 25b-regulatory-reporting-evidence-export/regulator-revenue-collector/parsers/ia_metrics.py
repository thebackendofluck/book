# Companion code for "The Backend of Luck" - Chapter 25b, Regulatory Reporting and Evidence Export.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Iowa Racing and Gaming Commission monthly revenue PDF parser.

IRGC publishes two PDFs per month — Gaming Revenue Report and Sports
Wagering Revenue Report. Both share a transposed layout: each "block" on
a page has 1-2 wrapped header rows of operator names at the top followed
by metric rows whose first cell is the metric label (e.g. ``STATE TAX``)
and whose remaining cells align positionally with those operator columns.

pdfplumber's ``extract_tables()`` works for the most recent format but fails
to detect tables on older PDFs (they have no ruling lines). This parser
therefore works directly off ``extract_words()`` and clusters words by
x-coordinate to recover columns — which works for every IRGC PDF format
from 2019 onwards (verified against Feb-2026, Mar-2026 samples).

Gaming PDF (commercial-casino), per-licensee block, row labels we care about:
    ADJUSTED GROSS REVENUE   → ggr
    STATE TAX                → tax_paid

Sports PDF (sports-wagering), per-licensee block:
    SPORTS WAGERING NET RECEIPTS → ggr
    SPORTS WAGERING HANDLE       → handle
    STATE TAX                    → tax_paid

Iowa casino tax is graduated (5–22 %); we never compute tax — we read
STATE TAX directly from the report.
"""
from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pdfplumber

from .metrics_model import MetricFact

# ---------------------------------------------------------------------------
# Constants / regex
# ---------------------------------------------------------------------------

_MONTHS = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
}

# Page-0 title is e.g. "GAMING REVENUE REPORT -- MARCH 2026"
_TITLE_RE = re.compile(
    r"\b(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|"
    r"SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\s+(\d{4})\b",
    re.I,
)

# Map metric label → canonical metric_name. Multi-word labels are tested
# against the joined-by-space line prefix (case-insensitive).
_CASINO_ROW_METRICS = {
    "ADJUSTED GROSS REVENUE": "ggr",
    "STATE TAX":              "tax_paid",
}
_SPORTS_ROW_METRICS = {
    "SPORTS WAGERING NET RECEIPTS": "ggr",
    "SPORTS WAGERING HANDLE":       "handle",
    "STATE TAX":                    "tax_paid",
}

_VALUE_RE = re.compile(r"^\(?-?\$?[\d,]+(?:\.\d+)?\)?$")
_TOTALS_RE = re.compile(r"^\s*totals?\s*$", re.I)

# Metric labels live in the left margin (x ≈ 40); the first operator-data
# value column starts at x ≈ 195+. Sports labels can wrap to ~115 px (e.g.
# "SPORTS WAGERING NET RECEIPTS"), so we split label vs. value at 180 px —
# still safely below any value column anchor across all sampled IRGC PDFs.
_LABEL_X_THRESHOLD = 180

# Header-row detection uses a lower threshold (100 px) because operator-name
# columns start as far left as ~155 px; a true metric-row label always sits
# in the 40–80 px range, so any row whose first word is right of 100 cannot
# be a metric label and is therefore a header / continuation.
_HEADER_X_THRESHOLD = 100


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_cents(raw: str) -> int | None:
    if raw is None:
        return None
    s = raw.strip()
    if not s or s in ("-", "--"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    s = s.replace("$", "").replace(",", "").replace(" ", "")
    if not s:
        return None
    try:
        cents = int((Decimal(s) * 100).quantize(Decimal("1")))
    except InvalidOperation:
        return None
    return -cents if neg else cents


def _period_from_pdf(pdf, source_url: str) -> str | None:
    """Extract YYYY-MM from the page-0 title; fall back to URL/path."""
    if pdf.pages:
        text = pdf.pages[0].extract_text() or ""
        m = _TITLE_RE.search(text)
        if m:
            return f"{m.group(2)}-{_MONTHS[m.group(1).lower()]}"
    m = _TITLE_RE.search(source_url)
    if m:
        return f"{m.group(2)}-{_MONTHS[m.group(1).lower()]}"
    return None


def _group_words_by_row(words, y_tol: int = 3) -> list[list[dict]]:
    """Cluster words by their ``top`` coordinate into visual rows."""
    if not words:
        return []
    by_top: dict[int, list[dict]] = defaultdict(list)
    for w in words:
        by_top[round(w["top"] / y_tol) * y_tol].append(w)
    rows = []
    for k in sorted(by_top.keys()):
        line = sorted(by_top[k], key=lambda w: w["x0"])
        rows.append(line)
    return rows


_VALUE_OR_PCT_RE = re.compile(r"^\(?-?\$?[\d,]+(?:\.\d+)?%?\)?$")


def _is_value_row(row: list[dict]) -> bool:
    """A value row has ≥3 numeric/percentage tokens right of the label area.

    "Value" includes percentage cells (e.g. ``18.14%``), counts (``24``),
    money (``$1,782``), and parenthesised negatives — so that rows like
    "TABLE REVENUE PERCENTAGE" and "NUMBER OF TABLE GAMES" stay classified
    as value rows (not section headers) and don't tear down ``columns``.
    Money-only filtering happens later when we emit MetricFacts via
    ``_to_cents``, which rejects percentages.
    """
    n_values = sum(
        1 for w in row
        if w["x0"] >= _LABEL_X_THRESHOLD and _VALUE_OR_PCT_RE.match(w["text"])
    )
    return n_values >= 3


def _row_label(row: list[dict]) -> str:
    """Concat the label words (those left of _LABEL_X_THRESHOLD)."""
    return " ".join(w["text"] for w in row if w["x0"] < _LABEL_X_THRESHOLD)


def _build_operator_columns(header_rows: list[list[dict]]) -> list[tuple[float, str]]:
    """Cluster header words into per-operator (anchor_x, name) tuples.

    The first row of a block is the primary header; an optional second row
    contains continuation tokens (e.g. wrapped names). We anchor each
    operator at the first-word x-coordinate of its first row, then attach
    any second-row tokens whose x-coordinate falls within the operator's
    column span (from this anchor up to the next operator's anchor).
    """
    if not header_rows or not header_rows[0]:
        return []
    primary = header_rows[0]
    # Group consecutive primary-row words into operator clusters whenever the
    # gap between word.x1 and the next word.x0 exceeds 6 px. Within an
    # operator name, words sit ~2.5 px apart; between operators the gap is
    # typically 15–30 px. The boundary between adjacent operators that
    # happen to share visual whitespace (e.g. "Grand Falls Casino" sitting
    # next to "Hard Rock Casino" with the "Resort" continuation on row 2)
    # collapses to ~7 px, so 6 splits cleanly without breaking long names.
    clusters: list[list[dict]] = []
    current: list[dict] = []
    last_x1 = None
    for w in primary:
        if last_x1 is None or (w["x0"] - last_x1) <= 6:
            current.append(w)
        else:
            clusters.append(current)
            current = [w]
        last_x1 = w["x1"]
    if current:
        clusters.append(current)

    # Anchor + initial name per cluster. Each cluster's centre-x is its
    # geometric mid-point — used to attach continuation words by nearest-
    # centre rather than range-overlap, since adjacent operator anchors can
    # sit just 2 px apart from a continuation word that visually belongs to
    # the *next* operator (e.g. "Emmetsburg" landing 2 px before the next
    # "Wild Rose -" anchor in the IA Wild Rose row).
    cols: list[dict] = []
    for c in clusters:
        anchor_x = c[0]["x0"]
        center_x = (c[0]["x0"] + c[-1]["x1"]) / 2
        name = " ".join(w["text"] for w in c)
        cols.append({
            "anchor_x": anchor_x,
            "center_x": center_x,
            "name": name,
        })

    # Attach continuation rows: each continuation word goes to the column
    # whose centre-x is closest to the word's centre-x.
    for cont in header_rows[1:]:
        for w in cont:
            wc = (w["x0"] + w["x1"]) / 2
            best_i = min(range(len(cols)), key=lambda i: abs(cols[i]["center_x"] - wc))
            cols[best_i]["name"] = (cols[best_i]["name"] + " " + w["text"]).strip()
    return [(c["anchor_x"], c["name"].strip()) for c in cols]


def _assign_value_to_column(
    word: dict, columns: list[tuple[float, str]],
) -> str | None:
    """Return the operator name whose column contains the value's centre-x."""
    if not columns:
        return None
    cx = (word["x0"] + word["x1"]) / 2
    # Pick the column whose anchor_x is the largest one ≤ cx (i.e. the
    # column the value belongs to). Values render right-aligned within
    # their column so anchor_x is always to the LEFT of the value.
    best_idx = None
    best_anchor = -1.0
    for i, (anchor_x, _) in enumerate(columns):
        if anchor_x <= word["x1"] and anchor_x > best_anchor:
            best_anchor = anchor_x
            best_idx = i
    if best_idx is None:
        return None
    return columns[best_idx][1]


# ---------------------------------------------------------------------------
# Per-page extraction
# ---------------------------------------------------------------------------


def _extract_facts_from_page(
    page,
    period: str,
    vertical: str,
    metric_map: dict[str, str],
    source_url: str,
) -> list[MetricFact]:
    """Walk a page top-to-bottom, splitting it into header→metric blocks."""
    words = page.extract_words(use_text_flow=True, keep_blank_chars=False)
    rows = _group_words_by_row(words)

    out: list[MetricFact] = []
    columns: list[tuple[float, str]] = []
    pending_header: list[list[dict]] = []

    # Build the metric_map lookup as a dict keyed by uppercased label.
    metric_map_upper = {k.upper(): v for k, v in metric_map.items()}

    for row in rows:
        if not row:
            continue

        # Skip the report title line (first row on each page).
        if any(w["text"].upper() in ("REPORT", "GAMING", "SPORTS") for w in row[:3]) \
                and not _is_value_row(row):
            pending_header = []
            columns = []
            continue

        if _is_value_row(row):
            # Flush any pending header rows before processing the first
            # metric row of a new block.
            if pending_header:
                columns = _build_operator_columns(pending_header)
                pending_header = []

            label_upper = _row_label(row).upper().strip()
            metric = metric_map_upper.get(label_upper)
            if metric is None or not columns:
                continue

            for w in row:
                if w["x0"] < _LABEL_X_THRESHOLD:
                    continue
                if not _VALUE_RE.match(w["text"]):
                    continue
                op = _assign_value_to_column(w, columns)
                if not op or _TOTALS_RE.match(op):
                    continue
                cents = _to_cents(w["text"])
                if cents is None:
                    continue
                out.append(MetricFact(
                    state="IA",
                    operator=op,
                    vertical=vertical,
                    period=period,
                    metric_name=metric,
                    value_usd_cents=cents,
                    source_url=source_url,
                ))
        else:
            # Non-value row: it's either a header row, a wrapped-header
            # continuation, or a section title. Section titles like
            # "BLACKJACK", "TABLE REVENUE BY GAME", or "ONLINE SPORTS
            # WAGERING BY OPERATOR" don't span columns; we treat any row
            # whose words all start at x ≥ _LABEL_X_THRESHOLD as header.
            if all(w["x0"] >= _HEADER_X_THRESHOLD for w in row):
                pending_header.append(row)
            else:
                # A label-margin word means a new section is starting; reset.
                pending_header = []
                columns = []
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_ia(
    path: Path,
    vertical: str,
    source_url: str,
) -> list[MetricFact]:
    """Parse an IRGC monthly revenue PDF (gaming OR sports).

    Args:
        path:        Local path to the downloaded PDF.
        vertical:    "commercial-casino" or "sports-wagering" — selects which
                     row labels to emit.
        source_url:  Canonical download URL, stored on every MetricFact.

    Returns:
        List of MetricFact records (operator-level GGR / handle / tax_paid).
    """
    if vertical == "commercial-casino":
        metric_map = _CASINO_ROW_METRICS
    elif vertical == "sports-wagering":
        metric_map = _SPORTS_ROW_METRICS
    else:
        return []

    # Guard: reject non-PDF bytes early (CDN errors return HTML).
    try:
        with open(path, "rb") as fh:
            magic = fh.read(4)
        if magic != b"%PDF":
            return []
    except OSError:
        return []

    facts: list[MetricFact] = []
    try:
        with pdfplumber.open(str(path)) as pdf:
            period = _period_from_pdf(pdf, source_url)
            if period is None:
                return []
            for page in pdf.pages:
                facts.extend(_extract_facts_from_page(
                    page, period, vertical, metric_map, source_url,
                ))
    except Exception:  # noqa: BLE001 — never let a bad PDF crash the run
        return []

    # De-dup: a metric label like "STATE TAX" appears on every per-licensee
    # block, but each operator only appears in ONE block per page, so the
    # combination (operator, metric, period) should already be unique. The
    # dedup below guards against page-2+ "TABLE REVENUE BY GAME" leakage if
    # any of those rows were ever to share a metric name.
    seen: set[tuple[str, str, str]] = set()
    unique: list[MetricFact] = []
    for f in facts:
        key = (f.operator, f.metric_name, f.period)
        if key in seen:
            continue
        seen.add(key)
        unique.append(f)
    return unique
