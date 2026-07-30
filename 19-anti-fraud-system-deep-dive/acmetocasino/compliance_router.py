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
Compliance HTTP endpoints: KYC verification and AML monitoring.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.rbac import get_current_user, require_role
from app.compliance.models import (
    AMLAlert,
    AMLReviewRequest,
    KYCCheck,
    KYCSubmitRequest,
    KYCVerifyRequest,
    RiskScore,
)
from app.compliance import service

router = APIRouter(prefix="/compliance", tags=["Compliance"])


# ---------- KYC ----------

@router.get("/kyc/pending", response_model=list[KYCCheck])
def list_pending_kyc(
    limit: int = Query(default=50, le=200),
    _user: dict = Depends(require_role("operator")),
):
    """List all pending KYC checks. Requires operator role."""
    return service.get_pending_kyc(limit=limit)


@router.post(
    "/kyc/{player_id}/submit",
    response_model=KYCCheck,
    status_code=status.HTTP_201_CREATED,
)
def submit_kyc(
    player_id: uuid.UUID,
    body: KYCSubmitRequest,
    _user: dict = Depends(get_current_user),
):
    """Submit a KYC document for verification."""
    try:
        check = service.submit_kyc(player_id, body.document_type, body.document_ref)
        return check
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


@router.post("/kyc/{player_id}/verify", response_model=KYCCheck)
def verify_kyc(
    player_id: uuid.UUID,
    body: KYCVerifyRequest,
    _user: dict = Depends(require_role("operator")),
):
    """Approve or reject a player's KYC check. Requires operator role."""
    try:
        check = service.verify_kyc(player_id, body.status, body.reviewer_id, body.notes)
        return check
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


@router.post(
    "/kyc/{player_id}/auto-verify",
    response_model=KYCCheck,
    status_code=status.HTTP_201_CREATED,
)
def auto_verify_kyc(
    player_id: uuid.UUID,
    _user: dict = Depends(get_current_user),
):
    """
    Submit and instantly approve KYC for demo players.
    Combines submit + verify in one step for automated flows.
    """
    try:
        check = service.submit_kyc(player_id, "AUTO_VERIFY", f"auto_{player_id}")
        approved = service.verify_kyc(
            player_id, "approved", uuid.UUID("00000000-0000-0000-0000-000000000000"),
            "Auto-verified for demo environment",
        )
        return approved
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


# ---------- AML ----------

@router.get("/aml/alerts", response_model=list[AMLAlert])
def get_aml_alerts(
    alert_status: str | None = Query(default=None, alias="status"),
    severity: str | None = None,
    limit: int = Query(default=50, le=200),
):
    """List AML alerts with optional filters (public for dashboard)."""
    return service.get_alerts(
        status_filter=alert_status,
        severity_filter=severity,
        limit=limit,
    )


@router.post("/aml/alerts/{alert_id}/review", response_model=AMLAlert)
def review_aml_alert(
    alert_id: uuid.UUID,
    body: AMLReviewRequest,
    _user: dict = Depends(require_role("operator")),
):
    """Review an AML alert. Requires operator role."""
    try:
        alert = service.review_alert(alert_id, body.status, body.reviewer_id, body.notes)
        return alert
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


@router.get("/aml/risk/{player_id}", response_model=RiskScore)
def get_risk_score(
    player_id: uuid.UUID,
    _user: dict = Depends(require_role("operator")),
):
    """Run velocity checks and return the player's risk score. Requires operator role."""
    return service.check_velocity(player_id)
