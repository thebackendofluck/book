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
Responsible Gaming audit trail — triggers, interventions, and follow-ups.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from auth import TokenData, require_permission
from models import RGAuditEntry, RGTriggerType

router = APIRouter(prefix="/rg-audit", tags=["Responsible Gaming"])

# ---------------------------------------------------------------------------
# Simulated RG audit store
# ---------------------------------------------------------------------------

_RG_ENTRIES: Dict[str, dict] = {
    "RGA-001": {
        "audit_id": "RGA-001",
        "player_id": "PLR-001",
        "trigger_type": RGTriggerType.SPEND_VELOCITY.value,
        "triggered_at": datetime(2024, 1, 20, tzinfo=timezone.utc),
        "triggered_by": "system",
        "action_taken": "Contacted player via email, offered deposit limit reduction",
        "outcome": "Player set self-imposed limit",
        "follow_up_required": False,
        "follow_up_due": None,
        "resolved_at": datetime(2024, 1, 22, tzinfo=timezone.utc),
    },
    "RGA-002": {
        "audit_id": "RGA-002",
        "player_id": "PLR-002",
        "trigger_type": RGTriggerType.FAILED_AFFORDABILITY.value,
        "triggered_at": datetime(2024, 2, 16, tzinfo=timezone.utc),
        "triggered_by": "system",
        "action_taken": "Account temporarily restricted pending SOW review",
        "outcome": None,
        "follow_up_required": True,
        "follow_up_due": datetime(2024, 3, 14, tzinfo=timezone.utc),
        "resolved_at": None,
    },
}

# ---------------------------------------------------------------------------
# UKGC / safer gambling interaction codes
# ---------------------------------------------------------------------------

INTERACTION_ACTIONS = [
    "email_sent",
    "sms_sent",
    "deposit_limit_applied",
    "cool_off_applied",
    "self_exclusion_applied",
    "account_restricted",
    "account_closed",
    "telephone_contact",
    "sow_requested",
    "affordability_check_triggered",
]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/", response_model=List[RGAuditEntry], summary="List all RG audit entries")
async def list_rg_entries(
    player_id: Optional[str] = Query(None),
    trigger_type: Optional[RGTriggerType] = Query(None),
    follow_up_required: Optional[bool] = Query(None),
    unresolved: Optional[bool] = Query(None),
    current: TokenData = Depends(require_permission("compliance:read")),
) -> List[RGAuditEntry]:
    results = list(_RG_ENTRIES.values())
    if player_id:
        results = [r for r in results if r["player_id"] == player_id]
    if trigger_type:
        results = [r for r in results if r["trigger_type"] == trigger_type.value]
    if follow_up_required is not None:
        results = [r for r in results if r["follow_up_required"] == follow_up_required]
    if unresolved is True:
        results = [r for r in results if r["resolved_at"] is None]
    return [RGAuditEntry(**r) for r in results]


@router.get("/{audit_id}", response_model=RGAuditEntry, summary="Get a specific RG audit entry")
async def get_rg_entry(
    audit_id: str,
    current: TokenData = Depends(require_permission("compliance:read")),
) -> RGAuditEntry:
    raw = _RG_ENTRIES.get(audit_id)
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="RG audit entry not found")
    return RGAuditEntry(**raw)


@router.post("/record", response_model=RGAuditEntry, summary="Record a new RG intervention")
async def record_intervention(
    player_id: str,
    trigger_type: RGTriggerType,
    action_taken: str = Query(..., min_length=10),
    follow_up_required: bool = Query(False),
    follow_up_days: Optional[int] = Query(None, ge=1, le=365),
    current: TokenData = Depends(require_permission("compliance:write")),
) -> RGAuditEntry:
    from datetime import timedelta
    audit_id = f"RGA-{uuid.uuid4().hex[:6].upper()}"
    now = datetime.now(timezone.utc)
    follow_up_due = (now + timedelta(days=follow_up_days)) if follow_up_required and follow_up_days else None
    raw = {
        "audit_id": audit_id,
        "player_id": player_id,
        "trigger_type": trigger_type.value,
        "triggered_at": now,
        "triggered_by": current.username,
        "action_taken": action_taken,
        "outcome": None,
        "follow_up_required": follow_up_required,
        "follow_up_due": follow_up_due,
        "resolved_at": None,
    }
    _RG_ENTRIES[audit_id] = raw
    return RGAuditEntry(**raw)


@router.patch("/{audit_id}/resolve", summary="Mark an RG intervention as resolved")
async def resolve_intervention(
    audit_id: str,
    outcome: str = Query(..., min_length=5),
    current: TokenData = Depends(require_permission("compliance:write")),
) -> RGAuditEntry:
    raw = _RG_ENTRIES.get(audit_id)
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="RG audit entry not found")
    if raw["resolved_at"] is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This RG entry is already resolved",
        )
    raw["resolved_at"] = datetime.now(timezone.utc)
    raw["outcome"] = outcome
    raw["follow_up_required"] = False
    return RGAuditEntry(**raw)


@router.get("/stats/overview", summary="RG audit statistics overview")
async def rg_stats(
    current: TokenData = Depends(require_permission("compliance:read")),
) -> dict:
    entries = list(_RG_ENTRIES.values())
    trigger_counts: dict = {}
    for e in entries:
        trigger_counts[e["trigger_type"]] = trigger_counts.get(e["trigger_type"], 0) + 1
    return {
        "total_interventions": len(entries),
        "unresolved": sum(1 for e in entries if e["resolved_at"] is None),
        "follow_ups_pending": sum(1 for e in entries if e["follow_up_required"]),
        "by_trigger_type": trigger_counts,
    }
