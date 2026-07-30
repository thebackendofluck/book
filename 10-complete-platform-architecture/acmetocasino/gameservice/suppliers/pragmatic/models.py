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
gameservice.suppliers.pragmatic.models — Pragmatic Play API Types
==================================================================

Pragmatic Play's SEAMLESS API uses JSON over HTTPS.  The supplier calls the
operator's wallet endpoints for:

* ``/balance``   — Query current balance.
* ``/debit``     — Apply a bet (wager).
* ``/credit``    — Apply a win (payout).
* ``/rollback``  — Cancel a bet (round abandoned).
* ``/promoWin``  — Promotional credit (Drops & Wins prize).

Pragmatic sends a hash parameter (MD5 of sorted key=value pairs + secret_key)
with every call for authentication.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PragmaticCallbackRequest(BaseModel):
    """Base model for all inbound Pragmatic Play wallet callbacks."""

    userId: str = Field(..., description="Platform player ID.")
    currency: str = Field(..., description="ISO-4217 currency code.")
    token: str = Field(..., description="Platform session token.")
    hash: str = Field(..., description="MD5 authentication hash.")
    extSessionId: str = Field(default="", description="External session identifier.")


class PragmaticBalanceRequest(PragmaticCallbackRequest):
    """Pragmatic balance query — called before each round."""


class PragmaticDebitRequest(PragmaticCallbackRequest):
    """Pragmatic debit (bet placement) callback."""

    gameId: str = Field(..., description="Pragmatic game identifier.")
    roundId: str = Field(..., description="Pragmatic round identifier.")
    amount: str = Field(..., description="Bet amount as a decimal string.")
    transactionId: str = Field(..., description="Pragmatic unique transaction ID.")
    actionId: str = Field(
        default="SPIN",
        description="Action subtype: SPIN, FREE_SPIN, BONUS_BUY, etc.",
    )
    jackpotContribution: str = Field(
        default="0",
        description="Jackpot contribution amount as decimal string.",
    )


class PragmaticCreditRequest(PragmaticCallbackRequest):
    """Pragmatic credit (win payout) callback."""

    gameId: str = Field(..., description="Pragmatic game identifier.")
    roundId: str = Field(..., description="Pragmatic round identifier.")
    amount: str = Field(..., description="Win amount as a decimal string.")
    transactionId: str = Field(..., description="Pragmatic unique transaction ID.")
    actionId: str = Field(default="SPIN", description="Action subtype.")
    jackpotWin: str = Field(
        default="0",
        description="Jackpot prize component of this win.",
    )
    roundEnded: bool = Field(
        default=True,
        description="Whether this credit closes the round.",
    )


class PragmaticRollbackRequest(PragmaticCallbackRequest):
    """Pragmatic rollback (bet cancellation) callback."""

    roundId: str = Field(..., description="Round to roll back.")
    transactionId: str = Field(..., description="The debit transaction to reverse.")
    gameId: str = Field(default="", description="Pragmatic game identifier.")


class PragmaticPromoWinRequest(PragmaticCallbackRequest):
    """Pragmatic promotional win — Drops & Wins tournament prize."""

    campaignId: str = Field(..., description="Drops & Wins campaign ID.")
    amount: str = Field(..., description="Prize amount.")
    transactionId: str = Field(..., description="Unique promotional transaction ID.")


class PragmaticCallbackResponse(BaseModel):
    """Standard response envelope for all Pragmatic wallet callbacks."""

    error: int = Field(default=0, description="0 = success; non-zero = error code.")
    description: str = Field(default="", description="Error description if error != 0.")
    currency: str = Field(..., description="ISO-4217 currency code.")
    cash: str = Field(..., description="Cash balance after the operation.")
    bonus: str = Field(default="0", description="Bonus balance after the operation.")
    transactionId: str = Field(
        ..., description="Platform transaction ID for this operation."
    )
    usedPromo: str = Field(
        default="0",
        description="Amount deducted from promotional balance.",
    )
    extra: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "PragmaticBalanceRequest",
    "PragmaticCallbackRequest",
    "PragmaticCallbackResponse",
    "PragmaticCreditRequest",
    "PragmaticDebitRequest",
    "PragmaticPromoWinRequest",
    "PragmaticRollbackRequest",
]
