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
Integration tests for the migrated PostgreSQL matrix-populator.

Spins up an ephemeral PostgreSQL 15 container via testcontainers, applies
test_schema.sql, seeds 3 deposit fixtures, runs the populator, and asserts
that the user_matrix_score row was inserted with the correct comment shape.

Run: pytest -xvs test_populator.py
Skips automatically if Docker isn't available locally.
"""
from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# Allow `from model import …` when pytest's rootdir is the scripts/ tree.
sys.path.insert(0, str(Path(__file__).resolve().parent))

psycopg = pytest.importorskip("psycopg")

try:
    from testcontainers.postgres import PostgresContainer  # ty: ignore[unresolved-import]
except Exception:  # pragma: no cover
    PostgresContainer = None  # type: ignore[assignment]

from model import CalculateOn, MatrixScoreType, load_matrix_score_types
from populator import Populator

SCHEMA_SQL = Path(__file__).parent / "test_schema.sql"


def _docker_available() -> bool:
    return shutil.which("docker") is not None


@pytest.fixture(scope="module")
def pg_url() -> str:
    if not _docker_available() or PostgresContainer is None:
        pytest.skip("docker / testcontainers not available")
    container = PostgresContainer("postgres:15-alpine")
    container.start()
    try:
        yield container.get_connection_url(driver=None)  # raw libpq URL
    finally:
        container.stop()


@pytest.fixture
def conn(pg_url: str):
    with psycopg.connect(pg_url, autocommit=False) as c:
        with c.cursor() as cur:
            cur.execute(SCHEMA_SQL.read_text())
        c.commit()
        yield c
        # Clean between tests
        with c.cursor() as cur:
            cur.execute("DROP SCHEMA platform CASCADE")
        c.commit()


def _seed_deposits(c) -> None:
    with c.cursor() as cur:
        cur.execute("INSERT INTO platform.user_info (userid, country) VALUES (101, 'GB')")
        cur.execute(
            "INSERT INTO platform.monthly_currencies (currency, year, month, rate) "
            "VALUES ('EUR', 2026, 4, 1.000000)"
        )
        base = datetime(2026, 4, 1, 12, 0, 0)
        for i in range(3):
            cur.execute(
                "INSERT INTO platform.user_payments "
                "(user_id, amount, currency, status, date_updated) "
                "VALUES (%s, %s, 'EUR', 'SUCCEEDED', %s)",
                (101, 5000 * (i + 1), base + timedelta(hours=i)),
            )
    c.commit()


def test_load_matrix_score_types_filters_daily_stats(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO platform.matrix_score_type "
            "(label, calculate_on, metric_period, condition, score_value) VALUES "
            "('skipme', 'daily-stats',           '1 days', 'depositCount > 0', 1), "
            "('keepme', 'deposit-confirmed',     '7 days', 'depositCount >= 3', 5)"
        )
    conn.commit()

    rules = load_matrix_score_types(conn)
    assert [r.label for r in rules] == ["keepme"]
    assert rules[0].calculate_on == CalculateOn.DEPOSIT_CONFIRMED
    assert rules[0].metric_period == timedelta(days=7)


def test_populator_inserts_score_when_condition_met(conn) -> None:
    _seed_deposits(conn)
    mst = MatrixScoreType(
        id=42,
        label="three-deposits-7-days",
        calculate_on=CalculateOn.DEPOSIT_CONFIRMED,
        metric_period=timedelta(days=7),
        condition="depositCount >= 3",
        score_value=10,
    )

    Populator(conn, mst).populate()

    with conn.cursor() as cur:
        cur.execute(
            "SELECT user_id, score_type_id, comments "
            "FROM platform.user_matrix_score WHERE score_type_id = 42"
        )
        row = cur.fetchone()
    assert row is not None
    user_id, score_type_id, comments = row
    assert (user_id, score_type_id) == (101, 42)
    assert "depositCount=3" in comments
    assert "depositTotal=" in comments
    assert "depositingDays=" in comments


def test_populator_idempotent_via_not_exists_guard(conn) -> None:
    _seed_deposits(conn)
    mst = MatrixScoreType(
        id=99,
        label="three-deposits",
        calculate_on=CalculateOn.DEPOSIT_CONFIRMED,
        metric_period=timedelta(days=7),
        condition="depositCount >= 3",
        score_value=10,
    )

    Populator(conn, mst).populate()
    Populator(conn, mst).populate()  # second run must be a no-op

    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM platform.user_matrix_score WHERE score_type_id = 99"
        )
        (count,) = cur.fetchone()
    assert count == 1


def test_populator_skips_non_gb_users(conn) -> None:
    _seed_deposits(conn)
    with conn.cursor() as cur:
        cur.execute("UPDATE platform.user_info SET country = 'DE' WHERE userid = 101")
    conn.commit()

    mst = MatrixScoreType(
        id=7,
        label="non-gb",
        calculate_on=CalculateOn.DEPOSIT_CONFIRMED,
        metric_period=timedelta(days=7),
        condition="depositCount >= 3",
        score_value=1,
    )
    Populator(conn, mst).populate()

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM platform.user_matrix_score")
        (count,) = cur.fetchone()
    assert count == 0


def test_build_sql_emits_postgres_interval_literal(conn) -> None:
    # 12 hours stays in the sub-day branch and exercises the HOUR literal path.
    mst = MatrixScoreType(
        id=1, label="x",
        calculate_on=CalculateOn.DEPOSIT_DECLINED,
        metric_period=timedelta(hours=12),
        condition="depositsDeclined > 5",
        score_value=1,
    )
    sql = Populator(conn, mst).build_sql()
    # PostgreSQL syntax — never emit Oracle DAY(3) / HOUR forms.
    assert "INTERVAL '12 hours'" in sql
    assert "DAY(3)" not in sql
    assert "to_char(events." not in sql  # we cast via ::text now
