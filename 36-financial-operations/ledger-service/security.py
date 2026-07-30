# Companion code for "The Backend of Luck" - Chapter 36, Financial Operations.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Service-to-service bearer token authentication and role-based access control.

A token is `base64url(json_claims).base64url(sig)` where
sig = HMAC-SHA256(base64url(json_claims), LEDGER_JWT_SECRET). Claims are
only trusted after the signature is recomputed and compared in constant time
and the expiry is checked. Mirrors the RBAC dependency pattern in chapter
33's backoffice-admin/security/access_control.py (Depends(require_roles(...))),
adapted to a stdlib HMAC token so this service carries no extra dependency.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import time
from enum import Enum
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

SECRET_KEY: str = os.getenv("LEDGER_JWT_SECRET", "")

_bearer_scheme = HTTPBearer(auto_error=False)


class ServiceRole(str, Enum):
    SERVICE = "service"  # trusted internal caller (payments platform, event translators)
    ADMIN = "admin"       # backoffice operator making manual corrections


class Principal(BaseModel):
    sub: str
    role: ServiceRole


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _verify_token(token: str, secret: str) -> Optional[dict]:
    try:
        payload_b64, sig_b64 = token.split(".", 1)
    except ValueError:
        return None

    expected_sig = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).digest()
    try:
        provided_sig = _b64url_decode(sig_b64)
    except (ValueError, binascii.Error):
        return None
    if not hmac.compare_digest(expected_sig, provided_sig):
        return None

    try:
        claims = json.loads(_b64url_decode(payload_b64))
    except (ValueError, binascii.Error, json.JSONDecodeError):
        return None
    if not claims.get("sub") or claims.get("role") not in {r.value for r in ServiceRole}:
        return None
    if not isinstance(claims.get("exp"), (int, float)) or claims["exp"] < time.time():
        return None
    return claims


async def get_current_principal(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> Principal:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    if not SECRET_KEY or len(SECRET_KEY) < 16:
        # Fail closed: without a real signing secret we cannot trust any token.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication not configured")

    claims = _verify_token(credentials.credentials, SECRET_KEY)
    if claims is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    return Principal(sub=claims["sub"], role=ServiceRole(claims["role"]))


def require_roles(roles: list[ServiceRole]):
    """FastAPI dependency factory that enforces one of the given roles."""

    async def _dependency(principal: Principal = Depends(get_current_principal)) -> Principal:
        if principal.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient role. Required: {[r.value for r in roles]}",
            )
        return principal

    return _dependency
