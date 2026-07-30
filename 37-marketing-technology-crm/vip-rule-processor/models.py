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
Domain models for the VIP Rule Processor.

The VIP rule processor determines a player's VIP tier based on their
30-day deposit and betting activity. Rules are stored in the database
and evaluated against aggregated player activity.

v2.0 improvements over the original:
  - Weighted bet volume (live casino 1.5x, table games 1.3x)
  - Net deposit consideration (deposits minus withdrawals)
  - Frequency bonus (active-days multiplier)
  - Responsible gambling check (self-excluded players always get None tier)
  - Minimum account age enforcement
  - Cooldown period between tier changes

Value objects (UserId, BrandId, RuleId) provide type safety to prevent
parameter swaps in function signatures -- a common source of bugs when
passing multiple int/long arguments.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------

class UserId(BaseModel):
    value: int

    def __hash__(self) -> int:
        return hash(self.value)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, UserId):
            return self.value == other.value
        return NotImplemented


class BrandId(BaseModel):
    value: int

    def __hash__(self) -> int:
        return hash(self.value)


class RuleId(BaseModel):
    value: int

    def __hash__(self) -> int:
        return hash(self.value)


class JobId(BaseModel):
    value: int


# ---------------------------------------------------------------------------
# VIP rule definition
# ---------------------------------------------------------------------------

class JurisdictionThreshold(BaseModel):
    deposit_low_boundary: int | None = None
    deposit_hi_boundary: int | None = None
    handle_low_boundary: int | None = None
    handle_hi_boundary: int | None = None


class VipTierBenefit(BaseModel):
    name: str
    description: str
    value: str | None = None


class VipRule(BaseModel):
    """
    A single VIP tier rule.

    Boundaries are optional: None = no limit on that side.
    A rule of depositLow=50000, depositHi=None means "deposit >= £500".
    """
    rule_id: RuleId
    brand_id: BrandId
    status_name: str
    tier: int
    deposit_low_boundary: int | None = None
    deposit_hi_boundary: int | None = None
    handle_low_boundary: int | None = None
    handle_hi_boundary: int | None = None
    minimum_days_active: int | None = None
    cooldown_days: int | None = None
    jurisdiction_overrides: dict[str, JurisdictionThreshold] = Field(default_factory=dict)
    benefits: list[VipTierBenefit] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Activity models
# ---------------------------------------------------------------------------

class Bet(BaseModel):
    id: int
    user_id: UserId
    amount: int
    timestamp: datetime
    game_type: str | None = None


class Deposit(BaseModel):
    id: int
    user_id: UserId
    amount: int
    timestamp: datetime
    payment_method: str | None = None


class Withdrawal(BaseModel):
    id: int
    user_id: UserId
    amount: int
    timestamp: datetime


class PlayerActivity(BaseModel):
    """Aggregated 30-day activity window for a player."""
    user_id: UserId
    total_bet_volume: int
    total_deposit_volume: int
    total_withdrawal_volume: int
    net_deposit_volume: int
    bet_count: int
    deposit_count: int
    active_days: int
    account_age_days: int
    game_type_bet_volumes: dict[str, int] = Field(default_factory=dict)
    is_self_excluded: bool = False
    self_exclusion_end: datetime | None = None


# ---------------------------------------------------------------------------
# User status audit trail
# ---------------------------------------------------------------------------

class LastChange(BaseModel):
    change_type: str  # "bet" | "deposit" | "withdrawal" | "scheduler" | "manual"
    reference_id: int | None = None
    operator: str | None = None
    reason: str | None = None
    timestamp: datetime | None = None


class UserStatus(BaseModel):
    id: int
    user_id: UserId
    rule_id: RuleId | None = None
    rule_name: str | None = None
    tier: int | None = None
    bet_volume: int
    deposit_volume: int
    net_deposit_volume: int
    timestamp: datetime
    last_change: LastChange
    jurisdiction: str | None = None


# ---------------------------------------------------------------------------
# Scheduler job
# ---------------------------------------------------------------------------

class SchedulerJob(BaseModel):
    id: int
    timestamp: datetime
    done: bool
    users_processed: int | None = None
    completed_at: datetime | None = None


# ---------------------------------------------------------------------------
# Kafka events
# ---------------------------------------------------------------------------

class AccountsEvent(BaseModel):
    event_id: str
    event_type: str
    user_id: int
    brand_id: int
    amount: int
    currency: str
    game_type: str | None = None
    timestamp: str  # ISO 8601


class UserVipRuleUpdated(BaseModel):
    user_id: UserId
    brand_id: BrandId
    previous_tier: str | None = None
    new_tier: str | None = None
    previous_rule_id: RuleId | None = None
    new_rule_id: RuleId | None = None
    bet_volume: int
    deposit_volume: int
    net_deposit_volume: int
    triggered_by: str
    benefits: list[VipTierBenefit] = Field(default_factory=list)
    timestamp: datetime


class RecalculateCommand(BaseModel):
    user_id: UserId
    brand_id: BrandId
    timestamp: datetime


# ---------------------------------------------------------------------------
# Tier change notification
# ---------------------------------------------------------------------------

class TierChangeNotification(BaseModel):
    user_id: UserId
    direction: str  # "initial" | "upgrade" | "downgrade" | "unchanged"
    previous_tier: str | None = None
    new_tier: str | None = None
    benefits: list[VipTierBenefit] = Field(default_factory=list)
    vip_manager_assigned: str | None = None


# ---------------------------------------------------------------------------
# Application configuration
# ---------------------------------------------------------------------------

class DbConfig(BaseModel):
    connection_uri: str
    user: str
    password: str
    schema: str = "public"


class KafkaConfig(BaseModel):
    bootstrap_servers: str
    group_id: str
    transactions_topic: str
    commands_topic: str
    events_topic: str


class SchedulerConfig(BaseModel):
    enabled: bool = True
    brand_id: int = 1
    clock_interval_seconds: int = 600
    start_hour: int = 0
    start_minute: int = 0


class HttpConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080


class AppConfig(BaseModel):
    db: DbConfig
    kafka: KafkaConfig
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    http: HttpConfig = Field(default_factory=HttpConfig)
