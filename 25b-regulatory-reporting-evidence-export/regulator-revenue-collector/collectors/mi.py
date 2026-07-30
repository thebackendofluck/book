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
Michigan Gaming Control Board (MGCB).

The michigan.gov news pages are Akamai-fronted and return HTTP 403 to
headless clients.  The MGCB publishes monthly revenue figures as
GovDelivery email bulletins; the HTML version of each bulletin is
publicly accessible at:

    https://content.govdelivery.com/accounts/MIGCB/bulletins/<hexid>

Bulletin title: "Michigan iGaming, online sports betting operators report
                 $313M in February revenue"
Bulletin sent:  "03/17/2026 10:44 AM EDT"  (month-after-data; February
                 revenue is announced in March)

Discovery:  We probe the GovDelivery public RSS/atom feed for MIGCB and
fall back to a hardcoded seed list of recent bulletin hex IDs.  The feed
URL is tried first because it requires no authentication.

PRIMARY DATA SOURCE — Excel attachments:
  Each bulletin also attaches two Excel files that contain the complete
  per-operator monthly history for the current calendar year PLUS the
  prior year on a second sheet.  The two most-recently confirmed files are:

    Internet Gaming - December 2024.xlsx   (iGaming, 2023+2024)
    Internet Sports Betting - December 2024.xlsx  (Sports, 2023+2024)
    Internet Gaming - February 2026.xlsx   (iGaming, 2025+2026)
    Internet Sports Betting - February 2026.xlsx  (Sports, 2025+2026)

  These files are available at:
    https://content.govdelivery.com/attachments/MIGCB/2025/01/21/
      file_attachments/3138414/Internet%20Gaming%20-%20December%202024.xlsx
    https://content.govdelivery.com/attachments/MIGCB/2025/01/21/
      file_attachments/3138413/Internet%20Sports%20Betting%20-%20December%202024.xlsx
    https://content.govdelivery.com/attachments/MIGCB/2026/03/17/
      file_attachments/3586362/Internet%20Gaming%20-%20February%202026.xlsx
    https://content.govdelivery.com/attachments/MIGCB/2026/03/17/
      file_attachments/3586360/Internet%20Sports%20Betting%20-%20February%202026.xlsx

  The backfill function (backfill_mi_excel in backfill.py) downloads these
  Excel files directly and parses all monthly rows.  The GovDelivery bulletin
  IDs for 2025 are not guessable by numeric probe — the Excel download path
  is the reliable route for covering the 2025 gap.

  When a new bulletin is published, add the new Excel attachment URLs to
  _EXCEL_SEED_URLS and the bulletin hex ID to _SEED_IDS.

Each bulletin URL is returned as a ReportFile with format='html'; the
parser (parsers/mi_metrics.py) extracts both the iGaming and
sports-wagering GGR rows directly from the bulletin text.
"""
from __future__ import annotations

import re
from datetime import date

import httpx
from bs4 import BeautifulSoup

from .base import StateCollector
from models import ReportFile

# GovDelivery public RSS feed for MIGCB (no auth required)
_RSS_URL = "https://public.govdelivery.com/accounts/MIGCB/subscriber/new"
_BULLETIN_BASE = "https://content.govdelivery.com/accounts/MIGCB/bulletins/"

# Bulletins are identified by a 7-char lowercase hex string.
# Seed list covers approximately the last 24 months (added newest-first).
# Extend this list whenever a new bulletin is published; the live
# discovery path via search will also pick up new ones automatically.
_SEED_IDS: list[str] = [
    # 2026
    "40eafc0",   # Feb 2026 — sent 2026-03-17
    # 2025 (add confirmed IDs here as they become available)
    "3cde4f7",   # Dec 2024 — sent 2025-01-21
    "3c2c22f",   # Oct 2024 — sent 2024-11-19
    "3bcbac7",   # Sep 2024 — sent ~2024-10
    "385ac56",   # Dec 2023 — sent ~2024-01
    "38bb978",   # Jan 2024 — sent ~2024-02
    "37c2524",   # Oct 2023 — sent ~2023-11
    "371393c",   # Aug 2023 — sent ~2023-09
]

# Primary Excel attachments from confirmed GovDelivery bulletin deliveries.
# Each tuple: (url, label, vertical_hint) where vertical_hint is 'igaming'
# or 'sports-wagering' (used only for the ReportFile; the parser emits facts
# for both verticals from each bulletin's HTML, but the Excel files are
# vertical-specific).
# These cover the full 2023–2026-02 monthly history and fill the gap that
# bulletin-ID-based discovery cannot reach for 2025.
_EXCEL_SEED_URLS: list[tuple[str, str, str]] = [
    # Internet Gaming Excel files (iGaming monthly by operator)
    (
        "https://content.govdelivery.com/attachments/MIGCB/2025/01/21"
        "/file_attachments/3138414/Internet%20Gaming%20-%20December%202024.xlsx",
        "MI iGaming Dec-2024 (2023+2024 data)",
        "igaming",
    ),
    (
        "https://content.govdelivery.com/attachments/MIGCB/2026/03/17"
        "/file_attachments/3586362/Internet%20Gaming%20-%20February%202026.xlsx",
        "MI iGaming Feb-2026 (2025+2026 data)",
        "igaming",
    ),
    # Internet Sports Betting Excel files (sports-wagering monthly by operator)
    (
        "https://content.govdelivery.com/attachments/MIGCB/2025/01/21"
        "/file_attachments/3138413/Internet%20Sports%20Betting%20-%20December%202024.xlsx",
        "MI Sports Dec-2024 (2023+2024 data)",
        "sports-wagering",
    ),
    (
        "https://content.govdelivery.com/attachments/MIGCB/2026/03/17"
        "/file_attachments/3586360/Internet%20Sports%20Betting%20-%20February%202026.xlsx",
        "MI Sports Feb-2026 (2025+2026 data)",
        "sports-wagering",
    ),
]

# Only match iGaming revenue bulletins (not Detroit casino or other releases)
_IGAMING_TITLE_RE = re.compile(
    r"igaming.*(?:sports\s+bett|online\s+sports)|"
    r"(?:sports\s+bett|online\s+sports).*igaming",
    re.I,
)

UA_BROWSER = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _bulletin_url(hex_id: str) -> str:
    return f"{_BULLETIN_BASE}{hex_id}"


def _is_igaming_bulletin(title: str) -> bool:
    """Return True if this looks like a monthly iGaming revenue release."""
    return bool(_IGAMING_TITLE_RE.search(title))


class MichiganCollector(StateCollector):
    state = "MI"
    regulator = "Michigan Gaming Control Board"
    source_url = "https://content.govdelivery.com/accounts/MIGCB/bulletins/"

    async def list_reports(self, client: httpx.AsyncClient) -> list[ReportFile]:
        out: list[ReportFile] = []
        seen: set[str] = set()

        # NOTE: The confirmed Excel attachment files (_EXCEL_SEED_URLS) are
        # downloaded and parsed by backfill_mi_excel() in backfill.py, NOT by
        # this collector's standard HTML-bulletin parse path.  Omitting them
        # here prevents the HTML parser from being invoked on XLSX files,
        # which produces harmless-but-noisy "could not determine data period"
        # warnings.  The Excel backfill is called from backfill.py --state MI.

        # 1. Attempt live discovery via GovDelivery search/feed
        discovered_ids = await self._discover_bulletin_ids(client)

        # 2. Merge with seed list (seeds fill gaps when discovery fails)
        all_ids = list(dict.fromkeys(discovered_ids + _SEED_IDS))

        for hex_id in all_ids:
            url = _bulletin_url(hex_id)
            if url in seen:
                continue
            seen.add(url)

            # Probe the bulletin to confirm it is an iGaming revenue release
            # and to extract the data-period label for the ReportFile operator.
            try:
                resp = await client.get(url, headers=UA_BROWSER, timeout=20)
            except httpx.HTTPError:
                # Network / timeout — include the seed anyway; parser handles
                # the case where the bulletin cannot be fetched at parse time.
                out.append(self._make_report(url, hex_id))
                continue

            if resp.status_code == 403:
                # GovDelivery occasionally rate-limits; include from seed list
                # anyway so the backfill can retry later.
                out.append(self._make_report(url, hex_id))
                continue

            if resp.status_code != 200:
                continue

            # Parse title from HTML
            title = _extract_title(resp.text)
            if title and not _is_igaming_bulletin(title):
                continue  # Skip unrelated bulletins (Detroit casino, etc.)

            out.append(self._make_report(url, hex_id, title=title))

        return out

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _discover_bulletin_ids(self, client: httpx.AsyncClient) -> list[str]:
        """Try to discover recent bulletin hex IDs via GovDelivery RSS/Atom."""
        ids: list[str] = []
        feeds = [
            "https://public.govdelivery.com/accounts/MIGCB/topics.rss",
            "https://content.govdelivery.com/accounts/MIGCB/bulletins.rss",
        ]
        hex_re = re.compile(r"/bulletins/([0-9a-f]{6,9})", re.I)
        for feed_url in feeds:
            try:
                resp = await client.get(feed_url, headers=UA_BROWSER, timeout=15)
                if resp.status_code != 200:
                    continue
                for m in hex_re.finditer(resp.text):
                    ids.append(m.group(1).lower())
                if ids:
                    break
            except httpx.HTTPError:
                continue
        return ids

    @staticmethod
    def _make_report(url: str, hex_id: str, *, title: str = "") -> ReportFile:
        operator = f"MI Statewide ({hex_id})"
        if title:
            # e.g. "Michigan iGaming … report $313M in February revenue"
            m = re.search(
                r"\b(january|february|march|april|may|june|july|august|"
                r"september|october|november|december)\b",
                title, re.I,
            )
            if m:
                operator = f"MI Statewide ({m.group(1).title()} bulletin)"
        return ReportFile(
            operator=operator,
            vertical="igaming",          # parser emits both igaming + sports-wagering
            cadence="monthly",
            format="html",
            source_url=url,
        )


def _extract_title(html: str) -> str:
    """Return the bulletin subject/title from its HTML."""
    soup = BeautifulSoup(html, "html.parser")
    # GovDelivery puts the subject in <title> and/or the first <h1>/<h2>
    h = soup.find("h1") or soup.find("h2") or soup.find("title")
    return h.get_text(" ", strip=True) if h else ""
