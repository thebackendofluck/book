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
Arizona Department of Gaming event-wagering monthly revenue reports.

gaming.az.gov is fronted by Cloudflare and returns 403 to all non-browser
clients on the production server.  The Internet Archive (Wayback Machine)
has every AZ event-wagering revenue file archived WITHOUT that protection.

Discovery strategy
------------------
1. Try the existing live probing / HTML-scrape approach (works when
   Cloudflare is not blocking — e.g. on a residential IP or if ADG
   relaxes the challenge).
2. If live discovery yields nothing (the common prod-server case), fall back
   to the IA CDX API to find every archived revenue file and serve raw bytes
   via the Wayback `id_` snapshot URL.

Wayback CDX patterns (verified against the IA index 2026-06-08):
  - gaming.az.gov/sites/default/files/EW*
    filter: original matches .*[Ee][Ww].*(pdf|PDF)
    → covers all monthly PDF reports from Sept 2021 through 2026:
      "EW Website Report - {Mon} {YYYY}.pdf"
      "EW Revenue Report for Website - {Mon} {YYYY}.pdf"
      "EW Revenue Report for ADG website - {Mon} {YYYY}.pdf"
      "EW Report for Website - {Mon} {YYYY}.pdf"
      "EW Website Report-{Month} {YYYY} UNAUDITED*.pdf"
      "EW Revenue June 2025 UNAUDITED-Revised *.pdf"
      "EW April 2022 Revenue Report.pdf" (and similar non-standard names)

The `id_` suffix on a Wayback URL returns the original raw bytes (verified:
correct Content-Type, correct file size, status 200) without any Cloudflare
challenge.

AZ launched event wagering in September 2021; we look back up to 56 months.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from urllib.parse import quote, urljoin

import httpx
from bs4 import BeautifulSoup

from .base import StateCollector
from models import ReportFile

INDEX = "https://gaming.az.gov/blog-terms/event-wagering-revenue-reports"
CURRENT_REPORTS_PAGE = "https://gaming.az.gov/blog-terms/current-adg-reports"
# New resources page added when ADG restructured the site (observed 2026).
RESOURCES_REPORTS_PAGE = "https://gaming.az.gov/resources/reports"
CDN_BASE = "https://gaming.az.gov/sites/default/files/"

# Abbreviated month names used in AZ PDF filenames.
_MON_ABBR = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]

# Full month names — used for 2025+ filenames.
_MON_LONG = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

# Regex to detect any EW revenue report link in HTML pages.
# Matches both /sites/default/files/EW*.pdf and wrapper URLs like
# /event-wagering-revenue-report-{month}-{year}.
_EW_FILE_RE = re.compile(
    r"(?i)(EW[_ -](?:Website[_ -]|Revenue[_ -]Report[_ -]|)[^\"'<>]*\.pdf)",
)
_EW_WRAPPER_RE = re.compile(
    r"event-wagering-revenue-report-(?P<m>[a-z]+)-(?P<y>20\d{2})", re.I,
)

# Regex that accepts any original URL that looks like a monthly revenue PDF.
# Covers all known naming conventions observed in the IA CDX index.
_EW_REVENUE_PDF_RE = re.compile(
    r"(?i)EW.*(revenue|report|rpt|website).+\.(pdf)$"
)

# Full browser headers — required to maximise pass-rate against Cloudflare's
# bot-challenge on gaming.az.gov.
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8,"
        "application/signed-exchange;v=b3;q=0.7"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}

_DOWNLOAD_HEADERS = {
    **_BROWSER_HEADERS,
    "Accept": "application/pdf,*/*;q=0.8",
    "Referer": "https://gaming.az.gov/blog-terms/current-adg-reports",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Dest": "document",
}

# AZ launched event wagering in September 2021; extend lookback to cover
# the full history available in the IA index.
_PROBE_MONTHS = 56

# Known unpredictable 2026 filename suffixes (google-indexed / CDX examples).
# Each tuple: (Month name as it appears in filename, year, fragment list)
# We probe each confirmed suffix first; generic UNAUDITED is always tried last.
_KNOWN_2026_SUFFIXES = [
    ("January",  2026, [
        "UNAUDITED UPDATED CB 17 APR",
        "UNAUDITED",
    ]),
    ("February", 2026, [
        "UNAUDITED CB UPDATED 17 APR",
        "UNAUDITED",
    ]),
    # March–May 2026: no confirmed suffix as of 2026-06-08; try generic variants.
    ("March",    2026, ["UNAUDITED"]),
    ("April",    2026, ["UNAUDITED"]),
    ("May",      2026, ["UNAUDITED"]),
]


def _candidate_urls(year: int, month_idx: int) -> list[str]:
    """Return all known filename variants for a given (year, 1-based month).

    Variants are ordered most-likely-first so that the probe loop can break
    as soon as any HEAD request returns 200.
    """
    abbr = _MON_ABBR[month_idx - 1]
    long_ = _MON_LONG[month_idx - 1]
    candidates: list[str] = []

    if year >= 2026:
        # Era 4: full month name + UNAUDITED suffix (unpredictable tail).
        # Try the known specific suffixes first, then generic UNAUDITED, then
        # the bare abbreviated variant in case AZ simplifies the name later.
        known = {m: suffs for m, _, suffs in _KNOWN_2026_SUFFIXES
                 if _ == year}
        suffs = known.get(long_, ["UNAUDITED"])
        for suff in suffs:
            candidates.append(f"EW Website Report-{long_} {year} {suff}.pdf")
        # Generic fallback variants for 2026+
        candidates.extend([
            f"EW Website Report-{long_} {year} UNAUDITED.pdf",
            f"EW Website Report - {long_} {year} UNAUDITED.pdf",
            f"EW Website Report - {abbr} {year}.pdf",
            f"EW Website Report-{abbr} {year}.pdf",
        ])
    elif year == 2025:
        # Era 3: both space-dash and no-space-dash variants observed.
        candidates.extend([
            f"EW Website Report - {abbr} {year}.pdf",
            f"EW Website Report-{abbr} {year}.pdf",
            f"EW Website Revenue Report - {abbr} {year}.pdf",
            f"EW Website Revenue Report-{abbr} {year}.pdf",
            # 2025 also uses "Revenue Report for ADG website" variant
            f"EW Revenue Report for ADG website - {abbr} {year}.pdf",
            # UNAUDITED suffix seen starting mid-2025
            f"EW Website Report-{long_} {year} UNAUDITED.pdf",
        ])
    elif year == 2024:
        # Era 2 / Era 3 boundary — both "Revenue" and non-"Revenue" forms seen.
        candidates.extend([
            f"EW Website Report - {abbr} {year}.pdf",
            f"EW Website Report-{abbr} {year}.pdf",
            f"EW Website Revenue Report - {abbr} {year}.pdf",
            f"EW Website Revenue Report-{abbr} {year}.pdf",
        ])
    elif year == 2023:
        # Era 2 (second half) + residual Era 1
        candidates.extend([
            f"EW Website Revenue Report-{abbr} {year}.pdf",
            f"EW Website Revenue Report - {abbr} {year}.pdf",
            f"EW Revenue Report for Website - {abbr} {year}.pdf",
            f"EW Revenue Report for Website -{abbr} {year}.pdf",
        ])
    else:
        # Era 1 (2021-2022): several dash/space variants + no-dash Jul 2022 +
        # non-standard "April 2022 Revenue Report" style.
        candidates.extend([
            f"EW Revenue Report for Website - {abbr} {year}.pdf",
            f"EW Revenue Report for Website -{abbr} {year}.pdf",
            f"EW Revenue Report for Website {abbr} {year}.pdf",
            f"EW Revenue Report for Website-{abbr} {year}.pdf",
            # Non-standard full-month-first style seen for Apr 2022:
            # "EW April 2022 Revenue Report.pdf"
            f"EW {long_} {year} Revenue Report.pdf",
            # Sep 2021 used "Sept" not "Sep"
            f"EW Website Report - Sept {year}.pdf" if abbr == "Sep" else "",
            # Oct 2021 format confirmed via CDX:
            # "EW Website Report - Oct 2021.pdf"
            f"EW Website Report - {abbr} {year}.pdf",
            # "EW Report for Website - {Mon} {YYYY}.pdf" (Aug 2022)
            f"EW Report for Website - {abbr} {year}.pdf",
        ])
        candidates = [c for c in candidates if c]

    # URL-encode and return deduplicated list.
    seen: set[str] = set()
    out: list[str] = []
    for c in candidates:
        url = CDN_BASE + quote(c, safe="")
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


class ArizonaCollector(StateCollector):
    state = "AZ"
    regulator = "Arizona Department of Gaming"
    source_url = RESOURCES_REPORTS_PAGE

    # Expose download headers for _backfill_via_collector.
    _download_headers = _DOWNLOAD_HEADERS

    async def list_reports(self, client: httpx.AsyncClient) -> list[ReportFile]:
        """Discover monthly revenue reports, with Wayback fallback.

        Priority order:
          1. Direct probe — tries each known filename variant for each month
             using HEAD requests (works when Cloudflare is not blocking).
          2. HTML index scrape — tries the ADG listing pages.
          3. Wayback Machine (IA CDX) — the reliable fallback that bypasses
             Cloudflare entirely; uses the `id_` raw-bytes URL.

        The Wayback fallback is tried whenever live discovery yields nothing
        after checking the most recent 3 months, which is the definitive
        signal that Cloudflare is blocking all non-browser clients.
        """
        found = await self._probe_direct(client)
        if not found:
            # Index-page fallback: works when Cloudflare is not blocking.
            # RESOURCES_REPORTS_PAGE is the current ADG page (post-2026 restructure);
            # the legacy blog-term pages are tried as secondary fallbacks.
            for index_url in [RESOURCES_REPORTS_PAGE, CURRENT_REPORTS_PAGE, INDEX]:
                found = await self._scrape_index(client, index_url)
                if found:
                    break

        if not found:
            # Wayback Machine fallback: bypasses Cloudflare entirely.
            found = await self._wayback_fallback(client)

        return found

    async def _probe_direct(
        self, client: httpx.AsyncClient
    ) -> list[ReportFile]:
        """Try every known filename variant for each month in the lookback window."""
        out: list[ReportFile] = []
        seen: set[str] = set()

        today = date.today()
        # Start from the last complete month.
        probe_date = date(today.year, today.month, 1) - timedelta(days=1)

        for _ in range(_PROBE_MONTHS):
            year = probe_date.year
            month = probe_date.month

            found_this_month = False
            for url in _candidate_urls(year, month):
                if url in seen:
                    continue
                try:
                    resp = await client.head(
                        url,
                        headers=_DOWNLOAD_HEADERS,
                        follow_redirects=True,
                        timeout=12.0,
                    )
                    if resp.status_code == 200:
                        seen.add(url)
                        out.append(ReportFile(
                            operator="AZ Statewide",
                            vertical="sports-wagering",
                            cadence="monthly",
                            format="pdf",
                            source_url=url,
                        ))
                        found_this_month = True
                        break  # Got this month; skip remaining variants.
                except httpx.HTTPError:
                    pass

            # If Cloudflare is blocking everything (no months found after 5
            # consecutive misses from the most recent period), bail early.
            # We use 5 (not 3) because the current and previous 1-2 months
            # may not have been published yet — those genuine misses must not
            # be mistaken for a blocked probe.
            if not found_this_month and len(out) == 0 and _ >= 4:
                break

            # Step back one month.
            first_of_month = date(probe_date.year, probe_date.month, 1)
            probe_date = first_of_month - timedelta(days=1)

        return out

    async def _scrape_index(
        self, client: httpx.AsyncClient, index_url: str,
    ) -> list[ReportFile]:
        """Fall back: scrape an HTML index page to discover PDF links.

        Handles both direct /sites/default/files/*.pdf hrefs and wrapper
        pages like /event-wagering-revenue-report-{month}-{year}.
        """
        try:
            r = await client.get(
                index_url,
                headers=_BROWSER_HEADERS,
                follow_redirects=True,
                timeout=20.0,
            )
            if r.status_code != 200:
                return []
        except httpx.HTTPError:
            return []

        soup = BeautifulSoup(r.text, "html.parser")
        out: list[ReportFile] = []
        seen: set[str] = set()

        for a in soup.find_all("a", href=True):
            href = str(a["href"])

            # Direct CDN PDF link.
            if CDN_BASE in href or (href.lower().endswith(".pdf") and "EW" in href):
                full = urljoin(index_url, href)
                if full not in seen:
                    seen.add(full)
                    out.append(ReportFile(
                        operator="AZ Statewide",
                        vertical="sports-wagering",
                        cadence="monthly",
                        format="pdf",
                        source_url=full,
                    ))
                continue

            # Wrapper page — follow it to get the CDN PDF link.
            if _EW_WRAPPER_RE.search(href):
                wrapper = urljoin(index_url, href)
                if wrapper in seen:
                    continue
                seen.add(wrapper)
                try:
                    page = await client.get(
                        wrapper,
                        headers=_BROWSER_HEADERS,
                        follow_redirects=True,
                        timeout=12.0,
                    )
                    if page.status_code != 200:
                        continue
                except httpx.HTTPError:
                    continue

                sub = BeautifulSoup(page.text, "html.parser")
                for sa in sub.find_all("a", href=True):
                    sh = str(sa["href"])
                    if not sh.lower().endswith(".pdf"):
                        continue
                    if not any(k in sh for k in ("EW", "Event", "event-wagering")):
                        continue
                    full = urljoin(wrapper, sh)
                    if full not in seen:
                        seen.add(full)
                        out.append(ReportFile(
                            operator="AZ Statewide",
                            vertical="sports-wagering",
                            cadence="monthly",
                            format="pdf",
                            source_url=full,
                        ))

        return out[:_PROBE_MONTHS]

    async def _wayback_fallback(
        self, client: httpx.AsyncClient
    ) -> list[ReportFile]:
        """Discover AZ monthly revenue PDFs via the Internet Archive CDX API.

        The IA has every ADG event-wagering revenue file archived.  The CDX
        `id_` URL returns the original raw bytes without any Cloudflare
        challenge, making this the most reliable discovery path when the live
        site is blocked.

        We run two CDX queries:
          1. Primary: gaming.az.gov/sites/default/files/EW*
             filtered to PDF files matching the monthly revenue naming pattern.
          2. Secondary: broader .*Reven.* filter that also catches non-standard
             names like "EW April 2022 Revenue Report.pdf" and the 2025+
             "Revenue Report for ADG website" variant.

        Results are deduplicated by original URL and returned as ReportFile
        objects pointing at Wayback `id_` URLs (raw bytes, no HTML wrapper).
        """
        # Query 1: files starting with "EW" — the primary naming convention.
        pairs1 = await self.wayback_snapshots(
            client,
            "gaming.az.gov/sites/default/files/EW*",
            filter_regex=r".*[Ee][Ww].*(pdf|PDF)$",
            limit=400,
        )
        # Query 2: any file containing "Reven" in the path — catches non-standard names.
        pairs2 = await self.wayback_snapshots(
            client,
            "gaming.az.gov/sites/default/files/*",
            filter_regex=r".*[Ee][Ww].*[Rr]even.*",
            limit=400,
        )

        # Merge and deduplicate by original URL, keeping newest timestamp.
        by_url: dict[str, str] = {}
        for orig, ts in pairs1 + pairs2:
            orig_lower = orig.lower()
            # Skip non-PDF files (images, application forms, license docs, etc.)
            if not orig_lower.endswith(".pdf"):
                continue
            # Require the URL to match the monthly revenue pattern.
            if not _EW_REVENUE_PDF_RE.search(orig):
                continue
            # Skip query-string variants (same file, different referrer).
            if "?" in orig:
                continue
            # Keep the most recent snapshot for each original URL.
            if orig not in by_url or ts > by_url[orig]:
                by_url[orig] = ts

        out: list[ReportFile] = []
        for orig_url, timestamp in sorted(by_url.items(), key=lambda x: x[1], reverse=True):
            wayback_url = self.wayback_raw_url(orig_url, timestamp)
            out.append(ReportFile(
                operator="AZ Statewide",
                vertical="sports-wagering",
                cadence="monthly",
                format="pdf",
                source_url=wayback_url,
            ))

        return out[:_PROBE_MONTHS]
