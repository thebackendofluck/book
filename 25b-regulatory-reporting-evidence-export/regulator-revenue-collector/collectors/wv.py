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
West Virginia Lottery iGaming + sports wagering monthly revenue.

Primary source: wvlottery.com monthly financial reports
  https://wvlottery.com/about/financial-reports/
  The WV Lottery publishes one PDF per month covering interactive gaming
  (iGaming) and sports wagering adjusted gross receipts.  However, the site
  aggressively blocks non-browser clients with HTTP 403; if the primary source
  is unreachable, we fall back to the WV Legislature agency reports index.

Fallback source: WV Legislature agency reports index (L04 = Lottery Commission)
  http://www.wvlegislature.gov/reports/agency_reports/agencylist_all.cfm
  The legislature index hosts the same "Monthly Report on Lottery Operations"
  PDFs filed by the Lottery Commission (agency code L04).  The default listing
  returns only the current fiscal year; we query FY_START..FY_END to collect
  all available historical months.

Page structure (legislature index): each report row is a <tr> with four <td>:
  td[0]  agency name  ("Lottery Commission")
  td[1]  report title ("Monthly Report on Lottery Operations July 2025")
  td[2]  fiscal year  ("Fiscal Year 2026")
  td[3]  PDF link     (<a href="…">View Report</a>)

The month/year lives in td[1], NOT in the <a> tag text, so we must walk up
to the parent <tr> to read the row title before filtering.

Coverage note: roughly half of the legislature PDFs are image-only scans with
no text layer; those return [] from the parser.  The legislature index currently
only hosts FY2026 reports (Jul 2025 onward).  Pre-FY2026 data requires direct
access to wvlottery.com which is blocked by IP-level 403.
"""
from __future__ import annotations

import logging
import re

import httpx
from bs4 import BeautifulSoup

from .base import StateCollector
from models import ReportFile

logger = logging.getLogger(__name__)

WVLOTTERY_FINANCIAL = "https://wvlottery.com/about/financial-reports/"
LISTING = "http://www.wvlegislature.gov/reports/agency_reports/agencylist_all.cfm"
BASE    = "https://www.wvlegislature.gov"
PDF_RE  = re.compile(r"/legisdocs/reports/agency/L04_FY_\d{4}_\d+\.pdf$", re.I)
# Matches WV Lottery monthly operations report title in two known orderings:
#   "Monthly Report on Lottery Operations <Month> <YYYY>"
#   "Lottery Report on Monthly Operations <Month> <YYYY>"  (seen Apr 2026)
MONTHLY_RE = re.compile(
    r"(?:monthly\s+report\s+on\s+lottery|lottery\s+report\s+on\s+monthly)\s+operations",
    re.I,
)

# Fiscal years to query from the legislature index.  The site returns the same
# current-FY listing regardless of the `fy` parameter, so querying multiple
# values is harmless (dedup by URL handles the overlap) and future-proofs the
# collector when the site adds per-FY filtering.
_FY_RANGE = range(2020, 2028)

UA_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


async def _try_wvlottery_com(client: httpx.AsyncClient) -> list[ReportFile]:
    """Attempt to collect monthly PDF links from wvlottery.com.

    Returns an empty list (not an exception) if the site is unreachable or
    returns a non-200 status (e.g. HTTP 403 IP-block).
    """
    try:
        res = await client.get(
            WVLOTTERY_FINANCIAL,
            headers=UA_HEADERS,
            follow_redirects=True,
            timeout=20.0,
        )
    except httpx.HTTPError as exc:
        logger.debug("wv: wvlottery.com unreachable: %s", exc)
        return []

    if res.status_code != 200:
        logger.debug(
            "wv: wvlottery.com returned HTTP %d — falling back to legislature",
            res.status_code,
        )
        return []

    out: list[ReportFile] = []
    seen: set[str] = set()
    # PDF links on wvlottery.com follow patterns like:
    #   /wp-content/uploads/YYYY/MM/<month>-<YYYY>-Interactive-Monthly-Report.pdf
    #   /wp-content/uploads/YYYY/MM/<month>-<YYYY>-Sports-Monthly-Report.pdf
    pdf_re = re.compile(r'href=["\']([^"\']+\.pdf)["\']', re.I)
    sports_re = re.compile(r"sports|wagering", re.I)
    igaming_re = re.compile(r"interactive|igaming", re.I)
    base_url = str(res.url).rsplit("/", 1)[0]

    for href in pdf_re.findall(res.text):
        full = href if href.startswith("http") else f"{base_url}/{href.lstrip('/')}"
        if full in seen:
            continue
        seen.add(full)
        fname = full.rsplit("/", 1)[-1]
        if sports_re.search(fname):
            vertical = "sports-wagering"
        elif igaming_re.search(fname):
            vertical = "igaming"
        else:
            # Emit both verticals — the combined monthly PDF has both.
            for v in ("igaming", "sports-wagering"):
                out.append(ReportFile(
                    operator="WV Lottery",
                    vertical=v,
                    cadence="monthly",
                    format="pdf",
                    source_url=full,
                ))
            continue
        out.append(ReportFile(
            operator="WV Lottery",
            vertical=vertical,
            cadence="monthly",
            format="pdf",
            source_url=full,
        ))
    return out


async def _collect_legislature(client: httpx.AsyncClient) -> list[ReportFile]:
    """Collect WV Legislature L04 monthly PDFs across all tracked fiscal years."""
    out: list[ReportFile] = []
    seen: set[str] = set()

    for fy in _FY_RANGE:
        try:
            res = await client.get(
                LISTING,
                params={"Agency": "L04", "fy": str(fy)},
                follow_redirects=True,
                timeout=20.0,
            )
            res.raise_for_status()
        except httpx.HTTPError as exc:
            logger.debug("wv: legislature listing FY%d failed: %s", fy, exc)
            continue

        soup = BeautifulSoup(res.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href: str = str(a["href"])
            if not PDF_RE.search(href):
                continue
            url: str = href if href.startswith("http") else BASE + href
            if url in seen:
                continue
            # The month/year text lives in a sibling <td>, not in the <a> tag.
            tr = a.find_parent("tr")
            if tr is None:
                continue
            tds = tr.find_all("td")
            row_title = tds[1].get_text(" ", strip=True) if len(tds) > 1 else ""
            if not MONTHLY_RE.search(row_title):
                continue  # skip annual / audit PDFs
            seen.add(url)
            # Emit two rows per month — the parser extracts both verticals.
            for vertical in ("igaming", "sports-wagering"):
                out.append(ReportFile(
                    operator="WV Lottery",
                    vertical=vertical,
                    cadence="monthly",
                    format="pdf",
                    source_url=url,
                ))

    return out


class WestVirginiaCollector(StateCollector):
    state = "WV"
    regulator = "West Virginia Lottery"
    source_url = LISTING

    async def list_reports(self, client: httpx.AsyncClient) -> list[ReportFile]:
        # 1. Try the primary source (wvlottery.com).
        primary = await _try_wvlottery_com(client)
        if primary:
            logger.info("wv: collected %d reports from wvlottery.com", len(primary))
            return primary

        # 2. Fall back to the WV Legislature agency-reports index.
        fallback = await _collect_legislature(client)
        logger.info(
            "wv: collected %d reports from WV Legislature index "
            "(wvlottery.com was unavailable)",
            len(fallback),
        )
        return fallback
