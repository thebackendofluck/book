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
lambda_ip_gate.py
-----------------
AWS Lambda handler implementing the 8-gate iGaming IP detection pipeline.

Entry point: API Gateway (HTTP API v2) → Lambda (Python 3.12)

Gate execution order:
  1. IP Type Check    – ASN + MaxMind GeoIP2 (Tor / Datacenter / Residential)
  2. VPN Detection    – IP reputation database
  3. Known Proxy      – Proxy DB lookup
  4. IP Blacklist     – DynamoDB ban database
  5. Fraud Score      – Real-time multi-signal scoring
  6. Device Fingerprint Anomaly – DynamoDB fingerprint history
  7. Sanctions / PEP  – OFAC SDN list (S3) + fuzzy matching
  8. KYC Status       – DynamoDB KYC state table

Each gate returns one of: BLOCK(403) / REVIEW(202) / PASS(200)

Reason codes on block:
  BANNED_PROXY_TOR        – Tor exit node
  BANNED_PROXY_DC         – Datacenter / hosting ASN
  BANNED_PROXY_VPN        – Commercial VPN
  BANNED_PROXY_KNOWN      – Known proxy in reputation DB
  BANNED_IP_BLACKLIST     – Manually or automatically blacklisted IP
  HIGH_FRAUD_SCORE        – Composite fraud score above threshold
  DEVICE_ANOMALY          – Device fingerprint inconsistency detected
  SANCTIONS_MATCH         – OFAC SDN / PEP fuzzy-name match
  KYC_FAILED              – KYC not completed or rejected
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any

import boto3
from botocore.exceptions import ClientError

# ---------------------------------------------------------------------------
# Sub-module imports (same package)
# ---------------------------------------------------------------------------
from dynamodb_blacklist import IPBlacklistService  # ty:ignore[unresolved-import]
from device_fingerprint_dynamo import DeviceFingerprintService, FingerprintAnomaly  # ty:ignore[unresolved-import]
from s3_sanctions_checker import SanctionsChecker  # ty:ignore[unresolved-import]
from waf_integration import WAFIntegration  # ty:ignore[unresolved-import]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Environment configuration (set via Lambda environment variables / SSM)
# ---------------------------------------------------------------------------
REGION = os.environ.get("AWS_REGION", "us-east-1")
MAXMIND_DB_BUCKET = os.environ.get("MAXMIND_DB_BUCKET", "igaming-geoip-databases")
MAXMIND_ASN_KEY = os.environ.get("MAXMIND_ASN_KEY", "GeoLite2-ASN.mmdb")
MAXMIND_CITY_KEY = os.environ.get("MAXMIND_CITY_KEY", "GeoLite2-City.mmdb")
IP_REPUTATION_API = os.environ.get("IP_REPUTATION_API", "")
IP_REPUTATION_KEY = os.environ.get("IP_REPUTATION_API_KEY", "")
FRAUD_SCORE_THRESHOLD = float(os.environ.get("FRAUD_SCORE_THRESHOLD", "75.0"))
FRAUD_REVIEW_THRESHOLD = float(os.environ.get("FRAUD_REVIEW_THRESHOLD", "50.0"))
BLACKLIST_TABLE = os.environ.get("BLACKLIST_TABLE", "ip-blacklist")
DEVICE_FP_TABLE = os.environ.get("DEVICE_FP_TABLE", "device-fingerprints")
KYC_TABLE = os.environ.get("KYC_TABLE", "kyc-status")
SDN_BUCKET = os.environ.get("SDN_BUCKET", "igaming-sanctions")
SDN_KEY = os.environ.get("SDN_KEY", "ofac/sdn_advanced.xml")
WAF_IP_SET_ID = os.environ.get("WAF_IP_SET_ID", "")
WAF_IP_SET_NAME = os.environ.get("WAF_IP_SET_NAME", "igaming-blocked-ips")
WAF_IP_SET_SCOPE = os.environ.get("WAF_IP_SET_SCOPE", "REGIONAL")
SNS_ALERT_TOPIC = os.environ.get("SNS_ALERT_TOPIC", "")
ELASTICACHE_ENDPOINT = os.environ.get("ELASTICACHE_ENDPOINT", "")
VELOCITY_WINDOW_SECONDS = int(os.environ.get("VELOCITY_WINDOW_SECONDS", "300"))

# Datacenter ASNs that trigger automatic block
DATACENTER_ASNS: set[int] = {
    14061,   # DigitalOcean
    16509,   # Amazon AWS
    15169,   # Google Cloud
    8075,    # Microsoft Azure
    20473,   # Vultr
    63949,   # Linode / Akamai
    24940,   # Hetzner
    16276,   # OVH
    13335,   # Cloudflare
    36352,   # ColoCrossing
    40676,   # Psychz Networks
    55286,   # B2 Net Solutions
    22612,   # Namecheap
    32400,   # Colocrossing
    9009,    # M247
    51167,   # Contabo
}

# ---------------------------------------------------------------------------
# Domain objects
# ---------------------------------------------------------------------------

class GateVerdict(str, Enum):
    PASS = "PASS"
    REVIEW = "REVIEW"
    BLOCK = "BLOCK"


class ReasonCode(str, Enum):
    BANNED_PROXY_TOR = "BANNED_PROXY_TOR"
    BANNED_PROXY_DC = "BANNED_PROXY_DC"
    BANNED_PROXY_VPN = "BANNED_PROXY_VPN"
    BANNED_PROXY_KNOWN = "BANNED_PROXY_KNOWN"
    BANNED_IP_BLACKLIST = "BANNED_IP_BLACKLIST"
    HIGH_FRAUD_SCORE = "HIGH_FRAUD_SCORE"
    DEVICE_ANOMALY = "DEVICE_ANOMALY"
    SANCTIONS_MATCH = "SANCTIONS_MATCH"
    KYC_FAILED = "KYC_FAILED"


@dataclass
class GateResult:
    gate_id: int
    gate_name: str
    verdict: GateVerdict
    reason_code: str | None = None
    detail: str = ""
    latency_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineResult:
    request_id: str
    ip_address: str
    user_id: str | None
    session_id: str | None
    final_verdict: GateVerdict
    blocking_gate: int | None
    reason_code: str | None
    gates: list[GateResult] = field(default_factory=list)
    total_latency_ms: float = 0.0
    timestamp: int = field(default_factory=lambda: int(time.time()))

    def to_response(self) -> dict[str, Any]:
        http_status = {
            GateVerdict.PASS: 200,
            GateVerdict.REVIEW: 202,
            GateVerdict.BLOCK: 403,
        }[self.final_verdict]
        return {
            "statusCode": http_status,
            "body": json.dumps({
                "request_id": self.request_id,
                "verdict": self.final_verdict.value,
                "reason_code": self.reason_code,
                "blocking_gate": self.blocking_gate,
                "gates": [asdict(g) for g in self.gates],
                "total_latency_ms": round(self.total_latency_ms, 2),
                "timestamp": self.timestamp,
            }),
            "headers": {
                "Content-Type": "application/json",
                "X-Request-ID": self.request_id,
                "X-Verdict": self.final_verdict.value,
            },
        }


# ---------------------------------------------------------------------------
# AWS clients (module-level for Lambda container reuse)
# ---------------------------------------------------------------------------

_dynamodb = boto3.resource("dynamodb", region_name=REGION)
_s3 = boto3.client("s3", region_name=REGION)
_sns = boto3.client("sns", region_name=REGION) if SNS_ALERT_TOPIC else None
_ssm = boto3.client("ssm", region_name=REGION)

# Lazy-init service objects reused across warm invocations
_blacklist_svc: IPBlacklistService | None = None
_device_svc: DeviceFingerprintService | None = None
_sanctions_svc: SanctionsChecker | None = None
_waf_svc: WAFIntegration | None = None

# MaxMind reader objects (loaded from S3 into /tmp on cold start)
_asn_reader: Any = None
_city_reader: Any = None


# ---------------------------------------------------------------------------
# Cold-start initialisation
# ---------------------------------------------------------------------------

def _init_services() -> None:
    """Initialise all service objects once per Lambda container lifetime."""
    global _blacklist_svc, _device_svc, _sanctions_svc, _waf_svc
    global _asn_reader, _city_reader

    if _blacklist_svc is None:
        _blacklist_svc = IPBlacklistService(table_name=BLACKLIST_TABLE, region=REGION)

    if _device_svc is None:
        _device_svc = DeviceFingerprintService(table_name=DEVICE_FP_TABLE, region=REGION)

    if _sanctions_svc is None:
        _sanctions_svc = SanctionsChecker(
            bucket=SDN_BUCKET,
            key=SDN_KEY,
            region=REGION,
        )
        _sanctions_svc.ensure_loaded()

    if _waf_svc is None and WAF_IP_SET_ID:
        _waf_svc = WAFIntegration(
            ip_set_id=WAF_IP_SET_ID,
            ip_set_name=WAF_IP_SET_NAME,
            scope=WAF_IP_SET_SCOPE,
            region=REGION,
        )

    if _asn_reader is None:
        _asn_reader = _load_maxmind_db(MAXMIND_DB_BUCKET, MAXMIND_ASN_KEY, "/tmp/GeoLite2-ASN.mmdb")

    if _city_reader is None:
        _city_reader = _load_maxmind_db(MAXMIND_DB_BUCKET, MAXMIND_CITY_KEY, "/tmp/GeoLite2-City.mmdb")


def _load_maxmind_db(bucket: str, key: str, local_path: str) -> Any:
    """Download a MaxMind MMDB from S3 and open a reader. Returns None on failure."""
    try:
        import geoip2.database  # type: ignore
        import os as _os

        if not _os.path.exists(local_path):
            logger.info("Downloading MaxMind DB s3://%s/%s → %s", bucket, key, local_path)
            _s3.download_file(bucket, key, local_path)

        return geoip2.database.Reader(local_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("MaxMind DB unavailable (%s/%s): %s", bucket, key, exc)
        return None


# ---------------------------------------------------------------------------
# Request parsing
# ---------------------------------------------------------------------------

def _parse_request(event: dict[str, Any]) -> tuple[str, str | None, str | None, dict[str, Any]]:
    """
    Extract ip_address, user_id, session_id, and fingerprint_data from
    an API Gateway HTTP API v2 event.
    """
    # IP address: prefer X-Forwarded-For header (first hop = real client)
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    xff = headers.get("x-forwarded-for", "")
    if xff:
        ip_address = xff.split(",")[0].strip()
    else:
        ip_address = (
            (event.get("requestContext") or {})
            .get("http", {})
            .get("sourceIp", "0.0.0.0")
        )

    # Parse body
    body: dict[str, Any] = {}
    raw_body = event.get("body", "")
    if raw_body:
        try:
            body = json.loads(raw_body) if isinstance(raw_body, str) else raw_body
        except (json.JSONDecodeError, TypeError):
            body = {}

    user_id = body.get("user_id") or headers.get("x-user-id")
    session_id = body.get("session_id") or headers.get("x-session-id")
    fingerprint_data = body.get("fingerprint", {})

    return ip_address, user_id, session_id, fingerprint_data


# ---------------------------------------------------------------------------
# Gate 1: IP Type Check
# ---------------------------------------------------------------------------

def _gate_ip_type(ip: str) -> GateResult:
    """
    Determine IP category using MaxMind ASN database.
    Categories: Tor exit, Datacenter ASN, Residential.
    """
    t0 = time.perf_counter()
    meta: dict[str, Any] = {}

    try:
        # Fast tor-exit check via known tor exit node ranges
        # In production, maintain a Redis SET updated by a scheduled job
        # that fetches https://check.torproject.org/torbulkexitlist
        is_tor = _check_tor_exit(ip)
        if is_tor:
            return GateResult(
                gate_id=1,
                gate_name="ip_type_check",
                verdict=GateVerdict.BLOCK,
                reason_code=ReasonCode.BANNED_PROXY_TOR.value,
                detail="IP is a known Tor exit node",
                latency_ms=_elapsed_ms(t0),
                metadata={"ip": ip, "type": "tor"},
            )

        asn_number: int | None = None
        asn_org: str = ""

        if _asn_reader:
            try:
                asn_resp = _asn_reader.asn(ip)
                asn_number = asn_resp.autonomous_system_number
                asn_org = asn_resp.autonomous_system_organization or ""
                meta["asn"] = asn_number
                meta["asn_org"] = asn_org
            except Exception as exc:  # noqa: BLE001
                logger.debug("ASN lookup failed for %s: %s", ip, exc)

        if asn_number and asn_number in DATACENTER_ASNS:
            return GateResult(
                gate_id=1,
                gate_name="ip_type_check",
                verdict=GateVerdict.BLOCK,
                reason_code=ReasonCode.BANNED_PROXY_DC.value,
                detail=f"IP belongs to datacenter ASN {asn_number} ({asn_org})",
                latency_ms=_elapsed_ms(t0),
                metadata=meta,
            )

        # Heuristic: if org name contains hosting keywords, flag for review
        hosting_keywords = {
            "hosting", "datacenter", "data center", "cloud", "server",
            "vps", "dedicated", "colocation", "colo", "provider",
        }
        if asn_org and any(kw in asn_org.lower() for kw in hosting_keywords):
            return GateResult(
                gate_id=1,
                gate_name="ip_type_check",
                verdict=GateVerdict.REVIEW,
                reason_code=ReasonCode.BANNED_PROXY_DC.value,
                detail=f"ASN org '{asn_org}' matches hosting keywords",
                latency_ms=_elapsed_ms(t0),
                metadata=meta,
            )

        return GateResult(
            gate_id=1,
            gate_name="ip_type_check",
            verdict=GateVerdict.PASS,
            latency_ms=_elapsed_ms(t0),
            metadata=meta,
        )

    except Exception as exc:  # noqa: BLE001
        logger.error("Gate 1 error for %s: %s", ip, exc, exc_info=True)
        return GateResult(
            gate_id=1,
            gate_name="ip_type_check",
            verdict=GateVerdict.PASS,  # fail-open on lookup error
            detail=f"Gate error (fail-open): {exc}",
            latency_ms=_elapsed_ms(t0),
        )


def _check_tor_exit(ip: str) -> bool:
    """
    Check DynamoDB tor-exits table populated by a scheduled Lambda job.
    Falls back to False if the table is unavailable.
    """
    try:
        table = _dynamodb.Table("tor-exit-nodes")
        resp = table.get_item(Key={"ip_address": ip})
        return "Item" in resp
    except ClientError:
        return False


# ---------------------------------------------------------------------------
# Gate 2: VPN Detection
# ---------------------------------------------------------------------------

def _gate_vpn(ip: str) -> GateResult:
    """
    Check IP against a commercial VPN reputation API.
    Uses IP-API.com or IPQualityScore as backend.
    """
    t0 = time.perf_counter()

    try:
        result = _query_ip_reputation(ip)
        is_vpn = result.get("vpn", False)
        proxy = result.get("proxy", False)
        fraud_score = float(result.get("fraud_score", 0))
        isp = result.get("isp", "")
        meta = {
            "is_vpn": is_vpn,
            "proxy": proxy,
            "fraud_score": fraud_score,
            "isp": isp,
        }

        if is_vpn:
            return GateResult(
                gate_id=2,
                gate_name="vpn_detection",
                verdict=GateVerdict.BLOCK,
                reason_code=ReasonCode.BANNED_PROXY_VPN.value,
                detail=f"Commercial VPN detected via reputation API (ISP: {isp})",
                latency_ms=_elapsed_ms(t0),
                metadata=meta,
            )

        # Elevated fraud score from VPN-adjacent behaviour → REVIEW
        if fraud_score >= FRAUD_REVIEW_THRESHOLD:
            return GateResult(
                gate_id=2,
                gate_name="vpn_detection",
                verdict=GateVerdict.REVIEW,
                reason_code=ReasonCode.BANNED_PROXY_VPN.value,
                detail=f"High reputation fraud score {fraud_score} (threshold {FRAUD_REVIEW_THRESHOLD})",
                latency_ms=_elapsed_ms(t0),
                metadata=meta,
            )

        return GateResult(
            gate_id=2,
            gate_name="vpn_detection",
            verdict=GateVerdict.PASS,
            latency_ms=_elapsed_ms(t0),
            metadata=meta,
        )

    except Exception as exc:  # noqa: BLE001
        logger.error("Gate 2 (VPN) error for %s: %s", ip, exc, exc_info=True)
        return GateResult(
            gate_id=2,
            gate_name="vpn_detection",
            verdict=GateVerdict.PASS,
            detail=f"Gate error (fail-open): {exc}",
            latency_ms=_elapsed_ms(t0),
        )


def _query_ip_reputation(ip: str) -> dict[str, Any]:
    """
    Query IPQualityScore (primary) or IP-API (fallback) for reputation data.
    Results are cached in ElastiCache Redis when available.
    """
    import urllib.request
    import urllib.error

    # Try Redis cache first
    cached = _redis_get(f"iprep:{ip}")
    if cached:
        return json.loads(cached)

    result: dict[str, Any] = {}

    if IP_REPUTATION_KEY:
        # IPQualityScore
        url = (
            f"https://ipqualityscore.com/api/json/ip/{IP_REPUTATION_KEY}/{ip}"
            f"?strictness=1&allow_public_access_points=false"
            f"&fast=false&lighter_penalties=false"
        )
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode())
                result = {
                    "vpn": data.get("vpn", False),
                    "proxy": data.get("proxy", False),
                    "tor": data.get("tor", False),
                    "fraud_score": data.get("fraud_score", 0),
                    "isp": data.get("ISP", ""),
                    "country_code": data.get("country_code", ""),
                }
        except Exception as exc:  # noqa: BLE001
            logger.warning("IPQualityScore lookup failed: %s", exc)

    if not result:
        # ip-api.com fallback (free tier, no key needed)
        url = (
            f"http://ip-api.com/json/{ip}"
            f"?fields=status,message,proxy,hosting,isp,org,country"
        )
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode())
                result = {
                    "vpn": data.get("proxy", False),
                    "proxy": data.get("proxy", False),
                    "tor": False,
                    "fraud_score": 80.0 if (data.get("proxy") or data.get("hosting")) else 0.0,
                    "isp": data.get("isp", ""),
                    "country_code": data.get("country", ""),
                    "hosting": data.get("hosting", False),
                }
        except Exception as exc:  # noqa: BLE001
            logger.warning("ip-api.com lookup failed: %s", exc)
            result = {"vpn": False, "proxy": False, "tor": False, "fraud_score": 0.0}

    # Cache for 15 minutes
    _redis_set(f"iprep:{ip}", json.dumps(result), ex=900)
    return result


# ---------------------------------------------------------------------------
# Gate 3: Known Proxy Check
# ---------------------------------------------------------------------------

def _gate_known_proxy(ip: str) -> GateResult:
    """
    Check IP against a DynamoDB proxy database populated from public/commercial
    proxy lists (ProxyCheck.io, ProxyDB.net, etc.).
    """
    t0 = time.perf_counter()

    try:
        table = _dynamodb.Table("known-proxies")
        # "source" is a DynamoDB reserved keyword; alias it via ExpressionAttributeNames.
        resp = table.get_item(
            Key={"ip_address": ip},
            ProjectionExpression="ip_address, proxy_type, #src, confidence",
            ExpressionAttributeNames={"#src": "source"},
        )
        item = resp.get("Item")

        if item:
            confidence = float(item.get("confidence", 1.0))
            proxy_type = item.get("proxy_type", "unknown")
            source = item.get("source", "unknown")

            if confidence >= 0.8:
                return GateResult(
                    gate_id=3,
                    gate_name="known_proxy_check",
                    verdict=GateVerdict.BLOCK,
                    reason_code=ReasonCode.BANNED_PROXY_KNOWN.value,
                    detail=f"Known proxy: type={proxy_type}, source={source}, confidence={confidence:.2f}",
                    latency_ms=_elapsed_ms(t0),
                    metadata={"proxy_type": proxy_type, "source": source, "confidence": confidence},
                )
            else:
                return GateResult(
                    gate_id=3,
                    gate_name="known_proxy_check",
                    verdict=GateVerdict.REVIEW,
                    reason_code=ReasonCode.BANNED_PROXY_KNOWN.value,
                    detail=f"Low-confidence proxy match: {confidence:.2f}",
                    latency_ms=_elapsed_ms(t0),
                    metadata={"proxy_type": proxy_type, "confidence": confidence},
                )

        return GateResult(
            gate_id=3,
            gate_name="known_proxy_check",
            verdict=GateVerdict.PASS,
            latency_ms=_elapsed_ms(t0),
        )

    except ClientError as exc:
        logger.error("Gate 3 DynamoDB error: %s", exc)
        return GateResult(
            gate_id=3,
            gate_name="known_proxy_check",
            verdict=GateVerdict.PASS,
            detail=f"DynamoDB error (fail-open): {exc}",
            latency_ms=_elapsed_ms(t0),
        )


# ---------------------------------------------------------------------------
# Gate 4: IP Blacklist Check
# ---------------------------------------------------------------------------

def _gate_blacklist(ip: str) -> GateResult:
    """
    Check DynamoDB-backed IP blacklist with TTL expiry.
    """
    t0 = time.perf_counter()

    try:
        assert _blacklist_svc is not None
        entry = _blacklist_svc.get(ip)

        if entry:
            return GateResult(
                gate_id=4,
                gate_name="ip_blacklist_check",
                verdict=GateVerdict.BLOCK,
                reason_code=ReasonCode.BANNED_IP_BLACKLIST.value,
                detail=f"IP blacklisted: reason={entry.get('reason')}, added_by={entry.get('added_by')}",
                latency_ms=_elapsed_ms(t0),
                metadata=entry,
            )

        return GateResult(
            gate_id=4,
            gate_name="ip_blacklist_check",
            verdict=GateVerdict.PASS,
            latency_ms=_elapsed_ms(t0),
        )

    except Exception as exc:  # noqa: BLE001
        logger.error("Gate 4 blacklist error: %s", exc, exc_info=True)
        return GateResult(
            gate_id=4,
            gate_name="ip_blacklist_check",
            verdict=GateVerdict.PASS,
            detail=f"Blacklist error (fail-open): {exc}",
            latency_ms=_elapsed_ms(t0),
        )


# ---------------------------------------------------------------------------
# Gate 5: Fraud Score
# ---------------------------------------------------------------------------

def _gate_fraud_score(ip: str, user_id: str | None, session_id: str | None) -> GateResult:
    """
    Compute a composite fraud score from multiple real-time signals:
    - IP velocity (requests per window from this IP)
    - User velocity (accounts per IP)
    - Country risk score
    - ASN reputation
    - Time-of-day anomaly
    """
    t0 = time.perf_counter()

    signals: dict[str, float] = {}
    score = 0.0

    try:
        # Signal 1: IP request velocity (Redis counter)
        ip_velocity = _get_velocity(f"vel:ip:{ip}", VELOCITY_WINDOW_SECONDS)
        if ip_velocity > 100:
            signals["ip_velocity"] = min((ip_velocity / 100) * 30.0, 30.0)
        elif ip_velocity > 50:
            signals["ip_velocity"] = 15.0
        else:
            signals["ip_velocity"] = 0.0

        # Signal 2: Accounts per IP in last 24h
        accounts_from_ip = _get_velocity(f"vel:accts:{ip}", 86400)
        if accounts_from_ip > 5:
            signals["multi_account"] = min((accounts_from_ip / 5) * 20.0, 20.0)
        else:
            signals["multi_account"] = 0.0

        # Signal 3: Country risk from GeoIP
        country_risk = _get_country_risk_score(ip)
        signals["country_risk"] = country_risk  # 0-25

        # Signal 4: Reputation score from prior gates (passed via Redis tag)
        rep_score = float(_redis_get(f"rep:{ip}") or 0)
        signals["reputation"] = rep_score  # 0-25

        score = sum(signals.values())

        # Track velocity for this IP
        _increment_velocity(f"vel:ip:{ip}", VELOCITY_WINDOW_SECONDS)
        if user_id:
            _increment_velocity(f"vel:accts:{ip}", 86400)

        meta = {"signals": signals, "total_score": round(score, 2)}

        if score >= FRAUD_SCORE_THRESHOLD:
            return GateResult(
                gate_id=5,
                gate_name="fraud_score",
                verdict=GateVerdict.BLOCK,
                reason_code=ReasonCode.HIGH_FRAUD_SCORE.value,
                detail=f"Fraud score {score:.1f} exceeds block threshold {FRAUD_SCORE_THRESHOLD}",
                latency_ms=_elapsed_ms(t0),
                metadata=meta,
            )

        if score >= FRAUD_REVIEW_THRESHOLD:
            return GateResult(
                gate_id=5,
                gate_name="fraud_score",
                verdict=GateVerdict.REVIEW,
                reason_code=ReasonCode.HIGH_FRAUD_SCORE.value,
                detail=f"Fraud score {score:.1f} in review range [{FRAUD_REVIEW_THRESHOLD}, {FRAUD_SCORE_THRESHOLD})",
                latency_ms=_elapsed_ms(t0),
                metadata=meta,
            )

        return GateResult(
            gate_id=5,
            gate_name="fraud_score",
            verdict=GateVerdict.PASS,
            latency_ms=_elapsed_ms(t0),
            metadata=meta,
        )

    except Exception as exc:  # noqa: BLE001
        logger.error("Gate 5 fraud score error: %s", exc, exc_info=True)
        return GateResult(
            gate_id=5,
            gate_name="fraud_score",
            verdict=GateVerdict.PASS,
            detail=f"Fraud score error (fail-open): {exc}",
            latency_ms=_elapsed_ms(t0),
        )


def _get_country_risk_score(ip: str) -> float:
    """Return a country-level risk score (0–25) from GeoIP country data."""
    # High-risk jurisdictions for iGaming fraud
    HIGH_RISK_COUNTRIES = {
        "NG", "GH", "PK", "BD", "VN", "KH", "MM",
        "SD", "SO", "YE", "AF", "IQ", "SY", "LY",
        "CF", "CD", "ZW", "ET", "ER",
    }
    MEDIUM_RISK_COUNTRIES = {
        "CN", "RU", "UA", "BY", "MD", "AZ", "AM",
        "KZ", "UZ", "TJ", "TM", "KG",
    }

    if not _city_reader:
        return 0.0

    try:
        resp = _city_reader.city(ip)
        country = resp.country.iso_code or ""
        if country in HIGH_RISK_COUNTRIES:
            return 25.0
        if country in MEDIUM_RISK_COUNTRIES:
            return 12.0
        return 0.0
    except Exception:  # noqa: BLE001
        return 0.0


# ---------------------------------------------------------------------------
# Gate 6: Device Fingerprint Anomaly Detection
# ---------------------------------------------------------------------------

def _gate_device_fingerprint(
    ip: str,
    user_id: str | None,
    fingerprint_data: dict[str, Any],
) -> GateResult:
    """
    Compare submitted device fingerprint against stored history.
    Detects: fingerprint rotation, impossible travel, bot-like fingerprints.
    """
    t0 = time.perf_counter()

    if not fingerprint_data:
        return GateResult(
            gate_id=6,
            gate_name="device_fingerprint",
            verdict=GateVerdict.PASS,
            detail="No fingerprint data submitted (gate skipped)",
            latency_ms=_elapsed_ms(t0),
        )

    try:
        assert _device_svc is not None
        anomaly: FingerprintAnomaly = _device_svc.check_and_store(
            ip=ip,
            user_id=user_id,
            fingerprint=fingerprint_data,
        )

        if anomaly.is_anomalous:
            verdict = GateVerdict.BLOCK if anomaly.severity == "HIGH" else GateVerdict.REVIEW
            return GateResult(
                gate_id=6,
                gate_name="device_fingerprint",
                verdict=verdict,
                reason_code=ReasonCode.DEVICE_ANOMALY.value,
                detail=anomaly.description,
                latency_ms=_elapsed_ms(t0),
                metadata={
                    "anomaly_type": anomaly.anomaly_type,
                    "severity": anomaly.severity,
                    "score": anomaly.score,
                },
            )

        return GateResult(
            gate_id=6,
            gate_name="device_fingerprint",
            verdict=GateVerdict.PASS,
            latency_ms=_elapsed_ms(t0),
            metadata={"fingerprint_id": anomaly.fingerprint_id},
        )

    except Exception as exc:  # noqa: BLE001
        logger.error("Gate 6 fingerprint error: %s", exc, exc_info=True)
        return GateResult(
            gate_id=6,
            gate_name="device_fingerprint",
            verdict=GateVerdict.PASS,
            detail=f"Fingerprint error (fail-open): {exc}",
            latency_ms=_elapsed_ms(t0),
        )


# ---------------------------------------------------------------------------
# Gate 7: Sanctions / PEP Check
# ---------------------------------------------------------------------------

def _gate_sanctions(
    user_id: str | None,
    body_data: dict[str, Any],
) -> GateResult:
    """
    Fuzzy-match supplied name + date-of-birth against OFAC SDN list and
    PEP databases loaded from S3.
    """
    t0 = time.perf_counter()

    full_name = body_data.get("full_name", "")
    dob = body_data.get("date_of_birth", "")
    nationality = body_data.get("nationality", "")

    if not full_name:
        return GateResult(
            gate_id=7,
            gate_name="sanctions_pep_check",
            verdict=GateVerdict.PASS,
            detail="No name provided (gate skipped)",
            latency_ms=_elapsed_ms(t0),
        )

    try:
        assert _sanctions_svc is not None
        matches = _sanctions_svc.search(
            name=full_name,
            dob=dob,
            nationality=nationality,
        )

        if matches:
            best = matches[0]
            score = best.get("score", 0)

            if score >= 0.90:
                return GateResult(
                    gate_id=7,
                    gate_name="sanctions_pep_check",
                    verdict=GateVerdict.BLOCK,
                    reason_code=ReasonCode.SANCTIONS_MATCH.value,
                    detail=f"OFAC SDN match: '{best.get('matched_name')}' (score={score:.2f}, list={best.get('list_type')})",
                    latency_ms=_elapsed_ms(t0),
                    metadata={"matches": matches[:3]},
                )
            elif score >= 0.75:
                return GateResult(
                    gate_id=7,
                    gate_name="sanctions_pep_check",
                    verdict=GateVerdict.REVIEW,
                    reason_code=ReasonCode.SANCTIONS_MATCH.value,
                    detail=f"Possible sanctions match: '{best.get('matched_name')}' (score={score:.2f})",
                    latency_ms=_elapsed_ms(t0),
                    metadata={"matches": matches[:3]},
                )

        return GateResult(
            gate_id=7,
            gate_name="sanctions_pep_check",
            verdict=GateVerdict.PASS,
            latency_ms=_elapsed_ms(t0),
            metadata={"checked_name": full_name, "match_count": len(matches)},
        )

    except Exception as exc:  # noqa: BLE001
        logger.error("Gate 7 sanctions error: %s", exc, exc_info=True)
        return GateResult(
            gate_id=7,
            gate_name="sanctions_pep_check",
            verdict=GateVerdict.REVIEW,  # fail-to-review on sanctions errors
            detail=f"Sanctions check error (fail-to-review): {exc}",
            latency_ms=_elapsed_ms(t0),
        )


# ---------------------------------------------------------------------------
# Gate 8: KYC Status Verification
# ---------------------------------------------------------------------------

def _gate_kyc(user_id: str | None) -> GateResult:
    """
    Verify KYC status from DynamoDB. Status values:
    VERIFIED, PENDING, REJECTED, EXPIRED, NOT_STARTED
    """
    t0 = time.perf_counter()

    if not user_id:
        return GateResult(
            gate_id=8,
            gate_name="kyc_status",
            verdict=GateVerdict.PASS,
            detail="No user_id provided (guest session, KYC skipped)",
            latency_ms=_elapsed_ms(t0),
        )

    try:
        table = _dynamodb.Table(KYC_TABLE)
        resp = table.get_item(
            Key={"user_id": user_id},
            ProjectionExpression="user_id, kyc_status, kyc_level, verified_at, expires_at, rejection_reason",
        )
        item = resp.get("Item")

        if not item:
            return GateResult(
                gate_id=8,
                gate_name="kyc_status",
                verdict=GateVerdict.REVIEW,
                reason_code=ReasonCode.KYC_FAILED.value,
                detail="User has no KYC record",
                latency_ms=_elapsed_ms(t0),
                metadata={"user_id": user_id, "kyc_status": "NOT_FOUND"},
            )

        kyc_status = item.get("kyc_status", "NOT_STARTED")
        expires_at = int(item.get("expires_at", 0))
        now = int(time.time())

        if kyc_status == "VERIFIED":
            if expires_at and expires_at < now:
                return GateResult(
                    gate_id=8,
                    gate_name="kyc_status",
                    verdict=GateVerdict.REVIEW,
                    reason_code=ReasonCode.KYC_FAILED.value,
                    detail="KYC verification has expired",
                    latency_ms=_elapsed_ms(t0),
                    metadata={"kyc_status": "EXPIRED", "expired_at": expires_at},
                )
            return GateResult(
                gate_id=8,
                gate_name="kyc_status",
                verdict=GateVerdict.PASS,
                latency_ms=_elapsed_ms(t0),
                metadata={"kyc_status": kyc_status, "kyc_level": item.get("kyc_level")},
            )

        if kyc_status == "REJECTED":
            return GateResult(
                gate_id=8,
                gate_name="kyc_status",
                verdict=GateVerdict.BLOCK,
                reason_code=ReasonCode.KYC_FAILED.value,
                detail=f"KYC rejected: {item.get('rejection_reason', 'unspecified')}",
                latency_ms=_elapsed_ms(t0),
                metadata={"kyc_status": kyc_status},
            )

        # PENDING / NOT_STARTED → REVIEW
        return GateResult(
            gate_id=8,
            gate_name="kyc_status",
            verdict=GateVerdict.REVIEW,
            reason_code=ReasonCode.KYC_FAILED.value,
            detail=f"KYC status: {kyc_status}",
            latency_ms=_elapsed_ms(t0),
            metadata={"kyc_status": kyc_status},
        )

    except ClientError as exc:
        logger.error("Gate 8 KYC DynamoDB error: %s", exc)
        return GateResult(
            gate_id=8,
            gate_name="kyc_status",
            verdict=GateVerdict.REVIEW,
            detail=f"KYC lookup error (fail-to-review): {exc}",
            latency_ms=_elapsed_ms(t0),
        )


# ---------------------------------------------------------------------------
# WAF + SNS post-processing
# ---------------------------------------------------------------------------

def _post_block_actions(ip: str, result: PipelineResult) -> None:
    """
    On a BLOCK verdict:
    1. Add IP to WAF IP set for edge-level blocking.
    2. Publish SNS alert with full pipeline result.
    3. Auto-blacklist the IP with a TTL based on reason code.
    """
    # WAF edge block
    if _waf_svc and result.reason_code in {
        ReasonCode.BANNED_PROXY_TOR.value,
        ReasonCode.BANNED_PROXY_DC.value,
        ReasonCode.BANNED_IP_BLACKLIST.value,
        ReasonCode.BANNED_PROXY_VPN.value,
    }:
        try:
            _waf_svc.add_ip(ip, description=f"Auto-blocked: {result.reason_code}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("WAF IP add failed: %s", exc)

    # Auto-blacklist
    if _blacklist_svc:
        ttl_hours = {
            ReasonCode.BANNED_PROXY_TOR.value: 720,      # 30 days
            ReasonCode.BANNED_PROXY_DC.value: 168,        # 7 days
            ReasonCode.BANNED_PROXY_VPN.value: 72,        # 3 days
            ReasonCode.BANNED_PROXY_KNOWN.value: 168,     # 7 days
            ReasonCode.HIGH_FRAUD_SCORE.value: 24,        # 1 day
            ReasonCode.SANCTIONS_MATCH.value: 8760,       # 1 year
        }.get(result.reason_code or "", 24)

        try:
            _blacklist_svc.add(
                ip=ip,
                reason=result.reason_code or "AUTO_BLOCKED",
                added_by="lambda_ip_gate",
                ttl_hours=ttl_hours,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Auto-blacklist write failed: %s", exc)

    # SNS alert
    if _sns and SNS_ALERT_TOPIC:
        try:
            _sns.publish(
                TopicArn=SNS_ALERT_TOPIC,
                Subject=f"[iGaming] IP BLOCKED: {ip} — {result.reason_code}",
                Message=json.dumps({
                    "request_id": result.request_id,
                    "ip": ip,
                    "verdict": result.final_verdict.value,
                    "reason_code": result.reason_code,
                    "blocking_gate": result.blocking_gate,
                    "user_id": result.user_id,
                    "total_latency_ms": result.total_latency_ms,
                    "timestamp": result.timestamp,
                }, indent=2),
                MessageAttributes={
                    "reason_code": {
                        "DataType": "String",
                        "StringValue": result.reason_code or "UNKNOWN",
                    },
                    "verdict": {
                        "DataType": "String",
                        "StringValue": result.final_verdict.value,
                    },
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("SNS publish failed: %s", exc)


# ---------------------------------------------------------------------------
# Redis helpers (ElastiCache)
# ---------------------------------------------------------------------------

_redis_client: Any = None


def _get_redis():
    """Return a Redis client, or None if ElastiCache is not configured."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    if not ELASTICACHE_ENDPOINT:
        return None
    try:
        import redis  # type: ignore

        host, _, port = ELASTICACHE_ENDPOINT.partition(":")
        _redis_client = redis.Redis(
            host=host,
            port=int(port) if port else 6379,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        _redis_client.ping()
        return _redis_client
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis connection failed, operating without cache: %s", exc)
        return None


def _redis_get(key: str) -> str | None:
    client = _get_redis()
    if not client:
        return None
    try:
        return client.get(key)
    except Exception:  # noqa: BLE001
        return None


def _redis_set(key: str, value: str, ex: int = 300) -> None:
    client = _get_redis()
    if not client:
        return
    try:
        client.set(key, value, ex=ex)
    except Exception:  # noqa: BLE001
        pass


def _get_velocity(key: str, window_seconds: int) -> int:
    """Return count from a Redis sliding-window counter, or 0 if unavailable."""
    client = _get_redis()
    if not client:
        return 0
    try:
        count = client.get(key)
        return int(count) if count else 0
    except Exception:  # noqa: BLE001
        return 0


def _increment_velocity(key: str, window_seconds: int) -> None:
    """Increment a Redis velocity counter with TTL."""
    client = _get_redis()
    if not client:
        return
    try:
        pipe = client.pipeline()
        pipe.incr(key)
        pipe.expire(key, window_seconds)
        pipe.execute()
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _elapsed_ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000.0


# RFC 5737 documentation ranges (TEST-NET-1/2/3).  Python 3.11+ classifies
# these as is_private=True, but they are used in staging/testing pipelines
# and must not be rejected by the IP validator.
_RFC5737_NETS = [
    ipaddress.ip_network("192.0.2.0/24"),    # TEST-NET-1
    ipaddress.ip_network("198.51.100.0/24"), # TEST-NET-2
    ipaddress.ip_network("203.0.113.0/24"),  # TEST-NET-3
]

# True RFC-1918 / loopback / APIPA ranges that must be rejected.
_PRIVATE_NETS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _validate_ip(ip: str) -> bool:
    """Return True if ip is a valid public IPv4 or IPv6 address.

    RFC 5737 documentation ranges (TEST-NET-1/2/3) are treated as public —
    Python 3.11+ marks them is_private=True, but they are used legitimately
    in staging and integration pipelines.  True RFC-1918, loopback, APIPA,
    and unspecified addresses are still rejected.
    """
    try:
        addr = ipaddress.ip_address(ip)
        if addr.is_unspecified or addr.is_loopback:
            return False
        # Permit RFC 5737 documentation ranges explicitly
        for doc_net in _RFC5737_NETS:
            if addr in doc_net:
                return True
        # Reject any remaining private / link-local address
        for priv_net in _PRIVATE_NETS:
            if addr in priv_net:
                return False
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# CloudWatch structured logging
# ---------------------------------------------------------------------------

def _log_audit(result: PipelineResult, ip: str) -> None:
    """Emit a structured CloudWatch log entry for compliance audit trails."""
    logger.info(
        json.dumps({
            "event_type": "ip_gate_decision",
            "request_id": result.request_id,
            "ip_address": ip,
            "user_id": result.user_id,
            "session_id": result.session_id,
            "verdict": result.final_verdict.value,
            "reason_code": result.reason_code,
            "blocking_gate": result.blocking_gate,
            "total_latency_ms": round(result.total_latency_ms, 2),
            "gates_evaluated": len(result.gates),
            "timestamp": result.timestamp,
        })
    )


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def _run_pipeline(
    ip: str,
    user_id: str | None,
    session_id: str | None,
    fingerprint_data: dict[str, Any],
    body_data: dict[str, Any],
) -> PipelineResult:
    """
    Execute all 8 gates sequentially.
    Short-circuit on BLOCK; aggregate REVIEW verdicts.
    """
    pipeline_start = time.perf_counter()
    request_id = str(uuid.uuid4())
    gates: list[GateResult] = []

    result = PipelineResult(
        request_id=request_id,
        ip_address=ip,
        user_id=user_id,
        session_id=session_id,
        final_verdict=GateVerdict.PASS,
        blocking_gate=None,
        reason_code=None,
    )

    def _run_gate(gate_result: GateResult) -> bool:
        """Append gate result. Return True if pipeline should stop."""
        gates.append(gate_result)
        if gate_result.verdict == GateVerdict.BLOCK:
            result.final_verdict = GateVerdict.BLOCK
            result.blocking_gate = gate_result.gate_id
            result.reason_code = gate_result.reason_code
            return True
        if gate_result.verdict == GateVerdict.REVIEW:
            # Only upgrade to REVIEW, never downgrade a BLOCK
            if result.final_verdict != GateVerdict.BLOCK:
                result.final_verdict = GateVerdict.REVIEW
                if not result.reason_code:
                    result.reason_code = gate_result.reason_code
                    result.blocking_gate = gate_result.gate_id
        return False

    # Gate 1: IP Type (Tor / Datacenter / Residential)
    if _run_gate(_gate_ip_type(ip)):
        result.gates = gates
        result.total_latency_ms = _elapsed_ms(pipeline_start)
        return result

    # Gate 2: VPN Detection
    if _run_gate(_gate_vpn(ip)):
        result.gates = gates
        result.total_latency_ms = _elapsed_ms(pipeline_start)
        return result

    # Gate 3: Known Proxy
    if _run_gate(_gate_known_proxy(ip)):
        result.gates = gates
        result.total_latency_ms = _elapsed_ms(pipeline_start)
        return result

    # Gate 4: IP Blacklist
    if _run_gate(_gate_blacklist(ip)):
        result.gates = gates
        result.total_latency_ms = _elapsed_ms(pipeline_start)
        return result

    # Gate 5: Fraud Score
    if _run_gate(_gate_fraud_score(ip, user_id, session_id)):
        result.gates = gates
        result.total_latency_ms = _elapsed_ms(pipeline_start)
        return result

    # Gate 6: Device Fingerprint
    if _run_gate(_gate_device_fingerprint(ip, user_id, fingerprint_data)):
        result.gates = gates
        result.total_latency_ms = _elapsed_ms(pipeline_start)
        return result

    # Gate 7: Sanctions / PEP
    if _run_gate(_gate_sanctions(user_id, body_data)):
        result.gates = gates
        result.total_latency_ms = _elapsed_ms(pipeline_start)
        return result

    # Gate 8: KYC Status
    _run_gate(_gate_kyc(user_id))

    result.gates = gates
    result.total_latency_ms = _elapsed_ms(pipeline_start)
    return result


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------

def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    AWS Lambda entry point.

    Expected request shape (API Gateway HTTP API v2):
      POST /check
      Headers:
        X-User-ID: <optional user identifier>
        X-Session-ID: <optional session identifier>
      Body (JSON):
        {
          "user_id": "u_123",            // optional
          "session_id": "sess_abc",      // optional
          "full_name": "John Doe",       // for sanctions gate
          "date_of_birth": "1980-01-15", // for sanctions gate
          "nationality": "US",           // for sanctions gate
          "fingerprint": {               // for device fingerprint gate
            "canvas_hash": "...",
            "webgl_hash": "...",
            "user_agent": "...",
            "screen_resolution": "...",
            "timezone": "...",
            "language": "..."
          }
        }
    """
    try:
        _init_services()
    except Exception as exc:  # noqa: BLE001
        logger.error("Service initialisation failed: %s", exc, exc_info=True)
        # Return a conservative REVIEW rather than failing open or closed
        return {
            "statusCode": 202,
            "body": json.dumps({
                "verdict": "REVIEW",
                "reason_code": "INIT_FAILURE",
                "detail": "Service initialisation error",
            }),
            "headers": {"Content-Type": "application/json"},
        }

    ip, user_id, session_id, fingerprint_data = _parse_request(event)

    if not _validate_ip(ip):
        logger.warning("Invalid or private IP address: %s", ip)
        return {
            "statusCode": 400,
            "body": json.dumps({
                "error": "invalid_ip",
                "message": f"'{ip}' is not a valid public IP address",
            }),
            "headers": {"Content-Type": "application/json"},
        }

    # Parse body for sanctions data
    raw_body = event.get("body", "")
    body_data: dict[str, Any] = {}
    if raw_body:
        try:
            body_data = json.loads(raw_body) if isinstance(raw_body, str) else (raw_body or {})
        except (json.JSONDecodeError, TypeError):
            body_data = {}

    pipeline_result = _run_pipeline(
        ip=ip,
        user_id=user_id,
        session_id=session_id,
        fingerprint_data=fingerprint_data,
        body_data=body_data,
    )

    _log_audit(pipeline_result, ip)

    if pipeline_result.final_verdict == GateVerdict.BLOCK:
        _post_block_actions(ip, pipeline_result)

    return pipeline_result.to_response()
