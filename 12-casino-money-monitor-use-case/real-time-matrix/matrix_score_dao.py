# Companion code for "The Backend of Luck" - Chapter 12, Real-Time Cash Flow Management for Online Casinos.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# MatrixScoreDAO
# Source: Production casino platform (sanitized)
# Chapter 12 - Casino Money Monitor
#
# Data access object for matrix score types stored in the database.
# Score types define the conditions under which an RTMX alert fires.
# =============================================================================

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)


@dataclass
class MatrixScoreType:
    matrix_id: str
    id: int
    label: str
    calculate_on: str
    metric_period_days: int
    condition: str


class MatrixScoreDAO:
    """
    Reads matrix score type configuration from PostgreSQL.
    The records define the scoring rules used by the RTMX detection engine.
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _connect(self):
        return psycopg2.connect(self._dsn)

    def get_matrix_score_types_by_calculate_on(self, calculate_on: str) -> list[MatrixScoreType]:
        sql = """
            SELECT matrix_id, id, label, calculate_on, metric_period_days, condition
            FROM matrix_score_types
            WHERE calculate_on = %s
        """
        with self._connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, (calculate_on,))
                return [MatrixScoreType(**row) for row in cur.fetchall()]

    def get_matrix_score_types(self) -> list[MatrixScoreType]:
        sql = """
            SELECT matrix_id, id, label, calculate_on, metric_period_days, condition
            FROM matrix_score_types
        """
        with self._connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql)
                return [MatrixScoreType(**row) for row in cur.fetchall()]

    def update_matrix_score_types(self, score_types: list[MatrixScoreType]) -> int:
        """Upsert score types; returns the number of rows affected."""
        sql = """
            INSERT INTO matrix_score_types
                (matrix_id, id, label, calculate_on, metric_period_days, condition)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                label = EXCLUDED.label,
                calculate_on = EXCLUDED.calculate_on,
                metric_period_days = EXCLUDED.metric_period_days,
                condition = EXCLUDED.condition
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                data = [
                    (s.matrix_id, s.id, s.label, s.calculate_on, s.metric_period_days, s.condition)
                    for s in score_types
                ]
                cur.executemany(sql, data)
                return cur.rowcount
