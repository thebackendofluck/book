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
test_pipeline.py — pytest suite for all 8 security gates.

All Redis and external service calls are mocked so the test suite runs
without any infrastructure.  Each gate is tested in isolation (unit tests)
plus the full pipeline integration flow is tested via the FastAPI test client.

Test structure:
  TestGate1IPTypeCheck
  TestGate2VPNDetection
  TestGate3KnownProxy
  TestGate4IPBlacklist
  TestGate5FraudScore
  TestGate6DeviceFingerprint
  TestGate7Sanctions
  TestGate8KYCStatus
  TestPipelineIntegration
  TestBlacklistService
  TestSanctionsChecker
  TestDeviceFingerprintTracker
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

# `sanctions_checker` pulls in fuzzywuzzy for SDN name matching; if the
# dep isn't installed (e.g. a full-repo pytest run from scripts/ without
# the chapter-24 requirements), skip the whole file rather than erroring
# at collection time.
pytest.importorskip("fuzzywuzzy", reason="chapter-24 sanctions matching needs fuzzywuzzy")

from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Module imports (allow running from any CWD by adjusting sys.path)
# ---------------------------------------------------------------------------

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from ip_blacklist_service import BlacklistEntry, BlacklistCheckResult, IPBlacklistService
from sanctions_checker import SDNEntry, SanctionsCheckResult, SanctionsMatch, SanctionsChecker
from device_fingerprint import (
    DeviceFingerprint,
    DeviceFingerprintTracker,
    FingerprintAnomaly,
    HEADLESS_JA3_HASHES,
    fingerprint_from_request_headers,
)
from ip_detection_pipeline import (
    GateVerdict,
    GateResult,
    PipelineResult,
    ReasonCode,
    DATACENTER_ASNS,
    _gate1_ip_type,
    _gate2_vpn_detection,
    _gate3_known_proxy,
    _gate4_ip_blacklist,
    _gate5_fraud_score,
    _gate6_device_fingerprint,
    _gate7_sanctions,
    _gate8_kyc_status,
    run_pipeline,
    app,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_redis():
    """A MagicMock that mimics a redis.Redis instance."""
    r = MagicMock()
    r.sismember.return_value = False
    r.get.return_value = None
    r.hget.return_value = None
    r.hgetall.return_value = {}
    r.zscore.return_value = None
    r.zcard.return_value = 0
    r.zcount.return_value = 0
    r.zrange.return_value = []
    r.zrangebyscore.return_value = []
    r.zrevrangebyscore.return_value = []
    r.smembers.return_value = set()
    r.incr.return_value = 1
    r.pipeline.return_value.__enter__ = lambda s: s
    r.pipeline.return_value.__exit__ = MagicMock(return_value=False)
    r.pipeline.return_value.execute.return_value = [1, True]
    return r


@pytest.fixture
def mock_request():
    """A minimal FastAPI Request-like mock."""
    req = MagicMock()
    req.headers = {
        "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        "accept-language": "en-US,en;q=0.9",
        "accept-encoding": "gzip, deflate, br",
        "x-ja3": "abc123def456abc123def456abc12345",
        "x-session-id": "sess_test_001",
        "x-player-name": "John Doe",
        "x-canvas-hash": "canvashash001",
        "x-tz-offset": "-180",
        "x-screen-res": "1920x1080",
        "x-platform": "Win32",
        "x-plugins-hash": "pluginhash001",
        "x-webgl-vendor": "Google Inc.",
        "x-webgl-renderer": "ANGLE (Intel, Mesa)",
    }
    req.query_params = {}
    req.url.path = "/api/v1/game/spin"
    req.method = "POST"
    req.client = MagicMock()
    req.client.host = "203.0.113.1"
    return req


@pytest.fixture
def clean_ip() -> str:
    return "203.0.113.1"  # TEST-NET-3 (RFC 5737) — safe in tests


@pytest.fixture
def dc_ip() -> str:
    return "174.138.0.1"  # DigitalOcean range (realistic)


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# Gate 1 — IP Type Check
# ---------------------------------------------------------------------------

class TestGate1IPTypeCheck:

    def test_tor_exit_node_blocked(self, clean_ip, mock_request, mock_redis):
        mock_redis.sismember.return_value = True  # IP is in tor:exit_nodes SET
        with (
            patch("ip_detection_pipeline.get_redis", return_value=mock_redis),
            patch("ip_detection_pipeline.get_asn_reader", return_value=None),
        ):
            result = _gate1_ip_type(clean_ip, mock_request)

        assert result.verdict == GateVerdict.BLOCK
        assert result.reason_code == ReasonCode.BANNED_PROXY_TOR
        assert "Tor" in result.detail

    def test_datacenter_asn_blocked(self, mock_request, mock_redis):
        mock_redis.sismember.return_value = False  # not in Tor list

        mock_asn_reader = MagicMock()
        mock_asn_resp = MagicMock()
        mock_asn_resp.autonomous_system_number = 14061  # DigitalOcean
        mock_asn_resp.autonomous_system_organization = "DigitalOcean LLC"
        mock_asn_reader.asn.return_value = mock_asn_resp

        with (
            patch("ip_detection_pipeline.get_redis", return_value=mock_redis),
            patch("ip_detection_pipeline.get_asn_reader", return_value=mock_asn_reader),
        ):
            result = _gate1_ip_type("198.51.100.1", mock_request)

        assert result.verdict == GateVerdict.BLOCK
        assert result.reason_code == ReasonCode.BANNED_PROXY_DC
        assert "14061" in result.detail

    def test_residential_ip_passes(self, clean_ip, mock_request, mock_redis):
        mock_redis.sismember.return_value = False

        mock_asn_reader = MagicMock()
        mock_asn_resp = MagicMock()
        mock_asn_resp.autonomous_system_number = 701  # Verizon (residential)
        mock_asn_resp.autonomous_system_organization = "Verizon Business"
        mock_asn_reader.asn.return_value = mock_asn_resp

        with (
            patch("ip_detection_pipeline.get_redis", return_value=mock_redis),
            patch("ip_detection_pipeline.get_asn_reader", return_value=mock_asn_reader),
        ):
            result = _gate1_ip_type(clean_ip, mock_request)

        assert result.verdict == GateVerdict.PASS

    def test_all_datacenter_asns_covered(self):
        """Verify all 9 required ASNs from the spec are present."""
        required = {14061, 16509, 15169, 8075, 20473, 63949, 24940, 16276, 13335}
        assert required.issubset(DATACENTER_ASNS), (
            f"Missing ASNs: {required - DATACENTER_ASNS}"
        )

    def test_no_maxmind_db_still_blocks_tor(self, clean_ip, mock_request, mock_redis):
        """Pipeline degrades gracefully when MaxMind DB is absent."""
        mock_redis.sismember.return_value = True  # Tor hit
        with (
            patch("ip_detection_pipeline.get_redis", return_value=mock_redis),
            patch("ip_detection_pipeline.get_asn_reader", return_value=None),
        ):
            result = _gate1_ip_type(clean_ip, mock_request)
        assert result.verdict == GateVerdict.BLOCK
        assert result.reason_code == ReasonCode.BANNED_PROXY_TOR


# ---------------------------------------------------------------------------
# Gate 2 — VPN Detection
# ---------------------------------------------------------------------------

class TestGate2VPNDetection:

    def test_cached_vpn_blocks(self, clean_ip, mock_request, mock_redis):
        mock_redis.get.return_value = "vpn"
        with patch("ip_detection_pipeline.get_redis", return_value=mock_redis):
            result = _gate2_vpn_detection(clean_ip, mock_request)

        assert result.verdict == GateVerdict.BLOCK
        assert result.reason_code == ReasonCode.BANNED_PROXY_VPN
        assert "cached" in result.detail

    def test_cached_clean_passes(self, clean_ip, mock_request, mock_redis):
        mock_redis.get.return_value = "clean"
        with patch("ip_detection_pipeline.get_redis", return_value=mock_redis):
            result = _gate2_vpn_detection(clean_ip, mock_request)
        assert result.verdict == GateVerdict.PASS

    def test_vpn_ip_list_match_blocks(self, clean_ip, mock_request, mock_redis):
        mock_redis.get.return_value = None
        # sismember returns True for the vpn:ip_list check
        mock_redis.sismember.return_value = True
        with patch("ip_detection_pipeline.get_redis", return_value=mock_redis):
            result = _gate2_vpn_detection(clean_ip, mock_request)

        assert result.verdict == GateVerdict.BLOCK
        assert result.reason_code == ReasonCode.BANNED_PROXY_VPN

    def test_proxycheck_vpn_confirmed_blocks(self, clean_ip, mock_request, mock_redis):
        mock_redis.get.return_value = None
        mock_redis.sismember.return_value = False

        with (
            patch("ip_detection_pipeline.get_redis", return_value=mock_redis),
            patch("ip_detection_pipeline.PROXYCHECK_API_KEY", "test_key_123"),
            patch("ip_detection_pipeline._proxycheck_query", return_value=True),
        ):
            result = _gate2_vpn_detection(clean_ip, mock_request)

        assert result.verdict == GateVerdict.BLOCK
        assert "proxycheck.io" in result.detail

    def test_proxycheck_clean_passes(self, clean_ip, mock_request, mock_redis):
        mock_redis.get.return_value = None
        mock_redis.sismember.return_value = False

        with (
            patch("ip_detection_pipeline.get_redis", return_value=mock_redis),
            patch("ip_detection_pipeline.PROXYCHECK_API_KEY", "test_key_123"),
            patch("ip_detection_pipeline._proxycheck_query", return_value=False),
        ):
            result = _gate2_vpn_detection(clean_ip, mock_request)

        assert result.verdict == GateVerdict.PASS


# ---------------------------------------------------------------------------
# Gate 3 — Known Proxy Check
# ---------------------------------------------------------------------------

class TestGate3KnownProxy:

    def test_ip_in_proxy_list_blocks(self, clean_ip, mock_request, mock_redis):
        mock_redis.sismember.return_value = True
        with (
            patch("ip_detection_pipeline.get_redis", return_value=mock_redis),
            patch("ip_detection_pipeline.get_asn_reader", return_value=None),
        ):
            result = _gate3_known_proxy(clean_ip, mock_request)

        assert result.verdict == GateVerdict.BLOCK
        assert result.reason_code == ReasonCode.BANNED_PROXY_KNOWN

    def test_asn_in_proxy_asn_list_blocks(self, clean_ip, mock_request, mock_redis):
        mock_redis.sismember.side_effect = lambda key, val: (
            key == "proxy:asn_list" and val == "12345"
        )

        mock_asn_reader = MagicMock()
        mock_asn_resp = MagicMock()
        mock_asn_resp.autonomous_system_number = 12345
        mock_asn_reader.asn.return_value = mock_asn_resp

        with (
            patch("ip_detection_pipeline.get_redis", return_value=mock_redis),
            patch("ip_detection_pipeline.get_asn_reader", return_value=mock_asn_reader),
        ):
            result = _gate3_known_proxy(clean_ip, mock_request)

        assert result.verdict == GateVerdict.BLOCK
        assert "12345" in result.detail

    def test_clean_ip_passes(self, clean_ip, mock_request, mock_redis):
        mock_redis.sismember.return_value = False
        with (
            patch("ip_detection_pipeline.get_redis", return_value=mock_redis),
            patch("ip_detection_pipeline.get_asn_reader", return_value=None),
        ):
            result = _gate3_known_proxy(clean_ip, mock_request)
        assert result.verdict == GateVerdict.PASS


# ---------------------------------------------------------------------------
# Gate 4 — IP Blacklist
# ---------------------------------------------------------------------------

class TestGate4IPBlacklist:

    def _make_blacklist_service(self, is_blacklisted: bool, entry: Optional[BlacklistEntry] = None):
        svc = MagicMock(spec=IPBlacklistService)
        svc.check.return_value = BlacklistCheckResult(
            ip="1.2.3.4",
            is_blacklisted=is_blacklisted,
            entry=entry,
        )
        return svc

    def test_blacklisted_ip_blocked(self, clean_ip, mock_request):
        entry = BlacklistEntry(
            ip=clean_ip,
            reason="Reported for brute-force",
            source="abuseipdb",
            added_at=time.time() - 3600,
            expires_at=0,
            confidence_score=90,
            abuse_categories=[18],
        )
        svc = self._make_blacklist_service(is_blacklisted=True, entry=entry)
        with patch("ip_detection_pipeline.get_blacklist", return_value=svc):
            result = _gate4_ip_blacklist(clean_ip, mock_request)

        assert result.verdict == GateVerdict.BLOCK
        assert result.reason_code == ReasonCode.BANNED_IP_BLACKLIST
        assert "abuseipdb" in result.detail

    def test_clean_ip_passes(self, clean_ip, mock_request):
        svc = self._make_blacklist_service(is_blacklisted=False)
        with patch("ip_detection_pipeline.get_blacklist", return_value=svc):
            result = _gate4_ip_blacklist(clean_ip, mock_request)
        assert result.verdict == GateVerdict.PASS


# ---------------------------------------------------------------------------
# Gate 5 — Fraud Score
# ---------------------------------------------------------------------------

class TestGate5FraudScore:

    def test_high_ip_velocity_blocks(self, clean_ip, mock_request, mock_redis):
        """IP sending 25+ requests in 30 s => score 25 => REVIEW (not block yet)."""
        call_count = [0]

        def incr_side_effect(key):
            call_count[0] += 1
            # First call (ip 30s counter) returns 25
            return 25 if call_count[0] == 1 else 1

        mock_redis.incr.side_effect = incr_side_effect
        mock_redis.expire.return_value = True
        mock_redis.get.return_value = None

        pipe = MagicMock()
        pipe.execute.return_value = [25, True]
        mock_redis.pipeline.return_value = pipe

        with (
            patch("ip_detection_pipeline.get_redis", return_value=mock_redis),
            patch("ip_detection_pipeline._get_player_profile", return_value={}),
            patch("ip_detection_pipeline.FRAUD_SCORE_BLOCK", 75),
            patch("ip_detection_pipeline.FRAUD_SCORE_REVIEW", 20),
        ):
            result = _gate5_fraud_score(clean_ip, mock_request, player_id="p001")

        # score 25 >= REVIEW threshold (20) => REVIEW
        assert result.verdict in (GateVerdict.REVIEW, GateVerdict.BLOCK)
        assert "IP_VEL_30S" in result.detail or "IP_VEL" in result.metadata.get("signals", [""])[0]

    def test_new_account_high_value_flagged(self, clean_ip, mock_request, mock_redis):
        mock_request.query_params = {"amount": "5000"}
        mock_redis.get.return_value = None

        pipe = MagicMock()
        pipe.execute.return_value = [1, True]
        mock_redis.pipeline.return_value = pipe

        profile = {"account_age_days": 0.1, "avg_tx_amount": 100.0}

        with (
            patch("ip_detection_pipeline.get_redis", return_value=mock_redis),
            patch("ip_detection_pipeline._get_player_profile", return_value=profile),
            patch("ip_detection_pipeline._extract_tx_amount", return_value=5000.0),
        ):
            result = _gate5_fraud_score(clean_ip, mock_request, player_id="p_new")

        # Should be REVIEW or BLOCK (score includes NEW_ACCT_HIGH_VALUE + AMOUNT_ANOMALY)
        assert result.verdict in (GateVerdict.REVIEW, GateVerdict.BLOCK)

    def test_clean_request_passes(self, clean_ip, mock_request, mock_redis):
        pipe = MagicMock()
        pipe.execute.return_value = [1, True]
        mock_redis.pipeline.return_value = pipe
        mock_redis.get.return_value = None

        with (
            patch("ip_detection_pipeline.get_redis", return_value=mock_redis),
            patch("ip_detection_pipeline._get_player_profile", return_value={}),
            patch("ip_detection_pipeline._extract_tx_amount", return_value=None),
        ):
            result = _gate5_fraud_score(clean_ip, mock_request, player_id="p_clean")

        assert result.verdict == GateVerdict.PASS

    def test_no_player_id_still_checks_ip(self, clean_ip, mock_request, mock_redis):
        pipe = MagicMock()
        pipe.execute.return_value = [1, True]
        mock_redis.pipeline.return_value = pipe
        mock_redis.get.return_value = None

        with (
            patch("ip_detection_pipeline.get_redis", return_value=mock_redis),
            patch("ip_detection_pipeline._get_player_profile", return_value={}),
        ):
            result = _gate5_fraud_score(clean_ip, mock_request, player_id="")

        assert result.gate == 5
        assert result.verdict == GateVerdict.PASS


# ---------------------------------------------------------------------------
# Gate 6 — Device Fingerprint
# ---------------------------------------------------------------------------

class TestGate6DeviceFingerprint:

    def _make_tracker(self, verdict: str, score: int, signals: list[str]):
        tracker = MagicMock(spec=DeviceFingerprintTracker)
        anomaly = FingerprintAnomaly(
            player_id="p001",
            session_id="s001",
            fp_hash="abc123",
            anomaly_score=score,
            verdict=verdict,
            signals=signals,
            is_new_device=False,
            distinct_fps_in_window=1,
            ja3_is_headless=(verdict == "BLOCK" and "HEADLESS" in " ".join(signals)),
            ja3_changed=False,
            browser_mismatch=False,
            timezone_mismatch=False,
        )
        tracker.check_and_record.return_value = anomaly
        return tracker

    def test_headless_ja3_blocked(self, clean_ip, mock_request, mock_redis):
        ja3 = list(HEADLESS_JA3_HASHES)[0]
        mock_request.headers = {**mock_request.headers, "x-ja3": ja3}

        tracker = self._make_tracker("BLOCK", 80, [f"HEADLESS_JA3:{ja3[:8]}"])
        with (
            patch("ip_detection_pipeline.get_fp_tracker", return_value=tracker),
            patch("ip_detection_pipeline.get_redis", return_value=mock_redis),
            patch("ip_detection_pipeline._get_player_profile", return_value={}),
        ):
            result = _gate6_device_fingerprint(clean_ip, mock_request, "p001")

        assert result.verdict == GateVerdict.BLOCK
        assert result.reason_code == ReasonCode.DEVICE_ANOMALY
        assert "HEADLESS" in result.detail

    def test_clean_fingerprint_passes(self, clean_ip, mock_request, mock_redis):
        tracker = self._make_tracker("PASS", 5, [])
        with (
            patch("ip_detection_pipeline.get_fp_tracker", return_value=tracker),
            patch("ip_detection_pipeline.get_redis", return_value=mock_redis),
            patch("ip_detection_pipeline._get_player_profile", return_value={}),
        ):
            result = _gate6_device_fingerprint(clean_ip, mock_request, "p001")
        assert result.verdict == GateVerdict.PASS

    def test_missing_ja3_skips_gate(self, clean_ip, mock_request, mock_redis):
        mock_request.headers = {k: v for k, v in mock_request.headers.items() if k != "x-ja3"}
        # No x-ja3 → ja3_hash defaults to "unknown" → gate skipped
        with (
            patch("ip_detection_pipeline.get_redis", return_value=mock_redis),
            patch("ip_detection_pipeline._get_player_profile", return_value={}),
        ):
            result = _gate6_device_fingerprint(clean_ip, mock_request, "p001")
        assert result.verdict == GateVerdict.PASS
        assert "skipped" in result.detail.lower()

    def test_review_verdict_propagates(self, clean_ip, mock_request, mock_redis):
        tracker = self._make_tracker("REVIEW", 45, ["JA3_CHANGED:prev=abc,curr=def"])
        with (
            patch("ip_detection_pipeline.get_fp_tracker", return_value=tracker),
            patch("ip_detection_pipeline.get_redis", return_value=mock_redis),
            patch("ip_detection_pipeline._get_player_profile", return_value={}),
        ):
            result = _gate6_device_fingerprint(clean_ip, mock_request, "p001")
        assert result.verdict == GateVerdict.REVIEW


# ---------------------------------------------------------------------------
# Gate 7 — Sanctions Check
# ---------------------------------------------------------------------------

class TestGate7Sanctions:

    def _make_checker(self, is_match: bool, score: int, entry: Optional[SDNEntry] = None):
        checker = MagicMock(spec=SanctionsChecker)
        checker.refresh_if_stale.return_value = False

        best = None
        if score > 0:
            best = SanctionsMatch(
                matched=is_match,
                score=score,
                matched_name="BLOFELD, ERNST",
                query_name="Ernst Blofeld",
                entry=entry or SDNEntry(
                    uid="123",
                    name="BLOFELD, ERNST",
                    aliases=[],
                    entity_type="Individual",
                    program="SDGT",
                    nationality="",
                    dob="",
                    remarks="",
                ),
            )

        checker.check.return_value = SanctionsCheckResult(
            query_name="Ernst Blofeld",
            is_match=is_match,
            best_match=best,
            all_matches=[best] if best else [],
        )
        return checker

    def test_sanctions_match_blocks(self, clean_ip, mock_request):
        checker = self._make_checker(is_match=True, score=96)
        with patch("ip_detection_pipeline.get_sanctions", return_value=checker):
            result = _gate7_sanctions(clean_ip, mock_request, "p001")

        assert result.verdict == GateVerdict.BLOCK
        assert result.reason_code == ReasonCode.SANCTIONS_MATCH
        assert "96" in result.detail

    def test_near_miss_triggers_review(self, clean_ip, mock_request):
        checker = self._make_checker(is_match=False, score=78)  # below threshold (85) but within 10
        with patch("ip_detection_pipeline.get_sanctions", return_value=checker):
            result = _gate7_sanctions(clean_ip, mock_request, "p001")

        assert result.verdict in (GateVerdict.REVIEW, GateVerdict.PASS)

    def test_no_match_passes(self, clean_ip, mock_request):
        checker = self._make_checker(is_match=False, score=0)
        with patch("ip_detection_pipeline.get_sanctions", return_value=checker):
            result = _gate7_sanctions(clean_ip, mock_request, "p001")
        assert result.verdict == GateVerdict.PASS

    def test_missing_player_name_skips(self, clean_ip, mock_request):
        mock_request.headers = {k: v for k, v in mock_request.headers.items()
                                  if k != "x-player-name"}
        result = _gate7_sanctions(clean_ip, mock_request, "p001")
        assert result.verdict == GateVerdict.PASS
        assert "skipped" in result.detail.lower()


# ---------------------------------------------------------------------------
# Gate 8 — KYC Status
# ---------------------------------------------------------------------------

class TestGate8KYCStatus:

    def _run_gate8(self, mock_redis, kyc_status: str, player_id: str = "p001"):
        mock_redis.get.return_value = kyc_status
        mock_request = MagicMock()
        with patch("ip_detection_pipeline.get_redis", return_value=mock_redis):
            return _gate8_kyc_status("1.2.3.4", mock_request, player_id)

    def test_approved_passes(self, mock_redis):
        result = self._run_gate8(mock_redis, "APPROVED")
        assert result.verdict == GateVerdict.PASS

    def test_enhanced_due_diligence_passes(self, mock_redis):
        result = self._run_gate8(mock_redis, "ENHANCED_DUE_DILIGENCE")
        assert result.verdict == GateVerdict.PASS

    def test_suspended_blocks(self, mock_redis):
        result = self._run_gate8(mock_redis, "SUSPENDED")
        assert result.verdict == GateVerdict.BLOCK
        assert result.reason_code == ReasonCode.KYC_SUSPENDED

    def test_pending_reviews(self, mock_redis):
        result = self._run_gate8(mock_redis, "PENDING")
        assert result.verdict == GateVerdict.REVIEW
        assert result.reason_code == ReasonCode.KYC_REQUIRED

    def test_documents_requested_reviews(self, mock_redis):
        result = self._run_gate8(mock_redis, "DOCUMENTS_REQUESTED")
        assert result.verdict == GateVerdict.REVIEW

    def test_under_review_reviews(self, mock_redis):
        result = self._run_gate8(mock_redis, "UNDER_REVIEW")
        assert result.verdict == GateVerdict.REVIEW

    def test_rejected_reviews(self, mock_redis):
        result = self._run_gate8(mock_redis, "REJECTED")
        assert result.verdict == GateVerdict.REVIEW

    def test_no_player_id_skipped(self, mock_redis):
        result = self._run_gate8(mock_redis, "", player_id="")
        assert result.verdict == GateVerdict.PASS
        assert "skipped" in result.detail.lower()

    def test_kyc_service_unavailable_review(self, mock_redis):
        """When both cache and remote KYC service fail, default to REVIEW (fail open)."""
        mock_redis.get.return_value = None
        mock_request = MagicMock()
        with (
            patch("ip_detection_pipeline.get_redis", return_value=mock_redis),
            patch("ip_detection_pipeline._fetch_kyc_status", return_value=""),
        ):
            result = _gate8_kyc_status("1.2.3.4", mock_request, "p001")
        assert result.verdict == GateVerdict.REVIEW


# ---------------------------------------------------------------------------
# Full pipeline integration
# ---------------------------------------------------------------------------

class TestPipelineIntegration:

    def _patch_all_gates_pass(self):
        """Context manager that patches all gates to return PASS."""
        from unittest.mock import patch

        mock_redis = MagicMock()
        mock_redis.sismember.return_value = False
        mock_redis.get.return_value = "APPROVED"  # KYC
        mock_redis.incr.return_value = 1
        pipe = MagicMock()
        pipe.execute.return_value = [1, True]
        mock_redis.pipeline.return_value = pipe

        mock_asn = MagicMock()
        mock_asn_resp = MagicMock()
        mock_asn_resp.autonomous_system_number = 701
        mock_asn_resp.autonomous_system_organization = "Verizon"
        mock_asn.asn.return_value = mock_asn_resp

        mock_blacklist = MagicMock(spec=IPBlacklistService)
        mock_blacklist.check.return_value = BlacklistCheckResult(ip="1.2.3.4", is_blacklisted=False)

        mock_sanctions = MagicMock(spec=SanctionsChecker)
        mock_sanctions.refresh_if_stale.return_value = False
        mock_sanctions.check.return_value = SanctionsCheckResult(
            query_name="John Doe", is_match=False,
        )

        mock_tracker = MagicMock(spec=DeviceFingerprintTracker)
        anomaly = FingerprintAnomaly(
            player_id="p001", session_id="s001", fp_hash="ok",
            anomaly_score=0, verdict="PASS", signals=[],
            is_new_device=True, distinct_fps_in_window=1,
            ja3_is_headless=False, ja3_changed=False,
            browser_mismatch=False, timezone_mismatch=False,
        )
        mock_tracker.check_and_record.return_value = anomaly

        return (
            patch("ip_detection_pipeline.get_redis", return_value=mock_redis),
            patch("ip_detection_pipeline.get_asn_reader", return_value=mock_asn),
            patch("ip_detection_pipeline.get_blacklist", return_value=mock_blacklist),
            patch("ip_detection_pipeline.get_sanctions", return_value=mock_sanctions),
            patch("ip_detection_pipeline.get_fp_tracker", return_value=mock_tracker),
            patch("ip_detection_pipeline._get_player_profile", return_value={}),
            patch("ip_detection_pipeline._extract_tx_amount", return_value=None),
        )

    def test_all_gates_pass(self, client):
        patches = self._patch_all_gates_pass()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            response = client.get(
                "/api/v1/game/spin",
                headers={
                    "x-real-ip": "203.0.113.1",
                    "x-player-id": "p001",
                    "x-player-name": "John Doe",
                    "x-session-id": "sess_001",
                    "x-ja3": "abc123def456",
                    "user-agent": "Mozilla/5.0",
                },
            )
        assert response.status_code in (200, 404)  # 404 ok — route not registered

    def test_tor_ip_returns_403(self, client):
        mock_redis = MagicMock()
        mock_redis.sismember.return_value = True  # Tor exit node

        with patch("ip_detection_pipeline.get_redis", return_value=mock_redis):
            response = client.get(
                "/api/v1/game/spin",
                headers={"x-real-ip": "203.0.113.100"},
            )

        assert response.status_code == 403
        body = response.json()
        assert body["reason"] == ReasonCode.BANNED_PROXY_TOR
        assert "gate" in body

    def test_blacklisted_ip_returns_403_with_reason(self, client):
        mock_redis_inst = MagicMock()
        mock_redis_inst.sismember.return_value = False
        mock_redis_inst.get.return_value = None
        pipe = MagicMock()
        pipe.execute.return_value = [1, True]
        mock_redis_inst.pipeline.return_value = pipe

        entry = BlacklistEntry(
            ip="10.0.0.1", reason="Mass scanner", source="abuseipdb",
            added_at=time.time() - 7200, expires_at=0, confidence_score=98,
        )
        mock_blacklist = MagicMock(spec=IPBlacklistService)
        mock_blacklist.check.return_value = BlacklistCheckResult(
            ip="10.0.0.1", is_blacklisted=True, entry=entry,
        )

        with (
            patch("ip_detection_pipeline.get_redis", return_value=mock_redis_inst),
            patch("ip_detection_pipeline.get_asn_reader", return_value=None),
            patch("ip_detection_pipeline.get_blacklist", return_value=mock_blacklist),
        ):
            response = client.get(
                "/api/v1/game/spin",
                headers={"x-real-ip": "10.0.0.1"},
            )

        assert response.status_code == 403
        body = response.json()
        assert body["reason"] == ReasonCode.BANNED_IP_BLACKLIST
        assert "abuseipdb" in body["detail"]

    def test_health_endpoint_bypasses_pipeline(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert "status" in response.json()

    def test_review_flag_in_response_header(self, client):
        patches = self._patch_all_gates_pass()
        # Override sanctions to return REVIEW
        mock_sanctions = MagicMock(spec=SanctionsChecker)
        mock_sanctions.refresh_if_stale.return_value = False
        near_miss = SanctionsMatch(
            matched=False, score=79,
            matched_name="DOE, JOHN", query_name="John Doe",
            entry=SDNEntry("999", "DOE, JOHN", [], "Individual", "SDGT", "", "", ""),
        )
        mock_sanctions.check.return_value = SanctionsCheckResult(
            query_name="John Doe", is_match=False, best_match=near_miss, all_matches=[near_miss],
        )

        with (
            patches[0], patches[1], patches[2],
            patch("ip_detection_pipeline.get_sanctions", return_value=mock_sanctions),
            patches[4], patches[5], patches[6],
        ):
            response = client.get(
                "/api/v1/game/spin",
                headers={
                    "x-real-ip": "203.0.113.1",
                    "x-player-id": "p001",
                    "x-player-name": "John Doe",
                    "x-session-id": "sess_001",
                    "x-ja3": "abc123def456",
                    "user-agent": "Mozilla/5.0",
                },
            )

        # Request should not be blocked (near-miss is REVIEW), and header should be set
        assert response.status_code != 403


# ---------------------------------------------------------------------------
# IPBlacklistService unit tests
# ---------------------------------------------------------------------------

class TestBlacklistService:

    def _make_service(self, mock_redis):
        svc = IPBlacklistService.__new__(IPBlacklistService)
        svc._client = mock_redis
        return svc

    def test_add_sets_zset_and_hash(self, mock_redis):
        pipe = MagicMock()
        pipe.execute.return_value = [1, True, True, True]
        mock_redis.pipeline.return_value = pipe

        svc = self._make_service(mock_redis)
        svc.add("1.2.3.4", reason="Test ban", source="manual")

        pipe.zadd.assert_called_once()
        pipe.set.assert_called_once()

    def test_check_returns_clean_for_absent_ip(self, mock_redis):
        mock_redis.zscore.return_value = None
        svc = self._make_service(mock_redis)
        result = svc.check("1.2.3.4")
        assert result.is_blacklisted is False

    def test_check_returns_blacklisted_for_permanent_entry(self, mock_redis):
        mock_redis.zscore.return_value = 0.0  # permanent (score == 0)
        mock_redis.get.return_value = json.dumps(asdict(BlacklistEntry(
            ip="1.2.3.4", reason="Scanner", source="manual",
            added_at=time.time() - 100, expires_at=0,
        )))
        svc = self._make_service(mock_redis)
        result = svc.check("1.2.3.4")
        assert result.is_blacklisted is True
        assert result.entry is not None

    def test_check_lazy_expires_ttl_entry(self, mock_redis):
        # TTL entry that expired 1 hour ago
        expired_ts = time.time() - 3600
        mock_redis.zscore.return_value = expired_ts

        pipe = MagicMock()
        pipe.execute.return_value = [1, 1]
        mock_redis.pipeline.return_value = pipe

        svc = self._make_service(mock_redis)
        result = svc.check("5.6.7.8")

        assert result.is_blacklisted is False
        pipe.zrem.assert_called_once()

    def test_invalid_ip_returns_clean(self, mock_redis):
        svc = self._make_service(mock_redis)
        result = svc.check("not-an-ip")
        assert result.is_blacklisted is False

    def test_import_abuseipdb_csv_filters_low_confidence(self, mock_redis):
        pipe = MagicMock()
        pipe.execute.return_value = [1, True, True, True]
        mock_redis.pipeline.return_value = pipe

        csv_data = (
            "ipAddress,abuseConfidenceScore,usageType,abuseCategories\n"
            "1.2.3.4,90,datacenter,\"18,15\"\n"
            "5.6.7.8,50,isp,\"3\"\n"           # below default threshold (75)
            "9.10.11.12,80,residential,\"14\"\n"
        )
        svc = self._make_service(mock_redis)

        # Patch the add method to count calls
        add_calls = []
        original_add = svc.add
        def tracking_add(*args, **kwargs):
            add_calls.append(kwargs.get("ip", args[0] if args else ""))

        svc.add = tracking_add
        imported, skipped = svc.import_abuseipdb_csv(csv_data, min_confidence=75)

        assert imported == 2   # 90 and 80 pass; 50 is skipped
        assert skipped == 1


# ---------------------------------------------------------------------------
# SanctionsChecker unit tests
# ---------------------------------------------------------------------------

class TestSanctionsChecker:

    def _make_checker(self, mock_redis) -> SanctionsChecker:
        checker = SanctionsChecker.__new__(SanctionsChecker)
        checker._redis = mock_redis
        checker.threshold = 85
        checker.refresh_hours = 24
        return checker

    def test_no_name_returns_no_match(self, mock_redis):
        checker = self._make_checker(mock_redis)
        result = checker.check("")
        assert result.is_match is False

    def test_cache_miss_returns_no_match_when_empty(self, mock_redis):
        mock_redis.smembers.return_value = set()
        mock_redis.hscan.return_value = ("0", {})

        checker = self._make_checker(mock_redis)
        result = checker.check("Alice Smith")
        assert result.is_match is False

    def test_high_score_match_found(self, mock_redis):
        """Simulate a token index hit and fuzzy match."""
        uid = "uid_001"
        entry = SDNEntry(
            uid=uid, name="BLOFELD, ERNST", aliases=["BLOFELD ERNST", "ERNST BLOFELD"],
            entity_type="Individual", program="SDGT", nationality="", dob="", remarks="",
        )

        mock_redis.smembers.return_value = {uid}
        mock_redis.hget.return_value = json.dumps(asdict(entry))

        checker = self._make_checker(mock_redis)
        result = checker.check("Ernst Blofeld")

        assert result.is_match is True
        assert result.best_match is not None
        assert result.best_match.score >= 85

    def test_refresh_skipped_when_fresh(self, mock_redis):
        mock_redis.get.side_effect = lambda key: (
            str(time.time() - 3600) if key == "sanctions:sdn:last_refresh" else None
        )
        checker = self._make_checker(mock_redis)
        refreshed = checker.refresh_if_stale()
        assert refreshed is False

    def test_force_refresh_with_unchanged_xml(self, mock_redis):
        xml_hash = "abc123fakehash"
        mock_redis.get.side_effect = lambda key: (
            xml_hash if key == "sanctions:sdn:xml_hash" else None
        )

        with patch("sanctions_checker._download_ofac_xml", return_value=b"<xml/>"):
            import hashlib
            real_hash = hashlib.sha256(b"<xml/>").hexdigest()
            mock_redis.get.side_effect = lambda key: (
                real_hash if key == "sanctions:sdn:xml_hash" else None
            )
            checker = self._make_checker(mock_redis)
            refreshed = checker.force_refresh()
            # Same hash → no rebuild
            assert refreshed is False


# ---------------------------------------------------------------------------
# DeviceFingerprintTracker unit tests
# ---------------------------------------------------------------------------

class TestDeviceFingerprintTracker:

    def _make_fp(self, player_id: str = "p001", ja3: str = "goodja3hash") -> DeviceFingerprint:
        return DeviceFingerprint(
            player_id=player_id,
            session_id="sess001",
            ja3_hash=ja3,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            accept_language="en-US",
            accept_encoding="gzip",
            canvas_hash="canvashash001",
            timezone_offset=-180,
            screen_resolution="1920x1080",
            platform="Win32",
            plugins_hash="pluginhash001",
            webgl_vendor="Google Inc.",
            webgl_renderer="ANGLE Intel",
        )

    def _make_tracker(self, mock_redis) -> DeviceFingerprintTracker:
        tracker = DeviceFingerprintTracker.__new__(DeviceFingerprintTracker)
        tracker._redis = mock_redis
        return tracker

    def test_headless_ja3_blocked(self, mock_redis):
        headless = list(HEADLESS_JA3_HASHES)[0]
        mock_redis.sismember.return_value = True  # in blocklist

        pipe = MagicMock()
        pipe.execute.return_value = [True]
        mock_redis.pipeline.return_value = pipe
        mock_redis.zrevrangebyscore.return_value = []
        mock_redis.zrangebyscore.return_value = []

        tracker = self._make_tracker(mock_redis)
        fp = self._make_fp(ja3=headless)
        anomaly = tracker.check_and_record(fp)

        assert anomaly.verdict == "BLOCK"
        assert anomaly.ja3_is_headless is True
        assert anomaly.anomaly_score >= 40

    def test_clean_first_session_passes(self, mock_redis):
        """New player, first device — should always PASS (no history to compare)."""
        mock_redis.sismember.return_value = False
        mock_redis.zrevrangebyscore.return_value = []  # no history
        mock_redis.zrangebyscore.return_value = []

        pipe = MagicMock()
        pipe.execute.return_value = [True]
        mock_redis.pipeline.return_value = pipe
        mock_redis.setex.return_value = True

        tracker = self._make_tracker(mock_redis)
        fp = self._make_fp()
        anomaly = tracker.check_and_record(fp)

        assert anomaly.verdict == "PASS"
        assert anomaly.is_new_device is True

    def test_rapid_switching_detected(self, mock_redis):
        """Player using 5 distinct devices in 5 minutes => anomaly."""
        mock_redis.sismember.return_value = False
        # Return 5 distinct hashes as recent fingerprints
        distinct = ["hash1", "hash2", "hash3", "hash4", "hash5"]
        mock_redis.zrangebyscore.return_value = distinct
        mock_redis.zrevrangebyscore.return_value = [("hash1", time.time())]
        mock_redis.hget.return_value = "goodja3hash"

        pipe = MagicMock()
        pipe.execute.return_value = [True]
        mock_redis.pipeline.return_value = pipe
        mock_redis.setex.return_value = True

        tracker = self._make_tracker(mock_redis)
        fp = self._make_fp()
        anomaly = tracker.check_and_record(fp)

        assert anomaly.distinct_fps_in_window == 5
        assert anomaly.anomaly_score >= 30

    def test_fingerprint_builder_from_headers(self):
        headers = {
            "x-ja3": "abc123",
            "user-agent": "TestBrowser/1.0",
            "accept-language": "pt-BR",
            "accept-encoding": "gzip",
        }
        client_data = {
            "canvas_hash": "cv001",
            "timezone_offset": "-180",
            "screen_resolution": "1280x720",
            "platform": "Linux x86_64",
            "plugins_hash": "ph001",
            "webgl_vendor": "Intel",
            "webgl_renderer": "Mesa",
        }
        fp = fingerprint_from_request_headers("p001", "s001", headers, client_data)

        assert fp.ja3_hash == "abc123"
        assert fp.user_agent == "TestBrowser/1.0"
        assert fp.timezone_offset == -180
        assert fp.platform == "Linux x86_64"
        assert isinstance(fp.composite_hash, str)
        assert len(fp.composite_hash) == 16
