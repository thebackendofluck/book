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
Lightweight PDF summarizer using pdfplumber.

Strategy: open the first page, find the first table (pdfplumber's
default `find_table()` returns the largest one), and return the first
N rows as label/value pairs. Tuned for the typical NY revenue PDF
which has Date | GGR | Hold | Wagers columns on page 1.

Reference: https://github.com/jsvine/pdfplumber#extracting-tables
"""
from __future__ import annotations

from pathlib import Path

import pdfplumber

from models import SummaryRow


def summarise_pdf(path: Path, max_rows: int = 5) -> list[SummaryRow]:
    rows: list[SummaryRow] = []
    with pdfplumber.open(path) as pdf:
        if not pdf.pages:
            return rows
        page = pdf.pages[0]
        table = page.extract_table()
        if not table:
            return rows
        # Skip the header if it has no numeric content; otherwise treat row 0 as headers.
        data = table[1:] if len(table) > 1 else table
        for row in data:
            cells = [str(c).strip() if c else "" for c in row]
            if not cells or all(not c for c in cells):
                continue
            label = cells[0]
            value = next((c for c in reversed(cells) if c), "")
            if not label or not value or label == value:
                continue
            rows.append(SummaryRow(label=label, value=value))
            if len(rows) >= max_rows:
                break
    return rows
