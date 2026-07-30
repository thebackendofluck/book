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
Connecticut Department of Consumer Protection — Online Gaming.

CT migrated monthly online-casino and sports-wagering reporting from
PDF/XLSX downloads to Socrata datasets on data.ct.gov. Each row is one
licensee per month; we discover the (licensee, month) pairs via the
JSON API and emit a ReportFile pointing at the .csv variant so the
downstream parser still works on a downloadable file.

Two licensees per vertical:
  Mohegan Tribe / FanDuel    (MPI Master Wagering License CT, LLC)
  Mashantucket Pequot / DraftKings
"""
from __future__ import annotations

import httpx

from .base import StateCollector
from models import ReportFile

DATASETS = [
    ("xf6g-659c", "sports-wagering"),  # Selected Online Sport Wagering Data
    ("imqd-at3c", "igaming"),          # Selected Online Casino Gaming Data
]


class ConnecticutCollector(StateCollector):
    state = "CT"
    regulator = "CT Department of Consumer Protection"
    source_url = "https://portal.ct.gov/dcp/gaming-division/gaming/gaming-revenue-and-statistics"

    async def list_reports(self, client: httpx.AsyncClient) -> list[ReportFile]:
        out: list[ReportFile] = []
        for dataset_id, vertical in DATASETS:
            url = f"https://data.ct.gov/resource/{dataset_id}.json"
            try:
                res = await client.get(url, params={
                    "$select": "licensee, month_ending",
                    "$order": "month_ending DESC",
                    "$limit": 200,
                })
                res.raise_for_status()
                rows = res.json()
            except (httpx.HTTPError, ValueError):
                continue
            seen: set[tuple[str, str]] = set()
            for row in rows:
                lic = (row.get("licensee") or "").strip()
                month_full = row.get("month_ending") or ""
                month = month_full[:7]
                if not lic or not month or (lic, month) in seen:
                    continue
                seen.add((lic, month))
                # Build a CSV download URL filtered to this (licensee, month).
                csv_url = (
                    f"https://data.ct.gov/resource/{dataset_id}.csv"
                    f"?$where=" + httpx.QueryParams(
                        {"$where": f"licensee='{lic.replace(chr(39), chr(39)*2)}' "
                                   f"AND month_ending='{month_full}'"}
                    )["$where"].replace(" ", "%20")
                )
                out.append(ReportFile(
                    operator=f"CT {lic} ({month})"[:120],
                    vertical=vertical,
                    cadence="monthly",
                    format="csv",
                    source_url=csv_url,
                ))
                if len(out) >= 80:
                    break
        return out
