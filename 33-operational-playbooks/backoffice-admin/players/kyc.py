# Companion code for "The Backend of Luck" - Chapter 33, Operational Playbooks.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
KYC document review, approval, and rejection workflow.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from auth import TokenData, require_permission
from models import KYCDocument, KYCReviewRequest, KYCStatus

router = APIRouter(prefix="/kyc", tags=["KYC"])

# ---------------------------------------------------------------------------
# Simulated KYC document store
# ---------------------------------------------------------------------------

_KYC_DOCS: Dict[str, dict] = {
    "DOC-001": {
        "document_id": "DOC-001",
        "player_id": "PLR-001",
        "document_type": "passport",
        "submitted_at": datetime(2022, 1, 12, tzinfo=timezone.utc),
        "reviewed_at": datetime(2022, 1, 13, tzinfo=timezone.utc),
        "reviewed_by": "admin",
        "status": "approved",
        "rejection_reason": None,
        "expiry_date": "2032-01-12",
        "file_url": "https://docs.acmetocasino.internal/kyc/DOC-001.pdf",
    },
    "DOC-002": {
        "document_id": "DOC-002",
        "player_id": "PLR-002",
        "document_type": "driving_licence",
        "submitted_at": datetime(2024, 2, 14, tzinfo=timezone.utc),
        "reviewed_at": None,
        "reviewed_by": None,
        "status": "pending",
        "rejection_reason": None,
        "expiry_date": "2028-06-30",
        "file_url": "https://docs.acmetocasino.internal/kyc/DOC-002.pdf",
    },
    "DOC-003": {
        "document_id": "DOC-003",
        "player_id": "PLR-002",
        "document_type": "utility_bill",
        "submitted_at": datetime(2024, 2, 14, tzinfo=timezone.utc),
        "reviewed_at": None,
        "reviewed_by": None,
        "status": "pending",
        "rejection_reason": None,
        "expiry_date": None,
        "file_url": "https://docs.acmetocasino.internal/kyc/DOC-003.pdf",
    },
}


def _doc_to_model(raw: dict) -> KYCDocument:
    return KYCDocument(**raw)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/pending", response_model=List[KYCDocument], summary="List all pending KYC documents")
async def list_pending_kyc(
    current: TokenData = Depends(require_permission("kyc:read")),
) -> List[KYCDocument]:
    return [_doc_to_model(d) for d in _KYC_DOCS.values() if d["status"] == "pending"]


@router.get("/player/{player_id}", response_model=List[KYCDocument], summary="Get KYC documents for a player")
async def get_player_kyc(
    player_id: str,
    current: TokenData = Depends(require_permission("kyc:read")),
) -> List[KYCDocument]:
    docs = [_doc_to_model(d) for d in _KYC_DOCS.values() if d["player_id"] == player_id]
    return docs


@router.get("/{document_id}", response_model=KYCDocument, summary="Get a single KYC document")
async def get_document(
    document_id: str,
    current: TokenData = Depends(require_permission("kyc:read")),
) -> KYCDocument:
    raw = _KYC_DOCS.get(document_id)
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return _doc_to_model(raw)


@router.post("/review", response_model=KYCDocument, summary="Approve or reject a KYC document")
async def review_document(
    review: KYCReviewRequest,
    current: TokenData = Depends(require_permission("kyc:approve")),
) -> KYCDocument:
    raw = _KYC_DOCS.get(review.document_id)
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    if raw["status"] not in ("pending",):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Document already in terminal state: {raw['status']}",
        )
    if review.action == KYCStatus.REJECTED and not review.rejection_reason:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="rejection_reason is required when rejecting a document",
        )
    raw["status"] = review.action.value
    raw["reviewed_at"] = datetime.now(timezone.utc)
    raw["reviewed_by"] = current.username
    raw["rejection_reason"] = review.rejection_reason
    return _doc_to_model(raw)


@router.post("/request/{player_id}", summary="Request KYC submission from a player")
async def request_kyc_from_player(
    player_id: str,
    document_types: List[str] = Query(default=["passport", "utility_bill"]),
    current: TokenData = Depends(require_permission("kyc:approve")),
) -> dict:
    return {
        "player_id": player_id,
        "requested_documents": document_types,
        "requested_by": current.username,
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "message": "KYC request notification sent to player",
    }


@router.get("/stats/summary", summary="KYC queue statistics")
async def kyc_stats(
    current: TokenData = Depends(require_permission("kyc:read")),
) -> dict:
    docs = list(_KYC_DOCS.values())
    return {
        "total": len(docs),
        "pending": sum(1 for d in docs if d["status"] == "pending"),
        "approved": sum(1 for d in docs if d["status"] == "approved"),
        "rejected": sum(1 for d in docs if d["status"] == "rejected"),
        "expired": sum(1 for d in docs if d["status"] == "expired"),
    }
