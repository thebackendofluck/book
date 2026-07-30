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
New Jersey Division of Gaming Enforcement (NJ DGE).

The DGE moved everything to njoag.gov under
/about/divisions-and-offices/division-of-gaming-enforcement-home/financial-and-statistical-information/
with three vertical-specific archive subpages. PDF assets still live on the
old nj.gov/oag/ge/docs/Financials/... host with deterministic URL prefixes
(MGR{YYYY}/, IGRTaxReturns/{YYYY}/, SWRTaxReturns/{YYYY}/).

The vertical is encoded in the URL prefix; we don't rely on anchor text.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .base import StateCollector
from models import ReportFile

BASE = (
    "https://www.njoag.gov/about/divisions-and-offices/"
    "division-of-gaming-enforcement-home/financial-and-statistical-information/"
)

SOURCES = [
    ("commercial-casino", "monthly-gross-revenue-reports/",          "/MGR"),
    ("igaming",           "monthly-internet-gross-revenue-reports/", "/IGRTaxReturns/"),
    ("sports-wagering",   "monthly-sports-wagering-revenue-reports/", "/SWRTaxReturns/"),
]

PDF_RE = re.compile(
    r"/(MGR\d{4}|IGRTaxReturns/\d{4}|SWRTaxReturns/\d{4})/[A-Za-z]+\d{4}\.pdf$"
)


class NewJerseyCollector(StateCollector):
    state = "NJ"
    regulator = "NJ Division of Gaming Enforcement"
    source_url = BASE

    async def list_reports(self, client: httpx.AsyncClient) -> list[ReportFile]:
        out: list[ReportFile] = []
        seen: set[str] = set()
        for vertical, slug, marker in SOURCES:
            try:
                res = await client.get(BASE + slug)
                res.raise_for_status()
            except httpx.HTTPError:
                continue
            soup = BeautifulSoup(res.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if marker not in href or not href.lower().endswith(".pdf"):
                    continue
                if "amendment" in href.lower():
                    continue
                if not PDF_RE.search(href):
                    continue
                full = urljoin(BASE, href)
                if full in seen:
                    continue
                seen.add(full)
                out.append(ReportFile(
                    operator=f"NJ Statewide {vertical.replace('-', ' ').title()}",
                    vertical=vertical,
                    cadence="monthly",
                    format="pdf",
                    source_url=full,
                ))
                if len(out) >= 60:  # 36 months × 3 verticals roughly
                    break
        return out
