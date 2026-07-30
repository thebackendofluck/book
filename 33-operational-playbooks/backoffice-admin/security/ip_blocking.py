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
IP allowlist and blocklist management for admin access control.
"""
from __future__ import annotations

import ipaddress
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from auth import AdminRole, TokenData, require_permission, require_roles
from models import IPBlockEntry

router = APIRouter(prefix="/ip-blocking", tags=["IP Blocking"])

# ---------------------------------------------------------------------------
# Simulated IP list store
# ---------------------------------------------------------------------------

_IP_ENTRIES: Dict[str, dict] = {
    "IP-001": {
        "entry_id": "IP-001",
        "ip_address": "10.0.0.0",
        "cidr": "10.0.0.0/8",
        "list_type": "allowlist",
        "reason": "Internal office network",
        "created_by": "admin",
        "created_at": datetime(2023, 1, 1, tzinfo=timezone.utc),
        "expires_at": None,
        "is_active": True,
    },
    "IP-002": {
        "entry_id": "IP-002",
        "ip_address": "185.220.101.35",
        "cidr": None,
        "list_type": "blocklist",
        "reason": "Repeated failed login attempts",
        "created_by": "admin",
        "created_at": datetime(2024, 1, 15, tzinfo=timezone.utc),
        "expires_at": None,
        "is_active": True,
    },
}


def _validate_ip(ip_str: str) -> str:
    """Validate and normalise an IP address string."""
    try:
        return str(ipaddress.ip_address(ip_str))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid IP address: {ip_str}",
        )


def _validate_cidr(cidr_str: str) -> str:
    """Validate and normalise a CIDR notation string."""
    try:
        net = ipaddress.ip_network(cidr_str, strict=False)
        return str(net)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid CIDR notation: {cidr_str}",
        )


def is_ip_blocked(ip_address: str) -> bool:
    """Check whether an IP is on the blocklist."""
    try:
        ip = ipaddress.ip_address(ip_address)
    except ValueError:
        return False
    for entry in _IP_ENTRIES.values():
        if not entry["is_active"] or entry["list_type"] != "blocklist":
            continue
        if entry["expires_at"] and entry["expires_at"] < datetime.now(timezone.utc):
            continue
        try:
            if entry["cidr"]:
                if ip in ipaddress.ip_network(entry["cidr"], strict=False):
                    return True
            elif str(ip) == entry["ip_address"]:
                return True
        except ValueError:
            continue
    return False


def is_ip_allowed(ip_address: str) -> bool:
    """Check whether an IP matches any allowlist entry."""
    try:
        ip = ipaddress.ip_address(ip_address)
    except ValueError:
        return False
    for entry in _IP_ENTRIES.values():
        if not entry["is_active"] or entry["list_type"] != "allowlist":
            continue
        if entry["expires_at"] and entry["expires_at"] < datetime.now(timezone.utc):
            continue
        try:
            if entry["cidr"]:
                if ip in ipaddress.ip_network(entry["cidr"], strict=False):
                    return True
            elif str(ip) == entry["ip_address"]:
                return True
        except ValueError:
            continue
    return False


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/", response_model=List[IPBlockEntry], summary="List all IP entries")
async def list_ip_entries(
    list_type: Optional[str] = Query(None, regex="^(allowlist|blocklist)$"),
    active_only: bool = Query(True),
    current: TokenData = Depends(require_permission("security:read")),
) -> List[IPBlockEntry]:
    results = list(_IP_ENTRIES.values())
    if list_type:
        results = [e for e in results if e["list_type"] == list_type]
    if active_only:
        now = datetime.now(timezone.utc)
        results = [
            e for e in results
            if e["is_active"] and (e["expires_at"] is None or e["expires_at"] > now)
        ]
    return [IPBlockEntry(**e) for e in results]


@router.post("/", response_model=IPBlockEntry, summary="Add an IP to the allowlist or blocklist")
async def add_ip_entry(
    ip_address: str = Query(...),
    list_type: str = Query(..., regex="^(allowlist|blocklist)$"),
    reason: str = Query(..., min_length=5),
    cidr: Optional[str] = Query(None),
    current: TokenData = Depends(require_permission("security:write")),
) -> IPBlockEntry:
    ip_address = _validate_ip(ip_address)
    cidr_validated = _validate_cidr(cidr) if cidr else None

    entry_id = f"IP-{uuid.uuid4().hex[:6].upper()}"
    raw = {
        "entry_id": entry_id,
        "ip_address": ip_address,
        "cidr": cidr_validated,
        "list_type": list_type,
        "reason": reason,
        "created_by": current.username,
        "created_at": datetime.now(timezone.utc),
        "expires_at": None,
        "is_active": True,
    }
    _IP_ENTRIES[entry_id] = raw
    return IPBlockEntry(**raw)


@router.delete("/{entry_id}", summary="Remove an IP entry")
async def remove_ip_entry(
    entry_id: str,
    current: TokenData = Depends(require_permission("security:write")),
) -> dict:
    raw = _IP_ENTRIES.get(entry_id)
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="IP entry not found")
    raw["is_active"] = False
    return {
        "entry_id": entry_id,
        "status": "deactivated",
        "deactivated_by": current.username,
        "deactivated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/check", summary="Check whether an IP is blocked or allowed")
async def check_ip(
    ip: str = Query(...),
    current: TokenData = Depends(require_permission("security:read")),
) -> dict:
    _validate_ip(ip)
    return {
        "ip": ip,
        "is_blocked": is_ip_blocked(ip),
        "is_explicitly_allowed": is_ip_allowed(ip),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
