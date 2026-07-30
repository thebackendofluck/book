# Companion code for "The Backend of Luck" - Chapter 25b, Regulatory Reporting and Evidence Export.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Tennessee Sports Wagering Council monthly report parser (PDF + CSV).

Each monthly report is a single-page state-aggregated summary with no
per-operator breakdown, published in both PDF and CSV formats.

Known layout (consistent across the 2020–2026 archive; minor label-wording
variation across years is handled by the regex patterns below):

  Row label (case-insensitive)              Canonical metric
  ────────────────────────────────────────  ────────────────
  Gross Wagers / Gross Handle               handle
  Total Wagers / Total Amount Wagered       handle
  Gross Payouts / Winnings Paid             patron_winnings
  Adjusted Gross Income / Adjusted Gross    ggr   ← PRIMARY (pre-2023-07)
    Revenue / AGI / AGR
  Privilege Tax / Privilege Tax Assessed /  tax_paid
    Privilege Tax Collected / Tax Collected

Format history:
  2020–mid-2022  TEL era: "Gross Wagers", "Gross Payouts", "Adjusted Gross
                 Income", "Privilege Tax" (whole-dollar or "X.X Million")
  mid-2022–2023  SWAC era: same labels, exact whole-dollar values
  2023-07+       SWC era: "Gross Wagers", "Adjustments", "Gross Handle",
                 "Privilege Tax Assessed" — no Adjusted Gross Income row.
                 GGR is IMPUTED as round(handle * 0.075) in this era.
  2023-11+       Reports published in both PDF and CSV formats.  The CSV
                 layout is identical to the PDF text layout; the same
                 row-parsing logic applies to both.
  2024+          Verbose filename: "Monthly Report for SWC - {Month} {YYYY}
                 (PDF/CSV).{ext}".  Some 2024 months omit the year from the
                 filename ("Monthly Report for SWC - January (PDF).pdf");
                 the year is recovered from the URL path component.
  2025+          Same SWC layout but pdfplumber extracts dollar amounts with
                 a spurious space after the first digit, e.g. "$ 4 37,543,866"
                 instead of "$ 437,543,866". _normalize_amount() strips these.
                 Some months arrive gzip-encoded from the Wayback id_ endpoint;
                 _open_pdf() decompresses them transparently.

The Adjusted Gross Income/Revenue row is the state-defined tax base
(handle minus payouts minus adjustments/credits) — equivalent to GGR in
every other jurisdiction, and the correct value to map to metric_name='ggr'.

All values are published in whole USD (no cents in source); we multiply
by 100 to produce USD cents per the MetricFact convention.

Period (YYYY-MM) is derived from the URL filename — the report title also
contains the date but the URL is always present and easier to parse.

Returns [] on any parse failure — never raises.
"""
from __future__ import annotations

import gzip
import io
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pdfplumber

from .metrics_model import MetricFact

# ---------------------------------------------------------------------------
# Month-name → zero-padded month number
# ---------------------------------------------------------------------------

_MONTHS: dict[str, str] = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
}

# ---------------------------------------------------------------------------
# Row-label → canonical metric_name
#
# Patterns are tested in order; the first match wins.
# ---------------------------------------------------------------------------

_ROW_MAP: list[tuple[re.Pattern[str], str]] = [
    # ── GGR ─────────────────────────────────────────────────────────────────
    # "Adjusted Gross Income" (TEL/SWAC era, 2020-2023)
    (re.compile(r"adjusted\s+gross\s+income\b", re.I), "ggr"),
    # "Adjusted Gross Revenue / Receipts / Wagering" (alternative phrasing)
    (re.compile(r"adjusted\s+gross\s+(revenue|receipts|wagering)\b", re.I), "ggr"),
    # Bare abbreviations AGI / AGR
    (re.compile(r"\b(AGI|AGR)\b"), "ggr"),

    # ── Handle ───────────────────────────────────────────────────────────────
    # "Gross Handle" (SWC era, 2022+) — must come before "Gross Wagers" so
    # that "Gross Handle" lines are not accidentally matched by a wager pattern.
    (re.compile(r"\bgross\s+handle\b", re.I), "handle"),
    # "Gross Wagers" (both TEL and SWC eras)
    (re.compile(r"\bgross\s+wagers?\b", re.I), "handle"),
    # Legacy TEL phrasing
    (re.compile(r"total\s+(amount\s+)?wager(s|ed)\b", re.I), "handle"),
    (re.compile(r"\btotal\s+handle\b", re.I), "handle"),

    # ── Patron winnings / payouts ─────────────────────────────────────────────
    (re.compile(r"\bgross\s+payout(s)?\b", re.I), "patron_winnings"),
    (re.compile(
        r"(total\s+)?(winnings?\s+paid|amount\s+paid|winnings?\s+to\s+patrons?)",
        re.I,
    ), "patron_winnings"),

    # ── Privilege tax ─────────────────────────────────────────────────────────
    # "Privilege Tax Assessed" (SWC era, 2023+) and "Privilege Tax Collected"
    (re.compile(r"\bprivilege\s+tax\b", re.I), "tax_paid"),
    # Generic fallback: "Tax Collected / Paid / Remitted / Due"
    (re.compile(
        r"(privilege\s+)?tax\s+(collected|paid|remitted|due|assessed)\b",
        re.I,
    ), "tax_paid"),
]

# Matches a dollar amount with optional leading "$" and commas, and an
# optional trailing "Million" / "M" multiplier (2021 TEL era used rounded
# millions, e.g. "$257.3 Million").  Group 1 = digits, group 2 = multiplier.
#
# Note: 2025+ TN PDFs exhibit a pdfplumber extraction artefact where a single
# space is inserted between the first digit and the rest of the number, e.g.
# "$ 4 37,543,866" instead of "$ 437,543,866". _normalize_amount() removes
# these spurious intra-digit spaces before matching.
_NUM_RE = re.compile(
    r"\(?\$?\s*([\d,]+(?:\.\d+)?)\)?\s*(Million|M\b)?",
    re.I,
)

# Pattern that detects a spurious single space inside a number that follows a
# "$" sign.  Two forms seen in 2025 TN PDFs:
#   Form A — space between leading digit and next digit: "$ 4 37,543,866"
#   Form B — space between leading digit and comma:      "$ 7 ,631,217"
# Both can appear in the same report (e.g. Feb 2025 has Form B).
# We match: dollar-sign + optional whitespace + digit + whitespace + (digit | comma)
# and collapse the middle whitespace.
_DOLLAR_SPLIT_RE = re.compile(
    r"(\$\s{0,4}\d)\s+([,\d])"
)


def _normalize_amount(line: str) -> str:
    """Remove spurious intra-number spaces from a dollar-amount string.

    pdfplumber sometimes renders "$ 4 37,543,866" or "$ 7 ,631,217" from
    2025 TN PDFs. We collapse the whitespace between the leading digit and the
    following digit-or-comma when it appears after a "$" sign.
    We iterate to handle multiple splits on the same line.
    """
    prev = None
    result = line
    while result != prev:
        prev = result
        result = _DOLLAR_SPLIT_RE.sub(lambda m: m.group(1) + m.group(2), result)
    return result

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _period_from_url(url: str) -> str | None:
    """Extract YYYY-MM from the DAM filename in the source URL.

    Handles five naming conventions used across the TN SWAC archive:

    1. 2024+ verbose with year in filename (PDF or CSV):
       Monthly%20Report%20for%20SWC%20-%20August%202024%20(PDF).pdf
       Monthly%20Report%20for%20SWC%20-%20August%202024%20(CSV).csv

    2. Verbose without year in filename (some 2024 months, PDF or CSV):
       Monthly%20Report%20for%20SWC%20-%20January%20(PDF).pdf
       Year is recovered from the /{YYYY}/ path component.

    3. 2020-2023 short (month name only, PDF):
       April.pdf  /  January.pdf
       Period is inferred from the year directory component in the URL path.

    4. 2023+ short CSV (month name only):
       November.csv
       Period is inferred from the year directory component.

    5. Legacy (never actually published, kept for resilience):
       TN-Sports-Wagering-Monthly-Report-{Month}-{YYYY}.pdf
    """
    _MONTH_NAMES = (
        r"(January|February|March|April|May|June|July|August|"
        r"September|October|November|December)"
    )

    # Pattern 1 — verbose with year (PDF or CSV, URL-encoded or decoded).
    m = re.search(
        r"Monthly[%20 ]+Report[%20 ]+for[%20 ]+SWC[%20 ]+-[%20 ]+"
        + _MONTH_NAMES + r"[%20 ]+(\d{4})",
        url,
        re.I,
    )
    if m:
        mm = _MONTHS.get(m.group(1).lower())
        if mm:
            return f"{m.group(2)}-{mm}"

    # Pattern 2 — verbose without year in filename: year from path component.
    m2 = re.search(
        r"/(\d{4})/"
        r"Monthly[%20 ]+Report[%20 ]+for[%20 ]+SWC[%20 ]+-[%20 ]+"
        + _MONTH_NAMES + r"[%20 ]+\(",
        url,
        re.I,
    )
    if m2:
        mm = _MONTHS.get(m2.group(2).lower())
        if mm:
            return f"{m2.group(1)}-{mm}"

    # Pattern 3 — short PDF convention: /{YYYY}/{Month}.pdf
    m3 = re.search(
        r"/(\d{4})/" + _MONTH_NAMES + r"\.pdf",
        url,
        re.I,
    )
    if m3:
        mm = _MONTHS.get(m3.group(2).lower())
        if mm:
            return f"{m3.group(1)}-{mm}"

    # Pattern 4 — short CSV convention: /{YYYY}/{Month}.csv
    m4 = re.search(
        r"/(\d{4})/" + _MONTH_NAMES + r"\.csv",
        url,
        re.I,
    )
    if m4:
        mm = _MONTHS.get(m4.group(2).lower())
        if mm:
            return f"{m4.group(1)}-{mm}"

    # Pattern 5 — legacy hyphenated convention.
    m5 = re.search(
        r"TN-Sports-Wagering-Monthly-Report-"
        + _MONTH_NAMES + r"-(\d{4})\.pdf",
        url,
        re.I,
    )
    if m5:
        mm = _MONTHS.get(m5.group(1).lower())
        if mm:
            return f"{m5.group(2)}-{mm}"

    return None


def _period_from_path(path: Path) -> str | None:
    """Fall back to extracting period from the local cache filename.

    The local cache filename is derived by the base collector's _safe_name()
    which slugifies 'TN Statewide' → 'tn-statewide-monthly.pdf', losing the
    period.  We therefore reconstruct the full URL-like string from the path
    so the verbose/short patterns in _period_from_url can still match when
    the parent directory name encodes the year (e.g. tn/2024/tn-statewide-…).
    As a last resort, try matching the raw path stem.
    """
    # Try treating the full path as a pseudo-URL (captures year from parent dir).
    return _period_from_url(str(path)) or _period_from_url(path.name)


def _to_cents(raw: str) -> int | None:
    """Convert a dollar string (possibly comma-formatted or "X.X Million") to USD cents.

    Handles the 2021 TEL era "X.X Million" abbreviated format as well as the
    standard whole-dollar format used in 2022+ reports.
    """
    if not raw:
        return None
    raw = raw.strip()
    neg = (raw.startswith("(") and raw.endswith(")")) or raw.startswith("-")
    m = _NUM_RE.search(raw)
    if not m:
        return None
    s = m.group(1).replace(",", "")
    multiplier_str = m.group(2) or ""
    try:
        value = float(Decimal(s))
        if multiplier_str.lower() in ("million", "m"):
            value *= 1_000_000
        cents = int(round(value * 100))
    except (InvalidOperation, ValueError):
        return None
    return -cents if neg else cents


def _first_number(line: str) -> int | None:
    """Return the first dollar-amount token on the line as cents.

    Includes the optional "Million" suffix in the match so that _to_cents
    receives the full token (e.g. "$257.3 Million") and can apply the
    correct multiplier.

    Normalizes the line first to strip spurious intra-digit spaces that
    pdfplumber injects when processing newer TN PDF layouts.
    """
    line = _normalize_amount(line)
    m = _NUM_RE.search(line)
    if not m:
        return None
    # Pass the full match (including optional "Million") to _to_cents.
    return _to_cents(m.group(0))


def _open_pdf(path: Path) -> pdfplumber.PDF:  # type: ignore[return]
    """Open a PDF file, transparently decompressing gzip encoding if present.

    The Wayback Machine id_ endpoint sometimes returns the original file with
    Content-Encoding: gzip — urllib3/httpx decode this automatically, but the
    streaming downloader in backfill.py writes raw bytes. When the file is
    gzip-encoded (magic \\x1f\\x8b), decompress into an in-memory buffer before
    handing to pdfplumber.
    """
    with open(path, "rb") as fh:
        header = fh.read(2)
    if header == b"\x1f\x8b":
        with open(path, "rb") as fh:
            raw = gzip.decompress(fh.read())
        return pdfplumber.open(io.BytesIO(raw))
    return pdfplumber.open(str(path))


def _metric_for_label(label: str) -> str | None:
    """Map a row-label string to a canonical metric_name, or None."""
    for pattern, name in _ROW_MAP:
        if pattern.search(label):
            return name
    return None


def _facts_from_lines(
    lines: list[str], period: str, source_url: str
) -> list[MetricFact]:
    """Convert a list of text lines into MetricFacts.

    Shared by both the PDF and CSV parsers — the row-label/value logic is
    identical for both formats since the CSV text layout mirrors the PDF.

    Returns [] when no handle or GGR row is found (indicates a malformed or
    empty report).
    """
    facts_by_metric: dict[str, int] = {}
    pending_metric: str | None = None

    for line in lines:
        stripped = line.strip()
        if not stripped:
            pending_metric = None
            continue

        metric = _metric_for_label(stripped)
        value = _first_number(stripped)

        if metric and value is not None:
            facts_by_metric[metric] = value
            pending_metric = None
        elif metric:
            pending_metric = metric
        elif pending_metric and value is not None:
            facts_by_metric[pending_metric] = value
            pending_metric = None
        else:
            pending_metric = None

    if "handle" not in facts_by_metric and "ggr" not in facts_by_metric:
        return []

    # Post-2023-07 TN reports dropped the Adjusted Gross Income row.  Impute
    # GGR as handle × 7.5 % — approximate hold rate used by TN operators.
    if "ggr" not in facts_by_metric and "handle" in facts_by_metric:
        facts_by_metric["ggr"] = round(facts_by_metric["handle"] * 0.075)

    facts: list[MetricFact] = []
    for metric_name, value_cents in facts_by_metric.items():
        facts.append(MetricFact(
            state="TN",
            operator="TN Statewide",
            vertical="sports-wagering",
            period=period,
            metric_name=metric_name,
            value_usd_cents=value_cents,
            source_url=source_url,
        ))

    facts.sort(key=lambda f: f.metric_name)
    return facts


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def parse_tn_csv(path: Path, source_url: str) -> list[MetricFact]:
    """Parse one TN SWAC monthly sports wagering CSV into MetricFacts.

    The CSV layout mirrors the PDF text layout: each row is a label + value
    pair (or bullet + value).  The same row-label patterns apply.

    Returns [] when the CSV is unreadable or the period cannot be determined.
    """
    period = _period_from_url(source_url) or _period_from_path(path)
    if period is None:
        return []

    try:
        with open(path, newline="", encoding="utf-8", errors="replace") as fh:
            raw_lines = fh.readlines()
    except OSError:
        return []

    # Flatten each CSV row to a single string — values may be quoted and
    # contain embedded commas, so we join all non-empty cells with a space.
    import csv as _csv
    import io as _io

    lines: list[str] = []
    try:
        reader = _csv.reader(_io.StringIO("".join(raw_lines)))
        for row in reader:
            merged = " ".join(cell.strip() for cell in row if cell.strip())
            lines.append(merged)
    except Exception:  # noqa: BLE001
        # Fall back to raw line iteration on malformed CSV.
        lines = [ln.rstrip("\n") for ln in raw_lines]

    return _facts_from_lines(lines, period, source_url)


def parse_tn_pdf(path: Path, source_url: str) -> list[MetricFact]:
    """Parse one TN SWAC monthly sports wagering report into MetricFacts.

    Dispatches to ``parse_tn_csv`` for ``.csv`` files so the backfill
    pipeline can use a single entry point regardless of report format.

    For PDFs: tries the URL filename first, then the local path, for period
    extraction.  Returns [] when the PDF is unreadable, has no text layer,
    or the period cannot be determined.
    """
    # Dispatch CSV files to the dedicated CSV parser.
    suffix = path.suffix.lower()
    if suffix == ".csv" or (suffix == "" and source_url.lower().endswith(".csv")):
        return parse_tn_csv(path, source_url)

    period = _period_from_url(source_url) or _period_from_path(path)
    if period is None:
        return []

    # Quick magic-byte guard: skip non-PDF / non-gzip files in the cache.
    try:
        with open(path, "rb") as fh:
            magic = fh.read(4)
        if not (magic[:4] == b"%PDF" or magic[:2] == b"\x1f\x8b"):
            return []
    except OSError:
        return []

    try:
        with _open_pdf(path) as pdf:
            full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception:  # noqa: BLE001 — be forgiving on malformed PDFs
        return []

    if not full_text.strip():
        return []

    # Parse line-by-line: treat each line as "label … value".
    # The TN layout is either:
    #   (a) single line: "Adjusted Gross Revenue   $12,345,678"
    #   (b) two-line split: label on one line, value on the next.
    return _facts_from_lines(full_text.splitlines(), period, source_url)
