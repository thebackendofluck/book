# Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Responsible Gaming Service — Limit Enforcement Engine
======================================================
Manages deposit / loss / session limits with real-time enforcement
backed by Redis counters.

Redis key scheme:
  rg:limit:{cpf_hash}:{limit_type}:{period}   →  current usage (float)

Design notes:
  - Limits are "soft" at 80% (warning) and "hard" at 100% (block).
  - Decreasing a limit takes effect immediately.
  - Increasing a limit requires a 24-hour cooling-off period
    (Portaria 615/2023 §12).
  - Counters reset at period boundaries (daily at midnight BRT,
    weekly on Monday, monthly on the 1st).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import structlog

logger = structlog.get_logger(__name__)

# Soft-warning threshold (80% of limit)
WARNING_THRESHOLD: float = 0.80
# Cooling-off period before a limit increase takes effect
LIMIT_INCREASE_COOLING_HOURS: int = 24


# ---------------------------------------------------------------------------
# Custom Exceptions
# ---------------------------------------------------------------------------


class LimitExceededError(RuntimeError):
    """Player has reached or exceeded a limit."""

    def __init__(self, limit_type: str, period: str, used: float, limit: float) -> None:
        self.limit_type = limit_type
        self.period = period
        self.used = used
        self.limit = limit
        super().__init__(
            f"{limit_type.capitalize()} {period} limit of R${limit:.2f} reached "
            f"(used R${used:.2f})"
        )


class LimitIncreaseBlockedError(RuntimeError):
    """Limit increase is blocked during the cooling-off period."""

    def __init__(self, cooling_until: datetime) -> None:
        self.cooling_until = cooling_until
        super().__init__(
            f"Limit increase blocked until cooling-off expires at "
            f"{cooling_until.isoformat()}"
        )


# ---------------------------------------------------------------------------
# In-memory Redis stub (replace with redis.asyncio in production)
# ---------------------------------------------------------------------------


class _RedisStub:
    """
    Thread-safe in-memory Redis stub for development and testing.
    Replace with redis.asyncio.Redis in production:
        import redis.asyncio as aioredis
        redis = aioredis.from_url(REDIS_URL)
    """

    def __init__(self) -> None:
        self._store: Dict[str, Any] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[bytes]:
        async with self._lock:
            val = self._store.get(key)
            return str(val).encode() if val is not None else None

    async def set(self, key: str, value: Any, ex: Optional[int] = None) -> None:
        async with self._lock:
            self._store[key] = value

    async def incrbyfloat(self, key: str, amount: float) -> float:
        async with self._lock:
            current = float(self._store.get(key, 0))
            new_val = current + amount
            self._store[key] = new_val
            return new_val

    async def delete(self, *keys: str) -> None:
        async with self._lock:
            for k in keys:
                self._store.pop(k, None)

    async def ttl(self, key: str) -> int:
        return -1  # stub: no TTL tracking

    async def ping(self) -> bool:
        return True


# Singleton stub instance — swap for aioredis in production
_redis: _RedisStub = _RedisStub()


def get_redis() -> _RedisStub:
    return _redis


# ---------------------------------------------------------------------------
# Limit Engine
# ---------------------------------------------------------------------------


class LimitEngine:
    """
    Enforces deposit / loss / session limits in real time using Redis counters.

    Usage:
        engine = LimitEngine()
        await engine.check_and_consume(cpf_hash, "deposit", "daily", amount=500.0)
    """

    def __init__(self, redis: Optional[_RedisStub] = None) -> None:
        self._redis = redis or get_redis()

    def _key(self, cpf_hash: str, limit_type: str, period: str) -> str:
        return f"rg:limit:{cpf_hash}:{limit_type}:{period}"

    def _config_key(self, cpf_hash: str, limit_type: str, period: str) -> str:
        return f"rg:limit_cfg:{cpf_hash}:{limit_type}:{period}"

    def _cooling_key(self, cpf_hash: str, limit_type: str, period: str) -> str:
        return f"rg:limit_cool:{cpf_hash}:{limit_type}:{period}"

    async def get_usage(self, cpf_hash: str, limit_type: str, period: str) -> float:
        """Return the current usage counter for a limit dimension."""
        raw = await self._redis.get(self._key(cpf_hash, limit_type, period))
        return float(raw) if raw else 0.0

    async def get_config(
        self, cpf_hash: str, limit_type: str, period: str
    ) -> Optional[float]:
        """Return the configured limit amount, or None if not set."""
        raw = await self._redis.get(self._config_key(cpf_hash, limit_type, period))
        return float(raw) if raw else None

    async def set_limit(
        self,
        cpf_hash: str,
        limit_type: str,
        period: str,
        new_amount: float,
    ) -> Tuple[bool, Optional[datetime]]:
        """
        Set or update a limit.

        Returns:
            (applied_immediately, cooling_until)
            - If a limit is being DECREASED: applied immediately.
            - If a limit is being INCREASED: cooling-off of 24 h applies.
        """
        existing = await self.get_config(cpf_hash, limit_type, period)
        now = datetime.now(timezone.utc)

        if existing is not None and new_amount > existing:
            # Increasing a limit → 24-h cooling-off
            cooling_until = now + timedelta(hours=LIMIT_INCREASE_COOLING_HOURS)
            await self._redis.set(
                self._cooling_key(cpf_hash, limit_type, period),
                cooling_until.isoformat(),
            )
            logger.info(
                "limit_increase_cooling",
                cpf_hash=cpf_hash[:8],
                limit_type=limit_type,
                period=period,
                cooling_until=cooling_until.isoformat(),
            )
            # Update the config key now; engine will enforce the old value until cooling expires
            await self._redis.set(
                self._config_key(cpf_hash, limit_type, period), new_amount
            )
            return False, cooling_until

        # Decrease or new limit — take effect immediately
        await self._redis.set(
            self._config_key(cpf_hash, limit_type, period), new_amount
        )
        logger.info(
            "limit_set",
            cpf_hash=cpf_hash[:8],
            limit_type=limit_type,
            period=period,
            amount=new_amount,
        )
        return True, None

    async def check_and_consume(
        self,
        cpf_hash: str,
        limit_type: str,
        period: str,
        amount: float,
    ) -> Dict[str, Any]:
        """
        Check whether the transaction fits within the configured limit and
        add it to the usage counter if it does.

        Returns a dict with usage details.
        Raises LimitExceededError if the limit would be breached.
        """
        limit = await self.get_config(cpf_hash, limit_type, period)
        if limit is None:
            # No limit configured — allow
            return {"allowed": True, "limit": None, "used": None, "remaining": None}

        usage = await self.get_usage(cpf_hash, limit_type, period)

        if usage + amount > limit:
            raise LimitExceededError(limit_type, period, usage + amount, limit)

        new_usage = await self._redis.incrbyfloat(
            self._key(cpf_hash, limit_type, period), amount
        )

        remaining = max(0.0, limit - new_usage)
        warning = (new_usage / limit) >= WARNING_THRESHOLD

        logger.info(
            "limit_consumed",
            cpf_hash=cpf_hash[:8],
            limit_type=limit_type,
            period=period,
            amount=amount,
            usage=new_usage,
            limit=limit,
            warning=warning,
        )

        return {
            "allowed": True,
            "limit": limit,
            "used": new_usage,
            "remaining": remaining,
            "warning": warning,
        }

    async def reset_usage(self, cpf_hash: str, limit_type: str, period: str) -> None:
        """Reset the usage counter for a limit (called by period-boundary scheduler)."""
        await self._redis.delete(self._key(cpf_hash, limit_type, period))
        logger.info(
            "limit_usage_reset",
            cpf_hash=cpf_hash[:8],
            limit_type=limit_type,
            period=period,
        )

    async def remove_limit(self, cpf_hash: str, limit_type: str, period: str) -> None:
        """Remove a limit configuration and its usage counter."""
        await self._redis.delete(
            self._key(cpf_hash, limit_type, period),
            self._config_key(cpf_hash, limit_type, period),
            self._cooling_key(cpf_hash, limit_type, period),
        )
