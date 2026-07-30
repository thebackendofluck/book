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
Indiana Gaming Commission monthly revenue (combined casino + sports).

IGC publishes one combined PDF/XLSX per month covering casino (riverboat /
racino tax) AND sports wagering (per-operator handle, AGR, tax).

in.gov rate-limits headless clients aggressively, so we don't scrape the
index page. Instead we enumerate (year, month) for the last 36 months and
HEAD-probe two URL conventions:

  Current (2024+):  /igc/files/reports/{YYYY}/{YYYY}-{MM}-Revenue.{pdf|xlsx}
  Legacy:           /igc/files/{YYYY}-{MM}-Revenue.{pdf|xlsx}
"""
from __future__ import annotations

from datetime import date

import httpx

from .base import StateCollector
from models import ReportFile

BASE = "https://www.in.gov"
UA_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _add_months(d: date, n: int) -> date:
    total = d.year * 12 + (d.month - 1) + n
    return date(total // 12, total % 12 + 1, 1)


class IndianaCollector(StateCollector):
    state = "IN"
    regulator = "Indiana Gaming Commission"
    source_url = BASE + "/igc/publications/monthly-revenue/"

    async def list_reports(self, client: httpx.AsyncClient) -> list[ReportFile]:
        out: list[ReportFile] = []
        seen: set[str] = set()
        today = date.today().replace(day=1)
        # Walk back 36 months; HEAD-probe both URL conventions per (year,month).
        for i in range(0, 36):
            m = _add_months(today, -i)
            ym = f"{m.year}-{m.month:02d}"
            for ext in ("xlsx", "pdf"):
                for path in (
                    f"/igc/files/reports/{m.year}/{ym}-Revenue.{ext}",
                    f"/igc/files/{ym}-Revenue.{ext}",
                ):
                    url = BASE + path
                    if url in seen:
                        continue
                    try:
                        r = await client.head(url, headers=UA_HEADERS,
                                              follow_redirects=True, timeout=15)
                    except httpx.HTTPError:
                        continue
                    if r.status_code != 200:
                        continue
                    seen.add(url)
                    # IN sports started Sep 2019; emit both verticals from then.
                    verticals = ["commercial-casino"]
                    if (m.year, m.month) >= (2019, 9):
                        verticals.append("sports-wagering")
                    for v in verticals:
                        out.append(ReportFile(
                            operator="IN All Operators",
                            vertical=v,
                            cadence="monthly",
                            format=ext,
                            source_url=url,
                        ))
                    break  # found ext for this month; don't double-fetch
        return out
