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
Denmark Spillemyndigheden monthly market data.

DK publishes a single combined XLSX per release in /uploads/{YYYY}-{MM}/
(publication month folder). The XLSX has separate sheets per vertical:
Online Casino, Væddemål (Betting), Landbaserede kasinoer, Slot arcades.
BSI = bruttospilleindtægt = GGR.
"""
from __future__ import annotations

import re

import httpx

from .base import StateCollector
from models import ReportFile

LANDING_EN = "https://www.spillemyndigheden.dk/en/statistik/statistik-det-danske-spilmarked"
XLSX_RE = re.compile(
    r'href="(https://www\.spillemyndigheden\.dk/uploads/'
    r'(\d{4})-(\d{2})/Data%20for%20the%20monthly%20statistics\.xlsx)"'
)
VERTICALS = ("igaming", "sports-wagering", "land-based-casino", "slot-arcades")
UA_HEADERS = {"User-Agent": "Mozilla/5.0 RegulatorRevenueCollector/1.0"}


class DenmarkCollector(StateCollector):
    state = "DK"
    regulator = "Spillemyndigheden (Danish Gambling Authority)"
    source_url = LANDING_EN

    async def list_reports(self, client: httpx.AsyncClient) -> list[ReportFile]:
        try:
            r = await client.get(LANDING_EN, headers=UA_HEADERS,
                                 follow_redirects=True, timeout=30.0)
            r.raise_for_status()
        except httpx.HTTPError:
            return []
        m = XLSX_RE.search(r.text)
        if not m:
            return []
        url = m.group(1)
        return [
            ReportFile(
                operator="DK Statewide",
                vertical=v,
                cadence="monthly",
                format="xlsx",
                source_url=url,
            )
            for v in VERTICALS
        ]
