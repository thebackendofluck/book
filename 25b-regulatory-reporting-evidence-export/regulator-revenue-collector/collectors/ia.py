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
Iowa Racing and Gaming Commission monthly revenue reports.

IRGC publishes two separate PDFs per month:

  * Gaming Revenue Report        (commercial-casino)
  * Sports Wagering Revenue Report (sports-wagering)

Files are served from a Drupal 11 media-ID system (URLs look like
``/media/<id>/download?inline``) with no predictable numbering, so we scrape
the two index pages and enrich each ``<a>`` tag's aria-label to recover the
period (e.g. "March 2026 Gaming Revenue") and vertical.

Index pages:
  * https://irgc.iowa.gov/publications-reports/gaming-revenue
  * https://irgc.iowa.gov/publications-reports/sports-wagering-revenue
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .base import StateCollector
from models import ReportFile

BASE = "https://irgc.iowa.gov"
GAMING_INDEX = f"{BASE}/publications-reports/gaming-revenue"
SPORTS_INDEX = f"{BASE}/publications-reports/sports-wagering-revenue"

UA_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
}

# "March 2026 Gaming Revenue", "February 2026 Sports Wagering Revenue Report", …
_LABEL_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|"
    r"August|September|October|November|December)\s+(\d{4})\b",
    re.I,
)
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


class IowaCollector(StateCollector):
    state = "IA"
    regulator = "Iowa Racing and Gaming Commission"
    source_url = GAMING_INDEX

    async def _scrape(
        self,
        client: httpx.AsyncClient,
        url: str,
        vertical: str,
        keep_word: str,
        skip_words: tuple[str, ...] = (),
    ) -> list[ReportFile]:
        try:
            r = await client.get(url, headers=UA_HEADERS, follow_redirects=True, timeout=30)
            r.raise_for_status()
        except httpx.HTTPError:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        out: list[ReportFile] = []
        seen: set[str] = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/media/" not in href or "download" not in href:
                continue
            label = (a.get("aria-label") or a.get_text() or "").strip()
            lower = label.lower()
            if keep_word.lower() not in lower:
                continue
            if any(sw.lower() in lower for sw in skip_words):
                continue
            m = _LABEL_RE.search(label)
            if not m:
                continue
            full = urljoin(BASE, href)
            if full in seen:
                continue
            seen.add(full)
            # IA files are all PDFs (Content-Type: application/pdf, served
            # with ?inline via Drupal's media module).
            out.append(ReportFile(
                operator="IA All Operators",
                vertical=vertical,
                cadence="monthly",
                format="pdf",
                source_url=full,
            ))
        return out

    async def list_reports(self, client: httpx.AsyncClient) -> list[ReportFile]:
        # Gaming index also links to "Gaming Amendments" and annual rollups;
        # filter to month-named "Gaming Revenue" entries only.
        gaming = await self._scrape(
            client, GAMING_INDEX,
            vertical="commercial-casino",
            keep_word="Gaming Revenue",
            skip_words=("amendment", "fiscal year", "sports"),
        )
        sports = await self._scrape(
            client, SPORTS_INDEX,
            vertical="sports-wagering",
            keep_word="Sports Wagering",
            skip_words=("amendment", "fiscal year", "annual"),
        )
        return gaming + sports
