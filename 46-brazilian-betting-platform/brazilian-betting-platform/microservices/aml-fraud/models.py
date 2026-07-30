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
AML/Fraud Detection Service — Pydantic v2 Models
=================================================
Domain models for the AML/Fraud microservice covering:
  - Transaction analysis
  - Risk scoring
  - COAF SAR reporting (Lei 9.613/1998)
  - Neo4j graph relationships
  - PIX fraud detection results
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Enumerations ──────────────────────────────────────────────────────────────


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class TransactionType(str, Enum):
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    BET = "BET"
    PIX = "PIX"
    BONUS = "BONUS"


class ReportUrgency(str, Enum):
    NORMAL = "NORMAL"
    HIGH = "HIGH"


class ReportStatus(str, Enum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    REJECTED = "REJECTED"


class PIXPattern(str, Enum):
    VELOCITY = "VELOCITY"
    SMURFING = "SMURFING"
    MULE_ACCOUNT = "MULE_ACCOUNT"
    ROUND_TRIP = "ROUND_TRIP"
    OFF_HOURS = "OFF_HOURS"
    DEVICE_ANOMALY = "DEVICE_ANOMALY"


# ── Core domain models ────────────────────────────────────────────────────────


class Transaction(BaseModel):
    """A single financial transaction to be analysed."""

    transaction_id: str = Field(..., description="Unique transaction identifier")
    cpf: str = Field(..., pattern=r"^\d{11}$", description="11-digit CPF (digits only)")
    amount: Decimal = Field(..., gt=0, description="Transaction amount in BRL")
    transaction_type: TransactionType
    pix_key: Optional[str] = Field(None, description="PIX key when type is PIX")
    counterparty_cpf: Optional[str] = Field(
        None, pattern=r"^\d{11}$", description="Counterparty CPF when known"
    )
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, str] = Field(default_factory=dict)

    model_config = {"json_encoders": {Decimal: str}}


class TransactionAnalysis(BaseModel):
    """Request body for POST /aml/analyze/{cpf}."""

    transaction: Transaction
    include_graph_analysis: bool = Field(
        False, description="Record transaction edge in Neo4j graph"
    )


class AnalysisResult(BaseModel):
    """Response for POST /aml/analyze/{cpf}."""

    transaction_id: str
    cpf: str
    risk_score: float = Field(..., ge=0.0, le=1.0)
    risk_level: RiskLevel
    flags: list[str]
    blocked: bool
    requires_review: bool
    timestamp: str


# ── Risk score model ──────────────────────────────────────────────────────────


class RiskScore(BaseModel):
    """Aggregated CPF risk profile — response for GET /aml/risk/{cpf}."""

    cpf: str = Field(..., pattern=r"^\d{11}$")
    score: float = Field(..., ge=0.0, le=1.0, description="Composite risk score 0–1")
    level: RiskLevel
    flags: list[str] = Field(default_factory=list)
    last_updated: str
    transaction_count: int = Field(..., ge=0)
    total_volume: Decimal = Field(..., ge=0)
    account_age_days: int = Field(..., ge=0)

    model_config = {"json_encoders": {Decimal: str}}


# ── COAF reporting models ─────────────────────────────────────────────────────


class COAFReportRequest(BaseModel):
    """Request body for POST /aml/report/coaf."""

    cpf: str = Field(..., pattern=r"^\d{11}$")
    report_reason: str = Field(
        ...,
        description="e.g. STRUCTURING, LAYERING, MULE_ACCOUNT, SMURFING",
    )
    transactions: list[str] = Field(..., min_length=1, description="Transaction IDs")
    evidence_summary: str = Field(..., min_length=10)
    urgency: ReportUrgency = ReportUrgency.NORMAL

    @field_validator("report_reason")
    @classmethod
    def validate_reason(cls, v: str) -> str:
        valid = {
            "STRUCTURING",
            "LAYERING",
            "MULE_ACCOUNT",
            "SMURFING",
            "ROUND_TRIP",
            "UNUSUAL_PATTERN",
            "HIGH_RISK_COUNTRY",
        }
        if v.upper() not in valid:
            raise ValueError(f"report_reason must be one of {sorted(valid)}")
        return v.upper()


class COAFReport(BaseModel):
    """Generated COAF Suspicious Activity Report — response for POST /aml/report/coaf."""

    report_id: str
    cpf: str
    report_reason: str
    transactions: list[str]
    evidence_summary: str
    status: ReportStatus
    submitted_at: str
    coaf_protocol: Optional[str] = None


# ── Graph relationship models ─────────────────────────────────────────────────


class GraphNode(BaseModel):
    """A node in the CPF relationship graph."""

    node_id: str
    label: str = Field(..., description="CPF | ACCOUNT | DEVICE | IP")
    properties: dict[str, str] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    """A directed edge in the CPF relationship graph."""

    source: str
    target: str
    relation: str = Field(
        ..., description="TRANSACTED_WITH | SHARES_DEVICE | SHARES_IP"
    )
    weight: float = Field(..., ge=0.0)
    properties: dict[str, str] = Field(default_factory=dict)


class GraphRelationship(BaseModel):
    """Full 2-hop relationship graph for a CPF — response for GET /aml/graph/{cpf}."""

    cpf: str
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    cluster_risk_score: float = Field(..., ge=0.0, le=1.0)
    is_mule_network: bool
    generated_at: str


# ── PIX fraud detection models ────────────────────────────────────────────────


class PIXFraudCheckRequest(BaseModel):
    """Request body for POST /aml/pix/fraud-check."""

    pix_key: str = Field(..., description="PIX key (CPF, CNPJ, phone, email, EVP)")
    sender_cpf: str = Field(..., pattern=r"^\d{11}$")
    receiver_cpf: str = Field(..., pattern=r"^\d{11}$")
    amount: Decimal = Field(..., gt=0)
    transaction_time: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    device_fingerprint: Optional[str] = None

    model_config = {"json_encoders": {Decimal: str}}


class PIXFraudResult(BaseModel):
    """Response for POST /aml/pix/fraud-check."""

    pix_key: str
    fraud_probability: float = Field(..., ge=0.0, le=1.0)
    patterns: list[PIXPattern]
    blocked: bool
    review_required: bool
    timestamp: str


# ── ML scoring models ─────────────────────────────────────────────────────────


class TransactionFeatures(BaseModel):
    """Feature vector for the ML fraud scorer."""

    transaction_id: str
    cpf: str
    amount: float = Field(..., gt=0)
    transaction_type: str
    hour_of_day: int = Field(..., ge=0, le=23)
    day_of_week: int = Field(..., ge=0, le=6)
    transaction_count_24h: int = Field(default=0, ge=0)
    total_volume_24h: float = Field(default=0.0, ge=0)
    account_age_days: int = Field(default=0, ge=0)
    is_new_device: bool = False
    is_new_pix_key: bool = False
    counterparty_risk_score: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("transaction_type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        allowed = {"DEPOSIT", "WITHDRAWAL", "BET", "PIX", "BONUS"}
        if v.upper() not in allowed:
            raise ValueError(f"transaction_type must be one of {allowed}")
        return v.upper()


class ScoringResult(BaseModel):
    """ML model scoring output."""

    transaction_id: str
    cpf: str
    fraud_probability: float
    risk_level: str
    feature_importance: dict[str, float]
    model_version: str
    scored_at: str
    latency_ms: float


class BatchScoringRequest(BaseModel):
    """Batch ML scoring request."""

    transactions: list[TransactionFeatures] = Field(..., min_length=1, max_length=500)


class BatchScoringResult(BaseModel):
    """Batch ML scoring response."""

    results: list[ScoringResult]
    total: int
    high_risk_count: int
    scored_at: str


# ── Health check ──────────────────────────────────────────────────────────────


class HealthStatus(BaseModel):
    """GET /health response."""

    status: str
    service: str
    version: str
    neo4j_up: bool
    model_loaded: bool
    timestamp: str
