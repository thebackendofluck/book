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
backfill.py — One-shot historical loader for the metric_facts table.

Each state has a different history strategy:

  CT  Socrata $limit=2000&$order=month_ending ASC pulls the full history
      (October 2021 onwards). Both datasets fit in a single page today.
  NJ  Walk MGR{YYYY}/, IGRTaxReturns/{YYYY}/, SWRTaxReturns/{YYYY}/ for
      the last 3 years; for each PDF found, download + parse via the
      NJ parser and emit MetricFacts.
  PA  Each FY xlsx is cumulative monthly — one file per vertical contains
      every monthly tab for the FY. Walk the current FY index and parse.
  NY  Per-operator XLSX includes monthly history; download each operator
      file once and parse all rows.

Usage:
    python backfill.py --state CT
    python backfill.py --all
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path

import httpx
import psycopg
import structlog

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from parsers.ct_metrics import parse_ct_dataset  # noqa: E402
from parsers.de_metrics import parse_de_html     # noqa: E402
from parsers.nj_metrics import parse_nj_pdf      # noqa: E402
from parsers.pa_metrics import parse_pa_xlsx     # noqa: E402
from parsers.ny_metrics import parse_ny_xlsx     # noqa: E402
from collectors.ny import (                       # noqa: E402
    COMMERCIAL_CASINOS, SPORTS_WAGERING, VIDEO_GAMING,
)

structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.INFO))
log = structlog.get_logger()


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL", "")
    if dsn.startswith("postgresql+asyncpg://"):
        dsn = "postgresql://" + dsn.split("://", 1)[1]
    return dsn


# Defensive cap applied at upsert time across ALL parsers. No single regulator
# fact (state, operator, vertical, period, metric) has ever exceeded $100B
# in published reality. Anything larger is a parser bug — drop it on the floor
# and log so the source can be investigated, rather than poison the dashboard.
_MAX_FACT_USD_CENTS = 100 * 1_000_000_000 * 100  # $100B → cents


def upsert_facts(facts) -> int:
    facts = list(facts)
    if not facts:
        return 0
    sane: list = []
    dropped = 0
    for f in facts:
        if abs(int(f.value_usd_cents)) > _MAX_FACT_USD_CENTS:
            dropped += 1
            log.warning("upsert.dropped_outlier",
                        state=f.state, vertical=f.vertical, period=f.period,
                        metric=f.metric_name,
                        value_usd_cents=int(f.value_usd_cents),
                        source_url=f.source_url)
            continue
        sane.append(f)
    if not sane:
        return 0
    with psycopg.connect(_dsn(), autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO metric_facts
                  (state, operator, vertical, period, metric_name, value_usd_cents, source_url)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (state, operator, vertical, period, metric_name)
                DO UPDATE SET
                    value_usd_cents = EXCLUDED.value_usd_cents,
                    source_url      = EXCLUDED.source_url,
                    inserted_at     = NOW()
                """,
                [
                    (f.state, f.operator, f.vertical, f.period,
                     f.metric_name, f.value_usd_cents, f.source_url)
                    for f in sane
                ],
            )
        conn.commit()
    if dropped:
        log.warning("upsert.dropped_total", dropped=dropped, kept=len(sane))
    return len(sane)


async def backfill_ct() -> int:
    """Pull full CT Socrata history for both datasets and upsert."""
    datasets = [
        ("imqd-at3c", "igaming"),
        ("xf6g-659c", "sports-wagering"),
    ]
    total = 0
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
        for ds_id, vertical in datasets:
            url = f"https://data.ct.gov/resource/{ds_id}.json"
            try:
                res = await client.get(url, params={
                    "$order": "month_ending ASC",
                    "$limit": 2000,
                })
                res.raise_for_status()
                rows = res.json()
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("ct.fetch failed", ds=ds_id, err=str(exc))
                continue
            facts = list(parse_ct_dataset(rows, vertical, source_url=url))
            inserted = upsert_facts(facts)
            log.info("ct.backfilled", ds=ds_id, vertical=vertical, rows=len(rows), facts=inserted)
            total += inserted
    return total


async def backfill_de(client: httpx.AsyncClient) -> int:
    """Download each DE Lottery FY page (iGaming + sports) and parse all operators.

    DE FY runs Jul → Jun; FY label = ending calendar year (e.g. 2025 = Jul 2024–Jun 2025).
    Each HTML page contains all three operators, so we download once per FY per vertical
    and emit facts for Delaware Park, Bally's Dover, and Harrington in a single parse call.
    """
    from datetime import date as _date
    from collectors.de import IGAMING_TPL, SPORTS_TPL

    def _current_fy() -> int:
        today = _date.today()
        return today.year + 1 if today.month >= 7 else today.year

    cache = Path("/tmp/rrc_de_cache")
    cache.mkdir(parents=True, exist_ok=True)
    total = 0

    for fy in range(2014, _current_fy() + 1):
        for vertical, tpl in (("igaming", IGAMING_TPL), ("sports-wagering", SPORTS_TPL)):
            url = tpl.format(fy=fy)
            local = cache / f"de-{vertical}-{fy}.html"
            if not local.exists():
                try:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        continue
                    local.write_bytes(resp.content)
                except httpx.HTTPError as exc:
                    log.warning("de.download failed", url=url, err=str(exc))
                    continue
            try:
                facts = parse_de_html(local, vertical, source_url=url)
            except Exception as exc:  # noqa: BLE001
                log.warning("de.parse failed", url=url, err=str(exc))
                continue
            if facts:
                inserted = upsert_facts(facts)
                total += inserted
                log.info("de.backfilled", fy=fy, vertical=vertical, facts=inserted)
    return total


async def backfill_nj(client: httpx.AsyncClient) -> int:
    """Walk MGR/IGR/SWR archive pages, download every monthly PDF, parse, upsert."""
    from datetime import datetime
    BASE = (
        "https://www.njoag.gov/about/divisions-and-offices/"
        "division-of-gaming-enforcement-home/financial-and-statistical-information/"
    )
    sources = [
        ("commercial-casino", "monthly-gross-revenue-reports/",          "/MGR"),
        ("igaming",           "monthly-internet-gross-revenue-reports/", "/IGRTaxReturns/"),
        ("sports-wagering",   "monthly-sports-wagering-revenue-reports/", "/SWRTaxReturns/"),
    ]
    pdf_re = re.compile(
        r"/(MGR\d{4}|IGRTaxReturns/\d{4}|SWRTaxReturns/\d{4})/"
        r"([A-Za-z]+)(\d{4})\.pdf$"
    )
    pdf_dir = Path("/tmp/rrc_nj_cache")
    pdf_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    from bs4 import BeautifulSoup
    for vertical, slug, marker in sources:
        try:
            res = await client.get(BASE + slug)
            res.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("nj.index failed", v=vertical, err=str(exc))
            continue
        soup = BeautifulSoup(res.text, "html.parser")
        urls: list[tuple[str, str]] = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if marker not in href or not href.lower().endswith(".pdf"):
                continue
            if "amendment" in href.lower():
                continue
            m = pdf_re.search(href)
            if not m:
                continue
            full = href if href.startswith("http") else "https://www.nj.gov" + href if href.startswith("/oag") else BASE + href.lstrip("/")
            urls.append((full, f"{m.group(3)}-{datetime.strptime(m.group(2)[:3], '%b').month:02d}"))
        for full, period in urls[:60]:
            local = pdf_dir / f"nj-{vertical}-{period}.pdf"
            if not local.exists():
                try:
                    async with client.stream("GET", full) as r:
                        r.raise_for_status()
                        with local.open("wb") as fp:
                            async for chunk in r.aiter_bytes(64*1024):
                                fp.write(chunk)
                except httpx.HTTPError as exc:
                    log.warning("nj.download failed", url=full, err=str(exc))
                    continue
            try:
                facts = parse_nj_pdf(local, vertical, period, full)
            except Exception as exc:  # noqa: BLE001
                log.warning("nj.parse failed", url=full, err=str(exc))
                continue
            inserted = upsert_facts(facts)
            total += inserted
        log.info("nj.backfilled", vertical=vertical, urls=len(urls), facts_so_far=total)
    return total


async def backfill_pa_archive(client: httpx.AsyncClient, years: int = 5) -> int:
    """Walk the PGCB document archive across multiple fiscal years.

    PGCB stores files at /sites/default/files/{YYYY-MM}/<filename>.xlsx — the
    index page only shows the current FY, but historical files remain at the
    same path layout under prior YYYY-MM subdirectories. We probe a list of
    likely FY-end months back `years` years and parse anything whose filename
    matches the revenue-file pattern.
    """
    from collectors.pa import REVENUE_PAT, _classify
    from parsers.pa_metrics import parse_pa_xlsx
    today = date.today() if False else None  # placeholder import location avoided
    from datetime import date as _date
    cur = _date.today()
    # Probe one month per quarter for the last `years` years (16-20 probes total)
    candidates: list[str] = []
    for y in range(cur.year - years, cur.year + 1):
        for m in (3, 6, 9, 12):
            candidates.append(f"{y}-{m:02d}")
    cache = Path("/tmp/rrc_pa_archive")
    cache.mkdir(parents=True, exist_ok=True)
    seen_xlsx: set[str] = set()
    total = 0
    # Walk prior FY index pages (PGCB keeps year-archived URLs sometimes)
    for ym in candidates:
        idx_url = f"https://gamingcontrolboard.pa.gov/sites/default/files/{ym}/"
        try:
            r = await client.get(idx_url, headers={"User-Agent": "Mozilla/5.0"},
                                 follow_redirects=True, timeout=30)
            if r.status_code != 200 or "Index of" not in r.text:
                continue
        except httpx.HTTPError:
            continue
        # Extract xlsx links from autoindex listing
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not (href.lower().endswith(".xlsx") or href.lower().endswith(".xls")):
                continue
            if not REVENUE_PAT.search(href):
                continue
            full = idx_url + href if not href.startswith("http") else href
            if full in seen_xlsx:
                continue
            seen_xlsx.add(full)
            local = cache / f"{ym}-{href[-80:]}"
            if not local.exists():
                try:
                    async with client.stream("GET", full,
                                             headers={"User-Agent": "Mozilla/5.0"}) as resp:
                        if resp.status_code != 200:
                            continue
                        with local.open("wb") as fp:
                            async for chunk in resp.aiter_bytes(64 * 1024):
                                fp.write(chunk)
                except httpx.HTTPError:
                    continue
            vertical = _classify(local.name, full)
            try:
                facts = parse_pa_xlsx(local, vertical, full)
            except Exception as exc:  # noqa: BLE001
                log.warning("pa_archive.parse failed", url=full, err=str(exc))
                continue
            inserted = upsert_facts(facts)
            total += inserted
    log.info("pa_archive.done", probed_months=len(candidates), files=len(seen_xlsx), facts=total)
    return total


async def backfill_pa_archive_v2(client: httpx.AsyncClient) -> int:
    """Probe known PGCB historical FY xlsx URLs and parse via parse_pa_xlsx.

    Directory listings are disabled on gamingcontrolboard.pa.gov, so this
    function uses a hardcoded matrix of (upload-month, filename-template) pairs
    discovered by probing. Files are cumulative per-FY: one xlsx per vertical
    contains all monthly tabs for that fiscal year.

    Confirmed URL pattern:
      /sites/default/files/{YYYY-MM}/{filename}.xlsx

    FY 2022-23 upload months confirmed: 2023-06 (Slots/VGT), 2023-07 (Tables)
    FY 2023-24 upload months confirmed: 2024-07 (all verticals)
    FY 2024-25 upload months confirmed: 2025-07 (all verticals)
    FY 2025-26 (current): handled by backfill_pa / the live collector

    The function also probes a wider sweep of months for each FY in case files
    are re-uploaded to a later month (PGCB sometimes refreshes mid-year).
    """
    from collectors.pa import _classify

    BASE = "https://gamingcontrolboard.pa.gov/sites/default/files"

    # (upload_month, filename, vertical)
    # Confirmed hits first; the function also probes sweep months below.
    KNOWN_URLS: list[tuple[str, str, str]] = [
        # FY 2022-23
        ("2023-06", "Gaming_Revenue_Monthly_Slots_FY20222023.xlsx",      "commercial-casino"),
        ("2023-06", "Gaming_Revenue_Monthly_VGT_FY20222023.xlsx",        "vgt"),
        ("2023-07", "FY%202022-2023%20Table%20Games%20Monthly%20Report.xlsx", "commercial-casino"),
        # FY 2023-24
        ("2024-07", "Gaming_Revenue_Monthly_Slots_FY20232024.xlsx",          "commercial-casino"),
        ("2024-07", "Gaming_Revenue_Monthly_VGT_FY20232024.xlsx",            "vgt"),
        ("2024-07", "FY23-24%20Monthly%20Interactive%20Gaming%20Report%20Summary.xlsx", "igaming"),
        ("2024-07", "FY23-24%20Monthly%20Sports%20Wagering%20Report%20Summary.xlsx",    "sports-wagering"),
        ("2024-07", "FY%202023-2024%20Table%20Games%20Monthly%20Report.xlsx",           "commercial-casino"),
        ("2024-07", "FY23-24%20Fantasy%20Contest%20Monthly%20Revenue%20Report.xlsx",    "fantasy"),
        # FY 2024-25
        ("2025-07", "Gaming_Revenue_Monthly_Slots_FY20242025.xlsx",          "commercial-casino"),
        ("2025-07", "Gaming_Revenue_Monthly_VGT_FY20242025.xlsx",            "vgt"),
        ("2025-07", "FY24-25%20Monthly%20Interactive%20Gaming%20Report%20Summary.xlsx", "igaming"),
        ("2025-07", "FY24-25%20Fantasy%20Contest%20Monthly%20Revenue%20Report.xlsx",    "fantasy"),
        ("2025-07", "FY%202024-2025%20Table%20Games%20Monthly%20Report.xlsx",           "commercial-casino"),
        # FY 2024-25 sports: uploaded 2025-02 (mid-year, covers Jul 2024–Feb 2025);
        # no full-year version found at 2025-07.  Load the mid-year file to fill
        # the 12-month gap 2024-07..2025-02 that exists in the DB.
        ("2025-02", "FY24-25%20Monthly%20Sports%20Wagering%20Report%20Summary.xlsx",   "sports-wagering"),
    ]

    # Additional candidate filenames to sweep across likely upload months.
    # (fy_tag used in filename, start_year_of_fy e.g. 2020 for FY2020-21)
    SWEEP_TEMPLATES = [
        # FY 2020-21 and 2021-22 — older PGCB format, probing broadly
        ("FY20202021", 2020, [
            "Gaming_Revenue_Monthly_Slots_FY20202021.xlsx",
            "Gaming_Revenue_Monthly_VGT_FY20202021.xlsx",
        ]),
        ("FY20212022", 2021, [
            "Gaming_Revenue_Monthly_Slots_FY20212022.xlsx",
            "Gaming_Revenue_Monthly_VGT_FY20212022.xlsx",
        ]),
        # FY 2022-23 additional verticals not confirmed yet
        ("FY20222023", 2022, [
            "FY22-23%20Monthly%20Interactive%20Gaming%20Report%20Summary.xlsx",
            "FY22-23%20Monthly%20Sports%20Wagering%20Report%20Summary.xlsx",
            "FY22-23%20Fantasy%20Contest%20Monthly%20Revenue%20Report.xlsx",
        ]),
        # FY 2024-25 sports final-year file: probed 2025-07 but 404; sweep a
        # wider window in case PGCB publishes the complete file at a later date.
        ("FY20242025_sports", 2024, [
            "FY24-25%20Monthly%20Sports%20Wagering%20Report%20Summary.xlsx",
            "FY24-25%20Monthly%20Sports%20Wagering%20Report%20Summary_0.xlsx",
        ]),
    ]

    # Upload months to probe for sweep templates: covers Jun–Dec of the FY-end year
    # and Jan–Mar of the following year (handles late uploads)
    def _sweep_months(fy_start_year: int) -> list[str]:
        fy_end_year = fy_start_year + 1
        months = []
        for m in range(6, 13):
            months.append(f"{fy_end_year}-{m:02d}")
        for m in range(1, 4):
            months.append(f"{fy_end_year + 1}-{m:02d}")
        return months

    cache = Path("/tmp/rrc_pa_archive_v2")
    cache.mkdir(parents=True, exist_ok=True)
    seen_xlsx: set[str] = set()
    total = 0
    headers = {"User-Agent": "Mozilla/5.0 RevCollector/1.0"}

    async def _try_download_and_parse(url: str, vertical: str) -> int:
        if url in seen_xlsx:
            return 0
        seen_xlsx.add(url)
        fname = url.rsplit("/", 1)[-1].replace("%20", "_")[:100]
        local = cache / fname
        if not local.exists():
            try:
                async with client.stream("GET", url, headers=headers) as resp:
                    if resp.status_code != 200:
                        return 0
                    with local.open("wb") as fp:
                        async for chunk in resp.aiter_bytes(64 * 1024):
                            fp.write(chunk)
            except httpx.HTTPError as exc:
                log.warning("pa_archive_v2.dl failed", url=url, err=str(exc))
                return 0
        try:
            facts = parse_pa_xlsx(local, vertical, url)
        except Exception as exc:  # noqa: BLE001
            log.warning("pa_archive_v2.parse failed", url=url, err=str(exc))
            return 0
        inserted = upsert_facts(facts)
        if inserted:
            log.info("pa_archive_v2.loaded", url=url, vertical=vertical, facts=inserted)
        return inserted

    # 1. Process confirmed known URLs first
    for upload_month, filename, vertical in KNOWN_URLS:
        url = f"{BASE}/{upload_month}/{filename}"
        total += await _try_download_and_parse(url, vertical)

    # 2. Sweep older / unconfirmed templates across a range of upload months
    for _fy_tag, fy_start, filenames in SWEEP_TEMPLATES:
        for month in _sweep_months(fy_start):
            for filename in filenames:
                # Decode %20 etc. before classification so the vertical regex
                # works on human-readable text rather than URL-percent-encoded bytes.
                from urllib.parse import unquote
                decoded = unquote(filename)
                vertical = _classify(decoded, decoded)
                url = f"{BASE}/{month}/{filename}"
                total += await _try_download_and_parse(url, vertical)

    log.info("pa_archive_v2.done",
             probed=len(seen_xlsx),
             facts=total)
    return total


async def backfill_pa(client: httpx.AsyncClient) -> int:
    """Download each FY xlsx from the PA index, parse all monthly tabs, upsert."""
    from bs4 import BeautifulSoup
    INDEX = "https://gamingcontrolboard.pa.gov/news-and-transparency/revenue"
    xlsx_dir = Path("/tmp/rrc_pa_cache")
    xlsx_dir.mkdir(parents=True, exist_ok=True)
    try:
        res = await client.get(INDEX, follow_redirects=True,
                               headers={"User-Agent": "Mozilla/5.0 RevCollector/1.0"})
        res.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("pa.index failed", err=str(exc))
        return 0
    soup = BeautifulSoup(res.text, "html.parser")
    files: list[tuple[str, str]] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/sites/default/files/" not in href:
            continue
        lower = href.lower()
        if not lower.endswith((".xlsx", ".xls")):
            continue
        full = href if href.startswith("http") else "https://gamingcontrolboard.pa.gov" + href
        if full in seen:
            continue
        seen.add(full)
        fname = href.rsplit("/", 1)[-1].lower()
        if "interactive" in fname or "igaming" in fname:
            v = "igaming"
        elif "sports" in fname:
            v = "sports-wagering"
        elif "fantasy" in fname:
            v = "fantasy"
        elif "vgt" in fname or "video" in fname:
            v = "video-gaming"
        elif "slot" in fname or "table" in fname:
            v = "commercial-casino"
        else:
            continue
        files.append((full, v))
    total = 0
    for full, vertical in files[:30]:
        local = xlsx_dir / Path(full).name
        if not local.exists():
            try:
                async with client.stream("GET", full,
                                         headers={"User-Agent": "Mozilla/5.0 RevCollector/1.0"}) as r:
                    r.raise_for_status()
                    with local.open("wb") as fp:
                        async for chunk in r.aiter_bytes(64*1024):
                            fp.write(chunk)
            except httpx.HTTPError as exc:
                log.warning("pa.download failed", url=full, err=str(exc))
                continue
        try:
            facts = parse_pa_xlsx(local, vertical, full)
        except Exception as exc:  # noqa: BLE001
            log.warning("pa.parse failed", url=full, err=str(exc))
            continue
        inserted = upsert_facts(facts)
        total += inserted
        log.info("pa.backfilled", file=local.name, vertical=vertical, facts=inserted)
    return total


async def backfill_ny(client: httpx.AsyncClient) -> int:
    """Download each NY per-operator xlsx, parse full monthly history, upsert."""
    xlsx_dir = Path("/tmp/rrc_ny_cache")
    xlsx_dir.mkdir(parents=True, exist_ok=True)
    catalog = (
        [(name, slug, "commercial-casino") for name, slug in COMMERCIAL_CASINOS]
        + [(name, slug, "sports-wagering")  for name, slug in SPORTS_WAGERING]
        + [(name, slug, "video-gaming")     for name, slug in VIDEO_GAMING]
    )
    total = 0
    for name, slug, vertical in catalog:
        url = f"https://gaming.ny.gov/{slug}-monthly-report-excel"
        local = xlsx_dir / f"ny-{slug}.xlsx"
        if not local.exists():
            try:
                async with client.stream("GET", url) as r:
                    if r.status_code != 200:
                        continue
                    with local.open("wb") as fp:
                        async for chunk in r.aiter_bytes(64*1024):
                            fp.write(chunk)
            except httpx.HTTPError as exc:
                log.warning("ny.download failed", op=name, err=str(exc))
                continue
        try:
            facts = parse_ny_xlsx(local, name, vertical, url)
        except Exception as exc:  # noqa: BLE001
            log.warning("ny.parse failed", op=name, err=str(exc))
            continue
        inserted = upsert_facts(facts)
        total += inserted
        if inserted:
            log.info("ny.backfilled", op=name, vertical=vertical, facts=inserted)
    return total


_CADENCE_SPAN: dict[str, int] = {
    "annual": 12,
    "semi-annual": 6,
    "quarterly": 3,
}

# Months-of-year that mark the natural END of an aggregation window. Used by
# `_spread_facts_if_aggregated` to distinguish "parser emitted aggregate
# values at period-end" from "parser emitted true monthly breakdown that
# happens to live in a quarterly source file" (e.g. Spain's CSV).
_AGGREGATE_END_MONTHS: dict[str, set[int]] = {
    "annual":      {3, 12},          # calendar year-end OR April-March FY
    "semi-annual": {6, 12},
    "quarterly":   {3, 6, 9, 12},
}


def _spread_facts_if_aggregated(facts: list, cadence: str) -> list:
    """Smooth aggregated facts into monthly buckets when the cadence says
    the parser emitted year/semester/quarter totals rather than a true
    monthly breakdown.

    Rule: for each group (state, operator, vertical, metric_name):
      - If every fact's period lands on the SAME calendar month-of-year (e.g.
        all "-12" for annual-year-end or all "-03" for FY April-March), the
        group is an aggregate emission → each fact is divided by `span` and
        replicated across the preceding `span` calendar months.
      - Otherwise (periods span multiple month-of-year values, indicating the
        parser already delivered a monthly breakdown), the group is left alone.

    This keeps parsers that already spread internally (MT, NL, GR) untouched
    while flattening the spikes from parsers that emit pure aggregates (NO,
    BE, BR, UK-annual).
    """
    span = _CADENCE_SPAN.get(cadence)
    if not span or not facts:
        return facts

    groups: dict = {}
    for f in facts:
        key = (f.state, f.operator, f.vertical, f.metric_name)
        groups.setdefault(key, []).append(f)

    allowed_end_months = _AGGREGATE_END_MONTHS.get(cadence, set())
    out = []
    for key, group_facts in groups.items():
        try:
            months_of_year = {int(f.period[5:7]) for f in group_facts}
        except (ValueError, IndexError):
            out.extend(group_facts)
            continue
        # Aggregate signature: every fact ends at a window-end month for this
        # cadence (e.g. all -12 / -03 for annual; all -3/-6/-9/-12 for
        # quarterly). If even one fact is in a non-window month (e.g. -01 or
        # -05), the parser already emitted a true monthly breakdown — leave
        # the whole group alone to avoid triple-counting.
        if not months_of_year.issubset(allowed_end_months):
            out.extend(group_facts)
            continue
        for f in group_facts:
            try:
                yr, mo = int(f.period[:4]), int(f.period[5:7])
            except (ValueError, IndexError):
                out.append(f)
                continue
            per_month = int(f.value_usd_cents) // span
            for offset in range(span):
                m = mo - offset
                y = yr
                while m < 1:
                    m += 12
                    y -= 1
                out.append(type(f)(
                    state=f.state,
                    operator=f.operator,
                    vertical=f.vertical,
                    period=f"{y:04d}-{m:02d}",
                    metric_name=f.metric_name,
                    value_usd_cents=per_month,
                    source_url=f.source_url,
                ))
    return out


async def _backfill_via_collector(
    collector_cls, client: httpx.AsyncClient, parse_func, cache_subdir: str,
    *, vertical_arg: bool = False, operator_arg: bool = False,
) -> int:
    """Generic: discover via collector, download each, parse, upsert.

    parse_func signatures supported:
      parse(path, source_url) -> list[MetricFact]
      parse(path, vertical, source_url) -> list[MetricFact]      # vertical_arg=True
      parse(path, operator, vertical, source_url) -> list[MetricFact]  # operator_arg=True
    """
    cache = Path("/tmp") / cache_subdir
    cache.mkdir(parents=True, exist_ok=True)
    inst = collector_cls(output_root=Path("/tmp/rrc_disc"))
    try:
        reports = list(await inst.list_reports(client))
    except Exception as exc:  # noqa: BLE001
        log.warning("backfill.list failed", coll=collector_cls.__name__, err=str(exc))
        return 0
    import hashlib
    total = 0
    for r in reports[:120]:
        # Include a short hash of source_url so distinct URLs (e.g. NV's
        # per-month PDFs that share operator/vertical/cadence) get distinct
        # cache files instead of overwriting each other.
        url_hash = hashlib.sha1(r.source_url.encode()).hexdigest()[:12]
        slug = "".join(c if c.isalnum() else "-" for c in
                       f"{r.operator}-{r.vertical}-{r.cadence}")[:80]
        local = cache / f"{slug}-{url_hash}.{r.format}"
        if not local.exists():
            try:
                # Allow collectors to override per-request headers (e.g. UA
                # filtering on some European regulator CDNs).
                extra_headers = getattr(inst, "_download_headers", {})
                async with client.stream(
                    "GET", r.source_url, headers=extra_headers
                ) as resp:
                    if resp.status_code != 200:
                        continue
                    with local.open("wb") as fp:
                        async for chunk in resp.aiter_bytes(64 * 1024):
                            fp.write(chunk)
            except httpx.HTTPError as exc:
                log.warning("backfill.dl failed", url=r.source_url, err=str(exc))
                continue
        try:
            if operator_arg:
                facts = parse_func(local, r.operator, r.vertical, r.source_url)
            elif vertical_arg:
                facts = parse_func(local, r.vertical, r.source_url)
            else:
                facts = parse_func(local, r.source_url)
        except Exception as exc:  # noqa: BLE001
            log.warning("backfill.parse failed", url=r.source_url, err=str(exc))
            continue
        facts = _spread_facts_if_aggregated(facts, r.cadence)
        inserted = upsert_facts(facts)
        total += inserted
    log.info(f"backfilled.{collector_cls.__name__}", facts=total)
    return total


async def backfill_mi_excel(client: httpx.AsyncClient) -> int:
    """Download MGCB GovDelivery Excel attachments and load all monthly history.

    Two confirmed file pairs are available and between them provide complete
    monthly coverage for 2023–2026-02:

      Internet Gaming + Sports Betting — December 2024
        - Each file contains two sheets: ``Internet Gaming 2024`` and
          ``Internet Gaming 2023`` (resp. Sports Betting).
        - Sent 2025-01-21; attachment IDs 3138414 (iGaming) / 3138413 (sports).

      Internet Gaming + Sports Betting — February 2026
        - Each file contains ``Internet Gaming 2026`` and ``Internet Gaming
          2025`` (resp. Sports Betting).
        - Sent 2026-03-17; attachment IDs 3586362 (iGaming) / 3586360 (sports).

    This function downloads all four files and parses every non-empty monthly
    row, then upserts to the DB.  It fills the entire 2023-01..2026-02 gap
    without relying on GovDelivery bulletin IDs (which are not guessable).
    """
    from collections.abc import Callable
    from parsers.mi_excel_metrics import parse_mi_igaming_xlsx, parse_mi_sports_xlsx

    # (url, local_filename, parse_func)
    ExcelParseFunc = Callable[[Path, str], list]
    EXCEL_SOURCES: list[tuple[str, str, ExcelParseFunc]] = [
        (
            "https://content.govdelivery.com/attachments/MIGCB/2025/01/21"
            "/file_attachments/3138414/Internet%20Gaming%20-%20December%202024.xlsx",
            "mi_igaming_dec2024.xlsx",
            parse_mi_igaming_xlsx,
        ),
        (
            "https://content.govdelivery.com/attachments/MIGCB/2025/01/21"
            "/file_attachments/3138413/Internet%20Sports%20Betting%20-%20December%202024.xlsx",
            "mi_sports_dec2024.xlsx",
            parse_mi_sports_xlsx,
        ),
        (
            "https://content.govdelivery.com/attachments/MIGCB/2026/03/17"
            "/file_attachments/3586362/Internet%20Gaming%20-%20February%202026.xlsx",
            "mi_igaming_feb2026.xlsx",
            parse_mi_igaming_xlsx,
        ),
        (
            "https://content.govdelivery.com/attachments/MIGCB/2026/03/17"
            "/file_attachments/3586360/Internet%20Sports%20Betting%20-%20February%202026.xlsx",
            "mi_sports_feb2026.xlsx",
            parse_mi_sports_xlsx,
        ),
        # michigan.gov also hosts per-year files with the exact same sheet
        # layout ("Internet Gaming 2026" + "Internet Gaming 2025"), refreshed
        # in place each month.  The revenue-files path is Akamai-fronted and
        # 403s non-browser clients, so the download below usually fails on the
        # production runner — but a pre-seeded copy in the cache dir (fetched
        # via a real browser, see cache note below) is parsed just the same.
        (
            "https://www.michigan.gov/mgcb/-/media/Project/Websites/mgcb"
            "/Detroit-Casino-Revenue-Files/Internet-Gaming---2026.xlsx",
            "mi_igaming_2026.xlsx",
            parse_mi_igaming_xlsx,
        ),
        (
            "https://www.michigan.gov/mgcb/-/media/Project/Websites/mgcb"
            "/Detroit-Casino-Revenue-Files/Internet-Sports-Betting---2026.xlsx",
            "mi_sports_2026.xlsx",
            parse_mi_sports_xlsx,
        ),
    ]

    # Prefer a cache dir inside the bind-mounted source tree (/src in the
    # container) so pre-seeded files survive `docker compose run --rm`;
    # fall back to /tmp for bare local runs.
    cache = (Path("/src/cache/rrc_mi_excel") if Path("/src").is_dir()
             else Path("/tmp/rrc_mi_excel"))
    cache.mkdir(parents=True, exist_ok=True)
    total = 0

    for url, fname, parse_func in EXCEL_SOURCES:
        local = cache / fname
        if not local.exists():
            try:
                async with client.stream("GET", url) as resp:
                    if resp.status_code != 200:
                        log.warning("mi_excel.download failed", url=url, status=resp.status_code)
                        continue
                    with local.open("wb") as fp:
                        async for chunk in resp.aiter_bytes(64 * 1024):
                            fp.write(chunk)
                log.info("mi_excel.downloaded", file=fname)
            except httpx.HTTPError as exc:
                log.warning("mi_excel.download error", url=url, err=str(exc))
                continue

        try:
            facts = parse_func(local, url)
        except Exception as exc:  # noqa: BLE001
            log.warning("mi_excel.parse failed", file=fname, err=str(exc))
            continue

        inserted = upsert_facts(facts)
        total += inserted
        log.info("mi_excel.upserted", file=fname, facts=inserted)

    log.info("mi_excel.done", total=total)
    return total


async def main_async(states: list[str]) -> None:
    total = 0
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(60.0, connect=15.0),
        follow_redirects=True,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        },
    ) as client:
        if "CT" in states:
            total += await backfill_ct()
        if "DE" in states:
            total += await backfill_de(client)
        if "NJ" in states:
            total += await backfill_nj(client)
        if "PA" in states:
            total += await backfill_pa(client)
        if "PA-ARCHIVE" in states:
            total += await backfill_pa_archive_v2(client)
        if "NY" in states:
            total += await backfill_ny(client)
        if "MA" in states:
            from collectors.ma import MassachusettsCollector
            from parsers.ma_metrics import parse_ma_pdf
            total += await _backfill_via_collector(
                MassachusettsCollector, client, parse_ma_pdf, "rrc_ma",
                operator_arg=True,
            )
        if "MD" in states:
            from collectors.md import MarylandCollector
            from parsers.md_metrics import parse_md_xlsx
            total += await _backfill_via_collector(
                MarylandCollector, client, parse_md_xlsx, "rrc_md",
                vertical_arg=True,
            )
        if "OH" in states:
            from collectors.oh import OhioCollector
            from parsers.oh_metrics import parse_oh_pdf, parse_oh_xlsx

            def _parse_oh_dispatch(path: Path, source_url: str):
                """Route to xlsx or pdf parser based on file extension."""
                if str(path).lower().endswith(".pdf"):
                    return parse_oh_pdf(path, source_url)
                return parse_oh_xlsx(path, source_url)

            total += await _backfill_via_collector(
                OhioCollector, client, _parse_oh_dispatch, "rrc_oh",
            )
        if "IN" in states:
            from collectors.in_ import IndianaCollector
            from parsers.in_metrics import parse_in
            total += await _backfill_via_collector(
                IndianaCollector, client, parse_in, "rrc_in",
                vertical_arg=True,
            )
        if "NV" in states:
            from collectors.nv import NevadaCollector
            from parsers.nv_metrics import parse_nv_pdf
            total += await _backfill_via_collector(
                NevadaCollector, client, parse_nv_pdf, "rrc_nv",
            )
        if "IA" in states:
            from collectors.ia import IowaCollector
            from parsers.ia_metrics import parse_ia
            total += await _backfill_via_collector(
                IowaCollector, client, parse_ia, "rrc_ia",
                vertical_arg=True,
            )
        if "LA" in states:
            from collectors.la import LouisianaCollector
            from parsers.la_metrics import parse_la_pdf
            total += await _backfill_via_collector(
                LouisianaCollector, client, parse_la_pdf, "rrc_la",
                vertical_arg=True,
            )
        if "RI" in states:
            from collectors.ri import RhodeIslandCollector
            from parsers.ri_metrics import parse_ri_pdf
            total += await _backfill_via_collector(
                RhodeIslandCollector, client, parse_ri_pdf, "rrc_ri",
                vertical_arg=True,
            )
        if "VA" in states:
            from collectors.va import VirginiaCollector
            from parsers.va_metrics import parse_va_pdf
            total += await _backfill_via_collector(
                VirginiaCollector, client, parse_va_pdf, "rrc_va",
            )
        if "CO" in states:
            from collectors.co import ColoradoCollector
            from parsers.co_metrics import parse_co_xls
            total += await _backfill_via_collector(
                ColoradoCollector, client, parse_co_xls, "rrc_co",
            )
        if "UK" in states:
            from collectors.uk import UKGamblingCommissionCollector
            from parsers.uk_metrics import parse_uk_xlsx
            total += await _backfill_via_collector(
                UKGamblingCommissionCollector, client, parse_uk_xlsx, "rrc_uk",
            )
        if "FR" in states:
            from collectors.fr import FranceCollector
            from parsers.fr_metrics import parse_fr_csv, parse_fr_pdf

            def _parse_fr_dispatch(path: Path, source_url: str) -> list:
                """Route to CSV or PDF parser based on file extension."""
                if str(path).lower().endswith(".pdf"):
                    return parse_fr_pdf(path, source_url)
                return parse_fr_csv(path, source_url)

            total += await _backfill_via_collector(
                FranceCollector, client, _parse_fr_dispatch, "rrc_fr",
            )
        if "SE" in states:
            from collectors.se import SwedenCollector
            from parsers.se_metrics import parse_se_xlsx
            total += await _backfill_via_collector(
                SwedenCollector, client, parse_se_xlsx, "rrc_se",
            )
        if "DK" in states:
            from collectors.dk import DenmarkCollector
            from parsers.dk_metrics import parse_dk_xlsx
            total += await _backfill_via_collector(
                DenmarkCollector, client, parse_dk_xlsx, "rrc_dk",
            )
        if "ON" in states:
            from collectors.on import OntarioCollector
            from parsers.on_metrics import parse_on_xlsx
            total += await _backfill_via_collector(
                OntarioCollector, client, parse_on_xlsx, "rrc_on",
                vertical_arg=True,
            )
        if "NL" in states:
            from collectors.nl import NetherlandsCollector
            from parsers.nl_metrics import parse_nl_pdf
            total += await _backfill_via_collector(
                NetherlandsCollector, client, parse_nl_pdf, "rrc_nl",
            )
        if "GE" in states:
            from collectors.de_country import GermanyCollector
            from parsers.ge_metrics import parse_ge_html
            total += await _backfill_via_collector(
                GermanyCollector, client, parse_ge_html, "rrc_ge",
            )
        if "RO" in states:
            from collectors.ro import RomaniaCollector
            from parsers.ro_metrics import parse_ro_pdf
            total += await _backfill_via_collector(
                RomaniaCollector, client, parse_ro_pdf, "rrc_ro",
            )
        if "MX" in states:
            # SEGOB/DGJYS does not publish machine-readable revenue data as of
            # 2026-04-23.  The collector returns an empty list so no downloads
            # or parse calls are made.  Wire it here so --all and --state MX
            # work transparently once data becomes available.
            from collectors.mx import MexicoCollector
            from parsers.mx_metrics import parse_mx_pdf
            total += await _backfill_via_collector(
                MexicoCollector, client, parse_mx_pdf, "rrc_mx",
                vertical_arg=True,
            )
        if "GR" in states:
            from collectors.gr import GreeceCollector
            from parsers.gr_metrics import parse_gr_pdf
            total += await _backfill_via_collector(
                GreeceCollector, client, parse_gr_pdf, "rrc_gr",
            )
        if "BE" in states:
            from collectors.be import BelgiumCollector
            from parsers.be_metrics import parse_be_pdf
            total += await _backfill_via_collector(
                BelgiumCollector, client, parse_be_pdf, "rrc_be",
            )
        if "NO" in states:
            from collectors.no import NorwayCollector
            from parsers.no_metrics import parse_no_pdf
            total += await _backfill_via_collector(
                NorwayCollector, client, parse_no_pdf, "rrc_no",
            )
        if "MT" in states:
            from collectors.mt import MaltaCollector
            from parsers.mt_metrics import parse_mt_pdf
            total += await _backfill_via_collector(
                MaltaCollector, client, parse_mt_pdf, "rrc_mt",
            )
        if "PT" in states:
            from collectors.pt import PortugalCollector
            from parsers.pt_metrics import parse_pt_pdf
            total += await _backfill_via_collector(
                PortugalCollector, client, parse_pt_pdf, "rrc_pt",
            )
        if "IT" in states:
            from collectors.it import ItalyCollector
            from parsers.it_metrics import parse_it_pdf
            total += await _backfill_via_collector(
                ItalyCollector, client, parse_it_pdf, "rrc_it",
            )
        if "ES" in states:
            from collectors.es import SpainCollector
            from parsers.es_metrics import parse_es_csv, parse_es_xlsx

            def _parse_es_dispatch(path: Path, source_url: str):
                # Skip the regional/retail XLSX entirely — it aggregates
                # Comunidad-Autónoma retail handle without the per-month
                # online-game breakdown we need, and produces values 100×+
                # off the scale when summed naively. The DGOJ monthly CSVs
                # remain the canonical source for our online verticals.
                if str(path).lower().endswith(".xlsx"):
                    return []
                return parse_es_csv(path, source_url)

            total += await _backfill_via_collector(
                SpainCollector, client, _parse_es_dispatch, "rrc_es",
            )
        if "MO" in states:
            from collectors.mo import MissouriCollector
            from parsers.mo_metrics import parse_mo_xlsx
            total += await _backfill_via_collector(
                MissouriCollector, client, parse_mo_xlsx, "rrc_mo",
                vertical_arg=True,
            )
        if "KS" in states:
            from collectors.ks import KansasCollector
            from parsers.ks_metrics import parse_ks_pdf
            total += await _backfill_via_collector(
                KansasCollector, client, parse_ks_pdf, "rrc_ks",
            )
        if "BR" in states:
            from collectors.br import BrazilCollector
            from parsers.br_metrics import (
                parse_br_panorama_pdf, parse_br_rfb_pdf, parse_br_rfb_xlsx,
            )

            def _parse_br_dispatch(path: Path, vertical: str, source_url: str):
                lower = str(path).lower()
                # Receita Federal monthly bundles are sports-wagering-tagged
                # by the collector; SPA Panoramas come in for both verticals.
                if "panorama" in lower or "relatorio-periodico" in source_url.lower():
                    return parse_br_panorama_pdf(path, vertical, source_url)
                if lower.endswith(".xlsx"):
                    return parse_br_rfb_xlsx(path, source_url)
                return parse_br_rfb_pdf(path, source_url)

            total += await _backfill_via_collector(
                BrazilCollector, client, _parse_br_dispatch, "rrc_br",
                vertical_arg=True,
            )
        if "MI" in states:
            from collectors.mi import MichiganCollector
            from parsers.mi_metrics import parse_mi_bulletin
            total += await _backfill_via_collector(
                MichiganCollector, client, parse_mi_bulletin, "rrc_mi",
            )
            # Primary Excel source: fills entire 2023-2025 history gap that
            # GovDelivery bulletin discovery cannot reach (2025 bulletin IDs
            # are not guessable by any probe method).
            total += await backfill_mi_excel(client)
        if "IL" in states:
            total += await backfill_il()
        if "AZ" in states:
            from collectors.az import ArizonaCollector
            from parsers.az_metrics import parse_az_pdf
            total += await _backfill_via_collector(
                ArizonaCollector, client, parse_az_pdf, "rrc_az",
            )
        if "WV" in states:
            from collectors.wv import WestVirginiaCollector
            from parsers.wv_metrics import parse_wv_pdf
            total += await _backfill_via_collector(
                WestVirginiaCollector, client, parse_wv_pdf, "rrc_wv",
                vertical_arg=True,
            )
        if "TN" in states:
            from collectors.tn import TennesseeCollector
            from parsers.tn_metrics import parse_tn_pdf
            total += await _backfill_via_collector(
                TennesseeCollector, client, parse_tn_pdf, "rrc_tn",
            )
    log.info("backfill done", total_facts=total, states=states)


async def backfill_il() -> int:
    """Download and parse all IGB sports-wagering monthly CSVs.

    Two passes are executed:

    Pass 1 — Existing cached Sport Detail files (output/il/*.csv):
      These files contain per-sport handle data; the updated parser emits
      ``metric_name='handle'`` facts from them.  The fabricated source_url
      is reconstructed from each filename so the period is recoverable.

    Pass 2 — Fresh Tax Summary downloads via the collector:
      The collector now fetches ``SearchType=TypeAccrual`` Tax Summary
      CSVs (one per month) which contain State AGR (= GGR).  The parser
      emits ``metric_name='ggr'`` facts from these files.

    Both passes upsert into the same metric_facts table.
    """
    import re as _re
    from collectors.il import ENDPOINT, IllinoisCollector
    from parsers.il_metrics import parse_il_csv

    total = 0

    # ------------------------------------------------------------------
    # Pass 1: parse existing cached AllActivitySportDetail files for handle
    # ------------------------------------------------------------------
    _FNAME_RE = _re.compile(
        r"il-statewide-sports-(\w+)-(\d{4})-monthly\.csv$", _re.I
    )
    _MONTH_MAP = {
        "january": "01", "february": "02", "march": "03", "april": "04",
        "may": "05", "june": "06", "july": "07", "august": "08",
        "september": "09", "october": "10", "november": "11", "december": "12",
    }
    cached_dir = Path("/src/output/il")
    if cached_dir.exists():
        for csv_path in sorted(cached_dir.glob("il-statewide-sports-*-monthly.csv")):
            m = _FNAME_RE.match(csv_path.name)
            if not m:
                continue
            month_word = m.group(1).lower()
            year = m.group(2)
            month_num = _MONTH_MAP.get(month_word)
            if not month_num:
                continue
            period = f"{year}-{month_num}"
            # Fabricate a source_url with rtype=sport-detail so the parser
            # knows to extract handle (not GGR).
            fake_url = f"{ENDPOINT}?period={period}&rtype=sport-detail"
            try:
                facts = parse_il_csv(csv_path, fake_url)
            except Exception as exc:  # noqa: BLE001
                log.warning("il.pass1.parse failed", file=csv_path.name, err=str(exc))
                continue
            if facts:
                inserted = upsert_facts(facts)
                total += inserted
        log.info("il.pass1.done", cached_dir=str(cached_dir), facts_so_far=total)

    # ------------------------------------------------------------------
    # Pass 2: download Tax Summary CSVs (GGR / AGR) via the collector
    # ------------------------------------------------------------------
    cache_root = Path("/tmp/rrc_il")
    cache_root.mkdir(parents=True, exist_ok=True)

    collector = IllinoisCollector(output_root=cache_root)
    try:
        snapshot = await collector.collect()
    except Exception as exc:  # noqa: BLE001
        log.warning("il.collect failed", err=str(exc))
        log.info("il.backfilled", facts=total)
        return total

    for report in snapshot.reports:
        # Only process Tax Summary reports in pass 2 (Sport Detail already
        # handled in pass 1 from the /src/output/il/ cache).
        if "rtype=tax-summary" not in report.source_url:
            continue
        if not report.local_path:
            continue
        local = cache_root / report.local_path
        if not local.exists():
            continue
        try:
            facts = parse_il_csv(local, report.source_url)
        except Exception as exc:  # noqa: BLE001
            log.warning("il.pass2.parse failed", url=report.source_url, err=str(exc))
            continue
        if facts:
            inserted = upsert_facts(facts)
            total += inserted

    log.info("il.backfilled", facts=total)
    return total


# Allow `--all` to walk every wired state.
ALL_STATES = ["CT", "DE", "NJ", "PA", "NY", "MA", "MD", "OH", "IN", "IA", "LA", "RI",
              "MI", "MO", "KS", "NV", "VA", "CO", "WV", "TN",
              "UK", "FR", "SE", "DK", "ON", "RO", "MX", "GR", "BE", "NO", "MT", "PT",
              "IT", "ES", "BR", "IL", "AZ", "NL"]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--state", action="append", default=[],
                   help="State code, repeatable. Default: --all.")
    p.add_argument("--all", action="store_true")
    args = p.parse_args()
    states = args.state or (ALL_STATES if args.all else ["CT"])
    asyncio.run(main_async([s.upper() for s in states]))


if __name__ == "__main__":
    main()
