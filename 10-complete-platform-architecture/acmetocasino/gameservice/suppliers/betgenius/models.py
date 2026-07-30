# Companion code for "The Backend of Luck" - Chapter 10, Complete Platform Architecture.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Pydantic models for Betgenius pushed wallet events."""

from __future__ import annotations

from pydantic import BaseModel, Field


class BetgeniusWalletEvent(BaseModel):
    """Signed wallet event pushed by Betgenius."""

    eventId: str = Field(..., description="Betgenius event identifier.")
    externalCustomerId: str = Field(..., description="Platform player ID.")
    roundId: str = Field(..., description="Sports bet or virtual-sport round ID.")
    transactionType: str = Field(..., description="DEBIT, CREDIT, or ROLLBACK.")
    amount: str = Field(..., description="Money amount as a decimal string.")
    currency: str = Field(..., description="ISO-4217 currency code.")
    product_type: str = Field(
        default="sportsbook",
        description="sportsbook or virtual_sports.",
    )
    supplier_ref: str = Field(..., description="Idempotency reference.")
    occurredAt: str = Field(..., description="ISO-8601 event timestamp.")
    signature: str | None = Field(default=None, description="Event signature.")


class BetgeniusLaunchMetadata(BaseModel):
    """Metadata returned when launching the Betgenius client."""

    offering: str
    jurisdiction: str
    currency: str


__all__ = ["BetgeniusLaunchMetadata", "BetgeniusWalletEvent"]
