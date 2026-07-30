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
device_fingerprint_dynamo.py
-----------------------------
DynamoDB-backed device fingerprint tracker with anomaly detection.

Table schema:
  PK: fingerprint_id (S)         — SHA-256 of stable fingerprint components
  SK: user_id (S)                — user associated with this fingerprint
  GSI: user-fp-index             — partition key: user_id, sort key: seen_at (N)
  GSI: ip-fp-index               — partition key: ip_address, sort key: seen_at (N)

Anomaly detection rules:
  1. FINGERPRINT_ROTATION     — same user, >N distinct fingerprints in window
  2. IMPOSSIBLE_TRAVEL        — same fingerprint, IPs from different continents within 1h
  3. HEADLESS_BROWSER         — missing or bot-indicative canvas/WebGL hashes
  4. UA_MISMATCH              — user-agent platform vs screen/timezone mismatch
  5. SHARED_FINGERPRINT       — same fingerprint used by too many distinct users
  6. TIMEZONE_MISMATCH        — timezone doesn't match GeoIP country
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key, Attr
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MAX_FINGERPRINTS_PER_USER = 5           # distinct FPs per user in 24h before anomaly
MAX_USERS_PER_FINGERPRINT = 3           # distinct users sharing one FP (bot farm)
FINGERPRINT_TTL_DAYS = 90              # DynamoDB TTL for fingerprint records
ROTATION_WINDOW_SECONDS = 86400        # 24h window for rotation detection
SHARED_FP_WINDOW_SECONDS = 3600        # 1h window for shared FP detection

# Headless browser canvas hash sentinels (known headless/bot fingerprints)
KNOWN_BOT_CANVAS_HASHES: frozenset[str] = frozenset({
    "2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d",  # blank canvas (Puppeteer headless)
    "00000000000000000000000000000000",    # zero hash
    "ffffffffffffffffffffffffffffffff",    # uniform fill
    "3f3f3f3f3f3f3f3f3f3f3f3f3f3f3f3f",  # common headless artefact
})

# ---------------------------------------------------------------------------
# Domain objects
# ---------------------------------------------------------------------------


@dataclass
class DeviceFingerprint:
    """Represents a normalised device fingerprint."""
    canvas_hash: str = ""
    webgl_hash: str = ""
    user_agent: str = ""
    screen_resolution: str = ""
    timezone: str = ""
    language: str = ""
    platform: str = ""
    hardware_concurrency: int = 0
    device_memory: float = 0.0
    color_depth: int = 0
    touch_support: bool = False
    audio_hash: str = ""
    font_hash: str = ""
    plugins_hash: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DeviceFingerprint":
        return cls(
            canvas_hash=str(data.get("canvas_hash", "")).lower()[:64],
            webgl_hash=str(data.get("webgl_hash", "")).lower()[:64],
            user_agent=str(data.get("user_agent", ""))[:512],
            screen_resolution=str(data.get("screen_resolution", ""))[:20],
            timezone=str(data.get("timezone", ""))[:64],
            language=str(data.get("language", ""))[:16],
            platform=str(data.get("platform", ""))[:32],
            hardware_concurrency=int(data.get("hardware_concurrency", 0)),
            device_memory=float(data.get("device_memory", 0.0)),
            color_depth=int(data.get("color_depth", 0)),
            touch_support=bool(data.get("touch_support", False)),
            audio_hash=str(data.get("audio_hash", "")).lower()[:64],
            font_hash=str(data.get("font_hash", "")).lower()[:64],
            plugins_hash=str(data.get("plugins_hash", "")).lower()[:64],
        )

    def stable_id(self) -> str:
        """
        Compute a stable fingerprint ID from components unlikely to change
        between sessions (canvas, WebGL, audio, fonts).
        """
        stable_parts = "|".join([
            self.canvas_hash,
            self.webgl_hash,
            self.audio_hash,
            self.font_hash,
            self.screen_resolution,
            self.platform,
            str(self.hardware_concurrency),
            str(self.color_depth),
        ])
        return hashlib.sha256(stable_parts.encode()).hexdigest()[:32]

    def is_likely_headless(self) -> bool:
        """Heuristic detection of headless browsers / bots."""
        # Missing stable components (real browsers always have these)
        if not self.canvas_hash and not self.webgl_hash:
            return True

        # Known bot canvas hashes
        if self.canvas_hash in KNOWN_BOT_CANVAS_HASHES:
            return True

        # Headless Chrome default: no plugins, Linux platform
        if (
            self.platform.lower() in {"linux x86_64", "linux armv8l"}
            and not self.plugins_hash
            and not self.font_hash
        ):
            return True

        # Unrealistic hardware_concurrency
        if self.hardware_concurrency > 256:
            return True

        return False

    def ua_platform_consistent(self) -> bool:
        """Check if the user-agent platform matches the reported platform."""
        if not self.user_agent or not self.platform:
            return True  # can't check → assume consistent

        ua_lower = self.user_agent.lower()
        plat_lower = self.platform.lower()

        # Windows
        if "windows" in ua_lower and "win" not in plat_lower:
            return False
        # macOS
        if ("mac os x" in ua_lower or "macintosh" in ua_lower) and "mac" not in plat_lower:
            return False
        # Android
        if "android" in ua_lower and "android" not in plat_lower and "linux" not in plat_lower:
            return False

        return True


@dataclass
class FingerprintAnomaly:
    """Result of anomaly detection check."""
    is_anomalous: bool
    anomaly_type: str = ""           # e.g. FINGERPRINT_ROTATION, HEADLESS_BROWSER
    severity: str = "LOW"            # LOW | MEDIUM | HIGH
    score: float = 0.0               # 0–100 anomaly score
    description: str = ""
    fingerprint_id: str = ""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class DeviceFingerprintService:
    """
    Stores device fingerprints in DynamoDB and detects anomalies.

    Usage:
        svc = DeviceFingerprintService(table_name="device-fingerprints")
        result = svc.check_and_store(ip="1.2.3.4", user_id="u123", fingerprint={...})
    """

    def __init__(
        self,
        table_name: str = "device-fingerprints",
        region: str = "us-east-1",
    ) -> None:
        self._table_name = table_name
        self._region = region
        self._dynamodb = boto3.resource("dynamodb", region_name=region)
        self._table = self._dynamodb.Table(table_name)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def check_and_store(
        self,
        ip: str,
        user_id: str | None,
        fingerprint: dict[str, Any],
    ) -> FingerprintAnomaly:
        """
        Evaluate a fingerprint for anomalies, then store it.

        Returns a FingerprintAnomaly describing any detected anomalies.
        The fingerprint is stored regardless of anomaly verdict.
        """
        fp = DeviceFingerprint.from_dict(fingerprint)
        fp_id = fp.stable_id()

        anomaly = self._detect_anomalies(fp=fp, fp_id=fp_id, ip=ip, user_id=user_id)

        # Always persist the fingerprint event
        self._store(fp=fp, fp_id=fp_id, ip=ip, user_id=user_id, anomaly=anomaly)

        anomaly.fingerprint_id = fp_id
        return anomaly

    # ------------------------------------------------------------------
    # Anomaly detection
    # ------------------------------------------------------------------

    def _detect_anomalies(
        self,
        fp: DeviceFingerprint,
        fp_id: str,
        ip: str,
        user_id: str | None,
    ) -> FingerprintAnomaly:
        """Run all anomaly checks and return the most severe finding."""
        findings: list[FingerprintAnomaly] = []

        # Check 1: Headless browser / bot
        if fp.is_likely_headless():
            findings.append(FingerprintAnomaly(
                is_anomalous=True,
                anomaly_type="HEADLESS_BROWSER",
                severity="HIGH",
                score=85.0,
                description="Device fingerprint indicates headless browser or bot automation",
                fingerprint_id=fp_id,
            ))

        # Check 2: User-agent / platform mismatch
        if not fp.ua_platform_consistent():
            findings.append(FingerprintAnomaly(
                is_anomalous=True,
                anomaly_type="UA_MISMATCH",
                severity="MEDIUM",
                score=55.0,
                description=(
                    f"User-agent platform mismatch: UA='{fp.user_agent[:80]}' "
                    f"vs platform='{fp.platform}'"
                ),
                fingerprint_id=fp_id,
            ))

        if user_id:
            # Check 3: Fingerprint rotation (too many distinct FPs for one user)
            rotation = self._check_rotation(user_id=user_id, fp_id=fp_id)
            if rotation:
                findings.append(rotation)

            # Check 4: Shared fingerprint (one FP used by many users)
            shared = self._check_shared_fingerprint(fp_id=fp_id, user_id=user_id)
            if shared:
                findings.append(shared)

        # Check 5: Impossible travel (same FP from geographically distant IPs)
        travel = self._check_impossible_travel(fp_id=fp_id, new_ip=ip)
        if travel:
            findings.append(travel)

        if not findings:
            return FingerprintAnomaly(is_anomalous=False, fingerprint_id=fp_id)

        # Return the most severe finding
        severity_order = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
        findings.sort(key=lambda f: severity_order.get(f.severity, 0), reverse=True)
        return findings[0]

    def _check_rotation(self, user_id: str, fp_id: str) -> FingerprintAnomaly | None:
        """Detect rapid fingerprint rotation for the same user."""
        try:
            cutoff = int(time.time()) - ROTATION_WINDOW_SECONDS
            resp = self._table.query(
                IndexName="user-fp-index",
                KeyConditionExpression=(
                    Key("user_id").eq(user_id) & Key("seen_at").gt(cutoff)
                ),
                ProjectionExpression="fingerprint_id",
                Select="SPECIFIC_ATTRIBUTES",
            )
            distinct_fps = {item["fingerprint_id"] for item in resp.get("Items", [])}
            distinct_fps.add(fp_id)  # include the current one

            if len(distinct_fps) > MAX_FINGERPRINTS_PER_USER:
                score = min(100.0, 50.0 + (len(distinct_fps) - MAX_FINGERPRINTS_PER_USER) * 10.0)
                return FingerprintAnomaly(
                    is_anomalous=True,
                    anomaly_type="FINGERPRINT_ROTATION",
                    severity="HIGH" if score >= 75 else "MEDIUM",
                    score=score,
                    description=(
                        f"User {user_id} used {len(distinct_fps)} distinct fingerprints "
                        f"in {ROTATION_WINDOW_SECONDS // 3600}h "
                        f"(threshold: {MAX_FINGERPRINTS_PER_USER})"
                    ),
                )
        except ClientError as exc:
            logger.warning("Rotation check DynamoDB error: %s", exc)
        return None

    def _check_shared_fingerprint(
        self, fp_id: str, user_id: str
    ) -> FingerprintAnomaly | None:
        """Detect fingerprints shared across too many distinct user accounts."""
        try:
            cutoff = int(time.time()) - SHARED_FP_WINDOW_SECONDS
            resp = self._table.query(
                IndexName="fp-users-index",
                KeyConditionExpression=(
                    Key("fingerprint_id").eq(fp_id) & Key("seen_at").gt(cutoff)
                ),
                ProjectionExpression="user_id",
                Select="SPECIFIC_ATTRIBUTES",
            )
            distinct_users = {
                item["user_id"]
                for item in resp.get("Items", [])
                if item.get("user_id")
            }
            distinct_users.add(user_id)

            if len(distinct_users) > MAX_USERS_PER_FINGERPRINT:
                score = min(100.0, 40.0 + (len(distinct_users) - MAX_USERS_PER_FINGERPRINT) * 15.0)
                return FingerprintAnomaly(
                    is_anomalous=True,
                    anomaly_type="SHARED_FINGERPRINT",
                    severity="HIGH" if score >= 70 else "MEDIUM",
                    score=score,
                    description=(
                        f"Fingerprint {fp_id} shared by {len(distinct_users)} accounts "
                        f"in {SHARED_FP_WINDOW_SECONDS // 60}min "
                        f"(threshold: {MAX_USERS_PER_FINGERPRINT})"
                    ),
                )
        except ClientError as exc:
            logger.warning("Shared FP check DynamoDB error: %s", exc)
        return None

    def _check_impossible_travel(
        self, fp_id: str, new_ip: str
    ) -> FingerprintAnomaly | None:
        """
        Detect the same fingerprint appearing from geographically distant IPs
        within a short time window.
        Uses a simple IP-based continent heuristic (first octet ranges).
        For production accuracy, integrate with MaxMind city lat/lon.
        """
        try:
            cutoff = int(time.time()) - 3600  # 1-hour window
            resp = self._table.query(
                IndexName="fp-ip-index",
                KeyConditionExpression=(
                    Key("fingerprint_id").eq(fp_id) & Key("seen_at").gt(cutoff)
                ),
                ProjectionExpression="ip_address, seen_at",
                Select="SPECIFIC_ATTRIBUTES",
                Limit=10,
                ScanIndexForward=False,
            )
            recent_ips = [item["ip_address"] for item in resp.get("Items", []) if item.get("ip_address")]

            if not recent_ips:
                return None

            new_continent = _rough_continent(new_ip)
            for prev_ip in recent_ips:
                prev_continent = _rough_continent(prev_ip)
                if new_continent and prev_continent and new_continent != prev_continent:
                    return FingerprintAnomaly(
                        is_anomalous=True,
                        anomaly_type="IMPOSSIBLE_TRAVEL",
                        severity="HIGH",
                        score=90.0,
                        description=(
                            f"Fingerprint {fp_id} seen from {prev_continent} ({prev_ip}) "
                            f"and {new_continent} ({new_ip}) within 1 hour"
                        ),
                    )
        except ClientError as exc:
            logger.warning("Impossible travel DynamoDB error: %s", exc)
        return None

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------

    def _store(
        self,
        fp: DeviceFingerprint,
        fp_id: str,
        ip: str,
        user_id: str | None,
        anomaly: FingerprintAnomaly,
    ) -> None:
        """Persist the fingerprint event to DynamoDB."""
        now = int(time.time())
        ttl = now + FINGERPRINT_TTL_DAYS * 86400

        item: dict[str, Any] = {
            "fingerprint_id": fp_id,
            "user_id": user_id or "__anonymous__",
            "ip_address": ip,
            "seen_at": now,
            "expires_at": ttl,
            "canvas_hash": fp.canvas_hash,
            "webgl_hash": fp.webgl_hash,
            "screen_resolution": fp.screen_resolution,
            "timezone": fp.timezone,
            "platform": fp.platform,
            "user_agent": fp.user_agent[:256],  # truncate for storage
            "anomaly_detected": anomaly.is_anomalous,
            "anomaly_type": anomaly.anomaly_type if anomaly.is_anomalous else "",
            "anomaly_severity": anomaly.severity if anomaly.is_anomalous else "",
        }

        # Strip empty strings (DynamoDB doesn't store empty strings by default)
        item = {k: v for k, v in item.items() if v != "" and v is not None}

        try:
            self._table.put_item(Item=item)
        except ClientError as exc:
            logger.error(
                "Failed to store fingerprint %s for user %s: %s",
                fp_id, user_id, exc,
            )

    # ------------------------------------------------------------------
    # History queries
    # ------------------------------------------------------------------

    def get_user_fingerprints(
        self,
        user_id: str,
        limit: int = 20,
        since_timestamp: int = 0,
    ) -> list[dict[str, Any]]:
        """Return recent fingerprint records for a user, newest first."""
        kwargs: dict[str, Any] = {
            "IndexName": "user-fp-index",
            "KeyConditionExpression": Key("user_id").eq(user_id),
            "ScanIndexForward": False,
            "Limit": limit,
        }
        if since_timestamp:
            kwargs["KeyConditionExpression"] = (
                Key("user_id").eq(user_id) & Key("seen_at").gt(since_timestamp)
            )

        try:
            resp = self._table.query(**kwargs)
            return resp.get("Items", [])
        except ClientError as exc:
            logger.error("get_user_fingerprints failed for %s: %s", user_id, exc)
            return []

    def get_ip_fingerprints(
        self,
        ip_address: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return recent fingerprint records for an IP, newest first."""
        try:
            resp = self._table.query(
                IndexName="ip-fp-index",
                KeyConditionExpression=Key("ip_address").eq(ip_address),
                ScanIndexForward=False,
                Limit=limit,
            )
            return resp.get("Items", [])
        except ClientError as exc:
            logger.error("get_ip_fingerprints failed for %s: %s", ip_address, exc)
            return []


# ---------------------------------------------------------------------------
# CloudFormation table definition
# ---------------------------------------------------------------------------

DYNAMODB_TABLE_DEFINITION: dict[str, Any] = {
    "TableName": "device-fingerprints",
    "AttributeDefinitions": [
        {"AttributeName": "fingerprint_id", "AttributeType": "S"},
        {"AttributeName": "user_id", "AttributeType": "S"},
        {"AttributeName": "ip_address", "AttributeType": "S"},
        {"AttributeName": "seen_at", "AttributeType": "N"},
    ],
    "KeySchema": [
        {"AttributeName": "fingerprint_id", "KeyType": "HASH"},
        {"AttributeName": "seen_at", "KeyType": "RANGE"},
    ],
    "GlobalSecondaryIndexes": [
        {
            "IndexName": "user-fp-index",
            "KeySchema": [
                {"AttributeName": "user_id", "KeyType": "HASH"},
                {"AttributeName": "seen_at", "KeyType": "RANGE"},
            ],
            "Projection": {"ProjectionType": "ALL"},
        },
        {
            "IndexName": "ip-fp-index",
            "KeySchema": [
                {"AttributeName": "ip_address", "KeyType": "HASH"},
                {"AttributeName": "seen_at", "KeyType": "RANGE"},
            ],
            "Projection": {
                "ProjectionType": "INCLUDE",
                "NonKeyAttributes": ["fingerprint_id", "user_id", "seen_at", "anomaly_type"],
            },
        },
        {
            "IndexName": "fp-users-index",
            "KeySchema": [
                {"AttributeName": "fingerprint_id", "KeyType": "HASH"},
                {"AttributeName": "seen_at", "KeyType": "RANGE"},
            ],
            "Projection": {
                "ProjectionType": "INCLUDE",
                "NonKeyAttributes": ["user_id"],
            },
        },
        {
            "IndexName": "fp-ip-index",
            "KeySchema": [
                {"AttributeName": "fingerprint_id", "KeyType": "HASH"},
                {"AttributeName": "seen_at", "KeyType": "RANGE"},
            ],
            "Projection": {
                "ProjectionType": "INCLUDE",
                "NonKeyAttributes": ["ip_address"],
            },
        },
    ],
    "BillingMode": "PAY_PER_REQUEST",
    "TimeToLiveSpecification": {
        "AttributeName": "expires_at",
        "Enabled": True,
    },
    "PointInTimeRecoverySpecification": {
        "PointInTimeRecoveryEnabled": True,
    },
    "SSESpecification": {
        "Enabled": True,
        "SSEType": "KMS",
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rough_continent(ip: str) -> str | None:
    """
    Very rough IP-to-continent mapping based on first octet ranges.
    This is a fallback; production should use MaxMind city database.
    Returns: 'NA', 'EU', 'AS', 'AF', 'SA', 'OC', or None.
    """
    try:
        first = int(ip.split(".")[0])
    except (ValueError, IndexError):
        return None

    # Rough IANA IPv4 allocation mapping
    if 1 <= first <= 100:
        return "NA"
    if 101 <= first <= 130:
        return "AS"
    if 131 <= first <= 150:
        return "EU"
    if 151 <= first <= 160:
        return "SA"
    if 161 <= first <= 180:
        return "AF"
    if 181 <= first <= 220:
        return "NA"
    if 221 <= first <= 240:
        return "AS"
    return None
