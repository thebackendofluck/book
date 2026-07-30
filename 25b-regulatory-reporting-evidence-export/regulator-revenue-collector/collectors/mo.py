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
Missouri Gaming Commission monthly revenue (riverboat casino + sports wagering).

MGC publishes two parallel hierarchies under www.mgc.dps.mo.gov:

  Casino (riverboat AGR + admissions):
    /Casino_Gaming/rb_financials/FY{YY}_FinReport/{MM}_{Mon}/WEB{MMYY}.xlsx
    The "WEB" workbook is the cleanest cross-property roll-up: one tab
    (MONTHLY STATS) lists every Missouri boat with monthly AGR + tax for
    the entire fiscal year-to-date. Earlier FYs (pre-FY25) drop the
    {MM}_{Mon}/ subdirectory and live directly under the FY folder.

  Sports wagering (live since Dec 2025, MO Amendment 2):
    /SportsWagering/sw_financials/FY{YY}_SWFinReport/{MM}_{Mon}/
        SW Monthly Financials {MMYY}.xlsx
    The "SW Monthly Financials" workbook breaks down each licensee's
    handle, taxable AGR, and 10 % tax across retail and mobile.

Reports are HEAD-probed across the last 3 fiscal years to avoid scraping
the index page (which sometimes 403s headless clients but always serves
direct file URLs to a Mozilla UA).
"""
from __future__ import annotations

from datetime import date

import httpx

from .base import StateCollector
from models import ReportFile

BASE = "https://www.mgc.dps.mo.gov"
CASINO_ROOT = f"{BASE}/Casino_Gaming/rb_financials"
SW_ROOT = f"{BASE}/SportsWagering/sw_financials"

_MONTH_DIRS = {
    1: "01_Jan", 2: "02_Feb", 3: "03_Mar", 4: "04_Apr",
    5: "05_May", 6: "06_Jun", 7: "07_Jul", 8: "08_Aug",
    9: "09_Sep", 10: "10_Oct", 11: "11_Nov", 12: "12_Dec",
}

UA_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,*/*",
}


def _fy_for(d: date) -> int:
    """Return Missouri fiscal year (FY26 = Jul 2025 - Jun 2026)."""
    return d.year + 1 if d.month >= 7 else d.year


def _add_months(d: date, n: int) -> date:
    total = d.year * 12 + (d.month - 1) + n
    return date(total // 12, total % 12 + 1, 1)


class MissouriCollector(StateCollector):
    state = "MO"
    regulator = "Missouri Gaming Commission"
    source_url = f"{BASE}/Casino_Gaming/rb_financials/rb_Fin_main.html"

    async def list_reports(self, client: httpx.AsyncClient) -> list[ReportFile]:
        out: list[ReportFile] = []
        seen: set[str] = set()
        today = date.today().replace(day=1)
        # Walk back ~36 months. MGC names both the directory and the file
        # after the *data* month — e.g. Feb-2026 AGR lives at
        # FY26_FinReport/02_Feb/WEB0226.xlsx (data month = directory month
        # = filename suffix). The fiscal year is just the data month's FY.
        for i in range(0, 36):
            data_month = _add_months(today, -i)
            mmyy = f"{data_month.month:02d}{data_month.year % 100:02d}"
            fy = _fy_for(data_month)
            month_dir = _MONTH_DIRS[data_month.month]

            # --- Casino: WEB{MMYY}.xlsx and .pdf ---
            for ext in ("xlsx", "xls", "pdf"):
                url = (f"{CASINO_ROOT}/FY{fy % 100}_FinReport/"
                       f"{month_dir}/WEB{mmyy}.{ext}")
                if url in seen:
                    continue
                if await self._head_ok(client, url):
                    seen.add(url)
                    out.append(ReportFile(
                        operator="MO All Casinos",
                        vertical="commercial-casino",
                        cadence="monthly",
                        format=ext,
                        source_url=url,
                    ))
                    break  # one format per month is enough

            # --- Sports wagering: SW Monthly Financials {MMYY}.xlsx ---
            # Live since launch (Dec 2025). Quietly 404s before that.
            for ext in ("xlsx", "xls", "pdf"):
                url = (f"{SW_ROOT}/FY{fy % 100}_SWFinReport/"
                       f"{month_dir}/SW Monthly Financials {mmyy}.{ext}")
                if url in seen:
                    continue
                if await self._head_ok(client, url):
                    seen.add(url)
                    out.append(ReportFile(
                        operator="MO All Operators",
                        vertical="sports-wagering",
                        cadence="monthly",
                        format=ext,
                        source_url=url,
                    ))
                    break

        return out

    async def _head_ok(self, client: httpx.AsyncClient, url: str) -> bool:
        """HEAD-probe a candidate URL.

        MGC's IIS server 302-redirects missing files to a styled HTML
        ``error.html`` page that is also served with status 200. Following
        redirects makes every URL look "found", so we explicitly disable
        redirects here and require the *direct* response to be both 200
        AND advertise an Office content-type. Real files Last-Modified by
        IIS always come back with content-type
        ``application/vnd.openxmlformats-officedocument.spreadsheetml.sheet``
        (or .ms-excel for the few legacy .xls survivors).
        """
        try:
            r = await client.head(url, headers=UA_HEADERS,
                                  follow_redirects=False, timeout=15)
        except httpx.HTTPError:
            return False
        if r.status_code != 200:
            return False
        ct = r.headers.get("content-type", "").lower()
        return any(token in ct for token in (
            "spreadsheetml",   # .xlsx
            "ms-excel",        # .xls
            "application/pdf", # .pdf
        ))
