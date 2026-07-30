# Companion code for "The Backend of Luck" - Chapter 25b, Regulatory Reporting and Evidence Export.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Portugal SRIJ quarterly online gambling statistics PDF parser.

Source: SRIJ Estatísticas do Jogo Online (quarterly bulletins, 2022–present)
Confirmed URL patterns (upload-folder / filename):
  Q1 2022: /2022-06/estatisticas_jogo_online_1t_2022.pdf
  Q2 2022: /2022-08/estatisticas_jogo_online_2t_2022.pdf
  Q3 2022: /2022-11/estatisticas_online_3t_2022.pdf   (_jogo dropped)
  Q4 2022: /2023-03/estatisticas_online_4t_2022.pdf
  Q1 2023: /2023-06/estatisticas_online_1T_2023.pdf
  Q2 2023: /2023-09/estatisticas_online_2t_2023.pdf
  Q3 2023: /2024-01/estatisticas_online_3t_2023.pdf
  Q4 2023: /2024-03/estatisticas_online_4t_2023.pdf
  Q1 2024: /2024-05/estatisticas_online_1t_2024.pdf
  Q2 2024: /2024-08/estatisticas_online_2t_2024.pdf
  Q3 2024: /2024-12/estatisticas_online_3t_2024.pdf
  Q4 2024: /2025-03/estatisticas_online_4t_2024.pdf
  Q1 2025: /2025-06/estatisticas_online_1t_2025.pdf
  Q2 2025: /2025-09/estatisticas_online_2t_2025.pdf
  Q3 2025: /2026-01/estatisticas_online_3t_2025.pdf
  Q4 2025: /2026-03/estatisticas_online_4t_2025.pdf
  Q1 2026: /2026-06/estatisticas_online_1t_2026.pdf

Bulletin layout — two active verticals since Q3 2022 (older had Poker too):
  Vertical abbreviation  Full Portuguese name                → canonical tag
  ─────────────────────────────────────────────────────────────────────────────
  ADC                    Apostas Desportivas à Cota          → sports-wagering
  JFA                    Jogos de Fortuna ou Azar            → igaming
  (Poker folded into igaming for pre-Q3 2022 reports)

PDF format — three distinct layouts depending on publication year:

  FORMAT A (Q1/Q2 2022 — old compact design):
    Page 3 summary table, concatenated text without spaces:
      'ReceitaBruta 158,6 141,1 12,4% ...'
      'ApostasDesportivasàCota 77,7 65,8 18,2% ...'   ← ADC GGR = first number
      'JogosdeFortunaouAzar 80,9 75,3 7,4% ...'        ← JFA GGR = first number
    Page 5 prose (also concatenated), "...cota online foi de 64,7 milhões..."
    and "...atingiu os 81,7 milhões..."

  FORMAT B (Q3 2022 – Q1 2024 — redesigned layout):
    Page 4: JFA/ADC section labels with percentages only (no Receita bruta row).
    Page 5 prose (spaced): two merged-column paragraphs on same line —
      'desportivas à cota foi de 84,0 milhões ... foi de 122,0 milhões ...'
      First 'foi de X milhões' = ADC, second = JFA.

  FORMAT C (Q2 2024+ — updated table):
    Page 4: Section label 'JFA' on own line, then
      'Receita bruta (M€): v1 v2 v3 v4 vN' — last value = current quarter.
    Followed by section label 'ADC' and its own 'Receita bruta (M€): ...' row.
    Page 5 prose unchanged (still has 'foi de X milhões' for confirmation).

  All amounts in EUR millions.  The SRIJ bulletins use Portuguese locale:
  period as thousands separator, comma as decimal: "45.678,23" = 45678.23.
  Values in full EUR (not millions) were used in older editions; the scale
  detector treats values < 1 000 as EUR millions, >= 1 000 as full EUR.

FX conversion: EUR → USD at fixed rate 1.08.
  value_usd_cents = int(eur_millions * 1_000_000 * 1.08 * 100)

Quarter-to-period mapping (last month of each quarter):
  Q1 → YYYY-03   Q2 → YYYY-06   Q3 → YYYY-09   Q4 → YYYY-12

GGR extraction strategies (applied in priority order — first hit wins):
  1. FORMAT C table  : 'JFA'/'ADC' section + 'Receita bruta (M€): ...' last value
  2. FORMAT A/B prose: 'foi de X milhões' on merged paragraph line (ADC=1st, JFA=2nd)
  3. FORMAT A summary: no-space 'ApostasDesportivasàCota X ...' line  (first number)
  4. Legacy fallback : 'atingiu os X milhões' near a JFA section marker
  5. Old label scan  : existing line-walk looking for 'receita bruta' label + value
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .metrics_model import MetricFact

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EUR_TO_USD: float = 1.08

_QUARTER_END_MONTH: dict[int, str] = {
    1: "03",
    2: "06",
    3: "09",
    4: "12",
}

# Section heading fragments → canonical vertical.
# Order: most specific first (longest match wins).
# Includes abbreviations (JFA, ADC) used in the FORMAT C table layout.
_VERTICAL_PATTERNS: list[tuple[str, str]] = [
    ("apostas desportivas à cota",          "sports-wagering"),
    ("apostas desportivas a cota",          "sports-wagering"),   # ascii fallback
    ("apostas desportivas",                 "sports-wagering"),
    ("apostas à cota",                      "sports-wagering"),
    ("apostas a cota",                      "sports-wagering"),   # ascii fallback
    # ADC abbreviation used in page-4 table section labels (FORMAT C)
    ("adc",                                 "sports-wagering"),
    ("jogos de fortuna ou azar",            "igaming"),
    ("jogos de fortuna",                    "igaming"),
    ("casino online",                       "igaming"),
    ("casino",                              "igaming"),
    # JFA abbreviation used in page-4 table section labels (FORMAT C)
    ("jfa",                                 "igaming"),
    # Older editions had a separate poker section; fold into igaming.
    ("póquer online",                       "igaming"),
    ("poker online",                        "igaming"),
    ("póquer",                              "igaming"),
    ("poker",                               "igaming"),
]

# GGR label fragments (Portuguese + occasional English fallback)
_GGR_PATTERNS: list[str] = [
    "receita bruta de jogo",
    "receita bruta",
    "rbj",
    "gross gaming revenue",
    "ggr",
]

# Filename quarter/year extractor.
# Handles all naming conventions used from 2022 onwards:
#   estatisticas_jogo_online_{q}t_{yyyy}.pdf        (Q1/Q2 2022, old stem)
#   estatisticas_online_{q}t_{yyyy}.pdf             (Q3 2022+, new stem)
#   estatisticas_online_{q}t_{yyyy}_v.pdf           (versioned variant seen in live data)
#   estatistica_jogo_online_{q}t_{yyyy}_srij.pdf    (2017 legacy variant)
_FILENAME_RE = re.compile(
    r"estatisticas?(?:_jogo)?_online_(\d)[Tt]_(\d{4})(?:_[a-z]{1,6})?\.pdf$",
    re.IGNORECASE,
)

# Number-cleaning regex: allow digits, period, comma, minus, space
_NUM_CLEAN_RE = re.compile(r"[^\d.,\-]")

# ---------------------------------------------------------------------------
# New-format extraction regexes  (FORMAT B / C — Q3 2022+)
# ---------------------------------------------------------------------------

# FORMAT C table row: 'Receita bruta (M€): v1 v2 ... vN'
# The current-quarter GGR is the LAST space-separated numeric token on the line.
_RBJ_TABLE_ROW_RE = re.compile(
    r"receita\s+bruta\s*\([Mm]",   # prefix; full token filtering done in code
    re.IGNORECASE,
)

# FORMAT B/C prose: 'foi de X milhões' — the standard phrasing for quarterly GGR.
# Appears on merged two-column paragraph lines where ADC appears first, JFA second.
# Also matches 'atingiu os X milhões' (older editions use this for JFA).
_FOI_DE_RE = re.compile(
    r"(?:foi\s+de|atingiu\s+os)\s+([\d]+[.,]\d+)\s+milh",
    re.IGNORECASE,
)

# FORMAT A old summary table (Q1/Q2 2022): no-space concatenated vertical names
# 'ApostasDesportivasàCota 77,7 65,8 ...' or 'JogosdeFortunaouAzar 80,9 ...'
_OLD_SUMMARY_ADC_RE = re.compile(
    r"apostasdesportivas(?:[aà]cota)?[\s,]+([\d]+[.,]\d+)",
    re.IGNORECASE,
)
_OLD_SUMMARY_JFA_RE = re.compile(
    r"jogosdefortuna(?:ouazar)?[\s,]+([\d]+[.,]\d+)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _quarter_period(quarter: int, year: int) -> str:
    """Return the ISO period (YYYY-MM) for the last month of the quarter."""
    return f"{year}-{_QUARTER_END_MONTH[quarter]}"


def _parse_filename(path: Path) -> Optional[tuple[int, int]]:
    """Extract (quarter, year) from filename; returns None on mismatch."""
    m = _FILENAME_RE.search(path.name)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _detect_vertical(text: str) -> Optional[str]:
    """Map a lowercased/normalised text fragment to a canonical vertical."""
    # Normalise accented characters to ASCII for robust matching
    lower = (
        text.lower()
        .replace("ó", "o")
        .replace("à", "a")
        .replace("á", "a")
        .replace("ú", "u")
        .replace("ã", "a")
        .replace("ç", "c")
        .replace("é", "e")
        .replace("ê", "e")
        .replace("í", "i")
        .replace("ô", "o")
    )
    for fragment, vertical in _VERTICAL_PATTERNS:
        # Normalise the fragment the same way
        norm_fragment = (
            fragment
            .replace("ó", "o")
            .replace("à", "a")
            .replace("á", "a")
            .replace("ú", "u")
            .replace("ã", "a")
            .replace("ç", "c")
            .replace("é", "e")
            .replace("ê", "e")
            .replace("í", "i")
            .replace("ô", "o")
        )
        if norm_fragment in lower:
            return vertical
    return None


def _is_ggr_label(text: str) -> bool:
    """Return True if the text appears to be a GGR label row."""
    lower = text.lower()
    return any(pat in lower for pat in _GGR_PATTERNS)


def _parse_eur(raw: str) -> Optional[float]:
    """Parse a Portuguese-locale number string to a plain EUR float.

    Handles formats:
      "45.678.901,23"  →  45678901.23   (PT: . = thousands, , = decimal)
      "45678901,23"    →  45678901.23
      "45.678.901"     →  45678901.0
      "261,8"          →  261.8         (EUR millions; old bulletins)
      "-"  ""          →  None
    """
    s = raw.strip()
    if not s or s in ("-", "—", "N/A", "n/a", "nd", "n.d.", "n/d"):
        return None
    # Remove non-numeric except comma, period, minus
    s = _NUM_CLEAN_RE.sub("", s)
    if not s:
        return None
    # European format: both period and comma present → . = thousands, , = decimal
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        # Comma only: decimal comma if one comma with ≤2 decimal digits and
        # the integer part has ≤6 digits; otherwise treat as thousands sep.
        parts = s.split(",")
        if len(parts) == 2 and len(parts[1]) <= 2 and len(parts[0]) <= 6:
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "." in s:
        # Period only: thousands sep if multiple periods or final group != 2-3 digits
        parts = s.split(".")
        if len(parts) > 2:
            # Multiple periods → all thousands separators
            s = s.replace(".", "")
        elif len(parts) == 2 and len(parts[1]) in (2, 3):
            # Could be decimal ("1234.56") or thousands ("1.234")
            # Heuristic: if integer part ≤ 3 digits, treat as decimal point
            if len(parts[0]) <= 3:
                pass  # leave as-is (decimal period)
            else:
                s = s.replace(".", "")  # thousands separator
        # else: single period → decimal period, leave as-is
    try:
        val = float(s)
    except ValueError:
        return None
    # Reject implausible values (negative or zero)
    if val <= 0:
        return None
    return val


def _scale_to_eur(val: float) -> float:
    """Detect bulletin scale and return full EUR value.

    SRIJ bulletins prior to ~2023 occasionally reported values in EUR millions.
    Heuristic: if val < 1 000, assume EUR millions; else assume full EUR.
    Quarterly GGR per vertical is typically €40M–€250M in full EUR.
    """
    if val < 1_000:
        return val * 1_000_000
    return val


def _eur_to_usd_cents(eur: float) -> int:
    """Convert EUR to USD cents at the fixed project exchange rate."""
    return int(round(eur * _EUR_TO_USD * 100))


# ---------------------------------------------------------------------------
# pdfplumber-based extraction
# ---------------------------------------------------------------------------

def _extract_lines_pdfplumber(path: Path) -> list[str]:
    """Return a flat list of non-empty text lines from all PDF pages."""
    try:
        import pdfplumber  # optional dep; only needed at parse time
    except ImportError as exc:
        raise ImportError(
            "pdfplumber is required to parse SRIJ PDFs. "
            "Install it with: pip install pdfplumber"
        ) from exc

    lines: list[str] = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for line in text.splitlines():
                stripped = line.strip()
                if stripped:
                    lines.append(stripped)
    return lines


# ---------------------------------------------------------------------------
# Core line-based parser
# ---------------------------------------------------------------------------

def _is_numeric_line(line: str) -> bool:
    """Return True if the line looks like a bare number (not a prose sentence).

    Used to guard strategy 4's pending-countdown lookahead against matching
    notification counts, page numbers, or other incidental integers that
    appear in non-numeric context lines.

    A line is considered numeric if, after stripping whitespace and common
    currency/unit tokens, at least 80% of the remaining non-whitespace
    characters are digits, commas, periods, or minus signs.
    """
    # Remove common non-numeric tokens we want to ignore
    cleaned = re.sub(r"[^\d.,\-]", "", line)
    if not cleaned:
        return False
    # Require that the cleaned string looks like a single plausible number
    # (no more than one comma and at most one decimal group)
    # AND that the original line has minimal alphabetic content
    alpha_chars = sum(1 for c in line if c.isalpha())
    digit_chars = sum(1 for c in line if c.isdigit())
    if alpha_chars > 3 or digit_chars == 0:
        return False
    return True


def _rbj_table_last_value(line: str) -> Optional[float]:
    """Extract the last numeric token from a 'Receita bruta (M€): v1 v2 vN' row.

    Returns EUR-millions float or None.
    This handles FORMAT C (Q2 2024+) where the current-quarter GGR is the
    rightmost of several historical values on the same line.
    """
    # Match lines that start with the label (with optional currency symbol variants)
    if not _RBJ_TABLE_ROW_RE.search(line.lower()):
        return None
    # Everything after the ':' character
    colon_pos = line.find(":")
    if colon_pos < 0:
        return None
    values_part = line[colon_pos + 1:].strip()
    # Split on whitespace, keep only tokens that look like plain numbers (no %)
    tokens = values_part.split()
    nums = [t for t in tokens if re.match(r"^-?[\d]+[.,]\d+$", t)]
    if not nums:
        return None
    return _parse_eur(nums[-1])


def _emit(
    vertical: str,
    eur_millions: float,
    period: str,
    source_url: str,
    emitted: set[str],
    facts: list[MetricFact],
) -> None:
    """Append a MetricFact if this vertical has not yet been emitted."""
    if vertical in emitted:
        return
    eur_full = _scale_to_eur(eur_millions)
    facts.append(MetricFact(
        state="PT",
        operator="PT Statewide",
        vertical=vertical,
        period=period,
        metric_name="ggr",
        value_usd_cents=_eur_to_usd_cents(eur_full),
        source_url=source_url,
    ))
    emitted.add(vertical)


def _parse_lines(
    lines: list[str],
    quarter: int,
    year: int,
    source_url: str,
) -> list[MetricFact]:
    """Extract MetricFacts by walking the flat PDF line list.

    Applies four strategies in priority order (first hit per vertical wins):

    Strategy 1 — FORMAT C table (Q2 2024+):
      After a 'JFA' or 'ADC' section label line, look for
      'Receita bruta (M€): v1 v2 ... vN'; extract the last value.

    Strategy 2 — Prose dual-column (all formats, most reliable):
      Lines containing 'foi de X milhões' or 'atingiu os X milhões'.
      When the merged paragraph carries both columns, the FIRST occurrence
      is ADC and the SECOND is JFA.

    Strategy 3 — FORMAT A old summary table (Q1/Q2 2022):
      No-space concatenated strings 'ApostasDesportivasàCota X ...'
      and 'JogosdeFortunaouAzar X ...'.

    Strategy 4 — Legacy label scan fallback:
      Look for 'receita bruta' label rows, try to parse a value from the
      same line or the following lines (handles any residual format
      variants not covered by strategies 1–3).
    """
    period = _quarter_period(quarter, year)
    facts: list[MetricFact] = []
    emitted: set[str] = set()

    current_vertical: Optional[str] = None
    pending_section: Optional[str] = None   # for strategy 1 table look-ahead
    pending_ggr_vertical: Optional[str] = None
    pending_countdown: int = 0

    for line in lines:
        lower = line.lower()

        # ── Strategy 1: FORMAT C table row ───────────────────────────────────
        # Detect JFA / ADC section labels (single-token lines like 'JFA', 'ADC')
        if lower.strip() == "jfa":
            pending_section = "igaming"
        elif lower.strip() == "adc":
            pending_section = "sports-wagering"
        elif pending_section and _RBJ_TABLE_ROW_RE.search(lower):
            val = _rbj_table_last_value(line)
            if val is not None:
                _emit(pending_section, val, period, source_url, emitted, facts)
            pending_section = None
        elif pending_section and lower.strip() and not lower.strip().startswith("%"):
            # Non-empty, non-percentage line resets the look-ahead unless it's
            # the '%' summary line that always immediately follows the label.
            if not re.match(r"^%|receita bruta", lower.strip()):
                pending_section = None

        # ── Strategy 2: prose dual-column 'foi de X milhões' ─────────────────
        # Only attempt on lines that do NOT also contain a GGR label trigger
        # (to avoid double-processing merged heading lines).
        if "milh" in lower and not _RBJ_TABLE_ROW_RE.search(lower):
            matches = _FOI_DE_RE.findall(line)
            if len(matches) >= 2:
                # Two values on same line — first=ADC, second=JFA
                adc_val = _parse_eur(matches[0])
                jfa_val = _parse_eur(matches[1])
                if adc_val is not None:
                    _emit("sports-wagering", adc_val, period, source_url, emitted, facts)
                if jfa_val is not None:
                    _emit("igaming", jfa_val, period, source_url, emitted, facts)
            elif len(matches) == 1:
                # Single value — need vertical context from surrounding text
                val = _parse_eur(matches[0])
                if val is not None:
                    # Determine vertical from this line's content
                    if "cota" in lower or "desportivas" in lower:
                        _emit("sports-wagering", val, period, source_url, emitted, facts)
                    elif "fortuna" in lower or "azar" in lower or "jfa" in lower:
                        _emit("igaming", val, period, source_url, emitted, facts)

        # ── Strategy 3: FORMAT A old no-space summary table ──────────────────
        m_adc = _OLD_SUMMARY_ADC_RE.search(lower)
        if m_adc:
            val = _parse_eur(m_adc.group(1))
            if val is not None:
                _emit("sports-wagering", val, period, source_url, emitted, facts)
        m_jfa = _OLD_SUMMARY_JFA_RE.search(lower)
        if m_jfa:
            val = _parse_eur(m_jfa.group(1))
            if val is not None:
                _emit("igaming", val, period, source_url, emitted, facts)

        # ── Strategy 4: legacy label scan fallback ────────────────────────────
        # Track section context for fallback GGR extraction.
        # Skip the combined dual-heading line (contains both verticals on one
        # line) which would incorrectly set current_vertical to whichever
        # vertical is detected last.
        vert_from_line = _detect_vertical(lower)
        if vert_from_line:
            # Only update current_vertical if the line belongs to a single
            # vertical (i.e. does not also contain the other vertical's name).
            both_present = (
                ("apostas desportivas" in lower or "adc" == lower.strip()) and
                ("jogos de fortuna" in lower or "jfa" == lower.strip() or
                 "fortuna ou azar" in lower)
            )
            if not both_present:
                current_vertical = vert_from_line
                pending_ggr_vertical = None
                pending_countdown = 0

        # GGR label detection for strategy 4 (legacy fallback).
        # Only fires when "receita bruta" is at the BEGINNING of the line
        # (after stripping leading whitespace/punctuation) — this avoids
        # false positives from prose sentences where "receita bruta" is
        # embedded mid-sentence (e.g. "em Receita Bruta, um crescimento...").
        # Also skips FORMAT C table rows, milh-containing lines (S1/S2),
        # and chart-title / legend lines.
        _lstrip = lower.lstrip(" |0123456789")
        _s4_label_at_start = (
            _lstrip.startswith("receita bruta de jogo")
            or _lstrip.startswith("receita bruta:")
            or _lstrip.startswith("rbj")
            or _lstrip.startswith("gross gaming revenue")
            or _lstrip.startswith("ggr")
        )
        if (
            _s4_label_at_start
            and current_vertical
            and "milh" not in lower
            and not _RBJ_TABLE_ROW_RE.search(lower)
            and "fig." not in lower
        ):
            if current_vertical not in emitted:
                remainder = line
                for pat in _GGR_PATTERNS:
                    remainder = re.sub(pat, "", remainder, flags=re.IGNORECASE).strip()
                val = _parse_eur(remainder) if remainder else None
                if val is not None:
                    _emit(current_vertical, val, period, source_url, emitted, facts)
                    pending_ggr_vertical = None
                    pending_countdown = 0
                else:
                    pending_ggr_vertical = current_vertical
                    pending_countdown = 8

        elif pending_ggr_vertical and pending_countdown > 0:
            pending_countdown -= 1
            if pending_ggr_vertical not in emitted:
                # Only accept bare-number lines to avoid grabbing incidental
                # integers (notification counts, page numbers, etc.)
                if _is_numeric_line(line):
                    val = _parse_eur(line)
                    if val is not None:
                        _emit(pending_ggr_vertical, val, period, source_url, emitted, facts)
                        pending_ggr_vertical = None
                        pending_countdown = 0
            else:
                pending_ggr_vertical = None
                pending_countdown = 0

        elif pending_countdown > 0:
            pending_countdown -= 1

    return facts


# ---------------------------------------------------------------------------
# Public API — called by backfill._backfill_via_collector
# ---------------------------------------------------------------------------

def parse_pt_pdf(path: Path, source_url: str) -> list[MetricFact]:
    """Parse a SRIJ quarterly online gambling statistics PDF.

    Extracts GGR facts for the two active online verticals:
      sports-wagering  (Apostas Desportivas à Cota)
      igaming          (Jogos de Fortuna ou Azar / Casino Online)

    Args:
        path:       Local path to the downloaded PDF.
        source_url: Original SRIJ URL recorded on each MetricFact.

    Returns:
        List of MetricFact with state="PT", operator="PT Statewide",
        period=YYYY-MM (last month of the bulletin's quarter),
        metric_name="ggr", value_usd_cents=int(eur*1.08*100).
    """
    qy = _parse_filename(path)
    if qy is None:
        # Fallback: try to derive quarter/year from source URL
        m = _FILENAME_RE.search(source_url)
        if m:
            qy = (int(m.group(1)), int(m.group(2)))
        else:
            raise ValueError(
                f"Cannot determine quarter/year from filename: {path.name!r}"
            )

    quarter, year = qy
    lines = _extract_lines_pdfplumber(path)
    return _parse_lines(lines, quarter, year, source_url)


# ---------------------------------------------------------------------------
# Convenience wrappers for direct import in scripts and unit tests
# ---------------------------------------------------------------------------

def parse_pt_q1(path: Path, year: int, source_url: str) -> list[MetricFact]:
    """Parse a Q1 (January–March) SRIJ quarterly bulletin."""
    lines = _extract_lines_pdfplumber(path)
    return _parse_lines(lines, 1, year, source_url)


def parse_pt_q2(path: Path, year: int, source_url: str) -> list[MetricFact]:
    """Parse a Q2 (April–June) SRIJ quarterly bulletin."""
    lines = _extract_lines_pdfplumber(path)
    return _parse_lines(lines, 2, year, source_url)


def parse_pt_q3(path: Path, year: int, source_url: str) -> list[MetricFact]:
    """Parse a Q3 (July–September) SRIJ quarterly bulletin."""
    lines = _extract_lines_pdfplumber(path)
    return _parse_lines(lines, 3, year, source_url)


def parse_pt_q4(path: Path, year: int, source_url: str) -> list[MetricFact]:
    """Parse a Q4 (October–December) SRIJ quarterly bulletin."""
    lines = _extract_lines_pdfplumber(path)
    return _parse_lines(lines, 4, year, source_url)
