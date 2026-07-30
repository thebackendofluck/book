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
gameservice.suppliers.hacksaw.models — Hacksaw Gaming API Types
================================================================

Hacksaw uses a SEAMLESS JSON wallet API.  Crash game rounds have a
``cashoutMultiplier`` field indicating the player's cash-out point.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class HacksawDebitRequest(BaseModel):
    """Hacksaw debit callback."""

    playerId: str = Field(..., description="Platform player ID.")
    sessionId: str = Field(..., description="Platform session token.")
    gameCode: str = Field(..., description="Hacksaw game code.")
    roundId: str = Field(..., description="Round identifier.")
    txId: str = Field(..., description="Unique transaction ID.")
    amount: str = Field(..., description="Bet amount as decimal string.")
    currency: str = Field(..., description="ISO-4217 currency code.")
    betType: str = Field(default="BET", description="Bet type: BET, BONUS_BUY.")
    isCrashGame: bool = Field(default=False, description="True for crash game rounds.")


class HacksawCreditRequest(BaseModel):
    """Hacksaw credit callback."""

    playerId: str = Field(..., description="Platform player ID.")
    sessionId: str = Field(..., description="Platform session token.")
    gameCode: str = Field(..., description="Hacksaw game code.")
    roundId: str = Field(..., description="Round identifier.")
    txId: str = Field(..., description="Unique transaction ID.")
    amount: str = Field(..., description="Win amount as decimal string.")
    currency: str = Field(..., description="ISO-4217 currency code.")
    cashoutMultiplier: str = Field(
        default="",
        description="Multiplier at which the player cashed out (crash games only).",
    )
    roundEnded: bool = Field(default=True, description="Whether this closes the round.")


class HacksawRollbackRequest(BaseModel):
    """Hacksaw rollback callback."""

    playerId: str = Field(..., description="Platform player ID.")
    sessionId: str = Field(..., description="Platform session token.")
    roundId: str = Field(..., description="Round to cancel.")
    txId: str = Field(..., description="Original debit transaction ID.")
    currency: str = Field(..., description="ISO-4217 currency code.")


class HacksawWalletResponse(BaseModel):
    """Standard Hacksaw wallet callback response."""

    status: str = Field(default="OK", description='"OK" on success.')
    balance: str = Field(..., description="Cash balance after the operation.")
    txId: str = Field(..., description="Platform transaction ID.")
    currency: str = Field(..., description="ISO-4217 currency code.")


__all__ = [
    "HacksawCreditRequest",
    "HacksawDebitRequest",
    "HacksawRollbackRequest",
    "HacksawWalletResponse",
]
