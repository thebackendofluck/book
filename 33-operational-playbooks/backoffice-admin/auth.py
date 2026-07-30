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
JWT authentication and role-based access control middleware
for AcmetoCasino Backoffice Admin Platform.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import List, Optional, Set

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

from models import AdminRole, AdminUser, TokenData, TokenRequest, TokenResponse

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SECRET_KEY: str = os.getenv("BACKOFFICE_SECRET_KEY", "change-me-in-production-use-32-char-key")
ALGORITHM: str = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("TOKEN_EXPIRE_MINUTES", "60"))

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")

# ---------------------------------------------------------------------------
# Role permission matrix
# ---------------------------------------------------------------------------

ROLE_PERMISSIONS: dict[AdminRole, Set[str]] = {
    AdminRole.SUPER_ADMIN: {
        "players:read", "players:write", "players:delete",
        "kyc:read", "kyc:approve", "kyc:reject",
        "finance:read", "finance:write", "finance:approve",
        "compliance:read", "compliance:write",
        "security:read", "security:write",
        "crm:read", "crm:write",
        "dashboard:read",
        "admin:read", "admin:write",
    },
    AdminRole.COMPLIANCE: {
        "players:read",
        "kyc:read", "kyc:approve", "kyc:reject",
        "compliance:read", "compliance:write",
        "finance:read",
        "dashboard:read",
    },
    AdminRole.FINANCE: {
        "players:read",
        "kyc:read",
        "finance:read", "finance:write", "finance:approve",
        "compliance:read",
        "dashboard:read",
    },
    AdminRole.CS: {
        "players:read", "players:write",
        "kyc:read",
        "finance:read",
        "crm:read",
        "dashboard:read",
    },
    AdminRole.MARKETING: {
        "players:read",
        "crm:read", "crm:write",
        "dashboard:read",
    },
    AdminRole.READ_ONLY: {
        "players:read",
        "kyc:read",
        "finance:read",
        "compliance:read",
        "crm:read",
        "dashboard:read",
    },
}

# ---------------------------------------------------------------------------
# In-memory admin store (replace with DB in production)
# ---------------------------------------------------------------------------

_ADMIN_STORE: dict[str, dict] = {
    "admin": {
        "admin_id": "adm-001",
        "username": "admin",
        "email": "admin@acmetocasino.com",
        "hashed_password": pwd_context.hash(os.getenv("ADMIN_BOOTSTRAP_PASSWORD", "change-me-on-first-boot")),
        "role": AdminRole.SUPER_ADMIN,
        "is_active": True,
        "allowed_ips": [],
        "two_fa_enabled": False,
    },
    "compliance_agent": {
        "admin_id": "adm-002",
        "username": "compliance_agent",
        "email": "compliance@acmetocasino.com",
        "hashed_password": pwd_context.hash("Comply123!"),
        "role": AdminRole.COMPLIANCE,
        "is_active": True,
        "allowed_ips": [],
        "two_fa_enabled": False,
    },
    "finance_agent": {
        "admin_id": "adm-003",
        "username": "finance_agent",
        "email": "finance@acmetocasino.com",
        "hashed_password": pwd_context.hash("Finance123!"),
        "role": AdminRole.FINANCE,
        "is_active": True,
        "allowed_ips": [],
        "two_fa_enabled": False,
    },
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta if expires_delta else timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> TokenData:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        admin_id: str = payload.get("sub")
        username: str = payload.get("username")
        role_str: str = payload.get("role")
        if admin_id is None or role_str is None:
            raise credentials_exception
        return TokenData(admin_id=admin_id, username=username, role=AdminRole(role_str))
    except JWTError:
        raise credentials_exception


def authenticate_admin(username: str, password: str) -> Optional[dict]:
    admin = _ADMIN_STORE.get(username)
    if not admin:
        return None
    if not verify_password(password, admin["hashed_password"]):
        return None
    if not admin["is_active"]:
        return None
    return admin


# ---------------------------------------------------------------------------
# FastAPI dependency: get current token data
# ---------------------------------------------------------------------------


async def get_current_admin(token: str = Depends(oauth2_scheme)) -> TokenData:
    return decode_token(token)


# ---------------------------------------------------------------------------
# Permission checker factory
# ---------------------------------------------------------------------------


def require_permission(permission: str):
    """
    FastAPI dependency factory that enforces a specific permission.

    Usage::

        @router.get("/sensitive")
        async def endpoint(current=Depends(require_permission("finance:write"))):
            ...
    """

    async def _dependency(current: TokenData = Depends(get_current_admin)) -> TokenData:
        allowed = ROLE_PERMISSIONS.get(current.role, set())
        if permission not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission '{permission}' required. Your role: {current.role.value}",
            )
        return current

    return _dependency


def require_roles(roles: List[AdminRole]):
    """
    FastAPI dependency factory that enforces one of the given roles.
    """

    async def _dependency(current: TokenData = Depends(get_current_admin)) -> TokenData:
        if current.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient role. Required: {[r.value for r in roles]}",
            )
        return current

    return _dependency


# ---------------------------------------------------------------------------
# IP allowlist check
# ---------------------------------------------------------------------------


def check_ip_allowlist(request: Request, admin: dict) -> None:
    allowed_ips: List[str] = admin.get("allowed_ips", [])
    if not allowed_ips:
        return  # no restriction
    client_ip = request.client.host if request.client else "unknown"
    if client_ip not in allowed_ips:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied from IP {client_ip}",
        )


# ---------------------------------------------------------------------------
# Login endpoint helper (called from main.py)
# ---------------------------------------------------------------------------


def login(request_data: TokenRequest) -> TokenResponse:
    admin = authenticate_admin(request_data.username, request_data.password)
    if not admin:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = create_access_token(
        data={
            "sub": admin["admin_id"],
            "username": admin["username"],
            "role": admin["role"].value,
        }
    )
    return TokenResponse(
        access_token=access_token,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        role=admin["role"],
    )
