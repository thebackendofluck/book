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
gameservice.suppliers.playngo.models — Play'n GO API Types
===========================================================

Play'n GO uses a SEAMLESS JSON/REST wallet API.  Amounts are decimal strings.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PlayngoDebitRequest(BaseModel):
    """Play'n GO debit (bet) callback."""

    userId: str = Field(..., description="Platform player ID.")
    sessionToken: str = Field(..., description="Platform session token.")
    gameId: str = Field(..., description="Play'n GO game ID.")
    roundId: str = Field(..., description="Round identifier.")
    transactionId: str = Field(..., description="Unique transaction ID.")
    amount: str = Field(..., description="Bet amount as decimal string.")
    currency: str = Field(..., description="ISO-4217 currency code.")
    actionType: str = Field(default="SPIN", description="Action type.")
    freeRound: bool = Field(default=False, description="True for free-round bets.")


class PlayngoCreditRequest(BaseModel):
    """Play'n GO credit (win) callback."""

    userId: str = Field(..., description="Platform player ID.")
    sessionToken: str = Field(..., description="Platform session token.")
    gameId: str = Field(..., description="Play'n GO game ID.")
    roundId: str = Field(..., description="Round identifier.")
    transactionId: str = Field(..., description="Unique transaction ID.")
    amount: str = Field(..., description="Win amount as decimal string.")
    currency: str = Field(..., description="ISO-4217 currency code.")
    actionType: str = Field(default="WIN", description="Action type.")
    jackpotWin: str = Field(default="0", description="Jackpot component of the win.")
    roundEnded: bool = Field(default=True, description="Whether the round is closed.")


class PlayngoRollbackRequest(BaseModel):
    """Play'n GO rollback callback."""

    userId: str = Field(..., description="Platform player ID.")
    sessionToken: str = Field(..., description="Platform session token.")
    roundId: str = Field(..., description="Round to roll back.")
    transactionId: str = Field(..., description="Original debit transaction ID.")
    currency: str = Field(..., description="ISO-4217 currency code.")


class PlayngoWalletResponse(BaseModel):
    """Standard Play'n GO wallet callback response."""

    status: int = Field(default=0, description="0 = success.")
    balance: str = Field(..., description="Cash balance after the operation.")
    bonus: str = Field(default="0", description="Bonus balance.")
    transactionId: str = Field(..., description="Platform transaction ID.")
    currency: str = Field(..., description="ISO-4217 currency code.")


__all__ = [
    "PlayngoCreditRequest",
    "PlayngoDebitRequest",
    "PlayngoRollbackRequest",
    "PlayngoWalletResponse",
]
