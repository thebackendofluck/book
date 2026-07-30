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
gameservice.suppliers.evolution.models — Evolution API Request/Response Types
==============================================================================

These models represent the data structures exchanged with Evolution Gaming's
REST API and the inbound webhook payloads delivered to the platform.

Evolution API conventions
--------------------------
* All monetary amounts are integers in the **lowest currency denomination**
  (e.g. cents for EUR/USD, pence for GBP).  The translator converts to/from
  :class:`decimal.Decimal` at the boundary.
* Inbound webhook payloads are JSON and carry a ``X-Evo-Signature`` header
  containing an HMAC-SHA256 signature over the raw request body.
* Evolution session tokens expire after 60 minutes of inactivity.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class EvolutionSessionRequest(BaseModel):
    """Outbound payload for creating a new Evolution live-casino session."""

    uuid: str = Field(..., description="Platform-generated request UUID for idempotency.")
    player: "EvolutionPlayerData" = Field(..., description="Player identity data.")
    config: "EvolutionGameConfig" = Field(..., description="Game configuration.")
    urls: "EvolutionUrlConfig" = Field(..., description="Redirect and callback URLs.")


class EvolutionPlayerData(BaseModel):
    """Player identity fields expected by the Evolution session endpoint."""

    id: str = Field(..., description="Platform player ID.")
    update: bool = Field(default=True, description="Update player record if changed.")
    firstName: str = Field(default="", description="Player first name (display only).")
    lastName: str = Field(default="", description="Player last name (display only).")
    country: str = Field(default="", description="ISO-3166-1 alpha-2 country code.")
    language: str = Field(default="en", description="IETF language tag.")
    currency: str = Field(..., description="ISO-4217 currency code.")
    session: "EvolutionPlayerSession" = Field(
        ..., description="Platform session reference."
    )


class EvolutionPlayerSession(BaseModel):
    """Platform session reference embedded in Evolution's player payload."""

    id: str = Field(..., description="Platform session token.")
    ip: str = Field(default="", description="Player IP address.")


class EvolutionGameConfig(BaseModel):
    """Game-specific configuration for the Evolution session request."""

    game: "EvolutionGameRef" = Field(..., description="Game to launch.")
    channel: "EvolutionChannel" = Field(..., description="Delivery channel.")


class EvolutionGameRef(BaseModel):
    """Reference to a specific Evolution game table."""

    table: "EvolutionTableRef" = Field(..., description="Table reference.")


class EvolutionTableRef(BaseModel):
    """Live table identifier within Evolution's system."""

    id: str = Field(..., description="Evolution table ID (e.g. 'LightningRoulette0000001').")


class EvolutionChannel(BaseModel):
    """Delivery channel specification."""

    wrapped: bool = Field(default=True, description="Whether to use the wrapped client.")
    mobile: bool = Field(default=False, description="Mobile-optimised layout.")


class EvolutionUrlConfig(BaseModel):
    """URL configuration for Evolution session flow."""

    lobby: str = Field(default="", description="URL to return to after leaving the game.")


class EvolutionSessionResponse(BaseModel):
    """Response from Evolution's session creation endpoint."""

    entry: str = Field(..., description="The fully-qualified game launch URL.")
    entryEmbedded: str = Field(
        default="",
        description="Embedded URL for iframe integration.",
    )


class EvolutionWebhookEvent(BaseModel):
    """Inbound webhook payload pushed by Evolution to the platform.

    All events share this envelope; the ``type`` field discriminates the
    specific action to apply to the wallet.

    Evolution event types
    ---------------------
    ``DEBIT``        — Player bet; deduct from wallet.
    ``CREDIT``       — Round win; add to wallet.
    ``CANCEL``       — Round cancelled; reverse the debit.
    ``TIP``          — Dealer tip; deduct from wallet (no win credit).
    ``PROMO``        — Promotional credit (bonus buy in live games).
    """

    sid: str = Field(..., description="Evolution session ID.")
    type: str = Field(..., description="Event type: DEBIT, CREDIT, CANCEL, TIP, PROMO.")
    value: int = Field(..., description="Amount in the smallest currency unit (e.g. cents).")
    gameId: str = Field(..., description="Evolution game/table identifier.")
    roundId: str = Field(..., description="Evolution round identifier.")
    transactionId: str = Field(
        ..., description="Evolution's unique transaction reference."
    )
    token: str = Field(..., description="Platform session token for player lookup.")
    balance: int = Field(
        default=0,
        description="Expected balance after the operation (for reconciliation).",
    )
    extra: dict[str, Any] = Field(
        default_factory=dict,
        description="Supplier-specific contextual data.",
    )


class EvolutionWebhookResponse(BaseModel):
    """Platform response to an inbound Evolution webhook event."""

    status: str = Field(default="OK", description='Always "OK" on success.')
    balance: int = Field(..., description="Player balance after applying the event (cents).")
    bonus: int = Field(
        default=0,
        description="Bonus balance after the event (cents).",
    )
    transactionId: str = Field(
        ...,
        description="Platform-generated transaction ID for this event.",
    )


__all__ = [
    "EvolutionChannel",
    "EvolutionGameConfig",
    "EvolutionGameRef",
    "EvolutionPlayerData",
    "EvolutionPlayerSession",
    "EvolutionSessionRequest",
    "EvolutionSessionResponse",
    "EvolutionTableRef",
    "EvolutionUrlConfig",
    "EvolutionWebhookEvent",
    "EvolutionWebhookResponse",
]
