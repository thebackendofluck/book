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
Illinois Gaming Board (IGB) — Sports Wagering Revenue Collector.

The IGB publishes per-operator monthly sports-wagering data via a stateful
ASP.NET WebForms application at:

    https://igbapps.illinois.gov/SportsReports_AEM.aspx

Each request requires harvesting the standard WebForms hidden fields
(__VIEWSTATE, __VIEWSTATEGENERATOR, __EVENTVALIDATION) from a prior GET,
then POSTing with the desired date range and report type.

Two report types are collected:

1. **Tax Summary** (``SearchType=TypeAccrual``, ``SearchAccrual=Tax Summary Report``)
   Contains per-operator State AGR (= GGR) and tax amounts.
   Cached with ``rtype=tax-summary`` in the fabricated source_url.
   Parser: ``parsers/il_metrics.py::parse_il_csv`` → ``metric_name='ggr'``

2. **AllActivitySportDetail** (``SearchType=TypeCash``, ``SearchCash=Sport Detail Report``)
   Contains per-sport wager counts and handle; no GGR column.
   Cached with ``rtype=sport-detail`` (or no rtype) in the fabricated source_url.
   Parser: ``parsers/il_metrics.py::parse_il_csv`` → ``metric_name='handle'``

Month coverage: 2020-03 (first full month of legal IL sports betting) through
the most recent complete month — one CSV per report type per month.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

from .base import StateCollector, RETRYABLE_STATUS
from models import ReportFile

ENDPOINT = "https://igbapps.illinois.gov/SportsReports_AEM.aspx"

# 2020-03 is the first full month of legal IL sports betting.
_START = date(2020, 3, 1)

def _current_end() -> date:
    """Return the last complete month to collect (lag one month from today)."""
    today = date.today()
    if today.month == 1:
        return date(today.year - 1, 12, 1)
    return date(today.year, today.month - 1, 1)

_MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

# Regex patterns to extract ASP.NET hidden field values from HTML.
_VS_PAT = re.compile(r'id="__VIEWSTATE"\s+value="([^"]*)"', re.I)
_VSG_PAT = re.compile(r'id="__VIEWSTATEGENERATOR"\s+value="([^"]*)"', re.I)
_EV_PAT = re.compile(r'id="__EVENTVALIDATION"\s+value="([^"]*)"', re.I)

log = logging.getLogger(__name__)


def _month_range(start: date, end: date) -> list[date]:
    """Return first-of-month dates from *start* to *end* inclusive."""
    months: list[date] = []
    cur = start.replace(day=1)
    end = end.replace(day=1)
    while cur <= end:
        months.append(cur)
        if cur.month == 12:
            cur = cur.replace(year=cur.year + 1, month=1)
        else:
            cur = cur.replace(month=cur.month + 1)
    return months


def _extract_viewstate(html: str) -> dict[str, str]:
    """Pull the three ASP.NET hidden-field values from a page."""
    fields: dict[str, str] = {}
    m = _VS_PAT.search(html)
    if m:
        fields["__VIEWSTATE"] = m.group(1)
    m = _VSG_PAT.search(html)
    if m:
        fields["__VIEWSTATEGENERATOR"] = m.group(1)
    m = _EV_PAT.search(html)
    if m:
        fields["__EVENTVALIDATION"] = m.group(1)
    return fields


def _build_tax_summary_post(month_name: str, year: str, viewstate: dict[str, str]) -> dict[str, str]:
    """Assemble the POST payload for the Tax Summary (AGR/GGR) report."""
    data: dict[str, str] = {
        "SearchType": "TypeAccrual",
        "SearchCash": "Detail Report",
        "SearchAccrual": "Tax Summary Report",
        "SearchStartMonth": month_name,
        "SearchStartYear": year,
        "SearchEndMonth": month_name,
        "SearchEndYear": year,
        "ViewType": "ViewCSV",
        "ButtonSearch.x": "5",
        "ButtonSearch.y": "5",
    }
    data.update(viewstate)
    return data


def _build_sport_detail_post(month_name: str, year: str, viewstate: dict[str, str]) -> dict[str, str]:
    """Assemble the POST payload for the AllActivity Sport Detail (handle) report."""
    data: dict[str, str] = {
        "SearchType": "TypeCash",
        "SearchCash": "Sport Detail Report",
        "SearchAccrual": "Tax Summary Report",
        "SearchStartMonth": month_name,
        "SearchStartYear": year,
        "SearchEndMonth": month_name,
        "SearchEndYear": year,
        "ViewType": "ViewCSV",
        "ButtonSearch.x": "5",
        "ButtonSearch.y": "5",
    }
    data.update(viewstate)
    return data


class IllinoisCollector(StateCollector):
    """Collects IGB sports-wagering monthly CSVs via WebForms POST.

    Produces two report entries per month:
      - Tax Summary (rtype=tax-summary) → GGR via State AGR column
      - Sport Detail (rtype=sport-detail) → handle via summed sport columns
    """

    state = "IL"
    regulator = "Illinois Gaming Board"
    source_url = ENDPOINT

    def __init__(self, output_root: Path, *, concurrency: int = 2) -> None:
        # Keep concurrency low — the IGB server is a single ASP.NET instance.
        super().__init__(output_root, concurrency=concurrency)
        # Maps fabricated report source_url → (real_endpoint, POST payload).
        self._post_params: dict[str, tuple[str, dict[str, str]]] = {}

    # ------------------------------------------------------------------
    # list_reports
    # ------------------------------------------------------------------

    async def list_reports(self, client: httpx.AsyncClient) -> list[ReportFile]:
        """Build two ReportFiles per month (Tax Summary + Sport Detail).

        A single GET harvests the initial ViewState; per-month POST payloads
        are registered in self._post_params keyed by the fabricated source_url
        that _download_one resolves at download time.
        """
        try:
            resp = await client.get(ENDPOINT)
            resp.raise_for_status()
            viewstate = _extract_viewstate(resp.text)
        except httpx.HTTPError as exc:
            log.warning("IL: failed to GET form page: %s", exc)
            viewstate = {}

        months = _month_range(_START, _current_end())
        reports: list[ReportFile] = []

        for m in months:
            month_name = _MONTH_NAMES[m.month - 1]
            year_str = str(m.year)
            period = f"{m.year}-{m.month:02d}"

            # --- Tax Summary (GGR / AGR) ---
            tax_url = f"{ENDPOINT}?period={period}&rtype=tax-summary"
            self._post_params[tax_url] = (
                ENDPOINT,
                _build_tax_summary_post(month_name, year_str, viewstate),
            )
            reports.append(ReportFile(
                operator=f"IL Statewide Tax Summary {month_name} {year_str}",
                vertical="sports-wagering",
                cadence="monthly",
                format="csv",
                source_url=tax_url,
            ))

            # --- Sport Detail (handle) ---
            detail_url = f"{ENDPOINT}?period={period}&rtype=sport-detail"
            self._post_params[detail_url] = (
                ENDPOINT,
                _build_sport_detail_post(month_name, year_str, viewstate),
            )
            reports.append(ReportFile(
                operator=f"IL Statewide Sport Detail {month_name} {year_str}",
                vertical="sports-wagering",
                cadence="monthly",
                format="csv",
                source_url=detail_url,
            ))

        return reports

    # ------------------------------------------------------------------
    # _download_one override — POST instead of GET
    # ------------------------------------------------------------------

    async def _download_one(
        self, client: httpx.AsyncClient, report: ReportFile, cache_dir: Path
    ) -> None:
        """Override base GET with a two-step WebForms POST.

        1. GET the endpoint to obtain a fresh __VIEWSTATE (MAC-protected and
           session-scoped — must be refreshed per request).
        2. POST with the month-specific payload and stream the CSV to disk.
        """
        if report.source_url not in self._post_params:
            report.fetch_error = "no POST params registered for this URL"
            return

        real_url, base_post = self._post_params[report.source_url]

        async with self.semaphore:
            for attempt in range(3):
                try:
                    # Step 1: refresh ViewState.
                    vs_resp = await client.get(real_url)
                    vs_resp.raise_for_status()
                    fresh_vs = _extract_viewstate(vs_resp.text)

                    post_data = dict(base_post)
                    post_data.update(fresh_vs)

                    # Step 2: POST and stream CSV.
                    sha = hashlib.sha256()
                    size = 0
                    target = cache_dir / self._safe_name(report)

                    async with client.stream("POST", real_url, data=post_data) as resp:
                        if resp.status_code in RETRYABLE_STATUS:
                            await asyncio.sleep(2 ** attempt)
                            continue
                        resp.raise_for_status()
                        with target.open("wb") as fp:
                            async for chunk in resp.aiter_bytes(64 * 1024):
                                sha.update(chunk)
                                size += len(chunk)
                                fp.write(chunk)

                    report.local_path = str(target.relative_to(self.output_root))
                    report.sha256 = sha.hexdigest()
                    report.size_bytes = size
                    report.retrieved_at = datetime.now(timezone.utc)

                    try:
                        report.summary = await self.parse(report, target)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("IL: parse failed for %s: %s", report.operator, exc)

                    return

                except (httpx.HTTPError, OSError) as exc:
                    log.warning(
                        "IL: download attempt %d failed for %s: %s",
                        attempt, report.operator, exc,
                    )
                    await asyncio.sleep(2 ** attempt)

            report.fetch_error = "download failed after 3 attempts"
