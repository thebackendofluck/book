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
Norway Lotteri- og stiftelsestilsynet (Lottstift) annual market statistics.

Norway operates a strict state monopoly:
  - Norsk Tipping AS  — lottery (Lotto, Keno, Eurojackpot), sports betting
                        (Tipping, Oddsen), online instant games (KongKasino,
                        Bingoria, Yezz, Flax), and terminal slots (Multix/Belago)
  - Norsk Rikstoto    — horse-race betting (tote + fixed-odds)

Private segment: bingo-with-assistant (bingo med medhjelpar), licensed
lotteries (Pantelotteriet, Postkodelotteriet), local lotteries with permit,
and poker championships.

Publication cadence: annual only.  A single PDF per year, titled
"Norske pengespel {YYYY}", is published at:
  https://lottstift.no/app/uploads/{UPLOAD_YYYY}-{MM}/
  Norske-pengespel-{YYYY}*.pdf

The upload-date folder is typically 12–18 months after the reference year
(e.g. "Norske pengespel 2024" was published 2026-01).

Known confirmed URLs (harvested from rapportar-og-publikasjonar page):
  2024 → https://lottstift.no/app/uploads/2026/01/Norske-pengespel-2024-v2.1_endeleg-versjon.pdf
  2023 → https://lottstift.no/app/uploads/2025/02/113965_Pengespel_2023_UU_v4.pdf
  2022 → https://lottstift.no/app/uploads/2024/03/Norske-pengespel-2022_UU.pdf
  2021 → https://lottstift.no/app/uploads/2023/03/76648_Pengespel_2021_UU_v2.pdf
  2020 → https://lottstift.no/app/uploads/2022/02/2020-arsstatistikk.pdf
  2019 → https://lottstift.no/app/uploads/2021/05/Pengespel_2019_web.pdf
  2018 → https://lottstift.no/app/uploads/2021/05/Norske_pengespel_2018_juni2020_web.pdf
  2017 → https://lottstift.no/app/uploads/2021/05/Norske-pengespel-2017.pdf
  2016 → https://lottstift.no/app/uploads/2021/05/Norske-pengespel-2016.pdf
  2015 → https://lottstift.no/app/uploads/2021/05/Norske-pengespel-2015.pdf
"""
from __future__ import annotations

import httpx

from .base import StateCollector
from models import ReportFile

PUBLICATIONS_PAGE = "https://lottstift.no/rapportar-og-publikasjonar/"
UA_HEADERS = {"User-Agent": "Mozilla/5.0 RegulatorRevenueCollector/1.0"}

# Verticals emitted per annual PDF (the parser resolves each from PDF tables)
VERTICALS = (
    "lottery",          # Norsk Tipping talspel (Lotto, Viking Lotto, Keno …)
    "sports-wagering",  # Norsk Tipping sportsspel + oddsen
    "igaming",          # Norsk Tipping Instaspill (online casino, bingo, scratch)
    "horse-racing",     # Norsk Rikstoto hestespel
    "bingo",            # Bingo med/utan medhjelpar
)

# Confirmed hardcoded URLs — (reference_year, upload_path, filename)
# The parser uses reference_year as the period basis.
_KNOWN: list[tuple[int, str]] = [
    (2024, "https://lottstift.no/app/uploads/2026/01/Norske-pengespel-2024-v2.1_endeleg-versjon.pdf"),
    (2023, "https://lottstift.no/app/uploads/2025/02/113965_Pengespel_2023_UU_v4.pdf"),
    (2022, "https://lottstift.no/app/uploads/2024/03/Norske-pengespel-2022_UU.pdf"),
    (2021, "https://lottstift.no/app/uploads/2023/03/76648_Pengespel_2021_UU_v2.pdf"),
    (2020, "https://lottstift.no/app/uploads/2022/02/2020-arsstatistikk.pdf"),
    (2019, "https://lottstift.no/app/uploads/2021/05/Pengespel_2019_web.pdf"),
    (2018, "https://lottstift.no/app/uploads/2021/05/Norske_pengespel_2018_juni2020_web.pdf"),
    (2017, "https://lottstift.no/app/uploads/2021/05/Norske-pengespel-2017.pdf"),
    (2016, "https://lottstift.no/app/uploads/2021/05/Norske-pengespel-2016.pdf"),
    (2015, "https://lottstift.no/app/uploads/2021/05/Norske-pengespel-2015.pdf"),
]


class NorwayCollector(StateCollector):
    state = "NO"
    regulator = "Lotteri- og stiftelsestilsynet (Lottstift)"
    source_url = PUBLICATIONS_PAGE

    async def list_reports(self, client: httpx.AsyncClient) -> list[ReportFile]:
        """Return one ReportFile per (year, vertical) for all confirmed annual PDFs.

        Each ReportFile points to the same annual PDF but carries a different
        vertical tag so _backfill_via_collector dispatches it once per vertical.
        The parser (no_metrics.parse_no_pdf) handles the vertical split
        internally; the duplicate downloads are deduplicated by the backfill
        cache (sha1 of source_url is embedded in the filename).

        For forward compatibility the method also does a lightweight HEAD
        check on the most recent year + 1 to detect a newly published edition.
        """
        out: list[ReportFile] = []
        confirmed = set(url for _, url in _KNOWN)
        seen_urls: set[str] = set()

        for year, url in _KNOWN:
            if url in seen_urls:
                continue
            seen_urls.add(url)
            try:
                resp = await client.head(url, headers=UA_HEADERS, follow_redirects=True)
                if resp.status_code != 200:
                    continue
            except httpx.HTTPError:
                continue
            for v in VERTICALS:
                out.append(ReportFile(
                    operator="NO Statewide",
                    vertical=v,
                    cadence="annual",
                    format="pdf",
                    source_url=url,
                ))

        # Probe for a potential new edition.
        # Publication pattern: "Norske pengespel YYYY" is uploaded in folder
        # {YYYY+2}/{MM}/ where MM is typically 01 but 02 and 03 have also been
        # observed (e.g. 2023 edition used month 02, 2022 used month 03).
        latest_known_year = max(y for y, _ in _KNOWN)
        probe_year = latest_known_year + 1
        upload_year = probe_year + 2
        for month in ("01", "02", "03"):
            probe_url = (
                f"https://lottstift.no/app/uploads/{upload_year}/{month}/"
                f"Norske-pengespel-{probe_year}.pdf"
            )
            if probe_url in confirmed or probe_url in seen_urls:
                continue
            try:
                resp = await client.head(probe_url, headers=UA_HEADERS, follow_redirects=True)
                if resp.status_code == 200:
                    for v in VERTICALS:
                        out.append(ReportFile(
                            operator="NO Statewide",
                            vertical=v,
                            cadence="annual",
                            format="pdf",
                            source_url=probe_url,
                        ))
                    break  # found — no need to probe further months
            except httpx.HTTPError:
                pass

        return out
