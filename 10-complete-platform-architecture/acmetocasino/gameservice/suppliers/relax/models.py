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
gameservice.suppliers.relax.models — Relax Gaming API Types
============================================================

Relax uses a SEAMLESS JSON wallet API with Silver Bullet partner studio support.
The ``partnerStudioId`` field identifies the originating studio for aggregated content.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RelaxDebitRequest(BaseModel):
    """Relax Gaming debit callback."""

    playerId: str = Field(..., description="Platform player ID.")
    sessionToken: str = Field(..., description="Platform session token.")
    gameCode: str = Field(..., description="Relax game code.")
    partnerStudioId: str = Field(
        default="",
        description="Silver Bullet partner studio ID (empty for Relax own titles).",
    )
    roundId: str = Field(..., description="Round identifier.")
    txId: str = Field(..., description="Unique transaction ID.")
    amount: str = Field(..., description="Bet amount as decimal string.")
    currency: str = Field(..., description="ISO-4217 currency code.")
    isFreeRound: bool = Field(default=False, description="True for free-round bets.")
    actionType: str = Field(default="SPIN")


class RelaxCreditRequest(BaseModel):
    """Relax Gaming credit callback."""

    playerId: str = Field(..., description="Platform player ID.")
    sessionToken: str = Field(..., description="Platform session token.")
    gameCode: str = Field(..., description="Relax game code.")
    partnerStudioId: str = Field(default="")
    roundId: str = Field(..., description="Round identifier.")
    txId: str = Field(..., description="Unique transaction ID.")
    amount: str = Field(..., description="Win amount as decimal string.")
    currency: str = Field(..., description="ISO-4217 currency code.")
    roundEnded: bool = Field(default=True)


class RelaxRollbackRequest(BaseModel):
    """Relax Gaming rollback callback."""

    playerId: str = Field(...)
    sessionToken: str = Field(...)
    roundId: str = Field(...)
    txId: str = Field(...)
    currency: str = Field(...)


class RelaxWalletResponse(BaseModel):
    """Standard Relax wallet response."""

    status: str = Field(default="OK")
    balance: str = Field(...)
    bonus: str = Field(default="0")
    txId: str = Field(...)
    currency: str = Field(...)


__all__ = [
    "RelaxCreditRequest",
    "RelaxDebitRequest",
    "RelaxRollbackRequest",
    "RelaxWalletResponse",
]
