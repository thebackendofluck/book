# Companion code for "The Backend of Luck" - Chapter 25b, Regulatory Reporting and Evidence Export.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""MA Gaming Commission monthly sports wagering PDF parser.

Each operator's PDF lists a month-by-month table since launch (Feb 2023).
We filter to only the period derived from the filename so re-running on
the same PDF doesn't double-emit rows.

Column layout (Cat. 1 retail and Cat. 3 mobile are identical):
  0 Month label
  1 Monthly Ticket Write           = handle (amount wagered)
  3 Monthly Win (Accrual Basis)    = casino gross win
  6 Taxable AGSWR (Win - Excise)   = ggr (canonical)
  7 Tax Collected (15% / 20%)      = tax_paid
patron_winnings is derived = handle - casino_win.
"""
from __future__ import annotations

import re
from pathlib import Path

import pdfplumber

from .metrics_model import MetricFact

_MONTHS = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
}


def _period_from_url(url: str) -> str | None:
    m = re.search(r"-([A-Za-z]+)-(\d{4})\.pdf", url)
    if not m:
        return None
    return f"{m.group(2)}-{_MONTHS.get(m.group(1).lower(), '01')}"


def _parse_dollars(raw) -> float | None:
    if not raw:
        return None
    s = re.sub(r"[^\d.\-]", "", str(raw).replace(",", ""))
    try:
        return float(s)
    except ValueError:
        return None


def _to_cents(d: float) -> int:
    return int(round(d * 100))


_COL_MONTH, _COL_HANDLE, _COL_WIN, _COL_AGSWR, _COL_TAX = 0, 1, 3, 6, 7
_MONTH_RE = re.compile(
    r"^(January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+(\d{4})", re.I,
)


def parse_ma_pdf(path: Path, operator: str, vertical: str, source_url: str) -> list[MetricFact]:
    period = _period_from_url(source_url)
    if period is None:
        return []
    facts: list[MetricFact] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                for row in table:
                    if not row or not row[_COL_MONTH]:
                        continue
                    m = _MONTH_RE.match(str(row[_COL_MONTH]).strip())
                    if not m:
                        continue
                    row_period = f"{m.group(2)}-{_MONTHS[m.group(1).lower()]}"
                    if row_period != period:
                        continue

                    def cell(idx):
                        return row[idx] if len(row) > idx else None

                    handle = _parse_dollars(cell(_COL_HANDLE))
                    win = _parse_dollars(cell(_COL_WIN))
                    agswr = _parse_dollars(cell(_COL_AGSWR))
                    tax = _parse_dollars(cell(_COL_TAX))

                    base = dict(state="MA", operator=operator,
                                vertical=vertical,
                                period=period, source_url=source_url)
                    if handle is not None:
                        facts.append(MetricFact(
                            **base, metric_name="handle",
                            value_usd_cents=_to_cents(handle)))
                    if handle is not None and win is not None:
                        facts.append(MetricFact(
                            **base, metric_name="patron_winnings",
                            value_usd_cents=_to_cents(handle - win)))
                    if agswr is not None:
                        facts.append(MetricFact(
                            **base, metric_name="ggr",
                            value_usd_cents=_to_cents(agswr)))
                    if tax is not None:
                        facts.append(MetricFact(
                            **base, metric_name="tax_paid",
                            value_usd_cents=_to_cents(tax)))
    return facts
