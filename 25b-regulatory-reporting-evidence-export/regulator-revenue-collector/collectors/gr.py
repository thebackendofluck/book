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
Greece Hellenic Gaming Commission (HGC / ΕΕΕΠ) annual report collector.

HGC migrated its whole site from the old Joomla install at
gamingcommission.gov.gr (index.php?... routes) to a WordPress install at
hgc.gov.gr some time before 2026-07. The old domain now 301-redirects the
homepage but the specific index.php paths this collector used 404 on the
new install (confirmed live 2026-07-22) — the report listings live at:

  https://hgc.gov.gr/το-έργο-μας/εκθέσεις-πεπραγμένων/
    → Annual Activity Reports (Greek PDF), 2012–present (now includes 2025)
  https://hgc.gov.gr/en/work/annual-activity-reports/
    → English editions for 2012–2015

Both pages link the *same* PDF filenames the old site used
(AnnualReport{YYYY}GR.pdf etc.), now hosted under
hgc.gov.gr/wp-content/uploads/..., so gr_metrics.py's filename-based year
parsing needs no changes.

A standalone 2019 Gambling Market Statistics Report (English, EN_FINAL) was
hosted at a fixed /images/banners/ URL on the old Joomla site; that path is
gone on the new WordPress install (404) and no equivalent has been found, so
it is kept only as a best-effort entry — the download will 404 and be
skipped gracefully by _backfill_via_collector.

All publications are PDF, cadence annual, currency EUR.

Note: HGC's promised "quarterly-statistical-reports" page does not exist yet
(returns 404 as of 2026-04). The collector is wired to pick those up
automatically if the URL becomes live — add QUARTERLY_INDEX to INDEX_URLS and
implement _is_quarterly_report() once the page appears.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .base import StateCollector
from models import ReportFile

BASE = "https://hgc.gov.gr"
_OLD_BASE = "https://www.gamingcommission.gov.gr"

# Periodic-reports index (Greek, all years)
PERIODIC_INDEX = f"{BASE}/το-έργο-μας/εκθέσεις-πεπραγμένων/"

# English annual-reports index (2012–2015)
EN_ANNUAL_INDEX = f"{BASE}/en/work/annual-activity-reports/"

# Standalone market-statistics report (English, 2019 data). Only existed on
# the old Joomla site; no equivalent URL found on the new WordPress install.
# Left pointing at the dead old-domain path — the 404 is skipped gracefully.
MARKET_STATS_PDF = (
    f"{_OLD_BASE}/images/banners/"
    "2019_Gambling_Market_Statistics_Report_March_2020_EN_FINAL.pdf"
)

# Pattern that matches the annual-report filename convention used by HGC
_ANNUAL_RE = re.compile(
    r"(Annual[Rr]eport\d{4}|AnnualReport\d{4}|"
    r"Annual[_\-]Activity[_\-]Report[_\-]\d{4}|"
    r"Annual[_\-]Report[_\-]\d{4}|"
    r"apologismos\d{4})",
    re.IGNORECASE,
)

UA_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": f"{BASE}/",
}


def _year_from_href(href: str) -> int | None:
    """Extract a 4-digit year from a PDF filename, or None."""
    m = re.search(r"(\d{4})", href.rsplit("/", 1)[-1])
    return int(m.group(1)) if m else None


def _is_annual_report_pdf(href: str) -> bool:
    """True if the link looks like an HGC annual/market-stats PDF."""
    lower = href.lower()
    if not lower.endswith(".pdf"):
        return False
    return bool(_ANNUAL_RE.search(href)) or "market_statistics" in lower


def _scrape_index(html: str, base_url: str) -> list[tuple[str, int | None]]:
    """Return (absolute_pdf_url, year_or_None) pairs from an index page."""
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    results: list[tuple[str, int | None]] = []
    for a in soup.find_all("a", href=True):
        href: str = a["href"]
        if not href.lower().endswith(".pdf"):
            continue
        if not _is_annual_report_pdf(href):
            continue
        full_url = urljoin(base_url, href)
        if full_url in seen:
            continue
        seen.add(full_url)
        results.append((full_url, _year_from_href(href)))
    return results


class GreeceCollector(StateCollector):
    state = "GR"
    regulator = "Hellenic Gaming Commission (HGC / ΕΕΕΠ)"
    source_url = PERIODIC_INDEX

    # The volt-adc WAF fronting hgc.gov.gr (same as the old
    # gamingcommission.gov.gr) blocks PDF downloads unless the request
    # carries a browser-like UA *and* a matching Referer.  Expose UA_HEADERS
    # as _download_headers so _backfill_via_collector picks it up
    # (see backfill.py: extra_headers = getattr(inst, "_download_headers", {})).
    _download_headers: dict[str, str] = UA_HEADERS

    async def list_reports(self, client: httpx.AsyncClient) -> list[ReportFile]:
        reports: list[ReportFile] = []
        seen_urls: set[str] = set()

        # ── 1. Greek periodic-reports index ──────────────────────────────────
        try:
            r = await client.get(
                PERIODIC_INDEX, headers=UA_HEADERS,
                follow_redirects=True, timeout=30.0,
            )
            r.raise_for_status()
            for url, year in _scrape_index(r.text, PERIODIC_INDEX):
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                reports.append(ReportFile(
                    operator="GR Statewide",
                    vertical="combined",
                    cadence="annual",
                    format="pdf",
                    source_url=url,
                ))
        except httpx.HTTPError:
            pass

        # ── 2. English annual-reports index (2012–2015) ──────────────────────
        try:
            r = await client.get(
                EN_ANNUAL_INDEX, headers=UA_HEADERS,
                follow_redirects=True, timeout=30.0,
            )
            r.raise_for_status()
            for url, _year in _scrape_index(r.text, EN_ANNUAL_INDEX):
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                reports.append(ReportFile(
                    operator="GR Statewide",
                    vertical="combined",
                    cadence="annual",
                    format="pdf",
                    source_url=url,
                ))
        except httpx.HTTPError:
            pass

        # ── 3. Standalone 2019 market-statistics report (EN, best data) ──────
        if MARKET_STATS_PDF not in seen_urls:
            reports.append(ReportFile(
                operator="GR Statewide",
                vertical="combined",
                cadence="annual",
                format="pdf",
                source_url=MARKET_STATS_PDF,
            ))

        return reports
