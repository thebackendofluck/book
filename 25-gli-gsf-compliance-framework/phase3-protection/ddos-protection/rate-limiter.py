#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 25, GLI-GSF Compliance Framework.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
rate-limiter.py - Adaptive Rate Limiting for iGaming Platforms
GLI-GSF Phase 3 - DDoS Protection Controls

Implements multi-algorithm rate limiting with gambling-specific tuning:
  - Token bucket algorithm for burst tolerance on game endpoints
  - Sliding window counters for financial transaction limits
  - Per-IP and per-account rate tracking
  - Configurable thresholds for login, deposit, game-round, and API endpoints
  - Automatic escalation from rate-limit to temporary ban

GLI-GSF-5 Reference:
  - OGIS-2: Brute force protection on authentication endpoints
  - OGIS-4: Bot mitigation via rate limiting on game endpoints
  - OGIS-5: Platform availability through DDoS mitigation

Rate limit defaults (per GLI-GSF recommendations):
  - Login:      10 req/min per IP,  5 req/min per account
  - Deposit:     5 req/min per IP, 10 req/min per account
  - Withdrawal:  3 req/min per IP,  5 req/min per account
  - Game round: 60 req/min per IP, 120 req/min per account
  - Bonus claim: 3 req/hour per IP,  3 req/hour per account
  - API general:120 req/min per IP

Usage:
    python3 rate-limiter.py serve --port 8081
    python3 rate-limiter.py test --endpoint login --ip 192.168.1.100
    python3 rate-limiter.py config
    python3 rate-limiter.py demo

Requirements:
    pip install flask redis  (optional, falls back to in-memory)
"""

import argparse
import json
import logging
import os
import time
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Tuple

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("rate-limiter")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
class EndpointCategory(str, Enum):
    LOGIN = "login"
    REGISTRATION = "registration"
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    GAME_ROUND = "game_round"
    BONUS_CLAIM = "bonus_claim"
    ODDS_QUERY = "odds_query"
    API_GENERAL = "api_general"


@dataclass
class RateLimitConfig:
    """Rate limit configuration for an endpoint category."""
    category: str
    per_ip_requests: int
    per_ip_window_seconds: int
    per_account_requests: int
    per_account_window_seconds: int
    ban_threshold: int
    ban_duration_seconds: int
    burst_allowance: float         # Token bucket burst multiplier (1.0 = no burst)
    gli_gsf_reference: str


# GLI-GSF recommended rate limits for gambling platforms
DEFAULT_LIMITS: Dict[str, RateLimitConfig] = {
    EndpointCategory.LOGIN.value: RateLimitConfig(
        category="login", per_ip_requests=10, per_ip_window_seconds=60,
        per_account_requests=5, per_account_window_seconds=60,
        ban_threshold=5, ban_duration_seconds=900, burst_allowance=1.0,
        gli_gsf_reference="OGIS-2: Authentication brute force protection",
    ),
    EndpointCategory.REGISTRATION.value: RateLimitConfig(
        category="registration", per_ip_requests=3, per_ip_window_seconds=3600,
        per_account_requests=1, per_account_window_seconds=86400,
        ban_threshold=3, ban_duration_seconds=86400, burst_allowance=1.0,
        gli_gsf_reference="OGIS-4: Multi-accounting prevention",
    ),
    EndpointCategory.DEPOSIT.value: RateLimitConfig(
        category="deposit", per_ip_requests=5, per_ip_window_seconds=60,
        per_account_requests=10, per_account_window_seconds=60,
        ban_threshold=10, ban_duration_seconds=300, burst_allowance=1.5,
        gli_gsf_reference="OGIS-2: Financial transaction protection",
    ),
    EndpointCategory.WITHDRAWAL.value: RateLimitConfig(
        category="withdrawal", per_ip_requests=3, per_ip_window_seconds=60,
        per_account_requests=5, per_account_window_seconds=60,
        ban_threshold=5, ban_duration_seconds=600, burst_allowance=1.0,
        gli_gsf_reference="OGIS-2: Withdrawal fraud prevention",
    ),
    EndpointCategory.GAME_ROUND.value: RateLimitConfig(
        category="game_round", per_ip_requests=60, per_ip_window_seconds=60,
        per_account_requests=120, per_account_window_seconds=60,
        ban_threshold=10, ban_duration_seconds=120, burst_allowance=2.0,
        gli_gsf_reference="OGIS-4: Bot prevention on game endpoints",
    ),
    EndpointCategory.BONUS_CLAIM.value: RateLimitConfig(
        category="bonus_claim", per_ip_requests=3, per_ip_window_seconds=3600,
        per_account_requests=3, per_account_window_seconds=3600,
        ban_threshold=3, ban_duration_seconds=3600, burst_allowance=1.0,
        gli_gsf_reference="OGIS-4: Bonus abuse prevention",
    ),
    EndpointCategory.ODDS_QUERY.value: RateLimitConfig(
        category="odds_query", per_ip_requests=30, per_ip_window_seconds=60,
        per_account_requests=60, per_account_window_seconds=60,
        ban_threshold=10, ban_duration_seconds=300, burst_allowance=1.5,
        gli_gsf_reference="OGIS-4: Odds scraping prevention",
    ),
    EndpointCategory.API_GENERAL.value: RateLimitConfig(
        category="api_general", per_ip_requests=120, per_ip_window_seconds=60,
        per_account_requests=300, per_account_window_seconds=60,
        ban_threshold=15, ban_duration_seconds=300, burst_allowance=1.5,
        gli_gsf_reference="OGIS-5: General API abuse prevention",
    ),
}


# ---------------------------------------------------------------------------
# Token Bucket Algorithm
# ---------------------------------------------------------------------------
class TokenBucket:
    """Token bucket for burst-tolerant rate limiting on game endpoints."""

    def __init__(self, rate: float, capacity: float):
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.last_refill = time.monotonic()
        self._lock = threading.Lock()

    def consume(self, tokens: int = 1) -> bool:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_refill
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            self.last_refill = now
            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False


# ---------------------------------------------------------------------------
# Sliding Window Counter
# ---------------------------------------------------------------------------
class SlidingWindowCounter:
    """Sliding window counter for precise rate limiting on financial endpoints."""

    def __init__(self, window_seconds: int, max_requests: int):
        self.window = window_seconds
        self.max_requests = max_requests
        self._timestamps: List[float] = []
        self._lock = threading.Lock()

    def record_and_check(self) -> Tuple[bool, int]:
        with self._lock:
            now = time.monotonic()
            cutoff = now - self.window
            self._timestamps = [t for t in self._timestamps if t > cutoff]
            current = len(self._timestamps)
            if current < self.max_requests:
                self._timestamps.append(now)
                return True, current + 1
            return False, current


# ---------------------------------------------------------------------------
# Rate Limit Result
# ---------------------------------------------------------------------------
@dataclass
class RateLimitResult:
    allowed: bool
    category: str
    identifier: str
    identifier_type: str
    current_count: int
    limit: int
    window_seconds: int
    remaining: int
    retry_after_seconds: int
    banned: bool
    ban_remaining_seconds: int
    gli_gsf_reference: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed, "category": self.category,
            "identifier": self.identifier, "identifier_type": self.identifier_type,
            "current_count": self.current_count, "limit": self.limit,
            "window_seconds": self.window_seconds, "remaining": self.remaining,
            "retry_after": self.retry_after_seconds, "banned": self.banned,
            "ban_remaining": self.ban_remaining_seconds,
            "gli_gsf_reference": self.gli_gsf_reference,
            "timestamp": self.timestamp,
        }

    def to_headers(self) -> Dict[str, str]:
        return {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(self.remaining),
            "X-RateLimit-Reset": str(self.window_seconds),
            "Retry-After": str(self.retry_after_seconds) if not self.allowed else "0",
        }


# ---------------------------------------------------------------------------
# Rate Limiter Engine
# ---------------------------------------------------------------------------
class RateLimiter:
    """Main rate limiter combining token bucket and sliding window."""

    def __init__(self, config: Optional[Dict[str, RateLimitConfig]] = None):
        self.config = config or DEFAULT_LIMITS
        self._ip_windows: Dict[str, Dict[str, SlidingWindowCounter]] = defaultdict(dict)
        self._account_windows: Dict[str, Dict[str, SlidingWindowCounter]] = defaultdict(dict)
        self._buckets: Dict[str, Dict[str, TokenBucket]] = defaultdict(dict)
        self._violations: Dict[str, int] = defaultdict(int)
        self._bans: Dict[str, float] = {}
        self._lock = threading.Lock()
        self._audit_log: List[dict] = []
        logger.info(f"Rate limiter initialized with {len(self.config)} endpoint categories")

    def check(self, category: str, ip: str,
              account_id: Optional[str] = None) -> RateLimitResult:
        cfg = self.config.get(category, self.config[EndpointCategory.API_GENERAL.value])

        # Check ban list
        ban_key = f"ban:{category}:{ip}"
        if ban_key in self._bans:
            ban_expiry = self._bans[ban_key]
            if time.monotonic() < ban_expiry:
                remaining_ban = int(ban_expiry - time.monotonic())
                result = RateLimitResult(
                    allowed=False, category=category, identifier=ip,
                    identifier_type="ip", current_count=0, limit=cfg.per_ip_requests,
                    window_seconds=cfg.per_ip_window_seconds, remaining=0,
                    retry_after_seconds=remaining_ban, banned=True,
                    ban_remaining_seconds=remaining_ban,
                    gli_gsf_reference=cfg.gli_gsf_reference,
                )
                self._log_event("banned", result)
                return result
            else:
                del self._bans[ban_key]

        # Per-IP sliding window check
        ip_result = self._check_window(
            self._ip_windows, category, ip, cfg.per_ip_requests,
            cfg.per_ip_window_seconds, "ip", cfg,
        )
        if not ip_result.allowed:
            self._record_violation(ban_key, cfg)
            self._log_event("rate_limited", ip_result)
            return ip_result

        # Per-account check
        if account_id:
            acct_result = self._check_window(
                self._account_windows, category, account_id,
                cfg.per_account_requests, cfg.per_account_window_seconds,
                "account", cfg,
            )
            if not acct_result.allowed:
                self._record_violation(f"ban:{category}:{account_id}", cfg)
                self._log_event("rate_limited", acct_result)
                return acct_result

        # Token bucket for burst endpoints
        if cfg.burst_allowance > 1.0:
            if not self._check_bucket(category, ip, cfg):
                ip_result.allowed = False
                ip_result.retry_after_seconds = 1
                self._log_event("burst_limited", ip_result)
                return ip_result

        self._log_event("allowed", ip_result)
        return ip_result

    def _check_window(self, windows, category, identifier, max_req, window_sec,
                      id_type, cfg):
        if identifier not in windows[category]:
            windows[category][identifier] = SlidingWindowCounter(window_sec, max_req)
        allowed, count = windows[category][identifier].record_and_check()
        return RateLimitResult(
            allowed=allowed, category=category, identifier=identifier,
            identifier_type=id_type, current_count=count, limit=max_req,
            window_seconds=window_sec, remaining=max(0, max_req - count),
            retry_after_seconds=0 if allowed else window_sec,
            banned=False, ban_remaining_seconds=0,
            gli_gsf_reference=cfg.gli_gsf_reference,
        )

    def _check_bucket(self, category, identifier, cfg):
        if identifier not in self._buckets[category]:
            rate = cfg.per_ip_requests / cfg.per_ip_window_seconds
            capacity = cfg.per_ip_requests * cfg.burst_allowance
            self._buckets[category][identifier] = TokenBucket(rate, capacity)
        return self._buckets[category][identifier].consume()

    def _record_violation(self, key, cfg):
        with self._lock:
            self._violations[key] += 1
            if self._violations[key] >= cfg.ban_threshold:
                self._bans[key] = time.monotonic() + cfg.ban_duration_seconds
                self._violations[key] = 0
                logger.warning(f"TEMP BAN: {key} for {cfg.ban_duration_seconds}s")

    def _log_event(self, event_type, result):
        self._audit_log.append({"event": event_type, **result.to_dict()})
        if len(self._audit_log) > 10000:
            self._audit_log = self._audit_log[-5000:]

    def get_stats(self):
        total = len(self._audit_log)
        allowed = sum(1 for e in self._audit_log if e["event"] == "allowed")
        limited = sum(1 for e in self._audit_log if e["event"] == "rate_limited")
        banned = sum(1 for e in self._audit_log if e["event"] == "banned")
        return {
            "total_requests": total, "allowed": allowed,
            "rate_limited": limited, "banned": banned,
            "active_bans": len(self._bans),
            "block_rate": round(((limited + banned) / total * 100), 2) if total else 0,
        }


# ---------------------------------------------------------------------------
# Flask API
# ---------------------------------------------------------------------------
def create_app():
    try:
        from flask import Flask, request, jsonify
    except ImportError:
        logger.error("Flask not installed. Run: pip install flask")
        raise SystemExit(1)

    app = Flask(__name__)
    limiter = RateLimiter()

    @app.route("/api/v1/ratelimit/check", methods=["POST"])
    def check():
        data = request.get_json(force=True)
        result = limiter.check(
            category=data.get("category", "api_general"),
            ip=data.get("ip", request.remote_addr),
            account_id=data.get("account_id"),
        )
        resp = jsonify(result.to_dict())
        for h, v in result.to_headers().items():
            resp.headers[h] = v
        resp.status_code = 200 if result.allowed else 429
        return resp

    @app.route("/api/v1/ratelimit/stats", methods=["GET"])
    def stats():
        return jsonify(limiter.get_stats())

    @app.route("/api/v1/ratelimit/config", methods=["GET"])
    def config():
        cfg = {}
        for cat, c in limiter.config.items():
            cfg[cat] = {
                "per_ip": f"{c.per_ip_requests}/{c.per_ip_window_seconds}s",
                "per_account": f"{c.per_account_requests}/{c.per_account_window_seconds}s",
                "ban_after": f"{c.ban_threshold} violations",
                "ban_duration": f"{c.ban_duration_seconds}s",
                "burst": f"{c.burst_allowance}x",
                "gli_gsf": c.gli_gsf_reference,
            }
        return jsonify(cfg)

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok", "service": "gli-gsf-rate-limiter"})

    return app


# ---------------------------------------------------------------------------
# Demo Mode
# ---------------------------------------------------------------------------
def run_demo():
    print("\n" + "=" * 70)
    print("  GLI-GSF Adaptive Rate Limiter - Demo Mode")
    print("  Simulating gambling platform traffic patterns")
    print("=" * 70 + "\n")

    limiter = RateLimiter()
    scenarios = [
        ("Legitimate Player Login", "login", "203.0.113.10", "player-001", 3, 0.5),
        ("Brute Force Login Attack", "login", "198.51.100.50", None, 25, 0.01),
        ("Normal Game Play (Slots)", "game_round", "203.0.113.20", "player-002", 45, 0.05),
        ("Bot Rapid Game Play", "game_round", "198.51.100.60", "bot-acct", 200, 0.001),
        ("Bonus Abuse Attempt", "bonus_claim", "198.51.100.70", None, 10, 0.1),
        ("Odds Scraping Bot", "odds_query", "198.51.100.80", None, 100, 0.01),
    ]

    for name, cat, ip, acct, count, delay in scenarios:
        print(f"\n--- Scenario: {name} ---")
        print(f"    Category: {cat}, IP: {ip}, Requests: {count}")
        allowed_n = blocked_n = 0
        banned = False
        for _ in range(count):
            r = limiter.check(cat, ip, acct)
            if r.allowed:
                allowed_n += 1
            else:
                blocked_n += 1
                if r.banned:
                    banned = True
            time.sleep(delay)
        status = "BANNED" if banned else ("RATE LIMITED" if blocked_n else "ALL ALLOWED")
        print(f"    Result: {allowed_n} allowed, {blocked_n} blocked [{status}]")

    stats = limiter.get_stats()
    print(f"\n{'=' * 70}")
    print(f"  Total: {stats['total_requests']}  Allowed: {stats['allowed']}  "
          f"Limited: {stats['rate_limited']}  Banned: {stats['banned']}  "
          f"Block rate: {stats['block_rate']}%")
    print(f"{'=' * 70}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="GLI-GSF Adaptive Rate Limiter for iGaming Platforms",
    )
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="Start rate limiter API server")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8081)
    serve.add_argument("--debug", action="store_true")

    test = sub.add_parser("test", help="Test a single rate limit check")
    test.add_argument("--endpoint", default="login",
                       choices=[e.value for e in EndpointCategory])
    test.add_argument("--ip", default="192.168.1.100")
    test.add_argument("--account", default=None)

    sub.add_parser("config", help="Show rate limit configuration")
    sub.add_parser("demo", help="Run demo with simulated traffic")

    args = parser.parse_args()

    if args.command == "serve":
        app = create_app()
        app.run(host=args.host, port=args.port, debug=args.debug)
    elif args.command == "test":
        limiter = RateLimiter()
        result = limiter.check(args.endpoint, args.ip, args.account)
        print(json.dumps(result.to_dict(), indent=2))
    elif args.command == "config":
        print(f"\n{'Category':<16} {'Per-IP':<14} {'Per-Account':<14} "
              f"{'Ban After':<12} {'Ban Dur':<10} {'Burst':<8} {'GLI-GSF Ref'}")
        print("-" * 110)
        for cat, c in DEFAULT_LIMITS.items():
            print(f"{cat:<16} {c.per_ip_requests}/{c.per_ip_window_seconds}s"
                  f"{'':>6} {c.per_account_requests}/{c.per_account_window_seconds}s"
                  f"{'':>6} {c.ban_threshold} hits{'':>4} "
                  f"{c.ban_duration_seconds}s{'':>4} "
                  f"{c.burst_allowance}x{'':>4} {c.gli_gsf_reference}")
    elif args.command == "demo":
        run_demo()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
