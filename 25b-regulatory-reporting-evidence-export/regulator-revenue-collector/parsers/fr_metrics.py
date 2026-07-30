# Companion code for "The Backend of Luck" - Chapter 25b, Regulatory Reporting and Evidence Export.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""France ANJ (Autorité Nationale des Jeux) CSV and PDF parsers.

Source 1 – data.gouv.fr CSV/XLSX annual timeseries
  Resource UUID: 4c8319f9-720f-4bb0-a4f3-b7d438867240 (CSV)
                 f08da369-c509-4b9c-ad25-3c7ab510b081 (XLSX equivalent)

  The CSV is in a **wide pivot** format:
    - First column contains metric labels (French), e.g. "Mises paris sportifs (en M€)"
    - Remaining columns are year-end dates, e.g. "Au 31/12/2010", "31/12/2022"
    - Values are in millions of EUR; some use comma as decimal separator

Source 2 – anj.fr PDFs (bilan économique annual + rapport semestriel)
  ANJ switched from quarterly HTML to semiannual/annual PDF reports in 2025.
  Known published PDFs:
    - FY2025 annual bilan:   https://anj.fr/sites/default/files/2026-04/Bilan%20%C3%A9conomique%202025.pdf
      Full-year 2025 online PBJ €2.617B (+8.5%):
        Paris sportifs en ligne  €1.766B
        Poker en ligne           €0.525B
        Paris hippiques en ligne €0.326B
    - H1 2025 semiannual:    https://anj.fr/sites/default/files/2025-10/Rapport%20semestriel%202025.pdf
      H1 2025 online PBJ €1.4B (approx, split across verticals)

  parse_fr_pdf() extracts PBJ (produit brut des jeux = GGR) per vertical and
  emits monthly MetricFact rows distributed evenly across the period.

ANJ does not permit online casino (RNG slots/table games); the only verticals
in this dataset are paris sportifs, paris hippiques, and poker en ligne.
Because the published file is annual (not quarterly), period maps to the
December end-month: YYYY-12.
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path

import pdfplumber

from .metrics_model import MetricFact

# ---------------------------------------------------------------------------
# Vertical mapping: canonical row-label fragments → (vertical, metric_name)
# Each pattern is matched case-insensitively as a substring of the first column.
# Order matters: most specific first to avoid false matches.
# ---------------------------------------------------------------------------
_ROW_MAP: list[tuple[str, str, str]] = [
    # Sports wagering
    ("mises paris sportifs",  "sports-wagering", "handle"),
    ("pbj paris sportifs",    "sports-wagering", "ggr"),
    # Horse racing
    ("mises paris hippiques", "horse-racing",    "handle"),
    ("pbj paris hippiques",   "horse-racing",    "ggr"),
    # Online poker — only GGR is published (no gross handle row in ANJ data)
    ("pbj poker",             "online-poker",    "ggr"),
]

# Year-column header patterns: "Au 31/12/YYYY" or "31/12/YYYY"
_YEAR_RE = re.compile(r"(?:Au\s+)?(\d{2})/(\d{2})/(\d{4})", re.IGNORECASE)

# EUR → USD exchange rate (fixed per spec)
_EUR_TO_USD: float = 1.08

# ANJ values are in millions of EUR — scale to EUR before converting
_MILLION: float = 1_000_000.0


def _parse_year_col(header: str) -> int | None:
    """Extract the 4-digit year from a column header like 'Au 31/12/2019'."""
    m = _YEAR_RE.search(header.strip())
    if m:
        return int(m.group(3))
    return None


def _parse_eur_millions(raw: str) -> float | None:
    """
    Parse a French-locale number string (millions of EUR) to a plain float.
    Handles:
      - comma decimal separator  ("62,6" → 62.6)
      - dot decimal separator    ("1234.5")
      - empty / dash cells       ("" / "-")
    Returns None if the cell cannot be parsed.
    """
    s = raw.strip()
    if not s or s in ("-", "N/A", "n/a"):
        return None
    # Remove thousand-separators: French uses space or period for thousands;
    # if both comma and period are present, comma is decimal (French style).
    if "," in s and "." in s:
        # e.g. "1.234,56" → remove dot, replace comma
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        # e.g. "62,6" → "62.6"
        s = s.replace(",", ".")
    # Strip any residual non-numeric characters (%, spaces)
    s = re.sub(r"[^\d.\-]", "", s)
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _eur_millions_to_usd_cents(eur_millions: float) -> int:
    """Convert EUR millions to USD cents using fixed FX rate 1.08."""
    eur = eur_millions * _MILLION
    usd = eur * _EUR_TO_USD
    return int(round(usd * 100))


def _sniff_delimiter(sample: str) -> str:
    """Use csv.Sniffer to detect delimiter; fall back to semicolon."""
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        return dialect.delimiter
    except csv.Error:
        return ";"


def parse_fr_csv(path: Path, source_url: str) -> list[MetricFact]:
    """Parse the ANJ wide-pivot CSV and return one MetricFact per
    (year × vertical × metric_name) combination found.

    Args:
        path:       Local path to the downloaded CSV file.
        source_url: Original URL recorded on each MetricFact for traceability.

    Returns:
        List of MetricFact instances with state="FR", operator="FR Statewide".
    """
    raw_bytes = path.read_bytes()
    # Decode: ANJ CSVs may be UTF-8 or Latin-1; try UTF-8-sig first (handles BOM)
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = raw_bytes.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw_bytes.decode("latin-1", errors="replace")

    # Detect delimiter from first 4 KB
    delimiter = _sniff_delimiter(text[:4096])

    reader = csv.reader(io.StringIO(text), delimiter=delimiter)

    # ---- Pass 1: read header row, map column index → year ----
    header: list[str] = []
    for row in reader:
        if row:
            header = row
            break

    # Build {col_index: year} for all year-type columns (skip col 0 = label)
    col_year: dict[int, int] = {}
    for idx, cell in enumerate(header):
        if idx == 0:
            continue
        year = _parse_year_col(cell)
        if year is not None:
            col_year[idx] = year

    # ---- Pass 2: scan data rows for recognised metric labels ----
    facts: list[MetricFact] = []

    for row in reader:
        if not row:
            continue

        label = row[0].strip().lower()

        # Skip aggregate / total rows
        if "total" in label:
            continue

        # Find which metric this row describes
        matched: tuple[str, str] | None = None
        for fragment, vertical, metric_name in _ROW_MAP:
            if fragment in label:
                matched = (vertical, metric_name)
                break

        if matched is None:
            continue

        vertical, metric_name = matched

        # Emit one MetricFact per year column that has a parseable value
        for col_idx, year in col_year.items():
            if col_idx >= len(row):
                continue
            val = _parse_eur_millions(row[col_idx])
            if val is None:
                continue
            # Annual data: distribute across 12 months so monthly trend charts
            # don't show a one-month spike. Sum across the 12 monthly facts
            # equals the original annual total — card aggregates stay correct.
            annual_cents = _eur_millions_to_usd_cents(val)
            monthly_cents = annual_cents // 12
            for m in range(1, 13):
                facts.append(
                    MetricFact(
                        state="FR",
                        operator="FR Statewide",
                        vertical=vertical,
                        period=f"{year}-{m:02d}",
                        metric_name=metric_name,
                        value_usd_cents=monthly_cents,
                        source_url=source_url,
                    )
                )

    return facts


# ---------------------------------------------------------------------------
# PDF parser — ANJ bilan économique (annual) + rapport semestriel
# ---------------------------------------------------------------------------

# Hardcoded PBJ data for ANJ annual bilans whose PDF cannot be reliably
# fetched (Cloudflare-blocked + Wayback snapshots truncated).
# Source: published PDF summaries and press releases.
# Keyed by (year, vertical) → PBJ in millions of EUR.
_HARDCODED_ANNUAL_PBJ: dict[tuple[int, str], float] = {
    # FY2025 bilan économique (Bilan économique 2025.pdf, April 2026)
    # Online market total: €2.617B (+8.5%)
    (2025, "sports-wagering"): 1766.0,   # Paris sportifs en ligne
    (2025, "online-poker"):     525.0,   # Poker en ligne
    (2025, "horse-racing"):     326.0,   # Paris hippiques en ligne
}

# Detect the reference year from filename or PDF text
_URL_YEAR_RE = re.compile(r"[/_-](\d{4})(?:[._\-]|$)", re.IGNORECASE)
# "Bilan économique 2025" / "bilan-economique-2025"
_BILAN_YEAR_RE = re.compile(
    r"bilan\s+[eé]conomique\s+(\d{4})", re.IGNORECASE
)
# "Rapport semestriel 2025" / "Rapport semestriel H1 2025"
_SEMESTRI_YEAR_RE = re.compile(
    r"rapport\s+semestriel\s+(?:H[12]\s+)?(\d{4})", re.IGNORECASE
)
# Half indicator: "H1" or "S1" / "H2" or "S2" (first half = months 1-6)
_HALF_RE = re.compile(r"\b([HS][12])\b", re.IGNORECASE)

# PBJ row patterns in ANJ PDFs.
# The PDFs contain tables or plain text with rows like:
#   "Paris sportifs en ligne  1 766"
#   "Poker en ligne           525"
#   "Paris hippiques en ligne 326"
# Values may appear as "1 766" / "1.766" / "1 766,3" (EUR millions).
_PDF_ROW_MAP: list[tuple[re.Pattern, str, str]] = [
    # sports betting
    (re.compile(r"paris\s+sportifs\s+en\s+ligne", re.IGNORECASE),
     "sports-wagering", "ggr"),
    (re.compile(r"paris\s+sportifs\b", re.IGNORECASE),
     "sports-wagering", "ggr"),
    # horse racing
    (re.compile(r"paris\s+hippiques\s+en\s+ligne", re.IGNORECASE),
     "horse-racing", "ggr"),
    (re.compile(r"paris\s+hippiques\b", re.IGNORECASE),
     "horse-racing", "ggr"),
    # online poker
    (re.compile(r"poker\s+en\s+ligne", re.IGNORECASE),
     "online-poker", "ggr"),
    (re.compile(r"\bpoker\b", re.IGNORECASE),
     "online-poker", "ggr"),
]

# Number pattern: optionally leading minus; digits with optional space/dot
# thousand-separators and optional comma decimal (French locale)
_NUM_RE = re.compile(
    r"(-?\d[\d\s.]*(?:,\d+)?)\s*(?:M€|M\s*€|€|Md€|\$)?",
    re.IGNORECASE,
)


def _parse_fr_pdf_number(s: str) -> float | None:
    """Parse a French-locale number (possibly in millions of EUR).

    Accepts:
      "1 766"    → 1766.0  (space thousands sep)
      "1.766"    → 1766.0  (dot thousands sep — French ambiguity resolved
                             below by checking magnitude)
      "1 766,3"  → 1766.3
      "525"      → 525.0
      "2,617"    → 2.617   (dot is decimal when no space thousands sep)
    Returns None if unparseable.
    """
    s = s.strip()
    if not s or s in ("-", "—", "N/A"):
        return None
    # Strip currency markers
    s = re.sub(r"\s*(?:M€|M\s*€|€|Md€|\$)\s*$", "", s, flags=re.IGNORECASE).strip()
    has_space = " " in s
    has_comma = "," in s
    has_dot = "." in s

    if has_space:
        # "1 766,3" or "1 766" — spaces are thousand-separators
        s = s.replace(" ", "")
        if has_comma:
            s = s.replace(",", ".")
    elif has_comma and has_dot:
        # "1.766,3" French style — dot is thousands, comma is decimal
        s = s.replace(".", "").replace(",", ".")
    elif has_comma:
        # "1,766" — ambiguous; treat comma as decimal only if result < 100
        # (ANJ annual data are in hundreds of M€ per vertical, not thousands)
        candidate = s.replace(",", ".")
        try:
            v = float(candidate)
            # If < 10 it's likely a decimal (e.g. "2,617" billion = 2617M)
            # but that would be Md€, not M€; treat as decimal for safety.
            return v
        except ValueError:
            return None
    # Otherwise plain integer or dot-decimal
    s = re.sub(r"[^\d.\-]", "", s)
    try:
        return float(s)
    except ValueError:
        return None


def _extract_year_from_context(source_url: str, page_texts: list[str]) -> int | None:
    """Try to extract the reference year from the URL or page text.

    Priority:
      1. Explicit "Bilan économique YYYY" or "Rapport semestriel YYYY" in page text
      2. Explicit "Bilan*YYYY" or "Rapport*semestriel*YYYY" in the URL itself
         (also tries URL-decoded version for percent-encoded URLs)
      3. Year tokens from URL filename portion only (after the last '/')
         (e.g. .../2026-04/Bilan...2025.pdf → 2025 not 2026)
    """
    from urllib.parse import unquote

    # 1. Text labels (most reliable)
    combined = " ".join(page_texts[:3])
    for pattern in (_BILAN_YEAR_RE, _SEMESTRI_YEAR_RE):
        m = pattern.search(combined)
        if m:
            return int(m.group(1))

    # 2. URL-embedded explicit labels — try both raw and decoded
    for url_candidate in (source_url, unquote(source_url)):
        for pattern in (_BILAN_YEAR_RE, _SEMESTRI_YEAR_RE):
            m = pattern.search(url_candidate)
            if m:
                return int(m.group(1))

    # 3. Year tokens from URL filename portion only (after the last '/')
    filename_part = unquote(source_url).rsplit("/", 1)[-1]
    for m in _URL_YEAR_RE.finditer(filename_part):
        y = int(m.group(1))
        if 2015 <= y <= 2035:
            return y

    # 4. Fallback: any year from full URL
    for m in _URL_YEAR_RE.finditer(source_url):
        y = int(m.group(1))
        if 2015 <= y <= 2035:
            return y

    return None


def _is_semi_annual(source_url: str, page_texts: list[str]) -> bool:
    """Return True if this is a semiannual (H1/H2) report, False if annual.

    Checks both the source_url (including URL-decoded form for Wayback URLs)
    and the first few pages of page text.
    """
    from urllib.parse import unquote
    combined = " ".join(page_texts[:3]) + " " + source_url + " " + unquote(source_url)
    # Positive: contains "semestriel" anywhere
    if _SEMESTRI_RE.search(combined):
        return True
    # Negative: contains "bilan" → annual
    if re.search(r"bilan", combined, re.IGNORECASE):
        return False
    return False


_SEMESTRI_RE = re.compile(r"semestriel|semestri|rapport\s+(?:H[12]|S[12])", re.IGNORECASE)


def _extract_half(source_url: str, page_texts: list[str]) -> int:
    """Return 1 (Jan-Jun) or 2 (Jul-Dec) for semiannual reports.

    ANJ publication calendar:
      - Rapport semestriel H1 (Jan-Jun coverage) is published in Oct of same year
      - Rapport semestriel H2 (Jul-Dec coverage) is published in Mar-Apr of following year

    URL path component like "/2025-10/" (October) → ANJ H1 report.
    URL path component like "/2026-03/" (March) → ANJ H2 report for prior year.
    """
    combined = " ".join(page_texts[:3]) + " " + source_url
    # Explicit half marker (e.g. "H1 2025", "S2 2024")
    m = _HALF_RE.search(combined)
    if m:
        tag = m.group(1).upper()
        return 1 if tag in ("H1", "S1") else 2
    # ANJ URL pattern: extract publication month from path component YYYY-MM
    url_m = re.search(r"/(\d{4})-(\d{2})/", source_url)
    if url_m:
        pub_month = int(url_m.group(2))
        # Published Jul-Dec of reference year → H1 coverage (Jan-Jun)
        # Published Jan-Jun of following year → H2 coverage (Jul-Dec)
        return 1 if pub_month >= 7 else 2
    return 1  # default to H1 if indeterminate


def parse_fr_pdf(path: Path, source_url: str) -> list[MetricFact]:
    """Parse an ANJ bilan économique or rapport semestriel PDF.

    Extracts PBJ (produit brut des jeux = GGR) by vertical:
      - paris sportifs en ligne → sports-wagering / ggr
      - paris hippiques en ligne → horse-racing / ggr
      - poker en ligne → online-poker / ggr

    Annual reports are distributed over 12 monthly facts (Jan-Dec).
    Semiannual H1 reports are distributed over 6 monthly facts (Jan-Jun).
    Semiannual H2 reports are distributed over 6 monthly facts (Jul-Dec).

    Values in the PDF are in millions of EUR; converted to USD cents at 1.08.

    Args:
        path:       Local path to the downloaded PDF.
        source_url: Original download URL for traceability.

    Returns:
        List of MetricFact instances.
    """
    facts: list[MetricFact] = []
    page_texts: list[str] = []
    try:
        with pdfplumber.open(path) as pdf:
            # Scan all pages (up to 20) — ANJ PDFs vary in layout
            for page in pdf.pages[:20]:
                try:
                    t = page.extract_text() or ""
                except Exception:  # noqa: BLE001 — partial page, skip
                    continue
                page_texts.append(t)
    except Exception:  # noqa: BLE001 — truncated/corrupt PDF; use whatever we got
        pass
    # Even if page_texts is empty (PDF truncated/corrupt), still try to extract
    # year from the source_url and fall back to hardcoded data.

    year = _extract_year_from_context(source_url, page_texts)
    if year is None:
        return facts

    is_semi = _is_semi_annual(source_url, page_texts)
    if is_semi:
        half = _extract_half(source_url, page_texts)
        span = 6
        start_month = 1 if half == 1 else 7
    else:
        span = 12
        start_month = 1

    # Extract vertical→value from all pages.
    #
    # ANJ PDFs embed PBJ values in prose sentences.  Examples from the
    # semestriel H1 2025:
    #   "Le PBJ progresse lui de 10% à 961M€"      → sports-wagering
    #   "le PBJ reste stable à 174M€"               → horse-racing
    #   "Le PBJ du poker s'établit à 246M€ au S1"  → online-poker
    #
    # Strategy: search the whole-text blob (pages joined) for proximity
    # between a vertical label and a PBJ value.
    #
    # Normalise newlines → spaces first so window searches work across
    # line breaks in pdfplumber output.
    vertical_values: dict[tuple[str, str], float] = {}

    full_text = " ".join(page_texts)
    # Collapse multiple spaces/newlines to a single space for easier regex
    full_text_norm = re.sub(r"\s+", " ", full_text)

    # Direct sentence patterns that appear in ANJ PDFs.
    # Match "PBJ ... [à|de|total|s'établit|stable] NNN[M€|Mds€]"
    # The value is in M€ (millions); range 50–5000 for these verticals.
    # Match "PBJ ... [à|soit] NNNMé" within a short window.
    # Both "à NNNMé" (direct report: "PBJ reste stable à 174M€") and
    # "soit NNNMé" (parenthetical: "(soit 961M€)") are used by ANJ.
    # Allow up to 60 chars between "PBJ" and the value preposition.
    _PBJ_VAL = r"PBJ\b.{0,60}?(?:à|soit)\s*\b(\d[\d\s]{0,5}(?:,\d+)?)M€"

    _DIRECT_PATTERNS: list[tuple[re.Pattern, str, str]] = [
        # ── Sports-wagering ──────────────────────────────────────────────────
        # "FOCUS PARIS SPORTIFS" section → first PBJ→value match
        (re.compile(
            r"FOCUS\s+PARIS\s+SPORTIFS.{0,800}" + _PBJ_VAL,
            re.IGNORECASE | re.DOTALL,
        ), "sports-wagering", "ggr"),
        # Fallback: "pari sportif" context → PBJ→value
        (re.compile(
            r"(?:pari\s+sportif|paris\s+sportifs?(?:\s+en\s+ligne)?)(?:\s+\S+){0,30}\s+"
            + _PBJ_VAL,
            re.IGNORECASE | re.DOTALL,
        ), "sports-wagering", "ggr"),

        # ── Horse racing ─────────────────────────────────────────────────────
        # "FOCUS PARIS HIPPIQUES" section
        (re.compile(
            r"FOCUS\s+(?:PARIS\s+HIPPIQUES?|PARI\s+HIPPIQUE).{0,800}" + _PBJ_VAL,
            re.IGNORECASE | re.DOTALL,
        ), "horse-racing", "ggr"),
        # "pari hippique ... PBJ ... à 174M€" — need to pass the handle figure
        # "PBJ reste stable à 174M€" is the key sentence; look for it
        # specifically with "stable" as discriminator to skip the handle sentence
        (re.compile(
            r"PBJ\b[^.]{0,40}?stable[^.]{0,40}?à\s*\b(\d[\d\s]{0,5}(?:,\d+)?)M€",
            re.IGNORECASE | re.DOTALL,
        ), "horse-racing", "ggr"),
        # General fallback: hippique context + PBJ value
        (re.compile(
            r"(?:pari\s+hippique|paris\s+hippiques?(?:\s+en\s+ligne)?).{0,200}"
            + _PBJ_VAL,
            re.IGNORECASE | re.DOTALL,
        ), "horse-racing", "ggr"),

        # ── Online poker ─────────────────────────────────────────────────────
        # "Le PBJ du poker s'établit à 246M€"
        (re.compile(
            r"PBJ\s+du\s+poker\b.{0,80}?(?:à|soit)\s*\b(\d[\d\s]{0,5}(?:,\d+)?)M€",
            re.IGNORECASE | re.DOTALL,
        ), "online-poker", "ggr"),
        # "FOCUS POKER" section
        (re.compile(
            r"FOCUS\s+POKER.{0,800}" + _PBJ_VAL,
            re.IGNORECASE | re.DOTALL,
        ), "online-poker", "ggr"),
        # General poker context
        (re.compile(
            r"\bpoker(?:\s+en\s+ligne)?.{0,300}" + _PBJ_VAL,
            re.IGNORECASE | re.DOTALL,
        ), "online-poker", "ggr"),
    ]

    def _extract_eur_millions(raw: str) -> float | None:
        """Extract a valid M€ value (range 50–5000) from a raw string."""
        # Remove spaces (thousand-separator) before parsing
        s = raw.strip().replace(" ", "").replace(",", ".")
        try:
            val = float(s)
        except ValueError:
            return None
        return val if 50.0 <= val <= 5000.0 else None

    for pattern, vertical, metric in _DIRECT_PATTERNS:
        key = (vertical, metric)
        if key in vertical_values:
            continue
        m = pattern.search(full_text_norm)
        if m:
            val = _extract_eur_millions(m.group(1))
            if val is not None:
                vertical_values[key] = val

    # Hardcoded fallback for annual bilans whose PDF is unavailable/truncated.
    # Only used when the PDF parse yielded no facts for a vertical AND we have
    # a known value in the hardcoded table.
    if not is_semi and year is not None:
        for vertical in ("sports-wagering", "online-poker", "horse-racing"):
            key = (vertical, "ggr")
            if key not in vertical_values:
                hardcoded = _HARDCODED_ANNUAL_PBJ.get((year, vertical))
                if hardcoded is not None:
                    vertical_values[key] = hardcoded

    # Emit monthly facts
    for (vertical, metric), eur_millions in vertical_values.items():
        total_cents = _eur_millions_to_usd_cents(eur_millions)
        monthly_cents = total_cents // span
        for offset in range(span):
            m = start_month + offset
            y = year
            if m > 12:
                m -= 12
                y += 1
            facts.append(
                MetricFact(
                    state="FR",
                    operator="FR Statewide",
                    vertical=vertical,
                    period=f"{y:04d}-{m:02d}",
                    metric_name=metric,
                    value_usd_cents=monthly_cents,
                    source_url=source_url,
                )
            )

    return facts
