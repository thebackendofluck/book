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
Affordability checks — income vs spend analysis per UKGC requirements.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from auth import TokenData, require_permission
from models import AffordabilityCheck

router = APIRouter(prefix="/affordability", tags=["Affordability"])

# ---------------------------------------------------------------------------
# Thresholds (UKGC guidance)
# ---------------------------------------------------------------------------

LOSS_THRESHOLD_90_DAYS = 500.0       # trigger enhanced checks above this
AFFORDABILITY_RATIO_FAIL = 0.30      # losses / annual income > 30% = fail


# ---------------------------------------------------------------------------
# Simulated affordability store
# ---------------------------------------------------------------------------

_CHECKS: Dict[str, dict] = {
    "AFC-001": {
        "check_id": "AFC-001",
        "player_id": "PLR-001",
        "checked_at": datetime(2024, 1, 15, tzinfo=timezone.utc),
        "period_days": 90,
        "total_deposits": 1800.0,
        "total_losses": 1200.0,
        "stated_annual_income": 55000.0,
        "income_band": "40k-60k",
        "affordability_ratio": round(1200.0 / 55000.0, 4),
        "trigger_threshold": AFFORDABILITY_RATIO_FAIL,
        "outcome": "pass",
        "action_taken": None,
        "reviewed_by": None,
    },
}


def _compute_outcome(total_losses: float, annual_income: Optional[float]) -> dict:
    """Return affordability outcome and ratio."""
    if annual_income and annual_income > 0:
        ratio = round(total_losses / annual_income, 4)
        outcome = "fail" if ratio > AFFORDABILITY_RATIO_FAIL else "pass"
    elif total_losses > LOSS_THRESHOLD_90_DAYS:
        ratio = 0.0
        outcome = "review"
    else:
        ratio = 0.0
        outcome = "pass"
    return {"affordability_ratio": ratio, "outcome": outcome}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/player/{player_id}", response_model=List[AffordabilityCheck], summary="Get affordability history for a player")
async def get_player_affordability(
    player_id: str,
    current: TokenData = Depends(require_permission("compliance:read")),
) -> List[AffordabilityCheck]:
    checks = [AffordabilityCheck(**c) for c in _CHECKS.values() if c["player_id"] == player_id]
    return checks


@router.post("/run/{player_id}", response_model=AffordabilityCheck, summary="Run an affordability check for a player")
async def run_affordability_check(
    player_id: str,
    total_deposits: float = Query(..., ge=0),
    total_losses: float = Query(..., ge=0),
    stated_annual_income: Optional[float] = Query(None, ge=0),
    income_band: Optional[str] = Query(None),
    period_days: int = Query(90, ge=1, le=365),
    current: TokenData = Depends(require_permission("compliance:write")),
) -> AffordabilityCheck:
    outcome_data = _compute_outcome(total_losses, stated_annual_income)
    check_id = f"AFC-{uuid.uuid4().hex[:6].upper()}"
    raw = {
        "check_id": check_id,
        "player_id": player_id,
        "checked_at": datetime.now(timezone.utc),
        "period_days": period_days,
        "total_deposits": total_deposits,
        "total_losses": total_losses,
        "stated_annual_income": stated_annual_income,
        "income_band": income_band,
        "affordability_ratio": outcome_data["affordability_ratio"],
        "trigger_threshold": AFFORDABILITY_RATIO_FAIL,
        "outcome": outcome_data["outcome"],
        "action_taken": None,
        "reviewed_by": current.username,
    }
    _CHECKS[check_id] = raw
    return AffordabilityCheck(**raw)


@router.patch("/{check_id}/action", summary="Record action taken after affordability check")
async def record_action(
    check_id: str,
    action: str = Query(..., min_length=5),
    current: TokenData = Depends(require_permission("compliance:write")),
) -> dict:
    raw = _CHECKS.get(check_id)
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Check not found")
    raw["action_taken"] = action
    raw["reviewed_by"] = current.username
    return {
        "check_id": check_id,
        "action_taken": action,
        "recorded_by": current.username,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/flagged", summary="List players flagged by affordability checks")
async def list_flagged(
    outcome: str = Query("fail", regex="^(fail|review|pass)$"),
    current: TokenData = Depends(require_permission("compliance:read")),
) -> List[dict]:
    flagged = [
        {"check_id": c["check_id"], "player_id": c["player_id"],
         "outcome": c["outcome"], "affordability_ratio": c["affordability_ratio"],
         "checked_at": c["checked_at"].isoformat()}
        for c in _CHECKS.values()
        if c["outcome"] == outcome
    ]
    return flagged
