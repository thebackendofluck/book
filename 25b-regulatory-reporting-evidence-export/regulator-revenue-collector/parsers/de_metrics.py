# Companion code for "The Backend of Luck" - Chapter 25b, Regulatory Reporting and Evidence Export.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Delaware Lottery iGaming / sports-wagering monthly revenue HTML parser.

The Delaware Lottery publishes per-fiscal-year HTML tables at:
  iGaming: /More/iGaming/Revenue-Distribution/
               Monthly-Proceeds-And-Distribution-Financial-Year/{FY}
  Sports:  /Sports-Lottery/Sportsbooks/
               Monthly-Proceeds-And-Distribution-Financial-Year/{FY}

Table layout (9 TD columns, odd columns are spacers):
  col 0: month label (e.g. "JULY-24") — present only on the first row of
         each month block; subsequent rows have an empty td[0]
  col 1: metric label (e.g. "NET GAMING REVENUE", "TOTAL NET GAMING REVENUE")
  col 2: Delaware Park value
  col 4: Bally's Dover value
  col 6: Harrington value
  col 8: Total (ignored — we emit per-operator facts)

We extract only the "TOTAL NET GAMING REVENUE" row per month as metric_name='ggr'.
Rows beginning with "TOTAL FY" are annual summaries and are skipped.

DE FY runs Jul → Jun; FY label = ending year (e.g. FY 2025 = Jul 2024 – Jun 2025).
Month labels: "JULY-24", "AUGUST-24", … "June-25" (mixed case in later months).
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from bs4 import BeautifulSoup

from .metrics_model import MetricFact

# DE iGaming operators in the order they appear in the HTML columns
_OPERATORS = [
    ("Delaware Park",   2),   # td column index (0-based)
    ("Bally's Dover",   4),
    ("Harrington",      6),
]

# Month abbreviation → two-digit number
_MONTH_MAP: dict[str, str] = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
}

# Matches "JULY-24", "March-25", "JANUARY-24", etc.
_MONTH_RE = re.compile(
    r"^(january|february|march|april|may|june|july|august|"
    r"september|october|november|december)-(\d{2})$",
    re.IGNORECASE,
)


def _parse_month_label(label: str) -> str | None:
    """Convert "JULY-24" → "2024-07", "March-25" → "2025-03".

    DE FY ends in June; the two-digit year on the label is the calendar year
    in which that month falls (e.g. JULY-24 = July 2024, JUNE-25 = June 2025).
    """
    m = _MONTH_RE.match(label.strip())
    if not m:
        return None
    month_name = m.group(1).lower()
    yy = int(m.group(2))
    # Two-digit year: 00-99 → 2000-2099 (DE iGaming launched ~2013)
    year = 2000 + yy
    return f"{year:04d}-{_MONTH_MAP[month_name]}"


def _parse_dollars(raw: str) -> int | None:
    """Convert "$1,031,557" → 103155700 (cents).  Returns None if not parseable."""
    if not raw:
        return None
    s = raw.strip().lstrip("$").replace(",", "")
    if not s:
        return None
    try:
        return int((Decimal(s) * 100).to_integral_value())
    except (InvalidOperation, ValueError):
        return None


def parse_de_html(
    path: Path,
    vertical: str,
    source_url: str = "",
) -> list[MetricFact]:
    """Parse one Delaware Lottery FY HTML file and return MetricFacts.

    Emits metric_name='ggr' for "TOTAL NET GAMING REVENUE" per operator per month.
    The ``vertical`` argument should be 'igaming' or 'sports-wagering'.
    """
    try:
        html = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        return []

    facts: list[MetricFact] = []
    current_period: str | None = None

    for row in table.find_all("tr"):
        cells = [td.get_text(strip=True) for td in row.find_all("td")]
        if not cells:
            continue

        # Skip the header row
        if cells[0] == "MONTH ENDING":
            continue

        # Skip annual-total rows ("TOTAL FY 2025" etc.)
        if cells[0].upper().startswith("TOTAL FY") or cells[0].upper().startswith("TOTAL FY"):
            continue

        # If td[0] is non-empty and matches a month pattern, update current period
        if cells[0]:
            parsed = _parse_month_label(cells[0])
            if parsed:
                current_period = parsed
            # If it doesn't parse as a month (e.g. a TOTAL FY row we missed above),
            # reset period so we don't emit stale data
            elif cells[0].upper().startswith("TOTAL"):
                current_period = None

        if current_period is None:
            continue

        # We only care about TOTAL NET GAMING REVENUE rows
        metric_label = cells[1].upper().strip() if len(cells) > 1 else ""
        if metric_label != "TOTAL NET GAMING REVENUE":
            continue

        # Emit one fact per operator
        for op_name, col_idx in _OPERATORS:
            if col_idx >= len(cells):
                continue
            cents = _parse_dollars(cells[col_idx])
            if cents is None:
                continue
            facts.append(MetricFact(
                state="DE",
                operator=op_name,
                vertical=vertical,
                period=current_period,
                metric_name="ggr",
                value_usd_cents=cents,
                source_url=source_url,
            ))

    return facts


def parse_de_html_from_bytes(
    content: bytes,
    vertical: str,
    source_url: str = "",
) -> Iterable[MetricFact]:
    """Parse Delaware Lottery HTML from raw bytes (e.g. from an HTTP response).

    Convenience wrapper used by the bespoke backfill function which downloads
    each FY page once and passes the body directly without writing to disk.
    """
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    try:
        yield from parse_de_html(tmp_path, vertical, source_url)
    finally:
        tmp_path.unlink(missing_ok=True)
