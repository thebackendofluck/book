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
Dormant account handling — detection, outreach, and closure.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from auth import TokenData, require_permission
from models import PlayerStatus

router = APIRouter(prefix="/dormant", tags=["Dormant Accounts"])

# ---------------------------------------------------------------------------
# Dormancy configuration (days of inactivity)
# ---------------------------------------------------------------------------

DORMANT_WARNING_DAYS = 365
DORMANT_CLOSE_DAYS = 730  # 2 years — common UKGC practice


# ---------------------------------------------------------------------------
# Simulated dormant player store
# ---------------------------------------------------------------------------

_DORMANT_RECORDS: Dict[str, dict] = {
    "PLR-099": {
        "player_id": "PLR-099",
        "username": "old_user_99",
        "email": "old99@example.com",
        "last_login": datetime(2021, 12, 1, tzinfo=timezone.utc),
        "balance": 12.50,
        "bonus_balance": 0.0,
        "status": "dormant",
        "outreach_sent": False,
        "outreach_sent_at": None,
        "closure_triggered": False,
        "closure_triggered_at": None,
        "days_inactive": (datetime.now(timezone.utc) - datetime(2021, 12, 1, tzinfo=timezone.utc)).days,
    },
    "PLR-100": {
        "player_id": "PLR-100",
        "username": "inactive_100",
        "email": "inactive100@example.com",
        "last_login": datetime(2023, 1, 20, tzinfo=timezone.utc),
        "balance": 0.0,
        "bonus_balance": 0.0,
        "status": "dormant",
        "outreach_sent": True,
        "outreach_sent_at": datetime(2024, 1, 21, tzinfo=timezone.utc),
        "closure_triggered": False,
        "closure_triggered_at": None,
        "days_inactive": (datetime.now(timezone.utc) - datetime(2023, 1, 20, tzinfo=timezone.utc)).days,
    },
}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/", summary="List dormant accounts")
async def list_dormant(
    min_days_inactive: int = Query(DORMANT_WARNING_DAYS, ge=1),
    outreach_sent: Optional[bool] = Query(None),
    closure_triggered: Optional[bool] = Query(None),
    current: TokenData = Depends(require_permission("players:read")),
) -> List[dict]:
    results = [
        r for r in _DORMANT_RECORDS.values()
        if r["days_inactive"] >= min_days_inactive
    ]
    if outreach_sent is not None:
        results = [r for r in results if r["outreach_sent"] == outreach_sent]
    if closure_triggered is not None:
        results = [r for r in results if r["closure_triggered"] == closure_triggered]
    return results


@router.post("/{player_id}/outreach", summary="Trigger outreach communication to dormant player")
async def trigger_outreach(
    player_id: str,
    channel: str = Query("email", regex="^(email|sms|post)$"),
    current: TokenData = Depends(require_permission("players:write")),
) -> dict:
    record = _DORMANT_RECORDS.get(player_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dormant record not found")
    record["outreach_sent"] = True
    record["outreach_sent_at"] = datetime.now(timezone.utc)
    return {
        "player_id": player_id,
        "channel": channel,
        "outreach_sent_at": record["outreach_sent_at"].isoformat(),
        "triggered_by": current.username,
        "message": f"Outreach via {channel} queued for {record['email']}",
    }


@router.post("/{player_id}/close", summary="Trigger dormant account closure")
async def close_dormant_account(
    player_id: str,
    reason: str = Query(..., min_length=10),
    return_balance: bool = Query(True),
    current: TokenData = Depends(require_permission("players:write")),
) -> dict:
    record = _DORMANT_RECORDS.get(player_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dormant record not found")
    if record["days_inactive"] < DORMANT_CLOSE_DAYS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Account inactive for {record['days_inactive']} days; "
                   f"minimum {DORMANT_CLOSE_DAYS} days required for closure",
        )
    record["closure_triggered"] = True
    record["closure_triggered_at"] = datetime.now(timezone.utc)
    record["status"] = "closed"
    balance_action = f"Return £{record['balance']:.2f} to player via last payment method" if return_balance else "Balance forfeited per T&Cs"
    return {
        "player_id": player_id,
        "status": "closed",
        "balance_action": balance_action,
        "reason": reason,
        "closed_by": current.username,
        "closed_at": record["closure_triggered_at"].isoformat(),
    }


@router.get("/stats", summary="Dormant account statistics")
async def dormant_stats(
    current: TokenData = Depends(require_permission("players:read")),
) -> dict:
    records = list(_DORMANT_RECORDS.values())
    now = datetime.now(timezone.utc)
    return {
        "total_dormant": len(records),
        "pending_outreach": sum(1 for r in records if not r["outreach_sent"]),
        "outreach_sent": sum(1 for r in records if r["outreach_sent"]),
        "eligible_for_closure": sum(1 for r in records if r["days_inactive"] >= DORMANT_CLOSE_DAYS),
        "total_dormant_balance": round(sum(r["balance"] for r in records), 2),
        "as_of": now.isoformat(),
    }
