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
Source of Wealth (SOW) tracker — completion tracking and deadline management.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from auth import TokenData, require_permission
from models import SOWRecord

router = APIRouter(prefix="/sow", tags=["Source of Wealth"])

# ---------------------------------------------------------------------------
# SOW configuration
# ---------------------------------------------------------------------------

SOW_RESPONSE_DEADLINE_DAYS = 28  # typical UKGC guidance


# ---------------------------------------------------------------------------
# Simulated SOW store
# ---------------------------------------------------------------------------

_SOW_RECORDS: Dict[str, dict] = {
    "SOW-001": {
        "sow_id": "SOW-001",
        "player_id": "PLR-001",
        "requested_at": datetime(2024, 1, 10, tzinfo=timezone.utc),
        "deadline": datetime(2024, 2, 7, tzinfo=timezone.utc),
        "submitted_at": datetime(2024, 1, 25, tzinfo=timezone.utc),
        "reviewed_at": datetime(2024, 1, 28, tzinfo=timezone.utc),
        "outcome": "accepted",
        "documents": ["payslip_jan24.pdf", "bank_statement_dec23.pdf"],
        "notes": "Player provided 3 months payslips and bank statements.",
        "reviewed_by": "compliance_agent",
    },
    "SOW-002": {
        "sow_id": "SOW-002",
        "player_id": "PLR-002",
        "requested_at": datetime(2024, 2, 15, tzinfo=timezone.utc),
        "deadline": datetime(2024, 3, 14, tzinfo=timezone.utc),
        "submitted_at": None,
        "reviewed_at": None,
        "outcome": None,
        "documents": [],
        "notes": None,
        "reviewed_by": None,
    },
}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/", response_model=List[SOWRecord], summary="List all SOW records")
async def list_sow_records(
    outcome: Optional[str] = Query(None, regex="^(accepted|rejected|pending)$"),
    overdue: Optional[bool] = Query(None),
    current: TokenData = Depends(require_permission("compliance:read")),
) -> List[SOWRecord]:
    now = datetime.now(timezone.utc)
    results = list(_SOW_RECORDS.values())
    if outcome == "pending":
        results = [r for r in results if r["outcome"] is None]
    elif outcome:
        results = [r for r in results if r["outcome"] == outcome]
    if overdue is True:
        results = [r for r in results if r["submitted_at"] is None and r["deadline"] < now]
    elif overdue is False:
        results = [r for r in results if not (r["submitted_at"] is None and r["deadline"] < now)]
    return [SOWRecord(**r) for r in results]


@router.get("/player/{player_id}", response_model=List[SOWRecord], summary="Get SOW records for a player")
async def get_player_sow(
    player_id: str,
    current: TokenData = Depends(require_permission("compliance:read")),
) -> List[SOWRecord]:
    return [SOWRecord(**r) for r in _SOW_RECORDS.values() if r["player_id"] == player_id]


@router.post("/request/{player_id}", response_model=SOWRecord, summary="Issue a new SOW request to a player")
async def request_sow(
    player_id: str,
    deadline_days: int = Query(SOW_RESPONSE_DEADLINE_DAYS, ge=7, le=90),
    notes: Optional[str] = Query(None),
    current: TokenData = Depends(require_permission("compliance:write")),
) -> SOWRecord:
    now = datetime.now(timezone.utc)
    sow_id = f"SOW-{uuid.uuid4().hex[:6].upper()}"
    raw = {
        "sow_id": sow_id,
        "player_id": player_id,
        "requested_at": now,
        "deadline": now + timedelta(days=deadline_days),
        "submitted_at": None,
        "reviewed_at": None,
        "outcome": None,
        "documents": [],
        "notes": notes,
        "reviewed_by": current.username,
    }
    _SOW_RECORDS[sow_id] = raw
    return SOWRecord(**raw)


@router.patch("/{sow_id}/review", response_model=SOWRecord, summary="Review and decide on a SOW submission")
async def review_sow(
    sow_id: str,
    outcome: str = Query(..., regex="^(accepted|rejected)$"),
    notes: Optional[str] = Query(None),
    current: TokenData = Depends(require_permission("compliance:write")),
) -> SOWRecord:
    raw = _SOW_RECORDS.get(sow_id)
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SOW record not found")
    if raw["submitted_at"] is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot review a SOW request that has not been submitted by the player",
        )
    if raw["outcome"] is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"SOW already reviewed with outcome: {raw['outcome']}",
        )
    raw["outcome"] = outcome
    raw["reviewed_at"] = datetime.now(timezone.utc)
    raw["reviewed_by"] = current.username
    if notes:
        raw["notes"] = notes
    return SOWRecord(**raw)


@router.get("/stats/completion-rates", summary="SOW completion rate statistics")
async def sow_completion_rates(
    current: TokenData = Depends(require_permission("compliance:read")),
) -> dict:
    now = datetime.now(timezone.utc)
    records = list(_SOW_RECORDS.values())
    total = len(records)
    submitted = sum(1 for r in records if r["submitted_at"] is not None)
    accepted = sum(1 for r in records if r["outcome"] == "accepted")
    rejected = sum(1 for r in records if r["outcome"] == "rejected")
    overdue = sum(1 for r in records if r["submitted_at"] is None and r["deadline"] < now)
    return {
        "total_requests": total,
        "submitted": submitted,
        "pending": total - submitted,
        "overdue": overdue,
        "accepted": accepted,
        "rejected": rejected,
        "completion_rate_pct": round(submitted / total * 100, 1) if total else 0,
        "acceptance_rate_pct": round(accepted / submitted * 100, 1) if submitted else 0,
        "as_of": now.isoformat(),
    }
