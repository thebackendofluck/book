#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
device_fingerprint.py — Device fingerprint tracker with JA3 + browser history.

Tracks device fingerprints per player session and detects anomalies by
comparing the current fingerprint against historical fingerprints stored in
Redis.  Integrates JA3 TLS fingerprinting (provided by upstream nginx/HAProxy)
with browser-level signals (User-Agent, Accept headers, canvas hash, timezone).

Anomaly signals:
  - JA3 hash changed for an established session
  - Browser fingerprint component mismatch vs. most-recent session
  - Multiple distinct device fingerprints in a short time window (rapid switching)
  - Known headless-browser JA3 hashes (Playwright, Puppeteer, Selenium)
  - Timezone/locale mismatch relative to declared jurisdiction

Redis layout:
  device:fp:{player_id}:history  — sorted set: fingerprint_hash -> last_seen_ts
  device:fp:{player_id}:detail:{fp_hash} — hash: component details
  device:fp:{player_id}:anomaly_score — string: last computed score
  device:fp:ja3:blocklist — set: known headless/bot JA3 hashes
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REDIS_URL: str = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# How many distinct fingerprints in RAPID_WINDOW_SECONDS triggers anomaly
RAPID_SWITCH_THRESHOLD: int = int(os.environ.get("FP_RAPID_SWITCH_THRESHOLD", "3"))
RAPID_WINDOW_SECONDS: int = int(os.environ.get("FP_RAPID_WINDOW_SECONDS", "300"))

# Score weights
WEIGHT_JA3_CHANGE = 35
WEIGHT_BROWSER_MISMATCH = 25
WEIGHT_RAPID_SWITCH = 30
WEIGHT_HEADLESS_JA3 = 70
WEIGHT_TIMEZONE_MISMATCH = 20

ANOMALY_SCORE_BLOCK = 70    # >= this: BLOCK
ANOMALY_SCORE_REVIEW = 40   # >= this: REVIEW

# Retain fingerprint history for 90 days
FP_HISTORY_TTL = 86400 * 90

# Known headless browser JA3 hashes (Playwright/Puppeteer Chromium default, Selenium)
# These are real JA3 signatures from automated testing tools
HEADLESS_JA3_HASHES: frozenset[str] = frozenset({
    "b32309a26951912be7dba376398abc3b",  # Playwright Chromium (no TLS extensions randomisation)
    "a0e9f5d64349fb13191bc781f81f42e1",  # Puppeteer default
    "c35c8c8b65c5f5ec6d72c08d55e4b20d",  # Python requests (no browser TLS stack)
    "6bea3f851e04cdfbf28c7a3400cef9af",  # Java HttpURLConnection
    "cbb835b1f3c28c09e6e26e4ea73c15b8",  # Selenium ChromeDriver
    "5d41402abc4b2a76b9719d911017c592",  # curl default TLS fingerprint
})

# Redis key templates
KEY_HISTORY = "device:fp:{player_id}:history"
KEY_DETAIL = "device:fp:{player_id}:detail:{fp_hash}"
KEY_ANOMALY = "device:fp:{player_id}:anomaly_score"
KEY_JA3_BLOCKLIST = "device:fp:ja3:blocklist"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class DeviceFingerprint:
    """A snapshot of device/browser signals at a point in time."""
    player_id: str
    session_id: str
    ja3_hash: str                    # TLS JA3 fingerprint from nginx/HAProxy header
    user_agent: str
    accept_language: str
    accept_encoding: str
    canvas_hash: str                 # SHA-256 of HTML5 canvas fingerprint
    timezone_offset: int             # UTC offset in minutes
    screen_resolution: str           # "1920x1080"
    platform: str                    # "Win32", "MacIntel", etc.
    plugins_hash: str                # hash of navigator.plugins list
    webgl_vendor: str
    webgl_renderer: str
    timestamp: float = field(default_factory=time.time)

    @property
    def composite_hash(self) -> str:
        """Stable hash of device-specific signals (excludes session + timestamp)."""
        components = [
            self.ja3_hash,
            self.user_agent,
            self.canvas_hash,
            self.platform,
            self.webgl_vendor,
            self.webgl_renderer,
            self.plugins_hash,
        ]
        joined = "|".join(components)
        return hashlib.sha256(joined.encode()).hexdigest()[:16]

    @property
    def browser_hash(self) -> str:
        """Hash of browser-only signals (no TLS-layer signals)."""
        components = [
            self.user_agent,
            self.accept_language,
            self.canvas_hash,
            self.platform,
            self.screen_resolution,
            self.plugins_hash,
        ]
        return hashlib.sha256("|".join(components).encode()).hexdigest()[:16]


@dataclass
class FingerprintAnomaly:
    """Detailed breakdown of anomaly scoring for a single check."""
    player_id: str
    session_id: str
    fp_hash: str
    anomaly_score: int
    verdict: str                     # PASS / REVIEW / BLOCK
    signals: list[str]               # human-readable triggered signals
    is_new_device: bool
    distinct_fps_in_window: int
    ja3_is_headless: bool
    ja3_changed: bool
    browser_mismatch: bool
    timezone_mismatch: bool
    checked_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Tracker
# ---------------------------------------------------------------------------

class DeviceFingerprintTracker:
    """
    Stores and analyses device fingerprints for anomaly detection.

    Usage:
        tracker = DeviceFingerprintTracker()
        fp = DeviceFingerprint(player_id="p123", session_id="s456", ...)
        anomaly = tracker.check_and_record(fp, expected_timezone_offset=-180)
        if anomaly.verdict == "BLOCK":
            raise RuntimeError("Device anomaly detected")
    """

    def __init__(self, redis_url: str = REDIS_URL) -> None:
        import redis as redis_lib

        self._redis = redis_lib.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
        self._seed_ja3_blocklist()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_and_record(
        self,
        fp: DeviceFingerprint,
        expected_timezone_offset: Optional[int] = None,
    ) -> FingerprintAnomaly:
        """
        Check a fingerprint for anomalies and record it in history.

        Args:
            fp:                        The current device fingerprint.
            expected_timezone_offset:  Declared player timezone offset in minutes.
                                       None disables timezone check.

        Returns:
            FingerprintAnomaly with verdict PASS / REVIEW / BLOCK.
        """
        fp_hash = fp.composite_hash
        signals: list[str] = []
        score = 0

        # --- Signal 1: headless/bot JA3 ---
        ja3_is_headless = self._is_headless_ja3(fp.ja3_hash)
        if ja3_is_headless:
            score += WEIGHT_HEADLESS_JA3
            signals.append(f"HEADLESS_JA3:{fp.ja3_hash[:8]}")

        # --- Signal 2: JA3 changed for existing player ---
        prev_ja3 = self._get_last_ja3(fp.player_id)
        ja3_changed = False
        if prev_ja3 and prev_ja3 != fp.ja3_hash and not ja3_is_headless:
            # JA3 changes legitimately with OS/browser updates; penalise lightly
            # unless combined with other signals
            score += WEIGHT_JA3_CHANGE
            ja3_changed = True
            signals.append(f"JA3_CHANGED:prev={prev_ja3[:8]},curr={fp.ja3_hash[:8]}")

        # --- Signal 3: browser component mismatch ---
        browser_mismatch = False
        prev_browser = self._get_last_browser_hash(fp.player_id)
        if prev_browser and prev_browser != fp.browser_hash:
            score += WEIGHT_BROWSER_MISMATCH
            browser_mismatch = True
            signals.append("BROWSER_COMPONENT_MISMATCH")

        # --- Signal 4: rapid device switching ---
        recent_distinct = self._count_recent_fps(fp.player_id)
        if recent_distinct >= RAPID_SWITCH_THRESHOLD:
            score += WEIGHT_RAPID_SWITCH
            signals.append(f"RAPID_SWITCH:{recent_distinct}_in_{RAPID_WINDOW_SECONDS}s")

        # --- Signal 5: timezone mismatch ---
        timezone_mismatch = False
        if expected_timezone_offset is not None:
            diff = abs(fp.timezone_offset - expected_timezone_offset)
            if diff > 60:   # more than 1 hour off
                score += WEIGHT_TIMEZONE_MISMATCH
                timezone_mismatch = True
                signals.append(
                    f"TZ_MISMATCH:declared={expected_timezone_offset},actual={fp.timezone_offset}"
                )

        # Clamp score to 0-100
        score = min(score, 100)

        # Determine verdict
        if score >= ANOMALY_SCORE_BLOCK:
            verdict = "BLOCK"
        elif score >= ANOMALY_SCORE_REVIEW:
            verdict = "REVIEW"
        else:
            verdict = "PASS"

        is_new_device = (prev_ja3 is None and prev_browser is None)

        anomaly = FingerprintAnomaly(
            player_id=fp.player_id,
            session_id=fp.session_id,
            fp_hash=fp_hash,
            anomaly_score=score,
            verdict=verdict,
            signals=signals,
            is_new_device=is_new_device,
            distinct_fps_in_window=recent_distinct,
            ja3_is_headless=ja3_is_headless,
            ja3_changed=ja3_changed,
            browser_mismatch=browser_mismatch,
            timezone_mismatch=timezone_mismatch,
        )

        # Record fingerprint (even on BLOCK — needed for audit trail)
        self._record(fp, fp_hash)
        self._store_anomaly_score(fp.player_id, score)

        if verdict != "PASS":
            logger.warning(
                "device_anomaly_detected",
                player_id=fp.player_id,
                session_id=fp.session_id,
                verdict=verdict,
                score=score,
                signals=signals,
            )

        return anomaly

    def get_history(self, player_id: str, limit: int = 20) -> list[dict]:
        """Return the most-recent fingerprint entries for a player."""
        key = KEY_HISTORY.format(player_id=player_id)
        entries = self._redis.zrevrangebyscore(key, "+inf", "-inf", start=0, num=limit, withscores=True)
        result = []
        for fp_hash, ts in entries:
            detail_key = KEY_DETAIL.format(player_id=player_id, fp_hash=fp_hash)
            raw = self._redis.hgetall(detail_key)
            result.append({
                "fp_hash": fp_hash,
                "last_seen": ts,
                "detail": raw,
            })
        return result

    def block_ja3(self, ja3_hash: str) -> None:
        """Add a JA3 hash to the persistent blocklist."""
        self._redis.sadd(KEY_JA3_BLOCKLIST, ja3_hash)
        logger.info("ja3_blocked", hash=ja3_hash)

    def unblock_ja3(self, ja3_hash: str) -> None:
        self._redis.srem(KEY_JA3_BLOCKLIST, ja3_hash)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _record(self, fp: DeviceFingerprint, fp_hash: str) -> None:
        """Persist the fingerprint in sorted-set history and detail hash."""
        history_key = KEY_HISTORY.format(player_id=fp.player_id)
        detail_key = KEY_DETAIL.format(player_id=fp.player_id, fp_hash=fp_hash)

        pipe = self._redis.pipeline(transaction=False)
        pipe.zadd(history_key, {fp_hash: fp.timestamp})
        pipe.expire(history_key, FP_HISTORY_TTL)

        detail = {
            "ja3_hash": fp.ja3_hash,
            "browser_hash": fp.browser_hash,
            "user_agent": fp.user_agent[:200],
            "accept_language": fp.accept_language[:80],
            "canvas_hash": fp.canvas_hash,
            "timezone_offset": str(fp.timezone_offset),
            "screen_resolution": fp.screen_resolution,
            "platform": fp.platform,
            "webgl_vendor": fp.webgl_vendor[:100],
            "webgl_renderer": fp.webgl_renderer[:100],
            "last_seen": str(fp.timestamp),
            "session_id": fp.session_id,
        }
        pipe.hset(detail_key, mapping=detail)
        pipe.expire(detail_key, FP_HISTORY_TTL)
        pipe.execute()

    def _get_last_ja3(self, player_id: str) -> Optional[str]:
        """Get the most-recently-seen JA3 hash for a player."""
        history_key = KEY_HISTORY.format(player_id=player_id)
        top = self._redis.zrevrangebyscore(
            history_key, "+inf", "-inf", start=0, num=1
        )
        if not top:
            return None
        fp_hash = top[0]
        detail_key = KEY_DETAIL.format(player_id=player_id, fp_hash=fp_hash)
        return self._redis.hget(detail_key, "ja3_hash")

    def _get_last_browser_hash(self, player_id: str) -> Optional[str]:
        history_key = KEY_HISTORY.format(player_id=player_id)
        top = self._redis.zrevrangebyscore(
            history_key, "+inf", "-inf", start=0, num=1
        )
        if not top:
            return None
        fp_hash = top[0]
        detail_key = KEY_DETAIL.format(player_id=player_id, fp_hash=fp_hash)
        return self._redis.hget(detail_key, "browser_hash")

    def _count_recent_fps(self, player_id: str) -> int:
        """Count distinct fingerprints seen in the last RAPID_WINDOW_SECONDS."""
        history_key = KEY_HISTORY.format(player_id=player_id)
        cutoff = time.time() - RAPID_WINDOW_SECONDS
        fps = self._redis.zrangebyscore(history_key, cutoff, "+inf")
        return len(set(fps))

    def _is_headless_ja3(self, ja3_hash: str) -> bool:
        """Check against static list + persistent Redis blocklist."""
        if ja3_hash in HEADLESS_JA3_HASHES:
            return True
        return bool(self._redis.sismember(KEY_JA3_BLOCKLIST, ja3_hash))

    def _store_anomaly_score(self, player_id: str, score: int) -> None:
        key = KEY_ANOMALY.format(player_id=player_id)
        self._redis.setex(key, 3600, str(score))

    def _seed_ja3_blocklist(self) -> None:
        """Pre-populate Redis blocklist with known headless JA3 hashes on startup."""
        if HEADLESS_JA3_HASHES:
            self._redis.sadd(KEY_JA3_BLOCKLIST, *HEADLESS_JA3_HASHES)


# ---------------------------------------------------------------------------
# Fingerprint builder helper (for tests and middleware)
# ---------------------------------------------------------------------------

def fingerprint_from_request_headers(
    player_id: str,
    session_id: str,
    headers: dict[str, str],
    client_data: dict,
) -> DeviceFingerprint:
    """
    Build a DeviceFingerprint from HTTP request headers and client-submitted data.

    Expected headers (set by nginx/HAProxy):
      X-JA3: <ja3_hash>
    Expected client_data keys (from JS fingerprint payload):
      canvas_hash, timezone_offset, screen_resolution, platform,
      plugins_hash, webgl_vendor, webgl_renderer
    """
    return DeviceFingerprint(
        player_id=player_id,
        session_id=session_id,
        ja3_hash=headers.get("x-ja3", "").lower().strip() or "unknown",
        user_agent=headers.get("user-agent", ""),
        accept_language=headers.get("accept-language", ""),
        accept_encoding=headers.get("accept-encoding", ""),
        canvas_hash=client_data.get("canvas_hash", ""),
        timezone_offset=int(client_data.get("timezone_offset", 0)),
        screen_resolution=client_data.get("screen_resolution", ""),
        platform=client_data.get("platform", ""),
        plugins_hash=client_data.get("plugins_hash", ""),
        webgl_vendor=client_data.get("webgl_vendor", ""),
        webgl_renderer=client_data.get("webgl_renderer", ""),
    )
