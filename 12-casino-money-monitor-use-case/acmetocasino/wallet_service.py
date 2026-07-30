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
Event-sourced wallet service.

Balance is NEVER stored directly. It is always computed as:
    SUM(credits) - SUM(debits)

Credits: DEPOSIT, WIN, BONUS_CREDIT
Debits:  BET, WITHDRAWAL, BONUS_DEBIT
"""

import json
import logging
import uuid
from decimal import Decimal
from typing import Any

from app.database import get_cursor
from app.events.publisher import CHANNELS, publish_event
from app.metrics import wallet_events_total

logger = logging.getLogger(__name__)

CREDIT_TYPES = {"DEPOSIT", "WIN", "BONUS_CREDIT"}
DEBIT_TYPES = {"BET", "WITHDRAWAL", "BONUS_DEBIT"}


def get_recent_events(limit: int = 50) -> list[dict[str, Any]]:
    """Retrieve the most recent wallet events across all players."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT id, player_id, event_type, amount, currency,
                   reference_id, metadata, created_at
            FROM wallet_events
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def get_balance(player_id: uuid.UUID) -> dict[str, Any]:
    """
    Compute the current balance from the event ledger.
    Returns {"player_id", "balance", "currency", "event_count"}.
    """
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT
                COALESCE(
                    SUM(CASE WHEN event_type IN ('DEPOSIT','WIN','BONUS_CREDIT')
                             THEN amount ELSE 0 END)
                    -
                    SUM(CASE WHEN event_type IN ('BET','WITHDRAWAL','BONUS_DEBIT')
                             THEN amount ELSE 0 END),
                    0
                ) AS balance,
                COUNT(*) AS event_count
            FROM wallet_events
            WHERE player_id = %s
            """,
            (str(player_id),),
        )
        row = cur.fetchone()

    return {
        "player_id": player_id,
        "balance": Decimal(str(row["balance"])),
        "currency": "USD",
        "event_count": row["event_count"],
    }


def create_event(
    player_id: uuid.UUID,
    event_type: str,
    amount: Decimal,
    currency: str = "USD",
    reference_id: uuid.UUID | None = None,
    metadata: dict | None = None,
) -> dict[str, Any]:
    """
    Append-only INSERT into the wallet ledger.
    For debit events, checks that sufficient balance exists.
    Returns the created event row.
    """
    meta_json = json.dumps(metadata) if metadata else "{}"

    # The balance check and the append MUST run in one transaction, serialized
    # per player. Append-only INSERTs remove the lost-update race on a mutable
    # balance column, but they do NOT make the insufficient-balance invariant
    # safe on their own: two concurrent BETs could both read the same balance,
    # both pass the check, and both insert, overdrawing the wallet. A
    # transaction-scoped per-player advisory lock serializes the read+append so
    # only one debit at a time evaluates the balance.
    with get_cursor() as cur:
        if event_type in DEBIT_TYPES:
            # Per-player lock held until this transaction commits/rolls back.
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (str(player_id),))
            cur.execute(
                """
                SELECT COALESCE(
                    SUM(CASE WHEN event_type IN ('DEPOSIT','WIN','BONUS_CREDIT')
                             THEN amount ELSE 0 END)
                    -
                    SUM(CASE WHEN event_type IN ('BET','WITHDRAWAL','BONUS_DEBIT')
                             THEN amount ELSE 0 END),
                    0
                ) AS balance
                FROM wallet_events
                WHERE player_id = %s
                """,
                (str(player_id),),
            )
            balance = Decimal(str(cur.fetchone()["balance"]))
            if balance < amount:
                raise ValueError(
                    f"Insufficient balance: have {balance}, need {amount}"
                )

        cur.execute(
            """
            INSERT INTO wallet_events
                (player_id, event_type, amount, currency, reference_id, metadata)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            RETURNING id, player_id, event_type, amount, currency,
                      reference_id, metadata, created_at
            """,
            (
                str(player_id),
                event_type,
                str(amount),
                currency,
                str(reference_id) if reference_id else None,
                meta_json,
            ),
        )
        event = dict(cur.fetchone())

    wallet_events_total.labels(event_type=event_type).inc()

    publish_event(
        CHANNELS["wallet"],
        f"wallet.{event_type.lower()}",
        {
            "player_id": str(player_id),
            "event_type": event_type,
            "amount": str(amount),
            "reference_id": str(reference_id) if reference_id else None,
        },
    )
    logger.info(
        "Wallet event: %s %s %s for player %s",
        event_type, amount, currency, player_id,
    )
    return event


def get_history(
    player_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Retrieve wallet event history for a player (newest first)."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT id, player_id, event_type, amount, currency,
                   reference_id, metadata, created_at
            FROM wallet_events
            WHERE player_id = %s
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
            """,
            (str(player_id), limit, offset),
        )
        rows = cur.fetchall()
    return [dict(r) for r in rows]
