# Companion code for "The Backend of Luck" - Chapter 25b, Regulatory Reporting and Evidence Export.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""West Virginia Lottery monthly PDF parser.

Each monthly PDF (``L04_FY_YYYY_NNNNN.pdf``) is the combined "Monthly Report
on Lottery Operations" filed with the WV Legislature.  The document contains
a full income statement plus two notes that state the *adjusted gross receipts*
for each interactive vertical:

  NOTE 10 – SPORTS WAGERING
    "The Sports Wagering adjusted gross wagering receipts for the month and
     year-to-date periods ended <Month> <DD>, <YYYY>[,] were $<amount> and ..."

  NOTE 11 – INTERACTIVE WAGERING
    "The Interactive Wagering adjusted gross interactive gaming receipts for
     the month and year-to-date periods ended
     <Month> <DD>, <YYYY>[,] were $<amount> and ..."

These *adjusted gross receipts* ARE the GGR figures (the privilege-tax base),
not the tax amounts themselves:
  - Sports Wagering privilege tax = 10 % of adjusted gross wagering receipts
  - Interactive Wagering privilege tax = 15 % of adjusted gross interactive
    gaming receipts

Roughly half the corpus consists of pure image scans with no embedded text
layer.  For those PDFs, pdfplumber extract_text() returns empty strings and a
second pass using tesseract OCR (via subprocess) is attempted.  The OCR pass
renders each page as an RGB image via pdfplumber + PIL and feeds it to the
``tesseract`` binary (expected on PATH).  If tesseract is not available the
image-only PDFs simply return [].

WV Lottery does not break out per-operator figures in these public reports;
all amounts are statewide totals, so operator is set to "WV Lottery".
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pdfplumber

from .metrics_model import MetricFact

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Month name → zero-padded month number
# ---------------------------------------------------------------------------

_MONTHS: dict[str, str] = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
}

# ---------------------------------------------------------------------------
# Regexes that match the sentence in NOTE 10 / NOTE 11
#
# The sentence can span a line break between "periods ended" and the date,
# so we use re.DOTALL and allow \s+ across newlines.
#
# Comma before "were" is present in some months, absent in others.
# ---------------------------------------------------------------------------

_SW_RE = re.compile(
    r"sports\s+wagering\s+adjusted\s+gross\s+wagering\s+receipts\s+"
    r"for\s+the\s+month\s+and\s+year[- ]to[- ]date\s+periods\s+ended\s+"
    r"(\w+)\s+\d{1,2},?\s*(\d{4}),?\s+were\s+\$([0-9,]+)",
    re.I | re.DOTALL,
)

_IW_RE = re.compile(
    r"interactive\s+wagering\s+adjusted\s+gross\s+interactive\s+gaming\s+receipts\s+"
    r"for\s+the\s+month\s+and\s+year[- ]to[- ]date\s+"
    r"(?:periods\s+ended\s+)?"          # "periods ended" may be on the preceding line
    r"(\w+)\s+\d{1,2},?\s*(\d{4}),?\s+were\s+\$([0-9,]+)",
    re.I | re.DOTALL,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_cents(dollar_str: str) -> int:
    """Convert a comma-formatted dollar string (no '$') to integer USD cents."""
    return int(round(float(dollar_str.replace(",", "")) * 100))


def _period(month_name: str, year: str) -> str | None:
    """Return 'YYYY-MM' or None if month_name is not recognised."""
    mm = _MONTHS.get(month_name.lower())
    if mm is None:
        return None
    return f"{year}-{mm}"


# ---------------------------------------------------------------------------
# OCR fallback helpers
# ---------------------------------------------------------------------------

# Notes (NOTE 10/11) typically start within the last 10 pages of the ~25-page
# monthly report.  Limiting OCR to those pages keeps processing time short
# and avoids wasting tesseract cycles on the financial-statement pages that
# precede the notes.
_OCR_TAIL_PAGES = 10

# Common tesseract misreads for the key words in NOTE 10/11.
# Applied as a post-OCR normalization step before the regexes run.
#
# Observed variants in the WV Lottery corpus:
#   reccipts   (cc instead of c)
#   reciepts   (ie transposed)
#   receipis   (t→i near end)
#   intcractive (er → cr)
#   interactivc (final e → c)
#   mteractive  (initial i → m)
#   wagcring    (e → c)
_OCR_CORRECTIONS: list[tuple[re.Pattern[str], str]] = [
    # "receipts" variants: reccipts, reciepts, receipis, receips, reccipt, etc.
    # Pattern: rec + optional(e|c) + i + optional(e|p) + optional(p) + 1-2 of (t|s|i)
    (re.compile(r"\brec[ec]?i[ep]?p?[tsi]{1,2}\b", re.I), "receipts"),
    # "interactive" variants: intcractive, interactivc, mteractive
    (re.compile(r"\b(?:intcr|mter)activ[ec]?\b", re.I), "interactive"),
    (re.compile(r"\binteractivc\b", re.I), "interactive"),
    # "wagering" variants: wagcring
    (re.compile(r"\bwag[ec]ring\b", re.I), "wagering"),
    # "year" variants: ycar (e→c misread, same class as receipts/interactive
    # above). Left unnormalized this breaks the "year-to-date" match in
    # _SW_RE / _IW_RE and silently drops the fact (seen in FY2026 April PDF).
    (re.compile(r"\bycar\b", re.I), "year"),
]


def _normalize_ocr(text: str) -> str:
    """Apply common tesseract correction substitutions to OCR output."""
    for pattern, replacement in _OCR_CORRECTIONS:
        text = pattern.sub(replacement, text)
    return text


def _ocr_pdf_tail(path: Path) -> str:
    """Render the last _OCR_TAIL_PAGES pages as images and run tesseract OCR.

    Returns concatenated OCR text, or an empty string if tesseract is not
    available or any rendering/OCR step fails.
    """
    tesseract_bin = shutil.which("tesseract")
    if tesseract_bin is None:
        log.debug("wv: tesseract not found on PATH — cannot OCR image-only PDF")
        return ""

    try:
        from PIL import Image as _PilImage  # noqa: F401 – presence check
    except ImportError:
        log.debug("wv: Pillow not available — cannot OCR image-only PDF")
        return ""

    texts: list[str] = []
    try:
        with pdfplumber.open(str(path)) as pdf:
            tail = pdf.pages[-_OCR_TAIL_PAGES:]
            for page in tail:
                try:
                    rendered = page.to_image(resolution=250)
                    pil_img = rendered.original.convert("RGB")
                except Exception as exc:  # noqa: BLE001
                    log.debug("wv: page render failed: %s", exc)
                    continue

                with tempfile.NamedTemporaryFile(
                    suffix=".png", delete=False, dir=tempfile.gettempdir()
                ) as fh:
                    img_path = fh.name

                try:
                    pil_img.save(img_path, "PNG")
                    result = subprocess.run(
                        [tesseract_bin, img_path, "stdout", "--psm", "6"],
                        capture_output=True,
                        timeout=60,
                    )
                    texts.append(result.stdout.decode("utf-8", errors="replace"))
                except Exception as exc:  # noqa: BLE001
                    log.debug("wv: tesseract failed on page: %s", exc)
                finally:
                    Path(img_path).unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001 – malformed PDF
        log.debug("wv: OCR pass failed: %s", exc)

    return "\n".join(texts)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_wv_pdf(path: Path, vertical: str, source_url: str) -> list[MetricFact]:
    """Parse one WV Lottery monthly PDF and return GGR MetricFacts.

    ``vertical`` is passed by ``_backfill_via_collector`` (one call per
    vertical per PDF), but since this parser extracts BOTH verticals from the
    single combined document, we always emit both and let the upsert layer
    handle duplicates harmlessly.

    For PDFs that have an embedded text layer (pdfplumber finds text), the
    fast text-extraction path is used.  For image-only scans (roughly half
    the WV corpus), a tesseract OCR fallback is attempted on the tail pages
    where NOTE 10/11 appear.  Returns [] only when the PDF is truly
    unreadable (missing text layer AND no tesseract, or tesseract OCR also
    finds no matching sentences).
    """
    # Quick guard: skip non-PDF files that may end up in cache.
    try:
        with open(path, "rb") as fh:
            if fh.read(4) != b"%PDF":
                return []
    except OSError:
        return []

    facts: list[MetricFact] = []

    try:
        with pdfplumber.open(str(path)) as pdf:
            # Concatenate all pages into one string so multi-line sentences
            # spanning a page boundary are still matched.
            full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception:  # noqa: BLE001 – malformed PDF
        return []

    if not full_text.strip():
        # Image-only PDF — attempt OCR on the tail pages (where NOTE 10/11 live).
        log.debug("wv: no text layer in %s — trying OCR fallback", path.name)
        ocr_text = _ocr_pdf_tail(path)
        if not ocr_text.strip():
            return []
        # Normalize common tesseract misreads before regex matching.
        full_text = _normalize_ocr(ocr_text)

    # ------------------------------------------------------------------
    # Sports Wagering GGR
    # ------------------------------------------------------------------
    m = _SW_RE.search(full_text)
    if m:
        period = _period(m.group(1), m.group(2))
        if period:
            facts.append(MetricFact(
                state="WV",
                operator="WV Lottery",
                vertical="sports-wagering",
                period=period,
                metric_name="ggr",
                value_usd_cents=_to_cents(m.group(3)),
                source_url=source_url,
            ))

    # ------------------------------------------------------------------
    # Interactive (iGaming) GGR
    # ------------------------------------------------------------------
    m = _IW_RE.search(full_text)
    if m:
        period = _period(m.group(1), m.group(2))
        if period:
            facts.append(MetricFact(
                state="WV",
                operator="WV Lottery",
                vertical="igaming",
                period=period,
                metric_name="ggr",
                value_usd_cents=_to_cents(m.group(3)),
                source_url=source_url,
            ))

    return facts
