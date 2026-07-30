# Companion code for "The Backend of Luck" - Chapter 19, Anti-Fraud System Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
populator.py — Calculates user risk scores from payment patterns.

Mirrors Populator.scala from the matrix-populator batch job.

For each MatrixScoreType the Populator generates a single INSERT … SELECT
that computes windowed aggregations over the PostgreSQL payment tables and
inserts risk score entries for users whose metrics match the condition.

The NOT EXISTS guard ensures idempotency: a user is only scored once per type.
The country filter (GB) restricts scoring to UKGC-regulated players.

Migration note (Oracle → PostgreSQL):
  - oracle.jdbc.OracleDriver + oracledb  →  psycopg (PostgreSQL driver)
  - INTERVAL '7' DAY(3)                  →  INTERVAL '7 days'
  - INTERVAL '168' HOUR                  →  INTERVAL '168 hours'
  - trunc(ts)                            →  date_trunc('day', ts)
  - to_char(numeric) with no format      →  numeric::text
  See scripts/chapter-19/matrix-populator/README.md for the rationale.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import psycopg
import structlog

from model import CalculateOn, MatrixScoreType

log = structlog.get_logger()


class Populator:
    """
    Generates and executes the PostgreSQL INSERT … SELECT for one MatrixScoreType.
    Mirrors Populator.populate() in Scala.
    """

    def __init__(self, connection: psycopg.Connection, mst: MatrixScoreType) -> None:
        self._conn = connection
        self._mst = mst

    def populate(
        self,
        from_dt: Optional[datetime] = None,
        to_dt: Optional[datetime] = None,
    ) -> None:
        mst = self._mst
        period = mst.metric_period

        # PostgreSQL INTERVAL literals accept both 'N days' and 'N hours' directly.
        # Unlike Oracle's INTERVAL '7' DAY(3) / INTERVAL '168' HOUR split, the
        # same literal form works for both day- and hour-precision windows.
        total_hours = int(period.total_seconds() // 3600)
        if period.days > 0 and period.seconds == 0:
            offset_interval = f"INTERVAL '{period.days} days'"
            interval_literal = f"INTERVAL '{period.days} days'"
        elif total_hours > 0:
            offset_interval = f"INTERVAL '{total_hours} hours'"
            interval_literal = f"INTERVAL '{total_hours} hours'"
        else:
            raise ValueError(f"Cannot handle duration {period}")

        # Build source, column aliases, and per-event-type metrics.
        # Window functions use RANGE BETWEEN INTERVAL … PRECEDING — supported in
        # PostgreSQL 11+ when the ORDER BY column is a timestamp/timestamptz.
        if mst.calculate_on == CalculateOn.DEPOSIT_CONFIRMED:
            source = """
                platform.user_payments UP
                JOIN platform.monthly_currencies c
                     ON c.currency = UP.currency
                     AND c.year  = EXTRACT(YEAR  FROM UP.date_updated)::int
                     AND c.month = EXTRACT(MONTH FROM UP.date_updated)::int
                WHERE UP.status = 'SUCCEEDED'
            """
            event_id_col = "UP.id"
            user_id_col  = "UP.user_id"
            date_col     = "UP.date_updated"
            metrics = {
                "depositCount": (
                    f"COUNT(UP.id) OVER (PARTITION BY UP.user_id ORDER BY UP.date_updated "
                    f"RANGE BETWEEN {interval_literal} PRECEDING AND CURRENT ROW)"
                ),
                "depositTotal": (
                    f"SUM(UP.amount / c.rate / 100) OVER (PARTITION BY UP.user_id "
                    f"ORDER BY UP.date_updated "
                    f"RANGE BETWEEN {interval_literal} PRECEDING AND CURRENT ROW)"
                ),
                "depositingDays": (
                    f"(SELECT COUNT(DISTINCT date_trunc('day', p2.date_updated)) "
                    f"FROM platform.user_payments p2 "
                    f"WHERE p2.user_id = UP.user_id AND p2.status = 'SUCCEEDED' "
                    f"AND p2.date_updated BETWEEN UP.date_updated - {offset_interval} "
                    f"AND UP.date_updated)"
                ),
            }

        elif mst.calculate_on == CalculateOn.DEPOSIT_DECLINED:
            source = """
                platform.user_payments UP
                WHERE UP.status = 'FAILED' AND UP.failure_reason = 'Refused'
            """
            event_id_col = "UP.id"
            user_id_col  = "UP.user_id"
            date_col     = "UP.date_updated"
            metrics = {
                "depositsDeclined": (
                    f"COUNT(UP.id) OVER (PARTITION BY UP.user_id ORDER BY UP.date_updated "
                    f"RANGE BETWEEN {interval_literal} PRECEDING AND CURRENT ROW)"
                ),
            }

        elif mst.calculate_on == CalculateOn.DEPOSIT_LIMIT_INCREASED:
            source = """
                platform.responsible_gaming_actions a
                JOIN platform.responsible_gaming_audit aa
                    ON aa.action_id = a.id
                    AND aa.audit_type = 'deposit-limit'
                WHERE a.action_type = 'deposit-limit-confirm'
            """
            event_id_col = "a.id"
            user_id_col  = "a.user_id"
            date_col     = "aa.timestamp"
            metrics = {
                "depositLimitIncreases": (
                    f"COUNT(a.id) OVER (PARTITION BY a.user_id ORDER BY aa.timestamp "
                    f"RANGE BETWEEN {interval_literal} PRECEDING AND CURRENT ROW)"
                ),
            }
        else:
            raise ValueError(f"Unknown calculate_on: {mst.calculate_on}")

        # CTE column list.
        metric_cols = ",\n".join(f"{expr} AS {name}" for name, expr in metrics.items())
        # PostgreSQL's || is ANSI; cast numeric to text rather than using Oracle's
        # to_char(numeric) which requires a format mask in PG.
        metric_text = " || ',' || ".join(
            f"'{name}=' || events.{name}::text" for name in metrics
        )

        # Optional date range filters.
        fmt = "%Y-%m-%d %H:%M:%S"
        date_constraint = ""
        if from_dt:
            date_constraint += f" AND {date_col} >= TIMESTAMP '{from_dt.strftime(fmt)}'"
        if to_dt:
            date_constraint += f" AND {date_col} <= TIMESTAMP '{to_dt.strftime(fmt)}'"

        score_type_id = mst.id

        sql = f"""
            INSERT INTO platform.user_matrix_score (user_id, score_type_id, "timestamp", comments)
            WITH events AS (
                SELECT {event_id_col} AS event_id,
                       {user_id_col}  AS user_id,
                       {date_col}     AS event_date,
                       {metric_cols}
                FROM {source}
                {date_constraint}
            ),
            first_event AS (
                SELECT user_id,
                       MIN(event_id)   AS event_id,
                       MIN(event_date) AS min_date
                FROM   events
                WHERE  {mst.condition}
                GROUP BY user_id
            )
            SELECT events.user_id,
                   {score_type_id},
                   events.event_date,
                   ({metric_text})
            FROM   events
            JOIN   first_event ON events.user_id = first_event.user_id
                              AND events.event_id = first_event.event_id
            JOIN   platform.user_info i ON i.userid = events.user_id AND i.country = 'GB'
            WHERE  NOT EXISTS (
                SELECT 1 FROM platform.user_matrix_score s
                WHERE  s.user_id = events.user_id
                  AND  s.score_type_id = {score_type_id}
            )
        """

        log.info("executing populator", score_type_id=score_type_id, label=mst.label)
        with self._conn.cursor() as cur:
            cur.execute(sql)
        self._conn.commit()
        log.info("populator done", score_type_id=score_type_id)

    # Exposed for tests: return the SQL without executing it.
    def build_sql(
        self,
        from_dt: Optional[datetime] = None,
        to_dt: Optional[datetime] = None,
    ) -> str:
        """Build the INSERT SQL without executing — used by unit tests."""
        saved_execute = self._conn
        class _NullCursor:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def execute(self, sql): self.sql = sql
        class _NullConn:
            def cursor(self): self._cur = _NullCursor(); return self._cur
            def commit(self): pass
        null = _NullConn()
        self._conn = null  # ty: ignore[assignment]
        try:
            self.populate(from_dt, to_dt)
            return null._cur.sql
        finally:
            self._conn = saved_execute
