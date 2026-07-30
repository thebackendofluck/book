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
promotions.py — Promotion domain models.

Mirrors Promotion.scala, PromotionType.scala, and RewardSetting.scala.
These are pure data classes used by the prize-backend; no HTTP server required.

Economic context:
    A 100% deposit match up to $200 with 35× wagering on 96% RTP slots
    means the player must wager $200 × 35 = $7,000.  Expected operator
    revenue = $7,000 × 4% = $280 > $200 bonus cost → promotion is profitable.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, field_validator, model_validator


# ---------------------------------------------------------------------------
# Promotion status lifecycle
# ---------------------------------------------------------------------------

class PromotionStatus(str, Enum):
    """
    Active/Resumed/Copied → players can opt-in, rewards are issued.
    Paused               → no new opt-ins, existing rewards continue.
    Stopped              → no new rewards, existing ones expire naturally.
    Disabled             → hidden from all player-facing surfaces.
    Revoked              → all outstanding rewards are clawed back.
    """
    ACTIVE   = "Active"
    DISABLED = "Disabled"
    STOPPED  = "Stopped"
    REVOKED  = "Revoked"
    PAUSED   = "Paused"
    RESUMED  = "Resumed"
    COPIED   = "Copied"

    @classmethod
    def is_player_facing(cls, status: "PromotionStatus") -> bool:
        return status in (cls.ACTIVE, cls.RESUMED, cls.COPIED)


# ---------------------------------------------------------------------------
# Promotion
# ---------------------------------------------------------------------------

class Promotion(BaseModel):
    """
    Core domain entity for casino promotions / bonus campaigns.

    Ties together brands, promotion type, reward settings, qualification
    requirements, and lifecycle status.
    """
    id: int
    brand_ids: set[int]
    marketing_category_id: int
    awarded_by: str
    language_id: int
    promotion_type_id: int
    qualification_requirement_id: int
    type_setting_ids: set[int]
    reward_setting_id: int
    player_messaging_id: int
    product_id: int
    is_active: bool
    name: str
    priority: int
    promotion_status: PromotionStatus
    send_external: bool
    segmentation: bool
    start_time: datetime
    end_time: datetime
    created_by: str
    created_at: datetime
    modified_by: str
    modified_at: datetime
    deleted_by: Optional[str] = None
    deleted_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class PromotionForm(BaseModel):
    """Form for creating or fully updating a promotion."""
    brand_ids: set[int]
    marketing_category_id: int
    awarded_by: str
    language_id: int
    promotion_type_id: int
    qualification_requirement_id: int
    type_setting_ids: set[int]
    reward_setting_id: int
    player_messaging_id: int
    product_id: int
    name: str
    priority: int
    promotion_status: PromotionStatus
    send_external: bool
    segmentation: bool
    start_time: datetime
    end_time: datetime

    @model_validator(mode="after")
    def validate_time_range(self) -> "PromotionForm":
        if self.start_time >= self.end_time:
            raise ValueError("start_time must be before end_time")
        return self


# ---------------------------------------------------------------------------
# Promotion Type
# ---------------------------------------------------------------------------

class PromotionType(BaseModel):
    """
    Classifies promotions by mechanic and regulatory category.

    Common categories: bonus, freespins, cashback, freebet, loyalty.
    The category field drives which RewardSetting fields are relevant.
    """
    id: int
    regulator_id: Optional[int] = None
    is_active: bool
    name: str
    category: str   # "bonus" | "freespins" | "cashback" | "freebet" | "loyalty"
    description: str
    created_by: str
    created_at: datetime
    modified_by: str
    modified_at: datetime
    deleted_by: Optional[str] = None
    deleted_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class PromotionTypeForm(BaseModel):
    name: str
    regulator_id: Optional[int] = None
    category: str
    description: str


class PromotionCalculation(BaseModel):
    """
    Defines the formula for computing a reward amount.
    Examples: flat (fixed amount), percentage (fraction of deposit), tiered.
    """
    id: int
    is_active: bool
    name: str
    created_by: str
    created_at: datetime
    modified_by: str
    modified_at: datetime
    deleted_by: Optional[str] = None
    deleted_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class PromotionCalculationForm(BaseModel):
    name: str


# ---------------------------------------------------------------------------
# Reward Setting
# ---------------------------------------------------------------------------

class RewardSetting(BaseModel):
    """
    Defines the economic parameters of a promotion's reward.

    NOTE: the monetary fields below are ``float`` for illustration; production
    money handling uses ``Decimal``/integer centavos to avoid float drift.

    Key fields and their meaning:
      reward_amount_min / reward_amount_max — bonus value range
      clawback_percentage       — fraction of bonus reclaimed if wagering unmet
      losses_percentage         — for cashback: % of net losses returned
      max_promotion_payout      — cap on total winnings from bonus funds
      max_player_daily_reward   — anti-abuse limit per player per day

    See module docstring for worked economic example.
    """
    id: int
    reward_type_id: int
    promotion_issue_id: int
    campaign_reoccurrence_id: int
    promotion_calculation_id: int
    is_active: bool
    expiry_date: datetime
    issue_date: datetime
    clawback_percentage: float
    is_clawback_full: bool
    losses_percentage: float
    reward_amount_min: float
    reward_amount_max: float
    max_promotion_payout: float
    max_player_daily_reward: float
    lead_time: datetime
    calculation: str
    created_by: str
    created_at: datetime
    modified_by: str
    modified_at: datetime
    deleted_by: Optional[str] = None
    deleted_at: Optional[datetime] = None

    @field_validator("clawback_percentage", "losses_percentage")
    @classmethod
    def validate_percentage(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"Percentage must be between 0 and 1, got {v}")
        return v

    model_config = {"from_attributes": True}


class RewardSettingForm(BaseModel):
    reward_type_id: int
    promotion_issue_id: int
    promotion_calculation_id: int
    campaign_reoccurrence_id: int
    expiry_date: datetime
    issue_date: datetime
    clawback_percentage: float
    is_clawback_full: bool
    losses_percentage: float
    reward_amount_min: float
    reward_amount_max: float
    max_promotion_payout: float
    max_player_daily_reward: float
    lead_time: datetime
    calculation: str
