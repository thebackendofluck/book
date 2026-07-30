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
Netherlands Kansspelautoriteit (KSA) market reports.

KSA publishes annual Marktscan + semi-annual Monitoringsrapportage online
kansspelen as PDFs.  The legacy /over-ons/publicaties/onderzoek/ index
returns HTTP 403 (bot-blocked as of 2026).  The live research page is at
/ons-onderzoek-0 and accessible.  As a resilience measure the collector
always seeds from a hardcoded known-URL list, then supplements with any
new PDFs discovered via a live scrape of /ons-onderzoek-0.

Only the online market reports are retained:
  - marktscan kansspelen {YYYY}   → annual, igaming + sports-wagering
  - monitoringsrapportage online kansspelen → semi-annual, igaming + sports-wagering
Land-based-only marktscans (filename contains "landgebonden") are excluded
because the parser (nl_metrics.py) covers only online verticals.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .base import StateCollector
from models import ReportFile

# /ons-onderzoek-0 is the accessible research index (returns 200).
# /over-ons/publicaties/onderzoek/ returns 403 — do NOT use it.
RESEARCH_PAGE = "https://kansspelautoriteit.nl/ons-onderzoek-0"
KSA_BASE = "https://kansspelautoriteit.nl"

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
UA_HEADERS = {
    "User-Agent": _BROWSER_UA,
    "Referer": KSA_BASE + "/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
}

PDF_RE = re.compile(r"\.pdf$", re.I)
# Matches the online marktscan series (excludes landgebonden-only scans).
MARKTSCAN_ONLINE_RE = re.compile(
    r"marktscan(?:_kansspelen)?[_\-\s]\d{4}\.pdf", re.I
)
# 20251121_ksa_marktscan_kansspelen_2025.pdf  (dated variant)
MARKTSCAN_DATED_RE = re.compile(
    r"\d{8}_ksa_marktscan_kansspelen_\d{4}\.pdf", re.I
)
# marktscan_2024.pdf  (short variant used up to 2024 edition)
MARKTSCAN_SHORT_RE = re.compile(r"marktscan_\d{4}\.pdf", re.I)
# marktscan.pdf  (2023 generic slug)
MARKTSCAN_GENERIC_RE = re.compile(r"/marktscan\.pdf$", re.I)
MONITOR_RE = re.compile(r"monitoringsrapportage", re.I)
LANDGEBONDEN_RE = re.compile(r"landgebonden", re.I)

# Hardcoded known-good report URLs (confirmed accessible 2026-06-08).
# These seeds ensure the collector never returns [] even if the live scrape fails.
# Format: (url, cadence)
_KNOWN_REPORTS: list[tuple[str, str]] = [
    # --- Marktscan annual series ---
    # Marktscan 2025 (covers 2024 data), dated filename
    (
        "https://kansspelautoriteit.nl/sites/default/files/"
        "20251121_ksa_marktscan_kansspelen_2025.pdf",
        "annual",
    ),
    # Marktscan 2024 (covers 2023 data)
    (
        "https://kansspelautoriteit.nl/sites/default/files/marktscan_2024.pdf",
        "annual",
    ),
    # Marktscan 2023 (covers 2022 data) — generic slug
    (
        "https://kansspelautoriteit.nl/sites/default/files/marktscan.pdf",
        "annual",
    ),
    # --- Monitoringsrapportage semi-annual series (online only) ---
    # Voorjaar 2026 (covers H2 2025)
    (
        "https://kansspelautoriteit.nl/sites/default/files/2026-04/"
        "Monitoringsrapportage%20online%20kansspelen%20voorjaar%202026.pdf",
        "semi-annual",
    ),
    # Najaar 2025 (covers H1 2025)
    (
        "https://kansspelautoriteit.nl/sites/default/files/"
        "monitoringsrapportage_najaar_2025_1.pdf",
        "semi-annual",
    ),
    # Voorjaar 2025 (covers H2 2024)
    (
        "https://kansspelautoriteit.nl/sites/default/files/"
        "monitoringsrapportage_online_kansspelen_voorjaar_2025.pdf",
        "semi-annual",
    ),
    # Najaar 2024 (covers H1 2024)
    (
        "https://kansspelautoriteit.nl/sites/default/files/"
        "monitoringsrapportage_najaar_2024.pdf",
        "semi-annual",
    ),
    # Voorjaar 2024 (covers H2 2023)
    (
        "https://kansspelautoriteit.nl/sites/default/files/"
        "monitoringsrapportage_voorjaar_2024.pdf",
        "semi-annual",
    ),
    # Najaar 2023 (covers H1 2023)
    (
        "https://kansspelautoriteit.nl/sites/default/files/"
        "monitoringsrapportage_online_kansspelen_najaar_2023_1.pdf",
        "semi-annual",
    ),
    # Voorjaar 2023 (covers H2 2022)
    (
        "https://kansspelautoriteit.nl/sites/default/files/"
        "monitoringsrapportage_online_kansspelen_voorjaar_2023_1.pdf",
        "semi-annual",
    ),
    # Najaar 2022 (covers H1 2022)
    (
        "https://kansspelautoriteit.nl/sites/default/files/"
        "monitoringsrapportage_online_kansspelen_najaar_2022.pdf",
        "semi-annual",
    ),
    # Post-KOA launch (covers H2 2021, first monitor after market opened Oct 2021)
    (
        "https://kansspelautoriteit.nl/sites/default/files/"
        "monitoringsrapportage_na_koa.pdf",
        "semi-annual",
    ),
]


def _is_online_marktscan(href: str) -> bool:
    """True when the href points to an online-market marktscan PDF (not land-based)."""
    filename = href.rsplit("/", 1)[-1]
    if LANDGEBONDEN_RE.search(filename):
        return False
    return bool(
        MARKTSCAN_DATED_RE.search(filename)
        or MARKTSCAN_SHORT_RE.search(filename)
        or MARKTSCAN_GENERIC_RE.search(href)
        or MARKTSCAN_ONLINE_RE.search(filename)
    )


def _cadence_from_href(href: str) -> str:
    """Return 'annual' or 'semi-annual' from URL path."""
    filename = href.rsplit("/", 1)[-1].lower()
    if MONITOR_RE.search(filename):
        return "semi-annual"
    return "annual"


class NetherlandsCollector(StateCollector):
    state = "NL"
    regulator = "Kansspelautoriteit (KSA)"
    source_url = RESEARCH_PAGE

    # KSA CDN filters User-Agent on PDF downloads.
    _download_headers: dict[str, str] = UA_HEADERS

    async def list_reports(self, client: httpx.AsyncClient) -> list[ReportFile]:
        """Return KSA online-market PDFs (marktscan + monitoringsrapportage).

        Seeds from the hardcoded known-URL list for resilience, then appends
        any newly-published PDFs discovered via live scrape of /ons-onderzoek-0.
        """
        # Always start with the known-good hardcoded seeds.
        seen: set[str] = set()
        out: list[ReportFile] = []

        for url, cadence in _KNOWN_REPORTS:
            seen.add(url)
            out.append(ReportFile(
                operator="NL Statewide",
                vertical="igaming",   # parser emits both igaming + sports-wagering
                cadence=cadence,
                format="pdf",
                source_url=url,
            ))

        # Supplement with live scrape — absorb errors silently so hardcoded
        # seeds still run if the page is temporarily unavailable.
        try:
            r = await client.get(
                RESEARCH_PAGE, headers=UA_HEADERS,
                follow_redirects=True, timeout=30.0,
            )
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href: str = a["href"]
                if not PDF_RE.search(href):
                    continue
                # Skip factsheet / bijlage / infographic sidecars
                fname = href.rsplit("/", 1)[-1].lower()
                if any(k in fname for k in ("factsheet", "bijlage", "infographic")):
                    continue
                if LANDGEBONDEN_RE.search(fname):
                    continue
                # Only retain marktscan or monitoringsrapportage
                if not (
                    _is_online_marktscan(href) or MONITOR_RE.search(fname)
                ):
                    continue
                url = href if href.startswith("http") else urljoin(KSA_BASE, href)
                if url in seen:
                    continue
                seen.add(url)
                cadence = _cadence_from_href(href)
                out.append(ReportFile(
                    operator="NL Statewide",
                    vertical="igaming",
                    cadence=cadence,
                    format="pdf",
                    source_url=url,
                ))
        except httpx.HTTPError:
            pass  # hardcoded seeds remain in `out`

        return out
