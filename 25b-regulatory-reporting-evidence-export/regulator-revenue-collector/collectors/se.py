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
Sweden Spelinspektionen quarterly market statistics.

SE publishes a single consolidated quarterly XLSX (overwritten each release)
plus per-year annual PDFs. Verticals are sheets/columns inside the XLSX.
"""
from __future__ import annotations

from datetime import date

import httpx

from .base import StateCollector
from models import ReportFile

BASE = "https://www.spelinspektionen.se/globalassets/dokument/statistik"
QUARTERLY_XLSX = f"{BASE}/omsattning-spelmarknaden-per-kvartal.xlsx"
ANNUAL_PDF = BASE + "/spelmarknaden-{year}.pdf"
VERTICALS = ("igaming", "sports-wagering", "lottery", "land-based-casino")
UA_HEADERS = {"User-Agent": "Mozilla/5.0 RegulatorRevenueCollector/1.0"}


class SwedenCollector(StateCollector):
    state = "SE"
    regulator = "Spelinspektionen (Swedish Gambling Authority)"
    source_url = "https://www.spelinspektionen.se/om-oss/statistik/"

    async def list_reports(self, client: httpx.AsyncClient) -> list[ReportFile]:
        out: list[ReportFile] = []
        # Consolidated quarterly XLSX — emit one ReportFile per vertical.
        try:
            head = await client.head(QUARTERLY_XLSX, headers=UA_HEADERS,
                                     follow_redirects=True)
            if head.status_code == 200:
                for v in VERTICALS:
                    out.append(ReportFile(
                        operator="SE Statewide",
                        vertical=v,
                        cadence="quarterly",
                        format="xlsx",
                        source_url=QUARTERLY_XLSX,
                    ))
        except httpx.HTTPError:
            pass

        # Annual narrative PDFs — one per year, probe last ~7 years.
        for year in range(date.today().year, 2018, -1):
            url = ANNUAL_PDF.format(year=year)
            try:
                r = await client.head(url, headers=UA_HEADERS, follow_redirects=True)
                if r.status_code != 200:
                    continue
            except httpx.HTTPError:
                continue
            for v in VERTICALS:
                out.append(ReportFile(
                    operator="SE Statewide",
                    vertical=v,
                    cadence="annual",
                    format="pdf",
                    source_url=url,
                ))
        return out
