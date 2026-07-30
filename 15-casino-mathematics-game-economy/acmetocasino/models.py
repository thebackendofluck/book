# Companion code for "The Backend of Luck" - Chapter 15, Casino Mathematics and Game Economy.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Pydantic models for the Game Aggregation Layer.
"""

import datetime
import uuid
from decimal import Decimal

from pydantic import BaseModel, Field


class LaunchRequest(BaseModel):
    player_id: uuid.UUID
    game_slug: str = Field(min_length=1, max_length=100)


class GameSession(BaseModel):
    id: uuid.UUID
    player_id: uuid.UUID
    game_slug: str
    status: str
    rounds_played: int
    total_bet: Decimal
    total_win: Decimal
    created_at: datetime.datetime
    closed_at: datetime.datetime | None = None


class BetRequest(BaseModel):
    session_id: uuid.UUID
    player_id: uuid.UUID | None = None
    game_slug: str | None = None
    amount: Decimal | None = None
    bet_amount: Decimal | None = None

    def get_amount(self) -> Decimal:
        """Return the bet amount from whichever field was provided."""
        return self.amount or self.bet_amount or Decimal("0")


class BetResult(BaseModel):
    id: uuid.UUID | None = None
    session_id: uuid.UUID
    player_id: uuid.UUID
    game_slug: str
    bet_amount: Decimal
    win_amount: Decimal
    new_balance: Decimal | None = None
    rng_seed_hash: str
    target_rtp: Decimal
    created_at: datetime.datetime
    outcome: dict | None = None


class RNGBatchRequest(BaseModel):
    count: int = Field(default=100, ge=1, le=500)


class RNGBatchResponse(BaseModel):
    numbers: list[float]
    audit: list[str]


class GameConfig(BaseModel):
    game_slug: str
    target_rtp: Decimal
    min_bet: Decimal = Decimal("0.10")
    max_bet: Decimal = Decimal("10000.00")
    max_multiplier: Decimal = Decimal("10000.00")
