# Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Database and cache connectivity for the Bonus Engine.

Uses:
  - PostgreSQL (via asyncpg + SQLAlchemy 2.x async) for persistent bonus records
  - Redis (via redis-py async) for real-time wagering counters and claim locks
"""

from __future__ import annotations

import logging
import os

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://bonus:bonus@localhost:5432/bonus_engine",
)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/1")

# ── SQLAlchemy ────────────────────────────────────────────────────────────────

engine = create_async_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    echo=os.getenv("SQL_ECHO", "false").lower() == "true",
)

async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    """FastAPI dependency — yields an async DB session."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ── Redis ─────────────────────────────────────────────────────────────────────

_redis_client: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    """FastAPI dependency — returns the shared Redis client."""
    global _redis_client
    if _redis_client is None:
        _redis_client = await aioredis.from_url(
            REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            max_connections=20,
        )
    return _redis_client


# ── Startup / shutdown ────────────────────────────────────────────────────────

async def startup() -> None:
    """Create tables and verify connectivity."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("PostgreSQL tables ensured")

    redis = await get_redis()
    await redis.ping()
    logger.info("Redis connectivity verified")


async def shutdown() -> None:
    global _redis_client
    await engine.dispose()
    if _redis_client:
        await _redis_client.aclose()
    logger.info("Database connections closed")


# ── Redis helpers ─────────────────────────────────────────────────────────────

WAGERING_KEY_PREFIX = "wagering:"
CLAIM_LOCK_PREFIX   = "claim_lock:"


async def acquire_claim_lock(redis: aioredis.Redis, cpf: str, campaign_id: str) -> bool:
    """Distributed lock to prevent double-claim race conditions."""
    key = f"{CLAIM_LOCK_PREFIX}{cpf}:{campaign_id}"
    result = await redis.set(key, "1", nx=True, ex=30)  # 30-second TTL
    return result is True


async def release_claim_lock(redis: aioredis.Redis, cpf: str, campaign_id: str) -> None:
    key = f"{CLAIM_LOCK_PREFIX}{cpf}:{campaign_id}"
    await redis.delete(key)


async def get_wagering_counter(
    redis: aioredis.Redis, cpf: str, bonus_id: str
) -> float:
    key = f"{WAGERING_KEY_PREFIX}{cpf}:{bonus_id}"
    value = await redis.get(key)
    return float(value) if value else 0.0


async def increment_wagering_counter(
    redis: aioredis.Redis, cpf: str, bonus_id: str, amount: float, ttl_seconds: int = 2592000
) -> float:
    """Atomically increment wagering counter, returns new total."""
    key = f"{WAGERING_KEY_PREFIX}{cpf}:{bonus_id}"
    pipe = redis.pipeline()
    pipe.incrbyfloat(key, amount)
    pipe.expire(key, ttl_seconds)
    results = await pipe.execute()
    return float(results[0])
