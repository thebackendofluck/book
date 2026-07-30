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
Bonus management — creation, assignment, and lifecycle tracking.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from auth import TokenData, require_permission
from models import Bonus, BonusAssignment, BonusType, Jurisdiction

router = APIRouter(prefix="/bonuses", tags=["Bonus Management"])

# ---------------------------------------------------------------------------
# Simulated bonus and assignment stores
# ---------------------------------------------------------------------------

_BONUSES: Dict[str, dict] = {
    "BON-001": {
        "bonus_id": "BON-001",
        "name": "Welcome Package 100%",
        "bonus_type": BonusType.WELCOME.value,
        "brand": "AcmetoCasino",
        "value": 200.0,
        "currency": "GBP",
        "wagering_requirement": 35.0,
        "max_bet": 5.0,
        "min_deposit": 20.0,
        "valid_days": 30,
        "is_active": True,
        "jurisdiction": Jurisdiction.UKGC.value,
        "created_by": "admin",
        "created_at": datetime(2023, 6, 1, tzinfo=timezone.utc),
        "expiry_date": None,
    },
    "BON-002": {
        "bonus_id": "BON-002",
        "name": "20 Free Spins Monday",
        "bonus_type": BonusType.FREE_SPINS.value,
        "brand": "AcmetoCasino",
        "value": 20.0,
        "currency": "GBP",
        "wagering_requirement": 40.0,
        "max_bet": 5.0,
        "min_deposit": 10.0,
        "valid_days": 7,
        "is_active": True,
        "jurisdiction": Jurisdiction.UKGC.value,
        "created_by": "admin",
        "created_at": datetime(2023, 8, 1, tzinfo=timezone.utc),
        "expiry_date": None,
    },
}

_ASSIGNMENTS: Dict[str, dict] = {
    "ASSI-001": {
        "assignment_id": "ASSI-001",
        "bonus_id": "BON-001",
        "player_id": "PLR-001",
        "assigned_by": "admin",
        "assigned_at": datetime(2022, 1, 10, tzinfo=timezone.utc),
        "expires_at": datetime(2022, 2, 9, tzinfo=timezone.utc),
        "status": "used",
        "wagered_amount": 7000.0,
        "remaining_balance": 0.0,
    },
}


# ---------------------------------------------------------------------------
# Endpoints — Bonus Templates
# ---------------------------------------------------------------------------


@router.get("/", response_model=List[Bonus], summary="List all bonus templates")
async def list_bonuses(
    bonus_type: Optional[BonusType] = Query(None),
    is_active: Optional[bool] = Query(None),
    jurisdiction: Optional[Jurisdiction] = Query(None),
    current: TokenData = Depends(require_permission("crm:read")),
) -> List[Bonus]:
    results = list(_BONUSES.values())
    if bonus_type:
        results = [b for b in results if b["bonus_type"] == bonus_type.value]
    if is_active is not None:
        results = [b for b in results if b["is_active"] == is_active]
    if jurisdiction:
        results = [b for b in results if b.get("jurisdiction") == jurisdiction.value]
    return [Bonus(**b) for b in results]


@router.get("/{bonus_id}", response_model=Bonus, summary="Get a bonus template")
async def get_bonus(
    bonus_id: str,
    current: TokenData = Depends(require_permission("crm:read")),
) -> Bonus:
    raw = _BONUSES.get(bonus_id)
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bonus not found")
    return Bonus(**raw)


@router.post("/", response_model=Bonus, summary="Create a new bonus template")
async def create_bonus(
    name: str = Query(..., min_length=3),
    bonus_type: BonusType = Query(...),
    value: float = Query(..., gt=0),
    wagering_requirement: float = Query(35.0, ge=0, le=200),
    min_deposit: Optional[float] = Query(None, ge=0),
    max_bet: Optional[float] = Query(None, ge=0),
    valid_days: int = Query(30, ge=1, le=365),
    jurisdiction: Optional[Jurisdiction] = Query(None),
    currency: str = Query("GBP"),
    current: TokenData = Depends(require_permission("crm:write")),
) -> Bonus:
    bonus_id = f"BON-{uuid.uuid4().hex[:6].upper()}"
    raw = {
        "bonus_id": bonus_id,
        "name": name,
        "bonus_type": bonus_type.value,
        "brand": "AcmetoCasino",
        "value": value,
        "currency": currency,
        "wagering_requirement": wagering_requirement,
        "max_bet": max_bet,
        "min_deposit": min_deposit,
        "valid_days": valid_days,
        "is_active": True,
        "jurisdiction": jurisdiction.value if jurisdiction else None,
        "created_by": current.username,
        "created_at": datetime.now(timezone.utc),
        "expiry_date": None,
    }
    _BONUSES[bonus_id] = raw
    return Bonus(**raw)


@router.patch("/{bonus_id}/deactivate", summary="Deactivate a bonus template")
async def deactivate_bonus(
    bonus_id: str,
    current: TokenData = Depends(require_permission("crm:write")),
) -> dict:
    raw = _BONUSES.get(bonus_id)
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bonus not found")
    raw["is_active"] = False
    return {
        "bonus_id": bonus_id,
        "is_active": False,
        "deactivated_by": current.username,
        "deactivated_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Endpoints — Bonus Assignments
# ---------------------------------------------------------------------------


@router.post("/assign", response_model=BonusAssignment, summary="Assign a bonus to a player")
async def assign_bonus(
    bonus_id: str,
    player_id: str,
    current: TokenData = Depends(require_permission("crm:write")),
) -> BonusAssignment:
    raw_bonus = _BONUSES.get(bonus_id)
    if not raw_bonus:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bonus not found")
    if not raw_bonus["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot assign an inactive bonus",
        )
    now = datetime.now(timezone.utc)
    assignment_id = f"ASSI-{uuid.uuid4().hex[:6].upper()}"
    raw = {
        "assignment_id": assignment_id,
        "bonus_id": bonus_id,
        "player_id": player_id,
        "assigned_by": current.username,
        "assigned_at": now,
        "expires_at": now + timedelta(days=raw_bonus["valid_days"]),
        "status": "active",
        "wagered_amount": 0.0,
        "remaining_balance": raw_bonus["value"],
    }
    _ASSIGNMENTS[assignment_id] = raw
    return BonusAssignment(**raw)


@router.get("/assignments/player/{player_id}", response_model=List[BonusAssignment], summary="Get bonus assignments for a player")
async def player_assignments(
    player_id: str,
    assignment_status: Optional[str] = Query(None, alias="status"),
    current: TokenData = Depends(require_permission("crm:read")),
) -> List[BonusAssignment]:
    results = [a for a in _ASSIGNMENTS.values() if a["player_id"] == player_id]
    if assignment_status:
        results = [a for a in results if a["status"] == assignment_status]
    return [BonusAssignment(**a) for a in results]


@router.patch("/assignments/{assignment_id}/revoke", summary="Revoke an active bonus assignment")
async def revoke_assignment(
    assignment_id: str,
    reason: str = Query(..., min_length=5),
    current: TokenData = Depends(require_permission("crm:write")),
) -> dict:
    raw = _ASSIGNMENTS.get(assignment_id)
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found")
    if raw["status"] != "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot revoke assignment in state: {raw['status']}",
        )
    raw["status"] = "revoked"
    raw["remaining_balance"] = 0.0
    return {
        "assignment_id": assignment_id,
        "status": "revoked",
        "reason": reason,
        "revoked_by": current.username,
        "revoked_at": datetime.now(timezone.utc).isoformat(),
    }
