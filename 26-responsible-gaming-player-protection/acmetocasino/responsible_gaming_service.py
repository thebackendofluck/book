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
Responsible Gaming service: deposit limits, self-exclusion, reality checks.
"""

import datetime
import logging
import uuid
from decimal import Decimal
from typing import Any

from app.database import get_cursor  # ty: ignore[unresolved-import]
from app.events.publisher import CHANNELS, publish_event  # ty: ignore[unresolved-import]

logger = logging.getLogger(__name__)

VALID_PERIODS = {"daily", "weekly", "monthly"}
# Regulatory cooling-off for limit INCREASES (Brazil Portaria 1231/2024;
# within the UKGC/MGA 24-72h range). Decreases take effect immediately.
LIMIT_INCREASE_COOLOFF = datetime.timedelta(hours=72)

# Minimum self-exclusion duration by jurisdiction, in days (Chapter 26,
# Section 26.2 table: UK/Ontario/Malta require 6 months; Sweden/Denmark
# require 1 month). A missing or unrecognized jurisdiction falls back to
# the strictest known minimum rather than the Pydantic model's bare
# `ge=1` floor, which exists only to keep the field a positive integer.
JURISDICTION_MIN_EXCLUSION_DAYS = {
    "GB": 180,  # United Kingdom (UKGC), GAMSTOP
    "ON": 180,  # Ontario (iGaming Ontario)
    "MT": 180,  # Malta (MGA)
    "SE": 30,   # Sweden (Spelpaus)
    "DK": 30,   # Denmark (ROFUS)
}
DEFAULT_MIN_EXCLUSION_DAYS = 180


def get_all_active_exclusions(limit: int = 50) -> list[dict[str, Any]]:
    """Retrieve all currently active self-exclusions across all players."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT id, player_id, duration_days, reason, starts_at, ends_at,
                   active, created_at
            FROM self_exclusions
            WHERE active = true AND ends_at > now()
            ORDER BY starts_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def set_limits(
    player_id: uuid.UUID,
    period: str,
    amount: Decimal,
) -> dict[str, Any]:
    """
    Set or replace a deposit limit for a player.
    Deactivates any existing limit for the same period.
    """
    if period not in VALID_PERIODS:
        raise ValueError(f"Period must be one of: {VALID_PERIODS}")

    with get_cursor() as cur:
        # Current in-force amount for this period (the most recent limit that
        # has already taken effect), to decide decrease vs increase.
        cur.execute(
            """
            SELECT amount FROM deposit_limits
            WHERE player_id = %s AND period = %s AND active = true
              AND effective_at <= now()
            ORDER BY effective_at DESC
            LIMIT 1
            """,
            (str(player_id), period),
        )
        row = cur.fetchone()
        current_amount = Decimal(str(row["amount"])) if row else None

        is_increase = current_amount is not None and amount > current_amount

        if is_increase:
            # A limit INCREASE must not take effect until the cooling-off has
            # elapsed. Leave the (lower) current limit active and in force;
            # insert the new one with a future effective_at.
            effective_at = datetime.datetime.now(datetime.timezone.utc) + LIMIT_INCREASE_COOLOFF
            cur.execute(
                """
                INSERT INTO deposit_limits (player_id, period, amount, effective_at)
                VALUES (%s, %s, %s, %s)
                RETURNING id, player_id, period, amount, active, created_at, effective_at
                """,
                (str(player_id), period, str(amount), effective_at),
            )
        else:
            # Decrease (or first-time limit): effective immediately. Retire any
            # prior limits for this period, including a pending increase.
            cur.execute(
                """
                UPDATE deposit_limits SET active = false
                WHERE player_id = %s AND period = %s AND active = true
                """,
                (str(player_id), period),
            )
            cur.execute(
                """
                INSERT INTO deposit_limits (player_id, period, amount, effective_at)
                VALUES (%s, %s, %s, now())
                RETURNING id, player_id, period, amount, active, created_at, effective_at
                """,
                (str(player_id), period, str(amount)),
            )
        limit = dict(cur.fetchone())

    publish_event(
        CHANNELS["player"],
        "responsible_gaming.limit_set",
        {
            "player_id": str(player_id),
            "period": period,
            "amount": str(amount),
        },
    )
    logger.info("Deposit limit set for player %s: %s %s", player_id, period, amount)
    return limit


def self_exclude(
    player_id: uuid.UUID,
    duration_days: int,
    reason: str | None = None,
    jurisdiction: str | None = None,
) -> dict[str, Any]:
    """
    Self-exclude a player for a specified duration.
    Also sets player status to 'excluded'.

    duration_days must meet the jurisdiction's regulatory minimum: 6 months
    for UK/Ontario/Malta, 1 month for Sweden/Denmark (Chapter 26, Section
    26.2). A missing or unrecognized jurisdiction is rejected below the
    strictest known minimum (6 months) rather than silently accepted.
    """
    min_days = JURISDICTION_MIN_EXCLUSION_DAYS.get(
        (jurisdiction or "").upper(), DEFAULT_MIN_EXCLUSION_DAYS
    )
    if duration_days < min_days:
        raise ValueError(
            f"Minimum self-exclusion period for jurisdiction "
            f"{jurisdiction or 'unspecified'} is {min_days} days."
        )

    starts_at = datetime.datetime.now(datetime.timezone.utc)
    ends_at = starts_at + datetime.timedelta(days=duration_days)

    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO self_exclusions (player_id, duration_days, reason, starts_at, ends_at)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id, player_id, duration_days, reason, starts_at, ends_at,
                      active, created_at
            """,
            (str(player_id), duration_days, reason, starts_at, ends_at),
        )
        exclusion = dict(cur.fetchone())

        # Set player status to excluded
        cur.execute(
            "UPDATE players SET status = 'excluded', updated_at = now() WHERE id = %s",
            (str(player_id),),
        )

    publish_event(
        CHANNELS["player"],
        "responsible_gaming.self_excluded",
        {
            "player_id": str(player_id),
            "duration_days": duration_days,
            "ends_at": ends_at.isoformat(),
        },
    )
    logger.warning("Player %s self-excluded for %d days", player_id, duration_days)
    return exclusion


def get_active_limits(player_id: uuid.UUID) -> list[dict[str, Any]]:
    """Get all active deposit limits for a player."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (period)
                   id, player_id, period, amount, active, created_at, effective_at
            FROM deposit_limits
            WHERE player_id = %s AND active = true AND effective_at <= now()
            ORDER BY period, effective_at DESC
            """,
            (str(player_id),),
        )
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def get_active_exclusion(player_id: uuid.UUID) -> dict[str, Any] | None:
    """Get the active self-exclusion for a player (if any)."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT id, player_id, duration_days, reason, starts_at, ends_at,
                   active, created_at
            FROM self_exclusions
            WHERE player_id = %s AND active = true AND ends_at > now()
            ORDER BY ends_at DESC
            LIMIT 1
            """,
            (str(player_id),),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def check_limits(player_id: uuid.UUID, deposit_amount: Decimal) -> bool:
    """
    Check if a deposit would exceed any active limit.
    Returns True if deposit is allowed, raises ValueError if blocked.

    A self-excluded player is blocked outright: exclusion must stop all
    deposits, not only enforce a spending cap.
    """
    if get_active_exclusion(player_id) is not None:
        raise ValueError("Player is self-excluded; deposits are blocked.")

    limits = get_active_limits(player_id)
    if not limits:
        return True

    interval_map = {
        "daily": "1 day",
        "weekly": "7 days",
        "monthly": "30 days",
    }

    with get_cursor() as cur:
        for limit in limits:
            period = limit["period"]
            interval = interval_map.get(period, "1 day")
            cur.execute(
                f"""
                SELECT COALESCE(SUM(amount), 0) AS total
                FROM wallet_events
                WHERE player_id = %s
                  AND event_type = 'DEPOSIT'
                  AND created_at > now() - interval '{interval}'
                """,
                (str(player_id),),
            )
            row = cur.fetchone()
            current_total = Decimal(str(row["total"]))
            if current_total + deposit_amount > Decimal(str(limit["amount"])):
                raise ValueError(
                    f"{period.capitalize()} deposit limit of {limit['amount']} would be exceeded. "
                    f"Current: {current_total}, attempted: {deposit_amount}"
                )

    return True


def reality_check(player_id: uuid.UUID) -> dict[str, Any]:
    """
    Generate a reality check snapshot showing session activity and limits.
    """
    with get_cursor() as cur:
        # Get today's session stats
        cur.execute(
            """
            SELECT
                COALESCE(SUM(total_bet), 0) AS total_bet,
                COALESCE(SUM(total_win), 0) AS total_win,
                -- Current session length = time since the most recent session
                -- started, NOT the span from the day's first session (which
                -- overstates play time across breaks).
                EXTRACT(EPOCH FROM (now() - MAX(created_at))) / 60 AS session_minutes
            FROM game_sessions
            WHERE player_id = %s
              AND created_at > now() - interval '24 hours'
            """,
            (str(player_id),),
        )
        stats = cur.fetchone()

    total_bet = Decimal(str(stats["total_bet"]))
    total_win = Decimal(str(stats["total_win"]))
    session_min = int(stats["session_minutes"] or 0)

    limits = get_active_limits(player_id)
    exclusion = get_active_exclusion(player_id)

    return {
        "player_id": player_id,
        "session_duration_minutes": session_min,
        "total_bet": total_bet,
        "total_win": total_win,
        "net_position": total_win - total_bet,
        "active_limits": limits,
        "excluded": exclusion is not None,
        "exclusion_ends_at": exclusion["ends_at"] if exclusion else None,
    }
