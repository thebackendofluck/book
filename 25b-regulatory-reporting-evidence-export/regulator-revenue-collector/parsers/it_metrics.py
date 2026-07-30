# Companion code for "The Backend of Luck" - Chapter 25b, Regulatory Reporting and Evidence Export.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Italy ADM Libro Blu Appendice parser.

Extracts Tabella A.96 — "Riepilogo nazionale per tipologia di gioco relativo
al gioco a distanza" — from the Appendice PDF.

The table has this layout (pdfplumber extraction):

    Row 0: ['Tabella A.96 - Riepilogo ...', None, …]
    Row 1: ['', 'Raccolta', None, None, 'Vincite', None, None, 'Spesa', None, None]
    Row 2: ['Tipologia di', None, …]
    Row 3: ['gioco', '2021', '2022', '2023', '2021', '2022', '2023', '2021', '2022', '2023']
    Row 4: ['', None, …]
    Row 5+: data rows e.g.
        ['Betting Exchange', '2.238,14', '2.678,69', '2.951,13', ...]
        ['Bingo', '274,73', '244,98', '250,31', ...]
        ['Giochi di carte\norganizzata ...', '47.521,71', ...]
        ['Giochi numerici\n...', '54,62', ...]
        ['Giochi a base\nippica', ...]    # horse racing (not online)
        ['Giochi a base\nsportiva', ...]  # sports (online)
        ['Poker Cash', ...]
        ['Torneo', ...]
        ['Slot machines', ...]
        ['Skillgames', ...]
        ['Totale', ...]

Values are expressed in millions of EUR. Columns map to years read from
the header row (positions 1,2,3 → raccolta; 4,5,6 → vincite; 7,8,9 → spesa).

Vertical mapping (ADM term → canonical):
  Betting Exchange          → sports-wagering
  Bingo                     → bingo
  Giochi di carte / sorte   → igaming        (casino — slots + table games + live)
  Giochi a base sportiva    → sports-wagering
  Giochi a base ippica      → sports-wagering (horse racing)
  Giochi numerici           → igaming        (online lottery/numbers)
  Poker Cash                → online-poker
  Torneo                    → online-poker
  Slot machines             → igaming
  Skillgames                → igaming

EUR → USD conversion: multiply by 1.08 (fixed rate per project spec),
then convert millions-of-EUR to USD cents: val_meur × 1_000_000 × 1.08 × 100.

Returns one MetricFact per (vertical, year, metric_name) triple.
Expected facts per file: ~3 metrics × ~3 years × ~5 verticals ≈ 45 facts.
"""
from __future__ import annotations

import re
from pathlib import Path

import pdfplumber

from .metrics_model import MetricFact

# --- constants ---------------------------------------------------------------

EUR_TO_USD = 1.08          # fixed rate per project specification
MILLION = 1_000_000
# millions of EUR × EUR_TO_USD × MILLION × 100 (cents)
_M_EUR_TO_USD_CENTS = EUR_TO_USD * MILLION * 100

# Vertical classification: (fragment to match in lowercase ADM label) → canonical
_VERTICAL_MAP: list[tuple[str, str]] = [
    ("poker cash",             "online-poker"),
    ("torneo",                 "online-poker"),
    ("bingo",                  "bingo"),
    ("betting exchange",       "sports-wagering"),
    ("base sportiva",          "sports-wagering"),
    ("base ippica",            "sports-wagering"),
    ("giochi di carte",        "igaming"),
    ("sorte a quota fissa",    "igaming"),
    ("giochi numerici",        "igaming"),
    ("slot machines",          "igaming"),
    ("skillgames",             "igaming"),
    ("scommesse",              "sports-wagering"),
]

# Italian number format: 1.234,56 → float 1234.56
_STRIP_RE = re.compile(r"[^\d,\-]")


def _parse_it_number(raw: str | None) -> float | None:
    """Convert Italian-formatted number (1.234,56 or -) to float."""
    if raw is None:
        return None
    s = raw.strip()
    if s in ("", "-", "–"):
        return 0.0
    # Remove thousands separators (dots) then convert decimal comma to dot
    s = _STRIP_RE.sub("", s)       # keep digits, comma, minus
    if not s or s == "-":
        return 0.0
    # Remove thousand-dot separators already gone; convert comma decimal
    s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _classify_vertical(label: str) -> str | None:
    low = label.lower().replace("\n", " ")
    for fragment, vertical in _VERTICAL_MAP:
        if fragment in low:
            return vertical
    return None


def _to_cents(value_meur: float) -> int:
    """Millions of EUR → USD cents at fixed 1.08 rate."""
    return int(round(value_meur * _M_EUR_TO_USD_CENTS))


# --- table finder ------------------------------------------------------------

def _find_a96_table(pdf) -> list[list] | None:
    """Locate and return the rows of Tabella A.96 from any page of the PDF.

    We match the exact table number "A.96" in the title cell, which is always
    the first cell of the first row in pdfplumber's output.  A.86 (physical vs
    online total) also contains "gioco a distanza" in its title so we must not
    rely on that phrase alone.
    """
    for page in pdf.pages:
        for table in page.extract_tables() or []:
            if not table or not table[0]:
                continue
            title_cell = str(table[0][0] or "")
            # Primary: explicit table number
            if "A.96" in title_cell:
                return table
            # Fallback: description without a table-number prefix (older editions)
            low = title_cell.lower()
            if ("riepilogo" in low and "distanza" in low
                    and "tipologia" in low and "A.86" not in title_cell
                    and "A.95" not in title_cell):
                return table
    return None


# --- header parser -----------------------------------------------------------

def _parse_header(table: list[list]) -> tuple[list[str], int]:
    """
    Return (year_labels, data_start_row).

    We scan rows until we find one whose cells look like years (4-digit numbers).
    Column layout: [label | R_yr1 | R_yr2 | R_yr3 | V_yr1 | V_yr2 | V_yr3 | S_yr1 | S_yr2 | S_yr3]
    """
    for i, row in enumerate(table):
        cells = [str(c or "").strip() for c in row]
        years = [c for c in cells if re.fullmatch(r"20\d{2}", c)]
        if len(years) >= 3:
            # deduplicate while preserving order
            seen: set[str] = set()
            unique_years: list[str] = []
            for y in years:
                if y not in seen:
                    seen.add(y)
                    unique_years.append(y)
            return unique_years[:3], i + 1
    # fallback: try to infer from context
    return ["2021", "2022", "2023"], 4


# --- main entry point --------------------------------------------------------

def parse_it_pdf(
    path: Path,
    source_url: str = "",
) -> list[MetricFact]:
    """
    Parse an ADM Libro Blu Appendice PDF and extract MetricFacts from
    Tabella A.96 (online gambling breakdown by vertical).

    Returns a list of MetricFact with:
      state="IT", operator="IT Statewide",
      vertical = canonical vertical string,
      period = "YYYY-12" (annual, keyed to December of the reference year),
      metric_name in {"raccolta", "vincite", "spesa"},
      value_usd_cents = EUR millions × 1.08 × 1e6 × 100.
    """
    facts: list[MetricFact] = []

    with pdfplumber.open(str(path)) as pdf:
        table = _find_a96_table(pdf)

    if table is None:
        return facts

    years, data_start = _parse_header(table)

    # ADM PDFs split long Italian labels across multiple physical rows using
    # pdfplumber's row detection.  The split pattern observed in A.96 is:
    #
    #   row A: ['Giochi di carte', '47.521,71', '', '', '', …]
    #   row B: ['organizzata in',  None, …]         <- label fragment only
    #   row C: ['forma diversa',   None, …]         <- label fragment only
    #   row D: [None, None, '53.310,30', '60.481,10', …]  <- numeric continuation
    #   row E: ['dal torneo e',    None, …]         <- label fragment only
    #   …
    #
    # Strategy: a row is "numeric" if any of cols 1-9 has a non-empty value;
    # a row is "label-fragment" if col[0] has text but cols 1-9 are all empty.
    # When a numeric row has col[2] empty (yr2 missing), keep it in "pending"
    # and merge subsequent numeric rows into it until col[2] is filled.

    def _has_numeric(row: list) -> bool:
        return any(
            (row[i] is not None and str(row[i]).strip() not in ("", "-"))
            for i in range(1, min(10, len(row)))
        )

    def _col2_filled(row: list) -> bool:
        return len(row) > 2 and row[2] is not None and str(row[2]).strip() not in ("", "-")

    merged_rows: list[list] = []
    pending: list | None = None
    pending_label: str = ""
    # Track whether the last entry in merged_rows was flushed without a fully
    # resolved label (e.g. "Gioco a base" without the trailing "sportiva" or
    # "ippica" word).  Trailing non-numeric rows that carry the missing fragment
    # must be allowed to update that last entry before vertical classification.
    last_row_label_incomplete: bool = False

    for raw_row in table[data_start:]:
        if not raw_row or all((c is None or str(c).strip() == "") for c in raw_row):
            continue

        label_raw = str(raw_row[0] or "").replace("\n", " ").strip()
        is_numeric = _has_numeric(raw_row)

        if not is_numeric:
            # Pure label-fragment row (no data columns set).
            if pending is not None and label_raw:
                # Accumulate additional label text into the open pending row so
                # that "Gioco a base" + "sportiva" → "Gioco a base sportiva".
                pending_label = (pending_label + " " + label_raw).strip()
                pending[0] = pending_label
            elif pending is None and label_raw and last_row_label_incomplete and merged_rows:
                # The pending row was already flushed (col[2] was filled) but
                # the trailing label fragment ("sportiva", "ippica", …) arrived
                # afterward.  Patch the label on the already-appended row so
                # vertical classification can still succeed.
                prev_label = str(merged_rows[-1][0] or "").replace("\n", " ").strip()
                merged_rows[-1][0] = (prev_label + " " + label_raw).strip()
            # Always skip non-numeric rows
            continue

        # A numeric row resets the trailing-label-incomplete flag.
        last_row_label_incomplete = False

        # This row has numeric data.
        if pending is not None:
            # Check if this row completes the pending row (fills col[2])
            merged: list = list(pending)
            for ci, cell in enumerate(raw_row):
                if ci < len(merged):
                    existing = merged[ci]
                    if (existing is None or str(existing).strip() == "") and cell is not None and str(cell).strip() != "":
                        merged[ci] = cell
                else:
                    merged.append(cell)
            if _col2_filled(merged):
                merged_rows.append(merged)
                # Check if this row's label can already be classified; if not,
                # a trailing label fragment may still arrive.
                last_row_label_incomplete = (
                    _classify_vertical(str(merged[0] or "")) is None
                    and str(merged[0] or "").strip() != ""
                    and not str(merged[0] or "").lower().startswith("total")
                )
                pending = None
                pending_label = ""
            else:
                pending = merged
            continue

        # Start fresh row
        row_copy = list(raw_row)
        if not _col2_filled(row_copy):
            # yr2/yr3 columns not yet filled — keep pending for merging
            pending = row_copy
            pending_label = label_raw
        else:
            merged_rows.append(row_copy)
            last_row_label_incomplete = False

    if pending is not None:
        # Flush incomplete row as-is
        merged_rows.append(pending)

    # Now parse the cleaned merged rows
    for row in merged_rows:
        label_raw = str(row[0] or "").replace("\n", " ").strip()
        if not label_raw or label_raw.lower().startswith("total"):
            continue  # skip "Totale" aggregate row

        vertical = _classify_vertical(label_raw)
        if vertical is None:
            continue

        # Columns:  1,2,3 = raccolta; 4,5,6 = vincite; 7,8,9 = spesa
        # Canonical metric mapping (ADM Italian → dashboard-standard):
        #   raccolta (handle/stake)   → "handle"
        #   vincite  (winnings paid)  → "patron_winnings"
        #   spesa    (net loss = GGR) → "ggr"
        # Without these aliases, Italy would appear blank on every chart that
        # filters `metric_name='ggr'` (which is all of them).
        metric_cols = [
            ("handle",          1, 2, 3),
            ("patron_winnings", 4, 5, 6),
            ("ggr",             7, 8, 9),
        ]

        for metric_name, c0, c1, c2 in metric_cols:
            col_indices = [c0, c1, c2]
            for i, yr in enumerate(years):
                col = col_indices[i]
                if col >= len(row):
                    continue
                val = _parse_it_number(row[col])
                if val is None:
                    continue
                facts.append(MetricFact(
                    state="IT",
                    operator="IT Statewide",
                    vertical=vertical,
                    period=f"{yr}-12",          # annual → key to December
                    metric_name=metric_name,
                    value_usd_cents=_to_cents(val),
                    source_url=source_url,
                ))

    # Aggregate duplicate (vertical, period, metric_name) facts by summing.
    # Multiple ADM categories map to the same canonical vertical
    # (e.g. "Giochi di carte" and "Giochi numerici" both → "igaming";
    # "Betting Exchange", "Gioco a base sportiva", and "Gioco a base ippica"
    # all → "sports-wagering").  Without aggregation the ON CONFLICT DO UPDATE
    # upsert lets the last-written value overwrite all previous ones.
    from collections import defaultdict

    agg_cents: dict[tuple, int] = defaultdict(int)
    agg_meta: dict[tuple, MetricFact] = {}
    for f in facts:
        key = (f.vertical, f.period, f.metric_name)
        agg_cents[key] += f.value_usd_cents
        agg_meta[key] = f  # keep last for metadata fields (state, operator, source_url)

    return [
        MetricFact(
            state=meta.state,
            operator=meta.operator,
            vertical=key[0],
            period=key[1],
            metric_name=key[2],
            value_usd_cents=agg_cents[key],
            source_url=meta.source_url,
        )
        for key, meta in agg_meta.items()
    ]
