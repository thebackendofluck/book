# Companion code for "The Backend of Luck" - Chapter 37, Marketing Technology and CRM Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Domain models for the player retention bonus engine.

The retention system calculates and awards bonuses to eligible players based
on their recent activity. Two main bonus types are implemented:
  - Daily reward bonus (based on prior-day activity and a configurable formula)
  - Birthday bonus (awarded on the player's birthday)

The calculator uses the Template Method pattern: the algorithm skeleton lives
in RetentionBonusCalculator, while concrete bonus types provide the SQL and
bonus parameters via the RetentionBonusType protocol.

Idempotency: the INSERT ... WHERE NOT EXISTS pattern ensures that re-running
for the same date/reason/user combination is a no-op.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class RetentionBonusSchedule(str, Enum):
    DAILY = "daily"


class RetentionFormula(BaseModel):
    id: int
    sql: str


class RewardBonusSettings(BaseModel):
    bonus_group: int
    bonus_formula: RetentionFormula
    schedule: RetentionBonusSchedule = RetentionBonusSchedule.DAILY
    bonus_percentage: int
    min_bonus: int
    max_bonus: int
    max_balance: int | None = None
    calc_threshold: float = 0.0
    days_since_deposit: int | None = None
    deposit_cap_days: int | None = None
    deposit_cap_percentage: float | None = None
    enabled: bool = True


class BirthdayBonusSettings(BaseModel):
    bonus_group: int
    calc_days: int = 1
    bonus_percentage: int
    min_bonus: int
    max_bonus: int
    non_loss_bonus: int
    unfunded_bonus: int
    enabled: bool = True


class BonusQueueItem(BaseModel):
    """A single pending bonus allocation from the DAILY_BONUS_ALLOCATION table."""
    reason: str
    brand_id: int
    user_id: int
    bonus: int  # minor currency units


# ---------------------------------------------------------------------------
# Rounding helper
# ---------------------------------------------------------------------------

ROUNDING_THRESHOLDS = [10.0, 50.0, 100.0, 500.0]
ROUNDING_VALUES = [1, 5, 10, 50]
DEFAULT_ROUNDING = 100


def round_bonus(bonus: int) -> int:
    """
    Round a bonus amount to the nearest tier-appropriate value.
    Amounts <= 10 round to 1, <= 50 to 5, <= 100 to 10, <= 500 to 50,
    otherwise to 100.
    """
    for threshold, rounding in zip(ROUNDING_THRESHOLDS, ROUNDING_VALUES):
        if bonus <= threshold:
            return int(rounding * (bonus // rounding))
    return int(DEFAULT_ROUNDING * (bonus // DEFAULT_ROUNDING))
