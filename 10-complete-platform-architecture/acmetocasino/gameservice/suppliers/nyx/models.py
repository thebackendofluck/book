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
gameservice.suppliers.nyx.models — NYX API Types
=================================================

NYX/Scientific Games SEAMLESS wallet API.

The ``studioId`` field on each callback identifies which partner studio the
game originates from, enabling studio-specific wagering contribution rules.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class NYXDebitRequest(BaseModel):
    """NYX debit callback."""

    playerId: str = Field(..., description="Platform player ID.")
    sessionToken: str = Field(..., description="Platform session token.")
    gameId: str = Field(..., description="NYX game identifier.")
    studioId: str = Field(default="", description="Originating studio identifier.")
    roundId: str = Field(..., description="Round identifier.")
    txId: str = Field(..., description="Unique transaction ID.")
    amount: str = Field(..., description="Bet amount as decimal string.")
    currency: str = Field(..., description="ISO-4217 currency code.")
    isFreeRound: bool = Field(default=False, description="True for free-round spins.")


class NYXCreditRequest(BaseModel):
    """NYX credit callback."""

    playerId: str = Field(..., description="Platform player ID.")
    sessionToken: str = Field(..., description="Platform session token.")
    gameId: str = Field(..., description="NYX game identifier.")
    studioId: str = Field(default="", description="Originating studio identifier.")
    roundId: str = Field(..., description="Round identifier.")
    txId: str = Field(..., description="Unique transaction ID.")
    amount: str = Field(..., description="Win amount as decimal string.")
    currency: str = Field(..., description="ISO-4217 currency code.")
    roundEnded: bool = Field(default=True)


class NYXRollbackRequest(BaseModel):
    """NYX rollback callback."""

    playerId: str = Field(..., description="Platform player ID.")
    sessionToken: str = Field(..., description="Platform session token.")
    roundId: str = Field(..., description="Round to cancel.")
    txId: str = Field(..., description="Original debit transaction ID.")
    currency: str = Field(..., description="ISO-4217 currency code.")


class NYXWalletResponse(BaseModel):
    """Standard NYX wallet response."""

    status: str = Field(default="OK")
    balance: str = Field(...)
    bonus: str = Field(default="0")
    txId: str = Field(...)
    currency: str = Field(...)


__all__ = [
    "NYXCreditRequest",
    "NYXDebitRequest",
    "NYXRollbackRequest",
    "NYXWalletResponse",
]
