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
Core caching patterns for iGaming platforms.

Implements:
- Cache-aside (lazy loading) pattern
- Write-through caching
- Read-through caching
- Write-behind (write-back) caching

All patterns designed for casino requirements:
- Sub-millisecond latency
- Strong consistency for financial data
- High availability (99.99% uptime)
"""

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CacheStrategy(Enum):
    """Cache population and invalidation strategies."""

    CACHE_ASIDE = "cache_aside"  # Lazy loading
    WRITE_THROUGH = "write_through"  # Sync write to cache + DB
    READ_THROUGH = "read_through"  # Cache handles DB reads
    WRITE_BEHIND = "write_behind"  # Async write to DB


@dataclass
class CacheConfig:
    """Configuration for cache behavior."""

    default_ttl: int = 300  # 5 minutes
    max_ttl: int = 3600  # 1 hour
    namespace: str = "igaming"
    enable_compression: bool = True
    compression_threshold: int = 1024  # bytes
    enable_stats: bool = True
    retry_attempts: int = 3
    retry_delay: float = 0.1


@dataclass
class CacheResult:
    """Result of a cache operation."""

    success: bool
    data: Optional[Any] = None
    hit: bool = False
    latency_ms: float = 0.0
    source: str = "unknown"  # cache, database, fallback
    error: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


class CacheManager:
    """
    Enterprise cache manager for iGaming platforms.

    Supports multiple caching strategies with built-in:
    - Metrics collection
    - Error handling and fallbacks
    - Compression for large values
    - Namespace isolation
    """

    def __init__(
        self,
        redis_client: Any,
        config: Optional[CacheConfig] = None,
        strategy: CacheStrategy = CacheStrategy.CACHE_ASIDE,
    ):
        self.redis = redis_client
        self.config = config or CacheConfig()
        self.strategy = strategy
        self._stats = {
            "hits": 0,
            "misses": 0,
            "errors": 0,
            "total_latency_ms": 0.0,
        }

    def _make_key(self, key: str) -> str:
        """Create namespaced cache key."""
        return f"{self.config.namespace}:{key}"

    def _serialize(self, value: Any) -> bytes:
        """Serialize value for storage."""
        data = json.dumps(value, default=str)
        encoded = data.encode("utf-8")

        if self.config.enable_compression and len(encoded) > self.config.compression_threshold:
            import zlib

            return b"compressed:" + zlib.compress(encoded)
        return encoded

    def _deserialize(self, data: bytes) -> Any:
        """Deserialize stored value."""
        if data.startswith(b"compressed:"):
            import zlib

            data = zlib.decompress(data[11:])
        return json.loads(data.decode("utf-8"))

    async def get_player_balance(self, player_id: str) -> CacheResult:
        """
        Cache-aside pattern for player balance.

        Casino-critical operation with:
        - 5-minute TTL (balances can change frequently)
        - Fallback to database on cache miss
        - Metrics collection for monitoring
        """
        start_time = time.perf_counter()
        cache_key = self._make_key(f"player_balance:{player_id}")

        try:
            cached_balance = await self.redis.get(cache_key)

            if cached_balance:
                self._stats["hits"] += 1
                latency = (time.perf_counter() - start_time) * 1000

                return CacheResult(
                    success=True,
                    data=float(cached_balance),
                    hit=True,
                    latency_ms=latency,
                    source="cache",
                )

            self._stats["misses"] += 1
            return CacheResult(
                success=True,
                data=None,
                hit=False,
                latency_ms=(time.perf_counter() - start_time) * 1000,
                source="cache_miss",
            )

        except Exception as e:
            self._stats["errors"] += 1
            logger.error(f"Cache error for player {player_id}: {e}")
            return CacheResult(
                success=False,
                error=str(e),
                latency_ms=(time.perf_counter() - start_time) * 1000,
            )

    async def set_player_balance(
        self,
        player_id: str,
        balance: float,
        ttl: Optional[int] = None,
    ) -> CacheResult:
        """
        Store player balance in cache.

        Args:
            player_id: Unique player identifier
            balance: Current balance value
            ttl: Time-to-live in seconds (default: 300)
        """
        start_time = time.perf_counter()
        cache_key = self._make_key(f"player_balance:{player_id}")
        ttl = ttl or self.config.default_ttl

        try:
            await self.redis.setex(cache_key, ttl, str(balance))

            return CacheResult(
                success=True,
                data=balance,
                latency_ms=(time.perf_counter() - start_time) * 1000,
                source="cache_set",
            )

        except Exception as e:
            self._stats["errors"] += 1
            logger.error(f"Cache set error for player {player_id}: {e}")
            return CacheResult(
                success=False,
                error=str(e),
                latency_ms=(time.perf_counter() - start_time) * 1000,
            )

    async def update_player_balance_write_through(
        self,
        player_id: str,
        new_balance: float,
        db_update_func: Callable[[str, float], Any],
    ) -> CacheResult:
        """
        Write-through pattern ensuring cache consistency.

        1. Update database first (source of truth)
        2. Update cache immediately
        3. Publish balance update event for subscribers

        Critical for financial data integrity.
        """
        start_time = time.perf_counter()
        cache_key = self._make_key(f"player_balance:{player_id}")

        try:
            await db_update_func(player_id, new_balance)
            await self.redis.setex(cache_key, self.config.default_ttl, str(new_balance))
            await self.redis.publish(
                f"balance_updates:{player_id}",
                json.dumps(
                    {
                        "player_id": player_id,
                        "balance": new_balance,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                ),
            )

            logger.info(f"Write-through update for player {player_id}: {new_balance}")

            return CacheResult(
                success=True,
                data=new_balance,
                latency_ms=(time.perf_counter() - start_time) * 1000,
                source="write_through",
                metadata={"published": True},
            )

        except Exception as e:
            self._stats["errors"] += 1
            logger.error(f"Write-through error for player {player_id}: {e}")
            return CacheResult(
                success=False,
                error=str(e),
                latency_ms=(time.perf_counter() - start_time) * 1000,
            )

    async def get_game_state(
        self,
        game_id: str,
        db_fetch_func: Optional[Callable[[str], Any]] = None,
    ) -> CacheResult:
        """
        Cache-aside pattern for game state.

        Game state has shorter TTL (60s) due to frequent updates.
        Uses read-through if db_fetch_func provided.
        """
        start_time = time.perf_counter()
        cache_key = self._make_key(f"game_state:{game_id}")

        try:
            cached_state = await self.redis.get(cache_key)

            if cached_state:
                self._stats["hits"] += 1
                return CacheResult(
                    success=True,
                    data=self._deserialize(cached_state),
                    hit=True,
                    latency_ms=(time.perf_counter() - start_time) * 1000,
                    source="cache",
                )

            self._stats["misses"] += 1

            if db_fetch_func:
                state = await db_fetch_func(game_id)
                if state:
                    await self.redis.setex(
                        cache_key,
                        60,  # Shorter TTL for game state
                        self._serialize(state),
                    )
                    return CacheResult(
                        success=True,
                        data=state,
                        hit=False,
                        latency_ms=(time.perf_counter() - start_time) * 1000,
                        source="database",
                    )

            return CacheResult(
                success=True,
                data=None,
                hit=False,
                latency_ms=(time.perf_counter() - start_time) * 1000,
                source="cache_miss",
            )

        except Exception as e:
            self._stats["errors"] += 1
            logger.error(f"Cache error for game {game_id}: {e}")
            return CacheResult(
                success=False,
                error=str(e),
                latency_ms=(time.perf_counter() - start_time) * 1000,
            )

    async def invalidate(self, pattern: str) -> int:
        """
        Invalidate cache entries matching pattern.

        Returns number of keys deleted.
        """
        full_pattern = self._make_key(pattern)
        keys = []

        async for key in self.redis.scan_iter(match=full_pattern):
            keys.append(key)

        if keys:
            deleted = await self.redis.delete(*keys)
            logger.info(f"Invalidated {deleted} keys matching {pattern}")
            return deleted

        return 0

    def get_stats(self) -> dict[str, Any]:
        """Get cache performance statistics."""
        total = self._stats["hits"] + self._stats["misses"]
        hit_rate = (self._stats["hits"] / total * 100) if total > 0 else 0.0
        avg_latency = (
            self._stats["total_latency_ms"] / total if total > 0 else 0.0
        )

        return {
            "hits": self._stats["hits"],
            "misses": self._stats["misses"],
            "errors": self._stats["errors"],
            "hit_rate_percent": round(hit_rate, 2),
            "avg_latency_ms": round(avg_latency, 3),
            "total_operations": total,
        }


class CacheKeyBuilder:
    """
    Utility for building consistent cache keys.

    Ensures proper namespacing and key structure across the platform.
    """

    def __init__(self, namespace: str = "igaming"):
        self.namespace = namespace

    def player_balance(self, player_id: str) -> str:
        return f"{self.namespace}:player_balance:{player_id}"

    def player_session(self, session_id: str) -> str:
        return f"{self.namespace}:session:{session_id}"

    def game_state(self, game_id: str) -> str:
        return f"{self.namespace}:game_state:{game_id}"

    def leaderboard(self, tournament_id: str) -> str:
        return f"{self.namespace}:leaderboard:{tournament_id}"

    def live_odds(self, event_id: str, market_id: str) -> str:
        return f"{self.namespace}:odds:{event_id}:{market_id}"

    def player_preferences(self, player_id: str) -> str:
        return f"{self.namespace}:prefs:{player_id}"

    def rate_limit(self, player_id: str, action: str) -> str:
        return f"{self.namespace}:ratelimit:{player_id}:{action}"

    def hash_key(self, *components: str) -> str:
        """Create hash-based key for complex lookups."""
        combined = ":".join(components)
        key_hash = hashlib.sha256(combined.encode()).hexdigest()[:16]
        return f"{self.namespace}:hash:{key_hash}"
