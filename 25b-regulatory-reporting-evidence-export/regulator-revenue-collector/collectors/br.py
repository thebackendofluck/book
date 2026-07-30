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
Brazil Secretaria de Prêmios e Apostas (SPA-MF) + Receita Federal collectors.

Two data sources are scraped:

1. SPA "Panorama Semestral de Apostas de Quota Fixa" PDFs
   Listing: https://www.gov.br/fazenda/.../secretaria-de-premios-e-apostas/apresentacoes
   Cadence:  semi-annual (Jan/2026 covers Jan–Dec 2025; Ago/2025 covers Jan–Jun 2025)
   Format:   PDF
   The Panorama bundles sports + online casino — quota-fixa covers both verticals.

2. Receita Federal "Análise Mensal de Arrecadação" XLSX annexes
   Listing:  https://www.gov.br/receitafederal/pt-br/centrais-de-conteudo/
             publicacoes/relatorios/arrecadacao-federal/{year}
   Cadence:  monthly (Mar 2025 → present)
   Format:   XLSX
   These contain the CNAE row "ATIV. DE EXPLORAÇÃO DE JOGOS DE AZAR E APOSTAS"
   which captures the total federal tax receipts from licensed betting operators.

Root cause of 0-report bug (original code):
  - User-Agent "RegulatorRevenueCollector/1.0" triggers HTTP 403 on gov.br CDN.
  - PANORAMA_RX matched against <a> link text, which is always "Download PDF";
    the document title lives in the parent <p class="callout">, not in <a>.

Fix:
  - Use a browser-like User-Agent per gov.br requirement.
  - Parse <p class="callout"> + inner <a> instead of scanning all <a> tags.
  - Add Receita Federal XLSX listing as a second source (monthly cadence).
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .base import StateCollector
from models import ReportFile

# ---------------------------------------------------------------------------
# Source URLs
# ---------------------------------------------------------------------------
SPA_LISTING = (
    "https://www.gov.br/fazenda/pt-br/composicao/orgaos/"
    "secretaria-de-premios-e-apostas/apresentacoes"
)
RFB_LISTING_BASE = (
    "https://www.gov.br/receitafederal/pt-br/centrais-de-conteudo/"
    "publicacoes/relatorios/arrecadacao-federal"
)

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------
# Matches the two Panorama Semestral reports (semi-annual GGR/RBJ summaries).
# Excludes webinar / agenda / API / autoexclusão / impedidos presentations.
PANORAMA_RX = re.compile(
    r"panorama\s+(?:semestral|peri[oó]dico)|relat[oó]rio\s+peri[oó]dico",
    re.I,
)

# Matches the XLSX annex links on the Receita Federal monthly page.
# e.g. "Análise Mensal - Dez 2025 - Anexo"
RFB_ANEXO_RX = re.compile(r"an[aá]lise\s+mensal.+anexo", re.I)

# Extract period from XLSX link text: "Análise Mensal - Dez 2025 - Anexo" → "2025-12"
_MONTH_PT = {
    "jan": "01", "fev": "02", "mar": "03", "abr": "04",
    "mai": "05", "jun": "06", "jul": "07", "ago": "08",
    "set": "09", "out": "10", "nov": "11", "dez": "12",
}
_PERIOD_RX = re.compile(
    r"(jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)[^\d]*(\d{4})", re.I
)

# ---------------------------------------------------------------------------
# gov.br requires a browser-like User-Agent; the default collector UA gets 403.
# ---------------------------------------------------------------------------
_BR_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.5",
}

# Separate header set for direct file downloads (PDF / XLSX).
_BR_DOWNLOAD_HEADERS = {
    **_BR_HEADERS,
    "Accept": "*/*",
    "Referer": "https://www.gov.br/",
}


def _rfb_period(title: str) -> str | None:
    """
    Parse YYYY-MM from an RFB XLSX link title like
    "Análise Mensal - Dez 2025 - Anexo" → "2025-12".
    """
    m = _PERIOD_RX.search(title)
    if not m:
        return None
    mon = _MONTH_PT.get(m.group(1).lower())
    if mon is None:
        return None
    return f"{m.group(2)}-{mon}"


class BrazilCollector(StateCollector):
    state = "BR"
    regulator = "Secretaria de Prêmios e Apostas (SPA-MF)"
    source_url = SPA_LISTING

    async def list_reports(self, client: httpx.AsyncClient) -> list[ReportFile]:
        out: list[ReportFile] = []
        out.extend(await self._list_spa_panoramas(client))
        out.extend(await self._list_rfb_xlsx(client))
        return out

    # ------------------------------------------------------------------
    # Source 1: SPA Panorama Semestral PDFs
    # ------------------------------------------------------------------
    async def _list_spa_panoramas(
        self, client: httpx.AsyncClient
    ) -> list[ReportFile]:
        """
        Scrape <p class="callout"> blocks on the SPA apresentacoes page.

        Each block contains the document title as text and wraps an <a href>
        pointing directly to the PDF.  Example structure:

            <p class="callout">
              2° Panorama Semestral de Apostas de Quota Fixa (Jan/2026)
              | <a href="...relatorio-periodico-2026.pdf">Download PDF</a>
            </p>

        Only entries whose title matches PANORAMA_RX are collected; webinars,
        agenda, API-docs, and autoexclusion presentations are skipped.
        """
        try:
            r = await client.get(
                SPA_LISTING, headers=_BR_HEADERS,
                follow_redirects=True, timeout=30.0,
            )
            r.raise_for_status()
        except httpx.HTTPError:
            return []

        soup = BeautifulSoup(r.text, "html.parser")
        out: list[ReportFile] = []
        seen: set[str] = set()

        for p in soup.find_all("p", class_="callout"):
            title = p.get_text(" ", strip=True)
            if not PANORAMA_RX.search(title):
                continue
            a = p.find("a", href=True)
            if not a:
                continue
            href = a["href"]
            if not href.lower().endswith(".pdf"):
                continue
            url = urljoin(SPA_LISTING, href)
            if url in seen:
                continue
            seen.add(url)
            # Quota-fixa covers both sports betting and igaming (online casino).
            for vertical in ("sports-wagering", "igaming"):
                out.append(ReportFile(
                    operator="BR Statewide",
                    vertical=vertical,
                    cadence="semi-annual",
                    format="pdf",
                    source_url=url,
                ))
        return out

    # ------------------------------------------------------------------
    # Source 2: Receita Federal monthly analysis PDFs + XLSX annexes
    # ------------------------------------------------------------------
    async def _list_rfb_xlsx(
        self, client: httpx.AsyncClient
    ) -> list[ReportFile]:
        """
        Scrape the Receita Federal annual arrecadação page and collect:

          (a) Monthly "Análise Mensal" PDFs — these contain the per-CNAE table
              "ATIV. DE EXPLORAÇÃO DE JOGOS DE AZAR E APOSTAS" with the monthly
              and YTD federal tax receipts from licensed betting operators.
              The CNAE row format is (R$ milhões):
                {label}  {current_period}  {prior_period}  {diff}  {pct}

          (b) Monthly XLSX annexes — for reference totals; betting-specific
              data is in the PDF so the XLSX is listed as supplementary.

        The /view suffix in Plone CMS hrefs is stripped to get the download URL.

        Each file is tagged with the period derived from its link text title.
        """
        import datetime
        current_year = datetime.date.today().year
        years_to_check = sorted({2025, current_year}, reverse=True)

        out: list[ReportFile] = []
        seen: set[str] = set()

        # Regex for the narrative PDF links: "Análise Mensal - Dez 2025"
        # (without "Anexo" suffix — those are the XLSX annex links)
        RFB_PDF_RX = re.compile(r"an[aá]lise\s+mensal\s+-\s+\w+\s+\d{4}(?:\s*$|(?!\s*-?\s*anexo))", re.I)

        for year in years_to_check:
            listing_url = f"{RFB_LISTING_BASE}/{year}"
            try:
                r = await client.get(
                    listing_url, headers=_BR_HEADERS,
                    follow_redirects=True, timeout=30.0,
                )
                r.raise_for_status()
            except httpx.HTTPError:
                continue

            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href: str = a["href"]
                if "arrecadacao-federal" not in href:
                    continue
                title = a.get_text(" ", strip=True)

                # --- Monthly PDF (primary: contains per-CNAE apostas row) ---
                is_pdf = href.lower().endswith((".pdf", ".pdf/view"))
                if is_pdf and RFB_PDF_RX.search(title):
                    # Strip /view suffix
                    download_url = href
                    if download_url.endswith("/view"):
                        download_url = download_url[:-5]
                    if download_url in seen:
                        continue
                    seen.add(download_url)
                    period = _rfb_period(title)
                    operator_tag = (
                        f"BR Receita Federal {period}" if period
                        else "BR Receita Federal"
                    )
                    out.append(ReportFile(
                        operator=operator_tag,
                        vertical="sports-wagering",
                        cadence="monthly",
                        format="pdf",
                        source_url=download_url,
                    ))
                    continue

                # --- Monthly XLSX annex (supplementary reference) ---
                is_xlsx = href.lower().endswith((".xlsx", ".xlsx/view"))
                if is_xlsx and RFB_ANEXO_RX.search(title):
                    download_url = href
                    if download_url.endswith("/view"):
                        download_url = download_url[:-5]
                    if download_url in seen:
                        continue
                    seen.add(download_url)
                    period = _rfb_period(title)
                    operator_tag = (
                        f"BR Receita Federal Anexo {period}" if period
                        else "BR Receita Federal Anexo"
                    )
                    out.append(ReportFile(
                        operator=operator_tag,
                        vertical="sports-wagering",
                        cadence="monthly",
                        format="xlsx",
                        source_url=download_url,
                    ))
        return out
