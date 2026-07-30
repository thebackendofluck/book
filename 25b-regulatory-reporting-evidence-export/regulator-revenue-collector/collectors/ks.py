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
Kansas Racing and Gaming Commission monthly revenue (commercial casino).

KRGC publishes one PDF per month covering the four state-owned commercial
casinos (Boot Hill, Kansas Star, Hollywood, Kansas Crossing) — one page
per facility, with EGM win, table-game win, and the state/local/casino
share splits.

Historical reports (through ~Nov 2025) live under the canonical path:
  /wp-content/uploads/{YYYY}/{MM}/{Month}_{YYYY}_Revenue_Report.pdf

From Dec 2025 onward KRGC began publishing via WordPress page slugs that
resolve directly to PDF content (no intermediate HTML page):
  /revenue-december-2025/
  /revenue-jan-2026/
  /revenue-feb-2026/
  /march-revenue-reports/
  /april-revenue-reports/
We scrape the index at /public-info/revenue-reports/ and accept:
  (a) any href ending in .pdf that matches the canonical filename pattern, or
  (b) any href matching the slug patterns above (the slug IS the PDF).

Sports wagering is operated through the Kansas Lottery (per K.S.A. 74-8780).
As of March 2026, kslottery.com permanently redirects to kslottery.gov.
Monthly handle/AGR PDFs are published at:
  https://www.kslottery.gov/publications/sports-monthly-revenues/
  https://www.kslottery.gov/publications/sports-monthly-detail-breakdown/
We probe both pages; if they 4xx/error we silently emit no sports-wagering
ReportFiles rather than poisoning the snapshot.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .base import StateCollector
from models import ReportFile

KRGC_INDEX = "https://krgc.kansas.gov/public-info/revenue-reports/"
KRGC_HOST = "https://krgc.kansas.gov"

# kslottery.com permanently redirected to kslottery.gov in March 2026.
KSLOTTERY_HOST = "https://www.kslottery.gov"
_KSLOTTERY_SW_PAGES = [
    "/publications/sports-monthly-revenues/",
    "/publications/sports-monthly-detail-breakdown/",
]

# PDF filename canonical form (historical wp-content path). Examples:
#   September_2025_Revenue_Report.pdf
#   April_2014_Revenue_Reports.pdf      (typo: trailing 's')
#   december_2013_RR.pdf                (early abbreviation)
_PDF_CANONICAL_RE = re.compile(
    r"/(?P<month>january|february|march|april|may|june|july|august|"
    r"september|october|november|december)"
    r"[_\-]?(?P<year>20\d{2})[_\- ](?:revenue[_ ]?reports?|RR)\.pdf$",
    re.I,
)

# Slug patterns used from Dec 2025 onward — the slug URL resolves directly to
# PDF bytes (Content-Type: application/pdf, confirmed empirically).
# Group 1 = month name, Group 2 = year.
_SLUG_RE = re.compile(
    r"krgc\.kansas\.gov/"
    r"(?:"
    # /revenue-{month}-{year}/ or /revenue-{month}{year}/
    r"revenue-"
    r"(?P<m1>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"[_\-]?(?P<y1>20\d{2})"
    r"|"
    # /{month}-revenue-reports/ (e.g. april-revenue-reports)
    r"(?P<m2>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"-revenue-reports"
    r")",
    re.I,
)

# Older single-casino files were per-property: 2011-MM_bhcr_revenue_report.pdf.
# We deliberately exclude those — they never aggregate into the consolidated
# multi-casino layout the parser expects.

UA_BROWSER = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/pdf,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
# kslottery.gov is also CDN-fronted; a standard browser UA works on the
# publications pages (confirmed Mar 2026).
UA_CURL = {"User-Agent": "curl/8.0.0"}


class KansasCollector(StateCollector):
    state = "KS"
    regulator = "Kansas Racing and Gaming Commission"
    source_url = KRGC_INDEX

    async def list_reports(self, client: httpx.AsyncClient) -> list[ReportFile]:
        out: list[ReportFile] = []
        seen: set[str] = set()

        # --- KRGC casino monthly revenue PDFs ---
        try:
            resp = await client.get(
                KRGC_INDEX, headers=UA_BROWSER, follow_redirects=True, timeout=30,
            )
            html = resp.text if resp.status_code == 200 else ""
        except httpx.HTTPError:
            html = ""

        if html:
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                full = urljoin(KRGC_INDEX, href)

                if href.lower().endswith(".pdf"):
                    # Historical wp-content path: apply canonical filename check.
                    if not _PDF_CANONICAL_RE.search(full):
                        continue
                else:
                    # Post-Nov-2025 slug-based reports: the slug 301-redirects to
                    # the actual wp-content PDF.  Resolve the redirect now so we
                    # store the canonical URL (which contains month+year in the
                    # path and is parseable by _period_from_url).
                    if not _SLUG_RE.search(full):
                        continue
                    full = full.rstrip("/")
                    try:
                        head = await client.head(
                            full, headers=UA_BROWSER,
                            follow_redirects=True, timeout=15,
                        )
                        if head.url and str(head.url) != full:
                            full = str(head.url)
                    except httpx.HTTPError:
                        pass  # keep the slug URL; parser will try its best

                if full in seen:
                    continue
                seen.add(full)
                out.append(ReportFile(
                    operator="KS All Casinos",
                    vertical="commercial-casino",
                    cadence="monthly",
                    format="pdf",
                    source_url=full,
                ))

        # --- Kansas Lottery sports-wagering (best-effort) ---
        # Live since 2022-09-01.  As of March 2026, kslottery.com permanently
        # redirects to kslottery.gov with a new publications structure.
        # We probe the two relevant monthly-report index pages on kslottery.gov.
        for sw_path in _KSLOTTERY_SW_PAGES:
            try:
                sw_resp = await client.get(
                    f"{KSLOTTERY_HOST}{sw_path}",
                    headers=UA_BROWSER, follow_redirects=True, timeout=20,
                )
            except httpx.HTTPError:
                continue

            if sw_resp.status_code != 200:
                continue

            sw_soup = BeautifulSoup(sw_resp.text, "html.parser")
            for a in sw_soup.find_all("a", href=True):
                href = a["href"]
                if not href.lower().endswith(".pdf"):
                    continue
                # kslottery.gov media URLs use opaque slugs (/media/<hash>/…);
                # accept any PDF from the kslottery.gov domain.
                full = urljoin(KSLOTTERY_HOST, href)
                if "kslottery.gov" not in full:
                    continue
                if full in seen:
                    continue
                seen.add(full)
                out.append(ReportFile(
                    operator="KS All Sports Operators",
                    vertical="sports-wagering",
                    cadence="monthly",
                    format="pdf",
                    source_url=full,
                ))

        return out
