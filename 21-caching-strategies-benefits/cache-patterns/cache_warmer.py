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
Cache warming strategies for iGaming platforms.

Implements:
- Startup warming (pre-populate frequently accessed data)
- Scheduled warming (periodic refresh)
- Predictive warming (anticipate access patterns)
- Event-driven warming (warm on data changes)

Critical for maintaining high cache hit rates during peak hours.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional, Union

logger = logging.getLogger(__name__)


class WarmingStrategy(Enum):
    """Cache warming strategies."""

    STARTUP = "startup"  # Warm on application start
    SCHEDULED = "scheduled"  # Periodic refresh
    PREDICTIVE = "predictive"  # ML-based prediction
    EVENT_DRIVEN = "event_driven"  # Warm on data changes
    LAZY = "lazy"  # Warm on first access


@dataclass
class WarmingResult:
    """Result of a cache warming operation."""

    success: bool
    keys_warmed: int = 0
    keys_failed: int = 0
    duration_ms: float = 0.0
    strategy: WarmingStrategy = WarmingStrategy.STARTUP
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class WarmingConfig:
    """Configuration for cache warming."""

    batch_size: int = 100
    concurrent_tasks: int = 10
    ttl_balance: int = 3600  # 1 hour for balance data
    ttl_preferences: int = 3600  # 1 hour for preferences
    ttl_session: int = 1800  # 30 minutes for sessions
    ttl_game_config: int = 86400  # 24 hours for game configs
    active_hours_lookback: int = 86400  # 24 hours


class CacheWarmer:
    """
    Cache warming manager for iGaming platforms.

    Ensures frequently accessed data is pre-loaded into cache
    to maintain high hit rates and low latency during peak traffic.
    """

    def __init__(
        self,
        redis_client: Any,
        db_client: Any,
        config: Optional[WarmingConfig] = None,
    ):
        self.redis = redis_client
        self.db = db_client
        self.config = config or WarmingConfig()
        self._total_warmings: int = 0
        self._total_keys: int = 0
        self._total_failures: int = 0
        self._last_warming: Optional[str] = None

    async def warm_player_caches(self) -> WarmingResult:
        """
        Warm caches with frequently accessed player data.

        Pre-loads for active players (last 24 hours):
        - Player balances
        - Session data
        - Game preferences
        - Betting limits
        """
        start_time = time.perf_counter()
        result = WarmingResult(success=True, strategy=WarmingStrategy.STARTUP)

        try:
            active_players = await self.db.get_active_players(
                self.config.active_hours_lookback
            )
            logger.info(f"Warming caches for {len(active_players)} active players")

            for i in range(0, len(active_players), self.config.batch_size):
                batch = active_players[i : i + self.config.batch_size]
                tasks = [self._warm_player(player) for player in batch]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for r in results:
                    if isinstance(r, Exception):
                        result.keys_failed += 1
                        result.errors.append(str(r))
                    elif isinstance(r, int):
                        result.keys_warmed += r

            result.duration_ms = (time.perf_counter() - start_time) * 1000
            self._update_stats(result)

            logger.info(
                f"Cache warming complete: {result.keys_warmed} keys warmed, "
                f"{result.keys_failed} failed, {result.duration_ms:.2f}ms"
            )

        except Exception as e:
            result.success = False
            result.errors.append(str(e))
            logger.error(f"Cache warming failed: {e}")

        return result

    async def _warm_player(self, player: Any) -> int:
        """Warm cache for a single player."""
        keys_warmed = 0
        player_id = player.id if hasattr(player, "id") else player.get("id")

        try:
            balance = await self.db.get_player_balance(player_id)
            balance_key = f"igaming:player_balance:{player_id}"
            await self.redis.setex(
                balance_key, self.config.ttl_balance, str(balance)
            )
            keys_warmed += 1
        except Exception as e:
            logger.warning(f"Failed to warm balance for {player_id}: {e}")

        try:
            prefs = await self.db.get_player_preferences(player_id)
            prefs_key = f"igaming:player_prefs:{player_id}"
            import json

            await self.redis.setex(
                prefs_key, self.config.ttl_preferences, json.dumps(prefs)
            )
            keys_warmed += 1
        except Exception as e:
            logger.warning(f"Failed to warm preferences for {player_id}: {e}")

        try:
            limits = await self.db.get_player_limits(player_id)
            limits_key = f"igaming:player_limits:{player_id}"
            import json

            await self.redis.setex(
                limits_key, self.config.ttl_preferences, json.dumps(limits)
            )
            keys_warmed += 1
        except Exception as e:
            logger.warning(f"Failed to warm limits for {player_id}: {e}")

        return keys_warmed

    async def warm_game_configs(self) -> WarmingResult:
        """
        Warm game configuration caches.

        Pre-loads:
        - Game rules and parameters
        - Payout tables
        - Bonus configurations
        - Jackpot pools
        """
        start_time = time.perf_counter()
        result = WarmingResult(success=True, strategy=WarmingStrategy.STARTUP)

        try:
            games = await self.db.get_all_games()
            logger.info(f"Warming configs for {len(games)} games")

            for game in games:
                game_id = game.id if hasattr(game, "id") else game.get("id")

                try:
                    config = await self.db.get_game_config(game_id)
                    config_key = f"igaming:game_config:{game_id}"
                    import json

                    await self.redis.setex(
                        config_key,
                        self.config.ttl_game_config,
                        json.dumps(config),
                    )
                    result.keys_warmed += 1

                    payout = await self.db.get_payout_table(game_id)
                    payout_key = f"igaming:payout_table:{game_id}"
                    await self.redis.setex(
                        payout_key,
                        self.config.ttl_game_config,
                        json.dumps(payout),
                    )
                    result.keys_warmed += 1

                except Exception as e:
                    result.keys_failed += 1
                    logger.warning(f"Failed to warm config for game {game_id}: {e}")

            result.duration_ms = (time.perf_counter() - start_time) * 1000

        except Exception as e:
            result.success = False
            result.errors.append(str(e))

        return result

    async def warm_leaderboards(self) -> WarmingResult:
        """
        Warm tournament leaderboard caches.

        Pre-loads active tournament rankings using Redis sorted sets.
        """
        start_time = time.perf_counter()
        result = WarmingResult(success=True, strategy=WarmingStrategy.STARTUP)

        try:
            tournaments = await self.db.get_active_tournaments()

            for tournament in tournaments:
                tournament_id = (
                    tournament.id
                    if hasattr(tournament, "id")
                    else tournament.get("id")
                )

                try:
                    rankings = await self.db.get_tournament_rankings(tournament_id)
                    leaderboard_key = f"igaming:leaderboard:{tournament_id}"

                    await self.redis.delete(leaderboard_key)

                    if rankings:
                        zadd_args = {}
                        for rank in rankings:
                            player_id = rank.get("player_id")
                            score = rank.get("score", 0)
                            zadd_args[player_id] = score

                        if zadd_args:
                            await self.redis.zadd(leaderboard_key, zadd_args)
                            await self.redis.expire(leaderboard_key, 3600)
                            result.keys_warmed += 1

                except Exception as e:
                    result.keys_failed += 1
                    logger.warning(
                        f"Failed to warm leaderboard {tournament_id}: {e}"
                    )

            result.duration_ms = (time.perf_counter() - start_time) * 1000

        except Exception as e:
            result.success = False
            result.errors.append(str(e))

        return result

    async def warm_live_odds(self, event_ids: list[str]) -> WarmingResult:
        """
        Warm live betting odds caches.

        Pre-loads current odds for specified events.
        Uses short TTL (60s) as odds change frequently.
        """
        start_time = time.perf_counter()
        result = WarmingResult(success=True, strategy=WarmingStrategy.EVENT_DRIVEN)

        for event_id in event_ids:
            try:
                odds_data = await self.db.get_event_odds(event_id)

                for market in odds_data.get("markets", []):
                    market_id = market.get("id")
                    odds_key = f"igaming:odds:{event_id}:{market_id}"

                    import json

                    await self.redis.setex(odds_key, 60, json.dumps(market))
                    result.keys_warmed += 1

            except Exception as e:
                result.keys_failed += 1
                result.errors.append(f"Event {event_id}: {e}")

        result.duration_ms = (time.perf_counter() - start_time) * 1000
        return result

    async def run_scheduled_warming(self, interval_seconds: int = 300) -> None:
        """
        Run periodic cache warming.

        Runs in background to keep caches fresh during off-peak hours.
        """
        logger.info(f"Starting scheduled warming every {interval_seconds}s")

        while True:
            try:
                await self.warm_player_caches()
                await self.warm_game_configs()
                await self.warm_leaderboards()
            except Exception as e:
                logger.error(f"Scheduled warming error: {e}")

            await asyncio.sleep(interval_seconds)

    def _update_stats(self, result: WarmingResult) -> None:
        """Update warming statistics."""
        self._total_warmings += 1
        self._total_keys += result.keys_warmed
        self._total_failures += result.keys_failed
        self._last_warming = datetime.now(timezone.utc).isoformat()

    def get_stats(self) -> dict[str, Any]:
        """Get cache warming statistics."""
        return {
            "total_warmings": self._total_warmings,
            "total_keys": self._total_keys,
            "total_failures": self._total_failures,
            "last_warming": self._last_warming,
            "avg_keys_per_warming": (
                self._total_keys / max(1, self._total_warmings)
            ),
        }


class PredictiveWarmer:
    """
    Predictive cache warming based on access patterns.

    Uses historical data to predict which keys will be accessed
    and pre-warms them before peak hours.
    """

    def __init__(
        self,
        redis_client: Any,
        db_client: Any,
        analytics_client: Any,
    ):
        self.redis = redis_client
        self.db = db_client
        self.analytics = analytics_client

    async def analyze_access_patterns(
        self, hours_back: int = 168
    ) -> dict[str, float]:
        """
        Analyze historical access patterns.

        Returns dict of key patterns with access frequency scores.
        """
        patterns = {}

        try:
            access_data = await self.analytics.get_cache_access_logs(hours_back)

            for entry in access_data:
                key_pattern = self._extract_pattern(entry.get("key", ""))
                patterns[key_pattern] = patterns.get(key_pattern, 0) + 1

            total = sum(patterns.values())
            return {k: v / total for k, v in patterns.items()}

        except Exception as e:
            logger.error(f"Access pattern analysis failed: {e}")
            return {}

    def _extract_pattern(self, key: str) -> str:
        """Extract pattern from cache key."""
        parts = key.split(":")
        if len(parts) >= 2:
            return f"{parts[0]}:{parts[1]}:*"
        return key

    async def warm_predicted_keys(
        self,
        threshold: float = 0.1,
    ) -> WarmingResult:
        """
        Warm keys predicted to be accessed based on patterns.

        Only warms patterns with frequency above threshold.
        """
        start_time = time.perf_counter()
        result = WarmingResult(success=True, strategy=WarmingStrategy.PREDICTIVE)

        patterns = await self.analyze_access_patterns()
        high_frequency = {k: v for k, v in patterns.items() if v >= threshold}

        logger.info(f"Warming {len(high_frequency)} high-frequency patterns")

        for pattern, frequency in high_frequency.items():
            try:
                keys_count = await self._warm_pattern(pattern)
                result.keys_warmed += keys_count
                result.metadata[pattern] = {
                    "frequency": frequency,
                    "keys_warmed": keys_count,
                }
            except Exception as e:
                result.keys_failed += 1
                result.errors.append(f"{pattern}: {e}")

        result.duration_ms = (time.perf_counter() - start_time) * 1000
        return result

    async def _warm_pattern(self, pattern: str) -> int:
        """Warm keys matching a pattern."""
        return 0
