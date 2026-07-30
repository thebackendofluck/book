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
Portugal SRIJ (Serviço de Regulação e Inspeção de Jogos) collector.

SRIJ publishes quarterly statistical bulletins for online gambling at:
  https://www.srij.turismodeportugal.pt/pt/media/publicacoes-estatisticas

The page is JavaScript-rendered, so direct HTML scraping is not reliable.
This collector uses a hardcoded confirmed-URL table (verified 2026-06 via
Google search index) plus a forward-probe for quarters that postdate the list.

Confirmed upload-folder patterns (verified from Google-indexed titles):
  Quarter          Upload folder    Notes
  ─────────────────────────────────────────────────────────────────────
  Q1 2022          2022-06          estatisticas_jogo_online_1t_2022.pdf
  Q2 2022          2022-08          estatisticas_jogo_online_2t_2022.pdf
                   fotos/editor2    also mirrored at non-standard path
  Q3 2022          2022-11          estatisticas_online_3t_2022.pdf  (_jogo dropped)
  Q4 2022          2023-03          estatisticas_online_4t_2022.pdf
  Q1 2023          2023-06          estatisticas_online_1T_2023.pdf  (cap T)
  Q2 2023          2023-09          estatisticas_online_2t_2023.pdf
  Q3 2023          2024-01          estatisticas_online_3t_2023.pdf
  Q4 2023          2024-03          estatisticas_online_4t_2023.pdf
  Q1 2024          2024-05          estatisticas_online_1t_2024.pdf
  Q2 2024          2024-08          estatisticas_online_2t_2024.pdf
  Q3 2024          2024-12          estatisticas_online_3t_2024.pdf
  Q4 2024          2025-03          estatisticas_online_4t_2024.pdf
  Q1 2025          2025-06          estatisticas_online_1t_2025.pdf
  Q2 2025          2025-09          estatisticas_online_2t_2025.pdf
  Q3 2025          2026-01          estatisticas_online_3t_2025.pdf
  Q4 2025          2026-03          estatisticas_online_4t_2025.pdf
  Q1 2026          2026-06          estatisticas_online_1t_2026.pdf

Two online verticals are present in every bulletin since Q3 2022:
  - Apostas Desportivas à Cota / Apostas Desportivas  → sports-wagering
  - Jogos de Fortuna ou Azar / Casino Online           → igaming

Note: prior editions (pre-Q3 2022) also reported Póquer Online as a
separate vertical, but it has been folded into igaming in newer reports.

TLS: The SRIJ site has a recurring certificate-chain issue.  All HTTP
requests are routed through self.fetch() which will automatically retry
without TLS verification when the cert fails (gated by base.StateCollector).
"""
from __future__ import annotations

import re
from datetime import date

import httpx

from .base import StateCollector
from models import ReportFile

BASE_URL = "https://www.srij.turismodeportugal.pt"
LANDING_URL = f"{BASE_URL}/pt/media/publicacoes-estatisticas"
FILES_BASE = f"{BASE_URL}/sites/default/files"

# Both verticals emitted per quarterly PDF (parser resolves values from PDF tables)
VERTICALS = ("sports-wagering", "igaming")

UA_HEADERS = {"User-Agent": "Mozilla/5.0 RegulatorRevenueCollector/1.0"}

# ---------------------------------------------------------------------------
# Confirmed URL table  (quarter, year) → full URL
# Source: Google search index, verified 2026-06.
# ---------------------------------------------------------------------------

# Each entry: (quarter: int, year: int, url: str)
_KNOWN: list[tuple[int, int, str]] = [
    # 2022
    (1, 2022, f"{FILES_BASE}/2022-06/estatisticas_jogo_online_1t_2022.pdf"),
    (2, 2022, f"{FILES_BASE}/2022-08/estatisticas_jogo_online_2t_2022.pdf"),
    # Q3 2022 — _jogo dropped from filename; upload month inferred as 2022-11
    (3, 2022, f"{FILES_BASE}/2022-11/estatisticas_online_3t_2022.pdf"),
    (4, 2022, f"{FILES_BASE}/2023-03/estatisticas_online_4t_2022.pdf"),
    # 2023
    (1, 2023, f"{FILES_BASE}/2023-06/estatisticas_online_1T_2023.pdf"),
    (2, 2023, f"{FILES_BASE}/2023-09/estatisticas_online_2t_2023.pdf"),
    (3, 2023, f"{FILES_BASE}/2024-01/estatisticas_online_3t_2023.pdf"),
    (4, 2023, f"{FILES_BASE}/2024-03/estatisticas_online_4t_2023.pdf"),
    # 2024
    (1, 2024, f"{FILES_BASE}/2024-05/estatisticas_online_1t_2024.pdf"),
    (2, 2024, f"{FILES_BASE}/2024-08/estatisticas_online_2t_2024.pdf"),
    (3, 2024, f"{FILES_BASE}/2024-12/estatisticas_online_3t_2024.pdf"),
    (4, 2024, f"{FILES_BASE}/2025-03/estatisticas_online_4t_2024.pdf"),
    # 2025
    (1, 2025, f"{FILES_BASE}/2025-06/estatisticas_online_1t_2025.pdf"),
    (2, 2025, f"{FILES_BASE}/2025-09/estatisticas_online_2t_2025.pdf"),
    (3, 2025, f"{FILES_BASE}/2026-01/estatisticas_online_3t_2025.pdf"),
    (4, 2025, f"{FILES_BASE}/2026-03/estatisticas_online_4t_2025.pdf"),
    # 2026
    (1, 2026, f"{FILES_BASE}/2026-06/estatisticas_online_1t_2026.pdf"),
]

# Anchor href pattern — kept for opportunistic HTML scraping when the page
# renders crawlable links (not reliable but cheap to check).
_HREF_RE = re.compile(
    r'href=["\']([^"\']*estatisticas(?:_jogo)?_online_[^"\']+\.pdf)["\']',
    re.IGNORECASE,
)

_ANY_FILE_RE = re.compile(
    r"estatisticas(?:_jogo)?_online_\d[Tt]_\d{4}\.pdf$",
    re.IGNORECASE,
)

# Quarter-end month — used to compute upload-window probes
_QUARTER_END_MONTH = {1: 3, 2: 6, 3: 9, 4: 12}


def _current_quarter() -> tuple[int, int]:
    """Return (quarter, year) for today's date."""
    today = date.today()
    return (today.month - 1) // 3 + 1, today.year


def _forward_probe_urls(latest_quarter: int, latest_year: int) -> list[tuple[int, int, str]]:
    """Generate candidate URLs for quarters newer than the latest known one.

    Probes the next two quarters beyond the latest confirmed entry.
    Upload-folder offset: quarter-end month + 2 or + 3 months (inclusive).
    """
    candidates: list[tuple[int, int, str]] = []

    def _next_quarter(q: int, y: int) -> tuple[int, int]:
        return (1, y + 1) if q == 4 else (q + 1, y)

    seen: set[tuple[int, int]] = set()
    q, y = _next_quarter(latest_quarter, latest_year)
    for _ in range(2):  # probe at most 2 future quarters
        if (q, y) in seen:
            break
        seen.add((q, y))
        q_end_month = _QUARTER_END_MONTH[q]
        for delta in (2, 3, 4):
            upload_month = q_end_month + delta
            upload_year = y
            if upload_month > 12:
                upload_month -= 12
                upload_year += 1
            folder = f"{upload_year}-{upload_month:02d}"
            # Current filename convention: estatisticas_online_{q}t_{yyyy}.pdf
            fn = f"estatisticas_online_{q}t_{y}.pdf"
            candidates.append((q, y, f"{FILES_BASE}/{folder}/{fn}"))
        q, y = _next_quarter(q, y)

    return candidates


def _report_files_from_url(url: str) -> list[ReportFile]:
    """Return one ReportFile per vertical for a confirmed quarterly PDF."""
    return [
        ReportFile(
            operator="PT Statewide",
            vertical=v,
            cadence="quarterly",
            format="pdf",
            source_url=url,
        )
        for v in VERTICALS
    ]


class PortugalCollector(StateCollector):
    state = "PT"
    regulator = "SRIJ — Serviço de Regulação e Inspeção de Jogos"
    source_url = LANDING_URL

    async def list_reports(self, client: httpx.AsyncClient) -> list[ReportFile]:
        found_urls: dict[str, tuple[int, int]] = {}  # url → (quarter, year)

        # --- Strategy 1: opportunistic HTML scrape ---
        # The landing page is JS-rendered so this usually yields nothing, but
        # it costs one request and may pick up new files if the CDN serves HTML.
        try:
            resp = await self.fetch(
                client, "GET", LANDING_URL,
                headers=UA_HEADERS,
                follow_redirects=True,
                timeout=30.0,
            )
            resp.raise_for_status()
            for href in _HREF_RE.findall(resp.text):
                full = href if href.startswith("http") else BASE_URL + href
                if _ANY_FILE_RE.search(full):
                    found_urls[full] = (0, 0)  # quarter/year extracted later
        except httpx.HTTPError:
            pass  # JS-rendered page, fall through to known-URL strategy

        # --- Strategy 2: verify confirmed known URLs ---
        known_set = {url for _, _, url in _KNOWN}
        latest_q, latest_y = 1, 2022
        for quarter, year, url in _KNOWN:
            if url in found_urls:
                # already found via HTML scrape
                found_urls[url] = (quarter, year)
                if (year, quarter) > (latest_y, latest_q):
                    latest_q, latest_y = quarter, year
                continue
            try:
                resp = await self.fetch(
                    client, "HEAD", url,
                    headers=UA_HEADERS,
                    follow_redirects=True,
                    timeout=20.0,
                )
                if resp.status_code == 200:
                    found_urls[url] = (quarter, year)
                    if (year, quarter) > (latest_y, latest_q):
                        latest_q, latest_y = quarter, year
            except httpx.HTTPError:
                continue

        # --- Strategy 3: forward-probe for new quarters ---
        for quarter, year, url in _forward_probe_urls(latest_q, latest_y):
            if url in found_urls or url in known_set:
                continue
            try:
                resp = await self.fetch(
                    client, "HEAD", url,
                    headers=UA_HEADERS,
                    follow_redirects=True,
                    timeout=20.0,
                )
                if resp.status_code == 200:
                    found_urls[url] = (quarter, year)
            except httpx.HTTPError:
                continue

        out: list[ReportFile] = []
        for url in sorted(found_urls):
            out.extend(_report_files_from_url(url))

        return out
