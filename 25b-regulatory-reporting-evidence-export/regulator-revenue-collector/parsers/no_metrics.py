# Companion code for "The Backend of Luck" - Chapter 25b, Regulatory Reporting and Evidence Export.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Norway Lottstift annual gambling statistics PDF parser.

Source:
    https://lottstift.no/rapportar-og-publikasjonar/
    Annual PDFs titled "Norske pengespel {YYYY}", published 12-18 months
    after the reference year.

Data structure (confirmed from 2024 edition):
    Main summary table ("Pengepremiar og gevinstar i YYYY"):
        Each provider row:  <name>  <brutto>  <gevinst>  <netto>  <pct>
        Example:
            Norsk Tipping    54 461   44 224   10 237   81 %
            Norsk Rikstoto    3 164    2 206      958   70 %
            ...
        All values in NOK millions.

    Norsk Tipping sub-vertical breakdown ("Total netto oms." row):
        Total netto oms.   <talspel>   <sportsspel>   <skrapelodd>   <terminal>   <instaspill>   <total>
        Example:
            Total netto oms.   6 387   1 421   450   224   1 755   10 237
        Columns in order: Talspel, Sportsspel, Skrapelodd(papir), Terminalspel, Instaspill, Totalt

Vertical mapping:
    Norsk Tipping sub-verticals:
        Talspel      → lottery          (Lotto, Viking Lotto, Keno …)
        Sportsspel   → sports-wagering
        Skrapelodd   → lottery          (Flax scratch — merged into lottery)
        Terminalspel → land-based-casino (Multix + Belago terminals)
        Instaspill   → igaming          (online casino, bingo, scratch)

    Provider rows (summary table):
        Norsk Rikstoto        → horse-racing
        Bingo med medhjelpar  → bingo
        Bingo utan medhjelpar → bingo  (merged/summed with above)
        Lotteri med lisens    → lottery
        Andre lotteri med løyve → lottery

FX:  NOK millions → USD cents (year-specific rate):
    value_usd_cents = int(nok_millions * 1_000_000 * _nok_to_usd_rate(year) * 100)
    Rates used: 0.119 (2015), 0.118 (2016-2018), 0.110 (2019), 0.103 (2020),
    0.115 (2021), 0.100 (2022), 0.094 (2023+)

Period encoding:
    Annual data → emitted as YYYY-12 (year-end convention).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Union

from .metrics_model import MetricFact

# ── FX ───────────────────────────────────────────────────────────────────────
# Year-specific annual-average NOK→USD rates.
# Using a fixed rate of 0.094 (current) for all years underestimates
# earlier years when the NOK was materially stronger against the USD.
# Approximate annual averages (USD per 1 NOK):
#   2015-2018: ~0.118-0.120 (NOK ≈ 8.3-8.5/USD)
#   2019-2020: ~0.103-0.110 (NOK ≈ 9.1-9.7/USD)
#   2021:      ~0.115       (NOK ≈ 8.7/USD)
#   2022:      ~0.100       (NOK ≈ 10.0/USD)
#   2023+:     ~0.094       (NOK ≈ 10.6/USD)
_NOK_TO_USD_BY_YEAR: dict[int, float] = {
    2015: 0.119,
    2016: 0.118,
    2017: 0.120,
    2018: 0.118,
    2019: 0.110,
    2020: 0.103,
    2021: 0.115,
    2022: 0.100,
    2023: 0.094,
    2024: 0.094,
}
_NOK_TO_USD_DEFAULT: float = 0.094  # fallback for years not in the table


def _nok_to_usd_rate(year: int | None) -> float:
    """Return the year-specific NOK→USD rate, or the default if unknown."""
    if year is None:
        return _NOK_TO_USD_DEFAULT
    return _NOK_TO_USD_BY_YEAR.get(year, _NOK_TO_USD_DEFAULT)


def _nok_millions_to_usd_cents(nok_millions: float, year: int | None = None) -> int:
    """Convert NOK millions to USD cents using the year-specific FX rate."""
    rate = _nok_to_usd_rate(year)
    return int(nok_millions * 1_000_000 * rate * 100)


# ── Year extraction from source URL ──────────────────────────────────────────
_YEAR_RE = re.compile(r"pengespel[_-](\d{4})", re.IGNORECASE)
_YEAR_RE2 = re.compile(r"arsstatistikk[_-]?(\d{4})|(\d{4})[_-]arsstatistikk", re.IGNORECASE)
_YEAR_PLAIN_RE = re.compile(r"[/_-](\d{4})[._/-]")


def _extract_year(source_url: str) -> int | None:
    """Parse reference year from the source URL."""
    m = _YEAR_RE.search(source_url)
    if m:
        return int(m.group(1))
    m = _YEAR_RE2.search(source_url)
    if m:
        return int(m.group(1) or m.group(2))
    for m in _YEAR_PLAIN_RE.finditer(source_url):
        y = int(m.group(1))
        if 2005 <= y <= 2035:
            return y
    return None


# ── Number tokeniser ──────────────────────────────────────────────────────────
# Norwegian number format: thousands separated by spaces, decimal by comma.
# E.g.  "10 237" = 10237,  "3 164" = 3164,  "958" = 958
# We do NOT want to merge two adjacent space-separated numbers that are
# actually different columns.  Strategy: scan for runs of digits that may
# include internal spaces only when the space is surrounded by exactly 1–3
# digits on the right (thousands group).
_NUM_TOKEN_RE = re.compile(
    r"\b(\d{1,3}(?:\s\d{3})*(?:,\d+)?)\b"
)


def _parse_nok_millions(token: str) -> float | None:
    """Convert a Norwegian-format number token to float."""
    s = token.strip()
    # Remove thousands-separator spaces
    s = re.sub(r"(\d)\s(\d{3})\b", r"\1\2", s)
    s = re.sub(r"(\d)\s(\d{3})\b", r"\1\2", s)  # second pass for 7-digit
    # Norwegian decimal comma → point
    s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _numbers_from_line(line: str) -> list[float]:
    """Return all NOK-format numbers found in a line, in left-to-right order.

    Handles thousands-space notation (e.g. "10 237") but avoids merging
    numbers across wide column gaps (multiple spaces).
    """
    # Replace multi-space gaps (>=2 spaces) that aren't part of a number with |
    # so we can split on them to find column boundaries.
    # Numbers with thousands spaces have exactly one space between groups.
    # Example: "54 461      44 224         10 237" → three distinct numbers.
    #
    # Approach: use _NUM_TOKEN_RE which anchors on word boundaries and handles
    # the "d ddd" pattern but not "d  dd" (double-space = column sep).
    results = []
    for m in _NUM_TOKEN_RE.finditer(line):
        val = _parse_nok_millions(m.group(1))
        if val is not None and val > 0:
            results.append(val)
    return results


# ── PDF text extraction ───────────────────────────────────────────────────────

def _extract_text(path: Union[Path, bytes]) -> str:
    """Return plain text from the PDF via pdftotext (layout mode)."""
    import subprocess
    if isinstance(path, (bytes, bytearray)):
        result = subprocess.run(
            ["pdftotext", "-layout", "-", "-"],
            input=path, capture_output=True,
        )
    else:
        result = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"],
            capture_output=True,
        )
    if result.returncode != 0:
        raise RuntimeError(
            f"pdftotext failed (rc={result.returncode}): {result.stderr.decode()[:200]}"
        )
    return result.stdout.decode("utf-8", errors="replace")


# ── Summary table row patterns ────────────────────────────────────────────────
# Each row of the main table:
#   <label>  <brutto_omsetning>  <gevinst>  <netto_omsetning>  <pct>%
# We identify rows by label prefix and extract the 3rd numeric column (netto).
#
# Pattern: label followed by exactly 3 integer columns then a percentage.
# Column values are space-separated with thousands-space notation.

_SUMMARY_ROW_RE = re.compile(
    r"([\w\sæøåæøå]+?)"  # label (lazy)
    r"\s{2,}"                                             # 2+ spaces before first number
    r"(\d[\d\s]*\d|\d)"                                  # brutto
    r"\s{2,}"
    r"(\d[\d\s]*\d|\d)"                                  # gevinst
    r"\s{2,}"
    r"(\d[\d\s]*\d|\d)"                                  # netto
    r"\s+\d+\s*%",                                       # pct column
    re.IGNORECASE,
)


def _parse_summary_row_netto(line: str) -> float | None:
    """Extract the netto omsetning (3rd numeric column) from the main summary table.

    The main "Pengepremiar og gevinstar" table rows have the form:
        <label>  <brutto>  <gevinst>  <netto>  <pct>%

    Validity guards applied:
        - Exactly 4 values: brutto, gevinst, netto, pct
        - pct < 100 (it is a percentage, e.g. 70 for 70 %)
        - brutto > gevinst > netto > 0  (payouts reduce the number)
        - brutto >= 50 (NOK millions — avoids accidentally matching small numbers
          from footnotes or text fragments)

    Note: the report contains a second, differently-shaped table further down
    ("Målt etter netto omsetning ... til formål") that repeats the same
    provider labels with a 5-column row: netto, til-formål (amount), til-formål
    (%), til-selskap (amount), til-selskap (%). E.g. for "Andre lotteri med
    løyve": ``74   39   53 %   35   47 %`` — 5 numbers, not 4. Without an
    exact-count guard, ``nums[2]`` (a *percentage*, "53 %") gets misread as a
    NOK-millions netto figure and silently fed through the NOK→USD conversion,
    inflating that vertical by the percentage value treated as millions of
    kroner. Requiring exactly 4 numbers (the true Pengepremiar-table shape)
    excludes these 5-number percentage rows.
    """
    nums = _numbers_from_line(line)
    if len(nums) != 4:
        return None
    brutto, gevinst, netto, pct = nums[0], nums[1], nums[2], nums[3]
    # pct must look like a realistic RTP (< 100)
    if pct >= 100:
        return None
    # Ordering guard: brutto > netto > 0 (gevinst can be < netto for low-RTP
    # games like bingo utan medhjelpar where pct ≈ 49 %)
    if not (brutto > netto > 0):
        return None
    # Sanity: brutto should be at least 50 MNOK to exclude text fragments
    if brutto < 50:
        return None
    return netto


# ── NT sub-vertical table parser ──────────────────────────────────────────────
# The Norsk Tipping chapter-3 table has the "Total netto oms." row:
#   Total netto oms.   6 387   1 421   450   224   1 755   10 237
# Columns: Talspel | Sportsspel | Skrapelodd | Terminalspel | Instaspill | Totalt

_NT_NETTO_ROW_RE = re.compile(
    r"total netto oms\.?\s+([\d\s]+)",
    re.IGNORECASE,
)


def _parse_nt_sub_verticals(text: str, year: int, source_url: str) -> list[MetricFact]:
    """Extract Norsk Tipping sub-vertical breakdown from the chapter-3 table."""
    period = f"{year}-12"
    m = _NT_NETTO_ROW_RE.search(text)
    if not m:
        return []
    nums = _numbers_from_line(m.group(1))
    # Expected order: Talspel, Sportsspel, Skrapelodd(papir), Terminalspel, Instaspill, Totalt
    if len(nums) < 5:
        return []
    talspel, sportsspel, skrapelodd, terminal, instaspill = (
        nums[0], nums[1], nums[2], nums[3], nums[4]
    )
    mapping = [
        ("lottery",           talspel + skrapelodd),   # Lotto/Keno + scratch = lottery
        ("sports-wagering",   sportsspel),
        ("land-based-casino", terminal),
        ("igaming",           instaspill),
    ]
    facts = []
    for vertical, nok_m in mapping:
        if nok_m > 0:
            facts.append(MetricFact(
                state="NO",
                operator="NO Statewide",
                vertical=vertical,
                period=period,
                metric_name="ggr",
                value_usd_cents=_nok_millions_to_usd_cents(nok_m, year),
                source_url=source_url,
            ))
    return facts


# ── Summary table parser ──────────────────────────────────────────────────────
# Provider → vertical for rows in the main Pengepremiar table.
_PROVIDER_VERTICAL: list[tuple[str, str]] = [
    ("norsk rikstoto",           "horse-racing"),
    ("bingo med medhjelpar",     "bingo"),
    ("bingo utan medhjelpar",    "bingo"),
    ("lotteri med lisens",       "lottery"),
    ("andre lotteri med løyve",  "lottery"),
    ("andre lotteri",            "lottery"),
]


def _parse_summary_table(text: str, year: int, source_url: str) -> list[MetricFact]:
    """Parse the main provider summary table for non-NT rows."""
    period = f"{year}-12"
    facts: list[MetricFact] = []
    lines = text.split("\n")

    for line in lines:
        low = line.lower().strip()
        if not low:
            continue
        for fragment, vertical in _PROVIDER_VERTICAL:
            if fragment not in low:
                continue
            # Must contain at least two digit sequences (exclude pure % lines)
            nums = _numbers_from_line(line)
            if len(nums) < 2:
                continue
            netto = _parse_summary_row_netto(line)
            if netto is None or netto <= 0:
                continue
            facts.append(MetricFact(
                state="NO",
                operator="NO Statewide",
                vertical=vertical,
                period=period,
                metric_name="ggr",
                value_usd_cents=_nok_millions_to_usd_cents(netto, year),
                source_url=source_url,
            ))
            break  # each line matches at most one provider
    return facts


# ── Public entry point ────────────────────────────────────────────────────────

def parse_no_pdf(
    path: Union[Path, bytes],
    source_url: str,
) -> list[MetricFact]:
    """Parse a Lottstift "Norske pengespel" annual PDF.

    Parameters
    ----------
    path:
        ``pathlib.Path`` to the PDF on disk, or raw ``bytes`` of the file.
    source_url:
        The URL the file was fetched from; stored verbatim in every
        ``MetricFact.source_url``.

    Returns
    -------
    list[MetricFact]
        One fact per (vertical, year).  Period is YYYY-12 (annual, year-end).

    Currency:
        NOK millions → USD cents using year-specific FX rates.
        See _NOK_TO_USD_BY_YEAR for per-year rates (range: 0.094–0.120).

    Verticals emitted (when present in the PDF):
        lottery           — NT talspel + skrapelodd + licensed/permitted lotteries
        sports-wagering   — NT sportsspel
        igaming           — NT instaspill (online casino/bingo)
        land-based-casino — NT terminalspel (Multix/Belago)
        horse-racing      — Norsk Rikstoto
        bingo             — bingo med/utan medhjelpar (summed)
    """
    year = _extract_year(source_url)
    if year is None and isinstance(path, Path):
        year = _extract_year(str(path))
    if year is None:
        raise ValueError(f"Cannot determine reference year from source_url={source_url!r}")

    text = _extract_text(path)

    facts: list[MetricFact] = []

    # 1. NT sub-verticals (chapter 3 breakdown)
    nt_facts = _parse_nt_sub_verticals(text, year, source_url)
    facts.extend(nt_facts)

    # 2. Other providers from the summary table
    facts.extend(_parse_summary_table(text, year, source_url))

    # Deduplicate: same (vertical, period) can appear from both bingo rows.
    # Sum the values (bingo med + bingo utan medhjelpar).
    merged: dict[tuple[str, str], MetricFact] = {}
    for f in facts:
        key = (f.vertical, f.period)
        if key in merged:
            merged[key] = f.model_copy(update={
                "value_usd_cents": merged[key].value_usd_cents + f.value_usd_cents
            })
        else:
            merged[key] = f

    return list(merged.values())
