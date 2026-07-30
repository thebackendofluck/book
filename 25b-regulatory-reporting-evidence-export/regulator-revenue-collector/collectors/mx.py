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
Mexico SEGOB — Dirección General de Juegos y Sorteos (DGJYS).

Data-availability finding (verified 2026-04-23):
  All three target URLs returned HTTP errors:
    • https://www.gob.mx/segob/acciones-y-programas/direccion-general-de-juegos-y-sorteos → 404
    • https://www.gob.mx/segob/documentos → 404 / 500
    • https://datos.gob.mx/busca/dataset?q=juegos+sorteos → 403

  SEGOB/DGJYS does **not** publish machine-readable revenue or handle data
  through any public endpoint as of 2026-04.  What is published (when the
  portal is reachable at all) is limited to:
    – Lists of authorised casino/sala de apuestas permittees (PDF/HTML)
    – Ley Federal de Juegos y Sorteos regulatory text
    – Occasional press notes with aggregate headcounts

  There is no CKAN dataset on datos.gob.mx, no Socrata API, and no bulk
  download of monthly gross gaming revenue comparable to DGOJ (Spain) or
  SPA-MF (Brazil).

  The collector below is wired with the best-known scraping targets so that
  it can be activated the moment SEGOB makes data public.  list_reports()
  returns an empty list until a real endpoint is discovered; this is
  intentional — it prevents the run-loop from failing and leaves a clear
  upgrade path.

Upgrade path:
  1. Watch https://www.gob.mx/segob/documentos for a new "Estadísticas de
     Juegos y Sorteos" series (they published sporadic PDFs in 2019–2021).
  2. Watch https://datos.gob.mx for a SEGOB/DGJYS dataset entry.
  3. When found, add PDF scraping (BeautifulSoup) or CKAN API call below
     and wire the matching parser in parsers/mx_metrics.py.
"""
from __future__ import annotations

import httpx

from .base import StateCollector
from models import ReportFile

# Best-known public landing pages for DGJYS data.
# None return machine-readable revenue data as of 2026-04-23.
_DOCUMENTS_URL = "https://www.gob.mx/segob/documentos"
_DATOS_URL = (
    "https://datos.gob.mx/busca/dataset"
    "?q=juegos+sorteos&organization=segob"
)
SOURCE_URL = _DOCUMENTS_URL

UA_HEADERS = {"User-Agent": "Mozilla/5.0 RegulatorRevenueCollector/1.0 (MX)"}


class MexicoCollector(StateCollector):
    """Mexico SEGOB Dirección General de Juegos y Sorteos.

    Covers: commercial casinos (salas de juego) and sports-betting kiosks
    (pronósticos deportivos).  Online/remote wagering is not yet legally
    regulated in Mexico as of 2026.

    Returns an empty report list until DGJYS publishes machine-readable data.
    See module docstring for upgrade path.
    """

    state = "MX"
    regulator = "SEGOB — Dirección General de Juegos y Sorteos (DGJYS)"
    source_url = SOURCE_URL

    async def list_reports(self, client: httpx.AsyncClient) -> list[ReportFile]:
        """Probe DGJYS public endpoints for downloadable revenue data.

        Current status: no public data endpoints exist (verified 2026-04-23).
        The function probes the documents page so the collector can
        self-activate once data is published without requiring code changes
        to backfill.py or __init__.py.
        """
        try:
            r = await client.get(
                _DOCUMENTS_URL,
                headers=UA_HEADERS,
                follow_redirects=True,
                timeout=20.0,
            )
            # Only proceed if the page is reachable and contains data files
            if r.status_code != 200:
                return []
        except httpx.HTTPError:
            return []

        # TODO: when DGJYS publishes revenue PDFs, add BeautifulSoup scraping
        # here, similar to collectors/br.py.  Expected filename patterns:
        #   "Informe estadístico * Juegos y Sorteos*.pdf"
        #   "Estadísticas * operadores *.pdf"
        # For now return empty — the downloader will not attempt any fetches.
        return []
