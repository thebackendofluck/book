# Companion code for "The Backend of Luck" - Chapter 25b, Regulatory Reporting and Evidence Export.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Rhode Island Lottery monthly revenue PDF parser.

The RI Lottery publishes one cumulative-FY PDF per vertical, with all
months for the FY in a single page. Each month-row repeats per-operator
column blocks side-by-side.

Layouts (confirmed across FY2019–FY2026):

  Sportsbook (NOV_SportsbookWebsiteData.pdf, SportsBookSummaryFY{Y}.pdf):
    Operator blocks: Twin River | Tiverton Casino | Online (Mobile) | Combined
    Per block:       Write (Accrual) | Payout (Accrual) | Book Revenue
    Row:  "Jul 25 $1,684,510 $1,435,151 $249,359  Jul 25 $...  Jul 25 $...  Jul 25 $..."

  iGaming (NOV_iGamingWebsiteData.pdf, iGamingWebsiteData{Y}.pdf):
    Operator blocks: iSlots | iTables | Combined
    Per block:       Wagers (Accrual) | Prizes (Accrual) | Net Gaming Revenue (NGR)

  Table Games (NOV_TableGamesWebsiteFYE_{Y}.pdf, TableGamesSummaryFY{Y}.pdf):
    Three sections (Twin River, Tiverton Casino, Combined), each with:
      Total Net Table Games Revenue | Operator Net | Town Net | RI Lottery Net
    Row:  "Jul $7,826,448 $6,535,084 $78,265 $1,213,099"

pdfplumber's table extractor returns no rows for these PDFs (vector-art
layout), so we operate on the raw text layer. Every row is normalised to
strip the cosmetic spaces inside numbers ("1 ,234,567" → "1,234,567").

Mapping to canonical metric names:
  sportsbook  Write   → handle
              Payout  → patron_winnings
              Book    → ggr
  iGaming     Wagers  → handle
              Prizes  → patron_winnings
              NGR     → ggr
  table       Total   → ggr (operator-level)
              Net (operator) → ggr_operator_share
              Town Net       → tax_paid_municipal
              RI Lottery Net → tax_paid

All values are USD cents. Returns [] on parse failure — never raises.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pdfplumber

from .metrics_model import MetricFact

logger = logging.getLogger(__name__)

_MONTHS = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "may": "05", "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "sept": "09", "oct": "10", "nov": "11", "dec": "12",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MONEY_TOKEN_RE = re.compile(r"\(?\$?-?[\d,]+(?:\.\d+)?\)?")


def _clean_line(s: str) -> str:
    """Collapse the cosmetic spaces pdfplumber leaves inside numbers.

    Targets only mid-number splits, NEVER touches valid "<Mon YY> <num>"
    separations. We detect a mid-number split by requiring that the digit
    AFTER the space is followed by either a comma (",") or a decimal point
    leading into more digits — i.e. it's the start of a thousands group.

    Examples handled:
      "$ 1,234"           → "$1,234"
      "$ 1 ,234,567"      → "$1,234,567"
      "$ 1 20,444,030"    → "$120,444,030"
      "1 30,697,888"      → "130,697,888"

    Examples preserved (NOT collapsed):
      "Aug 25 2,275,726"  → unchanged (the "5 2" isn't mid-number; the "2" is
                            the start of a fresh number, not a thousands group)

    Because the "Aug 25" case looks structurally identical to the mid-number
    case ("digit space digit-followed-by-comma"), we additionally protect any
    sequence matching "<3-or-4 letter alpha> <2-digit year>" by tagging the
    year before normalisation and untagging afterwards.
    """
    s = s.replace("\xa0", " ")
    # "$ 1,234" → "$1,234"
    s = re.sub(r"\$\s+", "$", s)

    # 1. Protect month-year pairs AND the trailing space after the year:
    #    "Aug 25 2,275" → "Aug␟25␟2,275" so the collapse below cannot eat
    #    either separator.
    s = re.sub(
        r"\b([A-Za-z]{3,4})\s+(\d{2})(\s+)",
        lambda m: f"{m.group(1)}␟{m.group(2)}␟",
        s,
    )

    # 2. "1 ,234,567" → "1,234,567"  (digit immediately followed by space
    #    then a comma is always mid-number)
    s = re.sub(r"(\d)\s+,", r"\1,", s)
    # 3. "$1 20,444" → "$120,444" — only collapse when the LEFT digit run is
    #    1-2 digits AND is NOT preceded by another digit or comma (meaning
    #    it's the leading digits of a number, not a complete thousands group
    #    like ",,275,726"). Right side must be 1-2 digits leading into a
    #    thousands separator.
    s = re.sub(
        r"(?<![\d,])(\d{1,2}) (\d{1,2}),",
        r"\1\2,",
        s,
    )
    # second pass for "1 2 3,456" stair-stepped splits
    s = re.sub(
        r"(?<![\d,])(\d{1,2}) (\d{1,2}),",
        r"\1\2,",
        s,
    )

    # 4. Restore month-year separators (single sentinel char U+241F)
    s = s.replace("␟", " ")
    return s


def _to_cents(raw: str) -> int | None:
    if raw is None:
        return None
    s = raw.strip()
    if s in ("", "-", "–", "—", "N/A"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    s = s.replace("$", "").replace(",", "").replace(" ", "")
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", s):
        return None
    try:
        val = float(s)
    except ValueError:
        return None
    return int(round(val * 100)) * (-1 if neg else 1)


# ---------------------------------------------------------------------------
# Period extraction
# ---------------------------------------------------------------------------

# Sportsbook & iGaming format: "Jul 25 $ ..." or "Jul 22 $ ..."
# The 2-digit year MUST NOT be immediately followed by "," — if it is, the
# digits are the leading portion of a dollar amount (e.g. "Dec 11,092,168"
# where "11" is part of "$11,092,168", not year 2011).  This was the root
# cause of spurious pre-2018 data points parsed from the FY2019 sportsbook
# PDF (format: bare month + bare number, no year token, no "$" prefix).
_MONTH_YEAR_RE = re.compile(r"\b([A-Za-z]{3,4})\s+(\d{2})(?!,)(?=\s|\$|\Z)")
# Table-games format: bare "Jul $..." or "Aug 10,613,806 ..." (no year on
# row, FY in header). Match the leading 3-4-letter month token followed by
# whitespace and either a "$" or a digit.
_BARE_MONTH_RE = re.compile(r"^([A-Za-z]{3,4})\s+(?:\$|\d)")


def _period_from_my(month: str, yr2: str) -> str | None:
    mon = month[:3].lower()
    if mon not in _MONTHS:
        return None
    return f"20{yr2}-{_MONTHS[mon]}"


def _fy_from_text(text: str) -> int | None:
    """Find 'FY 2026' / 'FY 2023' on the page; return the FY-end year."""
    m = re.search(r"\bFY\s*(20\d{2})\b", text)
    if m:
        return int(m.group(1))
    m = re.search(r"\bFY\s*(\d{2})\b", text)
    if m:
        return 2000 + int(m.group(1))
    m = re.search(r"Year\s+Ending\s+June\s+30,\s+(20\d{2})", text, re.I)
    if m:
        return int(m.group(1))
    return None


# ---------------------------------------------------------------------------
# Sportsbook & iGaming rows: "<Mon YY> $a $b $c  <Mon YY> $a $b $c ..."
# ---------------------------------------------------------------------------

def _split_blocks(line: str) -> list[tuple[str, list[str]]]:
    """Split a row into (period, [3 numeric tokens]) blocks.

    The line contains N repetitions of "<Mon YY> $a $b $c". We split on
    the month-year markers and take the first 3 monetary tokens after each.
    """
    matches = list(_MONTH_YEAR_RE.finditer(line))
    blocks: list[tuple[str, list[str]]] = []
    for i, m in enumerate(matches):
        period = _period_from_my(m.group(1), m.group(2))
        if not period:
            continue
        end = matches[i + 1].start() if i + 1 < len(matches) else len(line)
        chunk = line[m.end():end]
        tokens = _MONEY_TOKEN_RE.findall(chunk)
        # Keep at most 3 numeric tokens per block.
        tokens = tokens[:3]
        if tokens:
            blocks.append((period, tokens))
    return blocks


def _parse_sportsbook_bare(
    pdf: pdfplumber.PDF,
    source_url: str,
    fy_end: int,
) -> list[MetricFact]:
    """Parse the FY2019 bare-month sportsbook format.

    The FY2019 PDF predates the "Mon YY $ value" layout used from FY2020
    onwards.  Each data row looks like:
      "Nov 682,714 609,717 72,997 ∆ Nov - - - Nov 682,714 609,717 72,997"
    i.e. bare month name (no 2-digit year) + three numeric columns, repeated
    for each operator block side by side.

    There are only 3 operator columns in FY2019 (no "Online (Mobile)" block):
      Twin River | Tiverton Casino | Combined

    Period is derived from the document's FY header (e.g. "FY 2019") using the
    same Jul→Jun calendar logic as table-games:
      months Jul–Dec → fy_end-1
      months Jan–Jun → fy_end
    """
    operators = ["Twin River", "Tiverton Casino", "Combined"]
    metrics = ["handle", "patron_winnings", "ggr"]  # Write, Payout, Book Revenue
    facts: list[MetricFact] = []
    seen: set[tuple[str, str, str]] = set()

    for page in pdf.pages:
        text = page.extract_text() or ""
        for raw in text.splitlines():
            line = _clean_line(raw.strip())
            if re.match(r"^(total|fy|write|payout|book)\b", line, re.I):
                continue
            # Only process rows that start with a bare month name.
            m = _BARE_MONTH_RE.match(line)
            if not m:
                continue
            mon_str = m.group(1)[:3].lower()
            if mon_str not in _MONTHS:
                continue
            month_int = int(_MONTHS[mon_str])
            year = fy_end - 1 if month_int >= 7 else fy_end
            period = f"{year}-{_MONTHS[mon_str]}"

            # Each line has N repetitions of "Mon [delta/footnote] val val val"
            # Split by bare-month markers to get per-operator chunks.
            # Use _BARE_MONTH_RE to find all operator blocks within the line.
            sub_blocks: list[list[str]] = []
            positions = [sm.start() for sm in re.finditer(
                r"\b([A-Za-z]{3,4})\s+(?:\$|\d|\(|-)", line
            ) if line[sm.start():sm.start()+3].lower()[:3] in _MONTHS]
            positions.append(len(line))
            for idx in range(len(positions) - 1):
                chunk = line[positions[idx]:positions[idx + 1]]
                # Strip the leading month token.
                chunk = re.sub(r"^[A-Za-z]{3,4}\s+", "", chunk)
                # Remove footnote symbols (∆, £, *, etc.)
                chunk = re.sub(r"[^\d,.()\-\$\s]", " ", chunk)
                tokens = _MONEY_TOKEN_RE.findall(chunk)[:3]
                sub_blocks.append(tokens)

            for op_idx, tokens in enumerate(sub_blocks):
                if op_idx >= len(operators):
                    break
                op = operators[op_idx]
                for m_idx, metric in enumerate(metrics):
                    if m_idx >= len(tokens):
                        break
                    cents = _to_cents(tokens[m_idx])
                    if cents is None:
                        continue
                    key = (op, period, metric)
                    if key in seen:
                        continue
                    seen.add(key)
                    facts.append(MetricFact(
                        state="RI", operator=op,
                        vertical="sports-wagering",
                        period=period, metric_name=metric,
                        value_usd_cents=cents, source_url=source_url,
                    ))
    return facts


def _parse_sportsbook(pdf: pdfplumber.PDF, source_url: str) -> list[MetricFact]:
    """Parse sportsbook PDF: 4 operator blocks per row, 3 metrics each.

    Two distinct layouts exist in the RI sportsbook PDF archive:

    * FY2020+ (standard): each row contains "Mon YY $value $value $value"
      blocks side-by-side for up to 4 operators.  The 2-digit year anchors
      the period.

    * FY2019 (legacy): rows have no 2-digit year token; values are bare
      numbers ("Nov 682,714 609,717 72,997").  Only 3 operator columns
      (no "Online (Mobile)").  We detect this by checking whether any
      _MONTH_YEAR_RE match exists on data rows; if not, fall back to
      _parse_sportsbook_bare().
    """
    operators = ["Twin River", "Tiverton Casino", "Online (Mobile)", "Combined"]
    metrics = ["handle", "patron_winnings", "ggr"]  # Write, Payout, Book Revenue
    facts: list[MetricFact] = []
    seen: set[tuple[str, str, str]] = set()

    # Detect layout: collect all page text first to check for FY header and
    # whether month-year tokens appear on data rows.
    all_page_texts: list[str] = []
    for page in pdf.pages:
        all_page_texts.append(page.extract_text() or "")
    full_text = "\n".join(all_page_texts)

    # Check whether any data row contains a valid "Mon YY" pair (standard fmt).
    has_year_tokens = False
    for raw in full_text.splitlines():
        line = _clean_line(raw.strip())
        if re.match(r"^(total|fy|write|payout|book)\b", line, re.I):
            continue
        if _MONTH_YEAR_RE.search(line) and _BARE_MONTH_RE.match(line):
            has_year_tokens = True
            break

    if not has_year_tokens:
        # Legacy FY2019 bare-month format — use the FY from the document header.
        fy_end = _fy_from_text(full_text)
        if fy_end is None:
            logger.warning(
                "ri_metrics: bare-month sportsbook PDF has no FY header: %s",
                source_url,
            )
            return []
        logger.debug(
            "ri_metrics: using bare-month sportsbook parser for FY%d (%s)",
            fy_end, source_url,
        )
        # Re-open pages via the already-loaded texts (reconstruct PDF object).
        # We need the pdf object for _parse_sportsbook_bare; pass directly.
        return _parse_sportsbook_bare(pdf, source_url, fy_end)

    for page in pdf.pages:
        text = page.extract_text() or ""
        for raw in text.splitlines():
            line = _clean_line(raw.strip())
            if not _MONTH_YEAR_RE.search(line):
                continue
            # Skip subtotal/total/header rows.
            if re.match(r"^(total|fy)\b", line, re.I):
                continue
            blocks = _split_blocks(line)
            if not blocks:
                continue
            # If exactly 4 blocks, map to known operators by position.
            for op_idx, (period, tokens) in enumerate(blocks):
                if op_idx >= len(operators):
                    break
                op = operators[op_idx]
                for m_idx, metric in enumerate(metrics):
                    if m_idx >= len(tokens):
                        break
                    cents = _to_cents(tokens[m_idx])
                    if cents is None:
                        continue
                    key = (op, period, metric)
                    if key in seen:
                        continue
                    seen.add(key)
                    facts.append(MetricFact(
                        state="RI", operator=op,
                        vertical="sports-wagering",
                        period=period, metric_name=metric,
                        value_usd_cents=cents, source_url=source_url,
                    ))
    return facts


def _parse_igaming(pdf: pdfplumber.PDF, source_url: str) -> list[MetricFact]:
    """iGaming: 3 operator blocks (iSlots, iTables, Combined), 3 metrics."""
    operators = ["iSlots", "iTables", "Combined"]
    metrics = ["handle", "patron_winnings", "ggr"]  # Wagers, Prizes, NGR
    facts: list[MetricFact] = []
    seen: set[tuple[str, str, str]] = set()

    for page in pdf.pages:
        text = page.extract_text() or ""
        for raw in text.splitlines():
            line = _clean_line(raw.strip())
            if not _MONTH_YEAR_RE.search(line):
                continue
            if re.match(r"^(total|fy)\b", line, re.I):
                continue
            blocks = _split_blocks(line)
            if not blocks:
                continue
            for op_idx, (period, tokens) in enumerate(blocks):
                if op_idx >= len(operators):
                    break
                op = operators[op_idx]
                for m_idx, metric in enumerate(metrics):
                    if m_idx >= len(tokens):
                        break
                    cents = _to_cents(tokens[m_idx])
                    if cents is None:
                        continue
                    key = (op, period, metric)
                    if key in seen:
                        continue
                    seen.add(key)
                    facts.append(MetricFact(
                        state="RI", operator=op,
                        vertical="igaming",
                        period=period, metric_name=metric,
                        value_usd_cents=cents, source_url=source_url,
                    ))
    return facts


# ---------------------------------------------------------------------------
# Table games: bare-month rows, 3 sections (Twin River, Tiverton, Combined),
# 4 columns per section (Total, Operator, Town, RI Lottery).
# ---------------------------------------------------------------------------

def _parse_tables(pdf: pdfplumber.PDF, source_url: str) -> list[MetricFact]:
    """Table games: section header determines the operator family."""
    section_operators = {
        "twin river": "Twin River",
        "tiverton": "Tiverton Casino",
        "combined": "Combined",
    }
    metrics = [
        "ggr",                    # Total Net Table Games Revenue
        "ggr_operator_share",     # Operator share
        "tax_paid_municipal",     # Town net
        "tax_paid",               # RI Lottery net
    ]
    facts: list[MetricFact] = []
    seen: set[tuple[str, str, str]] = set()

    for page in pdf.pages:
        text = page.extract_text() or ""
        fy_end = _fy_from_text(text)
        if fy_end is None:
            continue

        current_section: str | None = None

        for raw in text.splitlines():
            line = _clean_line(raw.strip())
            low = line.lower()

            # Section detection — header lines name an operator family.
            for marker, op_name in section_operators.items():
                if low.startswith(marker) and "$" not in line:
                    current_section = op_name
                    break

            if current_section is None:
                continue

            m = _BARE_MONTH_RE.match(line)
            if not m:
                continue
            mon = m.group(1)[:3].lower()
            if mon not in _MONTHS:
                continue
            month_int = int(_MONTHS[mon])
            # FY runs Jul (fy-1) → Jun (fy). RI table-games file lists
            # months in calendar order Jul..Jun.
            year = fy_end - 1 if month_int >= 7 else fy_end
            period = f"{year}-{_MONTHS[mon]}"

            tokens = _MONEY_TOKEN_RE.findall(line[len(m.group(1)):])
            for m_idx, metric in enumerate(metrics):
                if m_idx >= len(tokens):
                    break
                cents = _to_cents(tokens[m_idx])
                if cents is None:
                    continue
                key = (current_section, period, metric)
                if key in seen:
                    continue
                seen.add(key)
                facts.append(MetricFact(
                    state="RI", operator=current_section,
                    vertical="commercial-casino",
                    period=period, metric_name=metric,
                    value_usd_cents=cents, source_url=source_url,
                ))
    return facts


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_ri_pdf(
    path: Path,
    vertical: str,
    source_url: str,
) -> list[MetricFact]:
    """Parse a Rhode Island Lottery FY-cumulative monthly-revenue PDF.

    Args:
        path:       Local path to the downloaded PDF.
        vertical:   "sports-wagering" | "igaming" | "commercial-casino"
        source_url: Canonical rilot.com CDN URL.

    Returns:
        List of MetricFact records, one per (operator, period, metric).
        Empty list on parse failure.
    """
    try:
        pdf_path = Path(path)
        if not pdf_path.exists():
            logger.warning("ri_metrics: file not found: %s", pdf_path)
            return []

        with pdf_path.open("rb") as fh:
            magic = fh.read(4)
        if magic != b"%PDF":
            logger.warning("ri_metrics: not a PDF (magic=%r): %s", magic, pdf_path)
            return []

        with pdfplumber.open(str(pdf_path)) as pdf:
            if vertical == "sports-wagering":
                return _parse_sportsbook(pdf, source_url)
            if vertical == "igaming":
                return _parse_igaming(pdf, source_url)
            if vertical == "commercial-casino":
                return _parse_tables(pdf, source_url)
        return []
    except Exception:
        logger.exception("ri_metrics: failed to parse %s", path)
        return []
