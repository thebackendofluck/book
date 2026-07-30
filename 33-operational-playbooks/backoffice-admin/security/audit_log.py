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
Audit log — every admin action is recorded with full context.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Request

from auth import TokenData, require_permission
from models import AuditLogEntry

router = APIRouter(prefix="/audit-log", tags=["Audit Log"])

# ---------------------------------------------------------------------------
# In-memory audit log store (replace with append-only DB table in production)
# ---------------------------------------------------------------------------

_AUDIT_LOG: Dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Core logging function — call this from all write endpoints
# ---------------------------------------------------------------------------


def log_action(
    admin_id: str,
    admin_username: str,
    action: str,
    resource_type: str,
    resource_id: Optional[str] = None,
    details: Optional[dict] = None,
    ip_address: str = "unknown",
    user_agent: Optional[str] = None,
    outcome: str = "success",
) -> AuditLogEntry:
    """
    Record an admin action to the audit log.

    Call this from any endpoint that mutates state::

        log_action(
            admin_id=current.admin_id,
            admin_username=current.username,
            action="kyc_document_approved",
            resource_type="kyc_document",
            resource_id=document_id,
            details={"document_type": "passport"},
            ip_address=request.client.host,
        )
    """
    log_id = f"LOG-{uuid.uuid4().hex[:10].upper()}"
    entry = {
        "log_id": log_id,
        "admin_id": admin_id,
        "admin_username": admin_username,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "details": details or {},
        "ip_address": ip_address,
        "user_agent": user_agent,
        "timestamp": datetime.now(timezone.utc),
        "outcome": outcome,
    }
    _AUDIT_LOG[log_id] = entry
    return AuditLogEntry(**entry)


# Pre-populate with some sample entries
log_action(
    admin_id="adm-001",
    admin_username="admin",
    action="player_status_updated",
    resource_type="player",
    resource_id="PLR-099",
    details={"old_status": "active", "new_status": "suspended", "reason": "Suspicious activity"},
    ip_address="10.0.0.1",
)
log_action(
    admin_id="adm-002",
    admin_username="compliance_agent",
    action="kyc_document_approved",
    resource_type="kyc_document",
    resource_id="DOC-001",
    details={"document_type": "passport", "player_id": "PLR-001"},
    ip_address="10.0.0.2",
)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/", response_model=List[AuditLogEntry], summary="Query audit log")
async def query_audit_log(
    admin_id: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    resource_type: Optional[str] = Query(None),
    resource_id: Optional[str] = Query(None),
    outcome: Optional[str] = Query(None, regex="^(success|failure)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current: TokenData = Depends(require_permission("admin:read")),
) -> List[AuditLogEntry]:
    import math
    results = list(_AUDIT_LOG.values())
    results.sort(key=lambda x: x["timestamp"], reverse=True)

    if admin_id:
        results = [r for r in results if r["admin_id"] == admin_id]
    if action:
        results = [r for r in results if action.lower() in r["action"].lower()]
    if resource_type:
        results = [r for r in results if r["resource_type"] == resource_type]
    if resource_id:
        results = [r for r in results if r["resource_id"] == resource_id]
    if outcome:
        results = [r for r in results if r["outcome"] == outcome]

    start = (page - 1) * page_size
    return [AuditLogEntry(**r) for r in results[start: start + page_size]]


@router.get("/player/{player_id}", response_model=List[AuditLogEntry], summary="Audit trail for a specific player")
async def player_audit_trail(
    player_id: str,
    current: TokenData = Depends(require_permission("players:read")),
) -> List[AuditLogEntry]:
    results = [
        AuditLogEntry(**r) for r in _AUDIT_LOG.values()
        if r.get("resource_id") == player_id or r.get("details", {}).get("player_id") == player_id
    ]
    results.sort(key=lambda x: x.timestamp, reverse=True)
    return results


@router.get("/stats", summary="Audit log statistics")
async def audit_stats(
    current: TokenData = Depends(require_permission("admin:read")),
) -> dict:
    entries = list(_AUDIT_LOG.values())
    action_counts: dict = {}
    for e in entries:
        action_counts[e["action"]] = action_counts.get(e["action"], 0) + 1
    return {
        "total_entries": len(entries),
        "failures": sum(1 for e in entries if e["outcome"] == "failure"),
        "top_actions": dict(sorted(action_counts.items(), key=lambda x: x[1], reverse=True)[:10]),
    }
