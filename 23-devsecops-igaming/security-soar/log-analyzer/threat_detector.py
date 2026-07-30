#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 23, DevSecOps for iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Standalone Threat Detection Engine for AcmeToCasino SOAR System.

Reads normalized events from a Redis stream (populated by log_collector.py),
runs each event through a configurable pipeline of detection modules, and
emits structured JSON alerts to stdout, a file, or back to Redis.

Detection modules:
  BruteForceDetector      – sliding-window failed login tracking per IP
  DDoSDetector            – request rate analysis, SYN flood, Slowloris
  InjectionDetector       – SQLi, XSS, command injection pattern matching
  BotDetector             – user-agent analysis, request timing, path crawling
  AccountTakeoverDetector – geo-anomaly, login velocity, device fingerprint
  BonusAbuseDetector      – multi-account / VPN / proxy detection (iGaming)

Usage:
    python threat_detector.py --config /etc/soar/config.yml
    python threat_detector.py --config /etc/soar/config.yml --detector brute_force
    python threat_detector.py --config /etc/soar/config.yml --dry-run --log-level DEBUG
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import Generator
from datetime import datetime, timezone
from typing import Any

import redis
import yaml

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def _build_logger(name: str, level: str = "INFO") -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


log = _build_logger("threat_detector")


# ---------------------------------------------------------------------------
# Alert schema factory
# ---------------------------------------------------------------------------

def _new_alert(
    detector: str,
    alert_type: str,
    severity: str,
    source_ip: str,
    description: str,
    evidence: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """
    Build a structured JSON alert.

    Args:
        detector:    Name of the detector that triggered the alert.
        alert_type:  Machine-readable alert category (e.g. "brute_force").
        severity:    One of: critical | high | medium | low | info.
        source_ip:   IP address of the suspected attacker.
        description: Human-readable summary of the threat.
        evidence:    Supporting data (counts, patterns, request samples).
        **extra:     Additional context fields.

    Returns:
        A dict ready for JSON serialization.
    """
    return {
        "alert_id": str(uuid.uuid4()),
        "schema_version": "1.0",
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "detector": detector,
        "alert_type": alert_type,
        "severity": severity,
        "source_ip": source_ip,
        "description": description,
        "evidence": evidence or {},
        **extra,
    }


# ---------------------------------------------------------------------------
# Redis helpers
# ---------------------------------------------------------------------------

class RedisState:
    """
    Thin wrapper around a Redis connection providing sliding-window counters
    and arbitrary key/value state used by detectors.

    Args:
        host:       Redis hostname.
        port:       Redis port.
        db:         Redis logical database index.
        password:   Redis AUTH password (empty string for none).
        key_prefix: Namespace prefix for all SOAR keys.
        default_ttl: Default key expiry in seconds.
        max_conn:   Connection pool size.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        password: str = "",
        key_prefix: str = "soar:",
        default_ttl: int = 86400,
        max_conn: int = 20,
    ) -> None:
        pool = redis.ConnectionPool(
            host=host,
            port=port,
            db=db,
            password=password or None,
            max_connections=max_conn,
            decode_responses=True,
        )
        self._r = redis.Redis(connection_pool=pool)
        self._prefix = key_prefix
        self._default_ttl = default_ttl

    def _k(self, *parts: str) -> str:
        return self._prefix + ":".join(parts)

    # --- Sliding-window increment -----------------------------------------

    def window_increment(self, namespace: str, key: str, window_seconds: int) -> int:
        """
        Increment a sliding-window counter for *key* within *namespace*.

        Uses a sorted set where member = epoch_ms and score = epoch_ms.
        Old members outside the window are pruned on each call.

        Args:
            namespace:      Logical grouping (e.g. "brute_force:failures").
            key:            Discriminator (e.g. IP address or user ID).
            window_seconds: Duration of the sliding window.

        Returns:
            Current count within the window after incrementing.
        """
        rkey = self._k(namespace, key)
        now_ms = int(time.time() * 1000)
        cutoff_ms = now_ms - window_seconds * 1000
        pipe = self._r.pipeline()
        pipe.zremrangebyscore(rkey, "-inf", cutoff_ms)
        pipe.zadd(rkey, {str(now_ms): now_ms})
        pipe.zcard(rkey)
        pipe.expire(rkey, self._default_ttl)
        results = pipe.execute()
        return int(results[2])

    def window_count(self, namespace: str, key: str, window_seconds: int) -> int:
        """Return the current count within the window without incrementing."""
        rkey = self._k(namespace, key)
        now_ms = int(time.time() * 1000)
        cutoff_ms = now_ms - window_seconds * 1000
        pipe = self._r.pipeline()
        pipe.zremrangebyscore(rkey, "-inf", cutoff_ms)
        pipe.zcard(rkey)
        results = pipe.execute()
        return int(results[1])

    # --- Simple key/value helpers -----------------------------------------

    def hset(self, namespace: str, key: str, field: str, value: str) -> None:
        self._r.hset(self._k(namespace, key), field, value)
        self._r.expire(self._k(namespace, key), self._default_ttl)

    def hget(self, namespace: str, key: str, field: str) -> str | None:
        return self._r.hget(self._k(namespace, key), field)

    def hgetall(self, namespace: str, key: str) -> dict[str, str]:
        return self._r.hgetall(self._k(namespace, key)) or {}

    def sadd(self, namespace: str, key: str, *members: str) -> None:
        rkey = self._k(namespace, key)
        self._r.sadd(rkey, *members)
        self._r.expire(rkey, self._default_ttl)

    def scard(self, namespace: str, key: str) -> int:
        return int(self._r.scard(self._k(namespace, key)) or 0)

    def smembers(self, namespace: str, key: str) -> frozenset[str]:
        raw = self._r.smembers(self._k(namespace, key))
        return frozenset(raw) if raw else frozenset()

    def incr(self, namespace: str, key: str, ttl: int | None = None) -> int:
        rkey = self._k(namespace, key)
        val = int(self._r.incr(rkey))
        self._r.expire(rkey, ttl or self._default_ttl)
        return val

    def get(self, namespace: str, key: str) -> str | None:
        return self._r.get(self._k(namespace, key))

    def set(self, namespace: str, key: str, value: str, ttl: int | None = None) -> None:
        self._r.set(self._k(namespace, key), value, ex=ttl or self._default_ttl)


# ---------------------------------------------------------------------------
# Abstract detector
# ---------------------------------------------------------------------------

class BaseDetector(ABC):
    """
    Abstract base class for all threat detection modules.

    Args:
        config: Detector-specific configuration block from config.yml.
        state:  Shared Redis state store.
    """

    def __init__(self, config: dict[str, Any], state: RedisState) -> None:
        self._cfg = config
        self._state = state
        self._logger = _build_logger(f"detector.{self.name}")

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this detector."""

    @abstractmethod
    def analyze(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        """
        Analyze a normalized event and return zero or more alert dicts.

        Args:
            event: A normalized event from the SOAR common schema.

        Returns:
            A list of alert dicts produced by :func:`_new_alert`.
            An empty list means no threat detected.
        """

    @property
    def severity(self) -> str:
        return self._cfg.get("severity", "medium")

    @property
    def enabled(self) -> bool:
        val = str(self._cfg.get("enabled", "true")).lower()
        return val in ("true", "1", "yes")


# ---------------------------------------------------------------------------
# BruteForceDetector
# ---------------------------------------------------------------------------

class BruteForceDetector(BaseDetector):
    """
    Detects brute-force login attacks using a per-IP sliding window counter.

    Tracks failed authentication events and raises an alert when the failure
    count exceeds ``threshold_failures`` within ``window_seconds``.

    Configuration keys:
        window_seconds:         Sliding window duration.
        threshold_failures:     Failure count that triggers an alert.
        auto_block_threshold:   Failure count that triggers auto-block metadata.
        severity:               Alert severity level.
    """

    # Event types that represent authentication failures
    _AUTH_FAILURE_EVENTS = frozenset({
        "auth_failure", "login_failed", "authentication_failed",
        "invalid_password", "account_locked",
    })

    @property
    def name(self) -> str:
        return "brute_force"

    def analyze(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        event_type = event.get("event_type", "")
        if event_type not in self._AUTH_FAILURE_EVENTS:
            # Also detect HTTP 401/403 from Nginx logs as proxy for auth failures
            http_status = int(event.get("http_status", 0))
            if http_status not in (401, 403):
                return []

        ip = event.get("source_ip", "0.0.0.0")
        window = int(self._cfg.get("window_seconds", 300))
        threshold = int(self._cfg.get("threshold_failures", 10))
        auto_block_threshold = int(self._cfg.get("auto_block_threshold", 20))

        count = self._state.window_increment("brute_force:failures", ip, window)

        alerts: list[dict[str, Any]] = []

        if count == threshold:
            alerts.append(_new_alert(
                detector=self.name,
                alert_type="brute_force",
                severity=self.severity,
                source_ip=ip,
                description=(
                    f"Brute force attack detected: {count} failed logins "
                    f"from {ip} in the last {window}s"
                ),
                evidence={"failure_count": count, "window_seconds": window},
                user_id=event.get("user_id") or event.get("username"),
                should_block=False,
            ))

        if auto_block_threshold > 0 and count == auto_block_threshold:
            alerts.append(_new_alert(
                detector=self.name,
                alert_type="brute_force_auto_block",
                severity="critical",
                source_ip=ip,
                description=(
                    f"Auto-block threshold reached: {count} failed logins from {ip}"
                ),
                evidence={"failure_count": count, "threshold": auto_block_threshold},
                should_block=True,
                block_duration_seconds=int(self._cfg.get("block_duration_seconds", 3600)),
            ))

        return alerts


# ---------------------------------------------------------------------------
# DDoSDetector
# ---------------------------------------------------------------------------

class DDoSDetector(BaseDetector):
    """
    Detects volumetric DDoS attacks, Slowloris, and SYN floods.

    Tracks per-IP request rate and global site request rate using sliding
    windows. Slowloris and SYN flood detection rely on connection metadata
    that must be present in the event (typically from Nginx or netflow data).

    Configuration keys:
        rps_threshold:                     Per-IP requests/second trigger.
        global_rps_threshold:              Site-wide requests/second trigger.
        slowloris_connection_age_seconds:  Minimum age to flag as Slowloris.
        slowloris_threshold:               Slow connections per IP to trigger.
        syn_flood_incomplete_pct:          % incomplete connections for SYN flood.
    """

    @property
    def name(self) -> str:
        return "ddos"

    def analyze(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        alerts: list[dict[str, Any]] = []
        ip = event.get("source_ip", "0.0.0.0")

        # --- Per-IP rate check ---
        rps_threshold = int(self._cfg.get("rps_threshold", 500))
        # Count in a 1-second window; multiply by 60 for rpm but report as rps
        count_1s = self._state.window_increment("ddos:rate_ip", ip, 1)
        if count_1s >= rps_threshold:
            alerts.append(_new_alert(
                detector=self.name,
                alert_type="ddos_rate_ip",
                severity=self.severity,
                source_ip=ip,
                description=(
                    f"DDoS rate limit exceeded: {count_1s} req/s from {ip} "
                    f"(threshold: {rps_threshold})"
                ),
                evidence={"requests_per_second": count_1s, "threshold": rps_threshold},
                should_block=True,
                block_duration_seconds=300,
            ))

        # --- Global rate check (keyed to "global") ---
        global_threshold = int(self._cfg.get("global_rps_threshold", 50000))
        global_count = self._state.window_increment("ddos:rate_global", "global", 1)
        if global_count >= global_threshold:
            alerts.append(_new_alert(
                detector=self.name,
                alert_type="ddos_global_rate",
                severity="critical",
                source_ip="0.0.0.0",
                description=(
                    f"Global DDoS rate limit exceeded: {global_count} req/s "
                    f"(threshold: {global_threshold})"
                ),
                evidence={"global_requests_per_second": global_count},
                should_block=False,
            ))

        # --- Slowloris detection ---
        connection_age = int(event.get("connection_age_seconds", 0))
        slowloris_age = int(self._cfg.get("slowloris_connection_age_seconds", 30))
        slowloris_threshold = int(self._cfg.get("slowloris_threshold", 20))

        if connection_age >= slowloris_age:
            slow_count = self._state.window_increment("ddos:slowloris", ip, 300)
            if slow_count >= slowloris_threshold:
                alerts.append(_new_alert(
                    detector=self.name,
                    alert_type="slowloris",
                    severity=self.severity,
                    source_ip=ip,
                    description=(
                        f"Slowloris attack detected: {slow_count} long-lived "
                        f"connections (age >= {slowloris_age}s) from {ip}"
                    ),
                    evidence={"slow_connections": slow_count, "connection_age_seconds": connection_age},
                    should_block=True,
                    block_duration_seconds=1800,
                ))

        # --- SYN flood detection ---
        if event.get("event_type") == "syn_flood_signal":
            incomplete_pct = float(event.get("incomplete_connection_pct", 0))
            syn_threshold = int(self._cfg.get("syn_flood_incomplete_pct", 80))
            if incomplete_pct >= syn_threshold:
                alerts.append(_new_alert(
                    detector=self.name,
                    alert_type="syn_flood",
                    severity="critical",
                    source_ip=ip,
                    description=(
                        f"SYN flood detected: {incomplete_pct:.0f}% incomplete "
                        f"connections from {ip}"
                    ),
                    evidence={"incomplete_pct": incomplete_pct},
                    should_block=True,
                    block_duration_seconds=3600,
                ))

        return alerts


# ---------------------------------------------------------------------------
# InjectionDetector
# ---------------------------------------------------------------------------

class InjectionDetector(BaseDetector):
    """
    Detects SQL injection, XSS, and OS command injection attempts.

    Pattern matching is performed against configurable request components.
    Each pattern has an associated confidence weight; the combined score
    determines whether an alert is raised.

    Configuration keys:
        confidence_threshold:  Minimum combined score (0.0–1.0) to alert.
        scan_targets:          List of event fields to inspect.
    """

    # (pattern, weight, category)
    _SQLI_PATTERNS: list[tuple[re.Pattern[str], float, str]] = [
        (re.compile(r"(?i)\b(union\s+select|select\s+.*\s+from|insert\s+into|drop\s+table|truncate\s+table)", re.I), 0.9, "sqli"),
        (re.compile(r"(?i)(--|#|/\*|\*/)\s*$"), 0.5, "sqli_comment"),
        (re.compile(r"(?i)\bor\b\s+['\"]?\s*\d+\s*['\"]?\s*=\s*['\"]?\s*\d+"), 0.8, "sqli_tautology"),
        (re.compile(r"(?i)(sleep\s*\(\s*\d+\s*\)|benchmark\s*\(|waitfor\s+delay)", re.I), 0.9, "sqli_timing"),
        (re.compile(r"(?i)(load_file\s*\(|into\s+outfile\s*['\"])", re.I), 0.95, "sqli_fileread"),
        (re.compile(r"'[^']*'=[^']*'|\"[^\"]*\"=[^\"]*\""), 0.5, "sqli_quote"),
    ]

    _XSS_PATTERNS: list[tuple[re.Pattern[str], float, str]] = [
        (re.compile(r"<script[^>]*>.*?</script>", re.I | re.S), 0.95, "xss_script"),
        (re.compile(r"(?i)on\w+\s*=\s*[\"']?[^\"'>\s]+"), 0.85, "xss_event"),
        (re.compile(r"(?i)javascript\s*:", re.I), 0.9, "xss_protocol"),
        (re.compile(r"(?i)<\s*img[^>]+src\s*=\s*[\"']?javascript:", re.I), 0.9, "xss_img"),
        (re.compile(r"(?i)document\.(cookie|location|write)\s*[=(]", re.I), 0.7, "xss_dom"),
        (re.compile(r"(?i)eval\s*\(|alert\s*\(|confirm\s*\(", re.I), 0.6, "xss_eval"),
    ]

    _CMD_INJECTION_PATTERNS: list[tuple[re.Pattern[str], float, str]] = [
        (re.compile(r"[;|`&]\s*(ls|cat|wget|curl|nc|bash|sh|python|perl|ruby|php)\b", re.I), 0.95, "cmdi_shell"),
        (re.compile(r"\$\(.*\)|`[^`]+`"), 0.85, "cmdi_subshell"),
        (re.compile(r"(?i)\.\./|\.\./\.\./|/etc/passwd|/etc/shadow|/proc/self"), 0.9, "path_traversal"),
        (re.compile(r"(?i)(wget|curl)\s+https?://"), 0.8, "cmdi_download"),
        (re.compile(r"(?i)(chmod|chown|rm\s+-rf|mkfifo)\s"), 0.85, "cmdi_dangerous"),
    ]

    @property
    def name(self) -> str:
        return "injection"

    def analyze(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        targets = self._cfg.get("scan_targets", ["uri", "query_string", "user_agent"])
        threshold = float(self._cfg.get("confidence_threshold", 0.7))

        # Build the inspection corpus from configured event fields
        corpus_parts: list[str] = []
        field_map = {
            "uri": "http_path",
            "query_string": "http_args",
            "user_agent": "user_agent",
            "referer": "referer",
            "post_body": "request_body",
            "http_uri": "http_uri",
        }
        for target in targets:
            field = field_map.get(target, target)
            val = event.get(field, "")
            if val:
                corpus_parts.append(str(val))
        corpus = " ".join(corpus_parts)
        if not corpus:
            return []

        alerts: list[dict[str, Any]] = []
        ip = event.get("source_ip", "0.0.0.0")

        for patterns, category in [
            (self._SQLI_PATTERNS, "sql_injection"),
            (self._XSS_PATTERNS, "xss"),
            (self._CMD_INJECTION_PATTERNS, "command_injection"),
        ]:
            max_score = 0.0
            matched_patterns: list[str] = []
            for pattern, weight, label in patterns:
                if pattern.search(corpus):
                    max_score = max(max_score, weight)
                    matched_patterns.append(label)

            if max_score >= threshold:
                # Track per-IP injection attempts for repeat offender scoring
                ip_count = self._state.window_increment(f"injection:{category}", ip, 3600)
                alerts.append(_new_alert(
                    detector=self.name,
                    alert_type=category,
                    severity=self.severity if max_score < 0.9 else "critical",
                    source_ip=ip,
                    description=(
                        f"{category.replace('_', ' ').title()} attempt detected from {ip} "
                        f"(confidence: {max_score:.0%}, repeat count: {ip_count})"
                    ),
                    evidence={
                        "confidence": max_score,
                        "matched_patterns": matched_patterns,
                        "corpus_excerpt": corpus[:200],
                        "ip_attempt_count_1h": ip_count,
                    },
                    http_path=event.get("http_path", ""),
                    http_method=event.get("http_method", ""),
                    should_block=max_score >= 0.9,
                ))

        return alerts


# ---------------------------------------------------------------------------
# BotDetector
# ---------------------------------------------------------------------------

class BotDetector(BaseDetector):
    """
    Detects automated bot traffic using user-agent analysis, request timing,
    and crawling behaviour (high distinct-path count, high error ratio).

    Configuration keys:
        whitelist_user_agents:    Substrings that identify legitimate bots.
        timing_jitter_threshold_ms: Minimum acceptable timing jitter.
        error_ratio_threshold:    HTTP error ratio that suggests a scanner.
        distinct_path_threshold:  Path diversity count per window per IP.
        window_seconds:           Analysis window duration.
    """

    # Known headless/automation UA substrings (lowercased for comparison)
    _HEADLESS_UA_PATTERNS = [
        "headlesschrome", "phantomjs", "selenium", "webdriver",
        "python-requests", "go-http-client", "java/", "okhttp",
        "libwww-perl", "lwp-useragent", "scrapy", "mechanize",
    ]

    _EMPTY_UA_SCORE = 0.8
    _HEADLESS_SCORE = 0.9
    _TIMING_SCORE = 0.6
    _CRAWL_SCORE = 0.7
    _ERROR_RATIO_SCORE = 0.65

    @property
    def name(self) -> str:
        return "bot"

    def analyze(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        ip = event.get("source_ip", "0.0.0.0")
        ua = event.get("user_agent", "").strip()
        whitelist = self._cfg.get("whitelist_user_agents", [])
        window = int(self._cfg.get("window_seconds", 60))

        # Skip whitelisted user agents (monitoring tools, search engines, etc.)
        for wl_ua in whitelist:
            if wl_ua.lower() in ua.lower():
                return []

        score = 0.0
        signals: list[str] = []
        alerts: list[dict[str, Any]] = []

        # --- User-agent analysis ---
        if not ua:
            score = max(score, self._EMPTY_UA_SCORE)
            signals.append("empty_user_agent")
        else:
            ua_lower = ua.lower()
            for pattern in self._HEADLESS_UA_PATTERNS:
                if pattern in ua_lower:
                    score = max(score, self._HEADLESS_SCORE)
                    signals.append(f"headless_ua:{pattern}")
                    break

        # --- Request timing jitter ---
        timing_jitter = int(event.get("request_duration_ms", 999))
        jitter_threshold = int(self._cfg.get("timing_jitter_threshold_ms", 50))
        if 0 < timing_jitter < jitter_threshold:
            score = max(score, self._TIMING_SCORE)
            signals.append(f"low_timing_jitter:{timing_jitter}ms")

        # --- Path diversity (crawling) ---
        path = event.get("http_path", "")
        if path:
            self._state.sadd(f"bot:paths:{window}", ip, path)
            distinct_paths = self._state.scard(f"bot:paths:{window}", ip)
            path_threshold = int(self._cfg.get("distinct_path_threshold", 100))
            if distinct_paths >= path_threshold:
                score = max(score, self._CRAWL_SCORE)
                signals.append(f"path_crawling:{distinct_paths}_distinct_paths")

        # --- Error ratio (scanner-like behaviour) ---
        http_status = int(event.get("http_status", 200))
        self._state.window_increment(f"bot:req:{ip}", "total", window)
        if http_status in (400, 401, 403, 404, 405, 410):
            err_count = self._state.window_increment(f"bot:err:{ip}", "errors", window)
            total_count = self._state.window_count(f"bot:req:{ip}", "total", window)
            if total_count >= 20:
                error_ratio = err_count / max(total_count, 1)
                err_threshold = float(self._cfg.get("error_ratio_threshold", 0.4))
                if error_ratio >= err_threshold:
                    score = max(score, self._ERROR_RATIO_SCORE)
                    signals.append(f"high_error_ratio:{error_ratio:.1%}")

        if score >= 0.6 and signals:
            alerts.append(_new_alert(
                detector=self.name,
                alert_type="bot_detected",
                severity=self.severity if score < 0.85 else "high",
                source_ip=ip,
                description=(
                    f"Automated bot traffic detected from {ip} "
                    f"(confidence: {score:.0%})"
                ),
                evidence={
                    "score": score,
                    "signals": signals,
                    "user_agent": ua[:200],
                },
                should_block=score >= 0.85,
            ))

        return alerts


# ---------------------------------------------------------------------------
# AccountTakeoverDetector
# ---------------------------------------------------------------------------

class AccountTakeoverDetector(BaseDetector):
    """
    Detects account takeover (ATO) attempts via geo-anomaly detection,
    login velocity checks, and device fingerprint changes.

    Relies on GeoIP enrichment data being present in the event
    (fields: ``country_code``, ``city``, ``asn``).

    Configuration keys:
        new_country_lookback_days:   Days of history before flagging new country.
        country_velocity_threshold:  Distinct countries per velocity_window.
        velocity_window_seconds:     Window for velocity checks.
        off_hours_start:             UTC hour marking start of off-hours period.
        off_hours_end:               UTC hour marking end of off-hours period.
    """

    @property
    def name(self) -> str:
        return "account_takeover"

    def analyze(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        event_type = event.get("event_type", "")
        # Only analyze successful or failed logins and session creation events
        if event_type not in ("login_success", "session_created", "password_changed",
                               "mfa_bypassed", "login_failed"):
            return []

        user_id: str = str(event.get("user_id") or event.get("username") or "")
        if not user_id:
            return []

        ip = event.get("source_ip", "0.0.0.0")
        country = event.get("country_code", event.get("country", ""))
        device_fp = event.get("device_fingerprint", "")
        alerts: list[dict[str, Any]] = []

        # --- Country history check ---
        if country:
            history_key = f"ato:countries:{user_id}"
            known_countries = self._state.smembers("ato:country_history", user_id)
            if country not in known_countries and len(known_countries) > 0:
                # New country for this user – potential ATO
                alerts.append(_new_alert(
                    detector=self.name,
                    alert_type="ato_new_country",
                    severity=self.severity,
                    source_ip=ip,
                    description=(
                        f"Login from new country '{country}' for user '{user_id}' "
                        f"(previously seen: {', '.join(sorted(known_countries)[:5])})"
                    ),
                    evidence={
                        "new_country": country,
                        "known_countries": sorted(known_countries)[:10],
                    },
                    user_id=user_id,
                    should_block=False,
                    requires_mfa_step_up=True,
                ))
            self._state.sadd("ato:country_history", user_id, country)

        # --- Login velocity check ---
        velocity_window = int(self._cfg.get("velocity_window_seconds", 3600))
        velocity_threshold = int(self._cfg.get("country_velocity_threshold", 3))
        if country:
            self._state.sadd(f"ato:velocity_countries:{velocity_window}", user_id, country)
            distinct_countries = self._state.scard(f"ato:velocity_countries:{velocity_window}", user_id)
            if distinct_countries >= velocity_threshold:
                alerts.append(_new_alert(
                    detector=self.name,
                    alert_type="ato_country_velocity",
                    severity="critical",
                    source_ip=ip,
                    description=(
                        f"Account '{user_id}' logged in from {distinct_countries} "
                        f"distinct countries within {velocity_window}s"
                    ),
                    evidence={
                        "distinct_countries": distinct_countries,
                        "window_seconds": velocity_window,
                    },
                    user_id=user_id,
                    should_block=True,
                    block_duration_seconds=7200,
                ))

        # --- Device fingerprint change ---
        if device_fp and event_type in ("login_success", "session_created"):
            stored_fp = self._state.get("ato:device_fp", user_id)
            if stored_fp and stored_fp != device_fp:
                alerts.append(_new_alert(
                    detector=self.name,
                    alert_type="ato_device_change",
                    severity="high",
                    source_ip=ip,
                    description=(
                        f"Device fingerprint changed for user '{user_id}' "
                        f"(new fp hash: {hashlib.sha256(device_fp.encode()).hexdigest()[:8]})"
                    ),
                    evidence={"fingerprint_changed": True},
                    user_id=user_id,
                    should_block=False,
                    requires_mfa_step_up=True,
                ))
            if device_fp:
                self._state.set("ato:device_fp", user_id, device_fp, ttl=86400 * 30)

        # --- Off-hours login ---
        now_hour = datetime.now(tz=timezone.utc).hour
        off_start = int(self._cfg.get("off_hours_start", 2))
        off_end = int(self._cfg.get("off_hours_end", 5))
        if event_type == "login_success" and off_start <= now_hour < off_end:
            off_count = self._state.window_increment("ato:off_hours", user_id, 3600)
            if off_count == 1:  # first off-hours login – alert once per hour
                alerts.append(_new_alert(
                    detector=self.name,
                    alert_type="ato_off_hours",
                    severity="low",
                    source_ip=ip,
                    description=(
                        f"Off-hours login for user '{user_id}' at "
                        f"{now_hour:02d}:xx UTC"
                    ),
                    evidence={"utc_hour": now_hour, "country": country},
                    user_id=user_id,
                    should_block=False,
                ))

        return alerts


# ---------------------------------------------------------------------------
# BonusAbuseDetector
# ---------------------------------------------------------------------------

class BonusAbuseDetector(BaseDetector):
    """
    Detects bonus abuse patterns specific to iGaming platforms.

    Checks:
      - Multiple accounts sharing the same device fingerprint
      - Multiple accounts from the same /24 subnet
      - VPN/proxy/Tor registration
      - Datacenter IP registration
      - High bonus claim velocity per device

    Configuration keys:
        max_accounts_per_device:       Max accounts per device fingerprint.
        max_accounts_per_subnet_24:    Max accounts per /24 subnet.
        flag_vpn_registrations:        Alert when VPN/proxy IP registers.
        flag_datacenter_registrations: Alert when datacenter IP registers.
        bonus_claim_threshold:         Max bonus claims per device per window.
        bonus_window_seconds:          Window for bonus velocity checks.
    """

    @property
    def name(self) -> str:
        return "bonus_abuse"

    def analyze(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        event_type = event.get("event_type", "")
        alerts: list[dict[str, Any]] = []
        ip = event.get("source_ip", "0.0.0.0")

        # --- Multi-account device fingerprint check ---
        device_fp = event.get("device_fingerprint", "")
        user_id = str(event.get("user_id") or "")

        if event_type in ("account_registered", "account_created") and device_fp and user_id:
            fp_hash = hashlib.sha256(device_fp.encode()).hexdigest()[:16]
            self._state.sadd("bonus_abuse:device_accounts", fp_hash, user_id)
            accts_on_device = self._state.scard("bonus_abuse:device_accounts", fp_hash)
            max_per_device = int(self._cfg.get("max_accounts_per_device", 3))

            if accts_on_device > max_per_device:
                alerts.append(_new_alert(
                    detector=self.name,
                    alert_type="bonus_abuse_multi_account_device",
                    severity=self.severity,
                    source_ip=ip,
                    description=(
                        f"{accts_on_device} accounts registered from device "
                        f"fingerprint {fp_hash} (limit: {max_per_device})"
                    ),
                    evidence={
                        "account_count": accts_on_device,
                        "device_fp_hash": fp_hash,
                        "registering_user": user_id,
                    },
                    user_id=user_id,
                    should_block=False,
                    requires_manual_review=True,
                ))

        # --- Multi-account subnet check ---
        if event_type in ("account_registered", "account_created") and user_id:
            subnet_24 = ".".join(ip.split(".")[:3]) + ".0/24" if "." in ip else ip
            self._state.sadd("bonus_abuse:subnet_accounts", subnet_24, user_id)
            accts_on_subnet = self._state.scard("bonus_abuse:subnet_accounts", subnet_24)
            max_per_subnet = int(self._cfg.get("max_accounts_per_subnet_24", 5))

            if accts_on_subnet > max_per_subnet:
                alerts.append(_new_alert(
                    detector=self.name,
                    alert_type="bonus_abuse_multi_account_subnet",
                    severity="medium",
                    source_ip=ip,
                    description=(
                        f"{accts_on_subnet} accounts registered from subnet "
                        f"{subnet_24} (limit: {max_per_subnet})"
                    ),
                    evidence={
                        "account_count": accts_on_subnet,
                        "subnet": subnet_24,
                        "registering_user": user_id,
                    },
                    user_id=user_id,
                    should_block=False,
                    requires_manual_review=True,
                ))

        # --- VPN/proxy detection ---
        is_vpn = event.get("is_vpn", False) or event.get("is_proxy", False) or event.get("is_tor", False)
        flag_vpn = str(self._cfg.get("flag_vpn_registrations", "true")).lower() in ("true", "1")
        if flag_vpn and is_vpn and event_type in ("account_registered", "bonus_claimed", "deposit_initiated"):
            vpn_type = "Tor" if event.get("is_tor") else ("Proxy" if event.get("is_proxy") else "VPN")
            alerts.append(_new_alert(
                detector=self.name,
                alert_type="bonus_abuse_vpn_registration",
                severity="high",
                source_ip=ip,
                description=(
                    f"{vpn_type} IP detected during '{event_type}' for user '{user_id}'"
                ),
                evidence={
                    "vpn_type": vpn_type,
                    "is_vpn": event.get("is_vpn"),
                    "is_proxy": event.get("is_proxy"),
                    "is_tor": event.get("is_tor"),
                },
                user_id=user_id,
                should_block=False,
                requires_manual_review=True,
            ))

        # --- Datacenter IP detection ---
        is_datacenter = event.get("is_datacenter", False)
        flag_dc = str(self._cfg.get("flag_datacenter_registrations", "true")).lower() in ("true", "1")
        if flag_dc and is_datacenter and event_type in ("account_registered", "bonus_claimed"):
            dc_name = event.get("datacenter_name", "unknown")
            alerts.append(_new_alert(
                detector=self.name,
                alert_type="bonus_abuse_datacenter_ip",
                severity="medium",
                source_ip=ip,
                description=(
                    f"Datacenter IP ({dc_name}) detected during '{event_type}' "
                    f"for user '{user_id}'"
                ),
                evidence={"datacenter_name": dc_name},
                user_id=user_id,
                should_block=False,
                requires_manual_review=True,
            ))

        # --- Bonus claim velocity ---
        if event_type == "bonus_claimed" and device_fp:
            fp_hash = hashlib.sha256(device_fp.encode()).hexdigest()[:16]
            bonus_window = int(self._cfg.get("bonus_window_seconds", 86400))
            bonus_threshold = int(self._cfg.get("bonus_claim_threshold", 5))
            claim_count = self._state.window_increment("bonus_abuse:claims", fp_hash, bonus_window)

            if claim_count >= bonus_threshold:
                alerts.append(_new_alert(
                    detector=self.name,
                    alert_type="bonus_abuse_velocity",
                    severity=self.severity,
                    source_ip=ip,
                    description=(
                        f"{claim_count} bonus claims in {bonus_window}s "
                        f"from device {fp_hash}"
                    ),
                    evidence={
                        "claim_count": claim_count,
                        "window_seconds": bonus_window,
                        "device_fp_hash": fp_hash,
                    },
                    user_id=user_id,
                    should_block=False,
                    requires_manual_review=True,
                ))

        return alerts


# ---------------------------------------------------------------------------
# Detection pipeline
# ---------------------------------------------------------------------------

class DetectionPipeline:
    """
    Runs a normalized event through all enabled detectors and collects alerts.

    Args:
        detectors: Ordered list of detector instances.
        alert_output: Callable that receives each alert dict (e.g. print to stdout,
                      write to file, push to Redis stream).
        dry_run:   When True, alerts are logged but not passed to alert_output.
    """

    def __init__(
        self,
        detectors: list[BaseDetector],
        alert_output: Any,
        dry_run: bool = False,
    ) -> None:
        self._detectors = detectors
        self._output = alert_output
        self._dry_run = dry_run

    def process(self, event: dict[str, Any]) -> int:
        """
        Run all detectors against the event.

        Args:
            event: Normalized event dict.

        Returns:
            Number of alerts generated.
        """
        total = 0
        for detector in self._detectors:
            try:
                alerts = detector.analyze(event)
            except Exception as exc:  # noqa: BLE001
                log.error("Detector '%s' raised an exception: %s", detector.name, exc, exc_info=True)
                continue
            for alert in alerts:
                total += 1
                log.info(
                    "ALERT type=%s severity=%s ip=%s",
                    alert["alert_type"],
                    alert["severity"],
                    alert["source_ip"],
                )
                if not self._dry_run:
                    try:
                        self._output(alert)
                    except Exception as exc:  # noqa: BLE001
                        log.error("Alert output handler failed: %s", exc)
        return total


# ---------------------------------------------------------------------------
# Alert output handlers
# ---------------------------------------------------------------------------

def _stdout_alert_output(alert: dict[str, Any]) -> None:
    """Write a JSON alert to stdout."""
    print(json.dumps(alert), flush=True)


def _file_alert_output(path: str) -> Any:
    """Return a callable that appends JSON alerts to a JSONL file."""
    from pathlib import Path as _Path
    _Path(path).parent.mkdir(parents=True, exist_ok=True)

    def _write(alert: dict[str, Any]) -> None:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(alert) + "\n")

    return _write


def _redis_stream_alert_output(state: RedisState, stream_key: str = "soar:alerts") -> Any:
    """Return a callable that pushes JSON alerts to a Redis stream."""

    def _push(alert: dict[str, Any]) -> None:
        state._r.xadd(stream_key, {"data": json.dumps(alert)}, maxlen=100000, approximate=True)

    return _push


# ---------------------------------------------------------------------------
# Redis stream event reader
# ---------------------------------------------------------------------------

def _read_events_from_redis(
    state: RedisState,
    stream_key: str = "soar:events",
    consumer_group: str = "threat_detector",
    consumer_name: str = "detector-1",
    batch_size: int = 100,
    block_ms: int = 2000,
) -> Generator[dict[str, Any], None, None]:
    """
    Read normalized events from a Redis stream using consumer groups.

    The stream is populated by log_collector.py when configured to write
    events to Redis instead of (or in addition to) the n8n webhook.

    Args:
        state:          RedisState instance.
        stream_key:     Redis stream key.
        consumer_group: Redis consumer group name.
        consumer_name:  This consumer's identity within the group.
        batch_size:     Maximum messages per read call.
        block_ms:       Milliseconds to block waiting for messages.

    Yields:
        Deserialized event dicts.
    """
    r = state._r
    # Ensure the consumer group exists
    try:
        r.xgroup_create(stream_key, consumer_group, id="$", mkstream=True)
        log.info("Created Redis consumer group '%s' on stream '%s'", consumer_group, stream_key)
    except redis.exceptions.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise

    while True:
        try:
            messages = r.xreadgroup(
                consumer_group,
                consumer_name,
                {stream_key: ">"},
                count=batch_size,
                block=block_ms,
            )
        except redis.exceptions.ConnectionError as exc:
            log.error("Redis connection lost: %s – retrying in 5s", exc)
            time.sleep(5)
            continue

        if not messages:
            continue

        for _stream, entries in messages:
            for msg_id, fields in entries:
                raw = fields.get("data", "")
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    log.warning("Could not decode event from Redis stream: %.80s", raw)
                    r.xack(stream_key, consumer_group, msg_id)
                    continue
                yield event
                r.xack(stream_key, consumer_group, msg_id)


# ---------------------------------------------------------------------------
# Config and factory helpers
# ---------------------------------------------------------------------------

def _resolve_env(value: str) -> str:
    pattern = re.compile(r"\$\{([^}:]+)(?::([^}]*))?\}")

    def _replace(m: re.Match[str]) -> str:
        return os.environ.get(m.group(1), m.group(2) or "")

    return pattern.sub(_replace, value)


def _deep_resolve(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _deep_resolve(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_resolve(v) for v in obj]
    if isinstance(obj, str):
        return _resolve_env(obj)
    return obj


def load_config(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as fh:
            raw: dict[str, Any] = yaml.safe_load(fh) or {}
    except OSError as exc:
        log.error("Cannot read config: %s", exc)
        sys.exit(1)
    except yaml.YAMLError as exc:
        log.error("Invalid YAML: %s", exc)
        sys.exit(1)
    return _deep_resolve(raw)


_DETECTOR_REGISTRY: dict[str, type[BaseDetector]] = {
    "brute_force": BruteForceDetector,
    "ddos": DDoSDetector,
    "injection": InjectionDetector,
    "bot": BotDetector,
    "account_takeover": AccountTakeoverDetector,
    "bonus_abuse": BonusAbuseDetector,
}


def _build_detectors(
    cfg: dict[str, Any],
    state: RedisState,
    detector_filter: str | None,
) -> list[BaseDetector]:
    detectors_cfg = cfg.get("detectors", {})
    detectors: list[BaseDetector] = []
    for key, cls in _DETECTOR_REGISTRY.items():
        if detector_filter and key != detector_filter:
            continue
        det_cfg = detectors_cfg.get(key, {})
        detector = cls(det_cfg, state)
        if detector.enabled:
            detectors.append(detector)
            log.info("Registered detector: %s", key)
        else:
            log.info("Detector '%s' is disabled in config", key)
    return detectors


def _build_redis_state(cfg: dict[str, Any]) -> RedisState:
    r_cfg = cfg.get("redis", {})
    return RedisState(
        host=r_cfg.get("host", "localhost"),
        port=int(r_cfg.get("port", 6379)),
        db=int(r_cfg.get("db", 0)),
        password=r_cfg.get("password", ""),
        key_prefix=r_cfg.get("key_prefix", "soar:"),
        default_ttl=int(r_cfg.get("default_ttl_seconds", 86400)),
        max_conn=int(r_cfg.get("max_connections", 20)),
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(cfg: dict[str, Any], dry_run: bool, detector_filter: str | None = None) -> None:
    """
    Start the threat detection engine.

    Reads normalized events from a Redis stream, processes each event through
    all enabled detectors, and writes alerts to stdout and optionally to a
    Redis alerts stream.

    Args:
        cfg:             Fully loaded configuration dict.
        dry_run:         When True, alerts are logged but not emitted.
        detector_filter: When set, only the named detector runs.
    """
    state = _build_redis_state(cfg)
    detectors = _build_detectors(cfg, state, detector_filter)

    if not detectors:
        log.error("No detectors are enabled or matched the filter.")
        sys.exit(1)

    # Alert output: stdout + Redis stream
    redis_output = _redis_stream_alert_output(state, "soar:alerts")

    def _combined_output(alert: dict[str, Any]) -> None:
        _stdout_alert_output(alert)
        redis_output(alert)

    pipeline = DetectionPipeline(detectors, _combined_output, dry_run)

    log.info("Threat detector ready – processing events from Redis stream soar:events")
    total_events = 0
    total_alerts = 0

    try:
        for event in _read_events_from_redis(state):
            alert_count = pipeline.process(event)
            total_events += 1
            total_alerts += alert_count
            if total_events % 1000 == 0:
                log.info("Processed %d events, generated %d alerts", total_events, total_alerts)
    except KeyboardInterrupt:
        log.info("Shutdown signal received")

    log.info("Detector stopped: %d events processed, %d alerts generated", total_events, total_alerts)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AcmeToCasino SOAR standalone threat detection engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--config",
        default="/etc/soar/config.yml",
        help="Path to SOAR YAML configuration file (default: %(default)s)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run detection but do not emit alerts",
    )
    parser.add_argument(
        "--detector",
        metavar="NAME",
        default=None,
        choices=list(_DETECTOR_REGISTRY.keys()),
        help="Run only the named detector: %(choices)s",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Override log level from config",
    )
    return parser


def main() -> None:
    """Entry point."""
    parser = _build_parser()
    args = parser.parse_args()
    cfg = load_config(args.config)
    log_level = args.log_level or cfg.get("system", {}).get("log_level", "INFO")
    logging.getLogger().setLevel(getattr(logging, log_level.upper(), logging.INFO))
    log.info("Starting SOAR threat detector | env=%s", cfg.get("system", {}).get("environment", "unknown"))
    run(cfg, dry_run=args.dry_run, detector_filter=args.detector)


if __name__ == "__main__":
    main()
