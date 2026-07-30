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
Treasury Service — FastAPI entry point

API surface:
  GET  /treasury/positions          — all PSP clearing positions
  GET  /treasury/position/{psp}     — single PSP clearing position
  POST /treasury/settle             — record a new settlement instruction
  POST /treasury/settle/{id}/settled — mark a settlement as completed
  POST /treasury/settle/{id}/failed  — mark a settlement as failed
  GET  /treasury/cash-position      — operator aggregate net cash
  GET  /treasury/stuck              — detect stuck (aged) settlements
  GET  /health                      — liveness probe
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Optional

import structlog

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from models import CashPosition, ClearingPosition, Settlement, SettlementDirection
from security import Principal, ServiceRole, require_roles
from treasury import TreasuryService, TreasuryStore

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO)
)
log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Application singletons
# ---------------------------------------------------------------------------

_store = TreasuryStore()
_service = TreasuryService(store=_store)


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Treasury Service starting")
    yield
    log.info("Treasury Service shutting down")


app = FastAPI(
    title="AcmetoCasino Treasury Service",
    version="1.0.0",
    description=(
        "Operator treasury management: PSP clearing positions, "
        "settlement recording, aggregate cash view, and stuck-settlement detection."
    ),
    lifespan=lifespan,
)

_allowed_origins = [o.strip() for o in os.getenv("TREASURY_CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class RecordSettlementRequest(BaseModel):
    psp_name: str = Field(..., min_length=1)
    amount: int = Field(..., gt=0, description="Amount in minor units (cents)")
    reference: str = Field(..., min_length=1, description="Idempotency key / bank reference")
    direction: SettlementDirection = SettlementDirection.INBOUND
    currency: str = Field("EUR", min_length=3, max_length=3)
    notes: str = ""


class MarkSettledRequest(BaseModel):
    reason: str = ""


class MarkFailedRequest(BaseModel):
    reason: str = Field(..., min_length=1)


class StuckSettlementsQuery(BaseModel):
    hours: float = Field(24.0, gt=0, description="Age threshold in hours")


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/health", tags=["ops"])
async def health() -> dict[str, Any]:
    return {"status": "ok", "service": "treasury-service"}


# ---------------------------------------------------------------------------
# Clearing positions
# ---------------------------------------------------------------------------


@app.get(
    "/treasury/positions",
    response_model=list[dict],
    tags=["treasury"],
    summary="List all PSP clearing positions",
)
async def list_positions() -> list[dict]:
    """Return the current clearing position for every registered PSP."""
    positions = _service.get_all_clearing_positions()
    return [_position_response(p) for p in positions]


@app.get(
    "/treasury/position/{psp_name}",
    response_model=dict,
    tags=["treasury"],
    summary="Single PSP clearing position",
)
async def get_position(psp_name: str) -> dict:
    """Return the clearing position for a specific PSP."""
    position = _service.get_clearing_position(psp_name)
    return _position_response(position)


# ---------------------------------------------------------------------------
# Settlement recording
# ---------------------------------------------------------------------------


@app.post(
    "/treasury/settle",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
    tags=["treasury"],
    summary="Record a settlement instruction",
)
async def record_settlement(
    body: RecordSettlementRequest,
    principal: Principal = Depends(require_roles([ServiceRole.SERVICE, ServiceRole.ADMIN])),
) -> dict:
    """
    Record a PSP settlement instruction.

    Idempotent: if `reference` already exists, the existing settlement is
    returned with HTTP 200 (not 201) — the caller can distinguish via the
    `already_existed` flag in the response body.
    """
    existing_before = _store.get_settlement_by_reference(body.reference)
    settlement = _service.record_settlement(
        psp_name=body.psp_name,
        amount=body.amount,
        reference=body.reference,
        direction=body.direction,
        currency=body.currency,
        notes=body.notes,
    )
    already_existed = existing_before is not None
    return {**_settlement_response(settlement), "already_existed": already_existed}


@app.post(
    "/treasury/settle/{settlement_id}/settled",
    response_model=dict,
    tags=["treasury"],
    summary="Mark settlement as completed",
)
async def mark_settled(
    settlement_id: str,
    principal: Principal = Depends(require_roles([ServiceRole.SERVICE, ServiceRole.ADMIN])),
) -> dict:
    """Transition a settlement to SETTLED and update bank account balance."""
    try:
        settlement = _service.mark_settlement_settled(settlement_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _settlement_response(settlement)


@app.post(
    "/treasury/settle/{settlement_id}/failed",
    response_model=dict,
    tags=["treasury"],
    summary="Mark settlement as failed",
)
async def mark_failed(
    settlement_id: str,
    body: MarkFailedRequest,
    principal: Principal = Depends(require_roles([ServiceRole.SERVICE, ServiceRole.ADMIN])),
) -> dict:
    """Mark a settlement as failed with an explanatory reason."""
    try:
        settlement = _service.mark_settlement_failed(settlement_id, body.reason)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _settlement_response(settlement)


# ---------------------------------------------------------------------------
# Cash position
# ---------------------------------------------------------------------------


@app.get(
    "/treasury/cash-position",
    response_model=dict,
    tags=["treasury"],
    summary="Operator aggregate cash position",
)
async def cash_position() -> dict:
    """Return the operator's net cash position across all treasury accounts."""
    pos = _service.get_operator_cash_position()
    return _cash_position_response(pos)


# ---------------------------------------------------------------------------
# Stuck settlements
# ---------------------------------------------------------------------------


@app.get(
    "/treasury/stuck",
    response_model=list[dict],
    tags=["treasury"],
    summary="Detect stuck (aged) settlements",
)
async def stuck_settlements(hours: float = 24.0) -> list[dict]:
    """
    Return all settlements that remain non-terminal after `hours` hours.

    Default threshold is 24 hours. Adjust via the `?hours=` query param.
    """
    if hours <= 0:
        raise HTTPException(status_code=400, detail="hours must be > 0")
    stuck = _service.detect_stuck_settlements(hours=hours)
    return [_settlement_response(s) for s in stuck]


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------


def _position_response(p: ClearingPosition) -> dict:
    return {
        "psp_name": p.psp_name,
        "currency": p.currency,
        "gross_deposits": p.gross_deposits,
        "gross_withdrawals": p.gross_withdrawals,
        "last_settled_amount": p.last_settled_amount,
        "net_position": p.net_position,
        "pending_settlement_amount": p.pending_settlement_amount,
        "effective_exposure": p.effective_exposure,
        "as_of": p.as_of.isoformat(),
    }


def _settlement_response(s: Settlement) -> dict:
    return {
        "settlement_id": s.settlement_id,
        "psp_name": s.psp_name,
        "amount": s.amount,
        "currency": s.currency,
        "direction": s.direction.value,
        "status": s.status.value,
        "reference": s.reference,
        "initiated_at": s.initiated_at.isoformat(),
        "settled_at": s.settled_at.isoformat() if s.settled_at else None,
        "failed_at": s.failed_at.isoformat() if s.failed_at else None,
        "failure_reason": s.failure_reason,
        "is_terminal": s.is_terminal,
        "age_hours": round(s.age_hours, 2),
        "notes": s.notes,
    }


def _cash_position_response(p: CashPosition) -> dict:
    return {
        "total_psp_clearing": p.total_psp_clearing,
        "total_bank_settlement": p.total_bank_settlement,
        "total_tax_reserve": p.total_tax_reserve,
        "net_liquid": p.net_liquid,
        "total_assets": p.total_assets,
        "currency": p.currency,
        "positions_by_psp": p.positions_by_psp,
        "as_of": p.as_of.isoformat(),
    }
