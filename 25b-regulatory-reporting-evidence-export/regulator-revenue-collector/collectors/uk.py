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
UK Gambling Commission quarterly Industry Statistics.

UKGC publishes one combined XLSX per quarter (Remote Casino, Remote Betting,
Bingo, Arcades, National Lottery aggregates). Files live on Contentful CDN
with opaque hash URLs — must scrape the publication index + per-quarter
detail page to discover them.

Mapping:
  Remote Casino             → igaming
  Remote Betting            → sports-wagering
  Non-remote Betting        → retail-betting
  Bingo / Arcades / Lottery → bingo / arcades / lottery
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .base import StateCollector
from models import ReportFile

UKGC_BASE = "https://www.gamblingcommission.gov.uk"
# UKGC restructured statistics-and-research navigation (~mid-2026): the old
# publication-slug index URL below now 302s to a generic hub page
# (/about-us/statistics-and-research) that no longer lists individual
# Industry Statistics releases, so scraping it for PUB_RE matches silently
# returns zero results. The series listing page is the current source of
# truth for quarterly/annual release links.
INDEX_URL = UKGC_BASE + "/statistics-and-research/the-gambling-market/series/industry-statistics"

PUB_RE = re.compile(
    r"/statistics-and-research/publication/industry-statistics-quarterly-report-"
    r"financial-year-april-(\d{4})-to-march-\d{4}-(?:q|quarter-)(\d)",
    re.IGNORECASE,
)
XLSX_RE = re.compile(r"https?://assets\.ctfassets\.net/[^\"'\s]+\.xlsx", re.I)
UA_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}

VERTICALS = ["sports-wagering", "igaming", "national-lottery", "retail-betting"]

# Direct download URLs that bypass scraping (UKGC site sometimes hides them).
# Add new asset URLs here as they're published.
_HARDCODED_URLS = [
    # FY 2024-25 Annual report (covers Apr 2024 → Mar 2025) — full year, all sectors
    ("https://assets.ctfassets.net/j16ev64qyf6l/5SoOf5hNy5CDQnKVcb0j61/"
     "de9d6828dea7ff2c77080754c0ee6f08/"
     "Industry_Statistics_%C3%A2___Annual_report_%C3%A2___"
     "Financial_year_April_2024_to_March_2025__1_.xlsx",
     "annual"),
    # FY 2025-26 Q2 quarterly report (Jul-Sep 2025)
    ("https://assets.ctfassets.net/j16ev64qyf6l/4UCJ50E0Ylj3E2HldP0Y04/"
     "70027aaadbdbd7e4e9bb2252af2fd3fe/"
     "Industry_Statistics_-_Quarterly_report_-_Financial_year_April_2025_to_March_2026"
     "_-_Financial_Quarter_2_-_Official_Statistic.xlsx",
     "quarterly"),
    # FY 2025-26 Q3 quarterly report (Oct-Dec 2025)
    ("https://assets.ctfassets.net/j16ev64qyf6l/25GCNeCtZ21jQyVh36By6t/"
     "60c689b87f5108e7ed8c6500670125a0/"
     "Industry_Statistics_-_Quarterly_report_-_Financial_year_April_2025_to_March_2026"
     "_-_Financial_Quarter_3_-_Official_Statistic.xlsx",
     "quarterly"),
]


class UKGamblingCommissionCollector(StateCollector):
    state = "UK"
    regulator = "UK Gambling Commission"
    source_url = INDEX_URL

    async def list_reports(self, client: httpx.AsyncClient) -> list[ReportFile]:
        # Always include hardcoded fallback URLs so the collector never returns
        # an empty list when scraping fails (Cloudflare WAF, page restructure).
        out: list[ReportFile] = []
        for url, cadence in _HARDCODED_URLS:
            for v in VERTICALS:
                out.append(ReportFile(
                    operator="UK Statewide",
                    vertical=v,
                    cadence=cadence,
                    format="xlsx",
                    source_url=url,
                ))
        try:
            idx = await client.get(INDEX_URL, headers=UA_HEADERS, follow_redirects=True)
            idx.raise_for_status()
        except httpx.HTTPError:
            return out  # fallback only
        # Each PUB_RE match is a path fragment (no leading slash for the full
        # site root); we expand to absolute URLs via urljoin below.
        raw_slugs = sorted({m.group(0) for m in PUB_RE.finditer(idx.text)}, reverse=True)
        slugs = [s if s.startswith("/") else "/" + s for s in raw_slugs]

        seen_xlsx: set[str] = {url for url, _ in _HARDCODED_URLS}
        for slug in slugs[:12]:  # last ~3 financial years
            try:
                page = await client.get(urljoin(UKGC_BASE, slug), headers=UA_HEADERS,
                                        follow_redirects=True)
                page.raise_for_status()
            except httpx.HTTPError:
                continue
            xlsx_match = XLSX_RE.search(page.text)
            if not xlsx_match:
                continue
            xlsx_url = xlsx_match.group(0)
            if xlsx_url in seen_xlsx:
                continue
            seen_xlsx.add(xlsx_url)
            for v in VERTICALS:
                out.append(ReportFile(
                    operator="UK Statewide",
                    vertical=v,
                    cadence="quarterly",
                    format="xlsx",
                    source_url=xlsx_url,
                ))
        return out
