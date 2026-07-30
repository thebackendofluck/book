# Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Bearer-JWT auth dependencies for this microservice.

Enforces that the caller is either the CPF owner (token `cpf` claim matches the
path) or holds an operator-class role. Fails closed if JWT_SECRET is unset.
"""
from __future__ import annotations

import os

import jwt
from fastapi import Depends, HTTPException, Path, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=True)
_OPERATOR_ROLES = {"operator", "compliance", "admin"}


def _secret() -> str:
    return os.environ.get("JWT_SECRET", "")


def get_claims(cred: HTTPAuthorizationCredentials = Depends(_bearer)) -> dict:
    secret = _secret()
    if not secret:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "auth not configured")
    try:
        return jwt.decode(cred.credentials, secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token") from exc


def require_cpf_access(cpf: str = Path(...), claims: dict = Depends(get_claims)) -> dict:
    """Allow the CPF owner or an operator; reject any other caller."""
    if claims.get("role", "") in _OPERATOR_ROLES:
        return claims
    if str(claims.get("cpf", "")) == str(cpf):
        return claims
    raise HTTPException(status.HTTP_403_FORBIDDEN, "not authorized for this CPF")


def require_operator(claims: dict = Depends(get_claims)) -> dict:
    """Operator-only actions (status changes, PII admin)."""
    if claims.get("role", "") not in _OPERATOR_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "operator role required")
    return claims
