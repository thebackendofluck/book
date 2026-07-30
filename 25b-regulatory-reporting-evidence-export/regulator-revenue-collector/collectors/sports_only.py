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
Sports-betting-only state collectors.

These US states permit retail/mobile sports betting but NOT online casino.
Each collector scrapes the regulator's revenue page for monthly Excel/PDF
reports. URLs change occasionally — when a collector returns 0 reports,
re-check the source page in a browser.

States covered:
  NV  Nevada Gaming Control Board     — monthly statewide revenue + sports
  IL  Illinois Gaming Board           — monthly sports revenue
  IN  Indiana Gaming Commission       — monthly sports revenue
  VA  Virginia Lottery                — monthly sports revenue
  CO  Colorado Division of Gaming     — monthly sports revenue
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .base import StateCollector
from models import ReportFile


def _scrape_pdf_xlsx_links(
    url: str,
    soup: BeautifulSoup,
    keywords: re.Pattern[str],
    cap: int = 18,
) -> list[tuple[str, str, str]]:
    """Returns [(href, text, ext), …] for files matching keywords."""
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(" ", strip=True) or ""
        lower = href.lower()
        if not (lower.endswith(".pdf") or lower.endswith(".xlsx") or lower.endswith(".xls")):
            continue
        blob = (text + " " + href).lower()
        if not keywords.search(blob):
            continue
        full = urljoin(url, href)
        if full in seen:
            continue
        seen.add(full)
        ext = "xlsx" if lower.endswith(".xlsx") else ("xls" if lower.endswith(".xls") else "pdf")
        out.append((full, text, ext))
        if len(out) >= cap:
            break
    return out


class _SportsLandingCollector(StateCollector):
    """Common helper: scrape one landing page, classify everything as sports-wagering."""
    keywords: re.Pattern[str] = re.compile(r"revenue|monthly|report|sports", re.I)

    async def list_reports(self, client: httpx.AsyncClient) -> list[ReportFile]:
        try:
            res = await client.get(self.source_url)
            res.raise_for_status()
        except httpx.HTTPError:
            return []
        soup = BeautifulSoup(res.text, "html.parser")
        out: list[ReportFile] = []
        for href, text, ext in _scrape_pdf_xlsx_links(self.source_url, soup, self.keywords):
            label = (text or href.rsplit("/", 1)[-1])[:120]
            out.append(ReportFile(
                operator=f"{self.state} {label}",
                vertical="sports-wagering",
                cadence="monthly",
                format=ext,
                source_url=href,
            ))
        return out


class NevadaCollector(_SportsLandingCollector):
    state = "NV"
    regulator = "Nevada Gaming Control Board"
    source_url = "https://gaming.nv.gov/index.aspx?page=149"


class IllinoisCollector(_SportsLandingCollector):
    state = "IL"
    regulator = "Illinois Gaming Board"
    source_url = "https://www.igb.illinois.gov/sportsreports.aspx"


class IndianaCollector(_SportsLandingCollector):
    state = "IN"
    regulator = "Indiana Gaming Commission"
    source_url = "https://www.in.gov/igc/files/sports-wagering-monthly-revenue-statewide/"


class VirginiaCollector(_SportsLandingCollector):
    state = "VA"
    regulator = "Virginia Lottery"
    source_url = "https://www.valottery.com/aboutus/sportsbettingreports"


class ColoradoCollector(_SportsLandingCollector):
    state = "CO"
    regulator = "Colorado Division of Gaming"
    source_url = "https://sbg.colorado.gov/sites/sbg/files/documents/Sports%20Betting%20Proceeds.pdf"

    async def list_reports(self, client: httpx.AsyncClient) -> list[ReportFile]:
        # CO publishes a single rolling PDF rather than per-month files.
        return [ReportFile(
            operator="CO Sports Betting Proceeds (rolling)",
            vertical="sports-wagering",
            cadence="monthly",
            format="pdf",
            source_url=self.source_url,
        )]
