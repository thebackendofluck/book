# Companion code for "The Backend of Luck" - Chapter 25b, Regulatory Reporting and Evidence Export.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""CT Socrata revenue parser. Fields verified against live API 2026-04-22."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable

from .metrics_model import MetricFact

# CT field -> canonical metric_name. total_gross_gaming = GGR after promo deduction.
_FIELD_MAP: dict[str, str] = {
    "wagers":              "handle",
    "patron_winnings":     "patron_winnings",
    "total_gross_gaming":  "ggr",
    "tax_payment_5":       "tax_paid",   # online casino dataset (imqd-at3c)
    "tax_payment_6":       "tax_paid",   # sports wagering dataset (xf6g-659c)
}


def _to_cents(raw) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int((Decimal(str(raw)) * 100).to_integral_value())
    except (InvalidOperation, ValueError):
        return None


def _to_period(month_ending) -> str | None:
    if not month_ending:
        return None
    try:
        s = str(month_ending).replace("Z", "")
        return datetime.fromisoformat(s).strftime("%Y-%m")
    except ValueError:
        return None


def parse_ct_dataset(rows: list[dict], vertical: str, source_url: str = "") -> Iterable[MetricFact]:
    """Yield one MetricFact per (licensee, month, metric)."""
    for row in rows:
        operator = (row.get("licensee") or "").strip()
        period = _to_period(row.get("month_ending"))
        if not operator or not period:
            continue
        for src_field, metric_name in _FIELD_MAP.items():
            if src_field not in row:
                continue
            cents = _to_cents(row[src_field])
            if cents is None:
                continue
            yield MetricFact(
                state="CT",
                operator=operator,
                vertical=vertical,
                period=period,
                metric_name=metric_name,
                value_usd_cents=cents,
                source_url=source_url,
            )
