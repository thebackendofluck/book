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
Mexico SEGOB/DGJYS metric parser.

Currency conversion: MXN → USD cents using a fixed exchange rate.

    MXN × MXN_TO_USD → USD dollars
    USD dollars × 100  → USD cents

The DGJYS publishes figures in Mexican Pesos (MXN).  A fixed rate is used
rather than a live FX lookup to keep the collector deterministic and
reproducible; the rate is close to the 2025 annual average.

    MXN_TO_USD = 0.05   (≈ 1 MXN = $0.05 USD, i.e. 20 MXN ≈ 1 USD)

This yields:
    1,000,000 MXN  →  50,000 USD cents  (= $500 USD)
    10,000,000 MXN → 500,000 USD cents  (= $5,000 USD)

Data-availability note (2026-04-23):
    SEGOB/DGJYS does not publish machine-readable revenue data publicly.
    This parser is a stub ready to be wired once data becomes available.
    When activated, implement parse_mx_pdf() following the structure of
    parsers/br.py or parsers/nl.py.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .metrics_model import MetricFact

# Fixed MXN → USD conversion rate (2025 annual average approximation).
# 1 MXN ≈ 0.05 USD
MXN_TO_USD: float = 0.05

STATE = "MX"

_STRIP_RE = re.compile(r"[\$,\s]")
_MXN_RE = re.compile(r"[Mm][Xx][Nn]|\$")


def mxn_to_usd_cents(mxn_value: Any) -> int | None:
    """Convert a MXN amount to integer USD cents.

    Handles:
      - int / float already in MXN (multiply by rate × 100)
      - string with optional $ or MXN prefix/suffix and thousands commas

    Returns None for blank / non-parseable values.

    Examples
    --------
    >>> mxn_to_usd_cents(1_000_000)
    50000
    >>> mxn_to_usd_cents("$10,500,000")
    52500
    >>> mxn_to_usd_cents("MXN 20,000,000")
    100000
    """
    if mxn_value is None or mxn_value == "":
        return None
    if isinstance(mxn_value, bool):
        return None
    if isinstance(mxn_value, (int, float)):
        val = float(mxn_value)
    else:
        s = _MXN_RE.sub("", str(mxn_value))
        s = _STRIP_RE.sub("", s)
        neg = s.startswith("(") and s.endswith(")")
        if neg:
            s = s[1:-1]
        if not s or s in ("-", "--", "N/A", "n/a"):
            return None
        try:
            val = float(s) * (-1 if neg else 1)
        except ValueError:
            return None

    usd_cents = round(val * MXN_TO_USD * 100)
    return int(usd_cents)


def parse_mx_pdf(
    path: Path,
    vertical: str,
    source_url: str,
) -> list[MetricFact]:
    """Parse a DGJYS statistics PDF and return MetricFacts in USD cents.

    This is a **stub** — SEGOB/DGJYS does not publish machine-readable
    revenue PDFs as of 2026-04-23.  Implement body when data is available.

    Expected PDF layout (based on sporadic 2019-2021 publications):
      - One table per reporting period with columns:
          Operador | Modalidad | Ingresos brutos (MXN) | Impuesto retenido
      - "Ingresos brutos" maps to metric_name="ggr"
      - "Impuesto retenido" maps to metric_name="tax_paid"
      - "Modalidad" maps to vertical (casino / pronósticos deportivos)

    When implementing, use parsers/pdf.py (pdfplumber-based) as the
    extraction layer, then call mxn_to_usd_cents() for each numeric cell.

    Parameters
    ----------
    path:
        Local path to the downloaded PDF file.
    vertical:
        Gambling vertical ("commercial-casino" or "sports-wagering").
    source_url:
        Canonical URL stored in every MetricFact row.

    Returns
    -------
    list[MetricFact]
        Empty until implementation — safe to call in backfill.py today.
    """
    _ = path, vertical, source_url  # suppress unused-variable warnings
    return []
