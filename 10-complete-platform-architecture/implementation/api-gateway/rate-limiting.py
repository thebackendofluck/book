#!/usr/bin/env python3
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
Chapter 42 - Complete Platform Architecture
Custom Rate Limiting Plugin for iGambling Platform

Implements player-tier-aware rate limiting with:
- Sliding window algorithm using Redis sorted sets
- Player tier detection (VIP, standard, anonymous)
- Jurisdiction-specific limits (some regulators mandate max bets/hour)
- Responsible gaming integration (auto-reduce limits for flagged players)
- Burst allowance for game round sequences

Usage:
    # As standalone service (sidecar or middleware)
    python rate-limiting.py --port 8090 --redis redis://localhost:6379

    # Test mode
    python rate-limiting.py --test

Dependencies:
    pip install redis aiohttp
"""

import argparse
import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

try:
    import redis.asyncio as aioredis
except ImportError:
    print("Install redis: pip install redis")
    exit(1)

try:
    from aiohttp import web
except ImportError:
    print("Install aiohttp: pip install aiohttp")
    exit(1)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("rate-limiter")


# ──────────────────────────────────────────────────────────────
# Rate Limit Configuration per Player Tier and Endpoint
# ──────────────────────────────────────────────────────────────

class PlayerTier(Enum):
    ANONYMOUS = "anonymous"
    STANDARD = "standard"
    VIP = "vip"
    WHALE = "whale"         # High-value players
    RESTRICTED = "restricted"  # Responsible gaming flagged


@dataclass
class RateLimitRule:
    """Rate limit rule for a specific endpoint pattern."""
    path_pattern: str
    method: str = "*"
    window_seconds: int = 60
    limits: dict = field(default_factory=dict)  # tier -> max_requests
    burst_allowance: int = 0        # Extra requests allowed in burst
    burst_window_seconds: int = 5   # Burst window
    jurisdiction_overrides: dict = field(default_factory=dict)


# Default rate limit rules for the iGambling platform
DEFAULT_RULES = [
    RateLimitRule(
        path_pattern="/api/v1/games/rounds",
        method="POST",
        window_seconds=60,
        limits={
            PlayerTier.ANONYMOUS: 0,        # Must be authenticated
            PlayerTier.STANDARD: 300,       # 5 rounds/sec
            PlayerTier.VIP: 600,            # 10 rounds/sec
            PlayerTier.WHALE: 600,
            PlayerTier.RESTRICTED: 60,      # Responsible gaming: 1/sec
        },
        burst_allowance=20,
        burst_window_seconds=5,
        jurisdiction_overrides={
            "UKGC": {PlayerTier.STANDARD: 180, PlayerTier.VIP: 300},  # UK stricter
            "SGA": {PlayerTier.STANDARD: 120, PlayerTier.VIP: 240},   # Sweden stricter
        }
    ),
    RateLimitRule(
        path_pattern="/api/v1/payments/deposits",
        method="POST",
        window_seconds=3600,  # Per hour
        limits={
            PlayerTier.ANONYMOUS: 0,
            PlayerTier.STANDARD: 10,        # 10 deposits/hour
            PlayerTier.VIP: 30,
            PlayerTier.WHALE: 50,
            PlayerTier.RESTRICTED: 3,       # Heavily restricted
        },
    ),
    RateLimitRule(
        path_pattern="/api/v1/payments/withdrawals",
        method="POST",
        window_seconds=3600,
        limits={
            PlayerTier.ANONYMOUS: 0,
            PlayerTier.STANDARD: 5,
            PlayerTier.VIP: 15,
            PlayerTier.WHALE: 20,
            PlayerTier.RESTRICTED: 2,
        },
    ),
    RateLimitRule(
        path_pattern="/api/v1/games",
        method="GET",
        window_seconds=60,
        limits={
            PlayerTier.ANONYMOUS: 30,       # Browsing allowed
            PlayerTier.STANDARD: 120,
            PlayerTier.VIP: 300,
            PlayerTier.WHALE: 300,
            PlayerTier.RESTRICTED: 60,
        },
    ),
    RateLimitRule(
        path_pattern="/api/v1/players/profile",
        method="*",
        window_seconds=60,
        limits={
            PlayerTier.ANONYMOUS: 0,
            PlayerTier.STANDARD: 30,
            PlayerTier.VIP: 60,
            PlayerTier.WHALE: 60,
            PlayerTier.RESTRICTED: 30,
        },
    ),
    RateLimitRule(
        path_pattern="/api/v1",
        method="*",
        window_seconds=60,
        limits={
            PlayerTier.ANONYMOUS: 60,
            PlayerTier.STANDARD: 600,
            PlayerTier.VIP: 3000,
            PlayerTier.WHALE: 3000,
            PlayerTier.RESTRICTED: 120,
        },
    ),
]


@dataclass
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    reset_at: float
    retry_after: Optional[float] = None
    tier: str = ""
    rule_matched: str = ""

    def to_headers(self) -> dict:
        headers = {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(max(0, self.remaining)),
            "X-RateLimit-Reset": str(int(self.reset_at)),
            "X-RateLimit-Tier": self.tier,
        }
        if not self.allowed and self.retry_after:
            headers["Retry-After"] = str(int(self.retry_after))
        return headers


class SlidingWindowRateLimiter:
    """
    Sliding window rate limiter using Redis sorted sets.

    Each request is stored as a member with its timestamp as the score.
    The window slides by removing expired entries before counting.
    """

    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self.redis_url = redis_url
        self.redis: Optional[aioredis.Redis] = None
        self.rules = DEFAULT_RULES

    async def connect(self):
        self.redis = aioredis.from_url(
            self.redis_url,
            decode_responses=True,
            max_connections=50
        )
        logger.info(f"Connected to Redis at {self.redis_url}")

    async def close(self):
        if self.redis:
            await self.redis.close()

    def _match_rule(self, path: str, method: str) -> Optional[RateLimitRule]:
        """Find the most specific matching rule for the request."""
        best_match = None
        best_specificity = 0

        for rule in self.rules:
            if rule.method != "*" and rule.method != method:
                continue
            if path.startswith(rule.path_pattern):
                specificity = len(rule.path_pattern)
                if specificity > best_specificity:
                    best_specificity = specificity
                    best_match = rule
        return best_match

    def _get_limit(self, rule: RateLimitRule, tier: PlayerTier,
                   jurisdiction: Optional[str] = None) -> int:
        """Get the rate limit for a given tier, considering jurisdiction overrides."""
        if jurisdiction and jurisdiction in rule.jurisdiction_overrides:
            overrides = rule.jurisdiction_overrides[jurisdiction]
            if tier in overrides:
                return overrides[tier]

        return rule.limits.get(tier, 0)

    async def check_rate_limit(
        self,
        identifier: str,           # player_id or IP
        path: str,
        method: str = "GET",
        tier: PlayerTier = PlayerTier.STANDARD,
        jurisdiction: Optional[str] = None,
    ) -> RateLimitResult:
        """Check if a request is allowed under rate limits."""

        rule = self._match_rule(path, method)
        if not rule:
            return RateLimitResult(
                allowed=True, limit=999999, remaining=999999,
                reset_at=time.time() + 60, tier=tier.value,
                rule_matched="none"
            )

        limit = self._get_limit(rule, tier, jurisdiction)

        # Zero limit means blocked entirely
        if limit == 0:
            return RateLimitResult(
                allowed=False, limit=0, remaining=0,
                reset_at=time.time() + rule.window_seconds,
                retry_after=float(rule.window_seconds),
                tier=tier.value, rule_matched=rule.path_pattern
            )

        now = time.time()
        window_start = now - rule.window_seconds
        key = f"rl:{identifier}:{rule.path_pattern}:{method}"

        # Use Redis pipeline for atomicity
        pipe = self.redis.pipeline()  # ty:ignore[possibly-missing-attribute]

        # Remove expired entries
        pipe.zremrangebyscore(key, "-inf", window_start)

        # Count current entries in window
        pipe.zcard(key)

        # Add current request (optimistic - we'll check count)
        member = f"{now}:{id(object())}"  # Unique member
        pipe.zadd(key, {member: now})

        # Set TTL on the key
        pipe.expire(key, rule.window_seconds + 10)

        results = await pipe.execute()
        current_count = results[1]  # zcard result (before adding)

        if current_count >= limit:
            # Over limit - remove the optimistically added entry
            await self.redis.zrem(key, member)  # ty:ignore[possibly-missing-attribute]

            # Calculate retry-after based on oldest entry in window
            oldest = await self.redis.zrange(key, 0, 0, withscores=True)  # ty:ignore[possibly-missing-attribute]
            retry_after = rule.window_seconds
            if oldest:
                oldest_time = oldest[0][1]
                retry_after = max(1, oldest_time + rule.window_seconds - now)

            return RateLimitResult(
                allowed=False,
                limit=limit,
                remaining=0,
                reset_at=now + retry_after,
                retry_after=retry_after,
                tier=tier.value,
                rule_matched=rule.path_pattern
            )

        # Check burst allowance
        if rule.burst_allowance > 0:
            burst_start = now - rule.burst_window_seconds
            burst_key = f"rl:burst:{identifier}:{rule.path_pattern}"

            burst_pipe = self.redis.pipeline()  # ty:ignore[possibly-missing-attribute]
            burst_pipe.zremrangebyscore(burst_key, "-inf", burst_start)
            burst_pipe.zcard(burst_key)
            burst_pipe.zadd(burst_key, {member: now})
            burst_pipe.expire(burst_key, rule.burst_window_seconds + 5)

            burst_results = await burst_pipe.execute()
            burst_count = burst_results[1]

            if burst_count > rule.burst_allowance:
                await self.redis.zrem(key, member)  # ty:ignore[possibly-missing-attribute]
                await self.redis.zrem(burst_key, member)  # ty:ignore[possibly-missing-attribute]
                return RateLimitResult(
                    allowed=False,
                    limit=limit,
                    remaining=max(0, limit - current_count),
                    reset_at=now + rule.burst_window_seconds,
                    retry_after=float(rule.burst_window_seconds),
                    tier=tier.value,
                    rule_matched=f"{rule.path_pattern} (burst)"
                )

        remaining = limit - current_count - 1
        reset_at = now + rule.window_seconds

        return RateLimitResult(
            allowed=True,
            limit=limit,
            remaining=max(0, remaining),
            reset_at=reset_at,
            tier=tier.value,
            rule_matched=rule.path_pattern
        )

    async def get_player_usage(self, player_id: str) -> dict:
        """Get current rate limit usage for a player across all rules."""
        now = time.time()
        usage = {}

        for rule in self.rules:
            key_pattern = f"rl:{player_id}:{rule.path_pattern}:*"
            keys = []
            async for k in self.redis.scan_iter(match=key_pattern):  # ty:ignore[possibly-missing-attribute]
                keys.append(k)

            for key in keys:
                window_start = now - rule.window_seconds
                await self.redis.zremrangebyscore(key, "-inf", window_start)  # ty:ignore[possibly-missing-attribute]
                count = await self.redis.zcard(key)  # ty:ignore[possibly-missing-attribute]
                usage[key] = {
                    "count": count,
                    "window_seconds": rule.window_seconds,
                    "path": rule.path_pattern
                }

        return usage

    async def reset_player_limits(self, player_id: str):
        """Reset all rate limits for a player (admin action)."""
        pattern = f"rl:{player_id}:*"
        keys = []
        async for k in self.redis.scan_iter(match=pattern):  # ty:ignore[possibly-missing-attribute]
            keys.append(k)
        if keys:
            await self.redis.delete(*keys)  # ty:ignore[possibly-missing-attribute]
            logger.info(f"Reset {len(keys)} rate limit keys for player {player_id}")


# ──────────────────────────────────────────────────────────────
# HTTP Middleware Server
# ──────────────────────────────────────────────────────────────

class RateLimitServer:
    """HTTP server that acts as a rate limit checking service."""

    def __init__(self, limiter: SlidingWindowRateLimiter):
        self.limiter = limiter

    async def handle_check(self, request: web.Request) -> web.Response:
        """
        Check rate limit for a request.
        Called by Kong/Envoy as an external auth service.

        Expected headers:
            X-Player-ID: player UUID (or IP for anonymous)
            X-Player-Tier: anonymous|standard|vip|whale|restricted
            X-Original-URI: the original request path
            X-Original-Method: the original HTTP method
            X-Player-Jurisdiction: jurisdiction code
        """
        player_id = request.headers.get("X-Player-ID", request.remote)
        tier_str = request.headers.get("X-Player-Tier", "anonymous")
        path = request.headers.get("X-Original-URI", "/")
        method = request.headers.get("X-Original-Method", "GET")
        jurisdiction = request.headers.get("X-Player-Jurisdiction")

        try:
            tier = PlayerTier(tier_str)
        except ValueError:
            tier = PlayerTier.ANONYMOUS

        result = await self.limiter.check_rate_limit(
            identifier=player_id,  # ty:ignore[invalid-argument-type]
            path=path,
            method=method,
            tier=tier,
            jurisdiction=jurisdiction
        )

        headers = result.to_headers()

        if result.allowed:
            return web.Response(status=200, headers=headers)
        else:
            return web.json_response(
                {
                    "error": "rate_limit_exceeded",
                    "message": f"Rate limit exceeded for tier '{tier.value}'. "
                               f"Try again in {int(result.retry_after or 60)} seconds.",
                    "limit": result.limit,
                    "remaining": result.remaining,
                    "retry_after": result.retry_after,
                    "rule": result.rule_matched,
                },
                status=429,
                headers=headers
            )

    async def handle_usage(self, request: web.Request) -> web.Response:
        """Get rate limit usage for a player (admin endpoint)."""
        player_id = request.match_info.get("player_id")
        if not player_id:
            return web.json_response({"error": "player_id required"}, status=400)

        usage = await self.limiter.get_player_usage(player_id)
        return web.json_response({"player_id": player_id, "usage": usage})

    async def handle_reset(self, request: web.Request) -> web.Response:
        """Reset rate limits for a player (admin endpoint)."""
        player_id = request.match_info.get("player_id")
        if not player_id:
            return web.json_response({"error": "player_id required"}, status=400)

        await self.limiter.reset_player_limits(player_id)
        return web.json_response({"status": "reset", "player_id": player_id})

    async def handle_health(self, request: web.Request) -> web.Response:
        """Health check endpoint."""
        try:
            await self.limiter.redis.ping()  # ty:ignore[invalid-await, unresolved-attribute]
            return web.json_response({"status": "healthy", "redis": "connected"})
        except Exception as e:
            return web.json_response(
                {"status": "unhealthy", "redis": str(e)}, status=503
            )

    async def handle_rules(self, request: web.Request) -> web.Response:
        """List all configured rate limit rules."""
        rules = []
        for rule in self.limiter.rules:
            rules.append({
                "path_pattern": rule.path_pattern,
                "method": rule.method,
                "window_seconds": rule.window_seconds,
                "limits": {t.value: l for t, l in rule.limits.items()},
                "burst_allowance": rule.burst_allowance,
                "jurisdiction_overrides": {
                    j: {t.value: l for t, l in limits.items()}
                    for j, limits in rule.jurisdiction_overrides.items()
                }
            })
        return web.json_response({"rules": rules})


async def run_test():
    """Run a test simulation of the rate limiter."""
    limiter = SlidingWindowRateLimiter("redis://localhost:6379")
    await limiter.connect()

    print("\n" + "=" * 60)
    print("Rate Limiter Test Simulation")
    print("=" * 60)

    test_cases = [
        ("player-001", "/api/v1/games/rounds", "POST", PlayerTier.STANDARD, None),
        ("player-001", "/api/v1/games/rounds", "POST", PlayerTier.VIP, None),
        ("player-001", "/api/v1/games/rounds", "POST", PlayerTier.RESTRICTED, None),
        ("player-002", "/api/v1/payments/deposits", "POST", PlayerTier.STANDARD, None),
        ("player-003", "/api/v1/games/rounds", "POST", PlayerTier.STANDARD, "UKGC"),
        ("anon-ip", "/api/v1/games", "GET", PlayerTier.ANONYMOUS, None),
    ]

    for player_id, path, method, tier, jurisdiction in test_cases:
        result = await limiter.check_rate_limit(
            player_id, path, method, tier, jurisdiction
        )
        print(f"\n  {tier.value:12s} | {method:4s} {path}")
        print(f"  Player: {player_id} | Jurisdiction: {jurisdiction or 'default'}")
        print(f"  Allowed: {result.allowed} | Limit: {result.limit} | "
              f"Remaining: {result.remaining}")
        print(f"  Rule: {result.rule_matched}")

    # Simulate hitting the limit
    print("\n" + "-" * 60)
    print("Simulating rapid requests to hit limit...")
    player = "test-player-rapid"
    for i in range(65):
        result = await limiter.check_rate_limit(
            player, "/api/v1/games", "GET", PlayerTier.ANONYMOUS
        )
        if not result.allowed:
            print(f"  Blocked at request #{i+1} (limit={result.limit}, "
                  f"retry_after={result.retry_after:.1f}s)")
            break
    else:
        print(f"  All 65 requests allowed (limit={result.limit})")

    # Clean up test data
    await limiter.reset_player_limits("player-001")
    await limiter.reset_player_limits("player-002")
    await limiter.reset_player_limits("player-003")
    await limiter.reset_player_limits("anon-ip")
    await limiter.reset_player_limits(player)

    await limiter.close()
    print("\nTest complete.")


def main():
    parser = argparse.ArgumentParser(
        description="Casino Platform Rate Limiting Service"
    )
    parser.add_argument("--port", type=int, default=8090,
                        help="HTTP port (default: 8090)")
    parser.add_argument("--redis", default="redis://localhost:6379",
                        help="Redis URL")
    parser.add_argument("--test", action="store_true",
                        help="Run test simulation")
    args = parser.parse_args()

    if args.test:
        asyncio.run(run_test())
        return

    limiter = SlidingWindowRateLimiter(args.redis)
    server = RateLimitServer(limiter)

    app = web.Application()
    app.router.add_get("/check", server.handle_check)
    app.router.add_get("/usage/{player_id}", server.handle_usage)
    app.router.add_post("/reset/{player_id}", server.handle_reset)
    app.router.add_get("/health", server.handle_health)
    app.router.add_get("/rules", server.handle_rules)

    async def on_startup(app):
        await limiter.connect()

    async def on_cleanup(app):
        await limiter.close()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    logger.info(f"Starting rate limit service on port {args.port}")
    web.run_app(app, port=args.port)


if __name__ == "__main__":
    main()
