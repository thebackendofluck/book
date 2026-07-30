# Companion code for "The Backend of Luck" - Chapter 10, Complete Platform Architecture.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
PostgreSQL connection pool using psycopg2.
"""

import contextlib
import logging
from typing import Generator

import psycopg2
import psycopg2.pool
import psycopg2.extras

from app.config import settings

logger = logging.getLogger(__name__)

_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def init_pool(min_conn: int = 2, max_conn: int = 20) -> None:
    """Initialize the connection pool. Called once at startup."""
    global _pool
    if _pool is not None:
        return
    _pool = psycopg2.pool.ThreadedConnectionPool(
        min_conn, max_conn, settings.DATABASE_URL
    )
    logger.info("PostgreSQL connection pool initialized (min=%d, max=%d)", min_conn, max_conn)


def close_pool() -> None:
    """Close the connection pool. Called at shutdown."""
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None
        logger.info("PostgreSQL connection pool closed")


@contextlib.contextmanager
def get_connection() -> Generator:
    """Yield a connection from the pool, returning it when done."""
    if _pool is None:
        raise RuntimeError("Database pool not initialized. Call init_pool() first.")
    conn = _pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)


@contextlib.contextmanager
def get_cursor(dict_cursor: bool = True) -> Generator:
    """Yield a cursor from a pooled connection."""
    cursor_factory = psycopg2.extras.RealDictCursor if dict_cursor else None
    with get_connection() as conn:
        cur = conn.cursor(cursor_factory=cursor_factory)
        try:
            yield cur
        finally:
            cur.close()


def run_migrations() -> None:
    """Create tables if they do not exist."""
    ddl = """
    -- PAM: players
    CREATE TABLE IF NOT EXISTS players (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        email           VARCHAR(255) UNIQUE NOT NULL,
        username        VARCHAR(100) UNIQUE NOT NULL,
        password_hash   TEXT NOT NULL,
        status          VARCHAR(20) DEFAULT 'active',
        kyc_status      VARCHAR(20) DEFAULT 'pending',
        vip_tier        VARCHAR(20) DEFAULT 'bronze',
        created_at      TIMESTAMPTZ DEFAULT now(),
        updated_at      TIMESTAMPTZ DEFAULT now()
    );

    -- Wallet: event-sourced ledger
    CREATE TABLE IF NOT EXISTS wallet_events (
        id              BIGSERIAL PRIMARY KEY,
        player_id       UUID NOT NULL REFERENCES players(id),
        event_type      VARCHAR(30) NOT NULL,
        amount          NUMERIC(15,2) NOT NULL,
        currency        VARCHAR(3) DEFAULT 'USD',
        reference_id    UUID,
        metadata        JSONB DEFAULT '{}',
        created_at      TIMESTAMPTZ DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS idx_wallet_events_player ON wallet_events(player_id);

    -- GAL: game sessions
    CREATE TABLE IF NOT EXISTS game_sessions (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        player_id       UUID NOT NULL REFERENCES players(id),
        game_slug       VARCHAR(100) NOT NULL,
        status          VARCHAR(20) DEFAULT 'active',
        rounds_played   INT DEFAULT 0,
        total_bet       NUMERIC(15,2) DEFAULT 0,
        total_win       NUMERIC(15,2) DEFAULT 0,
        created_at      TIMESTAMPTZ DEFAULT now(),
        closed_at       TIMESTAMPTZ
    );

    -- GAL: round audit log
    CREATE TABLE IF NOT EXISTS game_rounds (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        session_id      UUID NOT NULL REFERENCES game_sessions(id),
        player_id       UUID NOT NULL,
        game_slug       VARCHAR(100) NOT NULL,
        bet_amount      NUMERIC(15,2) NOT NULL,
        win_amount      NUMERIC(15,2) NOT NULL,
        rng_seed_hash   TEXT NOT NULL,
        rng_output      TEXT NOT NULL,
        target_rtp      NUMERIC(5,2),
        created_at      TIMESTAMPTZ DEFAULT now()
    );

    -- RTP configuration per game
    CREATE TABLE IF NOT EXISTS rtp_configs (
        game_slug       VARCHAR(100) PRIMARY KEY,
        target_rtp      NUMERIC(5,2) NOT NULL DEFAULT 96.00,
        min_rtp         NUMERIC(5,2) NOT NULL DEFAULT 80.00,
        max_rtp         NUMERIC(5,2) NOT NULL DEFAULT 99.00,
        updated_at      TIMESTAMPTZ DEFAULT now()
    );

    -- Compliance: KYC checks
    CREATE TABLE IF NOT EXISTS kyc_checks (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        player_id       UUID NOT NULL REFERENCES players(id),
        document_type   VARCHAR(50) NOT NULL,
        document_ref    TEXT NOT NULL,
        status          VARCHAR(20) DEFAULT 'pending',
        reviewer_id     UUID,
        notes           TEXT,
        submitted_at    TIMESTAMPTZ DEFAULT now(),
        reviewed_at     TIMESTAMPTZ
    );

    -- Compliance: AML alerts
    CREATE TABLE IF NOT EXISTS aml_alerts (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        player_id       UUID NOT NULL REFERENCES players(id),
        alert_type      VARCHAR(50) NOT NULL,
        severity        VARCHAR(20) NOT NULL DEFAULT 'medium',
        details         JSONB DEFAULT '{}',
        status          VARCHAR(20) DEFAULT 'open',
        reviewer_id     UUID,
        reviewed_at     TIMESTAMPTZ,
        created_at      TIMESTAMPTZ DEFAULT now()
    );

    -- Responsible Gaming: deposit limits
    CREATE TABLE IF NOT EXISTS deposit_limits (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        player_id       UUID NOT NULL REFERENCES players(id),
        period          VARCHAR(20) NOT NULL,
        amount          NUMERIC(15,2) NOT NULL,
        active          BOOLEAN DEFAULT true,
        created_at      TIMESTAMPTZ DEFAULT now(),
        effective_at    TIMESTAMPTZ DEFAULT now()
    );

    -- Responsible Gaming: self-exclusions
    CREATE TABLE IF NOT EXISTS self_exclusions (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        player_id       UUID NOT NULL REFERENCES players(id),
        duration_days   INT NOT NULL,
        reason          TEXT,
        starts_at       TIMESTAMPTZ DEFAULT now(),
        ends_at         TIMESTAMPTZ NOT NULL,
        active          BOOLEAN DEFAULT true,
        created_at      TIMESTAMPTZ DEFAULT now()
    );
    """
    with get_cursor(dict_cursor=False) as cur:
        cur.execute(ddl)
    logger.info("Database migrations applied")
