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
Domain models for the regulator-revenue-collector.

Every state plug-in normalizes its raw downloads into the same shape:

    StateSnapshot
      ├─ state         "NY" / "NJ" / "PA" / "MI"
      ├─ regulator     full name (e.g. "NY State Gaming Commission")
      ├─ collected_at  ISO-8601 UTC timestamp
      ├─ source_url    landing page URL
      └─ reports[]     ReportFile
                          ├─ operator     "Del Lago Resort and Casino"
                          ├─ vertical     "commercial-casino" / "sports-wagering" / "video-gaming" / "igaming"
                          ├─ cadence      "weekly" / "monthly" / "quarterly" / "annual"
                          ├─ format       "pdf" / "xlsx" / "csv"
                          ├─ source_url   direct download URL on the regulator's site
                          ├─ local_path   relative path under output/ where we cached the file
                          ├─ size_bytes
                          ├─ sha256       integrity hash
                          ├─ retrieved_at ISO-8601 UTC
                          └─ summary[]    optional list of {label, value} extracted via parser

The website consumes the JSON serialisation of one StateSnapshot per state.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class SummaryRow(BaseModel):
    """A single key/value extracted from a parsed report."""
    label: str
    value: str


class ReportFile(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    operator: str
    vertical: str = Field(description="commercial-casino|sports-wagering|video-gaming|igaming")
    cadence: str = Field(description="weekly|monthly|quarterly|annual")
    format: str = Field(description="pdf|xlsx|xls|csv")
    source_url: str
    local_path: Optional[str] = None
    size_bytes: int = 0
    sha256: Optional[str] = None
    retrieved_at: Optional[datetime] = None
    summary: list[SummaryRow] = Field(default_factory=list)
    fetch_error: Optional[str] = None


class StateSnapshot(BaseModel):
    state: str
    regulator: str
    source_url: str
    collected_at: datetime
    reports: list[ReportFile]
