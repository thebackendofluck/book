# Companion code for "The Backend of Luck" - Chapter 36, Financial Operations.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Domain models for the batch withdrawal processor.

This is a scheduled batch job that processes self-excluded players who have
a positive balance and exactly one withdrawal option on file. Players with
multiple payment methods are deferred to manual processing -- a regulatory
requirement in many jurisdictions (funds must be returned via the original
deposit method where possible).
"""

from __future__ import annotations

from pydantic import BaseModel


class UserWithdraw(BaseModel):
    """A player eligible for automated withdrawal processing."""

    user_id: int
    name: str
    email: str
    balance: int  # minor currency units
    currency: str
    brand_id: int
    brand_name: str


class WithdrawOption(BaseModel):
    """A single withdrawal option (e.g. a specific card or bank account)."""

    name: str
    id: str


class WithdrawOptionsResponse(BaseModel):
    """Response from the platform's getwithdrawoptions endpoint."""

    min_amount: str
    options: list[WithdrawOption]


class WithdrawResponse(BaseModel):
    """Response from a successful withdrawal request."""

    txn_id: str
    needs_kyc: bool
    total: str
    request_id: str
