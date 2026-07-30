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
Belgium Kansspelcommissie / Commission des jeux de hasard (KSC/CJH) collector.

The KSC publishes two document series:
  1. Jaarverslag (annual report) — organisational + player-protection content;
     GGR figures are NOT included (market chapter deferred to the financial report).
  2. Financieel rapport (financial report, separate PDF) — full GGR breakdown by
     licence class for each calendar year.  First published for 2023 data in 2024-07.

All PDFs are hosted at:
  https://www.gamingcommission.be/sites/default/files/{YYYY-MM}/<filename>.pdf

Index page for financial reports and annual reports:
  https://www.gamingcommission.be/jaarverslagen

Known financial-report URLs (confirmed accessible):
  2024 data: /sites/default/files/2026-03/2024-KSC_Financieel-verslag-NL.pdf
  2023 data: /sites/default/files/2024-07/Financieel%20rapport%202023.pdf
  2022 data: /sites/default/files/2023-12/KSC_2022_Financieel%20rapport_Finale%20versie.pdf

Verticals captured per financial report:
  igaming          → Klasse A online (casino) + Klasse B online (arcade)
  sports-wagering  → Klasse F online (online weddenschappen)
  land-based-casino→ Klasse A offline

CDN note (confirmed 2026-06):
  gamingcommission.be drops TCP connections for requests with a non-browser
  User-Agent (the connection is closed before sending an HTTP response — not
  a 403, but a RemoteDisconnected error in Python).  A full browser UA is
  required for BOTH the index-page scrape and PDF downloads.  The Referer
  header is included as a best-practice for the CDN hot-link check.

  Because base.StateCollector.collect() builds its own client with the
  project-wide bot UA (USER_AGENT), this collector overrides collect() to
  inject a browser UA client instead.  The same headers are exported as
  _download_headers so _backfill_via_collector also benefits.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .base import DEFAULT_TIMEOUT, StateCollector
from models import ReportFile, StateSnapshot

INDEX_URL = "https://www.gamingcommission.be/jaarverslagen"

# Confirmed financial report URLs (static fallback when scraping fails)
_KNOWN_FINANCIAL_REPORTS: list[tuple[str, str]] = [
    # (source_url, label)
    (
        "https://www.gamingcommission.be/sites/default/files/2026-03/2024-KSC_Financieel-verslag-NL.pdf",
        "Financieel verslag 2024",
    ),
    (
        "https://www.gamingcommission.be/sites/default/files/2024-07/Financieel%20rapport%202023.pdf",
        "Financieel rapport 2023",
    ),
    (
        "https://www.gamingcommission.be/sites/default/files/2023-12/KSC_2022_Financieel%20rapport_Finale%20versie.pdf",
        "Financieel rapport 2022",
    ),
]

_PDF_RE = re.compile(r"\.pdf$", re.I)
_FINANCIAL_RE = re.compile(r"financieel[\s_-]*(rapport|verslag)", re.I)

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Full browser headers used for both index scrape and PDF downloads.
# Referer is included because the KSC CDN may enforce a hot-link check.
_BROWSER_HEADERS = {
    "User-Agent": _BROWSER_UA,
    "Referer": INDEX_URL,
}

# Legacy alias kept for any code that references UA_HEADERS directly.
UA_HEADERS = _BROWSER_HEADERS


class BelgiumCollector(StateCollector):
    state = "BE"
    regulator = "Kansspelcommissie / Commission des jeux de hasard (KSC)"
    source_url = INDEX_URL

    # Used by _backfill_via_collector to override headers on each file download.
    # Must include a browser UA because the CDN blocks non-browser agents at the
    # TCP level (RemoteDisconnected, not HTTP 403).
    _download_headers: dict[str, str] = _BROWSER_HEADERS

    # ------------------------------------------------------------------ #
    # Override collect() so the StateCollector base-class client uses a   #
    # browser UA instead of the project-wide bot string (USER_AGENT).     #
    # Without this override, _download_one() in base.py would use the bot #
    # UA and every PDF download would fail with RemoteDisconnected.        #
    # ------------------------------------------------------------------ #
    async def collect(self) -> StateSnapshot:
        cache_dir = self.output_root / self.state.lower()
        cache_dir.mkdir(parents=True, exist_ok=True)

        try:
            async with httpx.AsyncClient(
                timeout=DEFAULT_TIMEOUT,
                follow_redirects=True,
                headers=_BROWSER_HEADERS,
            ) as client:
                reports = list(await self.list_reports(client))
                tasks = [self._download_one(client, r, cache_dir) for r in reports]
                await asyncio.gather(*tasks, return_exceptions=False)
        finally:
            if self._insecure_client is not None:
                await self._insecure_client.aclose()
                self._insecure_client = None

        return StateSnapshot(
            state=self.state,
            regulator=self.regulator,
            source_url=self.source_url,
            collected_at=datetime.now(timezone.utc),
            reports=reports,
        )

    async def list_reports(self, client: httpx.AsyncClient) -> list[ReportFile]:
        """Discover financial report PDFs from the BGC jaarverslagen index page.

        Falls back to the hardcoded known-URL list when scraping fails or yields
        no financial reports (e.g. site restructuring).
        """
        scraped: list[ReportFile] = []
        try:
            # Pass _BROWSER_HEADERS explicitly so this method works even when
            # called from a client that doesn't have the browser UA (e.g. tests).
            r = await client.get(
                INDEX_URL,
                headers=_BROWSER_HEADERS,
                follow_redirects=True,
                timeout=30.0,
            )
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href: str = a["href"]
                if not _PDF_RE.search(href):
                    continue
                title = (a.get_text(" ", strip=True) or href.rsplit("/", 1)[-1]).strip()
                slug = title.lower()
                if not _FINANCIAL_RE.search(slug):
                    continue
                url = urljoin(INDEX_URL, href)
                scraped.append(ReportFile(
                    operator="BE Statewide",
                    vertical="igaming",   # parser will emit multiple verticals
                    cadence="annual",
                    format="pdf",
                    source_url=url,
                ))
        except httpx.HTTPError:
            pass

        if scraped:
            return scraped

        # Fallback: known confirmed URLs
        return [
            ReportFile(
                operator="BE Statewide",
                vertical="igaming",
                cadence="annual",
                format="pdf",
                source_url=url,
            )
            for url, _label in _KNOWN_FINANCIAL_REPORTS
        ]

    async def parse(self, report: ReportFile, file_path) -> list:
        from parsers.be_metrics import parse_be_pdf
        return parse_be_pdf(file_path, report.source_url)
