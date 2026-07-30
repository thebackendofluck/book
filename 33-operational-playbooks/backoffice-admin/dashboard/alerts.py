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
Dashboard alerts — pending actions queue (KYC, withdrawals, RG triggers, SOW).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from auth import TokenData, require_permission
from models import AlertItem

router = APIRouter(prefix="/alerts", tags=["Alerts"])

# ---------------------------------------------------------------------------
# Simulated alert store
# ---------------------------------------------------------------------------

_ALERTS: Dict[str, dict] = {
    "ALT-001": {
        "alert_id": "ALT-001",
        "alert_type": "kyc_review",
        "priority": "high",
        "player_id": "PLR-002",
        "message": "Player PLR-002 has pending KYC documents awaiting review (driving_licence, utility_bill)",
        "created_at": datetime(2024, 2, 14, tzinfo=timezone.utc),
        "assigned_to": None,
        "is_resolved": False,
    },
    "ALT-002": {
        "alert_id": "ALT-002",
        "alert_type": "withdrawal_hold",
        "priority": "medium",
        "player_id": "PLR-002",
        "message": "Withdrawal WD-002 for £80.00 is pending but KYC not verified",
        "created_at": datetime(2024, 3, 2, tzinfo=timezone.utc),
        "assigned_to": None,
        "is_resolved": False,
    },
    "ALT-003": {
        "alert_id": "ALT-003",
        "alert_type": "rg_trigger",
        "priority": "high",
        "player_id": "PLR-002",
        "message": "Player PLR-002 failed affordability check — spend velocity exceeds income band threshold",
        "created_at": datetime(2024, 2, 16, tzinfo=timezone.utc),
        "assigned_to": "compliance_agent",
        "is_resolved": False,
    },
    "ALT-004": {
        "alert_id": "ALT-004",
        "alert_type": "sow_overdue",
        "priority": "critical",
        "player_id": "PLR-002",
        "message": "SOW request SOW-002 is approaching deadline (14 days remaining)",
        "created_at": datetime(2024, 2, 28, tzinfo=timezone.utc),
        "assigned_to": "compliance_agent",
        "is_resolved": False,
    },
}

ALERT_TYPES = ["kyc_review", "withdrawal_hold", "rg_trigger", "sow_overdue", "complaint", "fraud_flag"]
PRIORITIES = ["low", "medium", "high", "critical"]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/", response_model=List[AlertItem], summary="List all active alerts")
async def list_alerts(
    alert_type: Optional[str] = Query(None),
    priority: Optional[str] = Query(None, regex="^(low|medium|high|critical)$"),
    assigned_to: Optional[str] = Query(None),
    include_resolved: bool = Query(False),
    current: TokenData = Depends(require_permission("dashboard:read")),
) -> List[AlertItem]:
    results = list(_ALERTS.values())
    if not include_resolved:
        results = [a for a in results if not a["is_resolved"]]
    if alert_type:
        results = [a for a in results if a["alert_type"] == alert_type]
    if priority:
        results = [a for a in results if a["priority"] == priority]
    if assigned_to:
        results = [a for a in results if a["assigned_to"] == assigned_to]
    # Sort: critical first, then high, medium, low
    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    results.sort(key=lambda x: (priority_order.get(x["priority"], 4), x["created_at"]))
    return [AlertItem(**a) for a in results]


@router.get("/{alert_id}", response_model=AlertItem, summary="Get a specific alert")
async def get_alert(
    alert_id: str,
    current: TokenData = Depends(require_permission("dashboard:read")),
) -> AlertItem:
    raw = _ALERTS.get(alert_id)
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
    return AlertItem(**raw)


@router.post("/", response_model=AlertItem, summary="Create a new alert")
async def create_alert(
    alert_type: str = Query(...),
    message: str = Query(..., min_length=10),
    priority: str = Query("medium", regex="^(low|medium|high|critical)$"),
    player_id: Optional[str] = Query(None),
    current: TokenData = Depends(require_permission("dashboard:read")),
) -> AlertItem:
    alert_id = f"ALT-{uuid.uuid4().hex[:6].upper()}"
    raw = {
        "alert_id": alert_id,
        "alert_type": alert_type,
        "priority": priority,
        "player_id": player_id,
        "message": message,
        "created_at": datetime.now(timezone.utc),
        "assigned_to": None,
        "is_resolved": False,
    }
    _ALERTS[alert_id] = raw
    return AlertItem(**raw)


@router.patch("/{alert_id}/assign", summary="Assign an alert to an admin user")
async def assign_alert(
    alert_id: str,
    assignee: str = Query(..., min_length=2),
    current: TokenData = Depends(require_permission("dashboard:read")),
) -> AlertItem:
    raw = _ALERTS.get(alert_id)
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
    raw["assigned_to"] = assignee
    return AlertItem(**raw)


@router.patch("/{alert_id}/resolve", summary="Mark an alert as resolved")
async def resolve_alert(
    alert_id: str,
    current: TokenData = Depends(require_permission("dashboard:read")),
) -> dict:
    raw = _ALERTS.get(alert_id)
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
    raw["is_resolved"] = True
    return {
        "alert_id": alert_id,
        "is_resolved": True,
        "resolved_by": current.username,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/stats/counts", summary="Alert count by type and priority")
async def alert_counts(
    current: TokenData = Depends(require_permission("dashboard:read")),
) -> dict:
    active = [a for a in _ALERTS.values() if not a["is_resolved"]]
    by_type: dict = {}
    by_priority: dict = {}
    for a in active:
        by_type[a["alert_type"]] = by_type.get(a["alert_type"], 0) + 1
        by_priority[a["priority"]] = by_priority.get(a["priority"], 0) + 1
    return {
        "total_active": len(active),
        "critical": by_priority.get("critical", 0),
        "high": by_priority.get("high", 0),
        "medium": by_priority.get("medium", 0),
        "low": by_priority.get("low", 0),
        "by_type": by_type,
    }
