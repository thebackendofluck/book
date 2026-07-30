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
Bot Detection Engine with Browser Fingerprinting
GLI-GSF Phase 3 - Automated Threat Protection

Detects automated/bot traffic targeting iGaming platforms using:
- TLS fingerprinting (JA3/JA4)
- Behavioral analysis (request timing, mouse movement)
- Browser fingerprint validation
- Device consistency checks
- Gambling-specific bot patterns (odds scraping, bonus abuse)

GLI-GSF-5 Reference: Section 4.1 - Automated Threat Protection

Requirements:
    pip install flask redis mmh3 numpy
"""

import hashlib
import json
import logging
import math
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Optional

try:
    import redis
    HAS_REDIS = True
except ImportError:
    HAS_REDIS = False

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("bot-detector")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
class Config:
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    SCORE_THRESHOLD_BLOCK = float(os.getenv("BOT_THRESHOLD_BLOCK", "0.85"))
    SCORE_THRESHOLD_CHALLENGE = float(os.getenv("BOT_THRESHOLD_CHALLENGE", "0.60"))
    SESSION_WINDOW_SECONDS = int(os.getenv("SESSION_WINDOW", "300"))
    MAX_REQUESTS_PER_WINDOW = int(os.getenv("MAX_REQUESTS", "100"))
    FINGERPRINT_TTL = int(os.getenv("FP_TTL", "86400"))
    GAMBLING_MODE = os.getenv("GAMBLING_MODE", "sportsbook")  # sportsbook|casino|poker


class Action(Enum):
    ALLOW = "allow"
    CHALLENGE = "challenge"
    BLOCK = "block"
    MONITOR = "monitor"


@dataclass
class BotScore:
    ip: str
    total_score: float = 0.0
    action: Action = Action.ALLOW
    signals: dict = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())  # ty:ignore[deprecated]

    def to_dict(self):
        return {
            "ip": self.ip,
            "score": round(self.total_score, 3),
            "action": self.action.value,
            "signals": self.signals,
            "timestamp": self.timestamp
        }


@dataclass
class RequestContext:
    ip: str
    user_agent: str
    path: str
    method: str = "GET"
    headers: dict = field(default_factory=dict)
    ja3_hash: str = ""
    fingerprint: dict = field(default_factory=dict)
    timing_ms: float = 0.0
    mouse_events: list = field(default_factory=list)
    session_id: str = ""


# ---------------------------------------------------------------------------
# Signal Detectors
# ---------------------------------------------------------------------------
class UserAgentAnalyzer:
    """Analyze User-Agent for bot indicators."""

    KNOWN_BOTS = [
        "bot", "spider", "crawl", "scraper", "fetch", "wget", "curl",
        "python-requests", "httpie", "postman", "insomnia", "axios",
        "node-fetch", "phantomjs", "headless", "selenium", "puppeteer",
        "playwright", "cypress"
    ]

    GAMBLING_BOTS = [
        "oddsscraper", "betbot", "arbitragebot", "surebet", "betfinder",
        "oddsharvester", "bookiescraper", "linebot", "bonusbot"
    ]

    VALID_BROWSERS = [
        (r"Chrome/(\d+)", 80, 140),
        (r"Firefox/(\d+)", 90, 135),
        (r"Safari/(\d+)", 600, 620),
        (r"Edg/(\d+)", 90, 135),
    ]

    def analyze(self, ctx: RequestContext) -> tuple[float, dict]:
        """Return (score, details). Score 0=human, 1=bot."""
        ua = ctx.user_agent.lower()
        details = {}

        if not ua or ua == "-":
            return 0.9, {"reason": "empty_user_agent"}

        # Check known bot strings
        for bot_str in self.KNOWN_BOTS:
            if bot_str in ua:
                return 0.8, {"reason": f"known_bot_pattern:{bot_str}"}

        for bot_str in self.GAMBLING_BOTS:
            if bot_str in ua:
                return 0.95, {"reason": f"gambling_bot:{bot_str}"}

        # Check browser version plausibility
        for pattern, min_ver, max_ver in self.VALID_BROWSERS:
            match = re.search(pattern, ctx.user_agent)
            if match:
                version = int(match.group(1))
                if version < min_ver:
                    return 0.6, {"reason": f"outdated_browser_v{version}"}
                if version > max_ver:
                    return 0.7, {"reason": f"future_browser_v{version}"}
                break

        # Check for inconsistencies
        if "mobile" in ua and "windows nt" in ua and "android" not in ua:
            details["inconsistency"] = "mobile_on_desktop"
            return 0.5, details

        return 0.0, {"reason": "normal_ua"}


class HeaderAnalyzer:
    """Analyze HTTP headers for bot indicators."""

    EXPECTED_HEADERS = [
        "accept", "accept-language", "accept-encoding",
        "sec-fetch-mode", "sec-fetch-site", "sec-fetch-dest"
    ]

    def analyze(self, ctx: RequestContext) -> tuple[float, dict]:
        headers_lower = {k.lower(): v for k, v in ctx.headers.items()}
        score = 0.0
        details = {}

        # Missing standard browser headers
        missing = [h for h in self.EXPECTED_HEADERS if h not in headers_lower]
        if len(missing) > 3:
            score += 0.4
            details["missing_headers"] = missing

        # Check Accept-Language
        if "accept-language" not in headers_lower:
            score += 0.2
            details["no_accept_language"] = True

        # Check for automation tool headers
        if "x-requested-with" in headers_lower:
            val = headers_lower["x-requested-with"]
            if val not in ("XMLHttpRequest",):
                score += 0.3
                details["suspicious_xhr"] = val

        # Connection header anomalies
        if headers_lower.get("connection", "").lower() == "close":
            score += 0.1
            details["connection_close"] = True

        # Check header order consistency (simplified)
        header_keys = list(ctx.headers.keys())
        if header_keys and header_keys[0].lower() != "host":
            score += 0.1
            details["unusual_header_order"] = True

        return min(score, 1.0), details


class BehavioralAnalyzer:
    """Analyze request behavior patterns."""

    def __init__(self):
        self._request_history = defaultdict(list)
        self._redis = None
        if HAS_REDIS:
            try:
                self._redis = redis.from_url(Config.REDIS_URL, decode_responses=True)
                self._redis.ping()
            except Exception:
                self._redis = None
                logger.warning("Redis unavailable, using in-memory store")

    def analyze(self, ctx: RequestContext) -> tuple[float, dict]:
        score = 0.0
        details = {}
        now = time.time()

        # Track request timing
        history = self._get_history(ctx.ip)
        history.append(now)

        # Keep only recent window
        cutoff = now - Config.SESSION_WINDOW_SECONDS
        history = [t for t in history if t > cutoff]
        self._set_history(ctx.ip, history)

        request_count = len(history)
        details["requests_in_window"] = request_count

        # Volume-based detection
        if request_count > Config.MAX_REQUESTS_PER_WINDOW:
            score += 0.5
            details["high_volume"] = True

        # Timing regularity (bots have consistent intervals)
        if len(history) >= 5 and HAS_NUMPY:
            intervals = [history[i] - history[i - 1] for i in range(1, len(history))]
            cv = np.std(intervals) / np.mean(intervals) if np.mean(intervals) > 0 else 0

            if cv < 0.1 and len(intervals) >= 10:
                score += 0.6
                details["regular_timing"] = True
                details["timing_cv"] = round(cv, 4)
            elif cv < 0.2:
                score += 0.3
                details["semi_regular_timing"] = True

        # Check for sequential endpoint access (spider pattern)
        # This would need path tracking - simplified here

        return min(score, 1.0), details

    def _get_history(self, ip: str) -> list:
        if self._redis:
            key = f"bot:hist:{ip}"
            data = self._redis.lrange(key, 0, -1)
            return [float(t) for t in data]  # ty:ignore[not-iterable]
        return self._request_history[ip]

    def _set_history(self, ip: str, history: list):
        if self._redis:
            key = f"bot:hist:{ip}"
            pipe = self._redis.pipeline()
            pipe.delete(key)
            if history:
                pipe.rpush(key, *[str(t) for t in history])
            pipe.expire(key, Config.SESSION_WINDOW_SECONDS)
            pipe.execute()
        else:
            self._request_history[ip] = history


class FingerprintAnalyzer:
    """Analyze browser fingerprint for consistency."""

    def analyze(self, ctx: RequestContext) -> tuple[float, dict]:
        fp = ctx.fingerprint
        if not fp:
            return 0.3, {"reason": "no_fingerprint_data"}

        score = 0.0
        details = {}

        # Check canvas fingerprint
        if "canvas_hash" in fp:
            if fp["canvas_hash"] in ("", "0", "undefined"):
                score += 0.4
                details["empty_canvas"] = True

        # Check WebGL
        if "webgl_vendor" in fp:
            vendor = fp["webgl_vendor"]
            if vendor in ("Brian Paul", "Mesa", "Google SwiftShader"):
                score += 0.5
                details["headless_webgl"] = vendor

        # Screen resolution
        if "screen_width" in fp and "screen_height" in fp:
            w, h = fp.get("screen_width", 0), fp.get("screen_height", 0)
            if w == 0 or h == 0:
                score += 0.3
                details["zero_screen"] = True
            elif w == 800 and h == 600:
                score += 0.2
                details["default_resolution"] = True

        # Timezone vs Accept-Language mismatch
        tz = fp.get("timezone", "")
        lang = ctx.headers.get("Accept-Language", "")
        if tz and lang:
            tz_region = tz.split("/")[0] if "/" in tz else ""
            if tz_region == "America" and not any(
                l in lang for l in ["en", "es", "pt", "fr"]
            ):
                score += 0.2
                details["tz_lang_mismatch"] = True

        # Plugin count
        plugins = fp.get("plugins_count", -1)
        if plugins == 0:
            score += 0.2
            details["no_plugins"] = True

        # Navigator properties
        if fp.get("webdriver") is True:
            score += 0.8
            details["webdriver_detected"] = True

        if fp.get("languages_count", 1) == 0:
            score += 0.3
            details["no_languages"] = True

        # Automation indicators
        automation_props = [
            "chrome_runtime", "callPhantom", "phantom",
            "_selenium", "webdriver", "domAutomation"
        ]
        for prop in automation_props:
            if fp.get(prop):
                score += 0.7
                details[f"automation_{prop}"] = True
                break

        return min(score, 1.0), details


class JA3Analyzer:
    """Analyze TLS fingerprint (JA3/JA4)."""

    # Known bot/automation tool JA3 hashes
    KNOWN_BOT_JA3 = {
        "e7d705a3286e19ea42f587b344ee6865": "Python requests",
        "b32309a26951912be7dba376398abc3b": "Golang default",
        "3b5074b1b5d032e5620f69f9f700ff0e": "Node.js default",
        "a0e9f5d64349fb13191bc781f81f42e1": "curl/7.x",
        "c12f54a3f91dc7bafd92cb59fe009a35": "Java HttpClient",
        "d5a62b0b1c3a7cef9e7e8a0f6f8b4e3d": "Selenium Chrome",
        "a4b6c8d9e2f1a3b5c7d9e1f3a5b7c9d1": "Puppeteer",
    }

    def analyze(self, ctx: RequestContext) -> tuple[float, dict]:
        if not ctx.ja3_hash:
            return 0.1, {"reason": "no_ja3"}

        details = {"ja3": ctx.ja3_hash}

        if ctx.ja3_hash in self.KNOWN_BOT_JA3:
            tool = self.KNOWN_BOT_JA3[ctx.ja3_hash]
            details["matched_tool"] = tool
            return 0.9, details

        return 0.0, details


class GamblingPatternAnalyzer:
    """Detect gambling-specific bot patterns."""

    ODDS_PATHS = ["/odds", "/lines", "/markets", "/prices", "/spreads"]
    BONUS_PATHS = ["/bonus", "/promo", "/freebet", "/offer"]
    FINANCIAL_PATHS = ["/withdraw", "/deposit", "/cashout", "/payout"]

    def __init__(self):
        self._path_history = defaultdict(list)

    def analyze(self, ctx: RequestContext) -> tuple[float, dict]:
        score = 0.0
        details = {}
        path = ctx.path.lower()

        # Track path access patterns
        self._path_history[ctx.ip].append((time.time(), path))

        # Keep recent history only
        cutoff = time.time() - 300
        self._path_history[ctx.ip] = [
            (t, p) for t, p in self._path_history[ctx.ip] if t > cutoff
        ]

        history = self._path_history[ctx.ip]

        # Odds scraping pattern: high-frequency access to odds endpoints
        odds_count = sum(1 for _, p in history if any(o in p for o in self.ODDS_PATHS))
        if odds_count > 20:
            score += 0.7
            details["odds_scraping"] = True
            details["odds_requests_5min"] = odds_count

        # Bonus abuse pattern: rapid bonus claim attempts
        bonus_count = sum(1 for _, p in history if any(b in p for b in self.BONUS_PATHS))
        if bonus_count > 5:
            score += 0.6
            details["bonus_abuse_pattern"] = True
            details["bonus_requests_5min"] = bonus_count

        # Account creation farming
        if "/register" in path or "/signup" in path:
            reg_count = sum(1 for _, p in history if "register" in p or "signup" in p)
            if reg_count > 2:
                score += 0.8
                details["registration_farming"] = True

        # Withdrawal automation
        withdraw_count = sum(
            1 for _, p in history if any(f in p for f in self.FINANCIAL_PATHS)
        )
        if withdraw_count > 10:
            score += 0.5
            details["financial_automation"] = True

        # Check for systematic game exploration (casino mode)
        if Config.GAMBLING_MODE == "casino":
            game_paths = sum(1 for _, p in history if "/games/" in p)
            if game_paths > 50:
                score += 0.4
                details["game_enumeration"] = True

        return min(score, 1.0), details


# ---------------------------------------------------------------------------
# Bot Detection Engine
# ---------------------------------------------------------------------------
class BotDetector:
    """Main bot detection engine combining all signal analyzers."""

    # Weight for each signal (tunable)
    WEIGHTS = {
        "user_agent": 0.15,
        "headers": 0.10,
        "behavioral": 0.25,
        "fingerprint": 0.20,
        "ja3": 0.15,
        "gambling": 0.15,
    }

    def __init__(self):
        self.ua_analyzer = UserAgentAnalyzer()
        self.header_analyzer = HeaderAnalyzer()
        self.behavioral_analyzer = BehavioralAnalyzer()
        self.fingerprint_analyzer = FingerprintAnalyzer()
        self.ja3_analyzer = JA3Analyzer()
        self.gambling_analyzer = GamblingPatternAnalyzer()

        # IP allowlist/blocklist
        self._allowlist = set()
        self._blocklist = set()

        logger.info("Bot detection engine initialized")
        logger.info(f"Block threshold: {Config.SCORE_THRESHOLD_BLOCK}")
        logger.info(f"Challenge threshold: {Config.SCORE_THRESHOLD_CHALLENGE}")

    def load_lists(self, allowlist_path: str = None, blocklist_path: str = None):  # ty:ignore[invalid-parameter-default]
        """Load IP allow/block lists."""
        if allowlist_path and os.path.exists(allowlist_path):
            with open(allowlist_path) as f:
                self._allowlist = {
                    line.strip() for line in f if line.strip() and not line.startswith("#")
                }
            logger.info(f"Loaded {len(self._allowlist)} allowlisted IPs")

        if blocklist_path and os.path.exists(blocklist_path):
            with open(blocklist_path) as f:
                self._blocklist = {
                    line.strip() for line in f if line.strip() and not line.startswith("#")
                }
            logger.info(f"Loaded {len(self._blocklist)} blocklisted IPs")

    def evaluate(self, ctx: RequestContext) -> BotScore:
        """Evaluate a request and return bot score with recommended action."""
        result = BotScore(ip=ctx.ip)

        # Fast path: allowlist/blocklist
        if ctx.ip in self._allowlist:
            result.action = Action.ALLOW
            result.signals["allowlisted"] = True
            return result

        if ctx.ip in self._blocklist:
            result.total_score = 1.0
            result.action = Action.BLOCK
            result.signals["blocklisted"] = True
            return result

        # Run all analyzers
        analyzers = {
            "user_agent": self.ua_analyzer,
            "headers": self.header_analyzer,
            "behavioral": self.behavioral_analyzer,
            "fingerprint": self.fingerprint_analyzer,
            "ja3": self.ja3_analyzer,
            "gambling": self.gambling_analyzer,
        }

        weighted_score = 0.0
        for name, analyzer in analyzers.items():
            try:
                signal_score, details = analyzer.analyze(ctx)
                weight = self.WEIGHTS.get(name, 0.1)
                weighted_score += signal_score * weight
                result.signals[name] = {
                    "score": round(signal_score, 3),
                    "weight": weight,
                    "details": details
                }
            except Exception as e:
                logger.error(f"Analyzer {name} error: {e}")

        result.total_score = min(weighted_score, 1.0)

        # Determine action
        if result.total_score >= Config.SCORE_THRESHOLD_BLOCK:
            result.action = Action.BLOCK
        elif result.total_score >= Config.SCORE_THRESHOLD_CHALLENGE:
            result.action = Action.CHALLENGE
        else:
            result.action = Action.ALLOW

        return result


# ---------------------------------------------------------------------------
# Flask API (optional web service)
# ---------------------------------------------------------------------------
def create_app():
    """Create Flask app for bot detection API."""
    try:
        from flask import Flask, request, jsonify
    except ImportError:
        logger.error("Flask not installed. Run: pip install flask")
        return None

    app = Flask(__name__)
    detector = BotDetector()

    @app.route("/api/v1/bot-check", methods=["POST"])
    def check_request():
        data = request.get_json(force=True)
        ctx = RequestContext(
            ip=data.get("ip", request.remote_addr),
            user_agent=data.get("user_agent", ""),
            path=data.get("path", "/"),
            method=data.get("method", "GET"),
            headers=data.get("headers", {}),
            ja3_hash=data.get("ja3_hash", ""),
            fingerprint=data.get("fingerprint", {}),
            session_id=data.get("session_id", ""),
        )
        result = detector.evaluate(ctx)
        return jsonify(result.to_dict())

    @app.route("/api/v1/bot-check/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok", "version": "1.0.0"})

    return app


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    import argparse

    parser = argparse.ArgumentParser(description="GLI-GSF Bot Detection Engine")
    sub = parser.add_subparsers(dest="command")

    # Serve mode
    serve = sub.add_parser("serve", help="Start bot detection API server")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--debug", action="store_true")

    # Test mode
    test = sub.add_parser("test", help="Test a single request")
    test.add_argument("--ip", default="192.168.1.100")
    test.add_argument("--ua", default="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0")
    test.add_argument("--path", default="/api/v1/odds/football")
    test.add_argument("--ja3", default="")

    # Batch test
    batch = sub.add_parser("batch", help="Batch test from JSON file")
    batch.add_argument("file", help="JSON file with request array")

    args = parser.parse_args()

    if args.command == "serve":
        app = create_app()
        if app:
            app.run(host=args.host, port=args.port, debug=args.debug)

    elif args.command == "test":
        detector = BotDetector()
        ctx = RequestContext(
            ip=args.ip,
            user_agent=args.ua,
            path=args.path,
            ja3_hash=args.ja3,
            headers={"Accept-Language": "en-US", "Accept-Encoding": "gzip"}
        )
        result = detector.evaluate(ctx)
        print(json.dumps(result.to_dict(), indent=2))

    elif args.command == "batch":
        detector = BotDetector()
        with open(args.file) as f:
            requests_data = json.load(f)

        results: dict[str, Any] = {"total": 0, "blocked": 0, "challenged": 0, "allowed": 0, "details": []}
        for req in requests_data:
            ctx = RequestContext(
                ip=req.get("ip", "0.0.0.0"),
                user_agent=req.get("user_agent", ""),
                path=req.get("path", "/"),
                headers=req.get("headers", {}),
            )
            result = detector.evaluate(ctx)
            results["total"] += 1
            results[result.action.value.replace("monitor", "allowed")] = (
                results.get(result.action.value.replace("monitor", "allowed"), 0) + 1  # ty:ignore[unsupported-operator]
            )
            results["details"].append(result.to_dict())

        print(json.dumps(results, indent=2))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
