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
Marketing campaign CRUD — create, schedule, activate, and track campaigns.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from auth import TokenData, require_permission
from models import Campaign

router = APIRouter(prefix="/campaigns", tags=["Campaigns"])

# ---------------------------------------------------------------------------
# Simulated campaign store
# ---------------------------------------------------------------------------

_CAMPAIGNS: Dict[str, dict] = {
    "CAM-001": {
        "campaign_id": "CAM-001",
        "name": "March Madness Bonus",
        "brand": "AcmetoCasino",
        "segment_id": "SEG-003",
        "channel": "email",
        "status": "completed",
        "start_at": datetime(2024, 3, 1, tzinfo=timezone.utc),
        "end_at": datetime(2024, 3, 31, tzinfo=timezone.utc),
        "subject": "Your exclusive March offer is waiting!",
        "body": "Dear {first_name}, enjoy a 50% reload bonus this March...",
        "created_by": "admin",
        "created_at": datetime(2024, 2, 20, tzinfo=timezone.utc),
        "sent_count": 312,
        "open_count": 189,
        "click_count": 74,
    },
    "CAM-002": {
        "campaign_id": "CAM-002",
        "name": "Welcome Back SMS",
        "brand": "AcmetoCasino",
        "segment_id": "SEG-003",
        "channel": "sms",
        "status": "draft",
        "start_at": None,
        "end_at": None,
        "subject": None,
        "body": "Hi {first_name}! We miss you — come back and claim your free spins.",
        "created_by": "admin",
        "created_at": datetime(2024, 3, 10, tzinfo=timezone.utc),
        "sent_count": 0,
        "open_count": 0,
        "click_count": 0,
    },
}

VALID_TRANSITIONS = {
    "draft": {"scheduled", "cancelled"},
    "scheduled": {"active", "cancelled"},
    "active": {"completed", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/", response_model=List[Campaign], summary="List all campaigns")
async def list_campaigns(
    status_filter: Optional[str] = Query(None, alias="status"),
    channel: Optional[str] = Query(None),
    current: TokenData = Depends(require_permission("crm:read")),
) -> List[Campaign]:
    results = list(_CAMPAIGNS.values())
    if status_filter:
        results = [c for c in results if c["status"] == status_filter]
    if channel:
        results = [c for c in results if c["channel"] == channel]
    return [Campaign(**c) for c in results]


@router.get("/{campaign_id}", response_model=Campaign, summary="Get a specific campaign")
async def get_campaign(
    campaign_id: str,
    current: TokenData = Depends(require_permission("crm:read")),
) -> Campaign:
    raw = _CAMPAIGNS.get(campaign_id)
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found")
    return Campaign(**raw)


@router.post("/", response_model=Campaign, summary="Create a new campaign")
async def create_campaign(
    name: str = Query(..., min_length=3),
    channel: str = Query(..., regex="^(email|sms|push|in-app)$"),
    segment_id: Optional[str] = Query(None),
    body: str = Query(..., min_length=10),
    subject: Optional[str] = Query(None),
    current: TokenData = Depends(require_permission("crm:write")),
) -> Campaign:
    campaign_id = f"CAM-{uuid.uuid4().hex[:6].upper()}"
    now = datetime.now(timezone.utc)
    raw = {
        "campaign_id": campaign_id,
        "name": name,
        "brand": "AcmetoCasino",
        "segment_id": segment_id,
        "channel": channel,
        "status": "draft",
        "start_at": None,
        "end_at": None,
        "subject": subject,
        "body": body,
        "created_by": current.username,
        "created_at": now,
        "sent_count": 0,
        "open_count": 0,
        "click_count": 0,
    }
    _CAMPAIGNS[campaign_id] = raw
    return Campaign(**raw)


@router.patch("/{campaign_id}/status", response_model=Campaign, summary="Transition campaign status")
async def update_campaign_status(
    campaign_id: str,
    new_status: str = Query(..., regex="^(scheduled|active|completed|cancelled)$"),
    current: TokenData = Depends(require_permission("crm:write")),
) -> Campaign:
    raw = _CAMPAIGNS.get(campaign_id)
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found")
    allowed_next = VALID_TRANSITIONS.get(raw["status"], set())
    if new_status not in allowed_next:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot transition from '{raw['status']}' to '{new_status}'. "
                   f"Allowed: {sorted(allowed_next) or 'none'}",
        )
    raw["status"] = new_status
    if new_status == "active" and not raw["start_at"]:
        raw["start_at"] = datetime.now(timezone.utc)
    if new_status == "completed" and not raw["end_at"]:
        raw["end_at"] = datetime.now(timezone.utc)
    return Campaign(**raw)


@router.delete("/{campaign_id}", summary="Delete a draft campaign")
async def delete_campaign(
    campaign_id: str,
    current: TokenData = Depends(require_permission("crm:write")),
) -> dict:
    raw = _CAMPAIGNS.get(campaign_id)
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found")
    if raw["status"] != "draft":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only draft campaigns can be deleted",
        )
    del _CAMPAIGNS[campaign_id]
    return {
        "campaign_id": campaign_id,
        "deleted_by": current.username,
        "deleted_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/{campaign_id}/metrics", summary="Get campaign performance metrics")
async def campaign_metrics(
    campaign_id: str,
    current: TokenData = Depends(require_permission("crm:read")),
) -> dict:
    raw = _CAMPAIGNS.get(campaign_id)
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found")
    open_rate = round(raw["open_count"] / raw["sent_count"] * 100, 1) if raw["sent_count"] else 0
    click_rate = round(raw["click_count"] / raw["sent_count"] * 100, 1) if raw["sent_count"] else 0
    return {
        "campaign_id": campaign_id,
        "name": raw["name"],
        "status": raw["status"],
        "sent_count": raw["sent_count"],
        "open_count": raw["open_count"],
        "click_count": raw["click_count"],
        "open_rate_pct": open_rate,
        "click_rate_pct": click_rate,
    }
