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
Ontario iGaming Ontario (iGO) market performance reports.

iGO publishes monthly performance data tables (XLSX).  Each month's file
lives directly under /sites/default/files/documents/ with the naming
convention:

    iGO Monthly Market Performance Data Tables - {YYYY} {Month}.xlsx

The monthly hub page (/en/operator/market-performance-report-monthly)
only links to the *current* month's file.  Earlier months are not linked
from any index or release page — they must be constructed from the known
URL pattern and verified with a HEAD request.

Historical coverage: November 2024 is the earliest confirmed file.
The format was introduced in January 2025 (announcement) with data going
back to November 2024.
"""
from __future__ import annotations

import calendar
import re
import urllib.parse
from datetime import date

import httpx

from .base import StateCollector
from models import ReportFile

MONTHLY_HUB = "https://igamingontario.ca/en/operator/market-performance-report-monthly"
BASE_URL = "https://igamingontario.ca"
DOCS_BASE = f"{BASE_URL}/sites/default/files/documents/"

# Pattern matching the current-month direct XLSX link on the hub page
ASSET_RE = re.compile(
    r'href="((?:https://igamingontario\.ca)?/sites/default/files/documents/'
    r'iGO%20Monthly[^"]+\.xlsx)"',
    re.I,
)

UA_HEADERS = {"User-Agent": "Mozilla/5.0 RegulatorRevenueCollector/1.0"}

# Earliest month known to have a file (November 2024)
_HIST_START = date(2024, 11, 1)


def _xlsx_url(year: int, month: int) -> str:
    """Construct the canonical XLSX URL for a given year/month."""
    month_name = calendar.month_name[month]
    filename = f"iGO Monthly Market Performance Data Tables - {year} {month_name}.xlsx"
    return DOCS_BASE + urllib.parse.quote(filename)


def _month_range(start: date, end: date) -> list[tuple[int, int]]:
    """Yield (year, month) tuples from start up to and including end."""
    pairs: list[tuple[int, int]] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        pairs.append((y, m))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return pairs


class OntarioCollector(StateCollector):
    state = "ON"
    regulator = "iGaming Ontario / AGCO"
    source_url = MONTHLY_HUB

    async def list_reports(self, client: httpx.AsyncClient) -> list[ReportFile]:
        out: list[ReportFile] = []
        seen: set[str] = set()

        # ── Step 1: scrape hub page for the current month's direct XLSX link ──
        try:
            # iGaming Ontario's TLS cert periodically lapses; self.fetch tries a
            # verified connection first and only falls back to unverified on an
            # actual certificate error (logged), so discovery keeps working.
            r = await self.fetch(
                client, "GET", MONTHLY_HUB,
                headers=UA_HEADERS, follow_redirects=True, timeout=30,
            )
            r.raise_for_status()
            for raw_href in ASSET_RE.findall(r.text):
                url = raw_href if raw_href.startswith("http") else BASE_URL + raw_href
                if url not in seen:
                    seen.add(url)
                    out.append(
                        ReportFile(
                            operator="ON Statewide",
                            vertical="igaming",
                            cadence="monthly",
                            format="xlsx",
                            source_url=url,
                        )
                    )
        except httpx.HTTPError:
            pass

        # ── Step 2: enumerate historical months via constructed URLs ──
        today = date.today()
        # Go back to _HIST_START; stop at last complete month
        end_month = date(today.year, today.month, 1)
        for year, month in _month_range(_HIST_START, end_month):
            url = _xlsx_url(year, month)
            if url in seen:
                continue
            try:
                head = await self.fetch(
                    client, "HEAD", url,
                    headers=UA_HEADERS, follow_redirects=True, timeout=15,
                )
                if head.status_code == 200:
                    seen.add(url)
                    out.append(
                        ReportFile(
                            operator="ON Statewide",
                            vertical="igaming",
                            cadence="monthly",
                            format="xlsx",
                            source_url=url,
                        )
                    )
            except httpx.HTTPError:
                pass

        return out
