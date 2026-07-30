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
query_service.py — Query account history with filters.

Provides high-level query methods over the EventStore, SessionHistory,
TransactionHistory, and GameRoundHistory tables.

Mirrors the ReservationService.scala pattern (service layer over repository),
translated to a gambling account history context.

Supported queries:
  - Transaction history (deposits, withdrawals, bonuses)
  - Session history (login sessions with duration)
  - Game round history (individual bets and outcomes)
  - Combined account timeline (all event types, date-filtered)

All queries support:
  - Date range filtering
  - Event/transaction type filtering
  - Amount range filtering
  - Pagination (limit + offset)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

import psycopg2
import psycopg2.extras
import structlog

from event_store import EventStore
from models import (
    AccountEvent,
    GameRoundHistory,
    GameOutcome,
    HistoryFilter,
    PaginatedResult,
    SessionHistory,
    TransactionHistory,
    TransactionStatus,
)

log = structlog.get_logger(__name__)


class QueryService:
    """
    Query service for player account history.

    Wraps the EventStore and dedicated history tables with filter
    and pagination support. All reads are non-destructive.
    """

    def __init__(self, event_store: EventStore, conn: Any) -> None:
        self._store = event_store
        self._conn  = conn

    # ------------------------------------------------------------------
    # Transaction history
    # ------------------------------------------------------------------

    def get_transactions(self, f: HistoryFilter) -> PaginatedResult:
        """
        Retrieve transaction history (deposits, withdrawals, bonuses).

        Returns paginated TransactionHistory objects.
        """
        conditions = ["player_id = %s"]
        params: list[Any] = [f.player_id]

        if f.from_date:
            conditions.append("initiated_at >= %s")
            params.append(f.from_date)
        if f.to_date:
            conditions.append("initiated_at <= %s")
            params.append(f.to_date)
        if f.event_types:
            conditions.append("transaction_type = ANY(%s)")
            params.append(f.event_types)
        if f.min_amount is not None:
            conditions.append("amount >= %s")
            params.append(f.min_amount)
        if f.max_amount is not None:
            conditions.append("amount <= %s")
            params.append(f.max_amount)

        where = " AND ".join(conditions)

        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"SELECT COUNT(*) AS cnt FROM transaction_history WHERE {where}",
                params,
            )
            total = cur.fetchone()["cnt"]

            cur.execute(
                f"""
                SELECT * FROM transaction_history
                WHERE {where}
                ORDER BY initiated_at DESC
                LIMIT %s OFFSET %s
                """,
                params + [f.limit, f.offset],
            )
            rows = cur.fetchall()

        items = [self._row_to_transaction(r) for r in rows]
        log.debug("query_service: transactions", player_id=f.player_id,
                  total=total, returned=len(items))
        return PaginatedResult(items=items, total=total,
                               limit=f.limit, offset=f.offset)

    # ------------------------------------------------------------------
    # Session history
    # ------------------------------------------------------------------

    def get_sessions(self, f: HistoryFilter) -> PaginatedResult:
        """
        Retrieve session history for a player.

        Sessions track login time, logout time, device type and jurisdiction.
        """
        conditions = ["player_id = %s"]
        params: list[Any] = [f.player_id]

        if f.from_date:
            conditions.append("started_at >= %s")
            params.append(f.from_date)
        if f.to_date:
            conditions.append("started_at <= %s")
            params.append(f.to_date)

        where = " AND ".join(conditions)

        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"SELECT COUNT(*) AS cnt FROM session_history WHERE {where}",
                params,
            )
            total = cur.fetchone()["cnt"]

            cur.execute(
                f"""
                SELECT * FROM session_history
                WHERE {where}
                ORDER BY started_at DESC
                LIMIT %s OFFSET %s
                """,
                params + [f.limit, f.offset],
            )
            rows = cur.fetchall()

        items = [self._row_to_session(r) for r in rows]
        return PaginatedResult(items=items, total=total,
                               limit=f.limit, offset=f.offset)

    # ------------------------------------------------------------------
    # Game round history
    # ------------------------------------------------------------------

    def get_game_rounds(self, f: HistoryFilter) -> PaginatedResult:
        """
        Retrieve game round history.

        Includes bet amount, win amount, outcome, and GGR per round.
        """
        conditions = ["player_id = %s"]
        params: list[Any] = [f.player_id]

        if f.from_date:
            conditions.append("started_at >= %s")
            params.append(f.from_date)
        if f.to_date:
            conditions.append("started_at <= %s")
            params.append(f.to_date)
        if f.min_amount is not None:
            conditions.append("bet_amount >= %s")
            params.append(f.min_amount)
        if f.max_amount is not None:
            conditions.append("bet_amount <= %s")
            params.append(f.max_amount)

        where = " AND ".join(conditions)

        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"SELECT COUNT(*) AS cnt FROM game_round_history WHERE {where}",
                params,
            )
            total = cur.fetchone()["cnt"]

            cur.execute(
                f"""
                SELECT * FROM game_round_history
                WHERE {where}
                ORDER BY started_at DESC
                LIMIT %s OFFSET %s
                """,
                params + [f.limit, f.offset],
            )
            rows = cur.fetchall()

        items = [self._row_to_game_round(r) for r in rows]
        return PaginatedResult(items=items, total=total,
                               limit=f.limit, offset=f.offset)

    # ------------------------------------------------------------------
    # Combined account timeline
    # ------------------------------------------------------------------

    def get_account_timeline(self, f: HistoryFilter) -> PaginatedResult:
        """
        Retrieve the full account event timeline from the event store.

        Combines all event types in chronological order.
        """
        events = self._store.get_player_events(
            player_id=f.player_id,
            from_date=f.from_date,
            to_date=f.to_date,
            event_types=f.event_types,
            limit=f.limit,
            offset=f.offset,
        )
        total = self._store.count_player_events(
            player_id=f.player_id,
            from_date=f.from_date,
            to_date=f.to_date,
            event_types=f.event_types,
        )
        return PaginatedResult(items=events, total=total,
                               limit=f.limit, offset=f.offset)

    # ------------------------------------------------------------------
    # Row mappers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_transaction(row: dict) -> TransactionHistory:
        return TransactionHistory(
            id=row["id"],
            player_id=row["player_id"],
            transaction_type=row["transaction_type"],
            amount=float(row["amount"]),
            currency=row["currency"],
            status=TransactionStatus(row["status"]),
            initiated_at=row["initiated_at"],
            completed_at=row.get("completed_at"),
            payment_method=row.get("payment_method"),
            external_ref=row.get("external_ref"),
        )

    @staticmethod
    def _row_to_session(row: dict) -> SessionHistory:
        return SessionHistory(
            id=row["id"],
            player_id=row["player_id"],
            session_token=row["session_token"],
            started_at=row["started_at"],
            ended_at=row.get("ended_at"),
            ip_address=row.get("ip_address"),
            device_type=row.get("device_type"),
            jurisdiction=row.get("jurisdiction"),
        )

    @staticmethod
    def _row_to_game_round(row: dict) -> GameRoundHistory:
        return GameRoundHistory(
            id=row["id"],
            player_id=row["player_id"],
            session_id=row.get("session_id"),
            game_id=row["game_id"],
            game_name=row.get("game_name"),
            bet_amount=float(row["bet_amount"]),
            win_amount=float(row["win_amount"]),
            currency=row["currency"],
            outcome=GameOutcome(row["outcome"]),
            started_at=row["started_at"],
            ended_at=row.get("ended_at"),
            round_ref=row.get("round_ref"),
        )
