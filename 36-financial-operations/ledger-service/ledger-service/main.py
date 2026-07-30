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
Ledger Service — FastAPI Application

REST API for the double-entry accounting ledger.
All financial truth flows through this service.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from event_translator import EventTranslator
from ledger import Ledger, UnbalancedPostingError
from models import (
    Balance,
    EntryRequest,
    InvariantResult,
    LedgerEntry,
    Posting,
    PostingRequest,
    ReconciliationResult,
    RunResult,
)
from reconciliation import ReconciliationEngine

logger = structlog.get_logger()

# Global instances (initialized at startup)
ledger: Ledger
translator: EventTranslator
reconciler: ReconciliationEngine


@asynccontextmanager
async def lifespan(app: FastAPI):
    global ledger, translator, reconciler
    ledger = Ledger()
    translator = EventTranslator(ledger)
    reconciler = ReconciliationEngine(ledger)
    logger.info("ledger_service_started")
    yield
    logger.info("ledger_service_stopped")


app = FastAPI(
    title="Ledger Service",
    description="Double-entry accounting ledger — source of financial truth",
    version="1.0.0",
    lifespan=lifespan,
)


# --- Request/Response models ---


class CreatePostingRequest(BaseModel):
    entry_group_id: uuid.UUID | None = None
    entries: list[EntryRequest]
    metadata: dict[str, str] = {}


class TranslateEventRequest(BaseModel):
    player_id: str | None = None
    amount: int
    psp: str | None = None
    game: str | None = None
    idempotency_key: uuid.UUID | None = None


class HealthResponse(BaseModel):
    status: str
    version: str


# --- Endpoints ---


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="healthy", version="1.0.0")


@app.post("/postings", response_model=Posting)
async def create_posting(request: CreatePostingRequest) -> Posting:
    """Create a balanced posting. Rejects unbalanced entries."""
    try:
        posting = await ledger.create_posting(
            entries=request.entries,
            entry_group_id=request.entry_group_id,
            metadata=request.metadata,
        )
        return posting
    except UnbalancedPostingError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/accounts/{account_id}/balance", response_model=Balance)
async def get_balance(account_id: str) -> Balance:
    """Get the current balance of an account."""
    return await ledger.get_account_balance(account_id)


@app.get("/accounts/{account_id}/statement", response_model=list[LedgerEntry])
async def get_statement(
    account_id: str,
    from_date: datetime | None = Query(None),
    to_date: datetime | None = Query(None),
) -> list[LedgerEntry]:
    """Get account statement (list of entries) with optional date filter."""
    return await ledger.get_account_statement(account_id, from_date, to_date)


@app.get("/invariant-check", response_model=InvariantResult)
async def invariant_check() -> InvariantResult:
    """Verify that ALL postings in the ledger are balanced."""
    return await ledger.verify_invariant()


@app.post("/translate/{event_type}", response_model=Posting)
async def translate_event(event_type: str, request: TranslateEventRequest) -> Posting:
    """Translate a business event into a balanced ledger posting."""
    handlers: dict[str, Any] = {
        "deposit": lambda r: translator.deposit(r.player_id, r.amount, r.psp),
        "withdrawal": lambda r: translator.withdrawal(r.player_id, r.amount, r.psp),
        "bet": lambda r: translator.bet(r.player_id, r.amount, r.game),
        "win": lambda r: translator.win(r.player_id, r.amount, r.game),
        "bonus_grant": lambda r: translator.bonus_grant(r.player_id, r.amount),
        "tax_withhold": lambda r: translator.tax_withhold(r.player_id, r.amount),
        "psp_settlement": lambda r: translator.psp_settlement(r.psp, r.amount),
    }

    handler = handlers.get(event_type)
    if not handler:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown event type: {event_type}. "
            f"Valid types: {list(handlers.keys())}",
        )

    try:
        return await handler(request)
    except (TypeError, AttributeError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Missing required fields for {event_type}: {exc}",
        )


@app.post("/reconcile/wallet/{player_id}", response_model=ReconciliationResult)
async def reconcile_wallet(player_id: str) -> ReconciliationResult:
    """Reconcile a player's wallet balance against the ledger."""
    return await reconciler.reconcile_wallet_vs_ledger(player_id)


@app.post("/reconcile/psp/{psp_name}", response_model=ReconciliationResult)
async def reconcile_psp(
    psp_name: str, date: str = Query(...)
) -> ReconciliationResult:
    """Reconcile a PSP's settlement report against the ledger."""
    return await reconciler.reconcile_psp_vs_ledger(psp_name, date)


@app.post("/reconcile/daily", response_model=RunResult)
async def reconcile_daily() -> RunResult:
    """Run daily reconciliation across all wallets and PSPs."""
    return await reconciler.daily_reconciliation_run()
