# Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Bearer-JWT auth dependencies for the Bonus Engine.

Every endpoint here is either player-facing (bound to a single CPF) or
service-to-service (campaign administration, settlement callbacks). This
module enforces that:

  - a player token may only act on its own CPF (`require_cpf_access`,
    `assert_cpf_or_operator`);
  - operator-class actions (campaign creation, SIGAP reporting, wagering
    credit from settlement) require an operator/service role
    (`require_operator`).

Fails closed if BONUS_JWT_SECRET is unset — the service refuses to
authenticate rather than accept an unsigned or unchecked request.
"""
from __future__ import annotations

import os

import jwt
from fastapi import Depends, HTTPException, Path, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=True)

# Service/staff roles allowed to act across CPFs: settlement posts wagering
# credit, operator/compliance/admin handle campaign and CS operations.
_OPERATOR_ROLES = {"operator", "settlement", "compliance", "admin"}


def _secret() -> str:
    return os.environ.get("BONUS_JWT_SECRET", "")


def get_claims(cred: HTTPAuthorizationCredentials = Depends(_bearer)) -> dict:
    secret = _secret()
    if not secret:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "auth not configured")
    try:
        return jwt.decode(cred.credentials, secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token") from exc


def assert_cpf_or_operator(claims: dict, cpf: str) -> None:
    """Allow the CPF owner or an operator; reject any other caller.

    Used directly (not as a FastAPI dependency) by endpoints where the CPF
    is only known after loading a resource, e.g. forfeit-by-bonus-id.
    """
    if claims.get("role", "") in _OPERATOR_ROLES:
        return
    if str(claims.get("cpf", "")) == str(cpf):
        return
    raise HTTPException(status.HTTP_403_FORBIDDEN, "not authorized for this CPF")


def require_cpf_access(cpf: str = Path(...), claims: dict = Depends(get_claims)) -> dict:
    """FastAPI dependency form of `assert_cpf_or_operator` for routes whose
    path already contains `{cpf}`."""
    assert_cpf_or_operator(claims, cpf)
    return claims


def require_operator(claims: dict = Depends(get_claims)) -> dict:
    """Operator/service-only actions: campaign admin, SIGAP reporting,
    settlement callbacks that post wagering credit."""
    if claims.get("role", "") not in _OPERATOR_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "operator role required")
    return claims
