# Companion code for "The Backend of Luck" - Chapter 25b, Regulatory Reporting and Evidence Export.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Shared MetricFact model used by every per-state metric parser.

`metric_name` and `vertical` are free-form strings rather than enums so
each per-regulator parser can emit its native taxonomy. Normalization to
a small canonical set (ggr / handle / tax_paid / patron_winnings) is the
job of the API/dashboard query layer, not the parsers.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class MetricFact(BaseModel):
    state: str
    operator: str
    vertical: str
    period: str = Field(pattern=r"^\d{4}-\d{2}$")
    metric_name: str
    value_usd_cents: int
    source_url: str = ""
