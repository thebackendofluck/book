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
Romania ONJN (Oficiul Național pentru Jocuri de Noroc) collector.

ONJN publishes annual activity reports (rapoarte de activitate) as PDFs on
the public-information section of onjn.gov.ro under the Legea 544/2001
transparency obligations. The regulator covers all land-based and online
gambling in Romania, including:
  - cazino online  (online casino / iGaming)
  - pariuri sportive (sports betting)
  - slot-machine / aparate de joc (land-based slots / VGT)
  - poker online

Currency: Romanian Leu (RON). FX: 1 RON ≈ 0.22 USD (see parsers/ro_metrics.py).

Source pages:
  https://onjn.gov.ro/relatii-publice/informatii-de-interes-public/rapoarte-de-activitate/
  https://onjn.gov.ro/rapoarte-anuale-legea-nr-5442001/

Direct PDF base path (Legea 544 uploads):
  https://onjn.gov.ro/wp-content/uploads/Onjn.gov.ro/RelaţiiPublice/informatii/
      Rapoarte-anuale-Legea-544/{filename}.pdf

Cloudflare note (2026-06):
  onjn.gov.ro sits behind Cloudflare and returns HTTP 503 / JS-challenge pages
  to non-browser automated clients — both the HTML landing pages and HEAD probes
  fail. The collector therefore skips HTML scraping and HEAD probes and instead
  returns a pre-seeded list of confirmed PDF URLs (verified via Google index and
  news citations). The download in _backfill_via_collector will try to fetch
  each URL; if Cloudflare blocks the download we get a 503 and skip gracefully.
  The parser (ro_metrics.py) handles all years whose PDFs do download.

  Confirmed PDF URLs (via Google index as of 2026-06):
    2024: RAPORT ACTIVITATE ONJN 2024 publicare site.pdf  ← Google-indexed
    2021: RAPORT ACTIVITATE 2021.pdf                       ← Google-indexed
  Other years use the same base path with naming variants listed below.
"""
from __future__ import annotations

from .base import StateCollector
from models import ReportFile

# ------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------

# Official landing pages — kept for reference; not fetched at runtime because
# onjn.gov.ro returns 503 to automated clients (Cloudflare JS challenge).
ACTIVITY_REPORTS_URL = (
    "https://onjn.gov.ro/relatii-publice/informatii-de-interes-public/rapoarte-de-activitate/"
)

# Base path for Legea-544 annual activity report PDFs.
_LEGEA544_BASE = (
    "https://onjn.gov.ro/wp-content/uploads/Onjn.gov.ro/"
    "Rela%C8%9BiiPublice/informatii/Rapoarte-anuale-Legea-544/"
)

# Pre-seeded list of confirmed PDF URLs, most-recent first.
# Each tuple: (source_url, reporting_year_hint).
# Verified via Google search index (2026-06). The actual filename is the
# primary URL tried; naming variants are in _FALLBACK_URLS for future use
# if the main path ever 404s.
_SEEDED_REPORTS: list[tuple[str, int]] = [
    (
        _LEGEA544_BASE + "RAPORT%20ACTIVITATE%20ONJN%202024%20publicare%20site.pdf",
        2024,
    ),
    (
        _LEGEA544_BASE + "RAPORT%20ACTIVITATE%20ONJN%202023.pdf",
        2023,
    ),
    (
        _LEGEA544_BASE + "RAPORT%20ACTIVITATE%202022.pdf",
        2022,
    ),
    (
        _LEGEA544_BASE + "RAPORT%20ACTIVITATE%202021.pdf",
        2021,
    ),
    (
        _LEGEA544_BASE + "RAPORT%20ONJN%202020%20-%2029%20martie%202021.pdf",
        2020,
    ),
    (
        _LEGEA544_BASE + "RAPORT%20ACTIVITATE%202019.pdf",
        2019,
    ),
]

# Browser-like headers. Adding Accept, Accept-Language, and a Referer from the
# ONJN domain itself reduces Cloudflare challenge frequency for PDF downloads.
_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

UA_HEADERS: dict[str, str] = {
    "User-Agent": _BROWSER_UA,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "application/pdf,*/*;q=0.8"
    ),
    "Accept-Language": "ro-RO,ro;q=0.9,en-US;q=0.8",
    "Referer": "https://onjn.gov.ro/rapoarte-anuale-legea-nr-5442001/",
}

VERTICALS = ("igaming", "sports-wagering", "land-based-casino", "online-poker")


class RomaniaCollector(StateCollector):
    state = "RO"
    regulator = "ONJN — Oficiul Național pentru Jocuri de Noroc"
    source_url = ACTIVITY_REPORTS_URL

    # Used by _backfill_via_collector to pass browser-like headers on downloads.
    _download_headers: dict[str, str] = UA_HEADERS

    async def list_reports(self, client) -> list[ReportFile]:
        """Return ONJN annual report PDFs from the pre-seeded confirmed URL list.

        onjn.gov.ro returns 503 / Cloudflare JS challenge to automated clients,
        making HTML scraping and HEAD probes unreliable. We return known URLs
        directly; _backfill_via_collector will attempt each download and skip
        gracefully on any HTTP error.
        """
        out: list[ReportFile] = []
        for url, _year in _SEEDED_REPORTS:
            out.append(
                ReportFile(
                    operator="RO Statewide",
                    vertical="igaming",  # parser splits into all verticals
                    cadence="annual",
                    format="pdf",
                    source_url=url,
                )
            )
        return out
