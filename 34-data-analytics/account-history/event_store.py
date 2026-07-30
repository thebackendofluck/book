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
event_store.py — Event sourcing store for player account events.

Implements the append-only event log pattern adapted from the Scala
ReservationRepository (doobie/Quill → psycopg2, immutability preserved).

Key design principles:
  - Events are NEVER updated or deleted (append-only log)
  - Each event has a monotonically increasing sequence number per player
  - Optimistic concurrency: duplicate external references are rejected
  - Batch inserts are supported for bulk imports

Tables (PostgreSQL):
  account_events(
      id              BIGSERIAL PRIMARY KEY,
      player_id       BIGINT NOT NULL,
      event_type      TEXT NOT NULL,
      amount          NUMERIC(18,2) NOT NULL,
      currency        CHAR(3) NOT NULL,
      occurred_at     TIMESTAMPTZ NOT NULL,
      reference       TEXT,
      metadata        JSONB,
      created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
  )
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import psycopg2
import psycopg2.extras
import structlog

from models import AccountEvent, EventType

log = structlog.get_logger(__name__)


class EventStore:
    """
    Append-only store for player account events.

    All writes go through append(). Reads support filtering by player,
    event type, time range, and pagination.
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    # ------------------------------------------------------------------
    # Write operations (append-only)
    # ------------------------------------------------------------------

    def append(self, event: AccountEvent) -> int:
        """
        Append a single event to the store.

        Returns the generated event ID.
        Raises IntegrityError if a duplicate reference is detected.
        """
        log.debug("event_store: appending event",
                  player_id=event.player_id, event_type=event.event_type)

        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO account_events
                    (player_id, event_type, amount, currency,
                     occurred_at, reference, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    event.player_id,
                    event.event_type.value,
                    event.amount,
                    event.currency,
                    event.occurred_at,
                    event.reference,
                    json.dumps(event.metadata) if event.metadata else None,
                ),
            )
            event_id = cur.fetchone()[0]
        self._conn.commit()
        log.info("event_store: event appended",
                 event_id=event_id, player_id=event.player_id,
                 event_type=event.event_type)
        return event_id

    def append_batch(self, events: list[AccountEvent]) -> list[int]:
        """
        Append multiple events atomically.

        All events are inserted in a single transaction; if any fails,
        none are committed.
        """
        if not events:
            return []

        log.info("event_store: batch append",
                 count=len(events),
                 player_ids=list({e.player_id for e in events}))

        rows = [
            (
                e.player_id,
                e.event_type.value,
                e.amount,
                e.currency,
                e.occurred_at,
                e.reference,
                json.dumps(e.metadata) if e.metadata else None,
            )
            for e in events
        ]

        ids: list[int] = []
        with self._conn.cursor() as cur:
            for row in rows:
                cur.execute(
                    """
                    INSERT INTO account_events
                        (player_id, event_type, amount, currency,
                         occurred_at, reference, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    row,
                )
                ids.append(cur.fetchone()[0])
        self._conn.commit()
        log.info("event_store: batch append complete", ids_created=len(ids))
        return ids

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get_by_id(self, event_id: int) -> Optional[AccountEvent]:
        """Fetch a single event by its ID."""
        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM account_events WHERE id = %s",
                (event_id,),
            )
            row = cur.fetchone()
        return self._row_to_event(row) if row else None

    def get_player_events(
        self,
        player_id: int,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        event_types: Optional[list[str]] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AccountEvent]:
        """
        Fetch events for a player with optional filters.

        Results are ordered by occurred_at DESC (most recent first).
        """
        conditions = ["player_id = %s"]
        params: list[Any] = [player_id]

        if from_date:
            conditions.append("occurred_at >= %s")
            params.append(from_date)
        if to_date:
            conditions.append("occurred_at <= %s")
            params.append(to_date)
        if event_types:
            conditions.append("event_type = ANY(%s)")
            params.append(event_types)

        where = " AND ".join(conditions)
        params.extend([limit, offset])

        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT * FROM account_events
                WHERE {where}
                ORDER BY occurred_at DESC
                LIMIT %s OFFSET %s
                """,
                params,
            )
            rows = cur.fetchall()

        return [self._row_to_event(r) for r in rows]

    def count_player_events(
        self,
        player_id: int,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        event_types: Optional[list[str]] = None,
    ) -> int:
        """Count events matching the given criteria (for pagination)."""
        conditions = ["player_id = %s"]
        params: list[Any] = [player_id]

        if from_date:
            conditions.append("occurred_at >= %s")
            params.append(from_date)
        if to_date:
            conditions.append("occurred_at <= %s")
            params.append(to_date)
        if event_types:
            conditions.append("event_type = ANY(%s)")
            params.append(event_types)

        where = " AND ".join(conditions)
        with self._conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) FROM account_events WHERE {where}",
                params,
            )
            return cur.fetchone()[0]

    def get_by_reference(self, reference: str) -> Optional[AccountEvent]:
        """Look up an event by its external reference (idempotency check)."""
        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM account_events WHERE reference = %s LIMIT 1",
                (reference,),
            )
            row = cur.fetchone()
        return self._row_to_event(row) if row else None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_event(row: dict) -> AccountEvent:
        metadata = row["metadata"]
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        return AccountEvent(
            id=row["id"],
            player_id=row["player_id"],
            event_type=EventType(row["event_type"]),
            amount=float(row["amount"]),
            currency=row["currency"],
            occurred_at=row["occurred_at"],
            reference=row.get("reference"),
            metadata=metadata,
        )
