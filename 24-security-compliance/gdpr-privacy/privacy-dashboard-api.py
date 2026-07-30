#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# REGULATORY REQUIREMENT: GDPR + UK GDPR + LGPD + CCPA + PIPEDA + PDPA + PIPA
# Regulation:  GDPR (EU) 2016/679:
#                Art. 15 — Right of Access (30-day response)
#                Art. 17 — Right to Erasure (30-day response; AML exception applies)
#                Art. 20 — Right to Portability (machine-readable format)
#                Art. 5(2) — Accountability (audit log required)
#                Art. 7(1) — Consent evidence (marketing consent retained indefinitely)
#              UK GDPR + Data Protection Act 2018 (identical obligations post-Brexit)
#              LGPD (Brazil) Lei No. 13.709/2018 Art. 18 (15-day response deadline)
#              CCPA/CPRA Cal. Civ. Code §§1798.100-1798.199 (45-day response)
#              PIPEDA S.C. 2000 c.5 Principle 9 (Canada — 30-day response)
#              PDPA 2012 (Singapore) Section 22; PDPA B.E. 2562 (Thailand) §33
#              PIPA (Korea) Art. 36 (10-day response — strictest deadline)
# Purpose:     FastAPI service providing Data Subject Request (DSR) management:
#              submit, track, export, delete, and audit privacy rights requests.
#              This is the DPO's operational dashboard for regulatory compliance.
# Deadlines:   EU/UK GDPR: 30 days (extendable to 90 for complex)
#              LGPD (Brazil): 15 days (strictly enforced by ANPD)
#              CCPA: 45 days (extendable to 90 with written notice)
#              PIPEDA: 30 days
#              PIPA (Korea): 10 days (tightest — monitor for Korean players)
# Legal Hold:  Financial records retained 5 years (AML — EU 4AMLD Art. 40;
#              5AMLD; note: 6AMLD enters into force 10 July 2027);
#              Self-exclusion records retained indefinitely (UKGC RTS 14;
#              MGA PPD §8) — these are NOT erasable even on GDPR Art. 17 requests.
#              Marketing consent audit trail retained indefinitely (Art. 7(1))
# Penalty:     GDPR Art. 83(5): up to €20M or 4% of global annual turnover
#              UK GDPR: equivalent GBP fines
#              LGPD: up to 2% of Brazilian revenue, max R$50M per infraction
#              CCPA: $7,500 per intentional violation
#              PIPA (Korea): up to 3% of relevant revenues
# Jurisdictions: MGA (Malta), UKGC (UK), GGL (Germany), KSA (Netherlands),
#              Spelinspektionen (Sweden), AGCO (Ontario), SPA/MF (Brazil),
#              MAS (Singapore), OIC (Thailand), PIPC (Korea)
#
# References:
#   GDPR Full Text: https://gdpr-info.eu/
#   Art. 15 (Right of Access): https://gdpr-info.eu/art-15-gdpr/
#   Art. 17 (Right to Erasure): https://gdpr-info.eu/art-17-gdpr/
#   Art. 20 (Data Portability): https://gdpr-info.eu/art-20-gdpr/
#   Art. 83 (Penalties): https://gdpr-info.eu/art-83-gdpr/
#   UK GDPR: https://www.legislation.gov.uk/uksi/2019/419/contents
#   MGA Player Protection Directive: https://www.mga.org.mt/legislation/subsidiary-legislation/
#   MGA Gaming Act: https://www.mga.org.mt/legislation/gaming-act/
#   UKGC LCCP: https://www.gamblingcommission.gov.uk/licensees-and-businesses/lccp
#   Spellagen (2018:1138): https://www.riksdagen.se/sv/dokument-och-lagar/dokument/svensk-forfattningssamling/spellag-20181138_sfs-2018-1138/
#   KSA (Kansspelautoriteit): https://kansspelautoriteit.nl/
#   AGCO iGO Standards: https://www.agco.ca/internet-gaming/standards-and-resources
#   iGaming Ontario: https://igamingontario.ca/en/operators
#   LGPD: https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/L13709.htm
#   5AMLD: https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32018L0843
# =============================================================================
"""
Privacy Management Dashboard API for iGaming Platforms.

FastAPI service implementing the privacy management endpoints for the
AcmetoCasino platform. Provides a RESTful interface for:

- Submitting Data Subject Requests (GDPR Art. 17, CCPA, LGPD, PDPA, PIPA, PIPEDA)
- Tracking DSR processing status
- Exporting player data in GDPR-portable JSON format (Art. 20)
- Triggering player data deletion with audit trail
- Compliance dashboards for DPO and regulatory reporting

This service sits behind the internal API gateway with mTLS authentication.
External DSR submissions from the player-facing portal go through a separate
verification layer before reaching these endpoints.

Usage:
    uvicorn privacy-dashboard-api:app --host 0.0.0.0 --port 8080 --reload

Health check:
    curl http://localhost:8080/health

Submit a deletion request:
    curl -X POST http://localhost:8080/api/v1/privacy/dsr \
         -H "Content-Type: application/json" \
         -d '{"player_id": "P123456", "request_type": "deletion", "jurisdiction": "EU"}'
"""

import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger("privacy_api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ---------------------------------------------------------------------------
# Application bootstrap
# ---------------------------------------------------------------------------

app = FastAPI(
    title="AcmetoCasino Privacy Management API",
    description=(
        "GDPR/CCPA/LGPD Data Subject Request and privacy management service. "
        "Implements right to erasure, portability, rectification, and restriction "
        "for the new.acmetocasino.com iGaming platform."
    ),
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

# ---------------------------------------------------------------------------
# In-memory DSR store (replace with PostgreSQL in production)
# ---------------------------------------------------------------------------

_dsr_store: dict[str, dict[str, Any]] = {}
_audit_log: list[dict[str, Any]] = []


def _append_audit(event: str, detail: dict[str, Any]) -> None:
    """Append an immutable entry to the audit log."""
    _audit_log.append({
        "id": uuid.uuid4().hex,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "detail": detail,
    })


# ---------------------------------------------------------------------------
# Enums and constants
# ---------------------------------------------------------------------------


class DSRType(str, Enum):
    DELETION = "deletion"
    EXPORT = "export"
    RECTIFICATION = "rectification"
    RESTRICTION = "restriction"
    OBJECTION = "objection"


class DSRStatus(str, Enum):
    RECEIVED = "received"
    IDENTITY_VERIFIED = "identity_verified"
    LEGAL_ASSESSMENT = "legal_assessment"
    IN_PROGRESS = "in_progress"
    PARTIALLY_FULFILLED = "partially_fulfilled"
    FULFILLED = "fulfilled"
    REJECTED = "rejected"
    EXTENDED = "extended"


class Jurisdiction(str, Enum):
    EU = "EU"
    UK = "UK"
    CA_US = "CA_US"
    BR = "BR"
    KR = "KR"
    SG = "SG"
    TH = "TH"
    CA = "CA"
    OTHER = "OTHER"


RESPONSE_DEADLINE_DAYS: dict[str, int] = {
    "EU": 30,
    "UK": 30,
    "CA_US": 45,
    "BR": 15,
    "KR": 10,
    "SG": 30,
    "TH": 30,
    "CA": 30,
    "OTHER": 30,
}

APPLICABLE_LAWS: dict[str, str] = {
    "EU": "GDPR (Regulation (EU) 2016/679), Articles 17 and 20",
    "UK": "UK GDPR and Data Protection Act 2018",
    "CA_US": "CCPA/CPRA (Cal. Civ. Code §§ 1798.100–1798.199)",
    "BR": "LGPD (Lei No. 13.709/2018), Art. 18",
    "KR": "PIPA (Personal Information Protection Act), Art. 36",
    "SG": "PDPA 2012 (Singapore), Section 22",
    "TH": "PDPA B.E. 2562 (2019), Section 33",
    "CA": "PIPEDA (S.C. 2000, c. 5), Principle 4.5",
    "OTHER": "Best-effort erasure",
}


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class DSRSubmitRequest(BaseModel):
    player_id: str = Field(..., min_length=1, max_length=64, description="Platform player ID")
    request_type: DSRType = Field(..., description="Type of data subject request")
    jurisdiction: Jurisdiction = Field(..., description="Regulatory jurisdiction")
    identity_token: Optional[str] = Field(
        None, description="KYC identity verification token"
    )
    contact_email: Optional[str] = Field(
        None, description="Email address for notification on completion"
    )
    notes: Optional[str] = Field(
        None, max_length=1000, description="Optional free-text notes from the requester"
    )

    @field_validator("player_id")
    @classmethod
    def sanitise_player_id(cls, v: str) -> str:
        if not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError("player_id must be alphanumeric with hyphens/underscores only")
        return v


class DSRSubmitResponse(BaseModel):
    request_id: str
    player_id: str
    request_type: DSRType
    jurisdiction: Jurisdiction
    applicable_law: str
    status: DSRStatus
    submitted_at: str
    deadline: str
    message: str


class DSRStatusResponse(BaseModel):
    request_id: str
    player_id: str
    request_type: DSRType
    jurisdiction: Jurisdiction
    status: DSRStatus
    submitted_at: str
    deadline: str
    completed_at: Optional[str]
    summary: Optional[dict[str, Any]]
    retained_data_explanation: Optional[list[dict[str, Any]]]


class DataExportResponse(BaseModel):
    player_id: str
    export_id: str
    generated_at: str
    jurisdiction: str
    format: str
    data: dict[str, Any]


class DeletionRequest(BaseModel):
    reason: Optional[str] = Field(
        None, max_length=500, description="Reason for deletion (optional)"
    )
    jurisdiction: Jurisdiction = Field(Jurisdiction.EU, description="Regulatory jurisdiction")


class DeletionResponse(BaseModel):
    player_id: str
    request_id: str
    status: str
    deleted_categories: list[str]
    anonymised_categories: list[str]
    retained_categories: list[str]
    legal_holds: list[dict[str, Any]]
    completed_at: str


class RetentionReportResponse(BaseModel):
    generated_at: str
    summary: dict[str, Any]
    by_category: dict[str, dict[str, Any]]
    pending_dsrs: list[dict[str, Any]]
    overdue_dsrs: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health", include_in_schema=False)
async def health_check() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok", "service": "privacy-management-api", "version": "1.0.0"}


@app.post(
    "/api/v1/privacy/dsr",
    response_model=DSRSubmitResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a Data Subject Request",
    description=(
        "Submit a GDPR/CCPA/LGPD Data Subject Request. "
        "Returns a request ID and calculated response deadline per the applicable law."
    ),
    tags=["Data Subject Requests"],
)
async def submit_dsr(request: DSRSubmitRequest, http_request: Request) -> DSRSubmitResponse:
    """
    Intake a Data Subject Request (right to erasure, portability, or rectification).

    The response deadline is calculated from the jurisdiction:
    - EU/UK GDPR: 30 calendar days (extendable to 90 days for complex requests)
    - CCPA/CPRA: 45 calendar days (extendable to 90 days with written notice)
    - LGPD: 15 business days
    - PIPA (Korea): 10 days
    - PDPA (Singapore/Thailand): 30 calendar days
    - PIPEDA (Canada): 30 calendar days
    """
    now = datetime.now(timezone.utc)
    deadline_days = RESPONSE_DEADLINE_DAYS.get(request.jurisdiction.value, 30)
    deadline = now + timedelta(days=deadline_days)

    request_id = f"DSR-{uuid.uuid4().hex[:12].upper()}"

    dsr_record: dict[str, Any] = {
        "request_id": request_id,
        "player_id": request.player_id,
        "request_type": request.request_type.value,
        "jurisdiction": request.jurisdiction.value,
        "status": DSRStatus.RECEIVED.value,
        "submitted_at": now.isoformat(),
        "deadline": deadline.isoformat(),
        "completed_at": None,
        "contact_email": request.contact_email,
        "notes": request.notes,
        "identity_verified": bool(request.identity_token),
        "source_ip": http_request.client.host if http_request.client else "unknown",
        "decisions": [],
        "retained_explanation": [],
    }

    # Auto-advance to IDENTITY_VERIFIED if token provided (production: full KYC check)
    if request.identity_token:
        dsr_record["status"] = DSRStatus.IDENTITY_VERIFIED.value

    _dsr_store[request_id] = dsr_record
    _append_audit(
        "dsr_submitted",
        {
            "request_id": request_id,
            "player_id": request.player_id,
            "type": request.request_type.value,
            "jurisdiction": request.jurisdiction.value,
        },
    )

    logger.info(
        "DSR submitted: %s | player=%s | type=%s | jurisdiction=%s | deadline=%s",
        request_id,
        request.player_id,
        request.request_type.value,
        request.jurisdiction.value,
        deadline.date(),
    )

    return DSRSubmitResponse(
        request_id=request_id,
        player_id=request.player_id,
        request_type=request.request_type,
        jurisdiction=request.jurisdiction,
        applicable_law=APPLICABLE_LAWS.get(request.jurisdiction.value, "Unknown"),
        status=DSRStatus(dsr_record["status"]),
        submitted_at=now.isoformat(),
        deadline=deadline.isoformat(),
        message=(
            f"Your request has been received. We will respond within {deadline_days} days "
            f"as required by {APPLICABLE_LAWS.get(request.jurisdiction.value, 'applicable law')}. "
            f"Reference: {request_id}"
        ),
    )


@app.get(
    "/api/v1/privacy/dsr/{request_id}",
    response_model=DSRStatusResponse,
    summary="Get DSR status",
    description="Retrieve the current status and details of a Data Subject Request.",
    tags=["Data Subject Requests"],
)
async def get_dsr_status(request_id: str) -> DSRStatusResponse:
    """Track the processing status of a submitted DSR."""
    dsr = _dsr_store.get(request_id)
    if not dsr:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"DSR {request_id} not found",
        )

    return DSRStatusResponse(
        request_id=dsr["request_id"],
        player_id=dsr["player_id"],
        request_type=DSRType(dsr["request_type"]),
        jurisdiction=Jurisdiction(dsr["jurisdiction"]),
        status=DSRStatus(dsr["status"]),
        submitted_at=dsr["submitted_at"],
        deadline=dsr["deadline"],
        completed_at=dsr.get("completed_at"),
        summary=dsr.get("summary"),
        retained_data_explanation=dsr.get("retained_explanation") or None,
    )


@app.get(
    "/api/v1/privacy/data-export/{player_id}",
    response_model=DataExportResponse,
    summary="Export player data (GDPR portable format)",
    description=(
        "Generate a complete data export for a player in GDPR-portable JSON format. "
        "Satisfies GDPR Article 20 right to data portability, CCPA right to know, "
        "and LGPD portability right. The export includes all categories of player data "
        "held across platform systems."
    ),
    tags=["Data Portability"],
)
async def export_player_data(
    player_id: str,
    jurisdiction: Jurisdiction = Query(Jurisdiction.EU, description="Player's regulatory jurisdiction"),
) -> DataExportResponse:
    """
    Export all data held for a player in a machine-readable, portable format.

    In production this endpoint:
    1. Verifies the requester is the authenticated player (session check)
    2. Queries all data systems for records linked to player_id
    3. Assembles and returns the complete data package
    4. Logs the export to the audit trail (GDPR requires logging of data access)
    """
    export_id = f"EXPORT-{uuid.uuid4().hex[:10].upper()}"
    now = datetime.now(timezone.utc)

    # In production: query each system for actual player data
    # Here we return representative data matching the AcmetoCasino schema
    player_data: dict[str, Any] = {
        "profile": {
            "player_id": player_id,
            "first_name": "[from postgres:players]",
            "last_name": "[from postgres:players]",
            "email": "[from postgres:players]",
            "date_of_birth": "[from postgres:players]",
            "registration_date": "[from postgres:players]",
            "account_status": "[from postgres:players]",
            "jurisdiction": jurisdiction.value,
        },
        "financial_transactions": {
            "_note": "Transaction history (last 5 years, AML retention applies)",
            "_source": "postgres:transactions",
            "_count": "[from DB query]",
            "_sample": {
                "transaction_id": "[uuid]",
                "type": "deposit|withdrawal",
                "amount": "[amount]",
                "currency": "EUR",
                "created_at": "[timestamp]",
            },
        },
        "game_history": {
            "_note": "Game rounds played (last 5 years)",
            "_source": "clickhouse:game_rounds",
            "_count": "[from DB query]",
        },
        "kyc_documents": {
            "_note": "KYC verification records (document type and verification status only — "
                     "document images not included in portable export for security reasons)",
            "_source": "postgres:kyc_verifications",
        },
        "responsible_gambling": {
            "_note": "Deposit limits, cool-off periods, self-exclusion history",
            "_source": "postgres:rg_flags",
        },
        "marketing_consent": {
            "_note": "Consent preferences and full audit history",
            "_source": "postgres:consent_audit",
        },
        "support_history": {
            "_note": "Support ticket history (last 2 years)",
            "_source": "postgres:support_tickets",
        },
        "session_history": {
            "_note": "Login history with IP addresses (last 12 months)",
            "_source": "elasticsearch:sessions",
        },
        "bonus_history": {
            "_note": "Bonuses claimed and wagering history",
            "_source": "postgres:bonuses",
        },
    }

    _append_audit(
        "data_export_generated",
        {
            "export_id": export_id,
            "player_id": player_id,
            "jurisdiction": jurisdiction.value,
        },
    )

    logger.info(
        "Data export generated: export_id=%s player=%s jurisdiction=%s",
        export_id,
        player_id,
        jurisdiction.value,
    )

    return DataExportResponse(
        player_id=player_id,
        export_id=export_id,
        generated_at=now.isoformat(),
        jurisdiction=jurisdiction.value,
        format="GDPR-portable-JSON v1.0",
        data=player_data,
    )


@app.delete(
    "/api/v1/privacy/delete/{player_id}",
    response_model=DeletionResponse,
    summary="Process player data deletion",
    description=(
        "Execute selective erasure for a player. PII is deleted; financial records "
        "required for AML compliance are anonymised; self-exclusion and consent "
        "audit records are retained indefinitely per regulatory requirement."
    ),
    tags=["Erasure"],
)
async def delete_player_data(
    player_id: str,
    body: DeletionRequest,
) -> DeletionResponse:
    """
    Process a player data deletion request with selective erasure.

    This endpoint implements the core iGaming tension between GDPR Article 17
    (right to erasure) and AML/regulatory retention obligations.

    Deletion outcomes by category:
    - **PII** (name, email, address, DOB): DELETED
    - **Financial records** (transactions, game rounds): ANONYMISED — PII fields
      stripped, financial skeleton retained 5 years for AML audit
    - **KYC documents**: DELETED after 5-year AML retention window
    - **Session logs**: DELETED after 1 year
    - **Support tickets**: ANONYMISED after 2 years
    - **Self-exclusion records**: RETAINED indefinitely (cannot be erased —
      regulator requires this to prevent re-registration by excluded players)
    - **Consent audit trail**: RETAINED indefinitely as evidence of lawful basis
    """
    now = datetime.now(timezone.utc)
    request_id = f"DEL-{uuid.uuid4().hex[:12].upper()}"

    # Retention decisions (in production: driven by dsr-processor.py logic)
    deleted_categories = ["pii"]
    anonymised_categories = ["financial", "game_history", "support_tickets"]
    retained_categories = ["special_category", "marketing_consent"]

    legal_holds = [
        {
            "category": "financial",
            "action": "anonymised",
            "retain_until": (now + timedelta(days=5 * 365)).isoformat(),
            "basis": "AML — EU 4AMLD Art. 40, 5 year retention obligation",
        },
        {
            "category": "game_history",
            "action": "anonymised",
            "retain_until": (now + timedelta(days=5 * 365)).isoformat(),
            "basis": "AML — gaming transaction audit trail",
        },
        {
            "category": "kyc_documents",
            "action": "scheduled_deletion",
            "retain_until": (now + timedelta(days=5 * 365)).isoformat(),
            "basis": "AML CDD record retention — FATF Recommendation 11",
        },
        {
            "category": "special_category",
            "action": "retained_indefinitely",
            "retain_until": "indefinite",
            "basis": (
                "Responsible gambling records cannot be erased. Required to prevent "
                "re-registration by self-excluded players. UKGC RTS 14, MGA Directive 3/2018."
            ),
        },
        {
            "category": "marketing_consent",
            "action": "retained_indefinitely",
            "retain_until": "indefinite",
            "basis": "Consent audit trail — GDPR Art. 7(1) evidence requirement.",
        },
    ]

    _append_audit(
        "player_deletion_processed",
        {
            "request_id": request_id,
            "player_id": player_id,
            "jurisdiction": body.jurisdiction.value,
            "deleted": deleted_categories,
            "anonymised": anonymised_categories,
            "retained": retained_categories,
        },
    )

    logger.info(
        "Player deletion processed: %s | player=%s | deleted=%s | anonymised=%s | retained=%s",
        request_id,
        player_id,
        deleted_categories,
        anonymised_categories,
        retained_categories,
    )

    return DeletionResponse(
        player_id=player_id,
        request_id=request_id,
        status="partially_fulfilled",
        deleted_categories=deleted_categories,
        anonymised_categories=anonymised_categories,
        retained_categories=retained_categories,
        legal_holds=legal_holds,
        completed_at=now.isoformat(),
    )


@app.get(
    "/api/v1/privacy/retention-report",
    response_model=RetentionReportResponse,
    summary="Retention compliance dashboard",
    description=(
        "Generate a retention compliance report showing DSR status, overdue requests, "
        "and data category retention statistics. Intended for the DPO dashboard "
        "and regulatory reporting."
    ),
    tags=["Compliance Reporting"],
)
async def get_retention_report(
    include_fulfilled: bool = Query(False, description="Include completed DSRs in report"),
) -> RetentionReportResponse:
    """
    Compliance dashboard for the Data Protection Officer.

    Shows:
    - Pending DSRs and their deadlines
    - Overdue DSRs (past response deadline)
    - Retention statistics by data category
    - Summary counts for regulatory reporting
    """
    now = datetime.now(timezone.utc)

    all_dsrs = list(_dsr_store.values())
    pending = [
        d for d in all_dsrs
        if d["status"] not in (DSRStatus.FULFILLED.value, DSRStatus.REJECTED.value)
    ]
    overdue = [
        d for d in pending
        if datetime.fromisoformat(d["deadline"]) < now
    ]

    if include_fulfilled:
        report_dsrs = all_dsrs
    else:
        report_dsrs = pending

    # Simulated retention statistics (production: query retention_audit_log table)
    retention_stats: dict[str, dict[str, Any]] = {
        "pii": {
            "total_players": 125000,
            "deleted_last_30d": 47,
            "pending_deletion": 12,
            "policy": "Delete on account closure or DSR",
            "regulation": "GDPR Art. 17",
        },
        "financial": {
            "total_records": 8750000,
            "anonymised_last_30d": 1240,
            "pending_anonymisation": 85,
            "oldest_record_age_days": 1825,
            "policy": "Anonymise PII after 5 years (AML hold)",
            "regulation": "EU 4AMLD Art. 40",
        },
        "kyc_documents": {
            "total_documents": 185000,
            "deleted_last_30d": 220,
            "pending_deletion": 15,
            "policy": "Delete after 5 years AML retention",
            "regulation": "FATF Rec. 11",
        },
        "game_history": {
            "total_rounds": 45000000,
            "anonymised_last_30d": 88000,
            "policy": "Anonymise player_id after 5 years",
            "regulation": "MGA Directive 3/2018",
        },
        "session_logs": {
            "total_sessions": 2800000,
            "deleted_last_30d": 15000,
            "policy": "Delete after 1 year",
            "regulation": "GDPR Recital 47",
        },
        "special_category": {
            "total_rg_records": 3200,
            "deleted_last_30d": 0,
            "policy": "Retained indefinitely (self-exclusion cannot be erased)",
            "regulation": "UKGC RTS 14, MGA Directive 3/2018",
        },
    }

    summary: dict[str, Any] = {
        "report_generated_at": now.isoformat(),
        "total_dsrs": len(all_dsrs),
        "pending_dsrs": len(pending),
        "overdue_dsrs": len(overdue),
        "fulfilled_last_30d": len([
            d for d in all_dsrs
            if d["status"] == DSRStatus.FULFILLED.value
            and d.get("completed_at")
            and datetime.fromisoformat(d["completed_at"]) > now - timedelta(days=30)
        ]),
        "audit_log_entries": len(_audit_log),
    }

    pending_dsr_list = [
        {
            "request_id": d["request_id"],
            "player_id": d["player_id"],
            "type": d["request_type"],
            "jurisdiction": d["jurisdiction"],
            "status": d["status"],
            "submitted_at": d["submitted_at"],
            "deadline": d["deadline"],
            "days_remaining": (
                datetime.fromisoformat(d["deadline"]) - now
            ).days,
        }
        for d in report_dsrs
    ]

    overdue_dsr_list = [
        {
            "request_id": d["request_id"],
            "player_id": d["player_id"],
            "type": d["request_type"],
            "jurisdiction": d["jurisdiction"],
            "status": d["status"],
            "deadline": d["deadline"],
            "days_overdue": (now - datetime.fromisoformat(d["deadline"])).days,
        }
        for d in overdue
    ]

    return RetentionReportResponse(
        generated_at=now.isoformat(),
        summary=summary,
        by_category=retention_stats,
        pending_dsrs=pending_dsr_list,
        overdue_dsrs=overdue_dsr_list,
    )


@app.get(
    "/api/v1/privacy/audit-log",
    summary="Privacy audit log",
    description=(
        "Return the immutable privacy audit log. "
        "All DSR submissions, data exports, deletions, and anonymisation operations "
        "are recorded here. Required for GDPR Article 5(2) accountability compliance."
    ),
    tags=["Compliance Reporting"],
)
async def get_audit_log(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """
    Return the privacy audit log.

    In production this queries an append-only PostgreSQL table with row-level
    security (only the privacy service account can insert; no updates or deletes allowed).
    """
    total = len(_audit_log)
    page = _audit_log[offset:offset + limit]
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "entries": page,
    }


@app.post(
    "/api/v1/privacy/dsr/{request_id}/verify",
    summary="Verify identity for a DSR",
    description=(
        "Mark a DSR as identity-verified, advancing it for processing. "
        "In production this is triggered by the KYC service after document verification."
    ),
    tags=["Data Subject Requests"],
)
async def verify_dsr_identity(
    request_id: str,
    verification_reference: str = Query(..., description="KYC verification reference"),
) -> dict[str, str]:
    """Advance a DSR from RECEIVED to IDENTITY_VERIFIED status."""
    dsr = _dsr_store.get(request_id)
    if not dsr:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"DSR {request_id} not found",
        )
    if dsr["status"] != DSRStatus.RECEIVED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"DSR is in status {dsr['status']} — cannot re-verify",
        )

    dsr["status"] = DSRStatus.IDENTITY_VERIFIED.value
    dsr["identity_verified"] = True

    _append_audit(
        "dsr_identity_verified",
        {
            "request_id": request_id,
            "verification_reference": verification_reference,
        },
    )

    return {
        "request_id": request_id,
        "status": DSRStatus.IDENTITY_VERIFIED.value,
        "message": "Identity verified. Request will be processed within the deadline.",
    }


@app.post(
    "/api/v1/privacy/dsr/{request_id}/extend",
    summary="Extend DSR response deadline",
    description=(
        "Extend the response deadline for a complex DSR. "
        "GDPR allows a one-time 60-day extension (total 90 days) for complex requests. "
        "CCPA allows extension to 90 days with written notice. "
        "The player must be notified of the extension before the original deadline."
    ),
    tags=["Data Subject Requests"],
)
async def extend_dsr_deadline(
    request_id: str,
    reason: str = Query(..., description="Reason for extension (sent to player)"),
) -> dict[str, Any]:
    """Extend the DSR deadline for a complex request."""
    dsr = _dsr_store.get(request_id)
    if not dsr:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"DSR {request_id} not found",
        )
    if dsr["status"] in (DSRStatus.FULFILLED.value, DSRStatus.REJECTED.value):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot extend a completed DSR (status: {dsr['status']})",
        )

    jurisdiction = dsr["jurisdiction"]
    original_deadline = datetime.fromisoformat(dsr["deadline"])

    # GDPR: can extend to 90 days total from submission
    # CCPA: can extend to 90 days total with written notice
    submitted_at = datetime.fromisoformat(dsr["submitted_at"])
    max_deadline = submitted_at + timedelta(days=90)
    new_deadline = min(original_deadline + timedelta(days=60), max_deadline)

    dsr["deadline"] = new_deadline.isoformat()
    dsr["status"] = DSRStatus.EXTENDED.value

    _append_audit(
        "dsr_extended",
        {
            "request_id": request_id,
            "jurisdiction": jurisdiction,
            "original_deadline": original_deadline.isoformat(),
            "new_deadline": new_deadline.isoformat(),
            "reason": reason,
        },
    )

    return {
        "request_id": request_id,
        "status": DSRStatus.EXTENDED.value,
        "original_deadline": original_deadline.isoformat(),
        "new_deadline": new_deadline.isoformat(),
        "reason": reason,
        "note": "Player must be notified of this extension before the original deadline.",
    }


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------


@app.exception_handler(422)
async def validation_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": "validation_error",
            "detail": str(exc),
            "message": "Request validation failed. Check the request body and parameters.",
        },
    )


@app.exception_handler(500)
async def internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error: %s", exc)
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_server_error",
            "message": "An unexpected error occurred. The incident has been logged.",
        },
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "privacy-dashboard-api:app",
        host="0.0.0.0",
        port=8080,
        reload=True,
        log_level="info",
    )
