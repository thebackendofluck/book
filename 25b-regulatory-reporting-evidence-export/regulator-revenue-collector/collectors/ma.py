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
Massachusetts Gaming Commission sports wagering monthly revenue.

MA publishes one PDF per (operator, month) under
https://massgaming.com/wp-content/uploads/{Operator}-Cat.-{1|3}-Sports-Wagering-Revenue-Report-{Month}-{YYYY}.pdf

Cat. 1 = retail (Encore, MGM Springfield, Plainridge); Cat. 3 = mobile
(BetMGM, Caesars, DraftKings, FanDuel, Bally's, Fanatics, theScore).
The landing page only shows the most recent month; an archive page links
historical files. We scrape both.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .base import StateCollector
from models import ReportFile

LANDING = "https://massgaming.com/regulations/revenue/"
ARCHIVE = "https://massgaming.com/regulations/revenue/revenue-report-archives/"

PDF_RE = re.compile(
    r"/wp-content/uploads/"
    r"(?P<op>[A-Za-z0-9'\-]+?)-Cat\.?-?(?P<cat>[13])-"
    r"Sports-Wagering-Revenue-Report-(?P<month>[A-Z][a-z]+)-(?P<year>\d{4})\.pdf",
    re.IGNORECASE,
)

OP_NORMALIZE = {
    "Ballys":                 "Bally's",
    "theScore-Bet":           "theScore Bet",
    "Encore-Boston-Harbor":   "Encore Boston Harbor",
    "MGM-Springfield":        "MGM Springfield",
    "Plainridge-Park-Casino": "Plainridge Park Casino",
}


class MassachusettsCollector(StateCollector):
    state = "MA"
    regulator = "Massachusetts Gaming Commission"
    source_url = LANDING

    async def list_reports(self, client: httpx.AsyncClient) -> list[ReportFile]:
        out: list[ReportFile] = []
        seen: set[str] = set()
        for url in (LANDING, ARCHIVE):
            try:
                res = await client.get(url, follow_redirects=True)
                res.raise_for_status()
            except httpx.HTTPError:
                continue
            soup = BeautifulSoup(res.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if not href.lower().endswith(".pdf"):
                    continue
                m = PDF_RE.search(href)
                if not m:
                    continue
                full = href if href.startswith("http") else urljoin(url, href)
                if full in seen:
                    continue
                seen.add(full)
                op_raw = m.group("op")
                operator = OP_NORMALIZE.get(op_raw, op_raw.replace("-", " "))
                out.append(ReportFile(
                    operator=operator,
                    vertical="sports-wagering",
                    cadence="monthly",
                    format="pdf",
                    source_url=full,
                ))
        return out
