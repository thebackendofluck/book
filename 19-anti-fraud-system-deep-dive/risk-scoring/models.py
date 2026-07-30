# Companion code for "The Backend of Luck" - Chapter 19, Anti-Fraud System Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
models.py — Domain model for the multi-matrix risk scoring system.

Mirrors ScoreMatrix.scala, Messages.scala, and EventTypes.scala.

Six risk matrices are used in iGaming compliance:
  RG   – Responsible Gambling (player protection)
  CIR  – Customer Intelligence Response (behavioural analysis)
  CRA  – Customer Risk Assessment (financial risk)
  VIP  – VIP Alert (high-value player monitoring)
  AFF  – Affordability (spending sustainability)
  RGMX – Real-time Gambling Matrix (live session monitoring)
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Matrix identifiers
# ---------------------------------------------------------------------------

MATRIX_RG  = "rg"
MATRIX_CI  = "cir"
MATRIX_CRA = "cra"
MATRIX_VIP = "vip"
MATRIX_AFF = "aff"

SCORE_TYPE_RG2  = "RG2"
SCORE_TYPE_RG3  = "RG3"
RESET_TYPE_RG2  = "RG2-reset"
RESET_TYPE_RG3  = "RG3-reset"


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------

class EventType(str, Enum):
    """
    Events that trigger risk matrix scoring.

    storage=True entries are persisted to user_event; storage=False are
    transient (e.g. deposit-failed is only used for real-time alerting).
    """
    DEPOSIT_CONFIRMED       = "deposit-confirmed"
    DEPOSIT_DECLINED        = "deposit-declined"
    DEPOSIT_FAILED          = "deposit-failed"          # not stored
    DEPOSIT_LIMIT_INCREASED = "deposit-limit-increased"
    DEPOSIT_LIMIT_CHANGED   = "deposit-limit-changed"
    USER_REVERSED_WITHDRAWAL = "user-reversed-withdrawal"
    RESET_SCORE             = "reset-score"
    LOSS_LIMIT_REACHED      = "loss-limit-reached"
    TIMEOUT_APPLIED         = "timeout-apply"
    INTERACTION_APPLIED     = "interaction-apply"

    @property
    def storage(self) -> bool:
        return self != EventType.DEPOSIT_FAILED


# ---------------------------------------------------------------------------
# Scoring rule
# ---------------------------------------------------------------------------

class MatrixScoreType(BaseModel):
    """
    Configurable scoring rule stored in the database.

    condition is a Python-evaluable expression (operators and/or → && / ||
    are normalised before eval, mirroring the Scala Groovy evaluation).
    """
    score_matrix_id: str
    id: int
    label: str
    calculate_on: str
    metric_period_seconds: int          # stored as seconds for simplicity
    condition: str
    score_value: int
    is_global: bool = False
    resettable: bool = False
    rg_score_type: Optional[str] = None
    group_id: Optional[int] = None
    jurisdiction: Optional[str] = None
    propagate_globally: bool = False
    triggered_alert_type: Optional[str] = None
    triggered_interaction_type: Optional[str] = None
    bespoke_interaction: Optional[str] = None

    model_config = {"from_attributes": True}


class UserMatrixScore(BaseModel):
    user_id: int
    score_type_id: int
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    comments: str
    score_matrix_id: Optional[str] = None
    last_audit_id: Optional[int] = None
    last_update: Optional[datetime] = None
    active: bool = True
    disabled: bool = False

    model_config = {"from_attributes": True}


class UserMatrixScoreAudit(BaseModel):
    id: int = 0
    user_id: int
    score_type_id: int
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    comments: str
    admin_user_id: int = -1

    model_config = {"from_attributes": True}


class MatrixLevel(BaseModel):
    """
    Risk level definition per matrix.
    Level 1 (Yellow): score  8-19  → Monitor
    Level 2 (Orange): score 20-39  → Interact
    Level 3 (Red):    score 40+    → Intervene
    """
    score_matrix_id: str
    level_number: int
    label: str
    colour: str
    min_score: int
    max_score: int
    rg_message: Optional[str] = None

    model_config = {"from_attributes": True}


class UserMatrixLevel(BaseModel):
    user_id: int
    score_matrix_id: str
    level_number: int
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    level_delta: int
    admin_user_id: Optional[int] = None
    active: bool = True
    last_audit_id: Optional[int] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Kafka message types (mirrors Messages.scala)
# ---------------------------------------------------------------------------

class UserDetails(BaseModel):
    user_id: int
    global_id: int
    dob: Optional[date] = None
    country: str
    currency: str
    jurisdiction: str


class UserEvent(BaseModel):
    user_id: int
    event_type: str
    event_time: datetime
    value: Optional[str] = None


class ScoreTriggerMessage(BaseModel):
    user_details: UserDetails
    user_event: UserEvent
    data: dict[str, Any] = Field(default_factory=dict)


class MatrixScoreDataMessage(BaseModel):
    user_details: UserDetails
    params: str


class UserDailyStats(BaseModel):
    user_id: int
    on_date: datetime
    active_periods: int
    cash_hold: float
    deposit_total: float
    deposit_count: int
    deposit_top50: Optional[bool] = None
    net_losses_top50: Optional[bool] = None
    net_deposits: float
    twilight_periods: int
    longest_active_periods_in_session: int
    longest_daily_session_in_hours: int
    churn: Optional[float] = None
    yearly_losses: float
    slots_stakes_ratio: Optional[float] = None
    table_stakes_ratio: Optional[float] = None
    p2p_stakes_ratio: Optional[float] = None
    low_risk_payments_ratio: Optional[float] = None
    medium_low_risk_payments_ratio: Optional[float] = None
    medium_risk_payments_ratio: Optional[float] = None
    medium_high_risk_payments_ratio: Optional[float] = None
    high_risk_payments_ratio: Optional[float] = None


class AlertMessageParams(BaseModel):
    score_type_id: int
    matrix: str
    comment: str
    label: str


class OpsgenieAlert(BaseModel):
    message: str
    alert_name: str
    alias: Optional[str] = None
    description: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    details: dict[str, str] = Field(default_factory=dict)
    priority: Optional[str] = None
    user_ids: list[str] = Field(default_factory=list)


class AlertDescription(BaseModel):
    alert_name: str
    description: str
    active: bool
    priority: str


class Alert(BaseModel):
    id: str
    alert_name: str
    message: str
    details: str
    user_id: str
    priority: str
    status: str         # NEW | IN_PROGRESS | RESOLVED | DISMISSED
    agent_id: Optional[str] = None
    comment: Optional[str] = None
    updated: datetime
    created: datetime


class PlayerRiskProfile(BaseModel):
    """Aggregated per-player risk profile across all risk matrices."""

    user_id: int
    jurisdiction: str
    scores: dict[str, int] = Field(default_factory=dict)
    levels: dict[str, int] = Field(default_factory=dict)
    active_score_types: list[int] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    total_deposits_30d: int = 0
    deposit_count_30d: int = 0
    declined_deposits_30d: int = 0
    timeout_count_90d: int = 0
    last_calculated: Optional[datetime] = None
