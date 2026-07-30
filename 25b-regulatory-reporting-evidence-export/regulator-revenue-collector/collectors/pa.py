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
Pennsylvania Gaming Control Board (PGCB).

PGCB publishes monthly revenue spreadsheets at
https://gamingcontrolboard.pa.gov/news-and-transparency/revenue
(the legacy ?p=405 URL silently redirects to the homepage and is dead).

Each file is per-vertical and per-fiscal-year, cumulative monthly
(one xlsx per vertical, refreshed each month — not one file per month).
Verticals: slots, table games, interactive (iGaming), sports wagering,
fantasy contests, VGT (truck-stop video gaming).

Files live under /sites/default/files/<YYYY-MM>/<filename>.{xlsx,pdf}.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .base import StateCollector
from models import ReportFile

INDEX_URL = "https://gamingcontrolboard.pa.gov/news-and-transparency/revenue"

VERTICAL_RULES = [
    (re.compile(r"interactive|igaming",     re.I), "igaming"),
    (re.compile(r"sports[\s_-]*wager",      re.I), "sports-wagering"),
    (re.compile(r"fantasy",                 re.I), "fantasy"),
    (re.compile(r"vgt|video[\s_-]*gaming",  re.I), "vgt"),
    (re.compile(r"slots?",                  re.I), "commercial-casino"),
    (re.compile(r"table[\s_-]*games?",      re.I), "commercial-casino"),
]

REVENUE_PAT = re.compile(
    r"revenue|interactive|igaming|sports|slots?|table|fantasy|vgt", re.I,
)


def _classify(fname: str, href: str) -> str:
    blob = (fname + " " + href).lower()
    for pat, v in VERTICAL_RULES:
        if pat.search(blob):
            return v
    return "commercial-casino"


class PennsylvaniaCollector(StateCollector):
    state = "PA"
    regulator = "Pennsylvania Gaming Control Board"
    source_url = INDEX_URL

    async def list_reports(self, client: httpx.AsyncClient) -> list[ReportFile]:
        try:
            # PGCB Drupal sometimes 403s on default httpx UA; force a browser-like one.
            res = await client.get(
                self.source_url,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; RevCollector/1.0)"},
            )
            res.raise_for_status()
        except httpx.HTTPError:
            return []

        soup = BeautifulSoup(res.text, "html.parser")
        out: list[ReportFile] = []
        seen: set[str] = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/sites/default/files/" not in href:
                continue
            lower = href.lower()
            if not lower.endswith((".xlsx", ".xls", ".pdf")):
                continue
            if not REVENUE_PAT.search(href):
                continue
            full = urljoin(self.source_url, href)
            if full in seen:
                continue
            seen.add(full)
            fname = href.rsplit("/", 1)[-1]
            vertical = _classify(fname, href)
            ext = "xlsx" if lower.endswith(".xlsx") else ("xls" if lower.endswith(".xls") else "pdf")
            out.append(ReportFile(
                operator=f"PA {fname[:120]}",
                vertical=vertical,
                cadence="monthly",
                format=ext,
                source_url=full,
            ))
            if len(out) >= 30:
                break
        return out
