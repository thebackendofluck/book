# Companion code for "The Backend of Luck" - Chapter 10, Complete Platform Architecture.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
gameservice.suppliers.kambi.models — Kambi API Types
=====================================================

Kambi's API uses JSON over HTTPS with Bearer token authentication.

Key endpoints
-------------
* ``GET /offering/{offering}/betoffers`` — Retrieve available bet offers.
* ``POST /coupon``                        — Place a bet.
* ``GET /bet/{betId}``                    — Query a bet status.
* ``POST /coupon/{betId}/cashout``        — Cash out an open bet.
* ``GET /settlement``                     — Poll the settlement feed.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class KambiBetPlacementRequest(BaseModel):
    """Payload for placing a bet via Kambi's coupon endpoint."""

    externalCustomerId: str = Field(..., description="Platform player ID.")
    token: str = Field(..., description="Platform session token.")
    coupon: "KambiCoupon" = Field(..., description="The bet coupon details.")


class KambiCoupon(BaseModel):
    """Bet coupon containing one or more selections."""

    stake: str = Field(..., description="Total stake amount as decimal string.")
    currency: str = Field(..., description="ISO-4217 currency code.")
    odds: str = Field(..., description="Combined odds as decimal string.")
    selections: list["KambiSelection"] = Field(
        ..., description="Bet selections (legs)."
    )


class KambiSelection(BaseModel):
    """A single selection (leg) in a Kambi coupon."""

    id: str = Field(..., description="Bet offer outcome ID.")
    oddsDecimal: str = Field(..., description="Odds for this selection.")


class KambiBetReceiptEvent(BaseModel):
    """Inbound webhook event from Kambi on bet placement confirmation."""

    betId: str = Field(..., description="Kambi bet ID.")
    externalCustomerId: str = Field(..., description="Platform player ID.")
    stake: str = Field(..., description="Confirmed stake amount.")
    currency: str = Field(..., description="ISO-4217 currency code.")
    status: str = Field(..., description="Bet status: OPEN, SETTLED, CANCELLED.")


class KambiSettlementEvent(BaseModel):
    """A settled bet from the Kambi settlement feed."""

    betId: str = Field(..., description="Kambi bet ID.")
    externalCustomerId: str = Field(..., description="Platform player ID.")
    stake: str = Field(..., description="Original stake amount.")
    payout: str = Field(..., description="Win payout amount (0 for a loss).")
    currency: str = Field(..., description="ISO-4217 currency code.")
    settledAt: str = Field(..., description="ISO-8601 settlement timestamp.")
    outcome: str = Field(..., description="WIN, LOSS, VOID, PARTIAL_WIN.")


class KambiCashOutRequest(BaseModel):
    """Request to cash out an open Kambi bet."""

    betId: str = Field(..., description="Kambi bet ID to cash out.")
    cashOutValue: str = Field(..., description="Current cash-out value offered by Kambi.")
    token: str = Field(..., description="Platform session token.")


class KambiBalanceRequest(BaseModel):
    """Kambi balance query — called before bet slip presentation."""

    externalCustomerId: str = Field(..., description="Platform player ID.")
    token: str = Field(..., description="Platform session token.")
    currency: str = Field(..., description="ISO-4217 currency code.")


class KambiBalanceResponse(BaseModel):
    """Platform response to Kambi's balance query."""

    balance: str = Field(..., description="Cash balance as decimal string.")
    bonus: str = Field(default="0", description="Bonus balance as decimal string.")
    currency: str = Field(..., description="ISO-4217 currency code.")


__all__ = [
    "KambiBalanceRequest",
    "KambiBalanceResponse",
    "KambiBetPlacementRequest",
    "KambiBetReceiptEvent",
    "KambiCashOutRequest",
    "KambiCoupon",
    "KambiSelection",
    "KambiSettlementEvent",
]
