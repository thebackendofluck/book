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
Spain Dirección General de Ordenación del Juego (DGOJ).

DGOJ publishes quarterly market data via its Alfresco CMIS document store.
The landing page is:
  https://www.ordenacionjuego.es/datos-estudios/estudios/descarga-datos-mercado-juego

Links on that page use paths like  /cmis/document/alfresco/<UUID>  with NO
file extension in the URL itself.  Content-Disposition headers reveal the
actual extension (csv / xlsx).  The page also links to /sites/default/files/
if DGOJ ever migrates to plain Drupal file storage.

File inventory as of 1T-2026 (confirmed live 2026-06-09):
  GGR y cantidades jugadas          /cmis/document/alfresco/9dce6b70-...  (CSV)
  Depósitos y Retiradas             /cmis/document/alfresco/9feb0a53-...  (CSV)
  Número de cuentas                 /cmis/document/alfresco/7ece8fae-...  (CSV)
  Gastos publicidad/promoción       /cmis/document/alfresco/a9a44785-...  (CSV)
  Detalle datos mercado (series)    /cmis/document/alfresco/76e38aff-...  (XLSX)

The GGR CSV contains ALL quarters from 2013 onward as rows
(Año, Trimestre, Mes, Juego, Cantidades jugadas, GGR) — one file carries
the full historical series so only that file is needed for MetricFact parsing.
"""
from __future__ import annotations

import re

import httpx
from bs4 import BeautifulSoup

from .base import StateCollector
from models import ReportFile

BASE = "https://www.ordenacionjuego.es"
INDEX = BASE + "/datos-estudios/estudios/descarga-datos-mercado-juego"
UA_HEADERS = {"User-Agent": "Mozilla/5.0 RegulatorRevenueCollector/1.0"}

# Alfresco CMIS UUIDs for the key data files.  Update when DGOJ rotates files.
# Last verified: 1T-2026 release (2026-06-09).
_KNOWN_FILES: list[tuple[str, str, str]] = [
    # (uuid, label, format)
    ("9dce6b70-db0b-4c8a-8377-eca50bc06fdb", "GGR y cantidades jugadas",   "csv"),
    ("9feb0a53-284e-4f16-b60c-f2404d829aac", "Depósitos y Retiradas",       "csv"),
    ("7ece8fae-749e-4676-8383-40cff83524dd", "Número de cuentas",           "csv"),
    ("a9a44785-e141-4d3d-83c0-4ad3c930b332", "Gastos publicidad/promoción", "csv"),
    ("76e38aff-7136-432c-a513-56f168b020e9", "Detalle datos mercado",       "xlsx"),
]

# Vertical emitted on every ReportFile (the parser resolves per-row verticals).
_PRIMARY_VERTICAL = "igaming"


class SpainCollector(StateCollector):
    state = "ES"
    regulator = "Dirección General de Ordenación del Juego (DGOJ)"
    source_url = INDEX

    async def list_reports(self, client: httpx.AsyncClient) -> list[ReportFile]:
        """Scrape the DGOJ download page and return one ReportFile per data file.

        Strategy (two-pass):
        1. Fetch the landing page and collect every /cmis/document/alfresco/ link
           that is either labelled as a data file or has no file-extension filter
           applied (because Alfresco URLs have no .csv/.xlsx suffix).
        2. Fall back to the hard-coded _KNOWN_FILES list when the page returns
           no matches (e.g. scraping blocked or page structure changed).
        """
        scraped = await self._scrape_page(client)
        if scraped:
            return scraped
        # Fallback: known-good UUIDs from 1T-2026 release
        return self._fallback_reports()

    async def _scrape_page(self, client: httpx.AsyncClient) -> list[ReportFile]:
        try:
            r = await client.get(INDEX, headers=UA_HEADERS,
                                 follow_redirects=True, timeout=30.0)
            r.raise_for_status()
        except httpx.HTTPError:
            return []

        soup = BeautifulSoup(r.text, "html.parser")
        out: list[ReportFile] = []
        seen: set[str] = set()

        for a in soup.find_all("a", href=True):
            href: str = a["href"]

            # Accept Alfresco CMIS paths and Drupal /sites/default/files/ paths.
            is_alfresco = "/cmis/document/alfresco/" in href
            is_drupal = "/sites/default/files/" in href
            if not (is_alfresco or is_drupal):
                continue

            lower_href = href.lower()
            label = (a.get_text(" ", strip=True) or "").lower()

            # Skip known non-data files (operator list, metadata PDFs, TXT)
            if any(skip in label for skip in ("operadores", "metadatos")):
                if lower_href.endswith(".pdf") or lower_href.endswith(".txt"):
                    continue

            # Detect format: Alfresco URLs have no extension; use label hints.
            if lower_href.endswith(".xlsx") or lower_href.endswith(".xls") or "excel" in label:
                fmt = "xlsx"
            elif lower_href.endswith(".xls"):
                fmt = "xls"
            elif lower_href.endswith(".pdf"):
                fmt = "pdf"
            elif lower_href.endswith(".txt"):
                continue  # skip plain-text metadata
            else:
                # Alfresco URL with no extension: default csv unless labelled xlsx
                fmt = "xlsx" if "excel" in label else "csv"

            url = href if href.startswith("http") else BASE + href
            if url in seen:
                continue
            seen.add(url)

            out.append(ReportFile(
                operator="ES Statewide",
                vertical=_PRIMARY_VERTICAL,
                cadence="quarterly",
                format=fmt,
                source_url=url,
            ))

        return out

    def _fallback_reports(self) -> list[ReportFile]:
        """Return hard-coded known-good ReportFile list for 1T-2026."""
        return [
            ReportFile(
                operator="ES Statewide",
                vertical=_PRIMARY_VERTICAL,
                cadence="quarterly",
                format=fmt,
                source_url=BASE + f"/cmis/document/alfresco/{uuid}",
            )
            for uuid, _label, fmt in _KNOWN_FILES
        ]
