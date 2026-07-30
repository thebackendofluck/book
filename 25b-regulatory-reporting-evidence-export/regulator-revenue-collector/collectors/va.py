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
Virginia Lottery sports-wagering reports.

VA does not publish per-operator monthly files. The closest machine-extractable
artefacts are the quarterly "Gaming Update — Board Meeting" PDFs linked from
the casinos-and-sports-betting news page. Filenames are not date-templatable;
we scrape and keep anything that looks like a gaming/sports-betting update.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .base import StateCollector
from models import ReportFile

NEWS_URL = "https://www.valottery.com/aboutus/casinosandsportsbetting/newsandinformation"
PDF_RE = re.compile(r"\.pdf(\?|$)", re.I)
UA_HEADERS = {"User-Agent": "Mozilla/5.0 RegulatorRevenueCollector/1.0"}


class VirginiaCollector(StateCollector):
    state = "VA"
    regulator = "Virginia Lottery"
    source_url = NEWS_URL

    async def list_reports(self, client: httpx.AsyncClient) -> list[ReportFile]:
        try:
            r = await client.get(NEWS_URL, headers=UA_HEADERS, follow_redirects=True)
            r.raise_for_status()
        except httpx.HTTPError:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        out: list[ReportFile] = []
        seen: set[str] = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not PDF_RE.search(href):
                continue
            text = (a.get_text(" ", strip=True) or "").lower()
            if "gaming" not in text and "sports" not in text:
                continue
            full = urljoin(NEWS_URL, href)
            if full in seen:
                continue
            seen.add(full)
            out.append(ReportFile(
                operator="VA All Operators",
                vertical="sports-wagering",
                cadence="quarterly",   # board decks aggregate multiple months
                format="pdf",
                source_url=full,
            ))
        return out
