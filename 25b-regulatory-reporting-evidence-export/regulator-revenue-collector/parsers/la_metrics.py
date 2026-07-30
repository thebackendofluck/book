# Companion code for "The Backend of Luck" - Chapter 25b, Regulatory Reporting and Evidence Export.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Louisiana State Police / LGCB monthly-revenue PDF parser.

The LSP "Gaming Revenue Reports" page hosts three flavours of monthly
PDFs, each with a distinct text-layer layout:

  1. Riverboat / land-based / racetrack / video-poker (per-operator):
        FOR THE MONTH OF: <MONTH> <YYYY>
        Licensee | <Mon-YY> | <PrevMon-YY> | Difference | % | <Mon-YY>(prior) ...
        e.g. "BOOMTOWN BOSSIER  $4,115,114 ..."
     We emit one MetricFact per operator for the current-month "win"
     column, plus a state-aggregate "win" total.

  2. Sports book retail / mobile (statewide aggregate, FY-monthly):
        Header: "Statewide Mobile Sports Book"
        Data rows keyed by short month label "Jul-25 ... Feb-26" with
        columns Wagers Written | Promo Deduct | Net Proceeds | Taxes Paid.
     We emit handle (Wagers Written), ggr (Net Proceeds), tax_paid for
     each Mon-YY row in the "FY 26" / "FY 25" blocks.

  3. Daily Fantasy Sports (statewide aggregate, FY-monthly):
        Header: "Louisiana Daily Fantasy Sports"
        Columns: Gross FS Revenues | FY25 Gross | Contest Revenue | ...
                 Net Revenue | Taxes Paid
        Row label = month name ("July", "August", ...).
     Period derived from row label + the FY year embedded in the URL.

All monetary values are converted to USD cents.

Returns [] if no parseable content is found — never raises.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pdfplumber

from .metrics_model import MetricFact

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Numeric / period helpers
# ---------------------------------------------------------------------------

_MONTHS = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "may": "05", "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "sept": "09", "oct": "10", "nov": "11", "dec": "12",
    "january": "01", "february": "02", "march": "03", "april": "04",
    "june": "06", "july": "07", "august": "08", "september": "09",
    "october": "10", "november": "11", "december": "12",
}

_MONEY_STRIP = re.compile(r"[\$,\s\xa0]")


def _to_cents(raw: object) -> int | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if s in ("", "-", "–", "—", "N/A", "n/a"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    s = _MONEY_STRIP.sub("", s)
    if not s:
        return None
    # Reject anything that still has stray non-numeric chars (other than
    # a leading sign or single decimal point).
    m = re.fullmatch(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        val = float(s)
    except ValueError:
        return None
    return int(round(val * 100)) * (-1 if neg else 1)


def _period_from_short_month(s: str) -> str | None:
    """'Jul-25' / 'Mar 2026' / 'July' (with year context) → 'YYYY-MM'."""
    if not s:
        return None
    m = re.search(r"([A-Za-z]{3,9})\s*[-/\s]\s*(\d{2,4})", str(s))
    if not m:
        return None
    mon = m.group(1).lower()
    if mon not in _MONTHS:
        return None
    yr = m.group(2)
    if len(yr) == 2:
        yr = "20" + yr
    return f"{yr}-{_MONTHS[mon]}"


def _period_from_url(url: str) -> tuple[str | None, str | None]:
    """Return (current_month_period, fy_end_year_str) from filename.

    Examples:
      .../2026-03-2-riverboat-by-market-gaming-revenues.pdf
        → ('2026-03', None)
      .../fy26_mobile_sb_2026_02_feb.pdf
        → ('2026-02', '2026')
      .../fy26_dfs_2025_09_sept.pdf
        → ('2025-09', '2026')
      .../202407_july_fy25_la_dfs_rev.pdf
        → ('2024-07', '2025')
      .../2024-06_dfs_revenue.pdf
        → ('2024-06', None)
    """
    name = url.rsplit("/", 1)[-1].lower()

    # 1. Direct YYYY_MM_<mon> embed (sports / dfs newer)
    m = re.search(r"_(20\d{2})[_-](\d{1,2})[_-]([a-z]{3,9})", name)
    if m:
        period = f"{m.group(1)}-{int(m.group(2)):02d}"
        fy_m = re.search(r"\bfy(\d{2})(?:[-_]?\d{2})?\b", name)
        fy = "20" + fy_m.group(1) if fy_m else None
        return period, fy

    # 2. Compact YYYYMM_<mon> (older)
    m = re.search(r"\b(20\d{2})(\d{2})_([a-z]{3,9})", name)
    if m:
        period = f"{m.group(1)}-{int(m.group(2)):02d}"
        fy_m = re.search(r"\bfy(\d{2})", name)
        fy = "20" + fy_m.group(1) if fy_m else None
        return period, fy

    # 3. Casino (riverboat / land-based / video poker): YYYY-MM-…
    m = re.search(r"\b(20\d{2})-(\d{1,2})", name)
    if m:
        period = f"{m.group(1)}-{int(m.group(2)):02d}"
        return period, None

    return None, None


# ---------------------------------------------------------------------------
# Casino (riverboat / land-based / racetrack / video poker) per-operator
# ---------------------------------------------------------------------------

# Lower-cased operator markers found across LA casino PDFs.
_CASINO_OPERATORS = (
    "boomtown", "bally", "horseshoe", "live! casino", "sam's town",
    "margaritaville", "l'auberge", "golden nugget", "amelia belle",
    "treasure chest", "the queen", "queen baton rouge", "harrah",
    "evangeline downs", "delta downs", "fair grounds", "louisiana downs",
    "paragon", "coushatta",
)

_CASINO_TOTAL_LABELS = re.compile(
    r"^(total\b|riverboat\s+total|land[-\s]?based\s+total|"
    r"racetrack\s+total|video\s+(gaming|poker)\s+total)",
    re.I,
)


def _is_casino_operator_row(label: str) -> bool:
    low = label.lower().strip()
    if not low:
        return False
    return any(op in low for op in _CASINO_OPERATORS)


# Video-gaming PDFs are organised by venue type (BARS, RESTAURANTS, ...)
# instead of by operator brand.
_VG_VENUE_RE = re.compile(
    r"^(BARS|RESTAURANTS|HOTELS|RACETRACKS\s+OTBS|TRUCKSTOPS|TOTALS)\b",
)


def _parse_casino_pdf(
    pdf: pdfplumber.PDF,
    period: str,
    source_url: str,
    vertical: str,
) -> list[MetricFact]:
    """Extract per-operator and statewide 'win' for the current month."""
    facts: list[MetricFact] = []
    seen: set[tuple[str, str]] = set()  # (operator, metric_name)

    for page in pdf.pages:
        text = page.extract_text() or ""
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue

            # Video-gaming venue-type rows ("BARS 2,578 780 9,977,272 $...")
            # The PDF has both per-venue rows (BARS, RESTAURANTS, etc.) and a
            # TOTALS row that is the grand sum of all venues.  Emitting both
            # would double-count, so we keep ONLY the TOTALS row.
            if vertical == "video-gaming":
                vg_m = _VG_VENUE_RE.match(stripped)
                if vg_m:
                    venue = vg_m.group(1)
                    # Skip individual venue rows — only the TOTALS row is emitted
                    # to avoid double-counting venue subtotals.
                    if venue.upper() != "TOTALS":
                        continue
                    # For the TOTALS row the first unspaced $-token is the
                    # grand total (YTD NDR).
                    money = re.search(r"\$-?[\d,]+(?:\.\d+)?", stripped)
                    if money:
                        cents = _to_cents(money.group(0))
                        if cents is not None:
                            key = (venue, "win")
                            if key not in seen:
                                seen.add(key)
                                facts.append(MetricFact(
                                    state="LA", operator=venue,
                                    vertical=vertical,
                                    period=period, metric_name="ggr",
                                    value_usd_cents=cents,
                                    source_url=source_url,
                                ))
                    continue

            # Statewide total / subtotal rows — skip entirely for
            # commercial-casino to avoid double-counting individual operators.
            # The per-operator rows are the authoritative source; regional
            # subtotals (Total Shreveport/Bossier, Total Lake Charles, …) and
            # the grand total (Riverboat Total) are all redundant.
            if _CASINO_TOTAL_LABELS.match(stripped):
                continue

            # Per-operator rows. Require a $-prefixed money token to avoid
            # picking up dates / counts / admissions (some LA layouts include
            # admin columns like "02/13/02 30 75,650 $13,239,872 ..." before
            # the first dollar-denominated AGR value).
            if not _is_casino_operator_row(stripped):
                continue
            m = re.search(r"\$-?[\d,]+(?:\.\d+)?", stripped)
            if not m:
                continue
            label = stripped[:m.start()].strip(" .:")
            # Strip trailing admin columns (opening date, gaming days,
            # admissions count, etc.) by trimming everything from the first
            # all-digit / date-shaped token onward.
            label = re.sub(
                r"\s+(\d{1,2}/\d{1,2}/\d{2,4}|\d{1,3}(?:,\d{3})*).*$",
                "",
                label,
            ).strip(" .:")
            if not label:
                continue
            cents = _to_cents(m.group(0))
            if cents is None:
                continue
            key = (label, "win")
            if key in seen:
                continue
            seen.add(key)
            facts.append(MetricFact(
                state="LA", operator=label, vertical=vertical,
                period=period, metric_name="ggr",
                value_usd_cents=cents, source_url=source_url,
            ))

    return facts


# ---------------------------------------------------------------------------
# Sports book (statewide aggregate, FY-monthly table)
# ---------------------------------------------------------------------------

# Match "Jul-25 23.9% 213,408,818 (1,192,315) 30,130,998 4,519,650 14.1% ..."
# Capture: month label, then keep the rest as numeric tokens.
_SB_ROW_RE = re.compile(
    r"^([A-Za-z]{3,4}-\d{2})\s+"        # Jul-25
    r"(.+)$"                             # rest of row (mixed numbers/percents)
)


def _parse_sportsbook_pdf(
    pdf: pdfplumber.PDF,
    source_url: str,
    operator_label: str,
) -> list[MetricFact]:
    """Statewide retail or mobile sports book.

    Layout per FY block:
      Jul-25  vs%   Wagers   PromoDeduct   NetProceeds   TaxesPaid   Win%   ...

    We emit handle (Wagers Written), ggr (Net Proceeds), tax_paid (Taxes Paid).

    NOTE — positive tax on negative-GGR months (e.g. Nov 2022, World Cup):
    Louisiana law allows operators to carry forward losses to offset future
    net proceeds, so actual tax payments may not follow the nominal 15% rate.
    In months where a heavy operator-friendly sport dominates (e.g. soccer
    during the World Cup), aggregate net proceeds can go deeply negative while
    some licensees still owe minimum or catch-up tax payments.  A positive
    tax_paid alongside a negative ggr is therefore real, not a parse artifact.
    LGCB Nov 2022 report (FY23/24 historical table) footnote 1 confirms:
    "Due to state law allowing losses incurred by operators to offset future
    net proceeds, the actual tax payments received may not calculate to the
    15% tax rate."
    """
    facts: list[MetricFact] = []
    seen: set[tuple[str, str, str]] = set()

    for page in pdf.pages:
        text = page.extract_text() or ""
        for raw in text.splitlines():
            line = raw.strip()
            m = _SB_ROW_RE.match(line)
            if not m:
                continue
            period = _period_from_short_month(m.group(1))
            if not period:
                continue
            # Tokenize: walk all numeric runs and drop any followed by
            # '%' or '.<digit>%' (vs-prior-year and win-% columns).
            rest = m.group(2)
            raw_tokens = re.findall(
                r"\(?-?\$?[\d,]+(?:\.\d+)?\)?",
                rest,
            )
            # Filter out percentage-shaped tokens by re-checking each match
            # in context.
            tokens: list[str] = []
            cursor = 0
            for tok in raw_tokens:
                idx = rest.find(tok, cursor)
                if idx < 0:
                    continue
                cursor = idx + len(tok)
                # Look ahead in the original string
                tail = rest[cursor:cursor + 2]
                if tail.lstrip().startswith("%"):
                    continue
                tokens.append(tok)
            # Need at least: Wagers, PromoDeduct, NetProceeds, Taxes
            if len(tokens) < 4:
                continue
            handle = _to_cents(tokens[0])
            # tokens[1] is promo deduct (often parenthesised negative)
            net_proceeds = _to_cents(tokens[2])
            tax_paid = _to_cents(tokens[3])

            for metric, cents in (
                ("handle", handle),
                ("ggr", net_proceeds),
                ("tax_paid", tax_paid),
            ):
                if cents is None:
                    continue
                key = (operator_label, period, metric)
                if key in seen:
                    continue
                seen.add(key)
                facts.append(MetricFact(
                    state="LA",
                    operator=operator_label,
                    vertical="sports-wagering",
                    period=period,
                    metric_name=metric,
                    value_usd_cents=cents,
                    source_url=source_url,
                ))
    return facts


# ---------------------------------------------------------------------------
# Daily Fantasy Sports (statewide aggregate)
# ---------------------------------------------------------------------------

# Row pattern: "July 366,809 40,801 3,264 406,300 -9.7% 43,251 -5.7%"
# Tokens: [Gross_FY26, Net_FY26, Taxes_FY26, Gross_FY25, vs%, Net_FY25, vs%]
_DFS_ROW_RE = re.compile(
    r"^(January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+(.+)$"
)


def _parse_dfs_pdf(
    pdf: pdfplumber.PDF,
    fy_end_year: str | None,
    period_hint: str | None,
    source_url: str,
) -> list[MetricFact]:
    """Statewide DFS revenue.

    Each row is a month within FY{fy_end_year}. The FY runs Jul {fy-1} →
    Jun {fy}. We use the URL's fy_end_year to map the row month to a
    calendar period; if the URL only gave us a single-month period_hint
    (older single-month files), we use that directly when the row label
    matches.
    """
    facts: list[MetricFact] = []
    seen: set[tuple[str, str]] = set()

    fy_end = int(fy_end_year) if fy_end_year else None

    for page in pdf.pages:
        text = page.extract_text() or ""
        for raw in text.splitlines():
            line = raw.strip()
            m = _DFS_ROW_RE.match(line)
            if not m:
                continue
            month_name = m.group(1).lower()
            month_num = _MONTHS.get(month_name[:3])
            if not month_num:
                continue
            month_int = int(month_num)
            # Resolve calendar year:
            # FY ends in June → months Jul–Dec belong to fy_end-1, Jan–Jun to fy_end.
            if fy_end:
                year = fy_end - 1 if month_int >= 7 else fy_end
                period = f"{year}-{month_num}"
            elif period_hint:
                # Single-month file — only emit the matching row.
                if not period_hint.endswith(f"-{month_num}"):
                    continue
                period = period_hint
            else:
                continue

            rest = m.group(2)
            raw_tokens = re.findall(
                r"\(?-?\$?[\d,]+(?:\.\d+)?\)?",
                rest,
            )
            tokens: list[str] = []
            cursor = 0
            for tok in raw_tokens:
                idx = rest.find(tok, cursor)
                if idx < 0:
                    continue
                cursor = idx + len(tok)
                tail = rest[cursor:cursor + 2]
                if tail.lstrip().startswith("%"):
                    continue
                tokens.append(tok)
            if len(tokens) < 3:
                continue
            handle = _to_cents(tokens[0])    # Gross FS Revenues (wagers proxy)
            net_rev = _to_cents(tokens[1])   # Contest Revenue (kept as ggr)
            tax_paid = _to_cents(tokens[2])  # Taxes Paid

            for metric, cents in (
                ("handle", handle),
                ("ggr", net_rev),
                ("tax_paid", tax_paid),
            ):
                if cents is None:
                    continue
                key = (period, metric)
                if key in seen:
                    continue
                seen.add(key)
                facts.append(MetricFact(
                    state="LA",
                    operator="LA Statewide DFS",
                    vertical="fantasy",
                    period=period,
                    metric_name=metric,
                    value_usd_cents=cents,
                    source_url=source_url,
                ))
    return facts


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_la_pdf(
    path: Path,
    vertical: str,
    source_url: str,
) -> list[MetricFact]:
    """Parse a Louisiana monthly revenue PDF.

    Args:
        path:       Local path to the downloaded PDF.
        vertical:   "commercial-casino" | "sports-wagering" |
                    "video-gaming" | "fantasy"
        source_url: Canonical lsp.org URL (used for period extraction
                    and stamped on every emitted fact).

    Returns:
        List of MetricFact records. Empty list on parse failure.
    """
    try:
        pdf_path = Path(path)
        if not pdf_path.exists():
            logger.warning("la_metrics: file not found: %s", pdf_path)
            return []

        with pdf_path.open("rb") as fh:
            magic = fh.read(4)
        if magic != b"%PDF":
            logger.warning("la_metrics: not a PDF (magic=%r): %s", magic, pdf_path)
            return []

        period, fy_end = _period_from_url(source_url)
        name_lower = source_url.rsplit("/", 1)[-1].lower()

        with pdfplumber.open(str(pdf_path)) as pdf:
            if vertical in ("commercial-casino", "video-gaming"):
                if not period:
                    return []
                return _parse_casino_pdf(pdf, period, source_url, vertical)

            if vertical == "sports-wagering":
                operator = (
                    "LA Statewide Mobile" if "mobile" in name_lower
                    else "LA Statewide Retail" if "retail" in name_lower
                    else "LA Statewide Sportsbook"
                )
                return _parse_sportsbook_pdf(pdf, source_url, operator)

            if vertical == "fantasy":
                return _parse_dfs_pdf(pdf, fy_end, period, source_url)

        return []
    except Exception:
        logger.exception("la_metrics: failed to parse %s", path)
        return []
