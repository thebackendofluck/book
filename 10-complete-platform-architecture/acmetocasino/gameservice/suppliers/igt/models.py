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
gameservice.suppliers.igt.models — IGT API Types
=================================================

IGT uses both REST/JSON (new platform) and SOAP/XML (legacy services).

The SOAP models are represented as plain dataclasses since SOAP payloads are
typically built with string templates.  In production, use ``zeep`` or
``suds`` to generate strongly-typed SOAP stubs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# REST models
# ---------------------------------------------------------------------------


class IGTSessionRequest(BaseModel):
    """IGT session creation request (REST)."""

    operatorId: str = Field(..., description="IGT operator/system ID.")
    playerId: str = Field(..., description="Platform player ID.")
    sessionToken: str = Field(..., description="Platform session token.")
    gameCode: str = Field(..., description="IGT game code.")
    currency: str = Field(..., description="ISO-4217 currency code.")
    language: str = Field(default="en", description="Language code.")
    channel: str = Field(default="web", description="Delivery channel.")


class IGTSessionResponse(BaseModel):
    """IGT session creation response (REST)."""

    sessionId: str = Field(..., description="IGT session identifier.")
    gameUrl: str = Field(..., description="Game launch URL.")
    token: str = Field(..., description="IGT session token.")


class IGTRoundClosedEvent(BaseModel):
    """IGT round-closed event from the settlement feed (REST)."""

    roundId: str = Field(..., description="IGT round identifier.")
    playerId: str = Field(..., description="Platform player ID.")
    gameCode: str = Field(..., description="IGT game code.")
    betAmount: str = Field(..., description="Total bet amount.")
    winAmount: str = Field(..., description="Total win amount (0 for a loss).")
    currency: str = Field(..., description="ISO-4217 currency code.")
    closedAt: str = Field(..., description="ISO-8601 round close timestamp.")
    gameTypeCode: str = Field(default="", description="IGT regulatory game type code.")
    denominationCode: str = Field(
        default="",
        description="IGT land-based denomination code (regulatory).",
    )


class IGTJackpotPoolResponse(BaseModel):
    """Response from IGT's jackpot pool query (SOAP translated to JSON)."""

    networkId: str = Field(..., description="MegaJackpots network ID.")
    pools: list["IGTJackpotPool"] = Field(..., description="Individual jackpot pools.")


class IGTJackpotPool(BaseModel):
    """An individual jackpot pool within the MegaJackpots network."""

    poolId: str = Field(..., description="Pool identifier.")
    name: str = Field(..., description="Display name (e.g. 'Mega', 'Major', 'Mini').")
    currentValue: str = Field(..., description="Current pool value as decimal string.")
    currency: str = Field(..., description="ISO-4217 currency code.")
    seedValue: str = Field(default="0", description="Pool reset seed value.")


# ---------------------------------------------------------------------------
# SOAP stub models (represented as dataclasses)
# ---------------------------------------------------------------------------


@dataclass
class IGTSoapRequest:
    """Minimal SOAP envelope for IGT legacy service calls.

    In production, this is rendered to XML by a templating library.
    The structure mirrors IGT's WSDL-defined request types.
    """

    operation: str
    parameters: dict[str, Any] = field(default_factory=dict)
    username: str = ""
    password: str = ""


@dataclass
class IGTSoapResponse:
    """Parsed SOAP response from IGT legacy services."""

    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    fault_code: str = ""
    fault_message: str = ""


__all__ = [
    "IGTJackpotPool",
    "IGTJackpotPoolResponse",
    "IGTRoundClosedEvent",
    "IGTSessionRequest",
    "IGTSessionResponse",
    "IGTSoapRequest",
    "IGTSoapResponse",
]
