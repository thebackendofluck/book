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
Colorado Division of Gaming sports betting monthly reports.

CO publishes one PDF per month plus a rolling operator-level XLS. Filename
prefixes grow monotonically (`25` = May '22, `60` = April '25) so we do not
template URLs — we scrape the index each run and match `(Mon 'YY)` tokens.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .base import StateCollector
from models import ReportFile

LANDING = "https://sbg.colorado.gov/sports-betting-monthly-reports"
XLS_ROLLING = "https://sbg.colorado.gov/media/11906"
PDF_RE = re.compile(r"/sites/sbg/files/documents/\d+\s*Monthly\s*Summary.*\.pdf", re.I)
# CO uses Drupal behind aggressive bot detection; even WebFetch gets 403.
# These browser-mimic headers don't bypass it consistently — included for
# best-effort. The fallback is the rolling XLS URL (also 403 in practice).
# Real fix is a residential-proxy/Playwright path or a data-share agreement.
UA_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://sbg.colorado.gov/",
}


class ColoradoCollector(StateCollector):
    state = "CO"
    regulator = "Colorado Division of Gaming"
    source_url = LANDING

    async def list_reports(self, client: httpx.AsyncClient) -> list[ReportFile]:
        out: list[ReportFile] = []
        seen: set[str] = set()
        try:
            r = await client.get(LANDING, headers=UA_HEADERS, follow_redirects=True)
            r.raise_for_status()
        except httpx.HTTPError:
            # fall through: still emit the rolling XLS below
            r = None
        if r is not None:
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if not PDF_RE.search(href.replace("%20", " ")):
                    continue
                full = href if href.startswith("http") else urljoin(LANDING, href)
                if full in seen:
                    continue
                seen.add(full)
                out.append(ReportFile(
                    operator="CO All Operators",
                    vertical="sports-wagering",
                    cadence="monthly",
                    format="pdf",
                    source_url=full,
                ))
        # Always include the rolling operator-level XLS — it's the canonical source.
        out.append(ReportFile(
            operator="CO All Operators (rolling XLS)",
            vertical="sports-wagering",
            cadence="monthly",
            format="xls",
            source_url=XLS_ROLLING,
        ))
        return out
