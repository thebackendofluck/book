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
gate_orchestrator.py — Unified 8-gate orchestrator across all three platforms.

Runs all 8 security gates against a given IP/player request, selecting the
best available data source for each gate:

  Priority order per data source:
    1. Cloudflare cf-headers (cf.asn, cf.botManagement, cf.isTor, …)
       — available when the request arrives through a CF Worker
    2. MaxMind GeoIP2 databases (GeoLite2-ASN.mmdb, GeoLite2-City.mmdb)
       — available when the mmdb files are present on-premises
    3. Redis-backed heuristics (on-premises)
       — always available as final fallback
    4. DynamoDB query (AWS path, optional)
       — used for sanctions / KYC when configured

Gates:
  1  IP Type Check      — Tor / Datacenter / Residential via ASN/CF
  2  VPN Detection      — CF flags → MaxMind ASN → Redis ASN heuristics
  3  Known Proxy Check  — CF org patterns → static DATACENTER_ASNS set
  4  IP Blacklist       — Redis ZSET (canonical) with CF KV as secondary read
  5  Fraud Score        — Velocity from Redis + CF bot score signal
  6  Device Fingerprint — JA3/UA anomaly via Redis
  7  Sanctions/PEP      — Redis-backed OFAC list
  8  KYC Status         — Internal KYC service (HTTP)

Each gate returns a GateResult.  The first BLOCK verdict terminates the chain.
The orchestrator returns a UnifiedPipelineResult with full observability metadata.

Environment variables:
  REDIS_URL, MAXMIND_DB_PATH, MAXMIND_CITY_DB_PATH,
  KYC_SERVICE_URL, KYC_SERVICE_TOKEN,
  FRAUD_SCORE_THRESHOLD, FRAUD_SCORE_REVIEW
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REDIS_URL             = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
MAXMIND_ASN_DB        = os.environ.get("MAXMIND_DB_PATH", "/var/lib/GeoIP/GeoLite2-ASN.mmdb")
MAXMIND_CITY_DB       = os.environ.get("MAXMIND_CITY_DB_PATH", "/var/lib/GeoIP/GeoLite2-City.mmdb")
KYC_SERVICE_URL       = os.environ.get("KYC_SERVICE_URL", "http://kyc-service:8080")
KYC_SERVICE_TOKEN     = os.environ.get("KYC_SERVICE_TOKEN", "")
FRAUD_SCORE_BLOCK     = int(os.environ.get("FRAUD_SCORE_THRESHOLD", "75"))
FRAUD_SCORE_REVIEW    = int(os.environ.get("FRAUD_SCORE_REVIEW", "50"))

# ---------------------------------------------------------------------------
# Datacenter / proxy ASN lists (matches onpremise/ip_detection_pipeline.py)
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
    9009,    # M247 Ltd (proxy provider)
    62567,   # DataPacket (residential proxy reseller)
    51167,   # Contabo GmbH
    47583,   # Hostinger International
    197540,  # netcup GmbH
    44477,   # Stark Industries Solutions (known proxy host)
    398705,  # Mullvad VPN
    209103,  # Surfshark VPN
})

PROXY_ORG_PATTERNS: tuple[str, ...] = (
    "vpn", "proxy", "anonymizer", "anonymiser",
    "tor project", "tor exit", "tor relay",
    "nordvpn", "expressvpn", "surfshark", "mullvad",
    "perfect privacy", "protonvpn", "hide.me", "cyberghost", "ipvanish",
)

# CF Bot Management detection IDs that indicate proxy/VPN
VPN_DETECTION_IDS: frozenset[int] = frozenset({33, 34, 82, 83})

# ---------------------------------------------------------------------------
# Unified data model
# ---------------------------------------------------------------------------

class GateVerdict(str, Enum):
    BLOCK  = "BLOCK"
    REVIEW = "REVIEW"
    PASS   = "PASS"


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
    PASS                = "PASS"


@dataclass
class CFHeaders:
    """
    Cloudflare cf-object fields available when the request passes through a CF Worker.
    Map to the TypeScript CFProps interface in cloudflare/src/types.ts.
    """
    asn: Optional[int] = None
    as_organization: Optional[str] = None
    country: Optional[str] = None
    city: Optional[str] = None
    is_tor: bool = False
    is_anonymous: bool = False
    is_anonymous_vpn: bool = False
    is_public_proxy: bool = False
    bot_score: Optional[int] = None         # 1–99; low = bot
    bot_verified: bool = False
    ja3_hash: Optional[str] = None
    bot_detection_ids: dict[int, int] = field(default_factory=dict)


@dataclass
class PlayerRequest:
    ip: str
    player_id: str = ""
    player_name: str = ""
    session_id: str = ""
    user_agent: str = ""
    accept_language: str = ""
    ja3_raw: str = ""
    requested_at: float = field(default_factory=time.time)
    # Optional Cloudflare headers (populated when CF is in the path)
    cf: Optional[CFHeaders] = None


@dataclass
class GateResult:
    gate: int
    gate_name: str
    verdict: GateVerdict
    reason_code: ReasonCode = ReasonCode.PASS
    detail: str = ""
    data_source: str = ""     # which source provided the signal
    latency_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_terminal(self) -> bool:
        return self.verdict == GateVerdict.BLOCK


@dataclass
class UnifiedPipelineResult:
    ip: str
    player_id: str
    session_id: str
    final_verdict: GateVerdict
    blocking_gate: Optional[GateResult]
    gates: list[GateResult]
    review_flags: list[str]
    total_latency_ms: float
    timestamp: float = field(default_factory=time.time)
    data_sources_used: list[str] = field(default_factory=list)

    def to_403_body(self) -> dict[str, Any]:
        assert self.blocking_gate is not None
        return {
            "error": "access_denied",
            "reason": self.blocking_gate.reason_code.value,
            "detail": self.blocking_gate.detail,
            "gate": self.blocking_gate.gate_name,
            "request_id": f"{self.player_id}:{self.session_id}:{int(self.timestamp)}",
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "ip": self.ip,
            "player_id": self.player_id,
            "session_id": self.session_id,
            "final_verdict": self.final_verdict.value,
            "blocking_gate": _gate_to_dict(self.blocking_gate) if self.blocking_gate else None,
            "gates": [_gate_to_dict(g) for g in self.gates],
            "review_flags": self.review_flags,
            "total_latency_ms": self.total_latency_ms,
            "timestamp": self.timestamp,
            "data_sources_used": self.data_sources_used,
        }


def _gate_to_dict(g: GateResult) -> dict[str, Any]:
    return {
        "gate": g.gate,
        "gate_name": g.gate_name,
        "verdict": g.verdict.value,
        "reason_code": g.reason_code.value,
        "detail": g.detail,
        "data_source": g.data_source,
        "latency_ms": g.latency_ms,
    }


# ---------------------------------------------------------------------------
# Lazy resource handles
# ---------------------------------------------------------------------------

_redis_client = None
_asn_reader = None
_city_reader = None


def _get_redis():
    global _redis_client
    if _redis_client is None:
        import redis
        _redis_client = redis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
            retry_on_timeout=True,
        )
    return _redis_client


def _get_asn_reader():
    global _asn_reader
    if _asn_reader is None and os.path.isfile(MAXMIND_ASN_DB):
        import geoip2.database
        _asn_reader = geoip2.database.Reader(MAXMIND_ASN_DB)
    return _asn_reader


def _get_city_reader():
    global _city_reader
    if _city_reader is None and os.path.isfile(MAXMIND_CITY_DB):
        import geoip2.database
        _city_reader = geoip2.database.Reader(MAXMIND_CITY_DB)
    return _city_reader


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_proxy_org(org: str) -> bool:
    org_lower = org.lower()
    return any(pattern in org_lower for pattern in PROXY_ORG_PATTERNS)


def _get_maxmind_asn(ip: str) -> tuple[Optional[int], Optional[str]]:
    """Return (asn, org) from MaxMind ASN DB, or (None, None) on any error."""
    reader = _get_asn_reader()
    if reader is None:
        return None, None
    try:
        resp = reader.asn(ip)
        return resp.autonomous_system_number, resp.autonomous_system_organization
    except Exception:  # noqa: BLE001
        return None, None


def _get_maxmind_country(ip: str) -> Optional[str]:
    """Return ISO country code from MaxMind City DB, or None on any error."""
    reader = _get_city_reader()
    if reader is None:
        return None
    try:
        resp = reader.city(ip)
        return resp.country.iso_code
    except Exception:  # noqa: BLE001
        return None


def _redis_check_blacklist(ip: str) -> tuple[bool, str]:
    """Return (is_blocked, reason) from Redis blacklist."""
    try:
        r = _get_redis()
        score = r.zscore("ip_blacklist:entries", ip)
        if score is None:
            return False, ""
        # Lazy expiry
        if score > 0 and time.time() > score:
            return False, ""
        raw = r.get(f"ip_blacklist:meta:{ip}")
        reason = ""
        if raw:
            try:
                data = json.loads(raw)
                reason = data.get("reason", "")
            except (json.JSONDecodeError, TypeError):
                pass
        return True, reason
    except Exception as exc:  # noqa: BLE001
        logger.warning("redis.blacklist_check failed ip=%s: %s", ip, exc)
        return False, ""


def _redis_get_velocity(ip: str) -> tuple[int, int, int]:
    """Return (count_1m, count_5m, count_1h) from Redis velocity counters."""
    try:
        r = _get_redis()
        pipe = r.pipeline(transaction=False)
        pipe.get(f"vel:{ip}:1m")
        pipe.get(f"vel:{ip}:5m")
        pipe.get(f"vel:{ip}:1h")
        results = pipe.execute()
        return (
            int(results[0] or 0),
            int(results[1] or 0),
            int(results[2] or 0),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("redis.velocity_check failed ip=%s: %s", ip, exc)
        return 0, 0, 0


def _redis_increment_velocity(ip: str) -> None:
    try:
        r = _get_redis()
        pipe = r.pipeline(transaction=False)
        pipe.incr(f"vel:{ip}:1m")
        pipe.expire(f"vel:{ip}:1m", 60)
        pipe.incr(f"vel:{ip}:5m")
        pipe.expire(f"vel:{ip}:5m", 300)
        pipe.incr(f"vel:{ip}:1h")
        pipe.expire(f"vel:{ip}:1h", 3600)
        pipe.execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("redis.velocity_increment failed ip=%s: %s", ip, exc)


def _redis_check_ja3(ip: str, ja3_hash: str, limit: int = 3) -> bool:
    """Return True if this IP has seen more than `limit` distinct JA3 hashes in 1h."""
    if not ja3_hash:
        return False
    try:
        r = _get_redis()
        key = f"ja3:{ip}"
        r.sadd(key, ja3_hash)
        r.expire(key, 3600)
        count = r.scard(key)
        return int(count) > limit
    except Exception as exc:  # noqa: BLE001
        logger.warning("redis.ja3_check failed ip=%s: %s", ip, exc)
        return False


def _redis_check_sanctions(player_name: str) -> bool:
    """Simple token-based sanctions lookup in Redis."""
    if not player_name:
        return False
    try:
        r = _get_redis()
        # Tokenise and check each word
        tokens = player_name.lower().split()
        pipe = r.pipeline(transaction=False)
        for token in tokens:
            pipe.exists(f"sanctions:name:{token}")
        results = pipe.execute()
        return any(bool(r) for r in results)
    except Exception as exc:  # noqa: BLE001
        logger.warning("redis.sanctions_check failed: %s", exc)
        return False


def _http_kyc_check(player_id: str) -> Optional[str]:
    """
    Query the KYC service.
    Returns "BLOCKED", "PENDING", "APPROVED", or None on error.
    """
    if not player_id or not KYC_SERVICE_URL:
        return None
    try:
        import httpx
        resp = httpx.get(
            f"{KYC_SERVICE_URL}/kyc/status/{player_id}",
            headers={"Authorization": f"Bearer {KYC_SERVICE_TOKEN}"},
            timeout=2.0,
        )
        if resp.status_code == 200:
            return resp.json().get("status")
        if resp.status_code == 404:
            return "NOT_FOUND"
    except Exception as exc:  # noqa: BLE001
        logger.warning("kyc_check failed player_id=%s: %s", player_id, exc)
    return None


# ---------------------------------------------------------------------------
# The 8 gates
# ---------------------------------------------------------------------------

def _gate1_ip_type(req: PlayerRequest) -> GateResult:
    """Gate 1 — IP Type Check: Tor / Datacenter / Residential."""
    t0 = time.perf_counter()

    # Path 1: Cloudflare isTor flag
    if req.cf and req.cf.is_tor:
        return GateResult(
            gate=1, gate_name="ip_type",
            verdict=GateVerdict.BLOCK, reason_code=ReasonCode.BANNED_PROXY_TOR,
            detail=f"CF isTor flag set for ip={req.ip}",
            data_source="cloudflare_cf",
            latency_ms=round((time.perf_counter() - t0) * 1000, 2),
        )

    # Path 2: Cloudflare ASN in datacenter set
    if req.cf and req.cf.asn is not None:
        asn = req.cf.asn
        if asn in DATACENTER_ASNS:
            return GateResult(
                gate=1, gate_name="ip_type",
                verdict=GateVerdict.BLOCK, reason_code=ReasonCode.BANNED_PROXY_DC,
                detail=f"CF ASN {asn} ({req.cf.as_organization}) is datacenter for ip={req.ip}",
                data_source="cloudflare_cf",
                latency_ms=round((time.perf_counter() - t0) * 1000, 2),
            )

    # Path 3: MaxMind ASN DB
    asn_mm, org_mm = _get_maxmind_asn(req.ip)
    if asn_mm is not None:
        if asn_mm in DATACENTER_ASNS:
            return GateResult(
                gate=1, gate_name="ip_type",
                verdict=GateVerdict.BLOCK, reason_code=ReasonCode.BANNED_PROXY_DC,
                detail=f"MaxMind ASN {asn_mm} ({org_mm}) is datacenter for ip={req.ip}",
                data_source="maxmind_asn",
                latency_ms=round((time.perf_counter() - t0) * 1000, 2),
            )

    return GateResult(
        gate=1, gate_name="ip_type",
        verdict=GateVerdict.PASS, reason_code=ReasonCode.PASS,
        data_source="cloudflare_cf" if req.cf else "maxmind_asn" if asn_mm else "none",
        latency_ms=round((time.perf_counter() - t0) * 1000, 2),
    )


def _gate2_vpn(req: PlayerRequest) -> GateResult:
    """Gate 2 — VPN Detection."""
    t0 = time.perf_counter()

    if req.cf:
        if req.cf.is_anonymous_vpn:
            return GateResult(
                gate=2, gate_name="vpn_detection",
                verdict=GateVerdict.BLOCK, reason_code=ReasonCode.BANNED_PROXY_VPN,
                detail=f"CF isAnonymousVpn flag set for ip={req.ip}",
                data_source="cloudflare_cf",
                latency_ms=round((time.perf_counter() - t0) * 1000, 2),
            )
        if req.cf.is_anonymous:
            return GateResult(
                gate=2, gate_name="vpn_detection",
                verdict=GateVerdict.BLOCK, reason_code=ReasonCode.BANNED_PROXY_VPN,
                detail=f"CF isAnonymous flag set for ip={req.ip}",
                data_source="cloudflare_cf",
                latency_ms=round((time.perf_counter() - t0) * 1000, 2),
            )
        if req.cf.is_public_proxy:
            return GateResult(
                gate=2, gate_name="vpn_detection",
                verdict=GateVerdict.BLOCK, reason_code=ReasonCode.BANNED_PROXY_VPN,
                detail=f"CF isPublicProxy flag set for ip={req.ip}",
                data_source="cloudflare_cf",
                latency_ms=round((time.perf_counter() - t0) * 1000, 2),
            )
        for det_id in req.cf.bot_detection_ids:
            if det_id in VPN_DETECTION_IDS:
                return GateResult(
                    gate=2, gate_name="vpn_detection",
                    verdict=GateVerdict.BLOCK, reason_code=ReasonCode.BANNED_PROXY_VPN,
                    detail=f"CF BotMgmt detection ID {det_id} matched for ip={req.ip}",
                    data_source="cloudflare_bot_management",
                    latency_ms=round((time.perf_counter() - t0) * 1000, 2),
                )

    # Fallback: MaxMind org-name heuristics
    _, org = _get_maxmind_asn(req.ip)
    if org and _is_proxy_org(org):
        return GateResult(
            gate=2, gate_name="vpn_detection",
            verdict=GateVerdict.BLOCK, reason_code=ReasonCode.BANNED_PROXY_VPN,
            detail=f"MaxMind org '{org}' matches VPN/proxy pattern for ip={req.ip}",
            data_source="maxmind_asn",
            latency_ms=round((time.perf_counter() - t0) * 1000, 2),
        )

    return GateResult(
        gate=2, gate_name="vpn_detection",
        verdict=GateVerdict.PASS, reason_code=ReasonCode.PASS,
        data_source="cloudflare_cf" if req.cf else "maxmind_asn",
        latency_ms=round((time.perf_counter() - t0) * 1000, 2),
    )


def _gate3_known_proxy(req: PlayerRequest) -> GateResult:
    """Gate 3 — Known Proxy / hosting provider check."""
    t0 = time.perf_counter()

    asn: Optional[int] = None
    org: Optional[str] = None

    if req.cf:
        asn = req.cf.asn
        org = req.cf.as_organization
    else:
        asn, org = _get_maxmind_asn(req.ip)

    if asn and asn in DATACENTER_ASNS:
        return GateResult(
            gate=3, gate_name="known_proxy",
            verdict=GateVerdict.BLOCK, reason_code=ReasonCode.BANNED_PROXY_KNOWN,
            detail=f"Known proxy/hosting ASN {asn} ({org}) for ip={req.ip}",
            data_source="cloudflare_cf" if req.cf else "maxmind_asn",
            latency_ms=round((time.perf_counter() - t0) * 1000, 2),
        )

    if org and _is_proxy_org(org):
        return GateResult(
            gate=3, gate_name="known_proxy",
            verdict=GateVerdict.BLOCK, reason_code=ReasonCode.BANNED_PROXY_KNOWN,
            detail=f"Known proxy org pattern '{org}' for ip={req.ip}",
            data_source="cloudflare_cf" if req.cf else "maxmind_asn",
            latency_ms=round((time.perf_counter() - t0) * 1000, 2),
        )

    return GateResult(
        gate=3, gate_name="known_proxy",
        verdict=GateVerdict.PASS, reason_code=ReasonCode.PASS,
        data_source="cloudflare_cf" if req.cf else "maxmind_asn",
        latency_ms=round((time.perf_counter() - t0) * 1000, 2),
    )


def _gate4_blacklist(req: PlayerRequest) -> GateResult:
    """Gate 4 — IP Blacklist (Redis canonical, with platform metadata)."""
    t0 = time.perf_counter()
    is_blocked, reason = _redis_check_blacklist(req.ip)

    if is_blocked:
        return GateResult(
            gate=4, gate_name="ip_blacklist",
            verdict=GateVerdict.BLOCK, reason_code=ReasonCode.BANNED_IP_BLACKLIST,
            detail=f"IP {req.ip} on Redis blacklist: {reason}",
            data_source="redis",
            latency_ms=round((time.perf_counter() - t0) * 1000, 2),
        )

    return GateResult(
        gate=4, gate_name="ip_blacklist",
        verdict=GateVerdict.PASS, reason_code=ReasonCode.PASS,
        data_source="redis",
        latency_ms=round((time.perf_counter() - t0) * 1000, 2),
    )


def _gate5_fraud_score(req: PlayerRequest) -> GateResult:
    """Gate 5 — Fraud Score (velocity + CF bot score + geo/UA heuristics)."""
    t0 = time.perf_counter()

    count_1m, count_5m, count_1h = _redis_get_velocity(req.ip)

    # Velocity signals (weighted, matching the CF Worker scoring)
    RATE_LIMIT_1M = 60
    score = 0
    score += min(count_1m / RATE_LIMIT_1M, 1.0) * 20  # rate1m weight=20
    score += min(count_5m / (RATE_LIMIT_1M * 3), 1.0) * 15
    score += min(count_1h / (RATE_LIMIT_1M * 30), 1.0) * 10

    data_sources = ["redis_velocity"]

    # CF bot score signal
    if req.cf and req.cf.bot_score is not None:
        bot_score = req.cf.bot_score
        if bot_score < 99:
            inverted = (99 - bot_score) / 98
            score += inverted * 20
        data_sources.append("cloudflare_bot_management")

    # User-agent signals
    BAD_UA_FRAGMENTS = (
        "python-requests", "go-http-client", "axios/", "node-fetch",
        "curl/", "wget/", "libwww", "scrapy", "phantomjs",
        "headlesschrome", "selenium", "puppeteer", "playwright",
    )
    ua = (req.user_agent or "").lower()
    if not ua:
        score += 10
    elif any(f in ua for f in BAD_UA_FRAGMENTS):
        score += 10

    score = min(score, 100)
    _redis_increment_velocity(req.ip)

    if score >= FRAUD_SCORE_BLOCK:
        return GateResult(
            gate=5, gate_name="fraud_score",
            verdict=GateVerdict.BLOCK, reason_code=ReasonCode.HIGH_FRAUD_SCORE,
            detail=f"Fraud score {score:.1f} >= block threshold {FRAUD_SCORE_BLOCK}",
            data_source=",".join(data_sources),
            latency_ms=round((time.perf_counter() - t0) * 1000, 2),
            metadata={"score": round(score, 1), "count_1m": count_1m, "count_1h": count_1h},
        )

    if score >= FRAUD_SCORE_REVIEW:
        return GateResult(
            gate=5, gate_name="fraud_score",
            verdict=GateVerdict.REVIEW, reason_code=ReasonCode.HIGH_FRAUD_SCORE,
            detail=f"Fraud score {score:.1f} >= review threshold {FRAUD_SCORE_REVIEW}",
            data_source=",".join(data_sources),
            latency_ms=round((time.perf_counter() - t0) * 1000, 2),
            metadata={"score": round(score, 1)},
        )

    return GateResult(
        gate=5, gate_name="fraud_score",
        verdict=GateVerdict.PASS, reason_code=ReasonCode.PASS,
        data_source=",".join(data_sources),
        latency_ms=round((time.perf_counter() - t0) * 1000, 2),
        metadata={"score": round(score, 1)},
    )


def _gate6_device_fingerprint(req: PlayerRequest) -> GateResult:
    """Gate 6 — Device Fingerprint anomaly (JA3 distinct count per IP)."""
    t0 = time.perf_counter()

    ja3: Optional[str] = req.ja3_raw
    if req.cf and req.cf.ja3_hash:
        ja3 = req.cf.ja3_hash

    if not ja3:
        return GateResult(
            gate=6, gate_name="device_fingerprint",
            verdict=GateVerdict.PASS, reason_code=ReasonCode.PASS,
            detail="No JA3 available; skipping fingerprint gate",
            data_source="none",
            latency_ms=round((time.perf_counter() - t0) * 1000, 2),
        )

    anomaly = _redis_check_ja3(req.ip, ja3, limit=3)
    if anomaly:
        return GateResult(
            gate=6, gate_name="device_fingerprint",
            verdict=GateVerdict.REVIEW, reason_code=ReasonCode.DEVICE_ANOMALY,
            detail=f"IP {req.ip} has >3 distinct JA3 hashes in 1h window",
            data_source="redis",
            latency_ms=round((time.perf_counter() - t0) * 1000, 2),
        )

    return GateResult(
        gate=6, gate_name="device_fingerprint",
        verdict=GateVerdict.PASS, reason_code=ReasonCode.PASS,
        data_source="redis",
        latency_ms=round((time.perf_counter() - t0) * 1000, 2),
    )


def _gate7_sanctions(req: PlayerRequest) -> GateResult:
    """Gate 7 — Sanctions/PEP check (Redis-backed OFAC list)."""
    t0 = time.perf_counter()

    if not req.player_name:
        return GateResult(
            gate=7, gate_name="sanctions",
            verdict=GateVerdict.PASS, reason_code=ReasonCode.PASS,
            detail="No player name provided; sanctions gate skipped",
            data_source="none",
            latency_ms=round((time.perf_counter() - t0) * 1000, 2),
        )

    hit = _redis_check_sanctions(req.player_name)
    if hit:
        return GateResult(
            gate=7, gate_name="sanctions",
            verdict=GateVerdict.BLOCK, reason_code=ReasonCode.SANCTIONS_MATCH,
            detail=f"Player name '{req.player_name}' matched sanctions list",
            data_source="redis",
            latency_ms=round((time.perf_counter() - t0) * 1000, 2),
        )

    return GateResult(
        gate=7, gate_name="sanctions",
        verdict=GateVerdict.PASS, reason_code=ReasonCode.PASS,
        data_source="redis",
        latency_ms=round((time.perf_counter() - t0) * 1000, 2),
    )


def _gate8_kyc(req: PlayerRequest) -> GateResult:
    """Gate 8 — KYC Status (internal service HTTP call)."""
    t0 = time.perf_counter()

    if not req.player_id:
        return GateResult(
            gate=8, gate_name="kyc_status",
            verdict=GateVerdict.PASS, reason_code=ReasonCode.PASS,
            detail="No player_id; KYC gate skipped",
            data_source="none",
            latency_ms=round((time.perf_counter() - t0) * 1000, 2),
        )

    status = _http_kyc_check(req.player_id)

    if status == "BLOCKED" or status == "SUSPENDED":
        return GateResult(
            gate=8, gate_name="kyc_status",
            verdict=GateVerdict.BLOCK, reason_code=ReasonCode.KYC_SUSPENDED,
            detail=f"KYC status={status} for player_id={req.player_id}",
            data_source="kyc_service",
            latency_ms=round((time.perf_counter() - t0) * 1000, 2),
        )

    if status in ("PENDING", "NOT_FOUND", None):
        return GateResult(
            gate=8, gate_name="kyc_status",
            verdict=GateVerdict.REVIEW, reason_code=ReasonCode.KYC_REQUIRED,
            detail=f"KYC status={status} for player_id={req.player_id}",
            data_source="kyc_service",
            latency_ms=round((time.perf_counter() - t0) * 1000, 2),
        )

    return GateResult(
        gate=8, gate_name="kyc_status",
        verdict=GateVerdict.PASS, reason_code=ReasonCode.PASS,
        data_source="kyc_service",
        latency_ms=round((time.perf_counter() - t0) * 1000, 2),
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

_GATE_FUNCTIONS = [
    _gate1_ip_type,
    _gate2_vpn,
    _gate3_known_proxy,
    _gate4_blacklist,
    _gate5_fraud_score,
    _gate6_device_fingerprint,
    _gate7_sanctions,
    _gate8_kyc,
]


def run_gates(req: PlayerRequest) -> UnifiedPipelineResult:
    """
    Run all 8 gates sequentially.  Stop at the first BLOCK verdict.
    REVIEW verdicts accumulate in review_flags but do not terminate the chain.

    Returns a UnifiedPipelineResult with full observability metadata including
    which data source (CF headers, MaxMind, Redis) provided each signal.
    """
    pipeline_start = time.perf_counter()
    gates_run: list[GateResult] = []
    review_flags: list[str] = []
    blocking_gate: Optional[GateResult] = None
    data_sources_seen: set[str] = set()

    for gate_fn in _GATE_FUNCTIONS:
        result = gate_fn(req)
        gates_run.append(result)

        if result.data_source:
            for src in result.data_source.split(","):
                data_sources_seen.add(src.strip())

        if result.verdict == GateVerdict.REVIEW:
            review_flags.append(result.reason_code.value)
            continue

        if result.is_terminal():
            blocking_gate = result
            break

    total_ms = round((time.perf_counter() - pipeline_start) * 1000, 2)

    if blocking_gate:
        final_verdict = GateVerdict.BLOCK
    elif review_flags:
        final_verdict = GateVerdict.REVIEW
    else:
        final_verdict = GateVerdict.PASS

    return UnifiedPipelineResult(
        ip=req.ip,
        player_id=req.player_id,
        session_id=req.session_id,
        final_verdict=final_verdict,
        blocking_gate=blocking_gate,
        gates=gates_run,
        review_flags=review_flags,
        total_latency_ms=total_ms,
        data_sources_used=sorted(data_sources_seen - {"none", ""}),
    )
