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
Role-based access control — admin user management and permission inspection.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from passlib.context import CryptContext

from auth import (
    AdminRole,
    TokenData,
    _ADMIN_STORE,
    get_password_hash,
    require_permission,
    require_roles,
    ROLE_PERMISSIONS,
)
from models import AdminUser

router = APIRouter(prefix="/access-control", tags=["Access Control"])

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _raw_to_admin_user(raw: dict) -> AdminUser:
    return AdminUser(
        admin_id=raw["admin_id"],
        username=raw["username"],
        email=raw["email"],
        role=raw["role"],
        is_active=raw.get("is_active", True),
        created_at=raw.get("created_at", datetime.now(timezone.utc)),
        last_login=raw.get("last_login"),
        allowed_ips=raw.get("allowed_ips", []),
        two_fa_enabled=raw.get("two_fa_enabled", True),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/users", response_model=List[AdminUser], summary="List all admin users")
async def list_admin_users(
    role: Optional[AdminRole] = Query(None),
    is_active: Optional[bool] = Query(None),
    current: TokenData = Depends(require_roles([AdminRole.SUPER_ADMIN])),
) -> List[AdminUser]:
    results = list(_ADMIN_STORE.values())
    if role:
        results = [u for u in results if u["role"] == role]
    if is_active is not None:
        results = [u for u in results if u.get("is_active", True) == is_active]
    return [_raw_to_admin_user(u) for u in results]


@router.get("/users/{admin_id}", response_model=AdminUser, summary="Get a specific admin user")
async def get_admin_user(
    admin_id: str,
    current: TokenData = Depends(require_roles([AdminRole.SUPER_ADMIN])),
) -> AdminUser:
    for raw in _ADMIN_STORE.values():
        if raw["admin_id"] == admin_id:
            return _raw_to_admin_user(raw)
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Admin user not found")


@router.post("/users", response_model=AdminUser, summary="Create a new admin user")
async def create_admin_user(
    username: str = Query(..., min_length=3),
    email: str = Query(...),
    role: AdminRole = Query(...),
    password: str = Query(..., min_length=10),
    current: TokenData = Depends(require_roles([AdminRole.SUPER_ADMIN])),
) -> AdminUser:
    if username in _ADMIN_STORE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Username '{username}' already exists",
        )
    admin_id = f"adm-{uuid.uuid4().hex[:6]}"
    now = datetime.now(timezone.utc)
    raw = {
        "admin_id": admin_id,
        "username": username,
        "email": email,
        "hashed_password": get_password_hash(password),
        "role": role,
        "is_active": True,
        "created_at": now,
        "last_login": None,
        "allowed_ips": [],
        "two_fa_enabled": True,
    }
    _ADMIN_STORE[username] = raw
    return _raw_to_admin_user(raw)


@router.patch("/users/{admin_id}/deactivate", summary="Deactivate an admin user")
async def deactivate_admin_user(
    admin_id: str,
    current: TokenData = Depends(require_roles([AdminRole.SUPER_ADMIN])),
) -> dict:
    for raw in _ADMIN_STORE.values():
        if raw["admin_id"] == admin_id:
            if raw["admin_id"] == current.admin_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot deactivate your own account",
                )
            raw["is_active"] = False
            return {
                "admin_id": admin_id,
                "is_active": False,
                "deactivated_by": current.username,
                "deactivated_at": datetime.now(timezone.utc).isoformat(),
            }
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Admin user not found")


@router.patch("/users/{admin_id}/role", summary="Change an admin user's role")
async def change_admin_role(
    admin_id: str,
    new_role: AdminRole,
    current: TokenData = Depends(require_roles([AdminRole.SUPER_ADMIN])),
) -> dict:
    for raw in _ADMIN_STORE.values():
        if raw["admin_id"] == admin_id:
            old_role = raw["role"]
            raw["role"] = new_role
            return {
                "admin_id": admin_id,
                "old_role": old_role.value if hasattr(old_role, "value") else old_role,
                "new_role": new_role.value,
                "changed_by": current.username,
                "changed_at": datetime.now(timezone.utc).isoformat(),
            }
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Admin user not found")


@router.get("/permissions", summary="List permissions for all roles")
async def list_permissions(
    role: Optional[AdminRole] = Query(None),
    current: TokenData = Depends(require_permission("admin:read")),
) -> dict:
    if role:
        perms = ROLE_PERMISSIONS.get(role, set())
        return {role.value: sorted(list(perms))}
    return {r.value: sorted(list(p)) for r, p in ROLE_PERMISSIONS.items()}


@router.get("/my-permissions", summary="Show current user's permissions")
async def my_permissions(
    current: TokenData = Depends(require_permission("dashboard:read")),
) -> dict:
    perms = ROLE_PERMISSIONS.get(current.role, set())
    return {
        "admin_id": current.admin_id,
        "username": current.username,
        "role": current.role.value,
        "permissions": sorted(list(perms)),
    }
