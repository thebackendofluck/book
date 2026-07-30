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
db.py -- Database connection library for iGaming analytics pipeline.

Supports three connection modes:
  - replica: read replica for reporting queries (non-blocking for production)
  - prod:    primary production database (for write operations)
  - us:      US-specific database (NJ/PA/MI jurisdiction data)

Uses SQLAlchemy + psycopg2 with environment variable credentials.
Configures autocommit and search_path on each connection.

Chapter 34: Data Analytics -- Customer Analytics ETL Pipeline
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import pandas as pd
import psycopg2
import psycopg2.extensions
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine


# ---------------------------------------------------------------------------
# Connection modes
# ---------------------------------------------------------------------------

DB_MODES = ("replica", "prod", "us")


def _get_dsn(mode: str) -> str:
    """
    Build a PostgreSQL DSN from environment variables for the given mode.

    Environment variables (replace <MODE> with REPLICA, PROD, or US):
      DB_<MODE>_HOST     -- hostname or IP
      DB_<MODE>_PORT     -- port (default: 5432)
      DB_<MODE>_NAME     -- database name
      DB_<MODE>_USER     -- username
      DB_<MODE>_PASSWORD -- password

    Falls back to DB_HOST / DB_PORT / DB_NAME / DB_USER / DB_PASSWORD
    for backwards compatibility.
    """
    prefix = mode.upper()

    host = os.environ.get(f"DB_{prefix}_HOST") or os.environ.get("DB_HOST", "localhost")
    port = os.environ.get(f"DB_{prefix}_PORT") or os.environ.get("DB_PORT", "5432")
    dbname = os.environ.get(f"DB_{prefix}_NAME") or os.environ.get("DB_NAME", "casino")
    user = os.environ.get(f"DB_{prefix}_USER") or os.environ.get("DB_USER", "analytics")
    password = os.environ.get(f"DB_{prefix}_PASSWORD") or os.environ.get("DB_PASSWORD", "")

    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{dbname}"


def init_connection(
    mode: str = "replica",
    search_path: str = "public",
    pool_size: int = 5,
    max_overflow: int = 10,
) -> Engine:
    """
    Create and return a SQLAlchemy engine for the given mode.

    Args:
        mode:        One of 'replica', 'prod', 'us'.
        search_path: PostgreSQL search_path to set on each connection.
        pool_size:   SQLAlchemy connection pool size.
        max_overflow: Maximum overflow connections above pool_size.

    Returns:
        A SQLAlchemy Engine with autocommit disabled.

    Example::

        engine = init_connection(mode="replica")
        df = read_df_from_db(engine, "sql/daily_report.sql")
    """
    if mode not in DB_MODES:
        raise ValueError(f"Invalid mode {mode!r}. Must be one of: {DB_MODES}")

    dsn = _get_dsn(mode)

    def _set_search_path(dbapi_conn: psycopg2.extensions.connection, _conn_record: object) -> None:
        cursor = dbapi_conn.cursor()
        cursor.execute(f"SET search_path TO {search_path}")
        cursor.close()

    engine = create_engine(
        dsn,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,  # Detect stale connections
        pool_recycle=3600,   # Recycle connections every hour
    )

    # Register event listener to set search_path on each new connection
    from sqlalchemy import event as sqla_event
    sqla_event.listen(engine, "connect", _set_search_path)

    return engine


def read_df_from_db(
    engine: Engine,
    sql_path: str | Path,
    params: Optional[dict[str, object]] = None,
) -> pd.DataFrame:
    """
    Load a SQL file and execute it, returning results as a Pandas DataFrame.

    Args:
        engine:   SQLAlchemy engine (from init_connection).
        sql_path: Path to .sql file to execute.
        params:   Optional dict of named parameters for the query
                  (e.g., {'start_date': '2026-01-01'}).

    Returns:
        Pandas DataFrame with query results.

    Example::

        engine = init_connection(mode="replica")
        df = read_df_from_db(engine, "sql/commercial_report.sql",
                             params={"report_date": "2026-03-31"})
    """
    sql_path = Path(sql_path)
    if not sql_path.exists():
        raise FileNotFoundError(f"SQL file not found: {sql_path}")

    query = sql_path.read_text(encoding="utf-8")

    with engine.connect() as conn:
        return pd.read_sql(text(query), conn, params=params)


def execute_script(
    sql: str,
    mode: str = "prod",
    search_path: str = "public",
) -> None:
    """
    Execute a SQL script (DDL/DML) using raw psycopg2.

    Uses autocommit mode so DDL statements (CREATE TABLE, etc.) are
    committed immediately without requiring an explicit COMMIT.

    Args:
        sql:         SQL string to execute.
        mode:        Connection mode ('replica', 'prod', 'us').
        search_path: PostgreSQL search_path.

    Example::

        execute_script("REFRESH MATERIALIZED VIEW mv_daily_stats;")
    """
    prefix = mode.upper()
    conn_params = {
        "host": os.environ.get(f"DB_{prefix}_HOST") or os.environ.get("DB_HOST", "localhost"),
        "port": int(os.environ.get(f"DB_{prefix}_PORT") or os.environ.get("DB_PORT", "5432")),
        "dbname": os.environ.get(f"DB_{prefix}_NAME") or os.environ.get("DB_NAME", "casino"),
        "user": os.environ.get(f"DB_{prefix}_USER") or os.environ.get("DB_USER", "analytics"),
        "password": os.environ.get(f"DB_{prefix}_PASSWORD") or os.environ.get("DB_PASSWORD", ""),
    }

    conn = psycopg2.connect(**conn_params)
    try:
        conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {search_path}")
            cur.execute(sql)
    finally:
        conn.close()


def execute_script_file(
    sql_path: str | Path,
    mode: str = "prod",
    search_path: str = "public",
) -> None:
    """
    Read a SQL file and execute it via execute_script.

    Args:
        sql_path:    Path to .sql file.
        mode:        Connection mode.
        search_path: PostgreSQL search_path.
    """
    sql_path = Path(sql_path)
    if not sql_path.exists():
        raise FileNotFoundError(f"SQL file not found: {sql_path}")
    execute_script(sql_path.read_text(encoding="utf-8"), mode=mode, search_path=search_path)
