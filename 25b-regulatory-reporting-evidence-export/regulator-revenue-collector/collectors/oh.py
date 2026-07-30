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
Ohio Casino Control Commission sports-gaming monthly revenue.

OCCC has no static HTML index -- its site is React-rendered. Reports live
on the Ohio CDN at dam.assets.ohio.gov under predictable paths.

URL patterns used by OCCC:

  2023:
    - Per-month XLSX under .../revenue-reports/2023/
    - December 2023 PDF under .../revenue-reports/Full%20Year%20Revenue%20Reports/

  2024:
    - Annual cumulative PDF under .../revenue-reports/2024/ (versioned hash URL)
    - Some per-month XLSX files present (September 2024 known)

  2025:
    - Versionless (always-latest) annual PDF/XLSX at .../2025/Sports/ covering
      Jan-Aug 2025 (last updated Sep 30, 2025).
    - Sequential cumulative XLSXs for later months:
        2025_Sports_Gaming_Revenue_Report11.xlsx  (Jan-Nov)
        2025_Sports_Gaming_Revenue_Report12.xlsx  (Jan-Dec full-year, $1.039B)
    - Files 09 and 10 do not exist; 11 covers through November; 12 is full-year.

  2026:
    - Sequential cumulative XLSXs (01=Jan, 02=Feb, ...):
        2026_Sports_Gaming_Revenue_Report01.xlsx through 04.xlsx confirmed.
    - Probed versionlessly; new months added via _annual_2026_sequential().

Mobile sports launched 2023-01-01 in Ohio.
"""
from __future__ import annotations

import calendar
from datetime import date
from urllib.parse import quote

import httpx

from .base import StateCollector
from models import ReportFile

CDN = "https://dam.assets.ohio.gov"

# Known annual / cumulative files with stable URLs.
# Format: (url, fmt)
_ANNUAL_REPORTS: list[tuple[str, str]] = [
    # Dec 2023 per-month PDF (legacy path under Full Year Revenue Reports)
    (
        "https://dam.assets.ohio.gov/image/upload/"
        "casinocontrol.ohio.gov/revenue-reports/"
        "Full%20Year%20Revenue%20Reports/"
        "December%202023%20Ohio%20Sports%20Gaming%20Revenue%20Report.pdf",
        "pdf",
    ),
    # 2024 full-year cumulative PDF (versioned; stable)
    (
        "https://dam.assets.ohio.gov/image/upload/v1732715535/"
        "casinocontrol.ohio.gov/revenue-reports/2024/"
        "2024_Sports_Gaming_Revenue_Report.pdf.pdf",
        "pdf",
    ),
    # 2025 Jan-Aug cumulative XLSX (versionless; Sep 30 2025 snapshot)
    (
        "https://dam.assets.ohio.gov/raw/upload/"
        "casinocontrol.ohio.gov/revenue-reports/2025/Sports/"
        "2025_Sports_Gaming_Revenue_Report.xlsx",
        "xlsx",
    ),
    # 2025 Jan-Nov cumulative XLSX
    (
        "https://dam.assets.ohio.gov/raw/upload/"
        "casinocontrol.ohio.gov/revenue-reports/2025/Sports/"
        "2025_Sports_Gaming_Revenue_Report11.xlsx",
        "xlsx",
    ),
    # 2025 full-year (Jan-Dec) cumulative XLSX ($1.039B statewide total)
    (
        "https://dam.assets.ohio.gov/raw/upload/"
        "casinocontrol.ohio.gov/revenue-reports/2025/Sports/"
        "2025_Sports_Gaming_Revenue_Report12.xlsx",
        "xlsx",
    ),
    # 2026 Jan cumulative XLSX (versioned; stable)
    (
        "https://dam.assets.ohio.gov/raw/upload/v1772208645/"
        "casinocontrol.ohio.gov/revenue-reports/2026/Sports/"
        "2026_Sports_Gaming_Revenue_Report01.xlsx",
        "xlsx",
    ),
]

# Versionless (always-latest) annual URLs to discover any newer uploads.
# The Cloudinary CDN serves the current asset regardless of the version
# hash in the path, so omitting the version picks up re-uploaded files.
_ANNUAL_LATEST: list[tuple[str, str]] = [
    (
        "https://dam.assets.ohio.gov/image/upload/"
        "casinocontrol.ohio.gov/revenue-reports/2025/Sports/"
        "2025_Sports_Gaming_Revenue_Report.pdf",
        "pdf",
    ),
    (
        "https://dam.assets.ohio.gov/raw/upload/"
        "casinocontrol.ohio.gov/revenue-reports/2026/Sports/"
        "2026_Sports_Gaming_Revenue_Report01.xlsx",
        "xlsx",
    ),
]

# 2026 sequential cumulative XLSXs: probe months 02 onward versionlessly.
# OCCC publishes 2026_Sports_Gaming_Revenue_ReportNN.xlsx where NN = zero-padded
# month number (01=Jan, 02=Feb, ...).  Each file is cumulative (contains all
# months from January through NN).  We probe until the first 404.
_BASE_2026 = (
    "https://dam.assets.ohio.gov/raw/upload/"
    "casinocontrol.ohio.gov/revenue-reports/2026/Sports/"
    "2026_Sports_Gaming_Revenue_Report{:02d}.xlsx"
)


def _candidates(year: int, month: int, fmt: str) -> list[str]:
    name = calendar.month_name[month]
    base = f"{CDN}/raw/upload" if fmt == "xlsx" else f"{CDN}/image/upload"
    return [
        f"{base}/casinocontrol.ohio.gov/revenue-reports/{year}/{name}_{year}_Ohio_Sports_Gaming_Revenue_Report.{fmt}",
        f"{base}/casinocontrol.ohio.gov/revenue-reports/{year}/{name}_{year}_Sports_Gaming_Revenue_Report.{fmt}",
        f"{base}/casinocontrol.ohio.gov/revenue-reports/{year}/Sports/{name}_{year}_Sports_Gaming_Revenue_Report.{fmt}",
        f"{base}/casinocontrol.ohio.gov/revenue-reports/{year}/"
        + quote(f"{name} {year} Ohio Sports Gaming Revenue Report") + f".{fmt}",
        f"{base}/casinocontrol.ohio.gov/revenue-reports/{year}/"
        + quote(f"{name} {year} Sports Gaming Revenue Report") + f".{fmt}",
    ]


class OhioCollector(StateCollector):
    state = "OH"
    regulator = "Ohio Casino Control Commission"
    source_url = "https://casinocontrol.ohio.gov/data-and-reports"

    async def list_reports(self, client: httpx.AsyncClient) -> list[ReportFile]:
        out: list[ReportFile] = []
        seen: set[str] = set()

        def _add(url: str, fmt: str) -> None:
            seen.add(url)
            out.append(ReportFile(
                operator="OH All Operators",
                vertical="sports-wagering",
                cadence="monthly",
                format=fmt,
                source_url=url,
            ))

        # 1. Hardcoded annual / cumulative files (stable or known-good URLs).
        for url, fmt in _ANNUAL_REPORTS:
            if url in seen:
                continue
            try:
                r = await client.head(url, follow_redirects=True)
            except httpx.HTTPError:
                continue
            if r.status_code != 200:
                continue
            _add(url, fmt)

        # 2. Versionless (always-latest) URLs -- discovers re-uploads that
        #    differ from the hardcoded version-hash entries above.
        for url, fmt in _ANNUAL_LATEST:
            if url in seen:
                continue
            try:
                r = await client.head(url, follow_redirects=True)
            except httpx.HTTPError:
                continue
            if r.status_code != 200:
                continue
            _add(url, fmt)

        # 3. 2026 sequential cumulative XLSXs: probe month 02 onward until 404.
        #    Month 01 is already covered by _ANNUAL_REPORTS (versioned) and
        #    _ANNUAL_LATEST.  We start at 02 and stop at the first missing file.
        for month_seq in range(2, 13):
            url = _BASE_2026.format(month_seq)
            if url in seen:
                continue
            try:
                r = await client.head(url, follow_redirects=True)
            except httpx.HTTPError:
                break
            if r.status_code != 200:
                break
            _add(url, "xlsx")

        # 4. Per-month probe fallback -- catches any surviving per-month files
        #    and the current year's not-yet-consolidated months.
        today = date.today()
        for year in range(2023, today.year + 1):
            last_month = 12 if year < today.year else max(1, today.month - 1)
            for m in range(1, last_month + 1):
                month_found = False
                for fmt in ("xlsx", "pdf"):
                    if month_found:
                        break
                    for url in _candidates(year, m, fmt):
                        if url in seen:
                            month_found = True
                            break
                        try:
                            r = await client.head(url, follow_redirects=True)
                        except httpx.HTTPError:
                            continue
                        if r.status_code != 200:
                            continue
                        _add(url, fmt)
                        month_found = True
                        break
        return out
