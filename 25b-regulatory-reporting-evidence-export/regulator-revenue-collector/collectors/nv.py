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
Nevada Gaming Control Board monthly + quarterly revenue reports.

NV publishes a single PDF per month (Monthly Gaming Revenue Report / GRI)
that breaks out slots, table games, race, and sports-pool win/handle.
Filenames are wildly inconsistent across years — never construct URLs,
always scrape the index.

Sports wagering has NO separate report — it's a section inside the GRI.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .base import StateCollector
from models import ReportFile

GRI_INDEX = "https://gaming.nv.gov/about/gaming-revenue/information/"
QSR_INDEX = "https://gaming.nv.gov/about/quart-statistical/information/"
UA_HEADERS = {"User-Agent": "Mozilla/5.0 RegulatorRevenueCollector/1.0"}


class NevadaCollector(StateCollector):
    state = "NV"
    regulator = "Nevada Gaming Control Board"
    source_url = "https://gaming.nv.gov/about-us/statistics-and-publications/"

    async def list_reports(self, client: httpx.AsyncClient) -> list[ReportFile]:
        out: list[ReportFile] = []
        seen: set[str] = set()
        for url, cadence in ((GRI_INDEX, "monthly"), (QSR_INDEX, "quarterly")):
            try:
                r = await client.get(url, headers=UA_HEADERS, follow_redirects=True)
                r.raise_for_status()
            except httpx.HTTPError:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if not href.lower().endswith(".pdf"):
                    continue
                full = urljoin(url, href)
                if full in seen:
                    continue
                seen.add(full)
                out.append(ReportFile(
                    operator="NV Statewide",
                    # Note: NV's GRI bundles casino + sports in one PDF; we tag
                    # as commercial-casino because that's the dominant vertical.
                    # A later parser can emit both verticals per PDF.
                    vertical="commercial-casino",
                    cadence=cadence,
                    format="pdf",
                    source_url=full,
                ))
        return out
