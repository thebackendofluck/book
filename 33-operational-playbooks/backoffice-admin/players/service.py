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
Player management service — search, view, and edit player accounts.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from auth import TokenData, require_permission
from models import (
    PlayerDetail,
    PlayerSearchRequest,
    PlayerSearchResponse,
    PlayerStatus,
    PlayerSummary,
)

router = APIRouter(prefix="/players", tags=["Players"])

# ---------------------------------------------------------------------------
# Simulated player store (replace with DB layer in production)
# ---------------------------------------------------------------------------

_PLAYERS: Dict[str, dict] = {
    "PLR-001": {
        "player_id": "PLR-001",
        "username": "john_doe",
        "email": "john.doe@example.com",
        "first_name": "John",
        "last_name": "Doe",
        "date_of_birth": "1985-06-15",
        "country_code": "GB",
        "currency": "GBP",
        "jurisdiction": "UKGC",
        "status": "active",
        "brand": "AcmetoCasino",
        "registered_at": datetime(2022, 1, 10, tzinfo=timezone.utc),
        "last_login": datetime(2024, 3, 1, tzinfo=timezone.utc),
        "kyc_status": "approved",
        "total_deposits": 5400.0,
        "total_withdrawals": 2200.0,
        "ggr_lifetime": 1800.0,
        "balance": 350.0,
        "bonus_balance": 0.0,
        "tags": ["vip", "high_value"],
        "phone": "+441234567890",
        "deposit_limit_daily": 200.0,
        "deposit_limit_weekly": 1000.0,
        "deposit_limit_monthly": 3000.0,
        "annual_income_band": "40k-60k",
        "occupation": "Software Engineer",
        "marketing_email_opt_in": True,
        "marketing_sms_opt_in": False,
        "affiliate_id": None,
        "notes": [],
    },
    "PLR-002": {
        "player_id": "PLR-002",
        "username": "jane_smith",
        "email": "jane.smith@example.com",
        "first_name": "Jane",
        "last_name": "Smith",
        "date_of_birth": "1990-11-23",
        "country_code": "GB",
        "currency": "GBP",
        "jurisdiction": "UKGC",
        "status": "pending_verification",
        "brand": "AcmetoCasino",
        "registered_at": datetime(2024, 2, 14, tzinfo=timezone.utc),
        "last_login": datetime(2024, 2, 14, tzinfo=timezone.utc),
        "kyc_status": "pending",
        "total_deposits": 100.0,
        "total_withdrawals": 0.0,
        "ggr_lifetime": 45.0,
        "balance": 55.0,
        "bonus_balance": 20.0,
        "tags": ["new"],
        "phone": "+447700900123",
        "deposit_limit_daily": None,
        "deposit_limit_weekly": None,
        "deposit_limit_monthly": None,
        "annual_income_band": None,
        "occupation": None,
        "marketing_email_opt_in": True,
        "marketing_sms_opt_in": True,
        "affiliate_id": "AFF-42",
        "notes": [],
    },
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _to_summary(raw: dict) -> PlayerSummary:
    return PlayerSummary(**{k: raw[k] for k in PlayerSummary.model_fields if k in raw})


def _to_detail(raw: dict) -> PlayerDetail:
    return PlayerDetail(**{k: raw[k] for k in PlayerDetail.model_fields if k in raw})


def _filter_players(req: PlayerSearchRequest) -> List[dict]:
    results = list(_PLAYERS.values())

    if req.player_id:
        results = [p for p in results if p["player_id"] == req.player_id]
    if req.email:
        results = [p for p in results if req.email.lower() in p["email"].lower()]
    if req.query:
        q = req.query.lower()
        results = [
            p for p in results
            if q in p["username"].lower()
            or q in p["email"].lower()
            or q in p["first_name"].lower()
            or q in p["last_name"].lower()
        ]
    if req.status:
        results = [p for p in results if p["status"] == req.status.value]
    if req.kyc_status:
        results = [p for p in results if p["kyc_status"] == req.kyc_status.value]
    if req.jurisdiction:
        results = [p for p in results if p["jurisdiction"] == req.jurisdiction.value]

    return results


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/search", response_model=PlayerSearchResponse, summary="Search players")
async def search_players(
    query: Optional[str] = Query(None, description="Free-text search (username, email, name)"),
    email: Optional[str] = Query(None),
    player_id: Optional[str] = Query(None),
    status: Optional[PlayerStatus] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    current: TokenData = Depends(require_permission("players:read")),
) -> PlayerSearchResponse:
    req = PlayerSearchRequest(
        query=query, email=email, player_id=player_id,
        status=status, page=page, page_size=page_size,
    )
    all_results = _filter_players(req)
    total = len(all_results)
    start = (req.page - 1) * req.page_size
    end = start + req.page_size
    page_results = all_results[start:end]
    import math
    return PlayerSearchResponse(
        players=[_to_summary(p) for p in page_results],
        total=total,
        page=req.page,
        page_size=req.page_size,
        total_pages=max(1, math.ceil(total / req.page_size)),
    )


@router.get("/{player_id}", response_model=PlayerDetail, summary="Get full player detail")
async def get_player(
    player_id: str,
    current: TokenData = Depends(require_permission("players:read")),
) -> PlayerDetail:
    raw = _PLAYERS.get(player_id)
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Player not found")
    return _to_detail(raw)


@router.patch("/{player_id}/status", summary="Update player status")
async def update_player_status(
    player_id: str,
    new_status: PlayerStatus,
    reason: str = Query(..., min_length=5),
    current: TokenData = Depends(require_permission("players:write")),
) -> dict:
    raw = _PLAYERS.get(player_id)
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Player not found")
    old_status = raw["status"]
    raw["status"] = new_status.value
    return {
        "player_id": player_id,
        "old_status": old_status,
        "new_status": new_status.value,
        "reason": reason,
        "updated_by": current.username,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.patch("/{player_id}/limits", summary="Set deposit limits on a player account")
async def set_deposit_limits(
    player_id: str,
    daily: Optional[float] = Query(None, ge=0),
    weekly: Optional[float] = Query(None, ge=0),
    monthly: Optional[float] = Query(None, ge=0),
    current: TokenData = Depends(require_permission("players:write")),
) -> dict:
    raw = _PLAYERS.get(player_id)
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Player not found")
    if daily is not None:
        raw["deposit_limit_daily"] = daily
    if weekly is not None:
        raw["deposit_limit_weekly"] = weekly
    if monthly is not None:
        raw["deposit_limit_monthly"] = monthly
    return {
        "player_id": player_id,
        "deposit_limit_daily": raw["deposit_limit_daily"],
        "deposit_limit_weekly": raw["deposit_limit_weekly"],
        "deposit_limit_monthly": raw["deposit_limit_monthly"],
        "updated_by": current.username,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/{player_id}/notes", summary="Add a note to a player account")
async def add_player_note(
    player_id: str,
    note: str = Query(..., min_length=5),
    current: TokenData = Depends(require_permission("players:write")),
) -> dict:
    raw = _PLAYERS.get(player_id)
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Player not found")
    entry = f"[{datetime.now(timezone.utc).isoformat()}] {current.username}: {note}"
    raw.setdefault("notes", []).append(entry)
    return {"player_id": player_id, "note_added": entry}
