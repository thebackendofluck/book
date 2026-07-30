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
Player segmentation — dynamic and static segment management.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from auth import TokenData, require_permission
from models import PlayerSegment

router = APIRouter(prefix="/segments", tags=["Player Segments"])

# ---------------------------------------------------------------------------
# Simulated segment store
# ---------------------------------------------------------------------------

_SEGMENTS: Dict[str, dict] = {
    "SEG-001": {
        "segment_id": "SEG-001",
        "name": "High Value Players",
        "description": "Players with lifetime GGR over £5,000",
        "criteria": {"ggr_lifetime_min": 5000},
        "player_count": 87,
        "created_at": datetime(2023, 6, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "created_by": "admin",
        "is_dynamic": True,
    },
    "SEG-002": {
        "segment_id": "SEG-002",
        "name": "Recent Registrations",
        "description": "Players who registered in the last 30 days",
        "criteria": {"registered_within_days": 30},
        "player_count": 145,
        "created_at": datetime(2024, 1, 15, tzinfo=timezone.utc),
        "updated_at": datetime(2024, 3, 1, tzinfo=timezone.utc),
        "created_by": "admin",
        "is_dynamic": True,
    },
    "SEG-003": {
        "segment_id": "SEG-003",
        "name": "Lapsed Players",
        "description": "Players inactive for 60-180 days",
        "criteria": {"days_inactive_min": 60, "days_inactive_max": 180},
        "player_count": 312,
        "created_at": datetime(2023, 9, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2024, 2, 1, tzinfo=timezone.utc),
        "created_by": "marketing_admin",
        "is_dynamic": True,
    },
}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/", response_model=List[PlayerSegment], summary="List all player segments")
async def list_segments(
    is_dynamic: Optional[bool] = Query(None),
    current: TokenData = Depends(require_permission("crm:read")),
) -> List[PlayerSegment]:
    results = list(_SEGMENTS.values())
    if is_dynamic is not None:
        results = [s for s in results if s["is_dynamic"] == is_dynamic]
    return [PlayerSegment(**s) for s in results]


@router.get("/{segment_id}", response_model=PlayerSegment, summary="Get a specific segment")
async def get_segment(
    segment_id: str,
    current: TokenData = Depends(require_permission("crm:read")),
) -> PlayerSegment:
    raw = _SEGMENTS.get(segment_id)
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Segment not found")
    return PlayerSegment(**raw)


@router.post("/", response_model=PlayerSegment, summary="Create a new player segment")
async def create_segment(
    name: str = Query(..., min_length=3),
    description: Optional[str] = Query(None),
    is_dynamic: bool = Query(True),
    current: TokenData = Depends(require_permission("crm:write")),
) -> PlayerSegment:
    segment_id = f"SEG-{uuid.uuid4().hex[:6].upper()}"
    now = datetime.now(timezone.utc)
    raw = {
        "segment_id": segment_id,
        "name": name,
        "description": description,
        "criteria": {},
        "player_count": 0,
        "created_at": now,
        "updated_at": now,
        "created_by": current.username,
        "is_dynamic": is_dynamic,
    }
    _SEGMENTS[segment_id] = raw
    return PlayerSegment(**raw)


@router.patch("/{segment_id}", response_model=PlayerSegment, summary="Update segment criteria or metadata")
async def update_segment(
    segment_id: str,
    name: Optional[str] = Query(None),
    description: Optional[str] = Query(None),
    current: TokenData = Depends(require_permission("crm:write")),
) -> PlayerSegment:
    raw = _SEGMENTS.get(segment_id)
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Segment not found")
    if name:
        raw["name"] = name
    if description:
        raw["description"] = description
    raw["updated_at"] = datetime.now(timezone.utc)
    return PlayerSegment(**raw)


@router.delete("/{segment_id}", summary="Delete a player segment")
async def delete_segment(
    segment_id: str,
    current: TokenData = Depends(require_permission("crm:write")),
) -> dict:
    raw = _SEGMENTS.pop(segment_id, None)
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Segment not found")
    return {
        "segment_id": segment_id,
        "deleted_by": current.username,
        "deleted_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/{segment_id}/players", summary="Get player IDs in a segment (simulated)")
async def get_segment_players(
    segment_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    current: TokenData = Depends(require_permission("crm:read")),
) -> dict:
    raw = _SEGMENTS.get(segment_id)
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Segment not found")
    # Simulated player IDs
    all_ids = [f"PLR-{i:04d}" for i in range(1, raw["player_count"] + 1)]
    start = (page - 1) * page_size
    return {
        "segment_id": segment_id,
        "total": raw["player_count"],
        "page": page,
        "page_size": page_size,
        "player_ids": all_ids[start: start + page_size],
    }
