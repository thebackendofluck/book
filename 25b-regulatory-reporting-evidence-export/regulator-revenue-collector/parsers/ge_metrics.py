# Companion code for "The Backend of Luck" - Chapter 25b, Regulatory Reporting and Evidence Export.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Germany GGL Marktmonitor HTML parser.

Source:
    https://www.gluecksspiel-behoerde.de/de/forschung-und-publikationen/
    publikationen-der-ggl/marktmonitor

The page embeds two inline HTML tables under the heading
"Überblick der Spieleinsätze der länderübergreifenden Glücksspiele":

  Table I  – "Spieleinsätze der länderübergreifenden gefährlichen Glücksspiele"
             Row labels: Sportwetten stationär, Sportwetten online,
                         Sportwetten gesamt, Virtuelle Automatenspiele,
                         Online-Poker, Pferdewetten im Internet, Gesamt, …
  Table II – "Spieleinsätze der länderübergreifenden Lotterien" (ignored here)

Column headers encode YYYY + Q-number in free-text form, e.g. "2025 Q1".
Numeric cells are Spieleinsätze (GROSS STAKES — NOT GGR/net revenue).
Units: Mio. Euro (EUR millions).

IMPORTANT — metric_name is "stakes" throughout, not "ggr".
Spieleinsätze is the total wagered handle, which is a fundamentally different
concept from Gross Gaming Revenue (GGR/net).  The pipeline query layer must
NOT conflate these rows with ggr rows from other jurisdictions.

Vertical mapping applied to Table I only:
    "Sportwetten online"        → "sports-wagering"   (online only)
    "Sportwetten stationär"     → skipped             (retail; out of scope)
    "Sportwetten gesamt"        → skipped             (duplicate of above two)
    "Virtuelle Automatenspiele" → "igaming"
    "Online-Poker"              → "online-poker"
    All other rows              → skipped

FX: EUR millions → USD cents = value * 1_000_000 * 1.08 * 100
"""
from __future__ import annotations

import re
from pathlib import Path

from bs4 import BeautifulSoup, Tag

from .metrics_model import MetricFact

# ── Constants ─────────────────────────────────────────────────────────────────

_FX_EUR_USD: float = 1.08

# Quarter number → end-of-quarter month (ISO)
_Q_TO_MONTH: dict[int, str] = {1: "03", 2: "06", 3: "09", 4: "12"}

# Row-label substrings (lower-case) → canonical vertical.
# Order matters: more-specific entries first so "sportwetten online" matches
# before a hypothetical generic "sportwetten" fallback.
_VERTICAL_MAP: list[tuple[str, str]] = [
    ("sportwetten online", "sports-wagering"),
    ("virtuelle automatenspiele", "igaming"),
    ("online-poker", "online-poker"),
]

# Regex to pull YYYY and Q-number from a column header string.
# Handles: "2025 Q1", "Q1 2025", "2025Q1", "1. Quartal 2025", "2025/Q1".
_HEADER_RE = re.compile(
    r"(?:(\d{4})[^\d]+q?(\d))|(?:q?(\d)[^\d]+(\d{4}))",
    re.IGNORECASE,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _parse_header(text: str) -> str | None:
    """Return 'YYYY-MM' end-of-quarter period, or None if unrecognisable."""
    clean = text.strip().replace("\xa0", " ")
    m = _HEADER_RE.search(clean)
    if not m:
        return None
    # Group 1+2: year-first ("2025 Q1")
    if m.group(1) and m.group(2):
        year, q = int(m.group(1)), int(m.group(2))
    # Group 3+4: quarter-first ("Q1 2025")
    elif m.group(3) and m.group(4):
        q, year = int(m.group(3)), int(m.group(4))
    else:
        return None
    month = _Q_TO_MONTH.get(q)
    if month is None or not (2000 <= year <= 2100):
        return None
    return f"{year}-{month}"


def _parse_number(text: str) -> float | None:
    """Parse a German-locale numeric string to a Python float.

    Handles the two main formats found in GGL tables:
      "1.594"  → 1594.0    (German thousands separator ".")
      "1.099"  → 1099.0
      "210"    → 210.0     (plain integer, no separator)
      "1,5"    → 1.5       (German decimal separator ",")
      "1.594,5"→ 1594.5    (thousands + decimal)

    Disambiguation rule for a single dot without a comma:
      - If the dot is followed by exactly 3 digits at end-of-string,
        it is a German thousands separator → remove it.
      - Otherwise treat it as a decimal point (e.g. "1.5").
    """
    clean = text.strip().replace("\xa0", "").replace(" ", "").replace(" ", "")
    if not clean or clean in ("-", "–", "—", "n.v.", "k.A."):
        return None

    if "," in clean:
        # German locale: "." = thousands sep, "," = decimal sep
        clean = clean.replace(".", "").replace(",", ".")
    else:
        dot_positions = [i for i, c in enumerate(clean) if c == "."]
        if len(dot_positions) > 1:
            # Multiple dots → all are thousands separators
            clean = clean.replace(".", "")
        elif len(dot_positions) == 1:
            dot_idx = dot_positions[0]
            digits_after = len(clean) - dot_idx - 1
            if digits_after == 3:
                # e.g. "1.594" — dot separates thousands
                clean = clean.replace(".", "")
            # else: leave as decimal (e.g. "1.5")

    try:
        return float(clean)
    except ValueError:
        return None


def _map_vertical(row_label: str) -> str | None:
    low = row_label.lower()
    for fragment, vertical in _VERTICAL_MAP:
        if fragment in low:
            return vertical
    return None


def _eur_millions_to_usd_cents(value: float) -> int:
    return int(value * 1_000_000 * _FX_EUR_USD * 100)


# ── Table parser ──────────────────────────────────────────────────────────────


def _parse_table(table: Tag, source_url: str) -> list[MetricFact]:
    """Extract MetricFact rows from a single <table> BeautifulSoup element."""
    facts: list[MetricFact] = []

    # ── Collect all rows ──────────────────────────────────────────────────────
    rows = table.find_all("tr")
    if not rows:
        return []

    # ── Identify header row: first row where ≥1 cell parses as a quarter ─────
    header_idx: int | None = None
    col_periods: list[str | None] = []

    for ridx, row in enumerate(rows):
        cells = row.find_all(["th", "td"])
        if not cells:
            continue
        # Skip the first (label) cell; try to parse the rest as quarter headers.
        candidate = [_parse_header(c.get_text(" ", strip=True)) for c in cells[1:]]
        if any(p is not None for p in candidate):
            header_idx = ridx
            col_periods = candidate
            break

    if header_idx is None:
        return []  # no recognisable quarter columns in this table

    # ── Parse data rows ───────────────────────────────────────────────────────
    for row in rows[header_idx + 1:]:
        cells = row.find_all(["th", "td"])
        if not cells:
            continue

        label = cells[0].get_text(" ", strip=True)
        if not label:
            continue

        vertical = _map_vertical(label)
        if vertical is None:
            continue  # row not in scope (retail, totals, lotteries, etc.)

        data_cells = cells[1:]
        for cidx, period in enumerate(col_periods):
            if period is None:
                continue
            if cidx >= len(data_cells):
                break
            raw = data_cells[cidx].get_text(" ", strip=True)
            eur_millions = _parse_number(raw)
            if eur_millions is None:
                continue
            if eur_millions == 0.0:
                continue  # genuine zero — skip rather than store noise

            facts.append(MetricFact(
                state="GE",
                operator="GE Statewide",
                vertical=vertical,
                period=period,
                metric_name="stakes",  # Spieleinsätze = gross stakes, NOT GGR
                value_usd_cents=_eur_millions_to_usd_cents(eur_millions),
                source_url=source_url,
            ))

    return facts


# ── Public entry point ────────────────────────────────────────────────────────


def parse_ge_html(path: Path, source_url: str) -> list[MetricFact]:
    """Parse the GGL Marktmonitor HTML page and return MetricFact rows.

    Parameters
    ----------
    path:
        Path to the locally saved HTML file (UTF-8).
    source_url:
        Canonical URL for the page; stored verbatim in every MetricFact.

    Returns
    -------
    list[MetricFact]
        One fact per (vertical, quarter) found in Table I.  Returns [] if no
        suitable table is found (defensive — page layout may change).

    Notes
    -----
    * metric_name is "stakes" (Spieleinsätze / gross handle), NOT "ggr".
      Do not compare these figures directly with GGR-based facts from other
      jurisdictions without applying the appropriate margin ratio.
    * Only "Sportwetten online" (→ sports-wagering), "Virtuelle Automatenspiele"
      (→ igaming), and "Online-Poker" (→ online-poker) are extracted.
      Retail/stationär rows and lottery Table II are intentionally skipped.
    * FX: EUR millions → USD cents using fixed rate 1.08.
    """
    try:
        html = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        return []

    facts: list[MetricFact] = []
    for table in tables:
        table_facts = _parse_table(table, source_url)
        facts.extend(table_facts)
        # Stop after the first table that yielded results (Table I).
        # Table II (lotteries) has no rows matching our vertical map, but
        # we stop early to avoid parsing overhead on large pages.
        if table_facts:
            break

    return facts
