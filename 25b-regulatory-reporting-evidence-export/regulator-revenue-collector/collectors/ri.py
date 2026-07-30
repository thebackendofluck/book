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
Rhode Island Lottery monthly gaming revenue reports.

The RI Lottery is the regulator AND operator-of-record for Twin River's
sportsbook (mobile via Twin River Online, retail via Twin River Lincoln /
Tiverton) plus iGaming (iSlots / iTables) and table-games revenue.

All financial reports are PDFs hosted on the same CDN path:
  https://www.rilot.com/content/dam/interactive/ilottery/pdfs/financial/

There are three relevant report families, each one a single FY-cumulative
PDF that contains all monthly rows for that FY:

  Sportsbook     NOV_SportsbookWebsiteData.pdf            (current FY)
                 SportsbookWebsiteData{MM.YYYY}.pdf       (final-month snapshot)
                 SportsBookSummaryFY{YYYY}.pdf            (FY archives)

  iGaming        NOV_iGamingWebsiteData.pdf
                 iGamingWebsiteData{YYYY}.pdf

  Table Games    NOV_TableGamesWebsiteFYE_{YYYY}.pdf
                 TableGamesWebsiteFYE{MMYY}.pdf
                 TableGamesWebsite{YYYY}.pdf
                 TableGamesSummaryFY{YYYY}.pdf

Strategy: scrape the financials index page, keep only PDFs whose path
matches one of those families, and emit a ReportFile per file. The
parser opens each one and emits monthly facts.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

import httpx

from .base import StateCollector
from models import ReportFile

LANDING = "https://www.rilot.com/en-us/about-us/financials.html"
SPORTS_PAGE = "https://www.rilot.com/en-us/about/sports-wagering/"  # may 404; see README

UA_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Filename → vertical
_VERTICAL_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"sport(book|s)", re.I),       "sports-wagering"),
    (re.compile(r"igaming", re.I),             "igaming"),
    (re.compile(r"table[-_ ]?games?", re.I),   "commercial-casino"),
]

# Skip historical lottery / annual-report PDFs.
_SKIP_PATTERNS = (
    re.compile(r"acfr|cafr", re.I),
    re.compile(r"traditional", re.I),
    re.compile(r"annual", re.I),
    re.compile(r"financial.*statement", re.I),
)


def _classify(filename: str) -> str | None:
    name = filename.lower()
    for skip in _SKIP_PATTERNS:
        if skip.search(name):
            return None
    for pat, vertical in _VERTICAL_RULES:
        if pat.search(name):
            return vertical
    return None


class RhodeIslandCollector(StateCollector):
    state = "RI"
    regulator = "Rhode Island Lottery"
    source_url = LANDING

    async def list_reports(self, client: httpx.AsyncClient) -> list[ReportFile]:
        try:
            r = await client.get(LANDING, headers=UA_HEADERS,
                                 follow_redirects=True, timeout=30.0)
            r.raise_for_status()
        except httpx.HTTPError:
            return []

        # The rilot.com financials page contains a malformed <script> block
        # near the middle that causes html.parser AND lxml to silently drop
        # all content after it (including all the financial-PDF modals).
        # Fall back to a raw href regex over the whole response body.
        out: list[ReportFile] = []
        seen: set[str] = set()
        href_re = re.compile(r'href=["\']([^"\']+\.pdf)["\']', re.I)
        for href in href_re.findall(r.text):
            full = urljoin(LANDING, href)
            if full in seen:
                continue
            seen.add(full)
            fname = full.rsplit("/", 1)[-1]
            vertical = _classify(fname)
            if vertical is None:
                continue
            out.append(ReportFile(
                operator="RI Statewide",
                vertical=vertical,
                cadence="monthly",   # FY-cumulative file but all rows are monthly
                format="pdf",
                source_url=full,
            ))
        return out
