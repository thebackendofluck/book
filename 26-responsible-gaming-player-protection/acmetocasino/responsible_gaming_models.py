# Companion code for "The Backend of Luck" - Chapter 26, Responsible Gaming and Player Protection Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Pydantic models for the Responsible Gaming module.
"""

import datetime
import uuid
from decimal import Decimal

from pydantic import BaseModel, Field


class DepositLimitRequest(BaseModel):
    period: str = Field(description="daily, weekly, or monthly")
    amount: Decimal = Field(gt=0, max_digits=15, decimal_places=2)


class DepositLimit(BaseModel):
    id: uuid.UUID
    player_id: uuid.UUID
    period: str
    amount: Decimal
    active: bool
    created_at: datetime.datetime
    effective_at: datetime.datetime


class SelfExclusionRequest(BaseModel):
    # ge=1/le=3650 are only the outer bounds the model can express. The
    # actual floor is jurisdiction-dependent (6 months for UK/Ontario/Malta,
    # 1 month for Sweden/Denmark; see Chapter 26 Section 26.2) and is
    # enforced in responsible_gaming_service.self_exclude(), not here.
    duration_days: int = Field(ge=1, le=3650, description="1 day to 10 years; jurisdictional minimums are enforced server-side")
    jurisdiction: str | None = Field(
        default=None,
        description="ISO-ish jurisdiction code (GB, SE, ON, MT, DK, ...). "
                     "Missing or unrecognized jurisdictions are treated as "
                     "the strictest known minimum (6 months).",
    )
    reason: str | None = None


class SelfExclusion(BaseModel):
    id: uuid.UUID
    player_id: uuid.UUID
    duration_days: int
    reason: str | None
    starts_at: datetime.datetime
    ends_at: datetime.datetime
    active: bool
    created_at: datetime.datetime


class RealityCheck(BaseModel):
    player_id: uuid.UUID
    session_duration_minutes: int
    total_bet: Decimal
    total_win: Decimal
    net_position: Decimal
    active_limits: list[DepositLimit]
    excluded: bool
    exclusion_ends_at: datetime.datetime | None = None


class ResponsibleGamingStatus(BaseModel):
    player_id: uuid.UUID
    limits: list[DepositLimit]
    exclusion: SelfExclusion | None
    reality_check: RealityCheck
