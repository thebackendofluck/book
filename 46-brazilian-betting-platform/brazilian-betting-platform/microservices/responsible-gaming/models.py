# Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Responsible Gaming Service — Pydantic v2 Models
=================================================
Request / response schemas for the Responsible Gaming microservice.

Regulatory references:
  - Lei 14.790/2023 (Brazilian Sports Betting Law)
  - SPA/MF Portaria 1231/2024 (reporting obligations)
  - Portaria 615/2023 (responsible gaming minimum requirements)
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class LimitPeriod(str, enum.Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class LimitType(str, enum.Enum):
    DEPOSIT = "deposit"
    LOSS = "loss"
    SESSION = "session"  # duration in minutes


class SelfExclusionType(str, enum.Enum):
    TEMPORARY = "temporary"
    PERMANENT = "permanent"


class RiskLevel(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AlertType(str, enum.Enum):
    LIMIT_APPROACHING = "limit_approaching"
    LIMIT_REACHED = "limit_reached"
    UNUSUAL_PATTERN = "unusual_pattern"
    LOSS_CHASING = "loss_chasing"
    SESSION_EXTENDED = "session_extended"
    RAPID_DEPOSITS = "rapid_deposits"
    SELF_EXCLUSION_REMINDER = "self_exclusion_reminder"


# ---------------------------------------------------------------------------
# Request Models
# ---------------------------------------------------------------------------


class LimitSetRequest(BaseModel):
    """Payload for POST /limits/{cpf} — set or update a player limit."""

    limit_type: LimitType
    period: LimitPeriod
    amount: float = Field(..., gt=0, description="Amount in BRL or minutes for session limits")
    reason: Optional[str] = Field(None, max_length=500)

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("Limit amount must be positive")
        return round(v, 2)


class SelfExclusionRequest(BaseModel):
    """Payload for POST /self-exclusion/{cpf}."""

    exclusion_type: SelfExclusionType
    duration_days: Optional[int] = Field(
        None,
        ge=1,
        le=1825,  # max 5 years
        description="Duration in days (required for temporary exclusions)",
    )
    reason: Optional[str] = Field(None, max_length=500)
    notify_national_registry: bool = True

    @model_validator(mode="after")
    def validate_temporary_has_duration(self) -> "SelfExclusionRequest":
        if self.exclusion_type == SelfExclusionType.TEMPORARY and not self.duration_days:
            raise ValueError("duration_days is required for temporary self-exclusion")
        if self.exclusion_type == SelfExclusionType.PERMANENT and self.duration_days:
            raise ValueError("duration_days must be omitted for permanent self-exclusion")
        return self


class BehavioralAlertRequest(BaseModel):
    """Payload for POST /alerts/{cpf} — record a behavioral risk signal."""

    alert_type: AlertType
    context: Dict[str, Any] = Field(default_factory=dict)
    triggered_by: str = Field(..., description="System or operator that triggered the alert")
    severity: RiskLevel = RiskLevel.MEDIUM


# ---------------------------------------------------------------------------
# Response / Domain Models
# ---------------------------------------------------------------------------


class PlayerLimit(BaseModel):
    """A single active limit for a player."""

    limit_id: str
    cpf_hash: str
    limit_type: LimitType
    period: LimitPeriod
    amount: float
    amount_used: float = 0.0
    amount_remaining: float
    set_at: datetime
    resets_at: Optional[datetime] = None
    is_active: bool = True


class PlayerLimitsResponse(BaseModel):
    """All current limits for a player."""

    cpf_hash: str
    limits: List[PlayerLimit]
    retrieved_at: datetime


class SelfExclusionRecord(BaseModel):
    """Active self-exclusion record."""

    exclusion_id: str
    cpf_hash: str
    exclusion_type: SelfExclusionType
    started_at: datetime
    ends_at: Optional[datetime] = None  # None for permanent
    is_active: bool = True
    national_registry_notified: bool = False
    cooling_off_until: Optional[datetime] = None


class SelfExclusionResponse(BaseModel):
    record: SelfExclusionRecord
    message: str


class SelfExclusionCheckResponse(BaseModel):
    cpf_hash: str
    is_excluded: bool
    exclusion_details: Optional[SelfExclusionRecord] = None
    checked_at: datetime
    sources: List[str] = Field(default_factory=list)


class BehavioralAlert(BaseModel):
    """A recorded behavioral risk alert."""

    alert_id: str
    cpf_hash: str
    alert_type: AlertType
    severity: RiskLevel
    context: Dict[str, Any]
    triggered_by: str
    created_at: datetime
    acknowledged: bool = False


class RiskScore(BaseModel):
    """Computed behavioral risk score for a player."""

    cpf_hash: str
    overall_score: float = Field(..., ge=0.0, le=1.0)
    risk_level: RiskLevel
    components: Dict[str, float] = Field(default_factory=dict)
    computed_at: datetime
    signals: List[str] = Field(default_factory=list)


class ComplianceReportRow(BaseModel):
    """Single row in a Portaria 1231 daily compliance report."""

    report_date: str
    total_players: int
    active_limits: int
    new_self_exclusions: int
    permanent_exclusions: int
    high_risk_players: int
    alerts_triggered: int
    welfare_blocks: int


class DailyReportResponse(BaseModel):
    """Portaria 1231/2024 daily compliance report."""

    report_date: str
    generated_at: datetime
    data: ComplianceReportRow
    certification: str = "Portaria SPA/MF 1231/2024"
