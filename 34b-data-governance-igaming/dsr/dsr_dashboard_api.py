# Companion code for "The Backend of Luck" - Chapter 34b, Data Governance for iGaming Platforms.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
DSR Dashboard API — FastAPI Router
===================================
Provides REST endpoints for managing Data Subject Requests, triggering exports,
initiating erasures, querying the PII data map, and exposing Prometheus metrics.

Regulatory context:
  - GDPR Art. 12: Transparent information and communication (1-month SLA)
  - GDPR Art. 15: Right of access
  - GDPR Art. 17: Right to erasure
  - GDPR Art. 20: Right to data portability
  - LGPD Art. 18: Data subject rights (15 business days — Brazil)
  - CCPA § 1798.105: Right to deletion (45 days)
"""

from __future__ import annotations

import hmac
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Path as FPath, Query, status
from pydantic import BaseModel, Field
from prometheus_client import Counter, Gauge, Histogram, make_asgi_app

logger = logging.getLogger("dsr_dashboard_api")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# ---------------------------------------------------------------------------
# Service-token auth
# ---------------------------------------------------------------------------
# Every endpoint that mutates a player's data (erasure) or returns another
# player's PII (data export, DSR read/list) must be called by an
# authenticated backoffice service, never anonymously. In production this
# would be a signed JWT validated against the backoffice IdP; the demo here
# uses a shared bearer token compared in constant time, which is enough to
# stop the unauthenticated IDOR class of bug and easy for a reader to swap
# for real auth.
_SERVICE_TOKEN_ENV = "DSR_SERVICE_TOKEN"


def require_service_token(x_service_token: Optional[str] = Header(default=None, alias="X-Service-Token")) -> None:
    """FastAPI dependency: reject the request unless X-Service-Token matches."""
    expected = os.environ.get(_SERVICE_TOKEN_ENV, "")
    if not expected:
        logger.error("%s is not configured; refusing all authenticated DSR requests", _SERVICE_TOKEN_ENV)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="DSR service auth not configured")
    if not x_service_token or not hmac.compare_digest(x_service_token, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid X-Service-Token")

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------
DSR_REQUESTS_TOTAL = Counter(
    "dsr_requests_total",
    "Total DSR requests received, by type and jurisdiction",
    ["request_type", "jurisdiction"],
)
DSR_PROCESSING_SECONDS = Histogram(
    "dsr_processing_seconds",
    "Time taken to process a DSR request end-to-end",
    ["request_type"],
    buckets=[1, 5, 15, 60, 300, 900, 3600],
)
DSR_SLA_BREACH_TOTAL = Counter(
    "dsr_sla_breach_total",
    "DSR requests that exceeded the jurisdictional SLA deadline",
    ["jurisdiction"],
)
DSR_PENDING_GAUGE = Gauge(
    "dsr_pending_gauge",
    "Number of DSR requests currently in PENDING state",
)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class DSRType(str, Enum):
    ACCESS      = "ACCESS"       # GDPR Art. 15
    ERASURE     = "ERASURE"      # GDPR Art. 17
    PORTABILITY = "PORTABILITY"  # GDPR Art. 20
    RECTIFY     = "RECTIFY"      # GDPR Art. 16
    RESTRICT    = "RESTRICT"     # GDPR Art. 18


class DSRStatus(str, Enum):
    PENDING    = "PENDING"
    IN_REVIEW  = "IN_REVIEW"
    COMPLETED  = "COMPLETED"
    REJECTED   = "REJECTED"
    ON_HOLD    = "ON_HOLD"      # AML hold prevents completion


class Jurisdiction(str, Enum):
    GDPR_EU = "GDPR_EU"
    LGPD_BR = "LGPD_BR"
    CCPA_US = "CCPA_US"
    MGA_MT  = "MGA_MT"


class DSRCreateRequest(BaseModel):
    player_id: str = Field(..., description="Platform player identifier")
    request_type: DSRType
    jurisdiction: Jurisdiction
    notes: Optional[str] = Field(None, description="Optional free-text notes from the player")


class DSRResponse(BaseModel):
    dsr_id: str
    player_id: str
    request_type: DSRType
    jurisdiction: Jurisdiction
    status: DSRStatus
    submitted_at: str
    deadline: str
    notes: Optional[str] = None


class RetentionScheduleEntry(BaseModel):
    field: str
    retention_years: int
    legal_basis: str
    authority: str


class DataMapEntry(BaseModel):
    table: str
    column: str
    category: str
    erasability: str
    retention_years: int


class MetricsResponse(BaseModel):
    pending_count: int
    completed_today: int
    sla_breach_count: int


# ---------------------------------------------------------------------------
# In-memory store (replace with PostgreSQL in production)
# ---------------------------------------------------------------------------
_DSR_STORE: dict[str, dict[str, Any]] = {}

# SLA in calendar days per jurisdiction
# GDPR Art. 12(3): 1 month; LGPD Art. 18: 15 business days ≈ 21 calendar days;
# CCPA § 1798.105(b): 45 days; MGA: aligned with GDPR.
SLA_DAYS: dict[Jurisdiction, int] = {
    Jurisdiction.GDPR_EU: 30,
    Jurisdiction.LGPD_BR: 21,
    Jurisdiction.CCPA_US: 45,
    Jurisdiction.MGA_MT:  30,
}

RETENTION_SCHEDULE: list[RetentionScheduleEntry] = [
    RetentionScheduleEntry(field="email",             retention_years=3,  legal_basis="GDPR Art. 5(1)(e)",       authority="GDPR"),
    RetentionScheduleEntry(field="full_name",         retention_years=3,  legal_basis="GDPR Art. 5(1)(e)",       authority="GDPR"),
    RetentionScheduleEntry(field="date_of_birth",     retention_years=5,  legal_basis="AMLD Art. 40",            authority="FATF"),
    RetentionScheduleEntry(field="national_id",       retention_years=5,  legal_basis="AMLD Art. 40",            authority="FATF"),
    RetentionScheduleEntry(field="iban",              retention_years=7,  legal_basis="GDPR Art. 17(3)(b)",      authority="HMRC"),
    RetentionScheduleEntry(field="amount_eur",        retention_years=7,  legal_basis="GDPR Art. 17(3)(b)",      authority="HMRC"),
    RetentionScheduleEntry(field="self_exclusion_reason", retention_years=5, legal_basis="MGA LC 2021 Art. 11",  authority="MGA"),
    RetentionScheduleEntry(field="face_image_hash",   retention_years=5,  legal_basis="MGA LC 2021 Art. 11",    authority="MGA"),
    RetentionScheduleEntry(field="bet_pattern_vector",retention_years=2,  legal_basis="MGA LC 2021 — RG",       authority="MGA"),
]

DATA_MAP: list[DataMapEntry] = [
    DataMapEntry(table="players",           column="email",               category="Direct PII",   erasability="PSEUDONYMIZABLE", retention_years=3),
    DataMapEntry(table="players",           column="full_name",           category="Direct PII",   erasability="PSEUDONYMIZABLE", retention_years=3),
    DataMapEntry(table="players",           column="date_of_birth",       category="Direct PII",   erasability="RETAINED",        retention_years=5),
    DataMapEntry(table="players",           column="national_id",         category="Direct PII",   erasability="RETAINED",        retention_years=5),
    DataMapEntry(table="kyc_documents",     column="face_image_hash",     category="Biometric",    erasability="RETAINED",        retention_years=5),
    DataMapEntry(table="payment_methods",   column="iban",                category="Financial",    erasability="RETAINED",        retention_years=7),
    DataMapEntry(table="responsible_gaming",column="self_exclusion_reason",category="Sensitive PII",erasability="RETAINED",       retention_years=5),
]


# ---------------------------------------------------------------------------
# Router definition
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/dsr", tags=["DSR"])


@router.post("", response_model=DSRResponse, status_code=201)
def submit_dsr(req: DSRCreateRequest) -> DSRResponse:
    """Submit a new Data Subject Request. GDPR Art. 12 mandates acknowledgement."""
    start = time.time()
    DSR_REQUESTS_TOTAL.labels(
        request_type=req.request_type.value,
        jurisdiction=req.jurisdiction.value,
    ).inc()
    DSR_PENDING_GAUGE.inc()

    dsr_id = str(uuid.uuid4())
    submitted_at = datetime.now(timezone.utc)
    sla_days = SLA_DAYS.get(req.jurisdiction, 30)
    from datetime import timedelta
    deadline = submitted_at + timedelta(days=sla_days)

    record: dict[str, Any] = {
        "dsr_id":        dsr_id,
        "player_id":     req.player_id,
        "request_type":  req.request_type,
        "jurisdiction":  req.jurisdiction,
        "status":        DSRStatus.PENDING,
        "submitted_at":  submitted_at.isoformat() + "Z",
        "deadline":      deadline.isoformat() + "Z",
        "notes":         req.notes,
    }
    _DSR_STORE[dsr_id] = record
    logger.info("DSR %s created for player %s (%s)", dsr_id, req.player_id, req.request_type.value)

    DSR_PROCESSING_SECONDS.labels(request_type=req.request_type.value).observe(time.time() - start)
    return DSRResponse(**record)


@router.get("", response_model=list[DSRResponse], dependencies=[Depends(require_service_token)])
def list_dsrs(
    status: Optional[DSRStatus] = Query(None, description="Filter by status"),
    jurisdiction: Optional[Jurisdiction] = Query(None),
) -> list[DSRResponse]:
    """List all DSR requests, optionally filtered by status and jurisdiction."""
    results = list(_DSR_STORE.values())
    if status is not None:
        results = [r for r in results if r["status"] == status]
    if jurisdiction is not None:
        results = [r for r in results if r["jurisdiction"] == jurisdiction]
    return [DSRResponse(**r) for r in results]


@router.get("/{dsr_id}", response_model=DSRResponse, dependencies=[Depends(require_service_token)])
def get_dsr(dsr_id: str = FPath(..., description="DSR request UUID")) -> DSRResponse:
    """Retrieve a specific DSR by its ID."""
    record = _DSR_STORE.get(dsr_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"DSR {dsr_id} not found")
    return DSRResponse(**record)


@router.get("/data-export/{player_id}", response_model=dict[str, str], dependencies=[Depends(require_service_token)])
def trigger_data_export(player_id: str) -> dict[str, str]:
    """Trigger GDPR Art. 20 portable export generation. Returns a job reference."""
    job_id = str(uuid.uuid4())
    logger.info("Export job %s triggered for player %s", job_id, player_id)
    # Production: enqueue Celery/Kafka task; return job_id for polling.
    return {"job_id": job_id, "status": "queued", "player_id": player_id}


@router.delete("/player/{player_id}", response_model=dict[str, str], dependencies=[Depends(require_service_token)])
def initiate_erasure(player_id: str) -> dict[str, str]:
    """
    Initiate GDPR Art. 17 erasure for a player.
    Delegates to dsr_decision_engine to determine per-field actions.
    """
    job_id = str(uuid.uuid4())
    logger.info("Erasure job %s initiated for player %s", job_id, player_id)
    return {"job_id": job_id, "status": "queued", "player_id": player_id,
            "note": "Per-field decisions computed by dsr_decision_engine"}


@router.get("/data-map", response_model=list[DataMapEntry], tags=["DSR"])
def get_data_map() -> list[DataMapEntry]:
    """Return the platform PII data map (GDPR Art. 30 RoPA summary)."""
    return DATA_MAP


@router.get("/retention-schedule", response_model=list[RetentionScheduleEntry], tags=["DSR"])
def get_retention_schedule() -> list[RetentionScheduleEntry]:
    """Return the full retention schedule with legal citations."""
    return RETENTION_SCHEDULE


@router.get("/metrics", response_model=MetricsResponse, tags=["DSR"])
def get_dsr_metrics() -> MetricsResponse:
    """Return DSR operational metrics (human-readable summary)."""
    today = datetime.now(timezone.utc).date().isoformat()
    pending   = sum(1 for r in _DSR_STORE.values() if r["status"] == DSRStatus.PENDING)
    completed = sum(
        1 for r in _DSR_STORE.values()
        if r["status"] == DSRStatus.COMPLETED and r["submitted_at"][:10] == today
    )
    return MetricsResponse(pending_count=pending, completed_today=completed, sla_breach_count=0)


# ---------------------------------------------------------------------------
# App assembly
# ---------------------------------------------------------------------------
def create_app() -> FastAPI:
    app = FastAPI(
        title="DSR Dashboard API",
        description="iGaming Platform Data Subject Rights management endpoints",
        version="1.0.0",
    )
    app.include_router(router)
    # Expose Prometheus metrics at /metrics
    metrics_app = make_asgi_app()
    app.mount("/metrics", metrics_app)
    return app


app = create_app()

if __name__ == "__main__":
    import argparse
    import uvicorn  # type: ignore[import-untyped]

    parser = argparse.ArgumentParser(description="Run the DSR Dashboard API server.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--reload", action="store_true")
    cli_args = parser.parse_args()

    uvicorn.run("dsr_dashboard_api:app", host=cli_args.host, port=cli_args.port, reload=cli_args.reload)
