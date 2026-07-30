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
AcmeToCasino Fraud Detection API — Pydantic Domain Models

Defines the shared data contracts used across the fraud detection pipeline:
event ingestion, risk scoring, alert dispatch, rule evaluation, and the
player risk profile surfaced in the compliance dashboard.

Compliance references:
  - AMLD6 (EU 2018/1673): 6th Anti-Money Laundering Directive — risk-based approach
    requires structured typology of predicate offences; `FraudTypology` enum maps
    directly to AMLD6 Article 2 predicate offence categories.
  - FATF Recommendation 10: Customer due diligence; risk scores feed CDD decision.
  - FATF Recommendation 16: Wire transfer data — `correlation_id` / `trace_id` fields
    ensure end-to-end traceability of funds across the platform.
  - PCI DSS Requirement 10: Logging and monitoring — every model includes `created_at`
    and `correlation_id` to satisfy audit-trail requirements.
  - MGA Player Protection Directive 2023: `jurisdiction` field drives per-jurisdiction
    rule thresholds; risk profiles are segregated by licensing jurisdiction.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class FraudTypology(str, Enum):
    """
    Fraud typology categories aligned with FATF/AMLD6 predicate offence taxonomy.

    Keeping typologies in an enum (not free text) ensures regulatory reports
    produced downstream can reference standardised codes without a translation
    layer — the FIAU SAR schema expects exactly this level of categorisation.
    """
    BOT_ACTIVITY = "bot_activity"
    ACCOUNT_TAKEOVER = "account_takeover"
    MONEY_LAUNDERING = "money_laundering"
    STRUCTURING = "structuring"           # 'smurfing' — FATF typology
    BONUS_ABUSE = "bonus_abuse"
    CARD_TESTING = "card_testing"
    COLLUSION = "collusion"
    GEO_ANOMALY = "geo_anomaly"
    DEVICE_SHARING = "device_sharing"     # multi-accounting via shared device
    VELOCITY_ANOMALY = "velocity_anomaly"
    AMOUNT_ANOMALY = "amount_anomaly"
    UNKNOWN = "unknown"


class RiskLevel(str, Enum):
    """
    Four-tier risk hierarchy matching the alert decision engine in Chapter 19.

    Score → Level mapping:
        >= 0.90  →  CRITICAL  (automated account freeze)
        0.70–0.89 → HIGH      (human analyst review case)
        0.50–0.69 → MEDIUM    (enhanced monitoring)
        < 0.50   →  LOW       (logged, fed to learning pipeline)
    """
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class AlertStatus(str, Enum):
    """Lifecycle state of a fraud alert."""
    OPEN = "open"
    UNDER_REVIEW = "under_review"
    ESCALATED = "escalated"
    RESOLVED_TRUE_POSITIVE = "resolved_tp"
    RESOLVED_FALSE_POSITIVE = "resolved_fp"
    AUTO_CLOSED = "auto_closed"


class RuleStatus(str, Enum):
    """Operational state of a detection rule."""
    ACTIVE = "active"
    SHADOW = "shadow"       # fires and logs but does not score
    DISABLED = "disabled"
    TESTING = "testing"     # A/B test group only


class Jurisdiction(str, Enum):
    """
    Supported licensing jurisdictions.  Each jurisdiction may carry different
    velocity thresholds, AML reporting obligations, and player protection rules.
    """
    MGA = "MGA"         # Malta Gaming Authority
    UKGC = "UKGC"       # UK Gambling Commission
    SGA = "SGA"         # Swedish Gambling Authority
    DGE = "DGE"         # New Jersey Division of Gaming Enforcement
    IGCB = "IGCB"       # Pennsylvania iGaming Control Board
    ARJEL = "ARJEL"     # Autorité Nationale des Jeux (France)
    UNKNOWN = "UNKNOWN"


# ---------------------------------------------------------------------------
# Shared base
# ---------------------------------------------------------------------------

class BaseEvent(BaseModel):
    """
    Common header carried by every event entering the fraud pipeline.

    `correlation_id` ties events across services — the same UUID that the
    wallet service stamped on the original deposit appears on every downstream
    fraud event, satisfying FATF Recommendation 16 end-to-end traceability.
    """
    event_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique event identifier (UUIDv4).",
    )
    correlation_id: str = Field(
        ...,
        description=(
            "Trace identifier propagated from the originating wallet / game event. "
            "Used for cross-service log correlation (FATF R.16, PCI DSS Req. 10.2)."
        ),
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of event creation.",
    )

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Fraud Event
# ---------------------------------------------------------------------------

class FraudEvent(BaseEvent):
    """
    Represents a single transaction or behavioural signal that has been
    processed by the fraud rules engine.

    Indexed in Elasticsearch under the `fraud-events-YYYY.MM.dd` daily index
    pattern.  The `jurisdiction` field is mapped as a keyword so Kibana
    dashboards can filter per-regulatory-scope (MGA analysts should not see
    UKGC alerts and vice versa — data residency requirements).
    """
    player_id: str = Field(..., description="Platform player identifier.")
    brand_id: int = Field(
        ...,
        description=(
            "Multi-brand operator brand discriminator. "
            "Bonus abuse and multi-accounting rules are scoped per brand."
        ),
    )
    jurisdiction: Jurisdiction = Field(
        default=Jurisdiction.UNKNOWN,
        description="Licensing jurisdiction governing this event.",
    )

    # Transaction context
    transaction_type: str = Field(
        ...,
        description="deposit | withdrawal | bet | win | refund",
    )
    amount: float = Field(..., gt=0, description="Transaction amount in minor units (cents).")
    currency: str = Field(..., min_length=3, max_length=3, description="ISO 4217 currency code.")
    payment_method: Optional[str] = Field(None, description="card | ewallet | crypto | bank")
    deposit_number: Optional[int] = Field(
        None,
        description=(
            "Ordinal deposit count for this player. "
            "First-deposit fraud patterns differ significantly from recurring depositors."
        ),
    )

    # Session / device context
    ip_address: Optional[str] = Field(None, description="Player's IP address at event time.")
    country_code: Optional[str] = Field(None, description="ISO 3166-1 alpha-2 country code.")
    device_fingerprint: Optional[str] = Field(None, description="Hashed device fingerprint.")
    user_agent: Optional[str] = Field(None)
    game_session_id: Optional[str] = Field(None)

    # Scoring output
    risk_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Ensemble fraud risk score (0.0 = benign, 1.0 = confirmed fraud).",
    )
    risk_level: RiskLevel
    typologies: List[FraudTypology] = Field(
        default_factory=list,
        description="Fraud typologies matched by the rules engine.",
    )
    rule_hits: List[str] = Field(
        default_factory=list,
        description="Rule IDs that contributed to the risk score.",
    )
    model_scores: Dict[str, float] = Field(
        default_factory=dict,
        description=(
            "Per-model component scores before ensemble weighting. "
            "Keys: xgboost, isolation_forest, lstm, rules_engine."
        ),
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("currency")
    @classmethod
    def currency_uppercase(cls, v: str) -> str:
        return v.upper()

    @field_validator("risk_level", mode="before")
    @classmethod
    def derive_risk_level(cls, v: Any, info: Any) -> Any:
        """Allow callers to omit risk_level; derive from risk_score if absent."""
        if v is not None:
            return v
        score = (info.data or {}).get("risk_score", 0.0)
        if score >= 0.90:
            return RiskLevel.CRITICAL
        if score >= 0.70:
            return RiskLevel.HIGH
        if score >= 0.50:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW


# ---------------------------------------------------------------------------
# Fraud Alert
# ---------------------------------------------------------------------------

class FraudAlert(BaseEvent):
    """
    A persisted alert raised when a fraud event breaches an investigation
    threshold (risk_score >= 0.50).

    Indexed in Elasticsearch under `fraud-alerts-YYYY.MM.dd`.  Kibana
    watches on this index drive real-time analyst notifications.

    PCI DSS Req. 10.6: Alerts must be reviewed at least daily; the `status`
    field lifecycle tracks compliance with this requirement.
    """
    alert_id: str = Field(
        default_factory=lambda: f"ALT-{uuid.uuid4().hex[:12].upper()}",
        description="Human-readable alert reference (e.g. ALT-3F2A1B9C0D4E).",
    )
    fraud_event_id: str = Field(..., description="FK to the originating FraudEvent.event_id.")
    player_id: str
    brand_id: int
    jurisdiction: Jurisdiction

    risk_score: float = Field(..., ge=0.0, le=1.0)
    risk_level: RiskLevel
    typologies: List[FraudTypology] = Field(default_factory=list)
    summary: str = Field(
        ...,
        description="Human-readable alert summary for analyst review queue.",
    )

    # Case management
    status: AlertStatus = Field(default=AlertStatus.OPEN)
    assigned_to: Optional[str] = Field(None, description="Analyst user ID.")
    resolved_at: Optional[datetime] = None
    resolution_notes: Optional[str] = None

    # Automated response taken
    automated_action: Optional[str] = Field(
        None,
        description=(
            "Action taken automatically: account_freeze | transaction_block | "
            "enhanced_monitoring | none"
        ),
    )
    aml_report_required: bool = Field(
        default=False,
        description=(
            "Set to True when the alert triggers AML reporting obligations "
            "(AMLD6, FATF R.20 suspicious transaction reports)."
        ),
    )


# ---------------------------------------------------------------------------
# Risk Score (real-time API response)
# ---------------------------------------------------------------------------

class RiskScore(BaseModel):
    """
    Response model for POST /fraud/analyze.

    Combines the ensemble model output with rule-engine signals into a
    single scored response that the caller (wallet service, game launcher)
    can act on synchronously within the request path.
    """
    correlation_id: str
    player_id: str
    transaction_id: Optional[str] = None

    risk_score: float = Field(..., ge=0.0, le=1.0)
    risk_level: RiskLevel
    typologies: List[FraudTypology] = Field(default_factory=list)
    rule_hits: List[str] = Field(default_factory=list)

    # Recommended action for the caller
    recommended_action: str = Field(
        ...,
        description=(
            "allow | block | hold_for_review | require_2fa | require_kyc_step_up"
        ),
    )
    block_reason: Optional[str] = Field(
        None,
        description="Machine-readable block reason code (mirrors geo-block audit log pattern).",
    )

    # Explainability — required for UKGC/MGA 'explain the decision' obligations
    feature_importances: Dict[str, float] = Field(
        default_factory=dict,
        description=(
            "Top feature contributions to the risk score. "
            "Enables human-readable explanation of automated decisions."
        ),
    )
    processing_time_ms: float = Field(
        ...,
        description="End-to-end scoring latency (target: < 50ms per Chapter 19 SLA).",
    )
    scored_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Detection Rule
# ---------------------------------------------------------------------------

class DetectionRule(BaseModel):
    """
    Represents a fraud detection rule stored in the rules registry.

    Rules are the explainable layer on top of ML models.  They encode domain
    knowledge that analysts and compliance officers can audit without a data
    science background — critical for UKGC and MGA licence conditions that
    require explainability of automated decisions affecting players.
    """
    rule_id: str = Field(..., description="e.g. RULE-VEL-001")
    name: str
    description: str
    typology: FraudTypology
    status: RuleStatus = RuleStatus.ACTIVE
    jurisdiction_scope: List[Jurisdiction] = Field(
        default_factory=list,
        description=(
            "Empty list means rule applies to all jurisdictions. "
            "Non-empty list restricts the rule to the listed jurisdictions."
        ),
    )

    # Rule parameters (threshold values)
    parameters: Dict[str, Any] = Field(
        default_factory=dict,
        description="Threshold values, time-window sizes, and other rule-specific parameters.",
    )

    # Scoring contribution
    base_score_contribution: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Score delta added to the ensemble score when this rule fires.",
    )

    # Operational metadata
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_by: str = Field(..., description="Analyst ID who authored the rule.")
    hit_count_24h: int = Field(default=0, description="Cached hit count (last 24 hours).")
    false_positive_rate: Optional[float] = Field(
        None,
        description="Measured FP rate from analyst feedback; None if insufficient data.",
    )


# ---------------------------------------------------------------------------
# Player Risk Profile
# ---------------------------------------------------------------------------

class PlayerRiskProfile(BaseModel):
    """
    Aggregated risk view for a single player, used by the compliance dashboard
    and KYC/AML case management system.

    Designed to satisfy:
    - AMLD6 Article 18: risk-based approach to customer due diligence
    - FATF R.10: ongoing customer due diligence and transaction monitoring
    - MGA Player Protection Directive 2023: responsible gambling risk indicators
    """
    player_id: str
    brand_id: int
    jurisdiction: Jurisdiction

    # Current risk standing
    current_risk_score: float = Field(..., ge=0.0, le=1.0)
    current_risk_level: RiskLevel
    risk_score_7d_trend: float = Field(
        ...,
        description="Delta from 7-day average risk score (positive = increasing risk).",
    )

    # KYC / AML status
    kyc_verified: bool = Field(default=False)
    kyc_tier: int = Field(
        default=0,
        description="KYC tier: 0=unverified, 1=basic ID, 2=full EDD, 3=enhanced PEP check",
    )
    is_pep: bool = Field(default=False, description="Politically Exposed Person flag.")
    is_sanctioned: bool = Field(
        default=False,
        description="Matched against OFAC / EU / UN sanctions lists.",
    )
    aml_risk_category: str = Field(
        default="standard",
        description="low | standard | high | very_high (AMLD6 risk-based approach)",
    )

    # Velocity metrics (24h / 7d / 30d)
    deposit_count_24h: int = 0
    deposit_amount_24h: float = 0.0
    deposit_count_7d: int = 0
    deposit_amount_7d: float = 0.0
    deposit_count_30d: int = 0
    deposit_amount_30d: float = 0.0

    # Fraud history
    open_alert_count: int = 0
    total_alert_count: int = 0
    true_positive_count: int = 0
    last_alert_at: Optional[datetime] = None

    # Geo / device signals
    known_countries: List[str] = Field(default_factory=list)
    known_device_fingerprints: List[str] = Field(default_factory=list)
    geo_anomaly_count_30d: int = 0
    device_anomaly_count_30d: int = 0

    # Account status
    account_frozen: bool = False
    requires_enhanced_monitoring: bool = False
    next_review_due: Optional[datetime] = Field(
        None,
        description=(
            "Scheduled compliance review date. "
            "MGA requires periodic review proportional to AML risk category."
        ),
    )

    # Meta
    profile_updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    data_sources: List[str] = Field(
        default_factory=list,
        description="Data sources used to build this profile (audit trail).",
    )


# ---------------------------------------------------------------------------
# Request / Response models for API endpoints
# ---------------------------------------------------------------------------

class AnalyzeTransactionRequest(BaseModel):
    """Request body for POST /fraud/analyze."""
    correlation_id: str = Field(
        ...,
        description="Propagated from the calling service (wallet, game, payments).",
    )
    player_id: str
    brand_id: int
    jurisdiction: Jurisdiction = Jurisdiction.UNKNOWN
    transaction_type: str
    amount: float = Field(..., gt=0)
    currency: str = Field(..., min_length=3, max_length=3)
    payment_method: Optional[str] = None
    deposit_number: Optional[int] = None
    ip_address: Optional[str] = None
    country_code: Optional[str] = None
    device_fingerprint: Optional[str] = None
    user_agent: Optional[str] = None
    game_session_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class FraudEventsResponse(BaseModel):
    """Paginated response for GET /fraud/events."""
    total: int
    page: int
    page_size: int
    events: List[FraudEvent]


class FraudAlertsResponse(BaseModel):
    """Paginated response for GET /fraud/alerts."""
    total: int
    page: int
    page_size: int
    open_count: int
    critical_count: int
    alerts: List[FraudAlert]


class SystemStatusResponse(BaseModel):
    """Response for GET /fraud/status."""
    status: str                    # healthy | degraded | unhealthy
    version: str
    uptime_seconds: float
    elasticsearch_connected: bool
    redis_connected: bool
    kafka_consumer_lag: Optional[int] = None
    events_indexed_24h: int = 0
    alerts_generated_24h: int = 0
    avg_scoring_latency_ms: float = 0.0
    rules_active: int = 0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
