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
gameservice.suppliers.netent.models — NetEnt API Types
=======================================================

NetEnt's SEAMLESS wallet service uses JSON over HTTPS.  The supplier's game
server calls the platform's wallet endpoints.

Multi-credit rounds
-------------------
NetEnt can send multiple credits for a single round.  The ``roundEnded``
field signals whether this is the final credit for the round.  The platform
must handle partial credits without closing the round prematurely.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class NetEntDebitRequest(BaseModel):
    """NetEnt debit (bet) callback payload."""

    userId: str = Field(..., description="Platform player ID.")
    casinoSessionId: str = Field(..., description="NetEnt casino session ID.")
    gameId: str = Field(..., description="NetEnt game identifier.")
    roundId: str = Field(..., description="NetEnt round identifier.")
    transactionId: str = Field(..., description="NetEnt unique transaction ID.")
    amount: str = Field(..., description="Bet amount as decimal string.")
    currency: str = Field(..., description="ISO-4217 currency code.")
    gameType: str = Field(default="SLOT", description="Game type (SLOT, TABLE, etc.).")
    actionType: str = Field(default="SPIN", description="Action type.")


class NetEntCreditRequest(BaseModel):
    """NetEnt credit (win) callback payload.

    Multiple credits may be sent for the same round when features
    (free spins, pick bonus) award additional payouts.
    """

    userId: str = Field(..., description="Platform player ID.")
    casinoSessionId: str = Field(..., description="NetEnt casino session ID.")
    gameId: str = Field(..., description="NetEnt game identifier.")
    roundId: str = Field(..., description="Round this credit belongs to.")
    transactionId: str = Field(..., description="NetEnt unique transaction ID.")
    amount: str = Field(..., description="Win amount as decimal string.")
    currency: str = Field(..., description="ISO-4217 currency code.")
    roundEnded: bool = Field(
        default=True,
        description="True = final credit for this round; False = more credits to come.",
    )
    actionType: str = Field(default="WIN", description="Action type.")


class NetEntRollbackRequest(BaseModel):
    """NetEnt rollback (bet cancellation) callback."""

    userId: str = Field(..., description="Platform player ID.")
    casinoSessionId: str = Field(..., description="NetEnt casino session ID.")
    roundId: str = Field(..., description="Round to roll back.")
    transactionId: str = Field(..., description="Original debit transaction to reverse.")
    currency: str = Field(..., description="ISO-4217 currency code.")


class NetEntWalletResponse(BaseModel):
    """Standard response for all NetEnt wallet callbacks."""

    status: str = Field(default="OK", description='"OK" on success.')
    balance: str = Field(..., description="Cash balance after the operation.")
    bonus: str = Field(default="0", description="Bonus balance after the operation.")
    transactionId: str = Field(
        ..., description="Platform transaction ID."
    )
    currency: str = Field(..., description="ISO-4217 currency code.")


__all__ = [
    "NetEntCreditRequest",
    "NetEntDebitRequest",
    "NetEntRollbackRequest",
    "NetEntWalletResponse",
]
