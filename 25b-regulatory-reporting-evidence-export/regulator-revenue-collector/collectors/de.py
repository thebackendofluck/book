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
Delaware Lottery iGaming + sports wagering monthly revenue.

DE publishes monthly proceeds as HTML tables on per-fiscal-year pages
(no downloadable PDF/XLSX). One operator per row × month per column.

  iGaming FY page: /More/iGaming/Revenue-Distribution/Monthly-Proceeds-And-Distribution-Financial-Year/{FY}
  Sports  FY page: /Sports-Lottery/Sportsbooks/Monthly-Proceeds-And-Distribution-Financial-Year/{FY}

Operators: Delaware Park, Bally's Dover (formerly Dover Downs), Harrington Raceway.
DE FY runs Jul→Jun; FY label = ending year.
"""
from __future__ import annotations

from datetime import date

import httpx

from .base import StateCollector
from models import ReportFile

BASE = "https://www.delottery.com"
OPERATORS = ("Delaware Park", "Bally's Dover", "Harrington Raceway")

IGAMING_TPL = (BASE + "/More/iGaming/Revenue-Distribution/"
               "Monthly-Proceeds-And-Distribution-Financial-Year/{fy}")
SPORTS_TPL  = (BASE + "/Sports-Lottery/Sportsbooks/"
               "Monthly-Proceeds-And-Distribution-Financial-Year/{fy}")


def _current_fy() -> int:
    """DE FY runs Jul 1 → Jun 30; FY label = ending year."""
    today = date.today()
    return today.year + 1 if today.month >= 7 else today.year


class DelawareCollector(StateCollector):
    state = "DE"
    regulator = "Delaware Lottery"
    source_url = BASE + "/Sports-Lottery/Monthly-Net-Proceeds"

    async def list_reports(self, client: httpx.AsyncClient) -> list[ReportFile]:
        out: list[ReportFile] = []
        for fy in range(2014, _current_fy() + 1):
            for vertical, tpl in (("igaming", IGAMING_TPL), ("sports-wagering", SPORTS_TPL)):
                url = tpl.format(fy=fy)
                try:
                    head = await client.get(url, follow_redirects=True)
                    if head.status_code != 200:
                        continue
                except httpx.HTTPError:
                    continue
                for op in OPERATORS:
                    out.append(ReportFile(
                        operator=op,
                        vertical=vertical,
                        cadence="monthly",
                        format="html",
                        source_url=url,
                    ))
        return out
