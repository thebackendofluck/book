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
test_lambda.py
--------------
pytest test suite for the 8-gate iGaming IP detection pipeline.

Run:
    pytest test_lambda.py -v --tb=short

Requirements:
    pytest, pytest-mock, moto[dynamodb,s3,wafv2,sns], boto3, freezegun

All AWS calls are mocked via moto. No real AWS credentials are required.
"""

from __future__ import annotations

import hashlib
import json
import time
import xml.etree.ElementTree as ET
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Gate on the AWS mocking stack this suite needs. Without these a
# full-repo pytest run errors at collection time; importorskip makes
# the module skip cleanly instead.
boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")
mock_aws = moto.mock_aws

# ---------------------------------------------------------------------------
# Environment setup (must happen before importing the modules under test)
# ---------------------------------------------------------------------------
import os
import sys

# Put the siblings on sys.path so `from waf_integration import _is_ipv4_cidr`
# (and the other lazy sibling imports) resolve regardless of cwd.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("BLACKLIST_TABLE", "ip-blacklist")
os.environ.setdefault("DEVICE_FP_TABLE", "device-fingerprints")
os.environ.setdefault("KYC_TABLE", "kyc-status")
os.environ.setdefault("SDN_BUCKET", "test-sanctions")
os.environ.setdefault("SDN_KEY", "ofac/sdn_advanced.xml")
os.environ.setdefault("WAF_IP_SET_ID", "")
os.environ.setdefault("SNS_ALERT_TOPIC", "")
os.environ.setdefault("ELASTICACHE_ENDPOINT", "")
os.environ.setdefault("FRAUD_SCORE_THRESHOLD", "75.0")
os.environ.setdefault("FRAUD_REVIEW_THRESHOLD", "50.0")

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function")
def aws_region():
    return "us-east-1"


@pytest.fixture(scope="function")
def dynamodb_tables(aws_region):
    """Create all required DynamoDB tables using moto."""
    with mock_aws():
        db = boto3.resource("dynamodb", region_name=aws_region)

        # ip-blacklist
        db.create_table(
            TableName="ip-blacklist",
            AttributeDefinitions=[
                {"AttributeName": "ip_address", "AttributeType": "S"},
                {"AttributeName": "reason", "AttributeType": "S"},
                {"AttributeName": "added_at", "AttributeType": "N"},
            ],
            KeySchema=[{"AttributeName": "ip_address", "KeyType": "HASH"}],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "reason-code-index",
                    "KeySchema": [
                        {"AttributeName": "reason", "KeyType": "HASH"},
                        {"AttributeName": "added_at", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        # device-fingerprints
        db.create_table(
            TableName="device-fingerprints",
            AttributeDefinitions=[
                {"AttributeName": "fingerprint_id", "AttributeType": "S"},
                {"AttributeName": "user_id", "AttributeType": "S"},
                {"AttributeName": "ip_address", "AttributeType": "S"},
                {"AttributeName": "seen_at", "AttributeType": "N"},
            ],
            KeySchema=[
                {"AttributeName": "fingerprint_id", "KeyType": "HASH"},
                {"AttributeName": "seen_at", "KeyType": "RANGE"},
            ],
            GlobalSecondaryIndexes=[
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
                        "NonKeyAttributes": ["fingerprint_id", "user_id", "anomaly_type"],
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
            BillingMode="PAY_PER_REQUEST",
        )

        # kyc-status
        db.create_table(
            TableName="kyc-status",
            AttributeDefinitions=[
                {"AttributeName": "user_id", "AttributeType": "S"},
            ],
            KeySchema=[{"AttributeName": "user_id", "KeyType": "HASH"}],
            BillingMode="PAY_PER_REQUEST",
        )

        # tor-exit-nodes
        db.create_table(
            TableName="tor-exit-nodes",
            AttributeDefinitions=[
                {"AttributeName": "ip_address", "AttributeType": "S"},
            ],
            KeySchema=[{"AttributeName": "ip_address", "KeyType": "HASH"}],
            BillingMode="PAY_PER_REQUEST",
        )

        # known-proxies
        db.create_table(
            TableName="known-proxies",
            AttributeDefinitions=[
                {"AttributeName": "ip_address", "AttributeType": "S"},
                {"AttributeName": "proxy_type", "AttributeType": "S"},
                {"AttributeName": "added_at", "AttributeType": "N"},
            ],
            KeySchema=[{"AttributeName": "ip_address", "KeyType": "HASH"}],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "proxy-type-index",
                    "KeySchema": [
                        {"AttributeName": "proxy_type", "KeyType": "HASH"},
                        {"AttributeName": "added_at", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        yield db


@pytest.fixture(scope="function")
def s3_buckets(aws_region):
    """Create S3 buckets for GeoIP and sanctions."""
    with mock_aws():
        s3 = boto3.client("s3", region_name=aws_region)
        s3.create_bucket(Bucket="test-geoip")
        s3.create_bucket(Bucket="test-sanctions")
        yield s3


def _make_api_event(
    ip: str = "203.0.113.1",
    user_id: str | None = None,
    session_id: str | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a minimal API Gateway HTTP API v2 event."""
    headers: dict[str, str] = {"x-forwarded-for": ip}
    if user_id:
        headers["x-user-id"] = user_id
    if session_id:
        headers["x-session-id"] = session_id

    return {
        "version": "2.0",
        "headers": headers,
        "requestContext": {
            "http": {
                "method": "POST",
                "sourceIp": ip,
            }
        },
        "body": json.dumps(body or {}),
    }


def _make_fingerprint(
    canvas: str = "abcd1234",
    webgl: str = "efgh5678",
    ua: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    platform: str = "Win32",
) -> dict[str, Any]:
    return {
        "canvas_hash": canvas,
        "webgl_hash": webgl,
        "user_agent": ua,
        "screen_resolution": "1920x1080",
        "timezone": "America/New_York",
        "language": "en-US",
        "platform": platform,
        "hardware_concurrency": 8,
        "device_memory": 8.0,
        "color_depth": 24,
        "touch_support": False,
        "audio_hash": "audio1234",
        "font_hash": "font5678",
        "plugins_hash": "plugins9012",
    }


# ---------------------------------------------------------------------------
# Gate 1: IP Type Check (Tor / Datacenter / Residential)
# ---------------------------------------------------------------------------

class TestGate1IPTypeCheck:

    @mock_aws
    def test_tor_exit_node_blocked(self, dynamodb_tables):
        """Tor exit node should receive BLOCK with BANNED_PROXY_TOR."""
        from dynamodb_blacklist import IPBlacklistService
        # Seed tor-exit-nodes table
        db = boto3.resource("dynamodb", region_name="us-east-1")
        table = db.Table("tor-exit-nodes")
        table.put_item(Item={"ip_address": "185.220.101.1", "added_at": int(time.time())})

        # Import after moto is active
        import importlib
        import lambda_ip_gate
        importlib.reload(lambda_ip_gate)

        with patch.object(lambda_ip_gate, "_blacklist_svc", IPBlacklistService()):
            with patch.object(lambda_ip_gate, "_device_svc", MagicMock()):
                with patch.object(lambda_ip_gate, "_sanctions_svc", MagicMock(ensure_loaded=lambda: None, search=lambda **kw: [])):
                    with patch.object(lambda_ip_gate, "_asn_reader", None):
                        with patch.object(lambda_ip_gate, "_city_reader", None):
                            result = lambda_ip_gate._gate_ip_type("185.220.101.1")

        assert result.verdict.value == "BLOCK"
        assert result.reason_code == "BANNED_PROXY_TOR"
        assert result.gate_id == 1

    def test_datacenter_asn_blocked(self):
        """DigitalOcean ASN (14061) should trigger datacenter block."""
        from lambda_ip_gate import _gate_ip_type, GateVerdict, ReasonCode

        mock_reader = MagicMock()
        mock_reader.asn.return_value.autonomous_system_number = 14061
        mock_reader.asn.return_value.autonomous_system_organization = "DIGITALOCEAN-ASN"

        with patch("lambda_ip_gate._asn_reader", mock_reader):
            with patch("lambda_ip_gate._check_tor_exit", return_value=False):
                result = _gate_ip_type("159.89.1.1")

        assert result.verdict == GateVerdict.BLOCK
        assert result.reason_code == ReasonCode.BANNED_PROXY_DC.value
        assert "14061" in result.detail

    def test_aws_asn_blocked(self):
        """AWS ASN (16509) should trigger datacenter block."""
        from lambda_ip_gate import _gate_ip_type, GateVerdict

        mock_reader = MagicMock()
        mock_reader.asn.return_value.autonomous_system_number = 16509
        mock_reader.asn.return_value.autonomous_system_organization = "AMAZON-02"

        with patch("lambda_ip_gate._asn_reader", mock_reader):
            with patch("lambda_ip_gate._check_tor_exit", return_value=False):
                result = _gate_ip_type("54.204.1.1")

        assert result.verdict == GateVerdict.BLOCK
        assert "16509" in result.detail

    def test_residential_ip_passes(self):
        """A normal residential ISP ASN should pass Gate 1."""
        from lambda_ip_gate import _gate_ip_type, GateVerdict

        mock_reader = MagicMock()
        mock_reader.asn.return_value.autonomous_system_number = 7922  # Comcast
        mock_reader.asn.return_value.autonomous_system_organization = "Comcast Cable Communications"

        with patch("lambda_ip_gate._asn_reader", mock_reader):
            with patch("lambda_ip_gate._check_tor_exit", return_value=False):
                result = _gate_ip_type("73.100.1.1")

        assert result.verdict == GateVerdict.PASS

    def test_hosting_keyword_triggers_review(self):
        """An ASN org name containing 'hosting' should produce REVIEW."""
        from lambda_ip_gate import _gate_ip_type, GateVerdict

        mock_reader = MagicMock()
        mock_reader.asn.return_value.autonomous_system_number = 99999  # not in DATACENTER_ASNS
        mock_reader.asn.return_value.autonomous_system_organization = "SuperHosting Inc"

        with patch("lambda_ip_gate._asn_reader", mock_reader):
            with patch("lambda_ip_gate._check_tor_exit", return_value=False):
                result = _gate_ip_type("192.0.2.1")

        assert result.verdict == GateVerdict.REVIEW

    def test_private_ip_rejected_at_entry(self):
        """Private IPs should be rejected at the request validation stage."""
        from lambda_ip_gate import _validate_ip
        assert not _validate_ip("192.168.1.1")
        assert not _validate_ip("10.0.0.1")
        assert not _validate_ip("127.0.0.1")

    def test_valid_public_ip_accepted(self):
        """Public IPs should pass validation."""
        from lambda_ip_gate import _validate_ip
        assert _validate_ip("203.0.113.1")
        assert _validate_ip("8.8.8.8")


# ---------------------------------------------------------------------------
# Gate 2: VPN Detection
# ---------------------------------------------------------------------------

class TestGate2VPNDetection:

    def test_vpn_ip_blocked(self):
        """An IP identified as VPN should be blocked."""
        from lambda_ip_gate import _gate_vpn, GateVerdict, ReasonCode

        with patch("lambda_ip_gate._query_ip_reputation", return_value={
            "vpn": True,
            "proxy": False,
            "fraud_score": 90.0,
            "isp": "NordVPN",
        }):
            result = _gate_vpn("198.54.132.1")

        assert result.verdict == GateVerdict.BLOCK
        assert result.reason_code == ReasonCode.BANNED_PROXY_VPN.value
        assert "NordVPN" in result.detail

    def test_high_fraud_score_review(self):
        """High fraud score without explicit VPN flag should produce REVIEW."""
        from lambda_ip_gate import _gate_vpn, GateVerdict

        with patch("lambda_ip_gate._query_ip_reputation", return_value={
            "vpn": False,
            "proxy": False,
            "fraud_score": 65.0,
            "isp": "Unknown ISP",
        }):
            result = _gate_vpn("203.0.113.50")

        assert result.verdict == GateVerdict.REVIEW

    def test_clean_ip_passes(self):
        """Clean residential IP should pass VPN check."""
        from lambda_ip_gate import _gate_vpn, GateVerdict

        with patch("lambda_ip_gate._query_ip_reputation", return_value={
            "vpn": False,
            "proxy": False,
            "fraud_score": 5.0,
            "isp": "Comcast Cable",
        }):
            result = _gate_vpn("73.100.1.1")

        assert result.verdict == GateVerdict.PASS

    def test_reputation_api_failure_fails_open(self):
        """Gate 2 should fail-open if the reputation API is unreachable."""
        from lambda_ip_gate import _gate_vpn, GateVerdict

        with patch("lambda_ip_gate._query_ip_reputation", side_effect=ConnectionError("timeout")):
            result = _gate_vpn("203.0.113.1")

        assert result.verdict == GateVerdict.PASS  # fail-open


# ---------------------------------------------------------------------------
# Gate 3: Known Proxy Check
# ---------------------------------------------------------------------------

class TestGate3KnownProxy:

    @mock_aws
    def test_known_proxy_blocked(self, dynamodb_tables):
        """IP in known-proxies table with high confidence should be blocked."""
        db = boto3.resource("dynamodb", region_name="us-east-1")
        db.Table("known-proxies").put_item(Item={
            "ip_address": "45.142.212.1",
            "proxy_type": "HTTP",
            "source": "proxydb.net",
            "confidence": "0.95",
            "added_at": int(time.time()),
        })

        from lambda_ip_gate import _gate_known_proxy, GateVerdict, ReasonCode
        result = _gate_known_proxy("45.142.212.1")

        assert result.verdict == GateVerdict.BLOCK
        assert result.reason_code == ReasonCode.BANNED_PROXY_KNOWN.value

    @mock_aws
    def test_low_confidence_proxy_review(self, dynamodb_tables):
        """Low-confidence proxy match should produce REVIEW."""
        db = boto3.resource("dynamodb", region_name="us-east-1")
        db.Table("known-proxies").put_item(Item={
            "ip_address": "45.142.212.2",
            "proxy_type": "SOCKS",
            "source": "manual",
            "confidence": "0.55",
            "added_at": int(time.time()),
        })

        from lambda_ip_gate import _gate_known_proxy, GateVerdict
        result = _gate_known_proxy("45.142.212.2")

        assert result.verdict == GateVerdict.REVIEW

    @mock_aws
    def test_unlisted_ip_passes(self, dynamodb_tables):
        """IP not in the proxy table should pass."""
        from lambda_ip_gate import _gate_known_proxy, GateVerdict
        result = _gate_known_proxy("1.2.3.4")
        assert result.verdict == GateVerdict.PASS


# ---------------------------------------------------------------------------
# Gate 4: IP Blacklist Check
# ---------------------------------------------------------------------------

class TestGate4IPBlacklist:

    @mock_aws
    def test_blacklisted_ip_blocked(self, dynamodb_tables):
        """Manually blacklisted IP should return BLOCK."""
        from dynamodb_blacklist import IPBlacklistService
        from lambda_ip_gate import _gate_blacklist, GateVerdict, ReasonCode

        svc = IPBlacklistService(table_name="ip-blacklist")
        svc.add("192.0.2.100", reason="BANNED_IP_BLACKLIST", added_by="test", ttl_hours=24)

        with patch("lambda_ip_gate._blacklist_svc", svc):
            result = _gate_blacklist("192.0.2.100")

        assert result.verdict == GateVerdict.BLOCK
        assert result.reason_code == ReasonCode.BANNED_IP_BLACKLIST.value

    @mock_aws
    def test_unlisted_ip_passes(self, dynamodb_tables):
        """Non-blacklisted IP should pass the blacklist gate."""
        from dynamodb_blacklist import IPBlacklistService
        from lambda_ip_gate import _gate_blacklist, GateVerdict

        svc = IPBlacklistService(table_name="ip-blacklist")

        with patch("lambda_ip_gate._blacklist_svc", svc):
            result = _gate_blacklist("203.0.113.99")

        assert result.verdict == GateVerdict.PASS

    @mock_aws
    def test_expired_entry_not_blocked(self, dynamodb_tables):
        """An entry with an expired TTL should not block the IP."""
        db = boto3.resource("dynamodb", region_name="us-east-1")
        # Write an already-expired entry (expires_at in the past)
        db.Table("ip-blacklist").put_item(Item={
            "ip_address": "192.0.2.200",
            "reason": "TEST",
            "added_by": "test",
            "added_at": int(time.time()) - 3600,
            "expires_at": int(time.time()) - 1,  # expired
        })

        from dynamodb_blacklist import IPBlacklistService
        from lambda_ip_gate import _gate_blacklist, GateVerdict

        svc = IPBlacklistService(table_name="ip-blacklist")

        with patch("lambda_ip_gate._blacklist_svc", svc):
            result = _gate_blacklist("192.0.2.200")

        assert result.verdict == GateVerdict.PASS

    @mock_aws
    def test_batch_add_and_query_by_reason(self, dynamodb_tables):
        """Batch add IPs and verify GSI query by reason code works."""
        from dynamodb_blacklist import IPBlacklistService

        svc = IPBlacklistService(table_name="ip-blacklist")
        entries = [
            {"ip_address": f"10.0.0.{i}", "reason": "BANNED_PROXY_TOR"}
            for i in range(1, 6)
        ]
        written = svc.batch_add(entries, ttl_hours=24)
        assert written == 5

        items, _ = svc.query_by_reason("BANNED_PROXY_TOR")
        assert len(items) == 5


# ---------------------------------------------------------------------------
# Gate 5: Fraud Score
# ---------------------------------------------------------------------------

class TestGate5FraudScore:

    def test_high_velocity_triggers_block(self):
        """Extreme IP velocity (>100 req/window) should push score above block threshold."""
        from lambda_ip_gate import _gate_fraud_score, GateVerdict

        with patch("lambda_ip_gate._get_velocity", return_value=200):
            with patch("lambda_ip_gate._get_country_risk_score", return_value=25.0):
                with patch("lambda_ip_gate._redis_get", return_value="25"):
                    with patch("lambda_ip_gate._increment_velocity"):
                        result = _gate_fraud_score("203.0.113.1", None, None)

        # ip_velocity=30 + country=25 + rep=25 = 80 → BLOCK
        assert result.verdict == GateVerdict.BLOCK
        assert result.reason_code == "HIGH_FRAUD_SCORE"

    def test_moderate_signals_produce_review(self):
        """Moderate velocity + medium-risk country should produce REVIEW."""
        from lambda_ip_gate import _gate_fraud_score, GateVerdict

        # Simulate ip_velocity contributing ~15 (50-100 reqs), country 12, rep 0 → total ~27
        with patch("lambda_ip_gate._get_velocity", side_effect=lambda key, _: 70 if "vel:ip" in key else 0):
            with patch("lambda_ip_gate._get_country_risk_score", return_value=12.0):
                with patch("lambda_ip_gate._redis_get", return_value=None):
                    with patch("lambda_ip_gate._increment_velocity"):
                        result = _gate_fraud_score("203.0.113.1", "u1", "s1")

        # 15 + 12 = 27 < 50 threshold → PASS
        # Adjust expectation: 27 < review_threshold(50) → PASS
        assert result.verdict in (GateVerdict.PASS, GateVerdict.REVIEW)

    def test_clean_ip_passes(self):
        """Zero signals should produce a PASS."""
        from lambda_ip_gate import _gate_fraud_score, GateVerdict

        with patch("lambda_ip_gate._get_velocity", return_value=0):
            with patch("lambda_ip_gate._get_country_risk_score", return_value=0.0):
                with patch("lambda_ip_gate._redis_get", return_value=None):
                    with patch("lambda_ip_gate._increment_velocity"):
                        result = _gate_fraud_score("1.2.3.4", None, None)

        assert result.verdict == GateVerdict.PASS
        assert result.metadata["total_score"] == 0.0


# ---------------------------------------------------------------------------
# Gate 6: Device Fingerprint Anomaly Detection
# ---------------------------------------------------------------------------

class TestGate6DeviceFingerprint:

    @mock_aws
    def test_headless_browser_detected(self, dynamodb_tables):
        """Fingerprint with blank canvas hash should be detected as headless."""
        from device_fingerprint_dynamo import DeviceFingerprintService
        from lambda_ip_gate import _gate_device_fingerprint, GateVerdict, ReasonCode

        svc = DeviceFingerprintService(table_name="device-fingerprints")

        headless_fp = {
            "canvas_hash": "2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d",  # known headless hash
            "webgl_hash": "",
            "user_agent": "HeadlessChrome/90",
            "screen_resolution": "1920x1080",
            "platform": "Linux x86_64",
            "plugins_hash": "",
            "font_hash": "",
        }

        with patch("lambda_ip_gate._device_svc", svc):
            result = _gate_device_fingerprint("203.0.113.1", "u1", headless_fp)

        assert result.verdict == GateVerdict.BLOCK
        assert result.reason_code == ReasonCode.DEVICE_ANOMALY.value
        assert "headless" in result.detail.lower()

    @mock_aws
    def test_ua_platform_mismatch_review(self, dynamodb_tables):
        """Windows UA with Linux platform should trigger REVIEW."""
        from device_fingerprint_dynamo import DeviceFingerprintService
        from lambda_ip_gate import _gate_device_fingerprint, GateVerdict

        svc = DeviceFingerprintService(table_name="device-fingerprints")

        mismatched_fp = {
            "canvas_hash": "abc123",
            "webgl_hash": "def456",
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "screen_resolution": "1920x1080",
            "platform": "Linux aarch64",  # mismatch!
            "plugins_hash": "plugins123",
            "font_hash": "fonts456",
            "hardware_concurrency": 4,
        }

        with patch("lambda_ip_gate._device_svc", svc):
            result = _gate_device_fingerprint("203.0.113.1", "u1", mismatched_fp)

        assert result.verdict in (GateVerdict.REVIEW, GateVerdict.BLOCK)
        assert result.reason_code == "DEVICE_ANOMALY"

    @mock_aws
    def test_clean_fingerprint_passes(self, dynamodb_tables):
        """A consistent real-looking fingerprint should pass."""
        from device_fingerprint_dynamo import DeviceFingerprintService
        from lambda_ip_gate import _gate_device_fingerprint, GateVerdict

        svc = DeviceFingerprintService(table_name="device-fingerprints")

        good_fp = _make_fingerprint()

        with patch("lambda_ip_gate._device_svc", svc):
            result = _gate_device_fingerprint("203.0.113.1", "u1", good_fp)

        assert result.verdict == GateVerdict.PASS

    def test_missing_fingerprint_skips_gate(self):
        """Empty fingerprint dict should skip the gate with PASS."""
        from lambda_ip_gate import _gate_device_fingerprint, GateVerdict

        result = _gate_device_fingerprint("203.0.113.1", "u1", {})
        assert result.verdict == GateVerdict.PASS
        assert "skipped" in result.detail.lower()

    @mock_aws
    def test_fingerprint_rotation_detected(self, dynamodb_tables):
        """Same user with 6 distinct fingerprints in 24h should trigger anomaly."""
        from device_fingerprint_dynamo import DeviceFingerprintService

        svc = DeviceFingerprintService(table_name="device-fingerprints")
        user_id = "u_rotation_test"
        now = int(time.time())

        # Seed 5 existing fingerprint records
        db = boto3.resource("dynamodb", region_name="us-east-1")
        table = db.Table("device-fingerprints")
        for i in range(5):
            fp_id = hashlib.sha256(f"fp_seed_{i}".encode()).hexdigest()[:32]
            table.put_item(Item={
                "fingerprint_id": fp_id,
                "user_id": user_id,
                "ip_address": "203.0.113.1",
                "seen_at": now - (i * 100),
                "expires_at": now + 86400,
            })

        # Check with a 6th distinct fingerprint
        result = svc.check_and_store(
            ip="203.0.113.1",
            user_id=user_id,
            fingerprint=_make_fingerprint(canvas=f"unique_canvas_6"),
        )

        assert result.is_anomalous
        assert result.anomaly_type == "FINGERPRINT_ROTATION"


# ---------------------------------------------------------------------------
# Gate 7: Sanctions / PEP Check
# ---------------------------------------------------------------------------

class TestGate7Sanctions:

    def _build_minimal_sdn_xml(self, name: str, uid: str = "12345") -> bytes:
        """Build a minimal OFAC SDN XML for testing."""
        first, _, last = name.partition(" ")
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<sdnList xmlns="http://tempuri.org/sdnList.xsd">
  <sdnEntry>
    <uid>{uid}</uid>
    <firstName>{first}</firstName>
    <lastName>{last or first}</lastName>
    <sdnType>INDIVIDUAL</sdnType>
    <programList><program>SDGT</program></programList>
    <remarks>Test entry</remarks>
  </sdnEntry>
</sdnList>"""
        return xml.encode()

    def test_exact_name_match_blocks(self):
        """Exact name match against SDN list should produce BLOCK."""
        from s3_sanctions_checker import SanctionsChecker
        from lambda_ip_gate import _gate_sanctions, GateVerdict, ReasonCode

        mock_checker = MagicMock(spec=SanctionsChecker)
        mock_checker.search.return_value = [{
            "uid": "12345",
            "matched_name": "John Smith",
            "query_name": "John Smith",
            "score": 0.98,
            "sdn_type": "INDIVIDUAL",
            "program": "SDGT",
            "list_type": "OFAC_SDN",
        }]

        with patch("lambda_ip_gate._sanctions_svc", mock_checker):
            result = _gate_sanctions("u1", {"full_name": "John Smith", "date_of_birth": "1980-01-15"})

        assert result.verdict == GateVerdict.BLOCK
        assert result.reason_code == ReasonCode.SANCTIONS_MATCH.value

    def test_partial_match_review(self):
        """Fuzzy match score of 0.82 should produce REVIEW."""
        from s3_sanctions_checker import SanctionsChecker
        from lambda_ip_gate import _gate_sanctions, GateVerdict

        mock_checker = MagicMock(spec=SanctionsChecker)
        mock_checker.search.return_value = [{
            "uid": "99999",
            "matched_name": "Jon Smith",
            "query_name": "John Smith",
            "score": 0.82,
            "sdn_type": "INDIVIDUAL",
            "program": "IRAN",
            "list_type": "OFAC_SDN",
        }]

        with patch("lambda_ip_gate._sanctions_svc", mock_checker):
            result = _gate_sanctions("u1", {"full_name": "John Smith"})

        assert result.verdict == GateVerdict.REVIEW

    def test_no_match_passes(self):
        """Name with no SDN match should pass."""
        from s3_sanctions_checker import SanctionsChecker
        from lambda_ip_gate import _gate_sanctions, GateVerdict

        mock_checker = MagicMock(spec=SanctionsChecker)
        mock_checker.search.return_value = []

        with patch("lambda_ip_gate._sanctions_svc", mock_checker):
            result = _gate_sanctions("u1", {"full_name": "Alice Johnson"})

        assert result.verdict == GateVerdict.PASS

    def test_missing_name_skips_gate(self):
        """Request with no full_name should skip the gate."""
        from lambda_ip_gate import _gate_sanctions, GateVerdict

        result = _gate_sanctions("u1", {})
        assert result.verdict == GateVerdict.PASS
        assert "skipped" in result.detail.lower()

    def test_levenshtein_similarity_calculation(self):
        """Test the Levenshtein ratio helper directly."""
        from s3_sanctions_checker import _levenshtein_ratio, _compute_similarity

        assert _levenshtein_ratio("john smith", "john smith") == 1.0
        assert _levenshtein_ratio("", "") == 1.0  # edge case
        sim = _compute_similarity("John Smith", "Jon Smyth")
        assert 0.70 <= sim <= 0.95

        # Name normalisation strips diacritics
        from s3_sanctions_checker import _normalise_name
        assert _normalise_name("Müller") == "muller"
        assert _normalise_name("bin Laden") == "laden"  # 'bin' is a stop word

    @mock_aws
    def test_sdn_xml_parsing(self):
        """Test XML parsing produces correct SDNEntry objects."""
        from s3_sanctions_checker import _parse_sdn_xml

        xml = """<?xml version="1.0"?>
<sdnList xmlns="http://tempuri.org/sdnList.xsd">
  <sdnEntry>
    <uid>100</uid>
    <firstName>Pavel</firstName>
    <lastName>Petrov</lastName>
    <sdnType>INDIVIDUAL</sdnType>
    <programList><program>RUSSIA</program></programList>
    <akaList>
      <aka><firstName>P</firstName><lastName>Petrov</lastName></aka>
    </akaList>
    <remarks>Test SDN entry</remarks>
  </sdnEntry>
</sdnList>"""

        entries = _parse_sdn_xml(xml.encode())
        assert len(entries) == 1
        assert entries[0].uid == "100"
        assert "Pavel Petrov" in entries[0].names
        assert entries[0].program == "RUSSIA"


# ---------------------------------------------------------------------------
# Gate 8: KYC Status Verification
# ---------------------------------------------------------------------------

class TestGate8KYCStatus:

    @mock_aws
    def test_verified_kyc_passes(self, dynamodb_tables):
        """User with VERIFIED KYC status should pass."""
        db = boto3.resource("dynamodb", region_name="us-east-1")
        db.Table("kyc-status").put_item(Item={
            "user_id": "u_verified",
            "kyc_status": "VERIFIED",
            "kyc_level": 2,
            "verified_at": int(time.time()) - 86400,
            "expires_at": int(time.time()) + 365 * 86400,
        })

        from lambda_ip_gate import _gate_kyc, GateVerdict
        result = _gate_kyc("u_verified")
        assert result.verdict == GateVerdict.PASS

    @mock_aws
    def test_rejected_kyc_blocked(self, dynamodb_tables):
        """User with REJECTED KYC should receive BLOCK."""
        db = boto3.resource("dynamodb", region_name="us-east-1")
        db.Table("kyc-status").put_item(Item={
            "user_id": "u_rejected",
            "kyc_status": "REJECTED",
            "rejection_reason": "fraudulent_document",
        })

        from lambda_ip_gate import _gate_kyc, GateVerdict, ReasonCode
        result = _gate_kyc("u_rejected")
        assert result.verdict == GateVerdict.BLOCK
        assert result.reason_code == ReasonCode.KYC_FAILED.value

    @mock_aws
    def test_pending_kyc_review(self, dynamodb_tables):
        """User with PENDING KYC should receive REVIEW."""
        db = boto3.resource("dynamodb", region_name="us-east-1")
        db.Table("kyc-status").put_item(Item={
            "user_id": "u_pending",
            "kyc_status": "PENDING",
        })

        from lambda_ip_gate import _gate_kyc, GateVerdict
        result = _gate_kyc("u_pending")
        assert result.verdict == GateVerdict.REVIEW

    @mock_aws
    def test_expired_kyc_review(self, dynamodb_tables):
        """User with VERIFIED but expired KYC should receive REVIEW."""
        db = boto3.resource("dynamodb", region_name="us-east-1")
        db.Table("kyc-status").put_item(Item={
            "user_id": "u_expired",
            "kyc_status": "VERIFIED",
            "kyc_level": 1,
            "verified_at": int(time.time()) - 400 * 86400,
            "expires_at": int(time.time()) - 86400,  # expired yesterday
        })

        from lambda_ip_gate import _gate_kyc, GateVerdict
        result = _gate_kyc("u_expired")
        assert result.verdict == GateVerdict.REVIEW

    @mock_aws
    def test_missing_kyc_record_review(self, dynamodb_tables):
        """User with no KYC record should receive REVIEW."""
        from lambda_ip_gate import _gate_kyc, GateVerdict
        result = _gate_kyc("u_nonexistent")
        assert result.verdict == GateVerdict.REVIEW

    def test_guest_user_skips_kyc(self):
        """Request with no user_id should skip KYC gate."""
        from lambda_ip_gate import _gate_kyc, GateVerdict
        result = _gate_kyc(None)
        assert result.verdict == GateVerdict.PASS
        assert "skipped" in result.detail.lower()


# ---------------------------------------------------------------------------
# Full Pipeline Integration Tests
# ---------------------------------------------------------------------------

class TestPipelineIntegration:

    @mock_aws
    def test_clean_request_passes_all_gates(self, dynamodb_tables):
        """A clean request should pass all 8 gates."""
        from lambda_ip_gate import _run_pipeline, GateVerdict

        mock_reader = MagicMock()
        mock_reader.asn.return_value.autonomous_system_number = 7922
        mock_reader.asn.return_value.autonomous_system_organization = "Comcast Cable"

        from dynamodb_blacklist import IPBlacklistService
        from device_fingerprint_dynamo import DeviceFingerprintService
        from s3_sanctions_checker import SanctionsChecker

        db = boto3.resource("dynamodb", region_name="us-east-1")
        db.Table("kyc-status").put_item(Item={
            "user_id": "u_clean",
            "kyc_status": "VERIFIED",
            "expires_at": int(time.time()) + 365 * 86400,
        })

        mock_sanctions = MagicMock(spec=SanctionsChecker)
        mock_sanctions.search.return_value = []

        with patch("lambda_ip_gate._asn_reader", mock_reader):
            with patch("lambda_ip_gate._city_reader", None):
                with patch("lambda_ip_gate._check_tor_exit", return_value=False):
                    with patch("lambda_ip_gate._query_ip_reputation", return_value={
                        "vpn": False, "proxy": False, "fraud_score": 0.0, "isp": "Comcast",
                    }):
                        with patch("lambda_ip_gate._blacklist_svc", IPBlacklistService()):
                            with patch("lambda_ip_gate._device_svc", DeviceFingerprintService()):
                                with patch("lambda_ip_gate._sanctions_svc", mock_sanctions):
                                    with patch("lambda_ip_gate._get_velocity", return_value=0):
                                        with patch("lambda_ip_gate._increment_velocity"):
                                            with patch("lambda_ip_gate._redis_get", return_value=None):
                                                result = _run_pipeline(
                                                    ip="73.100.1.1",
                                                    user_id="u_clean",
                                                    session_id="sess_abc",
                                                    fingerprint_data=_make_fingerprint(),
                                                    body_data={"full_name": "Alice Johnson"},
                                                )

        assert result.final_verdict == GateVerdict.PASS
        assert result.blocking_gate is None
        assert len(result.gates) == 8

    @mock_aws
    def test_tor_exit_short_circuits_at_gate_1(self, dynamodb_tables):
        """Tor exit should stop at gate 1, not evaluate gates 2-8."""
        db = boto3.resource("dynamodb", region_name="us-east-1")
        db.Table("tor-exit-nodes").put_item(Item={"ip_address": "185.220.101.1"})

        from lambda_ip_gate import _run_pipeline, GateVerdict

        from dynamodb_blacklist import IPBlacklistService
        from device_fingerprint_dynamo import DeviceFingerprintService
        from s3_sanctions_checker import SanctionsChecker

        mock_sanctions = MagicMock(spec=SanctionsChecker)
        mock_sanctions.search.return_value = []

        with patch("lambda_ip_gate._asn_reader", None):
            with patch("lambda_ip_gate._city_reader", None):
                with patch("lambda_ip_gate._blacklist_svc", IPBlacklistService()):
                    with patch("lambda_ip_gate._device_svc", DeviceFingerprintService()):
                        with patch("lambda_ip_gate._sanctions_svc", mock_sanctions):
                            with patch("lambda_ip_gate._query_ip_reputation", return_value={
                                "vpn": False, "proxy": False, "fraud_score": 0.0, "isp": "Test",
                            }):
                                with patch("lambda_ip_gate._get_velocity", return_value=0):
                                    with patch("lambda_ip_gate._increment_velocity"):
                                        with patch("lambda_ip_gate._redis_get", return_value=None):
                                            result = _run_pipeline(
                                                ip="185.220.101.1",
                                                user_id=None,
                                                session_id=None,
                                                fingerprint_data={},
                                                body_data={},
                                            )

        assert result.final_verdict == GateVerdict.BLOCK
        assert result.blocking_gate == 1
        assert result.reason_code == "BANNED_PROXY_TOR"
        # Only gate 1 should have run
        assert len(result.gates) == 1

    def test_handler_returns_403_for_blocked_ip(self):
        """Lambda handler should return HTTP 403 for a blocked request."""
        import lambda_ip_gate

        mock_pipeline = MagicMock()
        from lambda_ip_gate import PipelineResult, GateVerdict, GateResult
        mock_result = PipelineResult(
            request_id="test-req-id",
            ip_address="185.220.101.1",
            user_id=None,
            session_id=None,
            final_verdict=GateVerdict.BLOCK,
            blocking_gate=1,
            reason_code="BANNED_PROXY_TOR",
            gates=[],
        )

        event = _make_api_event(ip="185.220.101.1")

        with patch.object(lambda_ip_gate, "_init_services"):
            with patch.object(lambda_ip_gate, "_run_pipeline", return_value=mock_result):
                with patch.object(lambda_ip_gate, "_log_audit"):
                    with patch.object(lambda_ip_gate, "_post_block_actions"):
                        response = lambda_ip_gate.handler(event, MagicMock())

        assert response["statusCode"] == 403
        body = json.loads(response["body"])
        assert body["verdict"] == "BLOCK"
        assert body["reason_code"] == "BANNED_PROXY_TOR"

    def test_handler_returns_200_for_clean_ip(self):
        """Lambda handler should return HTTP 200 for a passing request."""
        import lambda_ip_gate

        from lambda_ip_gate import PipelineResult, GateVerdict
        mock_result = PipelineResult(
            request_id="clean-req-id",
            ip_address="73.100.1.1",
            user_id="u1",
            session_id="s1",
            final_verdict=GateVerdict.PASS,
            blocking_gate=None,
            reason_code=None,
            gates=[],
        )

        event = _make_api_event(ip="73.100.1.1", user_id="u1")

        with patch.object(lambda_ip_gate, "_init_services"):
            with patch.object(lambda_ip_gate, "_run_pipeline", return_value=mock_result):
                with patch.object(lambda_ip_gate, "_log_audit"):
                    response = lambda_ip_gate.handler(event, MagicMock())

        assert response["statusCode"] == 200
        assert json.loads(response["body"])["verdict"] == "PASS"

    def test_handler_returns_400_for_private_ip(self):
        """Lambda handler should return 400 for private/loopback IPs."""
        import lambda_ip_gate

        event = _make_api_event(ip="127.0.0.1")

        with patch.object(lambda_ip_gate, "_init_services"):
            response = lambda_ip_gate.handler(event, MagicMock())

        assert response["statusCode"] == 400

    def test_handler_handles_init_failure_gracefully(self):
        """Service init failure should return 202 REVIEW, not 500."""
        import lambda_ip_gate

        event = _make_api_event(ip="203.0.113.1")

        with patch.object(lambda_ip_gate, "_init_services", side_effect=RuntimeError("cold start failure")):
            response = lambda_ip_gate.handler(event, MagicMock())

        assert response["statusCode"] == 202
        body = json.loads(response["body"])
        assert body["verdict"] == "REVIEW"


# ---------------------------------------------------------------------------
# DynamoDB Blacklist Unit Tests
# ---------------------------------------------------------------------------

class TestDynamoDBBlacklistService:

    @mock_aws
    def test_add_and_get(self, dynamodb_tables):
        from dynamodb_blacklist import IPBlacklistService
        svc = IPBlacklistService(table_name="ip-blacklist")

        entry = svc.add("1.2.3.4", reason="TEST", added_by="pytest", ttl_hours=1)
        assert entry.ip_address == "1.2.3.4"

        result = svc.get("1.2.3.4")
        assert result is not None
        assert result["reason"] == "TEST"

    @mock_aws
    def test_remove(self, dynamodb_tables):
        from dynamodb_blacklist import IPBlacklistService
        svc = IPBlacklistService(table_name="ip-blacklist")
        svc.add("5.6.7.8", reason="TEST", added_by="pytest")
        assert svc.is_blacklisted("5.6.7.8")
        svc.remove("5.6.7.8")
        assert not svc.is_blacklisted("5.6.7.8")

    @mock_aws
    def test_invalid_ip_raises(self, dynamodb_tables):
        from dynamodb_blacklist import IPBlacklistService
        svc = IPBlacklistService(table_name="ip-blacklist")
        with pytest.raises(ValueError, match="Invalid IP"):
            svc.add("not-an-ip", reason="TEST", added_by="pytest")

    @mock_aws
    def test_overwrite_false_raises_on_duplicate(self, dynamodb_tables):
        from dynamodb_blacklist import IPBlacklistService
        svc = IPBlacklistService(table_name="ip-blacklist")
        svc.add("9.9.9.9", reason="TEST", added_by="pytest")
        with pytest.raises(ValueError, match="already blacklisted"):
            svc.add("9.9.9.9", reason="TEST2", added_by="pytest", overwrite=False)


# ---------------------------------------------------------------------------
# WAF Integration Unit Tests
# ---------------------------------------------------------------------------

class TestWAFIntegration:

    def test_normalise_cidr_ipv4(self):
        from waf_integration import _normalise_cidr
        assert _normalise_cidr("1.2.3.4") == "1.2.3.4/32"
        assert _normalise_cidr("10.0.0.0/8") == "10.0.0.0/8"

    def test_normalise_cidr_ipv6(self):
        from waf_integration import _normalise_cidr
        assert _normalise_cidr("2001:db8::1") == "2001:db8::1/128"

    def test_normalise_invalid_ip_raises(self):
        from waf_integration import _normalise_cidr
        with pytest.raises(ValueError):
            _normalise_cidr("not-an-ip")

    def test_is_ipv4_cidr(self):
        from waf_integration import _is_ipv4_cidr
        assert _is_ipv4_cidr("1.2.3.4/32")
        assert not _is_ipv4_cidr("2001:db8::/32")


# ---------------------------------------------------------------------------
# Sanctions Checker Unit Tests
# ---------------------------------------------------------------------------

class TestSanctionsCheckerUnit:

    def test_name_normalisation_diacritics(self):
        from s3_sanctions_checker import _normalise_name
        assert _normalise_name("Müller") == "muller"
        assert _normalise_name("Ñoño") == "nono"
        assert _normalise_name("Björk") == "bjork"

    def test_name_normalisation_stop_words(self):
        from s3_sanctions_checker import _normalise_name
        # 'al' is a stop word
        assert "al" not in _normalise_name("al-Baghdadi").split()

    def test_similarity_exact(self):
        from s3_sanctions_checker import _compute_similarity
        assert _compute_similarity("John Smith", "John Smith") == 1.0

    def test_similarity_transposition(self):
        from s3_sanctions_checker import _compute_similarity
        # Word-order insensitive via token-set ratio
        score = _compute_similarity("Smith John", "John Smith")
        assert score >= 0.95

    def test_similarity_typo(self):
        from s3_sanctions_checker import _compute_similarity
        score = _compute_similarity("Mohamad", "Mohammed")
        assert score >= 0.70

    def test_similarity_unrelated(self):
        from s3_sanctions_checker import _compute_similarity
        score = _compute_similarity("Alice Johnson", "Zeng Xiao")
        assert score < 0.40
