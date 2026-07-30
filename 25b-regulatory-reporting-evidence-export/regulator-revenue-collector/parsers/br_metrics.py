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
Brazil SPA-MF / Receita Federal metric parsers.

Two parsers:

parse_br_panorama_pdf(path, vertical, source_url)
    Extracts semi-annual GGR (Receita Bruta de Jogo / RBJ) from the SPA
    Panorama Semestral PDF.  The report contains:
      - Total GGR for the period (BRL, full precision from page 10)
      - Tax distributed (12% "Destinações Legais", page 11)
      - Period dates extracted from page 1 title text

    The PDF uses embedded font glyphs for the R$ currency symbol which
    pdfplumber renders as "(cid:1533)"; these are normalised.

    Since the report has no per-operator breakdown (it's market-wide), the
    operator is "BR Statewide".

parse_br_rfb_xlsx(path, source_url)
    Extracts the "ATIV. DE EXPLORAÇÃO DE JOGOS DE AZAR E APOSTAS" CNAE row
    from Receita Federal Análise Mensal XLSX annexes.  Each XLSX covers one
    month and contains three comparison periods:
      - Current month vs same month previous year
      - Cumulative Jan–current month vs same YTD previous year
    We emit the current-month figure and the YTD figure separately.

    The XLSX sheets are:
      "Tabela I"  — current month vs prior year, in R$ milhões (correntes)
      "Tabela II" — YTD current year vs YTD prior year, in R$ milhões (correntes)

Currency conversion: BRL → USD at 0.20 USD/BRL (fixed proxy rate).
GGR is in BRL; tax_paid derived as 12% of GGR (Lei 14.790/2023 rate).

MetricFact fields:
    state          = "BR"
    operator       = "BR Statewide"
    vertical       = "sports-wagering" | "igaming" (panorama emits both)
    period         = "YYYY-MM"  (last month of the reporting window)
    metric_name    = "ggr" | "tax_paid" | "ggr_ytd" | "tax_ytd"
    value_usd_cents = int (BRL × 0.20 × 100, rounded)
    source_url     = direct download URL
"""
from __future__ import annotations

import re
from pathlib import Path

import pdfplumber
import openpyxl

from .metrics_model import MetricFact

# ---------------------------------------------------------------------------
# Currency conversion
# ---------------------------------------------------------------------------
_BRL_USD = 0.20          # fixed proxy: 1 BRL = 0.20 USD
_MILLION = 1_000_000.0   # RFB figures are in R$ milhões

# ---------------------------------------------------------------------------
# Panorama vertical split
# ---------------------------------------------------------------------------
# The SPA Panorama PDF reports a SINGLE combined "quota-fixa" GGR figure that
# bundles sports betting + online casino. The collector lists that one PDF under
# both verticals (sports-wagering, igaming); without a split, the parser emitted
# the full combined total for EACH vertical, double-counting Brazil's GGR (~2x:
# e.g. full-year 2025 R$36.9B became ~$14.8B instead of ~$7.4B). We split the
# combined total by the H2 Gambling Capital 2025 mix so the per-vertical sum
# equals the reported total and the breakdown stays realistic.
_PANORAMA_VERTICAL_SHARE = {"sports-wagering": 0.55, "igaming": 0.45}


def _brl_to_usd_cents(brl: float) -> int:
    """Convert BRL (full units) → USD cents."""
    return int(round(brl * _BRL_USD * 100))


def _brl_millions_to_usd_cents(brl_millions: float) -> int:
    """Convert R$ milhões → USD cents."""
    return _brl_to_usd_cents(brl_millions * _MILLION)


# ---------------------------------------------------------------------------
# Helpers: BRL number parsing
# ---------------------------------------------------------------------------
# Normalise the (cid:1533) glyph pdfplumber emits for the R$ currency symbol.
_CID_RE = re.compile(r"\(cid:\d+\)")
# Match Brazilian decimal format: "36.959.783.379,70" or "36.959.783.379"
_BRL_NUM_RE = re.compile(r"\d[\d.]*(?:,\d+)?")


def _parse_brl(text: str) -> float | None:
    """
    Parse a BRL-formatted number string → float.

    Handles:
      "R$ 36.959.783.379,70"  → 36959783379.70
      "(cid:1533) 4 bilhões"  — returns None (approximate text, not table value)
      "4.532.797.794,92"      → 4532797794.92
    """
    # Remove currency glyph / symbol variants
    s = _CID_RE.sub("", text).replace("R$", "").replace("R", "").strip()
    m = _BRL_NUM_RE.search(s)
    if not m:
        return None
    raw = m.group(0)
    # Remove thousand-separators (dots before a group of 3 digits + comma/end)
    raw = re.sub(r"\.(?=\d{3}(?:[.,]|$))", "", raw)
    raw = raw.replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def _extract_period_from_pdf_text(text: str) -> str | None:
    """
    Derive YYYY-MM from page-1 title text of the Panorama.

    Patterns seen:
      "Dados de 1º de janeiro a 30 de dezembro de 2025"  → "2025-12"
      "Dados de 1º de janeiro a 30 de junho de 2025"     → "2025-06"
      "(Jan/2026)"  in presentation header                → "2026-01" (fallback)
    """
    _MONTH_PT_LONG = {
        "janeiro": "01", "fevereiro": "02", "março": "03", "marco": "03",
        "abril": "04", "maio": "05", "junho": "06", "julho": "07",
        "agosto": "08", "setembro": "09", "outubro": "10",
        "novembro": "11", "dezembro": "12",
    }
    # "...a 30 de dezembro de 2025"
    m = re.search(
        r"a\s+\d+\s+de\s+(" + "|".join(_MONTH_PT_LONG) + r")\s+de\s+(\d{4})",
        text, re.I,
    )
    if m:
        mon = _MONTH_PT_LONG.get(m.group(1).lower())
        if mon:
            return f"{m.group(2)}-{mon}"
    # "(Jan/2026)" style from presentation header
    _MONTH_PT_SHORT = {
        "jan": "01", "fev": "02", "mar": "03", "abr": "04",
        "mai": "05", "jun": "06", "jul": "07", "ago": "08",
        "set": "09", "out": "10", "nov": "11", "dez": "12",
    }
    m2 = re.search(
        r"\((" + "|".join(_MONTH_PT_SHORT) + r")[./](\d{4})\)",
        text, re.I,
    )
    if m2:
        mon = _MONTH_PT_SHORT.get(m2.group(1).lower())
        if mon:
            return f"{m2.group(2)}-{mon}"
    return None


# ---------------------------------------------------------------------------
# Parser 1: SPA Panorama Semestral PDF
# ---------------------------------------------------------------------------

def parse_br_panorama_pdf(
    path: Path,
    vertical: str,
    source_url: str,
) -> list[MetricFact]:
    """
    Extract GGR and tax_paid from one SPA Panorama Semestral PDF.

    The PDF structure (confirmed via both published reports):
      Page 1: title with date range (used for period derivation)
      Page 10: "GGR DO PERÍODO  R$ 36.959.783.379,70"
      Page 11: "Total no período: R$ 4.532.797.794,92"  (12% destinações)

    GGR is reported in full BRL.  Tax = 12% of GGR (destinações legais).

    Args:
        path:       Local path to the downloaded PDF.
        vertical:   "sports-wagering" or "igaming".
        source_url: Original download URL (embedded in each MetricFact).

    Returns:
        List of MetricFact — typically [ggr, tax_paid] per (vertical, period).
    """
    facts: list[MetricFact] = []
    period: str | None = None
    ggr_brl: float | None = None
    tax_brl: float | None = None

    with pdfplumber.open(str(path)) as pdf:
        full_text_page1 = (pdf.pages[0].extract_text() or "") if pdf.pages else ""
        period = _extract_period_from_pdf_text(full_text_page1)
        # Scan all pages for GGR and destinações figures.
        for page in pdf.pages:
            txt = page.extract_text() or ""
            # GGR page: look for a standalone large number near "GGR"
            if ggr_brl is None and re.search(r"\bGGR\b", txt, re.I):
                # The value appears on a line by itself after "GGR DO PERÍODO"
                # e.g. "36.959.783.379,70"
                for line in txt.splitlines():
                    line_clean = _CID_RE.sub("", line).strip()
                    if re.match(r"^[\d.]+,\d{2}$", line_clean):
                        v = _parse_brl(line_clean)
                        if v and v > 1_000_000:   # sanity: must be > R$1M
                            ggr_brl = v
                            break
                    # Also catch "GGR DO PERÍODO  R$ 36.959.783.379,70" on same line
                    m = re.search(r"(?:GGR|RBJ)\s+[\w\s]*?([\d.]+,\d{2})", line, re.I)
                    if m and ggr_brl is None:
                        v = _parse_brl(m.group(1))
                        if v and v > 1_000_000:
                            ggr_brl = v
                            break
            # Destinações (tax) page: "Total no período: R$ 4.532.797.794,92"
            if tax_brl is None and re.search(r"destina[çc][oõ]es|total no per[íi]odo", txt, re.I):
                m = re.search(
                    r"total\s+n[ao]\s+(?:semestre|per[íi]odo|ano)[:\s]+"
                    r"[^\d]*([\d.]+,\d{2})",
                    txt, re.I,
                )
                if m:
                    tax_brl = _parse_brl(m.group(1))
        # Fallback period from any page if page 1 missed it
        if period is None:
            with pdfplumber.open(str(path)) as pdf2:
                for page in pdf2.pages:
                    period = _extract_period_from_pdf_text(page.extract_text() or "")
                    if period:
                        break
    if period is None or ggr_brl is None:
        return facts   # unable to parse — return empty rather than corrupt data
    # The combined quota-fixa total is split per vertical so the sports-wagering
    # + igaming sum equals the reported figure (instead of double-counting it).
    share = _PANORAMA_VERTICAL_SHARE.get(vertical, 1.0)
    # Emit GGR fact
    facts.append(MetricFact(
        state="BR",
        operator="BR Statewide",
        vertical=vertical,
        period=period,
        metric_name="ggr",
        value_usd_cents=_brl_to_usd_cents(ggr_brl * share),
        source_url=source_url,
    ))
    # Emit tax_paid — either parsed or derived as 12% of GGR (split per vertical)
    effective_tax = (tax_brl if tax_brl else ggr_brl * 0.12) * share
    facts.append(MetricFact(
        state="BR",
        operator="BR Statewide",
        vertical=vertical,
        period=period,
        metric_name="tax_paid",
        value_usd_cents=_brl_to_usd_cents(effective_tax),
        source_url=source_url,
    ))
    return facts


# ---------------------------------------------------------------------------
# Parser 2: Receita Federal monthly XLSX annex
# ---------------------------------------------------------------------------

# CNAE row label (truncated in XLSX to fit column width).
_JOGOS_RX = re.compile(
    r"ativ[.·]?\s*de\s*explora[çc][aã]o\s+de\s+jogos|jogos\s+de\s+azar\s+e\s+apostas",
    re.I,
)

# Month name → number (for the /view URL filename patterns like "nov2025").
_MONTH_PT = {
    "jan": "01", "fev": "02", "mar": "03", "abr": "04",
    "mai": "05", "jun": "06", "jul": "07", "ago": "08",
    "set": "09", "out": "10", "nov": "11", "dez": "12",
}
_PERIOD_FROM_URL_RX = re.compile(
    r"(jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)[- _]?(\d{4})",
    re.I,
)


def _period_from_url(source_url: str) -> str | None:
    """Extract YYYY-MM from RFB XLSX filename, e.g. 'analise-mensal-dez-2025-anexo'."""
    m = _PERIOD_FROM_URL_RX.search(source_url)
    if not m:
        return None
    mon = _MONTH_PT.get(m.group(1).lower())
    if mon is None:
        return None
    return f"{m.group(2)}-{mon}"


def _cell_float(cell) -> float | None:
    """Return cell value as float, or None if blank/non-numeric."""
    v = cell.value if hasattr(cell, "value") else cell
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", ".").replace(" ", "")
    try:
        return float(s)
    except ValueError:
        return None


def _find_jogos_row(ws) -> list[float | None] | None:
    """
    Find the CNAE "jogos de azar e apostas" row in a worksheet and return
    its numeric cell values.  Returns None if row not found.
    """
    for row in ws.iter_rows():
        label = str(row[0].value or "").strip()
        if _JOGOS_RX.search(label):
            return [_cell_float(c) for c in row]
    return None


def parse_br_rfb_xlsx(
    path: Path,
    source_url: str,
) -> list[MetricFact]:
    """
    Extract apostas/jogos federal tax receipts from a Receita Federal
    Análise Mensal XLSX annex (supplementary reference).

    The XLSX annex contains high-level revenue totals by tax type (IPI, COFINS,
    IRPJ, etc.) but does NOT contain a per-CNAE apostas breakout.  The apostas
    CNAE data is in the narrative PDF companion.  This function is retained for
    future use if a CNAE sheet is added to the XLSX, but currently returns [].

    Use parse_br_rfb_pdf() for the per-CNAE monthly apostas figures.
    """
    return []


# ---------------------------------------------------------------------------
# Parser 2b: Receita Federal monthly narrative PDF
# ---------------------------------------------------------------------------

# CNAE row pattern in the PDF text layer:
# ". ATIV. DE EXPLORAÇÃO DE JOGOS DE AZAR E APOSTAS  9.953  91  9.862  10.891,85"
_RFB_CNAE_ROW_RX = re.compile(
    r"ativ[.\s]+de\s+explora[çc][aã]o\s+de\s+jogos\s+de\s+azar\s+e\s+apostas"
    r"\s+([\d.,]+)\s+([\d.,]+)",
    re.I,
)

# Table section headers used to distinguish monthly vs YTD tables in the PDF.
# Pages 11-15 have annual YTD data; pages 17-21 have single-month data.
_MONTH_SECTION_RX = re.compile(
    r"per[íi]odo:\s*(?:dezembro|novembro|outubro|setembro|agosto|julho|junho|"
    r"maio|abril|mar[çc]o|fevereiro|janeiro)\s+-?\s*\d{4}/\d{4}",
    re.I,
)
_YTD_SECTION_RX = re.compile(
    r"per[íi]odo:\s*janeiro\s+a\s+\w+\s*-\s*\d{4}/\d{4}",
    re.I,
)


def _parse_brl_pt(s: str) -> float | None:
    """Parse Brazilian number string '9.953' or '9.953,14' → float (millions)."""
    s = s.strip()
    # Remove thousands-dots (dot before exactly 3 digits ending in comma/end)
    s = re.sub(r"\.(?=\d{3}(?:[,]|$))", "", s)
    s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def parse_br_rfb_pdf(
    path: Path,
    source_url: str,
) -> list[MetricFact]:
    """
    Extract monthly and YTD apostas federal tax receipts from a Receita
    Federal "Análise Mensal da Arrecadação" narrative PDF.

    PDF structure (confirmed from 2025 monthly reports):
      Section I  (pages ~8–15): annual YTD comparison (Jan–current month).
        - Contains per-CNAE tables: "ATIV. DE EXPLORAÇÃO DE JOGOS DE AZAR E APOSTAS"
        - Column 1 = YTD current year (R$ milhões); Column 2 = YTD prior year.
      Section III (pages ~17–21): single-month comparison.
        - Same per-CNAE table structure.
        - Column 1 = current month (R$ milhões); Column 2 = prior year same month.

    The apostas CNAE row appears in multiple tables per section:
      - Total RFB breakdown by CNAE (page ~11, ~17)
      - IRPJ/CSLL breakdown by CNAE (page ~13, ~18)
      - COFINS/PIS breakdown by CNAE (page ~15, ~21)

    We collect:
      metric_name="ggr_tax_month"   — R$ milhões, current month, RFB total CNAE table
      metric_name="ggr_tax_ytd"     — R$ milhões, YTD, RFB total CNAE table
    Then derive:
      metric_name="ggr"             — ggr_tax_month / 0.12 (12% Lei 14.790/2023)
      metric_name="ggr_ytd"         — ggr_tax_ytd  / 0.12

    Note: All tax receipt values are gross federal tax, not GGR directly.
    GGR = tax_receipt / 0.12 is an approximation — the actual effective rate
    for any given operator depends on whether they paid all obligations.

    Args:
        path:       Local path to the downloaded PDF.
        source_url: Original download URL.

    Returns:
        List of MetricFact — typically 4 facts (2 tax + 2 derived GGR).
    """
    period = _period_from_url(source_url)
    if period is None:
        return []

    # We need the first hit of the CNAE row in the YTD section and the first
    # hit in the monthly section.  The sections are identified by their
    # header "PERÍODO: JANEIRO A <month>" vs "PERÍODO: <month> - 2025/2024".
    # Simpler: the annual section comes first (Section I ≈ pages 8–15),
    # the monthly section comes later (Section III ≈ pages 17+).

    ytd_millions: float | None = None
    month_millions: float | None = None

    # Section III header marks the start of the single-month section.
    # "III. RECEITAS ADMINISTRADAS PELA RFB - DESEMPENHO DA ARRECADAÇÃO DE <month>"
    _SECTION_III_RX = re.compile(r"^III\.\s+RECEITAS\s+ADMINISTRADAS", re.I | re.M)

    with pdfplumber.open(str(path)) as pdf:
        in_monthly_section = False
        for page in pdf.pages:
            txt = page.extract_text() or ""
            # Detect entry into Section III (single-month comparison).
            if _SECTION_III_RX.search(txt):
                in_monthly_section = True
            m = _RFB_CNAE_ROW_RX.search(txt)
            if not m:
                continue
            val = _parse_brl_pt(m.group(1))
            if val is None:
                continue
            # Assign to YTD or monthly bucket.
            if in_monthly_section:
                if month_millions is None:
                    month_millions = val
            else:
                if ytd_millions is None:
                    ytd_millions = val

    facts: list[MetricFact] = []

    def _emit(metric: str, brl_m: float) -> None:
        facts.append(MetricFact(
            state="BR",
            operator="BR Statewide",
            vertical="sports-wagering",
            period=period,
            metric_name=metric,
            value_usd_cents=_brl_millions_to_usd_cents(brl_m),
            source_url=source_url,
        ))

    # Receita Federal publishes federal tax receipts; the SPA Panorama is the
    # canonical GGR source. Emitting BOTH (with a derived ggr = tax / 0.12)
    # double-counts on the dashboard's by-state totals because they overlap
    # with Panorama's semi-annual GGR rows. We therefore emit ONLY tax_paid
    # from the RFB feed and let Panorama own the GGR side; users can still
    # see the implied effective rate via the dashboard's tax-rate chart.
    if month_millions is not None:
        _emit("tax_paid", month_millions)

    # Emit the all-RFB YTD figure as tax_ytd when available.
    # For some months (e.g. Mar/Apr 2026) the gambling CNAE row does NOT
    # appear in Section III (single-month tables), only in the YTD tables of
    # Section I.  We emit tax_ytd so the cumulative data is not silently
    # discarded.  tax_ytd uses a distinct metric_name and does NOT compound
    # against monthly tax_paid aggregations on the dashboard.
    if ytd_millions is not None:
        _emit("tax_ytd", ytd_millions)

    return facts
