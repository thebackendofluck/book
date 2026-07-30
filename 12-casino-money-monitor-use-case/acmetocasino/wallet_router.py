# Companion code for "The Backend of Luck" - Chapter 12, Real-Time Cash Flow Management for Online Casinos.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Wallet HTTP endpoints. Event-sourced: no direct balance updates.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.rbac import get_current_user
from app.wallet.models import Balance, TransactionRequest, WalletEvent
from app.wallet import service

router = APIRouter(tags=["Wallet"])


@router.get("/wallet/recent-events")
def recent_events(limit: int = Query(default=50, le=200)):
    """List the most recent wallet events across all players."""
    return service.get_recent_events(limit=limit)


@router.get("/wallet/{player_id}/balance", response_model=Balance)
def get_balance(player_id: uuid.UUID, _user: dict = Depends(get_current_user)):
    """Get the current computed balance for a player."""
    return service.get_balance(player_id)


@router.post(
    "/wallet/{player_id}/transaction",
    response_model=WalletEvent,
    status_code=status.HTTP_201_CREATED,
)
def create_transaction(
    player_id: uuid.UUID,
    body: TransactionRequest,
    _user: dict = Depends(get_current_user),
):
    """
    Append a wallet event (BET, WIN, DEPOSIT, WITHDRAWAL, BONUS_CREDIT, BONUS_DEBIT).
    Balance is never updated directly -- it is always computed from the event log.
    """
    try:
        event = service.create_event(
            player_id=player_id,
            event_type=body.event_type.value,
            amount=body.amount,
            currency=body.currency,
            reference_id=body.reference_id,
            metadata=body.metadata,
        )
        return event
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


@router.get("/wallet/{player_id}/history", response_model=list[WalletEvent])
def get_history(
    player_id: uuid.UUID,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    _user: dict = Depends(get_current_user),
):
    """Get the wallet event history for a player."""
    return service.get_history(player_id, limit=limit, offset=offset)
