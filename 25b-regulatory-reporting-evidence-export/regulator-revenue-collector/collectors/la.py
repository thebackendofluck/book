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
Louisiana Gaming Control Board (LGCB) monthly revenue reports.

The LGCB landing page (lgcb.dps.louisiana.gov) is fronted by Cloudflare
and aggressively blocks non-browser clients with HTTP 403. The actual
PDF reports are hosted on the Louisiana State Police "Gaming Revenue
Reports" page (lsp.org/.../gaming-revenue-reports/) which serves the
files directly with no bot-block.

We therefore scrape the LSP index page and emit a ReportFile for each
monthly PDF we find. Filenames follow several conventions:

  Casino (riverboat / land-based / racetrack / video poker), YYYY-MM:
    /media/<hash>/{YYYY-MM}-{N}-{description}.pdf
    /media/<hash>/{YYYY-MM}-{description}.pdf
    e.g. 2026-03-1-riverboat-gaming-revenues.pdf

  Sports & DFS, FY-tagged with explicit year+month suffix:
    /media/<hash>/fy{NN-NN}_{retail|mobile}_sb_{YYYY}_{MM}_{mon}.pdf
    /media/<hash>/fy{NN-NN}_dfs_{YYYY}_{MM}_{mon}.pdf
    /media/<hash>/{YYYY-MM}_{retail|mobile}-sb_revenue.pdf
    /media/<hash>/{YYYYMM}_{mon}_fy{NN}_la_dfs_rev.pdf

The LSP page has no consistent <a> anchor text, so the filename itself
is our signal for both cadence and vertical classification.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .base import StateCollector
from models import ReportFile

LANDING = (
    "https://lsp.org/about/leadershipsections/bureau-of-investigations/"
    "gaming-enforcement-division/gaming-revenue-reports/"
)

UA_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Filename keyword → (vertical, operator-aggregate label).
# The same statewide PDF can roll up multiple operators; per-operator rows
# are emitted by the parser.
_VERTICAL_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"riverboat", re.I),                     "commercial-casino"),
    (re.compile(r"land[-_ ]?based", re.I),               "commercial-casino"),
    (re.compile(r"slots[-_ ]?at[-_ ]?racetrack", re.I),  "commercial-casino"),
    (re.compile(r"racetrack", re.I),                     "commercial-casino"),
    (re.compile(r"video[-_ ]?(gaming|poker)", re.I),     "video-gaming"),
    (re.compile(r"mobile[_-]?sb|mobile[-_ ]?sport",
                re.I),                                   "sports-wagering"),
    (re.compile(r"retail[_-]?sb|retail[-_ ]?sport",
                re.I),                                   "sports-wagering"),
    (re.compile(r"sportsbook", re.I),                    "sports-wagering"),
    (re.compile(r"\bdfs\b|daily[-_ ]?fantasy", re.I),    "fantasy"),
]


def _classify(filename: str) -> str | None:
    name = filename.lower()
    for pat, vertical in _VERTICAL_RULES:
        if pat.search(name):
            return vertical
    return None


class LouisianaCollector(StateCollector):
    state = "LA"
    regulator = "Louisiana Gaming Control Board"
    source_url = LANDING

    async def list_reports(self, client: httpx.AsyncClient) -> list[ReportFile]:
        try:
            r = await client.get(LANDING, headers=UA_HEADERS,
                                 follow_redirects=True, timeout=30.0)
            r.raise_for_status()
        except httpx.HTTPError:
            return []

        soup = BeautifulSoup(r.text, "html.parser")
        out: list[ReportFile] = []
        seen: set[str] = set()

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not href.lower().endswith(".pdf"):
                continue
            full = urljoin(LANDING, href)
            if full in seen:
                continue
            seen.add(full)

            fname = full.rsplit("/", 1)[-1]
            vertical = _classify(fname)
            if vertical is None:
                continue

            out.append(ReportFile(
                operator="LA Statewide",
                vertical=vertical,
                cadence="monthly",
                format="pdf",
                source_url=full,
            ))
        return out
