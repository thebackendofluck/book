# Companion code for "The Backend of Luck" - Chapter 40, Case Study.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Variance Check
# Source: Production casino platform (sanitized)
# Chapter 40 - Case Study
#
# Post-generation variance check: inspects reconciliation and summary cells
# in the generated Excel workbook for non-zero values that indicate
# discrepancies between supplier-reported and platform-calculated figures.
#
# Any non-zero variance triggers an OpsGenie alert. This catches issues like:
#   - Supplier SFTP file missing or incomplete
#   - Timezone mismatch causing transactions to land in wrong reporting day
#   - Currency conversion errors
#   - Stuck or double-counted bets
# =============================================================================

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Union

from models import FailedStep

logger = logging.getLogger(__name__)


@dataclass
class Variance:
    cell: str    # e.g., "C16"
    date: str
    value: float


# Reconciliation sheet cells that must be zero for a clean report
RECONCILIATION_CELLS = [
    "C16", "F16", "C20", "F20", "C25", "F25",
    "C34", "F34", "C44", "C53", "F53", "K25", "M25",
]

# Operational Summary cells that must reconcile
SUMMARY_CELLS = ["B29", "C29", "B53", "C53", "L17", "L36"]


def _col_index(col: str) -> int:
    """Convert column letter(s) to 0-based index (A=0, B=1, ..., Z=25, AA=26, ...)."""
    result = 0
    for ch in col.upper():
        result = result * 26 + (ord(ch) - ord("A") + 1)
    return result - 1


def _parse_cell_ref(cell_ref: str):
    """Parse a cell reference like 'C16' into (row_idx, col_idx) (0-based)."""
    import re
    m = re.match(r"([A-Za-z]+)(\d+)", cell_ref)
    if not m:
        raise ValueError(f"Invalid cell reference: {cell_ref}")
    col_str, row_str = m.group(1), m.group(2)
    return int(row_str) - 1, _col_index(col_str)


def check_variance(
    sheet,  # openpyxl Worksheet
    cells: list[str],
    date: str,
) -> list[Union[Variance, str]]:
    """Check each cell: zero -> 'OK', non-zero -> Variance object."""
    results: list[Union[Variance, str]] = []
    for cell_ref in cells:
        try:
            row_idx, col_idx = _parse_cell_ref(cell_ref)
            cell = sheet.cell(row=row_idx + 1, column=col_idx + 1)
            value = float(cell.value or 0)
            if value == 0.0:
                results.append("OK")
            else:
                results.append(Variance(cell=cell_ref, date=date, value=value))
        except Exception as exc:
            logger.warning("Could not read cell %s: %s", cell_ref, exc)
            results.append(Variance(cell=cell_ref, date=date, value=-1.0))
    return results


def check_variances(
    workbook,  # openpyxl Workbook
    date: str,
) -> Union[FailedStep, list[Union[Variance, str]]]:
    """
    Inspect both Reconciliation and Operational Summary sheets.
    Returns a FailedStep if an exception occurs, or a list of results.
    """
    try:
        recon_sheet   = workbook["Reconciliation"]
        summary_sheet = workbook["Operational Summary"]
        results = (
            check_variance(recon_sheet,   RECONCILIATION_CELLS, date)
            + check_variance(summary_sheet, SUMMARY_CELLS, date)
        )
        return results
    except Exception as exc:
        return FailedStep("Variance check", str(exc), exc)
