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
model.py — Risk scoring matrix types for AML/fraud detection.

Mirrors Model.scala from the matrix-populator batch job.

The matrix_score_type table stores configurable scoring rules. Each row defines:
  - calculate_on: which event triggers this score (deposit-confirmed / declined / limit-increased)
  - metric_period: look-back window (e.g. "7 days", "30 days")
  - condition: SQL expression users must satisfy (e.g. "depositCount > 20")
  - score_value: points awarded when condition matches

Migration note: driver switched from oracledb to psycopg (PostgreSQL).
The loader query is pure ANSI SQL and did not need any dialect changes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import Optional

import psycopg
import structlog

log = structlog.get_logger()


class CalculateOn(str, Enum):
    DEPOSIT_CONFIRMED = "deposit-confirmed"
    DEPOSIT_DECLINED  = "deposit-declined"
    DEPOSIT_LIMIT_INCREASED = "deposit-limit-increased"


def _parse_duration(period_str: str) -> timedelta:
    """
    Parse a duration string like "7 days" or "24 hours" into a timedelta.
    Mirrors scala.concurrent.duration.Duration(period) behaviour.
    """
    match = re.fullmatch(r"(\d+)\s*(days?|hours?|minutes?)", period_str.strip(), re.IGNORECASE)
    if not match:
        raise ValueError(f"Cannot parse duration: {period_str!r}")
    value = int(match.group(1))
    unit = match.group(2).lower().rstrip("s")
    if unit == "day":
        return timedelta(days=value)
    if unit == "hour":
        return timedelta(hours=value)
    if unit == "minute":
        return timedelta(minutes=value)
    raise ValueError(f"Unknown duration unit: {unit}")


@dataclass
class MatrixScoreType:
    id: int
    label: str
    calculate_on: CalculateOn
    metric_period: timedelta
    condition: str
    score_value: int


def load_matrix_score_types(connection: psycopg.Connection) -> list[MatrixScoreType]:
    """
    Load all non-daily-stats scoring rules from matrix_score_type.
    Mirrors Model.loadMatrixScoreTypes().
    """
    sql = """
        SELECT id, label, calculate_on, metric_period, condition, score_value
        FROM platform.matrix_score_type
        WHERE calculate_on <> 'daily-stats'
    """
    with connection.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()

    result = []
    for row in rows:
        id_, label, calculate_on_str, period_str, condition, score_value = row
        result.append(MatrixScoreType(
            id=id_,
            label=label,
            calculate_on=CalculateOn(calculate_on_str),
            metric_period=_parse_duration(period_str),
            condition=condition,
            score_value=score_value,
        ))
    return result
