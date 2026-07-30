# Companion code for "The Backend of Luck" - Chapter 21, Caching Strategies and Benefits.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Cache safety patterns for preventing common issues.

Implements:
- Cache stampede (thundering herd) prevention
- Distributed locking
- Circuit breakers
- Graceful degradation

Critical for high-traffic iGaming platforms where cache failures
can cascade to database overload.
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class LockState(Enum):
    """State of a distributed lock."""

    ACQUIRED = "acquired"
    WAITING = "waiting"
    TIMEOUT = "timeout"
    FAILED = "failed"


@dataclass
class LockConfig:
    """Configuration for distributed locking."""

    lock_timeout: int = 10  # seconds
    wait_timeout: float = 5.0  # seconds
    retry_interval: float = 0.1  # seconds
    max_retries: int = 50


@dataclass
class LockResult:
    """Result of a lock operation."""

    state: LockState
    lock_id: Optional[str] = None
    acquired_at: Optional[datetime] = None
    wait_time_ms: float = 0.0


class DistributedLock:
    """
    Redis-based distributed lock for cache population.

    Prevents multiple processes from simultaneously populating
    the same cache key (thundering herd problem).
    """

    def __init__(self, redis_client: Any, config: Optional[LockConfig] = None):
        self.redis = redis_client
        self.config = config or LockConfig()

    async def acquire(self, key: str) -> LockResult:
        """
        Attempt to acquire a lock on the given key.

        Uses Redis SET NX (set if not exists) for atomic acquisition.
        """
        start_time = time.perf_counter()
        lock_id = str(uuid.uuid4())
        lock_key = f"lock:{key}"
        retries = 0

        while retries < self.config.max_retries:
            acquired = await self.redis.set(
                lock_key,
                lock_id,
                ex=self.config.lock_timeout,
                nx=True,
            )

            if acquired:
                wait_time = (time.perf_counter() - start_time) * 1000
                logger.debug(f"Lock acquired for {key} after {wait_time:.2f}ms")

                return LockResult(
                    state=LockState.ACQUIRED,
                    lock_id=lock_id,
                    acquired_at=datetime.now(timezone.utc),
                    wait_time_ms=wait_time,
                )

            elapsed = time.perf_counter() - start_time
            if elapsed >= self.config.wait_timeout:
                return LockResult(
                    state=LockState.TIMEOUT,
                    wait_time_ms=elapsed * 1000,
                )

            await asyncio.sleep(self.config.retry_interval)
            retries += 1

        return LockResult(
            state=LockState.FAILED,
            wait_time_ms=(time.perf_counter() - start_time) * 1000,
        )

    async def release(self, key: str, lock_id: str) -> bool:
        """
        Release a lock only if we own it.

        Uses Lua script for atomic check-and-delete.
        """
        lock_key = f"lock:{key}"

        release_script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """

        try:
            result = await self.redis.eval(release_script, 1, lock_key, lock_id)
            released = result == 1

            if released:
                logger.debug(f"Lock released for {key}")
            else:
                logger.warning(f"Lock release failed for {key} - not owner")

            return released

        except Exception as e:
            logger.error(f"Lock release error for {key}: {e}")
            return False

    async def extend(self, key: str, lock_id: str, additional_time: int = 10) -> bool:
        """Extend lock TTL if we still own it."""
        lock_key = f"lock:{key}"

        extend_script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("expire", KEYS[1], ARGV[2])
        else
            return 0
        end
        """

        try:
            result = await self.redis.eval(
                extend_script, 1, lock_key, lock_id, additional_time
            )
            return result == 1
        except Exception as e:
            logger.error(f"Lock extend error for {key}: {e}")
            return False


class StampedeSafeCache:
    """
    Cache implementation with stampede prevention.

    When cache expires, only ONE request fetches from database.
    All other concurrent requests wait for the cache to be populated.
    """

    def __init__(
        self,
        redis_client: Any,
        lock: Optional[DistributedLock] = None,
    ):
        self.redis = redis_client
        self.lock = lock or DistributedLock(redis_client)
        self._stats = {
            "cache_hits": 0,
            "cache_misses": 0,
            "lock_acquired": 0,
            "lock_waited": 0,
            "db_fetches": 0,
        }

    async def get(
        self,
        key: str,
        fetch_func: Callable[[], Any],
        ttl: int = 300,
    ) -> Any:
        """
        Get value from cache with stampede protection.

        Flow:
        1. Check cache - return if hit
        2. Try to acquire lock
        3. If lock acquired: fetch from DB, populate cache, release lock
        4. If lock not acquired: wait and retry cache
        """
        data = await self.redis.get(key)
        if data is not None:
            self._stats["cache_hits"] += 1
            return data

        self._stats["cache_misses"] += 1
        lock_result = await self.lock.acquire(key)

        if lock_result.state == LockState.ACQUIRED:
            self._stats["lock_acquired"] += 1
            try:
                data = await self.redis.get(key)
                if data is not None:
                    return data

                self._stats["db_fetches"] += 1
                data = await fetch_func()
                await self.redis.setex(key, ttl, data)
                return data

            finally:
                if lock_result.lock_id:
                    await self.lock.release(key, lock_result.lock_id)

        else:
            self._stats["lock_waited"] += 1
            for _ in range(10):
                await asyncio.sleep(0.1)
                data = await self.redis.get(key)
                if data is not None:
                    return data

            logger.warning(f"Cache wait timeout for {key}, fetching directly")
            return await fetch_func()

    async def problematic_get(self, key: str, fetch_func: Callable[[], Any]) -> Any:
        """
        ANTI-PATTERN: Demonstrates the thundering herd problem.

        DO NOT USE IN PRODUCTION - for educational purposes only.
        All concurrent requests hit database when cache expires.
        """
        data = await self.redis.get(key)
        if not data:
            data = await fetch_func()
            await self.redis.setex(key, 300, data)
        return data

    def get_stats(self) -> dict[str, Any]:
        """Get stampede prevention statistics."""
        total = self._stats["cache_hits"] + self._stats["cache_misses"]
        hit_rate = (self._stats["cache_hits"] / total * 100) if total > 0 else 0

        return {
            **self._stats,
            "hit_rate_percent": round(hit_rate, 2),
            "db_fetch_rate": round(
                self._stats["db_fetches"] / max(1, self._stats["cache_misses"]) * 100, 2
            ),
        }


class CircuitBreaker:
    """
    Circuit breaker for cache operations.

    Prevents cascade failures when cache is unavailable.
    States: CLOSED (normal) -> OPEN (failing) -> HALF_OPEN (testing)
    """

    class State(Enum):
        CLOSED = "closed"  # Normal operation
        OPEN = "open"  # Failing, bypass cache
        HALF_OPEN = "half_open"  # Testing recovery

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_requests: int = 3,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_requests = half_open_requests
        self._state = self.State.CLOSED
        self._failures = 0
        self._last_failure_time: Optional[float] = None
        self._half_open_successes = 0

    @property
    def state(self) -> State:
        if self._state == self.State.OPEN and self._last_failure_time:
            if time.time() - self._last_failure_time >= self.recovery_timeout:
                self._state = self.State.HALF_OPEN
                self._half_open_successes = 0
        return self._state

    def record_success(self) -> None:
        """Record successful operation."""
        if self._state == self.State.HALF_OPEN:
            self._half_open_successes += 1
            if self._half_open_successes >= self.half_open_requests:
                self._state = self.State.CLOSED
                self._failures = 0
                logger.info("Circuit breaker CLOSED - cache recovered")
        elif self._state == self.State.CLOSED:
            self._failures = 0

    def record_failure(self) -> None:
        """Record failed operation."""
        self._failures += 1
        self._last_failure_time = time.time()

        if self._state == self.State.HALF_OPEN:
            self._state = self.State.OPEN
            logger.warning("Circuit breaker OPEN - half-open test failed")
        elif self._failures >= self.failure_threshold:
            self._state = self.State.OPEN
            logger.warning(
                f"Circuit breaker OPEN after {self._failures} failures"
            )

    def is_available(self) -> bool:
        """Check if cache operations should be attempted."""
        return self.state != self.State.OPEN

    async def execute(
        self,
        cache_func: Callable[[], Any],
        fallback_func: Callable[[], Any],
    ) -> Any:
        """
        Execute with circuit breaker protection.

        If circuit is open, skip cache and use fallback directly.
        """
        if not self.is_available():
            logger.debug("Circuit open - using fallback")
            return await fallback_func()

        try:
            result = await cache_func()
            self.record_success()
            return result

        except Exception as e:
            self.record_failure()
            logger.warning(f"Cache operation failed: {e}")
            return await fallback_func()


class GracefulDegradation:
    """
    Graceful degradation strategies for cache failures.

    Provides fallback options when cache is unavailable:
    1. Stale data (if available)
    2. Default values
    3. Database direct access (with rate limiting)
    """

    def __init__(
        self,
        redis_client: Any,
        stale_ttl: int = 3600,
        max_db_requests_per_second: int = 100,
    ):
        self.redis = redis_client
        self.stale_ttl = stale_ttl
        self.max_db_rps = max_db_requests_per_second
        self._db_request_count = 0
        self._last_reset = time.time()

    def _check_rate_limit(self) -> bool:
        """Check if database requests are within rate limit."""
        current_time = time.time()

        if current_time - self._last_reset >= 1.0:
            self._db_request_count = 0
            self._last_reset = current_time

        if self._db_request_count >= self.max_db_rps:
            return False

        self._db_request_count += 1
        return True

    async def get_with_fallback(
        self,
        key: str,
        fetch_func: Callable[[], Any],
        default_value: Optional[Any] = None,
        ttl: int = 300,
    ) -> tuple[Any, str]:
        """
        Get value with graceful degradation.

        Returns (value, source) where source is:
        - "cache": Fresh cached data
        - "stale": Stale cached data
        - "database": Fresh database data
        - "default": Default fallback value
        """
        try:
            data = await self.redis.get(key)
            if data is not None:
                return data, "cache"
        except Exception as e:
            logger.warning(f"Cache read failed: {e}")

        stale_key = f"stale:{key}"
        try:
            stale_data = await self.redis.get(stale_key)
            if stale_data is not None:
                logger.info(f"Serving stale data for {key}")
                return stale_data, "stale"
        except Exception:
            pass

        if self._check_rate_limit():
            try:
                data = await fetch_func()

                try:
                    await self.redis.setex(key, ttl, data)
                    await self.redis.setex(stale_key, self.stale_ttl, data)
                except Exception:
                    pass

                return data, "database"

            except Exception as e:
                logger.error(f"Database fetch failed: {e}")

        if default_value is not None:
            logger.warning(f"Using default value for {key}")
            return default_value, "default"

        raise RuntimeError(f"All fallback options exhausted for {key}")
