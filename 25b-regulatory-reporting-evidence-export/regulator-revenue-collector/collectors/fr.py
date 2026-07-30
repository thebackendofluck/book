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
France Autorité Nationale des Jeux (ANJ) market data.

Two complementary sources:

1. data.gouv.fr CSV/XLSX (2010–present annual timeseries)
   ANJ exports the consolidated dataset to data.gouv.fr (single XLSX/CSV
   covering paris sportifs + paris hippiques + poker en ligne; 2010 →
   present). FR does NOT permit online casino (RNG slots/table games).

   We resolve the dataset via the ANJ organisation endpoint rather than a
   hardcoded slug, so that slug rotations (e.g. "…-2010-a-2024" →
   "…-2010-a-2025" when the 2025 annual data is published ~Dec 2026) are
   handled automatically without code changes.

2. anj.fr PDF reports (bilan économique + rapport semestriel)
   Since 2025 ANJ switched from quarterly to semiannual PDF reports.
   Hardcoded known PDFs are always included; the rapports index page is
   also scraped to auto-discover future editions.

   Known PDFs:
     - FY2025 annual bilan: https://anj.fr/sites/default/files/2026-04/Bilan%20%C3%A9conomique%202025.pdf
     - H1 2025 semiannual:  https://anj.fr/sites/default/files/2025-10/Rapport%20semestriel%202025.pdf
"""
from __future__ import annotations

import re

import httpx
from bs4 import BeautifulSoup

from .base import StateCollector
from models import ReportFile

# Org-level listing: survives dataset-slug rotations; search for the
# market-data dataset by its stable title substring.
ORG_URL = (
    "https://www.data.gouv.fr/api/1/organizations/"
    "autorite-nationale-des-jeux/datasets/?page_size=50"
)
# Substring that appears in every annual-market-data slug published by ANJ.
_MARKET_SLUG_FRAGMENT = "marche-des-jeux-en-ligne"
VERTICALS = ("sports-wagering", "horse-racing", "online-poker")
UA_HEADERS = {"User-Agent": "Mozilla/5.0 RegulatorRevenueCollector/1.0"}

# ANJ reports index page for auto-discovery of future PDF editions.
ANJ_REPORTS_INDEX = "https://anj.fr/rapports"
ANJ_BASE = "https://anj.fr"

# Hardcoded PDFs for editions that are known to exist.
# anj.fr is Cloudflare-protected and blocks non-browser clients, so we use
# Wayback Machine raw-bytes URLs (id_ suffix) which bypass the WAF.
# Wayback archive timestamps confirmed 2026-06-09.
_HARDCODED_PDFS: list[tuple[str, str, str, str]] = [
    # (wayback_url, original_url_for_year_detection, cadence, description)
    (
        # Archived 2026-05-19 13:25:35
        "https://web.archive.org/web/20260519132535id_/"
        "https://anj.fr/sites/default/files/2026-04/Bilan%20%C3%A9conomique%202025.pdf",
        "https://anj.fr/sites/default/files/2026-04/Bilan%20%C3%A9conomique%202025.pdf",
        "annual",
        "Bilan économique 2025",
    ),
    (
        # Archived 2025-10-09 10:57:31 (earliest — closest to publication)
        "https://web.archive.org/web/20251009105731id_/"
        "https://anj.fr/sites/default/files/2025-10/Rapport%20semestriel%202025.pdf",
        "https://anj.fr/sites/default/files/2025-10/Rapport%20semestriel%202025.pdf",
        "semi-annual",
        "Rapport semestriel H1 2025",
    ),
]

# Match PDF link text for ANJ annual bilan or semiannual rapport
_BILAN_RE = re.compile(r"bilan\s+économique\s+(\d{4})", re.IGNORECASE)
_SEMESTRI_RE = re.compile(r"rapport\s+semestriel\s+(\d{4})", re.IGNORECASE)


class FranceCollector(StateCollector):
    state = "FR"
    regulator = "Autorité Nationale des Jeux (ANJ)"
    source_url = "https://anj.fr/offre-de-jeu-et-marche/le-marche"

    async def list_reports(self, client: httpx.AsyncClient) -> list[ReportFile]:
        out: list[ReportFile] = []

        # ── 1. data.gouv.fr CSV/XLSX timeseries ──────────────────────────────
        try:
            r = await client.get(ORG_URL, headers=UA_HEADERS,
                                 follow_redirects=True, timeout=30)
            r.raise_for_status()
            org = r.json()
        except (httpx.HTTPError, ValueError):
            org = {}

        meta = next(
            (d for d in org.get("data", [])
             if _MARKET_SLUG_FRAGMENT in (d.get("slug") or "")),
            {},
        )
        for res in meta.get("resources", []):
            fmt = (res.get("format") or "").lower()
            if fmt not in {"csv", "xlsx"}:
                continue
            url = res.get("url")
            if not url:
                continue
            for v in VERTICALS:
                out.append(ReportFile(
                    operator="FR Statewide",
                    vertical=v,
                    cadence="quarterly",  # data is per-quarter inside the file
                    format=fmt,
                    source_url=url,
                ))

        # ── 2. Hardcoded ANJ PDFs (bilan + semestriel) ───────────────────────
        # anj.fr is Cloudflare-protected; download via Wayback Machine id_ URL.
        # Track the original (non-Wayback) URL so the auto-discover step can
        # deduplicate against the same canonical PDF path.
        seen_wayback_urls: set[str] = set()
        seen_orig_urls: set[str] = set()
        for wayback_url, orig_url, cadence, _desc in _HARDCODED_PDFS:
            seen_wayback_urls.add(wayback_url)
            seen_orig_urls.add(orig_url)
            out.append(ReportFile(
                operator="FR Statewide",
                vertical="sports-wagering",   # placeholder; parser handles all verticals
                cadence=cadence,
                format="pdf",
                source_url=wayback_url,
            ))

        # ── 3. Auto-discover from anj.fr/rapports index ───────────────────────
        try:
            r2 = await self.fetch(client, "GET", ANJ_REPORTS_INDEX,
                                  headers=UA_HEADERS,
                                  follow_redirects=True, timeout=30)
            if r2.status_code == 200:
                soup = BeautifulSoup(r2.text, "html.parser")
                for a in soup.find_all("a", href=True):
                    href: str = a["href"]
                    if not href.lower().endswith(".pdf"):
                        continue
                    text = a.get_text(" ", strip=True)
                    full = href if href.startswith("http") else ANJ_BASE + href
                    if full in seen_orig_urls or full in seen_wayback_urls:
                        continue
                    # Determine cadence from link text
                    if _BILAN_RE.search(text):
                        cadence = "annual"
                    elif _SEMESTRI_RE.search(text):
                        cadence = "semi-annual"
                    else:
                        continue  # not a market-data PDF we care about
                    seen_orig_urls.add(full)
                    out.append(ReportFile(
                        operator="FR Statewide",
                        vertical="sports-wagering",  # placeholder; parser reads all
                        cadence=cadence,
                        format="pdf",
                        source_url=full,
                    ))
        except (httpx.HTTPError, Exception):  # noqa: BLE001
            pass  # index scrape is best-effort; hardcoded PDFs already included

        return out
