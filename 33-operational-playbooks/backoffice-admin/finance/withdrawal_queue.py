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
Manual withdrawal review queue — hold, approve, and reject withdrawals.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from auth import TokenData, require_permission
from models import WithdrawalDecisionRequest, WithdrawalRequest, WithdrawalStatus

router = APIRouter(prefix="/withdrawals", tags=["Withdrawal Queue"])

# ---------------------------------------------------------------------------
# Simulated withdrawal store
# ---------------------------------------------------------------------------

_WITHDRAWALS: Dict[str, dict] = {
    "WD-001": {
        "withdrawal_id": "WD-001",
        "player_id": "PLR-001",
        "amount": 500.0,
        "currency": "GBP",
        "payment_method": "bank_transfer",
        "payment_reference": "GB29NWBK60161331926819",
        "requested_at": datetime(2024, 3, 1, 9, 0, tzinfo=timezone.utc),
        "status": "pending",
        "reviewed_at": None,
        "reviewed_by": None,
        "rejection_reason": None,
        "kyc_verified": True,
        "aml_cleared": True,
    },
    "WD-002": {
        "withdrawal_id": "WD-002",
        "player_id": "PLR-002",
        "amount": 80.0,
        "currency": "GBP",
        "payment_method": "debit_card",
        "payment_reference": None,
        "requested_at": datetime(2024, 3, 2, 14, 30, tzinfo=timezone.utc),
        "status": "pending",
        "reviewed_at": None,
        "reviewed_by": None,
        "rejection_reason": None,
        "kyc_verified": False,
        "aml_cleared": False,
    },
}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/", response_model=List[WithdrawalRequest], summary="List withdrawal requests")
async def list_withdrawals(
    status_filter: Optional[WithdrawalStatus] = Query(None, alias="status"),
    player_id: Optional[str] = Query(None),
    kyc_verified: Optional[bool] = Query(None),
    current: TokenData = Depends(require_permission("finance:read")),
) -> List[WithdrawalRequest]:
    results = list(_WITHDRAWALS.values())
    if status_filter:
        results = [w for w in results if w["status"] == status_filter.value]
    if player_id:
        results = [w for w in results if w["player_id"] == player_id]
    if kyc_verified is not None:
        results = [w for w in results if w["kyc_verified"] == kyc_verified]
    return [WithdrawalRequest(**w) for w in results]


@router.get("/pending", response_model=List[WithdrawalRequest], summary="List all pending withdrawals")
async def list_pending(
    current: TokenData = Depends(require_permission("finance:read")),
) -> List[WithdrawalRequest]:
    return [
        WithdrawalRequest(**w) for w in _WITHDRAWALS.values()
        if w["status"] == "pending"
    ]


@router.get("/{withdrawal_id}", response_model=WithdrawalRequest, summary="Get a withdrawal request")
async def get_withdrawal(
    withdrawal_id: str,
    current: TokenData = Depends(require_permission("finance:read")),
) -> WithdrawalRequest:
    raw = _WITHDRAWALS.get(withdrawal_id)
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Withdrawal not found")
    return WithdrawalRequest(**raw)


@router.post("/decide", response_model=WithdrawalRequest, summary="Approve or reject a withdrawal")
async def decide_withdrawal(
    decision: WithdrawalDecisionRequest,
    current: TokenData = Depends(require_permission("finance:approve")),
) -> WithdrawalRequest:
    raw = _WITHDRAWALS.get(decision.withdrawal_id)
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Withdrawal not found")
    if raw["status"] != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Withdrawal already in state: {raw['status']}",
        )
    if decision.decision == WithdrawalStatus.REJECTED and not decision.rejection_reason:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="rejection_reason is required when rejecting a withdrawal",
        )
    if decision.decision == WithdrawalStatus.APPROVED and not raw["kyc_verified"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot approve withdrawal: player KYC is not verified",
        )
    raw["status"] = decision.decision.value
    raw["reviewed_at"] = datetime.now(timezone.utc)
    raw["reviewed_by"] = current.username
    raw["rejection_reason"] = decision.rejection_reason
    return WithdrawalRequest(**raw)


@router.post("/{withdrawal_id}/hold", summary="Place a withdrawal on hold for further review")
async def hold_withdrawal(
    withdrawal_id: str,
    reason: str = Query(..., min_length=5),
    current: TokenData = Depends(require_permission("finance:write")),
) -> dict:
    raw = _WITHDRAWALS.get(withdrawal_id)
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Withdrawal not found")
    if raw["status"] != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot hold withdrawal in state: {raw['status']}",
        )
    raw["status"] = "processing"  # using processing to indicate manual hold
    return {
        "withdrawal_id": withdrawal_id,
        "status": "on_hold",
        "reason": reason,
        "held_by": current.username,
        "held_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/stats/summary", summary="Withdrawal queue statistics")
async def withdrawal_stats(
    current: TokenData = Depends(require_permission("finance:read")),
) -> dict:
    wds = list(_WITHDRAWALS.values())
    total_pending_value = sum(w["amount"] for w in wds if w["status"] == "pending")
    return {
        "total": len(wds),
        "pending": sum(1 for w in wds if w["status"] == "pending"),
        "approved": sum(1 for w in wds if w["status"] == "approved"),
        "rejected": sum(1 for w in wds if w["status"] == "rejected"),
        "processing": sum(1 for w in wds if w["status"] == "processing"),
        "pending_kyc_unverified": sum(1 for w in wds if w["status"] == "pending" and not w["kyc_verified"]),
        "total_pending_value_gbp": round(total_pending_value, 2),
    }
