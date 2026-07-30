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
gameservice.suppliers.push_gaming.models — Push Gaming API Types
================================================================

Push Gaming uses a SEAMLESS JSON wallet API with HMAC-SHA256 authentication.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PushGamingDebitRequest(BaseModel):
    """Push Gaming bet callback."""

    playerId: str = Field(..., description="Platform player ID.")
    sessionToken: str = Field(..., description="Platform session token.")
    gameRef: str = Field(..., description="Push Gaming game reference.")
    roundRef: str = Field(..., description="Round reference.")
    txRef: str = Field(..., description="Unique transaction reference.")
    betAmount: str = Field(..., description="Bet amount as decimal string.")
    currency: str = Field(..., description="ISO-4217 currency code.")
    actionType: str = Field(default="BET", description="Action type: BET, FREE_SPIN.")


class PushGamingCreditRequest(BaseModel):
    """Push Gaming win callback."""

    playerId: str = Field(..., description="Platform player ID.")
    sessionToken: str = Field(..., description="Platform session token.")
    gameRef: str = Field(..., description="Push Gaming game reference.")
    roundRef: str = Field(..., description="Round reference.")
    txRef: str = Field(..., description="Unique transaction reference.")
    winAmount: str = Field(..., description="Win amount as decimal string.")
    currency: str = Field(..., description="ISO-4217 currency code.")
    jackpotContribution: str = Field(default="0", description="Jackpot win amount.")
    roundEnded: bool = Field(default=True)


class PushGamingRollbackRequest(BaseModel):
    """Push Gaming rollback callback."""

    playerId: str = Field(..., description="Platform player ID.")
    sessionToken: str = Field(..., description="Platform session token.")
    roundRef: str = Field(..., description="Round to cancel.")
    txRef: str = Field(..., description="Original debit transaction reference.")
    currency: str = Field(..., description="ISO-4217 currency code.")


class PushGamingWalletResponse(BaseModel):
    """Standard Push Gaming wallet response."""

    status: str = Field(default="OK")
    balance: str = Field(..., description="Cash balance after the operation.")
    bonus: str = Field(default="0")
    txId: str = Field(..., description="Platform transaction ID.")
    currency: str = Field(...)


__all__ = [
    "PushGamingCreditRequest",
    "PushGamingDebitRequest",
    "PushGamingRollbackRequest",
    "PushGamingWalletResponse",
]
