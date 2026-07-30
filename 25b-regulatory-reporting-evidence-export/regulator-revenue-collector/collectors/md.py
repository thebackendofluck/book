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
Maryland Lottery & Gaming Control Agency monthly revenue.

MD publishes two kinds of monthly XLSXs:
  - Sports wagering: .../wp-content/uploads/YYYY/MM/{Month}-{YYYY}-Sports-Wagering-Data.xlsx
  - Casino:          .../wp-content/uploads/YYYY/MM/{Month}-{YYYY}-Casino-Revenues.xlsx

Both contain ALL operators as columns (or rows for the 6 casinos). We scrape
the release index, follow each per-month press-release page, and pull the XLSX
links out of the release body.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .base import StateCollector
from models import ReportFile

INDEX_URLS = [
    ("sports-wagering", "https://www.mdgaming.com/maryland-sports-wagering/revenue-reports/all-financial-reports/"),
    ("sports-wagering", "https://www.mdgaming.com/maryland-sports-wagering/revenue-reports/"),
    ("commercial-casino", "https://www.mdgaming.com/marylands-casinos/revenue-reports/"),
]

FILE_RE = re.compile(
    r"https://www\.mdgaming\.com/wp-content/uploads/\d{4}/\d{2}/"
    r"[A-Za-z]+-\d{4}-(?:Sports-Wagering-Data|Sports-Wagering-Revenue|Casino-Revenues)\.xlsx",
    re.I,
)
RELEASE_RE = re.compile(r"/[a-z0-9\-]+-(?:19|20|21|22|23|24|25|26|27|28)\d?/?$", re.I)
UA_HEADERS = {"User-Agent": "Mozilla/5.0 RegulatorRevenueCollector/1.0"}


class MarylandCollector(StateCollector):
    state = "MD"
    regulator = "Maryland Lottery & Gaming Control Agency"
    source_url = "https://www.mdgaming.com/"

    async def list_reports(self, client: httpx.AsyncClient) -> list[ReportFile]:
        out: list[ReportFile] = []
        seen: set[str] = set()
        for vertical, idx_url in INDEX_URLS:
            try:
                idx = await client.get(idx_url, headers=UA_HEADERS, follow_redirects=True)
                if idx.status_code != 200:
                    continue
            except httpx.HTTPError:
                continue
            # First try: files linked directly on the index.
            for match in FILE_RE.findall(idx.text):
                if match in seen:
                    continue
                seen.add(match)
                out.append(ReportFile(
                    operator="MD All Operators",
                    vertical=vertical,
                    cadence="monthly",
                    format="xlsx",
                    source_url=match,
                ))
            # Second try: per-release pages linked from the index.
            soup = BeautifulSoup(idx.text, "html.parser")
            release_urls: set[str] = set()
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "mdgaming.com" not in href and not href.startswith("/"):
                    continue
                if RELEASE_RE.search(href):
                    release_urls.add(urljoin(idx_url, href))
            for rel_url in list(release_urls)[:24]:
                try:
                    rel = await client.get(rel_url, headers=UA_HEADERS, follow_redirects=True)
                    if rel.status_code != 200:
                        continue
                except httpx.HTTPError:
                    continue
                for match in FILE_RE.findall(rel.text):
                    if match in seen:
                        continue
                    seen.add(match)
                    out.append(ReportFile(
                        operator="MD All Operators",
                        vertical=vertical,
                        cadence="monthly",
                        format="xlsx",
                        source_url=match,
                    ))
        return out
