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
ip_detection_pipeline.py — FastAPI middleware implementing all 8 iGaming security gates.

Each incoming request passes through the following sequential gates:

  Gate 1  IP Type Check      — Tor / Datacenter / Residential via ASN + MaxMind GeoIP2
  Gate 2  VPN Detection      — IP reputation DB (proxycheck.io or local Redis cache)
  Gate 3  Known Proxy Check  — Hardcoded datacenter ASN list (13+ providers)
  Gate 4  IP Blacklist       — Abuse/ban database backed by Redis
  Gate 5  Fraud Score        — Real-time multi-signal scoring (velocity + amount anomaly)
  Gate 6  Device Fingerprint — JA3 + browser fingerprint + history anomaly
  Gate 7  Sanctions/PEP      — OFAC SDN list fuzzy name matching
  Gate 8  KYC Status         — Document lifecycle state machine check

Every gate returns one of:
  BLOCK  — HTTP 403 with reason code JSON body
  REVIEW — Pass-through with X-Security-Review: <reason> response header
  PASS   — Continue to upstream

Audit log entry is written for every decision (structured JSON via structlog).

Environment variables (no hardcoded values):
  REDIS_URL              — Redis connection string   (default: redis://localhost:6379/0)
  MAXMIND_DB_PATH        — Path to GeoLite2-ASN.mmdb (default: /var/lib/GeoIP/GeoLite2-ASN.mmdb)
  MAXMIND_CITY_DB_PATH   — Path to GeoLite2-City.mmdb (default: /var/lib/GeoIP/GeoLite2-City.mmdb)
  PROXYCHECK_API_KEY     — proxycheck.io API key (optional; Gate 2 falls back to ASN heuristics)
  PIPELINE_ENV           — "production" | "staging" (default: production)
  FRAUD_SCORE_THRESHOLD  — Gate 5 block threshold 0-100 (default: 75)
  FRAUD_SCORE_REVIEW     — Gate 5 review threshold 0-100 (default: 50)
  KYC_SERVICE_URL        — Internal KYC service base URL (default: http://kyc-service:8080)
  KYC_SERVICE_TOKEN      — Bearer token for KYC service
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

import geoip2.database
import geoip2.errors
import httpx
import redis
import structlog
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from device_fingerprint import DeviceFingerprintTracker, fingerprint_from_request_headers  # ty:ignore[unresolved-import]
from ip_blacklist_service import IPBlacklistService  # ty:ignore[unresolved-import]
from sanctions_checker import SanctionsChecker  # ty:ignore[unresolved-import]

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REDIS_URL: str = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
MAXMIND_ASN_DB: str = os.environ.get("MAXMIND_DB_PATH", "/var/lib/GeoIP/GeoLite2-ASN.mmdb")
MAXMIND_CITY_DB: str = os.environ.get("MAXMIND_CITY_DB_PATH", "/var/lib/GeoIP/GeoLite2-City.mmdb")
PROXYCHECK_API_KEY: str = os.environ.get("PROXYCHECK_API_KEY", "")
PIPELINE_ENV: str = os.environ.get("PIPELINE_ENV", "production")
# int(float(...)) so that callers passing the threshold as a float string
# (e.g. "75.0" from a YAML config loader) still parse cleanly.
FRAUD_SCORE_BLOCK: int = int(float(os.environ.get("FRAUD_SCORE_THRESHOLD", "75")))
FRAUD_SCORE_REVIEW: int = int(float(os.environ.get("FRAUD_SCORE_REVIEW", "50")))
KYC_SERVICE_URL: str = os.environ.get("KYC_SERVICE_URL", "http://kyc-service:8080")
KYC_SERVICE_TOKEN: str = os.environ.get("KYC_SERVICE_TOKEN", "")

# ---------------------------------------------------------------------------
# Datacenter ASN list (Gate 3)
# ASNs listed here are treated as Known Proxy / Datacenter
# ---------------------------------------------------------------------------

DATACENTER_ASNS: frozenset[int] = frozenset({
    14061,   # DigitalOcean
    16509,   # Amazon AWS
    15169,   # Google Cloud
    8075,    # Microsoft Azure
    20473,   # Vultr Holdings LLC
    63949,   # Akamai Connected Cloud (Linode)
    24940,   # Hetzner Online GmbH
    16276,   # OVH SAS
    13335,   # Cloudflare Inc.
    36351,   # SoftLayer Technologies (IBM Cloud)
    19527,   # Google LLC (additional range)
    32934,   # Facebook (Meta Connectivity)
    2635,    # Internap Network Services
    46606,   # Unified Layer (Bluehost/HostGator)
})

# ---------------------------------------------------------------------------
# Reason codes (must match flowchart spec)
# ---------------------------------------------------------------------------

class ReasonCode(str, Enum):
    BANNED_PROXY_TOR    = "BANNED_PROXY_TOR"
    BANNED_PROXY_DC     = "BANNED_PROXY_DC"
    BANNED_PROXY_VPN    = "BANNED_PROXY_VPN"
    BANNED_PROXY_KNOWN  = "BANNED_PROXY_KNOWN"
    BANNED_IP_BLACKLIST = "BANNED_IP_BLACKLIST"
    HIGH_FRAUD_SCORE    = "HIGH_FRAUD_SCORE"
    DEVICE_ANOMALY      = "DEVICE_ANOMALY"
    SANCTIONS_MATCH     = "SANCTIONS_MATCH"
    KYC_REQUIRED        = "KYC_REQUIRED"
    KYC_SUSPENDED       = "KYC_SUSPENDED"


class GateVerdict(str, Enum):
    BLOCK  = "BLOCK"
    REVIEW = "REVIEW"
    PASS   = "PASS"


# ---------------------------------------------------------------------------
# Gate result
# ---------------------------------------------------------------------------

@dataclass
class GateResult:
    gate: int
    gate_name: str
    verdict: GateVerdict
    reason_code: Optional[ReasonCode] = None
    detail: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0

    def is_terminal(self) -> bool:
        return self.verdict == GateVerdict.BLOCK


@dataclass
class PipelineResult:
    ip: str
    player_id: str
    session_id: str
    final_verdict: GateVerdict
    blocking_gate: Optional[GateResult]
    gates: list[GateResult]
    review_flags: list[str]
    total_latency_ms: float
    timestamp: float = field(default_factory=time.time)

    def to_403_body(self) -> dict:
        assert self.blocking_gate is not None
        return {
            "error": "access_denied",
            "reason": self.blocking_gate.reason_code,
            "detail": self.blocking_gate.detail,
            "gate": self.blocking_gate.gate_name,
            "request_id": f"{self.player_id}:{self.session_id}:{int(self.timestamp)}",
        }


# ---------------------------------------------------------------------------
# Shared clients (lazy singletons)
# ---------------------------------------------------------------------------

_redis_client: Optional[redis.Redis[str]] = None
_blacklist_service: Optional[IPBlacklistService] = None
_sanctions_checker: Optional[SanctionsChecker] = None
_fp_tracker: Optional[DeviceFingerprintTracker] = None
_asn_reader: Optional[geoip2.database.Reader] = None
_city_reader: Optional[geoip2.database.Reader] = None


def get_redis() -> redis.Redis[str]:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(  # ty:ignore[invalid-assignment]
            REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
            retry_on_timeout=True,
        )
    assert _redis_client is not None
    return _redis_client


def get_blacklist() -> IPBlacklistService:
    global _blacklist_service
    if _blacklist_service is None:
        _blacklist_service = IPBlacklistService(REDIS_URL)
    return _blacklist_service


def get_sanctions() -> SanctionsChecker:
    global _sanctions_checker
    if _sanctions_checker is None:
        _sanctions_checker = SanctionsChecker(REDIS_URL)
    return _sanctions_checker


def get_fp_tracker() -> DeviceFingerprintTracker:
    global _fp_tracker
    if _fp_tracker is None:
        _fp_tracker = DeviceFingerprintTracker(REDIS_URL)
    return _fp_tracker


def get_asn_reader() -> Optional[geoip2.database.Reader]:
    global _asn_reader
    if _asn_reader is None and os.path.isfile(MAXMIND_ASN_DB):
        _asn_reader = geoip2.database.Reader(MAXMIND_ASN_DB)
    return _asn_reader


def get_city_reader() -> Optional[geoip2.database.Reader]:
    global _city_reader
    if _city_reader is None and os.path.isfile(MAXMIND_CITY_DB):
        _city_reader = geoip2.database.Reader(MAXMIND_CITY_DB)
    return _city_reader


# ---------------------------------------------------------------------------
# Velocity / fraud tracking helpers
# ---------------------------------------------------------------------------

def _incr_velocity(
    r: redis.Redis[str],
    key: str,
    window_seconds: int,
    max_value: int = 10_000,
) -> int:
    """Increment a counter with sliding window expiry. Returns new count."""
    pipe = r.pipeline(transaction=True)
    pipe.incr(key)
    pipe.expire(key, window_seconds)
    result = pipe.execute()
    count = int(result[0])
    return min(count, max_value)


def _get_player_profile(r: redis.Redis[str], player_id: str) -> dict:
    """Return cached player transaction profile from Redis."""
    raw = r.get(f"fraud:profile:{player_id}")
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    return {}


# ---------------------------------------------------------------------------
# Gate implementations
# ---------------------------------------------------------------------------

def _gate1_ip_type(ip: str, request: Request) -> GateResult:
    """
    Gate 1 — IP Type Check: Tor / Datacenter / Residential.

    Uses MaxMind GeoIP2 ASN database to retrieve the originating ASN.
    Tor detection is performed via a Redis-cached list of Tor exit nodes
    populated externally (e.g. https://check.torproject.org/torbulkexitlist).
    """
    t0 = time.perf_counter()

    # Check Tor exit node list (Redis SET: tor:exit_nodes)
    r = get_redis()
    is_tor = bool(r.sismember("tor:exit_nodes", ip))
    if is_tor:
        return GateResult(
            gate=1,
            gate_name="ip_type_check",
            verdict=GateVerdict.BLOCK,
            reason_code=ReasonCode.BANNED_PROXY_TOR,
            detail=f"IP {ip} is a known Tor exit node",
            latency_ms=(time.perf_counter() - t0) * 1000,
        )

    # MaxMind ASN lookup
    asn_number: Optional[int] = None
    asn_org: str = ""
    reader = get_asn_reader()
    if reader:
        try:
            resp = reader.asn(ip)
            asn_number = resp.autonomous_system_number
            asn_org = resp.autonomous_system_organization or ""
        except geoip2.errors.AddressNotFoundError:
            pass
        except Exception as exc:
            logger.warning("maxmind_asn_lookup_failed", ip=ip, error=str(exc))

    # Datacenter ASN check
    if asn_number and asn_number in DATACENTER_ASNS:
        return GateResult(
            gate=1,
            gate_name="ip_type_check",
            verdict=GateVerdict.BLOCK,
            reason_code=ReasonCode.BANNED_PROXY_DC,
            detail=f"IP {ip} belongs to datacenter ASN {asn_number} ({asn_org})",
            metadata={"asn": asn_number, "org": asn_org},
            latency_ms=(time.perf_counter() - t0) * 1000,
        )

    return GateResult(
        gate=1,
        gate_name="ip_type_check",
        verdict=GateVerdict.PASS,
        detail=f"ASN {asn_number} ({asn_org})",
        metadata={"asn": asn_number, "org": asn_org},
        latency_ms=(time.perf_counter() - t0) * 1000,
    )


def _gate2_vpn_detection(ip: str, request: Request) -> GateResult:
    """
    Gate 2 — VPN Detection via proxycheck.io API or local reputation cache.

    If PROXYCHECK_API_KEY is set, queries proxycheck.io (v2) with vpn=1.
    Otherwise falls back to a Redis-cached VPN IP list (vpn:ip_list SET)
    which can be populated from any provider (IPHub, ip-api.com, etc.).
    """
    t0 = time.perf_counter()

    # Fast path: check Redis VPN cache first
    r = get_redis()
    cached_verdict = r.get(f"vpn:cache:{ip}")
    if cached_verdict == "vpn":
        return GateResult(
            gate=2,
            gate_name="vpn_detection",
            verdict=GateVerdict.BLOCK,
            reason_code=ReasonCode.BANNED_PROXY_VPN,
            detail=f"IP {ip} is a known VPN endpoint (cached)",
            latency_ms=(time.perf_counter() - t0) * 1000,
        )
    if cached_verdict == "clean":
        return GateResult(
            gate=2,
            gate_name="vpn_detection",
            verdict=GateVerdict.PASS,
            detail="VPN check: clean (cached)",
            latency_ms=(time.perf_counter() - t0) * 1000,
        )

    # Check static VPN IP set
    if r.sismember("vpn:ip_list", ip):
        r.setex(f"vpn:cache:{ip}", 3600, "vpn")
        return GateResult(
            gate=2,
            gate_name="vpn_detection",
            verdict=GateVerdict.BLOCK,
            reason_code=ReasonCode.BANNED_PROXY_VPN,
            detail=f"IP {ip} matched VPN reputation list",
            latency_ms=(time.perf_counter() - t0) * 1000,
        )

    # Live proxycheck.io query (if API key is configured)
    if PROXYCHECK_API_KEY:
        is_vpn = _proxycheck_query(ip, PROXYCHECK_API_KEY)
        ttl = 3600
        if is_vpn:
            r.setex(f"vpn:cache:{ip}", ttl, "vpn")
            return GateResult(
                gate=2,
                gate_name="vpn_detection",
                verdict=GateVerdict.BLOCK,
                reason_code=ReasonCode.BANNED_PROXY_VPN,
                detail=f"proxycheck.io confirmed VPN: {ip}",
                metadata={"source": "proxycheck.io"},
                latency_ms=(time.perf_counter() - t0) * 1000,
            )
        r.setex(f"vpn:cache:{ip}", ttl, "clean")

    return GateResult(
        gate=2,
        gate_name="vpn_detection",
        verdict=GateVerdict.PASS,
        detail="VPN check: clean",
        latency_ms=(time.perf_counter() - t0) * 1000,
    )


def _proxycheck_query(ip: str, api_key: str) -> bool:
    """Query proxycheck.io v2 API. Returns True if VPN detected."""
    url = f"https://proxycheck.io/v2/{ip}?key={api_key}&vpn=1&asn=1"
    try:
        with httpx.Client(timeout=3.0) as client:
            resp = client.get(url)
            data = resp.json()
            ip_data = data.get(ip, {})
            return ip_data.get("vpn") == "yes" or ip_data.get("proxy") == "yes"
    except Exception as exc:
        logger.warning("proxycheck_query_failed", ip=ip, error=str(exc))
        return False  # fail open on external service error


def _gate3_known_proxy(ip: str, request: Request) -> GateResult:
    """
    Gate 3 — Known Proxy Check via ASN list + proxy database lookup.

    Cross-references the IP's ASN against DATACENTER_ASNS (already checked
    in Gate 1) and a broader Redis proxy database (proxy:asn_list SET of
    ASN integers, proxy:ip_list SET of specific IPs).
    """
    t0 = time.perf_counter()
    r = get_redis()

    # Check direct IP match in proxy database
    if r.sismember("proxy:ip_list", ip):
        return GateResult(
            gate=3,
            gate_name="known_proxy_check",
            verdict=GateVerdict.BLOCK,
            reason_code=ReasonCode.BANNED_PROXY_KNOWN,
            detail=f"IP {ip} matched known proxy database",
            latency_ms=(time.perf_counter() - t0) * 1000,
        )

    # Check ASN against extended proxy ASN list
    reader = get_asn_reader()
    if reader:
        try:
            resp = reader.asn(ip)
            asn_number = resp.autonomous_system_number
            if asn_number:
                # Check extended ASN blocklist in Redis (proxy:asn_list is a set of ints as strings)
                if r.sismember("proxy:asn_list", str(asn_number)):
                    return GateResult(
                        gate=3,
                        gate_name="known_proxy_check",
                        verdict=GateVerdict.BLOCK,
                        reason_code=ReasonCode.BANNED_PROXY_KNOWN,
                        detail=f"IP {ip} ASN {asn_number} in known proxy ASN list",
                        metadata={"asn": asn_number},
                        latency_ms=(time.perf_counter() - t0) * 1000,
                    )
        except geoip2.errors.AddressNotFoundError:
            pass
        except Exception as exc:
            logger.warning("maxmind_gate3_failed", ip=ip, error=str(exc))

    return GateResult(
        gate=3,
        gate_name="known_proxy_check",
        verdict=GateVerdict.PASS,
        detail="Not in known proxy database",
        latency_ms=(time.perf_counter() - t0) * 1000,
    )


def _gate4_ip_blacklist(ip: str, request: Request) -> GateResult:
    """
    Gate 4 — IP Blacklist Check via Redis-backed abuse/ban database.
    """
    t0 = time.perf_counter()
    result = get_blacklist().check(ip)

    if result.is_blacklisted:
        entry = result.entry
        detail = "IP is blacklisted"
        if entry:
            detail = f"IP blacklisted — reason: {entry.reason} (source: {entry.source}, score: {entry.confidence_score})"
        return GateResult(
            gate=4,
            gate_name="ip_blacklist_check",
            verdict=GateVerdict.BLOCK,
            reason_code=ReasonCode.BANNED_IP_BLACKLIST,
            detail=detail,
            metadata={
                "source": entry.source if entry else "",
                "confidence": entry.confidence_score if entry else 0,
            },
            latency_ms=(time.perf_counter() - t0) * 1000,
        )

    return GateResult(
        gate=4,
        gate_name="ip_blacklist_check",
        verdict=GateVerdict.PASS,
        detail="IP not blacklisted",
        latency_ms=(time.perf_counter() - t0) * 1000,
    )


def _gate5_fraud_score(ip: str, request: Request, player_id: str) -> GateResult:
    """
    Gate 5 — Fraud Score: real-time multi-signal scoring.

    Signals:
      - Request velocity per IP (30-second and 5-minute windows)
      - Request velocity per player ID (1-minute window)
      - Transaction amount anomaly vs. player's rolling average (from profile cache)
      - Failed auth attempts in last 15 minutes
      - New account + high-value action combination
    """
    t0 = time.perf_counter()
    r = get_redis()
    score = 0
    signals: list[str] = []

    # --- Velocity: IP in 30 s ---
    ip_30s = _incr_velocity(r, f"vel:ip30:{ip}", 30)
    if ip_30s > 20:
        score += 25
        signals.append(f"IP_VEL_30S:{ip_30s}")
    elif ip_30s > 10:
        score += 10
        signals.append(f"IP_VEL_30S_WARN:{ip_30s}")

    # --- Velocity: IP in 5 min ---
    ip_5m = _incr_velocity(r, f"vel:ip5m:{ip}", 300)
    if ip_5m > 100:
        score += 20
        signals.append(f"IP_VEL_5M:{ip_5m}")
    elif ip_5m > 50:
        score += 8
        signals.append(f"IP_VEL_5M_WARN:{ip_5m}")

    # --- Velocity: player in 1 min ---
    if player_id:
        player_1m = _incr_velocity(r, f"vel:player1m:{player_id}", 60)
        if player_1m > 30:
            score += 20
            signals.append(f"PLAYER_VEL_1M:{player_1m}")
        elif player_1m > 15:
            score += 8
            signals.append(f"PLAYER_VEL_1M_WARN:{player_1m}")

    # --- Failed auth attempts ---
    if player_id:
        fail_key = f"auth:fail:{player_id}"
        _fail_raw = r.get(fail_key)
        try:
            fail_count = int(_fail_raw) if _fail_raw is not None else 0
        except (ValueError, TypeError):
            fail_count = 0
        if fail_count >= 5:
            score += 15
            signals.append(f"FAILED_AUTH:{fail_count}")
        elif fail_count >= 3:
            score += 8
            signals.append(f"FAILED_AUTH_WARN:{fail_count}")

    # --- Transaction amount anomaly ---
    if player_id:
        profile = _get_player_profile(r, player_id)
        tx_amount = _extract_tx_amount(request)
        if tx_amount and profile:
            avg = float(profile.get("avg_tx_amount", 0))
            if avg > 0:
                ratio = tx_amount / avg
                if ratio > 10:
                    score += 25
                    signals.append(f"AMOUNT_ANOMALY:{ratio:.1f}x_avg")
                elif ratio > 5:
                    score += 10
                    signals.append(f"AMOUNT_ANOMALY_WARN:{ratio:.1f}x_avg")

    # --- New account + high-value combination ---
    if player_id:
        profile = _get_player_profile(r, player_id)
        account_age_days = float(profile.get("account_age_days", 999))
        tx_amount = _extract_tx_amount(request) or 0
        if account_age_days < 1 and tx_amount > 1000:
            score += 25
            signals.append(f"NEW_ACCT_HIGH_VALUE:age={account_age_days}d,amt={tx_amount}")

    score = min(score, 100)

    if score >= FRAUD_SCORE_BLOCK:
        return GateResult(
            gate=5,
            gate_name="fraud_score",
            verdict=GateVerdict.BLOCK,
            reason_code=ReasonCode.HIGH_FRAUD_SCORE,
            detail=f"Fraud score {score}/100 — signals: {', '.join(signals)}",
            metadata={"score": score, "signals": signals},
            latency_ms=(time.perf_counter() - t0) * 1000,
        )
    if score >= FRAUD_SCORE_REVIEW:
        return GateResult(
            gate=5,
            gate_name="fraud_score",
            verdict=GateVerdict.REVIEW,
            detail=f"Fraud score {score}/100 — review flagged: {', '.join(signals)}",
            metadata={"score": score, "signals": signals},
            latency_ms=(time.perf_counter() - t0) * 1000,
        )

    return GateResult(
        gate=5,
        gate_name="fraud_score",
        verdict=GateVerdict.PASS,
        detail=f"Fraud score {score}/100",
        metadata={"score": score},
        latency_ms=(time.perf_counter() - t0) * 1000,
    )


def _extract_tx_amount(request: Request) -> Optional[float]:
    """Best-effort extraction of transaction amount from query params or cached body."""
    try:
        amount = request.query_params.get("amount") or request.query_params.get("tx_amount")
        if amount:
            return float(amount)
    except (ValueError, AttributeError):
        pass
    return None


def _gate6_device_fingerprint(ip: str, request: Request, player_id: str) -> GateResult:
    """
    Gate 6 — Device Fingerprint Anomaly Detection.

    Extracts JA3 (from X-JA3 header set by nginx) and browser-level signals
    from the request.  Compares against stored history to detect anomalies.
    """
    t0 = time.perf_counter()

    # Build fingerprint from available request signals
    headers = dict(request.headers)
    client_data = {
        "canvas_hash":      request.headers.get("x-canvas-hash", ""),
        "timezone_offset":  request.headers.get("x-tz-offset", "0"),
        "screen_resolution": request.headers.get("x-screen-res", ""),
        "platform":         request.headers.get("x-platform", ""),
        "plugins_hash":     request.headers.get("x-plugins-hash", ""),
        "webgl_vendor":     request.headers.get("x-webgl-vendor", ""),
        "webgl_renderer":   request.headers.get("x-webgl-renderer", ""),
    }

    session_id = request.headers.get("x-session-id", "unknown")
    fp = fingerprint_from_request_headers(player_id, session_id, headers, client_data)

    # Skip check if no JA3 header (non-browser client / internal request)
    if fp.ja3_hash == "unknown":
        return GateResult(
            gate=6,
            gate_name="device_fingerprint",
            verdict=GateVerdict.PASS,
            detail="No JA3 header — skipped (non-browser client)",
            latency_ms=(time.perf_counter() - t0) * 1000,
        )

    # Expected timezone from player profile
    r = get_redis()
    profile = _get_player_profile(r, player_id)
    expected_tz = profile.get("timezone_offset")

    tracker = get_fp_tracker()
    anomaly = tracker.check_and_record(fp, expected_timezone_offset=expected_tz)

    if anomaly.verdict == "BLOCK":
        return GateResult(
            gate=6,
            gate_name="device_fingerprint",
            verdict=GateVerdict.BLOCK,
            reason_code=ReasonCode.DEVICE_ANOMALY,
            detail=f"Device anomaly score {anomaly.anomaly_score}/100 — {', '.join(anomaly.signals)}",
            metadata={
                "anomaly_score": anomaly.anomaly_score,
                "signals": anomaly.signals,
                "fp_hash": anomaly.fp_hash,
            },
            latency_ms=(time.perf_counter() - t0) * 1000,
        )
    if anomaly.verdict == "REVIEW":
        return GateResult(
            gate=6,
            gate_name="device_fingerprint",
            verdict=GateVerdict.REVIEW,
            detail=f"Device anomaly review — score {anomaly.anomaly_score}: {', '.join(anomaly.signals)}",
            metadata={"anomaly_score": anomaly.anomaly_score, "signals": anomaly.signals},
            latency_ms=(time.perf_counter() - t0) * 1000,
        )

    return GateResult(
        gate=6,
        gate_name="device_fingerprint",
        verdict=GateVerdict.PASS,
        detail=f"Device fingerprint clean (score {anomaly.anomaly_score})",
        metadata={"fp_hash": anomaly.fp_hash},
        latency_ms=(time.perf_counter() - t0) * 1000,
    )


def _gate7_sanctions(ip: str, request: Request, player_id: str) -> GateResult:
    """
    Gate 7 — Sanctions/PEP Check via OFAC SDN list fuzzy matching.

    Extracts the player's full name from the X-Player-Name header (set by
    the authentication middleware after JWT validation) and runs it against
    the cached OFAC SDN list.

    Returns REVIEW (not BLOCK) for near-miss matches to allow manual review.
    """
    t0 = time.perf_counter()

    player_name = request.headers.get("x-player-name", "").strip()
    if not player_name:
        # No name available — skip (cannot match without a name)
        return GateResult(
            gate=7,
            gate_name="sanctions_check",
            verdict=GateVerdict.PASS,
            detail="No player name header — sanctions check skipped",
            latency_ms=(time.perf_counter() - t0) * 1000,
        )

    checker = get_sanctions()

    # Refresh cache if stale (non-blocking check; actual download is async-compatible)
    try:
        checker.refresh_if_stale()
    except Exception as exc:
        logger.warning("sanctions_refresh_failed", error=str(exc))

    result = checker.check(player_name)

    if result.is_match and result.best_match:
        m = result.best_match
        entry = m.entry
        return GateResult(
            gate=7,
            gate_name="sanctions_check",
            verdict=GateVerdict.BLOCK,
            reason_code=ReasonCode.SANCTIONS_MATCH,
            detail=(
                f"Sanctions match: '{player_name}' matched '{m.matched_name}' "
                f"(score {m.score}/100, program: {entry.program if entry else 'unknown'})"
            ),
            metadata={
                "query": player_name,
                "matched_name": m.matched_name,
                "score": m.score,
                "program": entry.program if entry else "",
                "uid": entry.uid if entry else "",
            },
            latency_ms=(time.perf_counter() - t0) * 1000,
        )

    # Near-miss: below threshold but above threshold-10
    if result.best_match and result.best_match.is_review_required:
        m = result.best_match
        return GateResult(
            gate=7,
            gate_name="sanctions_check",
            verdict=GateVerdict.REVIEW,
            detail=(
                f"Sanctions near-miss: '{player_name}' scored {m.score}/100 "
                f"against '{m.matched_name}' — manual review required"
            ),
            metadata={"score": m.score, "matched_name": m.matched_name},
            latency_ms=(time.perf_counter() - t0) * 1000,
        )

    return GateResult(
        gate=7,
        gate_name="sanctions_check",
        verdict=GateVerdict.PASS,
        detail=f"No sanctions match for '{player_name}'",
        latency_ms=(time.perf_counter() - t0) * 1000,
    )


def _gate8_kyc_status(ip: str, request: Request, player_id: str) -> GateResult:
    """
    Gate 8 — KYC Status Verification.

    Queries the internal KYC service to check whether the player has completed
    the required KYC lifecycle steps.  The KYC state machine has these states:

      PENDING → DOCUMENTS_REQUESTED → UNDER_REVIEW → APPROVED
                                                   → REJECTED (→ retry)
                                     → ENHANCED_DUE_DILIGENCE
      ANY STATE → SUSPENDED (compliance override)

    APPROVED and ENHANCED_DUE_DILIGENCE are the only states that allow access.
    SUSPENDED results in an immediate BLOCK.
    All others return REVIEW (player must complete KYC before accessing).
    """
    t0 = time.perf_counter()

    if not player_id:
        return GateResult(
            gate=8,
            gate_name="kyc_status",
            verdict=GateVerdict.PASS,
            detail="No player ID — KYC check skipped (unauthenticated request)",
            latency_ms=(time.perf_counter() - t0) * 1000,
        )

    # Check KYC status cache first (5 min TTL)
    r = get_redis()
    cache_key = f"kyc:status:{player_id}"
    cached = r.get(cache_key)
    if cached:
        kyc_status = cached.upper()
    else:
        kyc_status = _fetch_kyc_status(player_id)
        if kyc_status:
            r.setex(cache_key, 300, kyc_status)

    if not kyc_status:
        # KYC service unreachable — fail open with REVIEW
        return GateResult(
            gate=8,
            gate_name="kyc_status",
            verdict=GateVerdict.REVIEW,
            detail="KYC service unavailable — defaulting to REVIEW",
            latency_ms=(time.perf_counter() - t0) * 1000,
        )

    if kyc_status == "SUSPENDED":
        return GateResult(
            gate=8,
            gate_name="kyc_status",
            verdict=GateVerdict.BLOCK,
            reason_code=ReasonCode.KYC_SUSPENDED,
            detail=f"Player {player_id} KYC status is SUSPENDED (compliance hold)",
            metadata={"kyc_status": kyc_status},
            latency_ms=(time.perf_counter() - t0) * 1000,
        )

    if kyc_status in ("APPROVED", "ENHANCED_DUE_DILIGENCE"):
        return GateResult(
            gate=8,
            gate_name="kyc_status",
            verdict=GateVerdict.PASS,
            detail=f"KYC status: {kyc_status}",
            metadata={"kyc_status": kyc_status},
            latency_ms=(time.perf_counter() - t0) * 1000,
        )

    # All other states: PENDING, DOCUMENTS_REQUESTED, UNDER_REVIEW, REJECTED
    return GateResult(
        gate=8,
        gate_name="kyc_status",
        verdict=GateVerdict.REVIEW,
        reason_code=ReasonCode.KYC_REQUIRED,
        detail=f"KYC status {kyc_status} — player must complete verification",
        metadata={"kyc_status": kyc_status},
        latency_ms=(time.perf_counter() - t0) * 1000,
    )


def _fetch_kyc_status(player_id: str) -> str:
    """Call internal KYC service to retrieve player status. Returns status string or ''."""
    if not KYC_SERVICE_URL:
        return ""
    url = f"{KYC_SERVICE_URL.rstrip('/')}/api/v1/kyc/status/{player_id}"
    headers = {}
    if KYC_SERVICE_TOKEN:
        headers["Authorization"] = f"Bearer {KYC_SERVICE_TOKEN}"
    try:
        with httpx.Client(timeout=2.0) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                return (
                    data.get("status")
                    or data.get("kyc_status")
                    or data.get("data", {}).get("status")
                    or ""
                ).upper()
            if resp.status_code == 404:
                return "PENDING"  # New player, not yet in KYC system
    except Exception as exc:
        logger.warning("kyc_service_fetch_failed", player_id=player_id, error=str(exc))
    return ""


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

GATE_FUNCTIONS: list[Callable] = [
    _gate1_ip_type,
    _gate2_vpn_detection,
    _gate3_known_proxy,
    _gate4_ip_blacklist,
]

PLAYER_GATE_FUNCTIONS: list[Callable] = [
    _gate5_fraud_score,
    _gate6_device_fingerprint,
    _gate7_sanctions,
    _gate8_kyc_status,
]


def run_pipeline(
    ip: str,
    request: Request,
    player_id: str = "",
) -> PipelineResult:
    """
    Run all 8 gates sequentially.

    Gates 1-4 operate on IP-level data and run unconditionally.
    Gates 5-8 require a player_id and are skipped for unauthenticated requests.

    Short-circuits on first BLOCK verdict.
    Collects all REVIEW verdicts (request continues but is flagged).
    """
    gates: list[GateResult] = []
    review_flags: list[str] = []
    pipeline_start = time.perf_counter()

    # Gates 1-4: IP-level, always run
    for fn in GATE_FUNCTIONS:
        result = fn(ip, request)
        gates.append(result)
        _audit_log(ip, player_id, result, request)
        if result.verdict == GateVerdict.REVIEW:
            review_flags.append(f"{result.gate_name}:{result.detail[:80]}")
        if result.is_terminal():
            return PipelineResult(
                ip=ip,
                player_id=player_id,
                session_id=request.headers.get("x-session-id", ""),
                final_verdict=GateVerdict.BLOCK,
                blocking_gate=result,
                gates=gates,
                review_flags=review_flags,
                total_latency_ms=(time.perf_counter() - pipeline_start) * 1000,
            )

    # Gates 5-8: player-level, run when player_id is known
    if player_id:
        for fn in PLAYER_GATE_FUNCTIONS:
            result = fn(ip, request, player_id)
            gates.append(result)
            _audit_log(ip, player_id, result, request)
            if result.verdict == GateVerdict.REVIEW:
                review_flags.append(f"{result.gate_name}:{result.detail[:80]}")
            if result.is_terminal():
                return PipelineResult(
                    ip=ip,
                    player_id=player_id,
                    session_id=request.headers.get("x-session-id", ""),
                    final_verdict=GateVerdict.BLOCK,
                    blocking_gate=result,
                    gates=gates,
                    review_flags=review_flags,
                    total_latency_ms=(time.perf_counter() - pipeline_start) * 1000,
                )

    final = GateVerdict.REVIEW if review_flags else GateVerdict.PASS
    return PipelineResult(
        ip=ip,
        player_id=player_id,
        session_id=request.headers.get("x-session-id", ""),
        final_verdict=final,
        blocking_gate=None,
        gates=gates,
        review_flags=review_flags,
        total_latency_ms=(time.perf_counter() - pipeline_start) * 1000,
    )


def _audit_log(ip: str, player_id: str, gate_result: GateResult, request: Request) -> None:
    """Write a structured audit log entry for every gate decision."""
    logger.info(
        "gate_decision",
        gate=gate_result.gate,
        gate_name=gate_result.gate_name,
        verdict=gate_result.verdict,
        reason_code=gate_result.reason_code,
        ip=ip,
        player_id=player_id,
        latency_ms=round(gate_result.latency_ms, 2),
        detail=gate_result.detail,
        path=str(request.url.path),
        method=request.method,
        user_agent=request.headers.get("user-agent", "")[:100],
    )


# ---------------------------------------------------------------------------
# FastAPI application + middleware
# ---------------------------------------------------------------------------

app = FastAPI(
    title="IP Detection Pipeline",
    description="8-gate iGaming security pipeline (on-premises)",
    version="1.0.0",
)


def _get_real_ip(request: Request) -> str:
    """
    Resolve the client's real IP from X-Forwarded-For (nginx reverse proxy).

    Trusts only the leftmost IP when CF-Connecting-IP / X-Real-IP is absent.
    """
    # nginx sets X-Real-IP for single-hop proxies
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip

    # Cloudflare-style single header
    cf_ip = request.headers.get("cf-connecting-ip", "").strip()
    if cf_ip:
        return cf_ip

    # X-Forwarded-For: take leftmost (original client)
    xff = request.headers.get("x-forwarded-for", "").strip()
    if xff:
        return xff.split(",")[0].strip()

    # Direct connection
    if request.client:
        return request.client.host

    return "0.0.0.0"


@app.middleware("http")
async def ip_detection_middleware(request: Request, call_next: Callable) -> Response:
    """
    FastAPI HTTP middleware — runs the 8-gate pipeline on every request.

    Requests that BLOCK receive a 403 JSON response.
    Requests that REVIEW pass through with X-Security-Review headers.
    Requests that PASS continue unmodified.
    """
    # Skip health check endpoint
    if request.url.path in ("/health", "/healthz", "/ping"):
        return await call_next(request)

    ip = _get_real_ip(request)
    player_id = request.headers.get("x-player-id", "").strip()

    # Validate IP format before processing
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        logger.warning("invalid_ip_from_proxy", raw_ip=ip)
        ip = "0.0.0.0"

    pipeline = run_pipeline(ip=ip, request=request, player_id=player_id)

    if pipeline.final_verdict == GateVerdict.BLOCK:
        return JSONResponse(
            status_code=403,
            content=pipeline.to_403_body(),
            headers={
                "X-Pipeline-Latency": f"{pipeline.total_latency_ms:.1f}ms",
                "X-Blocked-Gate": pipeline.blocking_gate.gate_name if pipeline.blocking_gate else "",
            },
        )

    response = await call_next(request)

    # Append review flags to response headers
    # HTTP headers must be latin-1; encode to ASCII to strip non-ASCII characters safely
    if pipeline.review_flags:
        review_value = "; ".join(pipeline.review_flags)[:500]
        review_value = review_value.encode("ascii", errors="replace").decode("ascii")
        response.headers["X-Security-Review"] = review_value
    response.headers["X-Pipeline-Latency"] = f"{pipeline.total_latency_ms:.1f}ms"

    return response


# ---------------------------------------------------------------------------
# Admin / management endpoints
# ---------------------------------------------------------------------------

class BlacklistAddRequest(BaseModel):
    ip: str
    reason: str
    source: str = "manual"
    ttl_seconds: int = 0
    confidence_score: int = 100


class BlacklistRemoveRequest(BaseModel):
    ip: str


@app.get("/health")
async def health() -> dict:
    """Health check — verifies Redis connectivity."""
    try:
        get_redis().ping()
        redis_ok = True
    except Exception:
        redis_ok = False
    return {
        "status": "ok" if redis_ok else "degraded",
        "redis": "connected" if redis_ok else "unreachable",
        "env": PIPELINE_ENV,
    }


@app.post("/admin/blacklist/add")
async def blacklist_add(req: BlacklistAddRequest) -> dict:
    get_blacklist().add(
        ip=req.ip,
        reason=req.reason,
        source=req.source,
        ttl_seconds=req.ttl_seconds,
        confidence_score=req.confidence_score,
    )
    return {"status": "added", "ip": req.ip}


@app.post("/admin/blacklist/remove")
async def blacklist_remove(req: BlacklistRemoveRequest) -> dict:
    removed = get_blacklist().remove(req.ip)
    return {"status": "removed" if removed else "not_found", "ip": req.ip}


@app.get("/admin/blacklist/stats")
async def blacklist_stats() -> dict:
    return get_blacklist().stats()


@app.post("/admin/sanctions/refresh")
async def sanctions_refresh() -> dict:
    refreshed = get_sanctions().force_refresh()
    stats = get_sanctions().cache_stats()
    return {"refreshed": refreshed, **stats}


@app.get("/admin/sanctions/stats")
async def sanctions_stats() -> dict:
    return get_sanctions().cache_stats()


@app.get("/admin/pipeline/check")
async def pipeline_check(ip: str, request: Request, player_id: str = "") -> dict:
    """
    On-demand pipeline check for a specific IP + player.
    Useful for admin tools and incident investigation.
    """
    result = run_pipeline(ip=ip, request=request, player_id=player_id)
    return {
        "ip": result.ip,
        "player_id": result.player_id,
        "verdict": result.final_verdict,
        "review_flags": result.review_flags,
        "total_latency_ms": round(result.total_latency_ms, 2),
        "gates": [
            {
                "gate": g.gate,
                "name": g.gate_name,
                "verdict": g.verdict,
                "reason_code": g.reason_code,
                "detail": g.detail,
                "latency_ms": round(g.latency_ms, 2),
            }
            for g in result.gates
        ],
    }


# ---------------------------------------------------------------------------
# Entry point (uvicorn)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "ip_detection_pipeline:app",
        host="127.0.0.1",
        port=8000,
        workers=4,
        log_level="info",
        access_log=False,   # disable uvicorn access log — we use structlog
    )
