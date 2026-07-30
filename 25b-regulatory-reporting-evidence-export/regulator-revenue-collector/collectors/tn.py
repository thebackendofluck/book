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
Tennessee Sports Wagering Council (SWAC) monthly reports.

TN was the first US state to permit ONLY mobile sports betting. Reports
are state-aggregated (handle, AGR, privilege tax) — no per-operator breakout
in the public files.

Landing: https://www.tn.gov/swac/reports.html
DAM root: https://www.tn.gov/content/dam/tn/swac/documents/report/{YYYY}/

The landing page — and the CDN edge serving /content/dam/ — is fronted by a
WAF that returns TCP status 000 (connection dropped) to non-browser clients
when accessed from cloud/server IPs.  We try live discovery first (works from
developer desktops), then ALWAYS also query the Internet Archive Wayback
Machine and merge the results.  This is necessary because the live CDN
sometimes returns 200 for older years (2020-2024) while returning 404 for
more-recent months that Wayback has already archived — a live-only strategy
therefore silently misses the newest data.

Two filename conventions exist across the archive:

  2020–2023 (short, month-name only):
    {Month}.pdf  e.g. April.pdf
    Full URL: .../report/2023/April.pdf

  2024–present (verbose with year):
    Monthly Report for SWC - {Month} {YYYY} (PDF).pdf  (uppercase PDF)
    Monthly Report for SWC - {Month} {YYYY} (pdf).pdf  (lowercase pdf, some 2025 months)
    Monthly Report for SWC - {Month} {YYYY} (PDF) 1.pdf  (extra " 1", July 2025)
    Full URL (URL-encoded spaces as %20):
    .../report/2024/Monthly%20Report%20for%20SWC%20-%20August%202024%20(PDF).pdf

Wayback discovery (always run, merged with live results):
  Discovery: CDX API query for tn.gov/content/dam/tn/swac/documents/report*
  Download:  wayback_raw_url() → id_ snapshots (original bytes, status 200)
  The parser's _period_from_url() handles Wayback-wrapped URLs because the
  original URL is embedded verbatim after the `id_/` prefix.

NOTE: the council was originally the "Sports Wagering Advisory Council"
(SWAC) and briefly labelled as "SWC" in filenames — the DAM path uses
"swac" throughout. The filename label "SWC" (not "SWAC") is used in the
verbose pattern as confirmed by live indexed URLs.
"""
from __future__ import annotations

import asyncio
from datetime import date
from urllib.parse import quote

import httpx
import structlog

from .base import StateCollector
from models import ReportFile

log = structlog.get_logger()

LANDING = "https://www.tn.gov/swac/reports.html"
DAM_BASE = "https://www.tn.gov/content/dam/tn/swac/documents/report"

# CDX wildcard pattern — covers both swac/ and swc/ sub-paths plus any year.
_WAYBACK_PATTERN = "tn.gov/content/dam/tn/swac/documents/report*"

_MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

# Full browser UA — required to reach the tn.gov DAM CDN edge when live.
_UA_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": LANDING,
}

# TN sports wagering launched November 2019; DAM archive starts Jan 2020.
_START_YEAR = 2020

# Year from which the verbose "Monthly Report for SWC - {Month} {YYYY} (PDF).pdf"
# naming convention was adopted (confirmed: August 2024 and December 2024 use it).
_VERBOSE_FROM_YEAR = 2024


def _candidate_urls(year: int, month: str) -> list[str]:
    """Return candidate DAM URLs for a given year and month name.

    For 2024+ try the verbose pattern first (primary), then short as fallback.
    For 2020-2023 try the short pattern first, verbose as fallback.

    Both PDF and CSV variants are included — TN publishes both formats for
    most months starting in late 2023.  PDF is preferred when both exist
    (PDF URLs listed first so the live probe resolves them with priority).

    Additional PDF variants:
    - Lowercase "(pdf)" suffix seen in 2025 (May, June, …)
    - Trailing " 1" before ".pdf" seen in July 2025

    CSV is also published in verbose format starting 2023-11; for 2023 there
    are also yearless-verbose CSVs for some months.
    """
    base = f"{DAM_BASE}/{year}"

    # ── PDF variants ─────────────────────────────────────────────────────────
    verbose_pdf_name = f"Monthly Report for SWC - {month} {year} (PDF).pdf"
    verbose_pdf_url = f"{base}/{quote(verbose_pdf_name)}"

    verbose_lc_pdf_name = f"Monthly Report for SWC - {month} {year} (pdf).pdf"
    verbose_lc_pdf_url = f"{base}/{quote(verbose_lc_pdf_name)}"

    verbose_1_pdf_name = f"Monthly Report for SWC - {month} {year} (PDF) 1.pdf"
    verbose_1_pdf_url = f"{base}/{quote(verbose_1_pdf_name)}"

    short_pdf_url = f"{base}/{month}.pdf"

    # Yearless-verbose PDF (seen for some 2024 months, e.g. January 2024).
    yearless_pdf_name = f"Monthly Report for SWC - {month} (PDF).pdf"
    yearless_pdf_url = f"{base}/{quote(yearless_pdf_name)}"

    # ── CSV variants ──────────────────────────────────────────────────────────
    verbose_csv_name = f"Monthly Report for SWC - {month} {year} (CSV).csv"
    verbose_csv_url = f"{base}/{quote(verbose_csv_name)}"

    short_csv_url = f"{base}/{month}.csv"

    # Yearless-verbose CSV (seen for some 2023-2024 months).
    yearless_csv_name = f"Monthly Report for SWC - {month} (CSV).csv"
    yearless_csv_url = f"{base}/{quote(yearless_csv_name)}"

    if year >= _VERBOSE_FROM_YEAR:
        return [
            verbose_pdf_url, verbose_lc_pdf_url, verbose_1_pdf_url,
            yearless_pdf_url,
            verbose_csv_url, yearless_csv_url,
            short_pdf_url, short_csv_url,
        ]
    return [
        short_pdf_url, short_csv_url,
        verbose_pdf_url, verbose_lc_pdf_url, verbose_1_pdf_url,
        yearless_pdf_url,
        verbose_csv_url, yearless_csv_url,
    ]


def _all_candidates() -> list[tuple[str, str, str]]:
    """Return [(url, month_name, year_str), ...] for all non-future months."""
    today = date.today()
    out: list[tuple[str, str, str]] = []
    for year in range(_START_YEAR, today.year + 1):
        for idx, month in enumerate(_MONTHS, start=1):
            if year == today.year and idx > today.month:
                break
            for url in _candidate_urls(year, month):
                out.append((url, month, str(year)))
    return out


class TennesseeCollector(StateCollector):
    state = "TN"
    regulator = "Tennessee Sports Wagering Council"
    source_url = LANDING

    # Expose headers so _backfill_via_collector can pass them to the
    # streaming download (same pattern as GR/BE collectors).
    _download_headers: dict[str, str] = _UA_HEADERS

    async def list_reports(self, client: httpx.AsyncClient) -> list[ReportFile]:
        """Discover TN SWAC monthly PDFs.

        Strategy: run BOTH live probes and Wayback discovery concurrently,
        then merge the results.  Deduplication is by (year, month) period so
        a month covered by both sources produces only one ReportFile (the live
        URL is preferred when the live probe succeeds; the Wayback id_ URL is
        used otherwise).

        This dual strategy is necessary because the tn.gov CDN may serve 200
        for older files (2020-2024) while returning 404 for recently-published
        months that the Wayback Machine has already crawled — a live-only
        strategy silently misses the newest data.  Conversely, a Wayback-only
        strategy misses months published after the last Wayback crawl.
        """
        live_reports, wayback_reports = await asyncio.gather(
            self._list_live(client),
            self._list_wayback(client),
        )

        if not live_reports and not wayback_reports:
            log.warning("tn: both live and Wayback discovery returned 0 results",
                        state=self.state)
            return []

        # Build a map of period → ReportFile from Wayback as the base layer,
        # then overlay live results (live URL preferred when available).
        # Within each source, PDFs are preferred over CSVs for the same period.
        from parsers.tn_metrics import _period_from_url  # local import to avoid circular

        def _period(r: ReportFile) -> str | None:
            return _period_from_url(r.source_url)

        def _is_pdf(r: ReportFile) -> bool:
            return r.format == "pdf"

        merged: dict[str, ReportFile] = {}

        for r in wayback_reports:
            p = _period(r)
            if p:
                # Prefer PDF over CSV when both are present for the same period.
                if p not in merged or (_is_pdf(r) and not _is_pdf(merged[p])):
                    merged[p] = r

        for r in live_reports:
            p = _period(r)
            if p:
                # Live overrides Wayback; PDFs preferred over CSVs.
                if p not in merged or _is_pdf(r) or not _is_pdf(merged[p]):
                    merged[p] = r

        result = list(merged.values())
        # Include any reports whose period couldn't be parsed (unusual, but keep them).
        unkeyed = [r for r in (live_reports + wayback_reports) if _period(r) is None]
        result.extend(unkeyed)

        log.info(
            "tn: discovered reports",
            state=self.state,
            live=len(live_reports),
            wayback=len(wayback_reports),
            merged=len(result),
        )
        return result

    # ------------------------------------------------------------------
    # Live discovery
    # ------------------------------------------------------------------

    async def _list_live(self, client: httpx.AsyncClient) -> list[ReportFile]:
        """Probe candidate DAM URLs with HEAD requests; return one ReportFile
        per successfully resolved month.  Returns [] when the WAF blocks all
        requests (every HEAD gets an httpx.ConnectError / status 000).
        """
        candidates = _all_candidates()
        sem = asyncio.Semaphore(8)

        # Track which (year, month) pairs have already resolved so we don't
        # emit two ReportFiles for the same month if both patterns exist.
        resolved: set[tuple[str, str]] = set()
        lock = asyncio.Lock()

        async def probe(url: str, month: str, year: str) -> ReportFile | None:
            async with lock:
                if (year, month) in resolved:
                    return None
            async with sem:
                try:
                    r = await client.head(
                        url,
                        headers=_UA_HEADERS,
                        follow_redirects=True,
                        timeout=20.0,
                    )
                    if r.status_code == 200:
                        async with lock:
                            if (year, month) in resolved:
                                return None
                            resolved.add((year, month))
                        fmt = "csv" if url.lower().endswith(".csv") else "pdf"
                        return ReportFile(
                            operator="TN Statewide",
                            vertical="sports-wagering",
                            cadence="monthly",
                            format=fmt,
                            source_url=url,
                        )
                except httpx.HTTPError:
                    pass
            return None

        tasks = [probe(url, month, year) for url, month, year in candidates]
        results = await asyncio.gather(*tasks)
        return [r for r in results if r is not None]

    # ------------------------------------------------------------------
    # Wayback Machine fallback
    # ------------------------------------------------------------------

    async def _list_wayback(self, client: httpx.AsyncClient) -> list[ReportFile]:
        """Discover TN SWAC PDFs and CSVs via the IA CDX API.

        Returns ReportFiles whose source_url points to the Wayback raw-bytes
        (id_) endpoint.  The id_ URL delivers the original file bytes without
        the tn.gov WAF.  The parser's _period_from_url() handles these wrapped
        URLs because the original URL is embedded verbatim after `id_/`:
          https://web.archive.org/web/{ts}id_/https://www.tn.gov/.../April.pdf

        Both PDF and CSV files are discovered — TN publishes both formats for
        most months starting in late 2023.
        """
        pairs = await self.wayback_snapshots(
            client,
            _WAYBACK_PATTERN,
            filter_regex=r".*\.(pdf|csv)$",
            limit=500,
        )
        if not pairs:
            return []

        # Dedupe by original URL — keep the most-recent snapshot for each.
        seen: dict[str, str] = {}  # original_url → timestamp
        for original, timestamp in pairs:
            if original not in seen:
                seen[original] = timestamp

        reports: list[ReportFile] = []
        for original, timestamp in seen.items():
            fmt = "csv" if original.lower().endswith(".csv") else "pdf"
            reports.append(ReportFile(
                operator="TN Statewide",
                vertical="sports-wagering",
                cadence="monthly",
                format=fmt,
                source_url=self.wayback_raw_url(original, timestamp),
            ))

        return reports
