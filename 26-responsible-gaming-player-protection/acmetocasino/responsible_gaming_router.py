# Companion code for "The Backend of Luck" - Chapter 26, Responsible Gaming and Player Protection Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Responsible Gaming HTTP endpoints.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.rbac import get_current_user, require_role  # ty: ignore[unresolved-import]
from app.responsible_gaming.models import (  # ty: ignore[unresolved-import]
    DepositLimit,
    DepositLimitRequest,
    RealityCheck,
    ResponsibleGamingStatus,
    SelfExclusion,
    SelfExclusionRequest,
)
from app.responsible_gaming import service  # ty: ignore[unresolved-import]

router = APIRouter(prefix="/responsible-gaming", tags=["Responsible Gaming"])


@router.get("/excluded", response_model=list[SelfExclusion])
def list_excluded(
    limit: int = Query(default=50, le=200),
    _user: dict = Depends(require_role("operator")),
):
    """List all currently self-excluded players. Requires operator role."""
    return service.get_all_active_exclusions(limit=limit)


@router.post(
    "/limits/{player_id}",
    response_model=DepositLimit,
    status_code=status.HTTP_201_CREATED,
)
def set_deposit_limit(
    player_id: uuid.UUID,
    body: DepositLimitRequest,
    _user: dict = Depends(get_current_user),
):
    """Set or replace a deposit limit for a player."""
    try:
        limit = service.set_limits(player_id, body.period, body.amount)
        return limit
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


@router.post(
    "/exclude/{player_id}",
    response_model=SelfExclusion,
    status_code=status.HTTP_201_CREATED,
)
def self_exclude(
    player_id: uuid.UUID,
    body: SelfExclusionRequest,
    _user: dict = Depends(get_current_user),
):
    """Self-exclude a player for a specified number of days."""
    try:
        exclusion = service.self_exclude(
            player_id, body.duration_days, body.reason, body.jurisdiction
        )
        return exclusion
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


@router.get("/status/{player_id}", response_model=ResponsibleGamingStatus)
def get_status(
    player_id: uuid.UUID,
    _user: dict = Depends(get_current_user),
):
    """Get the full responsible gaming status for a player."""
    limits = service.get_active_limits(player_id)
    exclusion = service.get_active_exclusion(player_id)
    check = service.reality_check(player_id)

    return {
        "player_id": player_id,
        "limits": limits,
        "exclusion": exclusion,
        "reality_check": check,
    }
