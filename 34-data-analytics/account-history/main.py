# Companion code for "The Backend of Luck" - Chapter 34, Data and Analytics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
main.py — Account History Service (FastAPI).

Tracks and exposes player account history:
  - Transactions (deposits, withdrawals, bonuses)
  - Sessions (login/logout with duration)
  - Game rounds (bet, win, outcome, GGR)
  - Account event timeline (append-only log)
  - Aggregated stats (total deposits, GGR, play time)

Architecture mirrors the Scala ReservationService pattern (service layer
over repository) adapted for event-sourced gambling account data.

Endpoints:
  POST /events                             — Append a new account event
  GET  /players/{player_id}/events         — Full event timeline
  GET  /players/{player_id}/transactions   — Transaction history
  GET  /players/{player_id}/sessions       — Session history
  GET  /players/{player_id}/game-rounds    — Game round history
  GET  /players/{player_id}/stats          — Aggregated stats
  GET  /players/{player_id}/ggr-by-game    — GGR breakdown by game
  GET  /players/{player_id}/daily-ggr      — Daily GGR time series
  GET  /health
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

import psycopg2
import structlog
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from aggregator import Aggregator
from event_store import EventStore
from models import AccountEvent, EventType, HistoryFilter, PlayerStats
from query_service import QueryService

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(
        logging.getLevelName(os.environ.get("LOG_LEVEL", "INFO"))
    )
)
log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Account History Service",
    description=(
        "Event-sourced player account history: transactions, sessions, "
        "game rounds, and aggregated GGR / deposit statistics."
    ),
    version="1.0.0",
)


# ---------------------------------------------------------------------------
# Dependency: DB connection + services (created per-request for simplicity;
#             in production use a connection pool via asyncpg or SQLAlchemy)
# ---------------------------------------------------------------------------

def _get_conn():
    return psycopg2.connect(os.environ.get("DATABASE_URL", ""))


def _make_services():
    conn     = _get_conn()
    store    = EventStore(conn)
    query    = QueryService(store, conn)
    agg      = Aggregator(conn)
    return conn, store, query, agg


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class AppendEventRequest(BaseModel):
    player_id:   int
    event_type:  str
    amount:      float
    currency:    str  = Field(default="GBP", pattern="^[A-Z]{3}$")
    occurred_at: Optional[str] = None   # ISO-8601; defaults to now()
    reference:   Optional[str] = None
    metadata:    Optional[dict] = None


class AppendEventResponse(BaseModel):
    event_id: int
    player_id: int
    event_type: str


class TransactionOut(BaseModel):
    id:               int
    player_id:        int
    transaction_type: str
    amount:           float
    currency:         str
    status:           str
    initiated_at:     str
    completed_at:     Optional[str]
    payment_method:   Optional[str]
    external_ref:     Optional[str]


class SessionOut(BaseModel):
    id:               int
    player_id:        int
    session_token:    str
    started_at:       str
    ended_at:         Optional[str]
    ip_address:       Optional[str]
    device_type:      Optional[str]
    jurisdiction:     Optional[str]
    duration_seconds: Optional[float]


class GameRoundOut(BaseModel):
    id:          int
    player_id:   int
    game_id:     str
    game_name:   Optional[str]
    bet_amount:  float
    win_amount:  float
    currency:    str
    outcome:     str
    ggr:         float
    started_at:  str
    ended_at:    Optional[str]
    round_ref:   Optional[str]


class StatsOut(BaseModel):
    player_id:               int
    from_date:               str
    to_date:                 str
    total_deposits:          float
    total_withdrawals:       float
    net_deposits:            float
    total_bets:              float
    total_wins:              float
    ggr:                     float
    ngr:                     float
    bonus_awarded:           float
    bonus_wagered:           float
    currency:                str
    session_count:           int
    total_play_time_seconds: float


class PaginatedResponse(BaseModel):
    items:    list[Any]
    total:    int
    limit:    int
    offset:   int
    has_more: bool


# ---------------------------------------------------------------------------
# Helper converters
# ---------------------------------------------------------------------------

def _dt(ts) -> Optional[str]:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts.isoformat()
    return str(ts)


def _make_filter(
    player_id: int,
    from_date: Optional[str],
    to_date: Optional[str],
    event_types: Optional[str],
    min_amount: Optional[float],
    max_amount: Optional[float],
    limit: int,
    offset: int,
) -> HistoryFilter:
    def _parse_dt(s: str) -> datetime:
        # URL-decode: "+" may arrive as " " after query-string parsing
        return datetime.fromisoformat(s.replace(" ", "+"))

    return HistoryFilter(
        player_id=player_id,
        from_date=_parse_dt(from_date) if from_date else None,
        to_date=_parse_dt(to_date) if to_date else None,
        event_types=event_types.split(",") if event_types else None,
        min_amount=min_amount,
        max_amount=max_amount,
        limit=limit,
        offset=offset,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/events", response_model=AppendEventResponse, status_code=201,
          tags=["events"])
def append_event(body: AppendEventRequest) -> AppendEventResponse:
    """Append a new account event to the immutable event log."""
    try:
        event_type = EventType(body.event_type)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown event_type: {body.event_type}"
        )

    occurred = (
        datetime.fromisoformat(body.occurred_at)
        if body.occurred_at
        else datetime.now(timezone.utc)
    )
    event = AccountEvent(
        id=0,
        player_id=body.player_id,
        event_type=event_type,
        amount=body.amount,
        currency=body.currency,
        occurred_at=occurred,
        reference=body.reference,
        metadata=body.metadata,
    )

    conn, store, _, _ = _make_services()
    try:
        event_id = store.append(event)
    except Exception as exc:
        log.error("append_event failed", error=str(exc))
        raise HTTPException(status_code=500, detail="Failed to store event")
    finally:
        conn.close()

    return AppendEventResponse(
        event_id=event_id,
        player_id=body.player_id,
        event_type=body.event_type,
    )


@app.get("/players/{player_id}/events", response_model=PaginatedResponse,
         tags=["history"])
def get_events(
    player_id: int,
    from_date:   Optional[str] = Query(None, description="ISO-8601 datetime"),
    to_date:     Optional[str] = Query(None),
    event_types: Optional[str] = Query(None, description="Comma-separated event types"),
    limit:  int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> PaginatedResponse:
    """Full account event timeline for a player."""
    f = _make_filter(player_id, from_date, to_date, event_types, None, None, limit, offset)
    conn, store, query, _ = _make_services()
    try:
        result = query.get_account_timeline(f)
    finally:
        conn.close()

    items = [
        {
            "id": e.id, "player_id": e.player_id,
            "event_type": e.event_type.value,
            "amount": e.amount, "currency": e.currency,
            "occurred_at": _dt(e.occurred_at),
            "reference": e.reference,
        }
        for e in result.items
    ]
    return PaginatedResponse(items=items, total=result.total,
                             limit=result.limit, offset=result.offset,
                             has_more=result.has_more)


@app.get("/players/{player_id}/transactions", response_model=PaginatedResponse,
         tags=["history"])
def get_transactions(
    player_id: int,
    from_date:    Optional[str] = Query(None),
    to_date:      Optional[str] = Query(None),
    types:        Optional[str] = Query(None, description="deposit,withdrawal,bonus"),
    min_amount:   Optional[float] = Query(None),
    max_amount:   Optional[float] = Query(None),
    limit:  int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> PaginatedResponse:
    """Transaction history (deposits, withdrawals, bonuses)."""
    f = _make_filter(player_id, from_date, to_date, types, min_amount, max_amount,
                     limit, offset)
    conn, _, query, _ = _make_services()
    try:
        result = query.get_transactions(f)
    finally:
        conn.close()

    items = [
        {
            "id": t.id, "player_id": t.player_id,
            "transaction_type": t.transaction_type,
            "amount": t.amount, "currency": t.currency,
            "status": t.status.value,
            "initiated_at": _dt(t.initiated_at),
            "completed_at": _dt(t.completed_at),
            "payment_method": t.payment_method,
            "external_ref": t.external_ref,
        }
        for t in result.items
    ]
    return PaginatedResponse(items=items, total=result.total,
                             limit=result.limit, offset=result.offset,
                             has_more=result.has_more)


@app.get("/players/{player_id}/sessions", response_model=PaginatedResponse,
         tags=["history"])
def get_sessions(
    player_id: int,
    from_date: Optional[str] = Query(None),
    to_date:   Optional[str] = Query(None),
    limit:  int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> PaginatedResponse:
    """Session history (login/logout with duration)."""
    f = _make_filter(player_id, from_date, to_date, None, None, None, limit, offset)
    conn, _, query, _ = _make_services()
    try:
        result = query.get_sessions(f)
    finally:
        conn.close()

    items = [
        {
            "id": s.id, "player_id": s.player_id,
            "session_token": s.session_token,
            "started_at": _dt(s.started_at),
            "ended_at": _dt(s.ended_at),
            "ip_address": s.ip_address,
            "device_type": s.device_type,
            "jurisdiction": s.jurisdiction,
            "duration_seconds": s.duration_seconds,
        }
        for s in result.items
    ]
    return PaginatedResponse(items=items, total=result.total,
                             limit=result.limit, offset=result.offset,
                             has_more=result.has_more)


@app.get("/players/{player_id}/game-rounds", response_model=PaginatedResponse,
         tags=["history"])
def get_game_rounds(
    player_id: int,
    from_date:  Optional[str] = Query(None),
    to_date:    Optional[str] = Query(None),
    min_bet:    Optional[float] = Query(None),
    max_bet:    Optional[float] = Query(None),
    limit:  int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> PaginatedResponse:
    """Game round history (bets, wins, GGR per round)."""
    f = _make_filter(player_id, from_date, to_date, None, min_bet, max_bet,
                     limit, offset)
    conn, _, query, _ = _make_services()
    try:
        result = query.get_game_rounds(f)
    finally:
        conn.close()

    items = [
        {
            "id": r.id, "player_id": r.player_id,
            "game_id": r.game_id, "game_name": r.game_name,
            "bet_amount": r.bet_amount, "win_amount": r.win_amount,
            "currency": r.currency, "outcome": r.outcome.value,
            "ggr": r.ggr,
            "started_at": _dt(r.started_at),
            "ended_at": _dt(r.ended_at),
            "round_ref": r.round_ref,
        }
        for r in result.items
    ]
    return PaginatedResponse(items=items, total=result.total,
                             limit=result.limit, offset=result.offset,
                             has_more=result.has_more)


@app.get("/players/{player_id}/stats", response_model=StatsOut, tags=["analytics"])
def get_player_stats(
    player_id: int,
    from_date: Optional[str] = Query(None),
    to_date:   Optional[str] = Query(None),
    currency:  str = Query("GBP", pattern="^[A-Z]{3}$"),
) -> StatsOut:
    """Aggregated player statistics: deposits, GGR, NGR, play time."""
    fd = datetime.fromisoformat(from_date) if from_date else None
    td = datetime.fromisoformat(to_date)   if to_date   else None
    conn, _, _, agg = _make_services()
    try:
        stats = agg.get_player_stats(player_id, fd, td, currency)
    finally:
        conn.close()

    return StatsOut(
        player_id=stats.player_id,
        from_date=stats.from_date.isoformat(),
        to_date=stats.to_date.isoformat(),
        total_deposits=stats.total_deposits,
        total_withdrawals=stats.total_withdrawals,
        net_deposits=stats.net_deposits,
        total_bets=stats.total_bets,
        total_wins=stats.total_wins,
        ggr=stats.ggr,
        ngr=stats.ngr,
        bonus_awarded=stats.bonus_awarded,
        bonus_wagered=stats.bonus_wagered,
        currency=stats.currency,
        session_count=stats.session_count,
        total_play_time_seconds=stats.total_play_time_seconds,
    )


@app.get("/players/{player_id}/ggr-by-game", tags=["analytics"])
def get_ggr_by_game(
    player_id: int,
    from_date: Optional[str] = Query(None),
    to_date:   Optional[str] = Query(None),
) -> list[dict]:
    """GGR breakdown by game for a player."""
    fd = datetime.fromisoformat(from_date) if from_date else None
    td = datetime.fromisoformat(to_date)   if to_date   else None
    conn, _, _, agg = _make_services()
    try:
        return agg.get_ggr_by_game(player_id, fd, td)
    finally:
        conn.close()


@app.get("/players/{player_id}/daily-ggr", tags=["analytics"])
def get_daily_ggr(
    player_id: int,
    from_date: Optional[str] = Query(None),
    to_date:   Optional[str] = Query(None),
) -> list[dict]:
    """Daily GGR time series for a player."""
    fd = datetime.fromisoformat(from_date) if from_date else None
    td = datetime.fromisoformat(to_date)   if to_date   else None
    conn, _, _, agg = _make_services()
    try:
        return agg.get_daily_ggr(player_id, fd, td)
    finally:
        conn.close()


@app.get("/health", tags=["health"])
def health() -> dict:
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8080")),
        reload=False,
    )
