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
Italy Agenzia delle Dogane e dei Monopoli (ADM) annual gambling statistics.

ADM publishes the "Libro Blu" (Blue Book) annually, whose Appendice PDF
contains Tabella A.96 — "Riepilogo nazionale per tipologia di gioco relativo
al gioco a distanza" — with raccolta / vincite / spesa broken down by online
vertical (Bingo, Casino/cards, sports betting, poker, etc.) for the last 3
reference years.

-- Quarterly Bollettino Statistico (investigated 2026-06-09) --
ADM also publishes a quarterly Bollettino Statistico at
  https://www.adm.gov.it/portale/en/-/bollettino-statistico-2024
covering Q1–Q4 2024 and Q1–Q3 2025 (and continuing). These PDFs are
image-based charts for the gaming section; pdfplumber extracts only section
headings (e.g. "Gettito Scommesse", "Gettito Apparecchi"). There are NO
text-selectable per-vertical online GGR figures (no raccolta/spesa by
tipologia di gioco). The bulletins report only aggregate fiscal revenue
split into four broad buckets (Apparecchi, Scommesse, Giochi numerici,
Altri giochi) — these do not distinguish online from land-based. They are
therefore NOT suitable as a monthly/quarterly source for per-vertical
online spesa/GGR. This decision was verified by fetching both the Q4 2024
and Q3 2025 PDFs and attempting pdfplumber extraction.

-- Libro Blu 2024 status (investigated 2026-06-09) --
The Libro Blu 2023 (covering reference year 2023) was published on
2025-09-03, nearly two years after the reference year. As of 2026-06-09
there is NO Libro Blu 2024 available on adm.gov.it. The official
publication index at
  https://www.adm.gov.it/portale/en/libro-blu-organizzazione-statistiche-e-attivita-anno-2023
lists editions 2019–2023 only. Provisional 2024 total-market figures
(raccolta €157.45 bn, spesa €21.58 bn) are available only via a
parliamentary inquiry (May 2025) — NOT disaggregated by online vertical
and NOT in a machine-readable form suitable for wiring into this collector.
The Libro Blu 2024 Appendice (needed for per-vertical online spesa) is
expected ~late 2026 based on the ~2-year publication lag.

-- Action on next update --
When Libro Blu 2024 Appendice is published, add its path to
LIBRO_BLU_APPENDICE below (verify with HEAD 200), update LIBRO_BLU_INDEX
to point to the 2024 edition URL, and backfill IT.

Discovery strategy: each Libro Blu year has a known Appendice document ID
embedded in the portale. We maintain a hard-coded list of confirmed-live
document paths (verified via HEAD 200) and yield one ReportFile per year.
When a new edition appears the list is extended.

Cadence: annual (one file covers a full calendar year).
"""
from __future__ import annotations

import httpx

from .base import StateCollector
from models import ReportFile

ADM_BASE = "https://www.adm.gov.it"

# Confirmed-live Appendice PDF paths (HEAD → 200 verified 2026-04-23).
# Each entry: (year_label, path)
LIBRO_BLU_APPENDICE: list[tuple[str, str]] = [
    (
        "2023",
        "/portale/documents/20182/228258926/"
        "Libro+blu+2023+-+Appendice+(1).pdf/"
        "69136ac1-7651-f842-10a5-55c8844b9fbc?t=1756971508596",
    ),
    (
        "2022",
        "/portale/documents/20182/151943740/"
        "Libro+blu+2022+-+Appendice.pdf/"
        "22df313e-a77a-bc1d-ae95-96789fe70499?t=1704734887870",
    ),
    (
        "2021",
        "/portale/documents/20182/77358098/"
        "Libro-Blu2021-Appendice-2nov2022-prot.pdf/"
        "1ad0d6b3-a02e-9ae1-7b1e-d866b3389548?t=1667398433893",
    ),
]

# Landing page used as the canonical source_url for the snapshot.
LIBRO_BLU_INDEX = "https://www.adm.gov.it/portale/en/libro-blu-organizzazione-statistiche-e-attivita-anno-2023"

UA_HEADERS = {"User-Agent": "Mozilla/5.0 RegulatorRevenueCollector/1.0"}


class ItalyCollector(StateCollector):
    state = "IT"
    regulator = "Agenzia delle Dogane e dei Monopoli (ADM)"
    source_url = LIBRO_BLU_INDEX

    async def list_reports(self, client: httpx.AsyncClient) -> list[ReportFile]:
        """Return one ReportFile per confirmed-live Libro Blu Appendice PDF."""
        out: list[ReportFile] = []
        for year, path in LIBRO_BLU_APPENDICE:
            url = ADM_BASE + path
            try:
                r = await client.head(url, headers=UA_HEADERS,
                                      follow_redirects=True, timeout=20)
            except httpx.HTTPError:
                continue
            if r.status_code != 200:
                continue
            out.append(ReportFile(
                operator="IT Statewide",
                vertical="igaming",          # parser extracts all verticals internally
                cadence="annual",
                format="pdf",
                source_url=url,
            ))
        return out
