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
Malta Gaming Authority (MGA) annual report collector.

The MGA is the world's largest iGaming licensing jurisdiction.  It publishes:

  1. **Annual Reports** (fiscal year; released mid-following year)
     URL pattern: https://www.mga.org.mt/app/uploads/MGA-Annual-Report-{YYYY}.pdf
     Fallback: /app/uploads/Annual-Report-{YYYY}.pdf  (used for 2009-2022 on some years)

  2. **Interim Reports** (H1 review; released autumn of the same year)
     URL pattern: https://www.mga.org.mt/app/uploads/Interim-Report-{YYYY}.pdf

  3. **Regulatory Oversight** reports (annual supervisory summary)
     URL pattern: /app/uploads/Regulatory-Oversight-{YYYY}.pdf
     Older: /app/uploads/Regulatory-Oversight-Supervisory-Engagement-Efforts-{YYYY}.pdf

All reports contain Gross Gaming Revenue (GGR) figures in EUR broken down by:
  - B2C Remote Casino (igaming)
  - B2C Sports Betting (sports-wagering)
  - B2C Poker (online-poker)
  - B2C Lottery / Lotto (lottery)
  - B2B (platform/software supply revenue — tracked separately in later editions)

Currency: EUR.  FX conversion to USD uses fixed 1.08 approximation.

Index page: https://www.mga.org.mt/our-work/annual-reports-financial-statements/
"""
from __future__ import annotations

from datetime import date

import httpx

from .base import StateCollector
from models import ReportFile

LANDING = "https://www.mga.org.mt/our-work/annual-reports-financial-statements/"
UA_HEADERS = {"User-Agent": "Mozilla/5.0 RegulatorRevenueCollector/1.0"}

# Confirmed PDF URL templates (in probe order)
_ANNUAL_TEMPLATES = [
    "https://www.mga.org.mt/app/uploads/MGA-Annual-Report-{year}.pdf",
    "https://www.mga.org.mt/app/uploads/MGA-{year}-Annual-Report.pdf",
    "https://www.mga.org.mt/app/uploads/Annual-Report-{year}.pdf",
]
_INTERIM_TEMPLATE = "https://www.mga.org.mt/app/uploads/Interim-Report-{year}.pdf"
_OVERSIGHT_TEMPLATES = [
    "https://www.mga.org.mt/app/uploads/Regulatory-Oversight-{year}.pdf",
    "https://www.mga.org.mt/app/uploads/Regulatory-Oversight-Supervisory-Engagement-Efforts-{year}.pdf",
]

# Verticals present in MGA annual reports
_ANNUAL_VERTICALS = (
    "igaming",           # B2C Remote Casino (RNG + live dealer)
    "sports-wagering",   # B2C Sports Betting
    "online-poker",      # B2C Poker
    "lottery",           # B2C Lottery
)

# Back-fill years: annual reports confirmed available 2014–present
_ANNUAL_YEAR_RANGE = range(2014, date.today().year)
# Interim reports available from 2015
_INTERIM_YEAR_RANGE = range(2015, date.today().year + 1)
# Regulatory Oversight reports from 2025+
_OVERSIGHT_YEAR_RANGE = range(2025, date.today().year + 1)


class MaltaCollector(StateCollector):
    state = "MT"
    regulator = "Malta Gaming Authority (MGA)"
    source_url = LANDING

    async def list_reports(self, client: httpx.AsyncClient) -> list[ReportFile]:
        out: list[ReportFile] = []
        seen_urls: set[str] = set()

        async def _probe(url: str) -> bool:
            """Return True if the URL resolves to a PDF (200 response)."""
            try:
                r = await client.head(url, headers=UA_HEADERS,
                                      follow_redirects=True, timeout=20.0)
                return r.status_code == 200
            except httpx.HTTPError:
                return False

        # ── Annual reports ────────────────────────────────────────────────────
        for year in _ANNUAL_YEAR_RANGE:
            confirmed_url: str | None = None
            for tpl in _ANNUAL_TEMPLATES:
                url = tpl.format(year=year)
                if url in seen_urls:
                    confirmed_url = url
                    break
                if await _probe(url):
                    confirmed_url = url
                    seen_urls.add(url)
                    break

            if confirmed_url is None:
                continue

            # Emit one ReportFile per PDF; the parser handles all verticals
            # internally.  Emitting one-per-vertical caused 4× duplication
            # because parse_mt_pdf always emits all four verticals regardless
            # of the ReportFile.vertical field.
            out.append(ReportFile(
                operator="MT Statewide",
                vertical="all",
                cadence="annual",
                format="pdf",
                source_url=confirmed_url,
            ))

        # ── Interim (H1) reports ──────────────────────────────────────────────
        for year in _INTERIM_YEAR_RANGE:
            url = _INTERIM_TEMPLATE.format(year=year)
            if url in seen_urls:
                continue
            if not await _probe(url):
                continue
            seen_urls.add(url)
            out.append(ReportFile(
                operator="MT Statewide",
                vertical="all",
                cadence="semi-annual",
                format="pdf",
                source_url=url,
            ))

        # ── Regulatory Oversight reports ─────────────────────────────────────
        for year in _OVERSIGHT_YEAR_RANGE:
            for tpl in _OVERSIGHT_TEMPLATES:
                url = tpl.format(year=year)
                if url in seen_urls:
                    break
                if await _probe(url):
                    seen_urls.add(url)
                    out.append(ReportFile(
                        operator="MT Statewide",
                        vertical="all",
                        cadence="annual",
                        format="pdf",
                        source_url=url,
                    ))
                    break

        return out
