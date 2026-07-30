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
aggregator.py — Aggregate player account statistics.

Computes:
  - Total deposits and withdrawals (net deposits)
  - Total bets and wins (GGR, NGR)
  - Bonus awarded and wagered
  - Session count and total play time
  - Per-game GGR breakdown

Used for:
  - Regulatory reporting (HMRC, Spelinspektionen, Spillemyndigheden, SEAE)
  - Responsible Gambling affordability checks
  - Marketing segmentation
  - Bonus abuse detection

Mirrors the aggregation logic that would have lived in the Scala
ReservationService as counter increments, translated to SQL GROUP BY
aggregations for the gambling domain.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import psycopg2
import psycopg2.extras
import structlog

from models import EventType, PlayerStats

log = structlog.get_logger(__name__)


class Aggregator:
    """
    Compute aggregated player statistics over a given time window.

    All queries run against the account_events, transaction_history,
    session_history, and game_round_history tables.
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    # ------------------------------------------------------------------
    # Main aggregation
    # ------------------------------------------------------------------

    def get_player_stats(
        self,
        player_id: int,
        from_date: Optional[datetime] = None,
        to_date:   Optional[datetime] = None,
        currency:  str = "GBP",
    ) -> PlayerStats:
        """
        Compute full statistics for a player over the given period.

        If from_date / to_date are omitted, aggregates over all time.
        """
        log.info("aggregator: computing player stats",
                 player_id=player_id,
                 from_date=from_date,
                 to_date=to_date)

        txn_stats  = self._transaction_stats(player_id, from_date, to_date)
        round_stats = self._game_round_stats(player_id, from_date, to_date)
        sess_stats  = self._session_stats(player_id, from_date, to_date)

        now = datetime.now(timezone.utc)
        return PlayerStats(
            player_id=player_id,
            from_date=from_date or datetime.min.replace(tzinfo=timezone.utc),
            to_date=to_date or now,
            total_deposits=txn_stats.get("deposits", 0.0),
            total_withdrawals=txn_stats.get("withdrawals", 0.0),
            total_bets=round_stats.get("total_bets", 0.0),
            total_wins=round_stats.get("total_wins", 0.0),
            bonus_awarded=txn_stats.get("bonus_awarded", 0.0),
            bonus_wagered=txn_stats.get("bonus_wagered", 0.0),
            currency=currency,
            session_count=sess_stats.get("session_count", 0),
            total_play_time_seconds=sess_stats.get("total_play_time_seconds", 0.0),
        )

    def get_ggr_by_game(
        self,
        player_id: int,
        from_date: Optional[datetime] = None,
        to_date:   Optional[datetime] = None,
    ) -> list[dict]:
        """
        GGR breakdown by game, for a player.

        Returns a list of {"game_id": ..., "game_name": ..., "ggr": ...}
        sorted by GGR descending (highest-margin games first).
        """
        conditions = ["player_id = %s"]
        params: list[Any] = [player_id]

        if from_date:
            conditions.append("started_at >= %s")
            params.append(from_date)
        if to_date:
            conditions.append("started_at <= %s")
            params.append(to_date)

        where = " AND ".join(conditions)

        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT
                    game_id,
                    MAX(game_name)  AS game_name,
                    SUM(bet_amount) AS total_bets,
                    SUM(win_amount) AS total_wins,
                    SUM(bet_amount - win_amount) AS ggr,
                    COUNT(*)        AS round_count
                FROM game_round_history
                WHERE {where}
                GROUP BY game_id
                ORDER BY ggr DESC
                """,
                params,
            )
            rows = cur.fetchall()

        return [dict(r) for r in rows]

    def get_daily_ggr(
        self,
        player_id: int,
        from_date: Optional[datetime] = None,
        to_date:   Optional[datetime] = None,
    ) -> list[dict]:
        """
        Daily GGR time series for a player.

        Returns list of {"date": "YYYY-MM-DD", "ggr": float, "bets": float, "wins": float}.
        """
        conditions = ["player_id = %s"]
        params: list[Any] = [player_id]

        if from_date:
            conditions.append("started_at >= %s")
            params.append(from_date)
        if to_date:
            conditions.append("started_at <= %s")
            params.append(to_date)

        where = " AND ".join(conditions)

        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT
                    DATE(started_at AT TIME ZONE 'UTC') AS date,
                    SUM(bet_amount)                     AS bets,
                    SUM(win_amount)                     AS wins,
                    SUM(bet_amount - win_amount)        AS ggr
                FROM game_round_history
                WHERE {where}
                GROUP BY DATE(started_at AT TIME ZONE 'UTC')
                ORDER BY date ASC
                """,
                params,
            )
            rows = cur.fetchall()

        return [
            {
                "date": str(r["date"]),
                "bets": float(r["bets"] or 0),
                "wins": float(r["wins"] or 0),
                "ggr":  float(r["ggr"]  or 0),
            }
            for r in rows
        ]

    def get_deposit_frequency(
        self,
        player_id: int,
        from_date: Optional[datetime] = None,
        to_date:   Optional[datetime] = None,
    ) -> dict:
        """
        Deposit frequency analysis (total count, total amount, avg per deposit).

        Used in RG affordability checks to flag high-frequency depositors.
        """
        conditions = ["player_id = %s", "transaction_type = 'deposit'",
                      "status = 'completed'"]
        params: list[Any] = [player_id]

        if from_date:
            conditions.append("initiated_at >= %s")
            params.append(from_date)
        if to_date:
            conditions.append("initiated_at <= %s")
            params.append(to_date)

        where = " AND ".join(conditions)

        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT
                    COUNT(*)    AS deposit_count,
                    SUM(amount) AS total_amount,
                    AVG(amount) AS avg_amount,
                    MIN(amount) AS min_amount,
                    MAX(amount) AS max_amount
                FROM transaction_history
                WHERE {where}
                """,
                params,
            )
            row = cur.fetchone()

        return {
            "player_id":     player_id,
            "deposit_count": int(row["deposit_count"] or 0),
            "total_amount":  float(row["total_amount"] or 0),
            "avg_amount":    float(row["avg_amount"] or 0),
            "min_amount":    float(row["min_amount"] or 0),
            "max_amount":    float(row["max_amount"] or 0),
        }

    # ------------------------------------------------------------------
    # Private SQL helpers
    # ------------------------------------------------------------------

    def _transaction_stats(
        self,
        player_id: int,
        from_date: Optional[datetime],
        to_date:   Optional[datetime],
    ) -> dict:
        conditions = ["player_id = %s", "status = 'completed'"]
        params: list[Any] = [player_id]

        if from_date:
            conditions.append("initiated_at >= %s")
            params.append(from_date)
        if to_date:
            conditions.append("initiated_at <= %s")
            params.append(to_date)

        where = " AND ".join(conditions)

        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT
                    COALESCE(SUM(CASE WHEN transaction_type='deposit'      THEN amount ELSE 0 END), 0) AS deposits,
                    COALESCE(SUM(CASE WHEN transaction_type='withdrawal'   THEN amount ELSE 0 END), 0) AS withdrawals,
                    COALESCE(SUM(CASE WHEN transaction_type='bonus'        THEN amount ELSE 0 END), 0) AS bonus_awarded,
                    COALESCE(SUM(CASE WHEN transaction_type='bonus_wager'  THEN amount ELSE 0 END), 0) AS bonus_wagered
                FROM transaction_history
                WHERE {where}
                """,
                params,
            )
            row = cur.fetchone()

        return {
            "deposits":      float(row["deposits"]      or 0),
            "withdrawals":   float(row["withdrawals"]   or 0),
            "bonus_awarded": float(row["bonus_awarded"] or 0),
            "bonus_wagered": float(row["bonus_wagered"] or 0),
        }

    def _game_round_stats(
        self,
        player_id: int,
        from_date: Optional[datetime],
        to_date:   Optional[datetime],
    ) -> dict:
        conditions = ["player_id = %s"]
        params: list[Any] = [player_id]

        if from_date:
            conditions.append("started_at >= %s")
            params.append(from_date)
        if to_date:
            conditions.append("started_at <= %s")
            params.append(to_date)

        where = " AND ".join(conditions)

        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT
                    COALESCE(SUM(bet_amount), 0) AS total_bets,
                    COALESCE(SUM(win_amount), 0) AS total_wins
                FROM game_round_history
                WHERE {where}
                """,
                params,
            )
            row = cur.fetchone()

        return {
            "total_bets": float(row["total_bets"] or 0),
            "total_wins": float(row["total_wins"] or 0),
        }

    def _session_stats(
        self,
        player_id: int,
        from_date: Optional[datetime],
        to_date:   Optional[datetime],
    ) -> dict:
        conditions = ["player_id = %s", "ended_at IS NOT NULL"]
        params: list[Any] = [player_id]

        if from_date:
            conditions.append("started_at >= %s")
            params.append(from_date)
        if to_date:
            conditions.append("started_at <= %s")
            params.append(to_date)

        where = " AND ".join(conditions)

        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT
                    COUNT(*)                AS session_count,
                    COALESCE(
                        SUM(EXTRACT(EPOCH FROM (ended_at - started_at))), 0
                    )                       AS total_play_time_seconds
                FROM session_history
                WHERE {where}
                """,
                params,
            )
            row = cur.fetchone()

        return {
            "session_count":           int(row["session_count"] or 0),
            "total_play_time_seconds": float(row["total_play_time_seconds"] or 0),
        }
