# Companion code for "The Backend of Luck" - Chapter 25b, Regulatory Reporting and Evidence Export.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""NJ DGE monthly revenue PDF parser (DGE-101 / DGE-105 / DGE-107)."""
from __future__ import annotations

import re
from pathlib import Path

import pdfplumber

from .metrics_model import MetricFact

_NUM_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def _to_cents(raw) -> int | None:
    """Parse '$ 1 2,304,711' / '(2,804)' / '-' / '$ -' to integer cents."""
    if raw is None:
        return None
    s = str(raw).strip()
    if s in ("", "-", "$", "$-", "$ -"):
        return 0
    s = s.replace("$", "").replace(" ", "")
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    s = s.replace(",", "")
    m = _NUM_RE.fullmatch(s)
    if not m:
        return None
    val = float(m.group(0))
    return int(round(val * 100)) * (-1 if neg else 1)


def _operator_from_page(page) -> str:
    txt = page.extract_text() or ""
    for line in txt.splitlines():
        line = line.strip()
        if line:
            return line
    return "UNKNOWN"


def _line_no(cell) -> str:
    return (cell or "").strip() if cell is not None else ""


def _is_skin_page(page) -> bool:
    txt = (page.extract_text() or "").upper()
    return "SKIN DETAIL" in txt


_MGR_LINES = {
    "1": "table_games_win", "2": "poker_win", "4": "slot_win",
    "5": "ggr",  # canonical: total casino win for the month
    "10": "ggr_ytd", "13": "simulcast_revenue", "14": "simulcast_handle",
}
_MGR_HANDLE_LINES = {"1": "table_games_drop", "4": "slot_handle"}

_IGR_MAIN_LINES = {
    "1": "peer_to_peer_win", "2": "other_games_win",
    # Line 3 is the licensee monthly iGaming total — DO NOT emit as "ggr"
    # because the per-brand "Skin Detail" page already emits ggr for each
    # brand and the brands sum to the licensee total (would double-count).
    "3": "licensee_total_ggr",
    "9": "tax_ytd", "11": "tax_paid", "12": "promo_credits_wagered",
}

_SWR_MAIN_LINES = {
    # Line 3 (retail GGR) IS canonical state GGR for the retail portion.
    # No per-brand skin row exists for retail, so emit as canonical "ggr".
    "3": "ggr", "7": "retail_sports_tax", "9": "retail_sports_tax_paid",
    # Line 16 (online GGR) is licensee-level total — per-brand skin pages
    # already emit ggr for each brand and brands sum to the licensee total
    # (would double-count). Keep distinct.
    "16": "licensee_total_online_sports_ggr",
    "19": "online_sports_tax", "21": "online_sports_tax_paid",
    "26": "tax_paid",
}


def _parse_mgr_page(page, period, source_url):
    out = []
    operator = _operator_from_page(page)
    for table in page.extract_tables() or []:
        for row in table:
            if not row or all((c is None or str(c).strip() == "") for c in row):
                continue
            ln = _line_no(row[0])
            if ln in _MGR_LINES and len(row) >= 4:
                cents = _to_cents(row[3])
                if cents is not None:
                    out.append(MetricFact(
                        state="NJ", operator=operator, vertical="commercial-casino",
                        period=period, metric_name=_MGR_LINES[ln],
                        value_usd_cents=cents, source_url=source_url))
            if ln in _MGR_HANDLE_LINES and len(row) >= 5:
                cents = _to_cents(row[4])
                if cents is not None:
                    out.append(MetricFact(
                        state="NJ", operator=operator, vertical="commercial-casino",
                        period=period, metric_name=_MGR_HANDLE_LINES[ln],
                        value_usd_cents=cents, source_url=source_url))
            if ln in _MGR_LINES and len(row) == 3:
                cents = _to_cents(row[2])
                if cents is not None:
                    out.append(MetricFact(
                        state="NJ", operator=operator, vertical="commercial-casino",
                        period=period, metric_name=_MGR_LINES[ln],
                        value_usd_cents=cents, source_url=source_url))
    return out


def _parse_igr_main_page(page, period, source_url):
    out = []
    licensee = _operator_from_page(page)
    for table in page.extract_tables() or []:
        for row in table:
            if not row:
                continue
            ln = _line_no(row[0])
            if ln in _IGR_MAIN_LINES and len(row) >= 3:
                cents = _to_cents(row[-1])
                if cents is not None:
                    out.append(MetricFact(
                        state="NJ", operator=licensee, vertical="igaming",
                        period=period, metric_name=_IGR_MAIN_LINES[ln],
                        value_usd_cents=cents, source_url=source_url))
    return out


def _parse_igr_skin_page(page, period, source_url):
    out = []
    for table in page.extract_tables() or []:
        if not table or len(table) < 2:
            continue
        header = table[0]
        brand_cols = []
        for idx, h in enumerate(header[2:-1], start=2):
            name = (h or "").replace("\n", " ").replace("Win", "").strip()
            if name and name.lower() != "total":
                brand_cols.append((idx, name))
        if not brand_cols:
            continue
        for row in table[1:]:
            ln = _line_no(row[0])
            if ln != "3":
                continue
            for idx, brand in brand_cols:
                if idx >= len(row):
                    continue
                cents = _to_cents(row[idx])
                if cents is None:
                    continue
                out.append(MetricFact(
                    state="NJ", operator=brand, vertical="igaming",
                    period=period, metric_name="ggr",
                    value_usd_cents=cents, source_url=source_url))
    return out


def _parse_swr_main_page(page, period, source_url):
    out = []
    licensee = _operator_from_page(page)
    for table in page.extract_tables() or []:
        for row in table:
            if not row:
                continue
            ln = _line_no(row[0])
            if ln in _SWR_MAIN_LINES and len(row) >= 3:
                cents = _to_cents(row[-1])
                if cents is None:
                    continue
                out.append(MetricFact(
                    state="NJ", operator=licensee, vertical="sports-wagering",
                    period=period, metric_name=_SWR_MAIN_LINES[ln],
                    value_usd_cents=cents, source_url=source_url))
    return out


def _parse_swr_skin_page(page, period, source_url):
    out = []
    for table in page.extract_tables() or []:
        if not table or len(table) < 2:
            continue
        header = table[0]
        brand_cols = []
        for idx, h in enumerate(header[2:-1], start=2):
            name = (h or "").replace("\n", " ").replace("Gross Revenue", "").strip()
            if name and name.lower() != "total":
                brand_cols.append((idx, name))
        for row in table[1:]:
            ln = _line_no(row[0])
            if ln != "16":
                continue
            for idx, brand in brand_cols:
                if idx >= len(row):
                    continue
                cents = _to_cents(row[idx])
                if cents is None:
                    continue
                out.append(MetricFact(
                    state="NJ", operator=brand, vertical="sports-wagering",
                    period=period, metric_name="ggr",
                    value_usd_cents=cents, source_url=source_url))
    return out


def parse_nj_pdf(path: Path, vertical: str, period: str, source_url: str) -> list[MetricFact]:
    """Extract per-operator $$$ from one NJ DGE monthly PDF."""
    v = vertical.lower().replace("-", "_")
    out: list[MetricFact] = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            if v in ("commercial_casino", "mgr", "casino"):
                out.extend(_parse_mgr_page(page, period, source_url))
            elif v in ("igaming", "igr", "internet"):
                if _is_skin_page(page):
                    out.extend(_parse_igr_skin_page(page, period, source_url))
                else:
                    out.extend(_parse_igr_main_page(page, period, source_url))
            elif v in ("sports", "swr", "sports_wagering"):
                if _is_skin_page(page):
                    out.extend(_parse_swr_skin_page(page, period, source_url))
                else:
                    out.extend(_parse_swr_main_page(page, period, source_url))
    return [f for f in out if f.operator and f.operator != "UNKNOWN"]
