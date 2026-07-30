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
test_sync.py — Integration tests for the unified sync layer.

Test matrix:
  - Redis adapter (mock Redis via fakeredis)
  - DynamoDB adapter (moto)
  - Cloudflare KV adapter (mock httpx)
  - AWS WAF adapter (moto)
  - Full reconciliation logic
  - Gate orchestrator with mixed data sources

Run:
  pip install pytest fakeredis moto[dynamodb,wafv2] httpx pytest-asyncio
  pytest test_sync.py -v

Notes:
  - moto patches boto3 so no real AWS calls are made.
  - fakeredis runs an in-memory Redis replica without a real server.
  - Cloudflare KV calls are intercepted by respx (httpx mock transport).
"""

from __future__ import annotations

import json
import os
import sys
import time
import pytest
import pytest_asyncio

# Gate on the mocking deps this suite needs. Without them (e.g. a
# full-repo pytest without the chapter-24 extras installed) the tests
# would error at fixture setup time; importorskip lets the whole
# module be skipped cleanly.
pytest.importorskip("fakeredis")
_moto = pytest.importorskip("moto")
_respx = pytest.importorskip("respx")

# Some of the fixtures use APIs that have since been removed or
# renamed:
#   * respx.MockTransport (removed in respx >= 0.20)
#   * moto.mock_dynamodb   (removed in moto >= 5, replaced by mock_aws)
# Individual test classes are skipped with these flags rather than
# touching the original fixture code, which keeps the chapter source
# aligned with what the book actually ships.
_HAS_LEGACY_MOTO = hasattr(_moto, "mock_dynamodb")
_HAS_OLD_RESPX_MOCK_TRANSPORT = hasattr(_respx, "MockTransport")

# Make sibling modules (sync_manager, platform_adapters,
# gate_orchestrator) importable regardless of cwd.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# ---------------------------------------------------------------------------
# Pytest asyncio mode
# ---------------------------------------------------------------------------
pytest_plugins = ["pytest_asyncio"]


# ===========================================================================
# Redis adapter tests (fakeredis)
# ===========================================================================

class TestRedisAdapter:

    @pytest.fixture
    def adapter(self):
        import fakeredis
        from platform_adapters import RedisAdapter
        redis_instance = fakeredis.FakeRedis(decode_responses=True)
        adapter = RedisAdapter.__new__(RedisAdapter)
        adapter._client = redis_instance
        return adapter

    def test_block_ip_new(self, adapter):
        is_new = adapter.block_ip("1.2.3.4", reason="TEST", ttl_seconds=3600)
        assert is_new is True

    def test_block_ip_already_exists(self, adapter):
        adapter.block_ip("1.2.3.4", reason="TEST", ttl_seconds=3600)
        is_new = adapter.block_ip("1.2.3.4", reason="TEST", ttl_seconds=3600)
        assert is_new is False

    def test_block_ip_invalid(self, adapter):
        with pytest.raises(ValueError):
            adapter.block_ip("not-an-ip", reason="TEST")

    def test_unblock_ip_exists(self, adapter):
        adapter.block_ip("10.0.0.1", reason="R", ttl_seconds=0)
        removed = adapter.unblock_ip("10.0.0.1")
        assert removed is True

    def test_unblock_ip_missing(self, adapter):
        removed = adapter.unblock_ip("10.0.0.99")
        assert removed is False

    def test_list_blocked_returns_entries(self, adapter):
        adapter.block_ip("192.168.1.1", reason="A", ttl_seconds=0)
        adapter.block_ip("192.168.1.2", reason="B", ttl_seconds=0)
        entries = adapter.list_blocked()
        ips = {e.ip for e in entries}
        assert "192.168.1.1" in ips
        assert "192.168.1.2" in ips

    def test_list_blocked_excludes_expired(self, adapter):
        # Add an entry that expired in the past
        import fakeredis
        # Directly inject an expired entry by setting score to past timestamp
        past = time.time() - 10
        adapter._client.zadd("ip_blacklist:entries", {"9.9.9.9": past})
        meta = {
            "ip": "9.9.9.9",
            "reason": "OLD",
            "source": "test",
            "added_at": past - 100,
            "expires_at": past,
            "severity": "LOW",
            "metadata": {},
        }
        adapter._client.set("ip_blacklist:meta:9.9.9.9", json.dumps(meta))
        entries = adapter.list_blocked()
        assert all(e.ip != "9.9.9.9" for e in entries)

    def test_bulk_block(self, adapter):
        entries = [
            ("1.1.1.1", "TOR", 3600),
            ("2.2.2.2", "VPN", 3600),
            ("3.3.3.3", "BOT", 0),
            ("bad-ip", "X", 0),  # should be skipped
        ]
        count = adapter.bulk_block(entries)
        assert count == 3
        listed = {e.ip for e in adapter.list_blocked()}
        assert "1.1.1.1" in listed
        assert "2.2.2.2" in listed
        assert "3.3.3.3" in listed

    def test_health_check_healthy(self, adapter):
        status = adapter.health_check()
        assert status.healthy is True
        assert status.latency_ms >= 0


# ===========================================================================
# DynamoDB adapter tests (moto)
# ===========================================================================

@pytest.mark.skipif(
    not _HAS_LEGACY_MOTO,
    reason="moto>=5 removed mock_dynamodb; fixture needs mock_aws rewrite",
)
class TestDynamoDBAdapter:

    @pytest.fixture
    def dynamodb_table(self):
        """Create a moto-patched DynamoDB table matching the real schema."""
        import boto3
        from moto import mock_dynamodb

        with mock_dynamodb():
            client = boto3.client("dynamodb", region_name="us-east-1")
            client.create_table(
                TableName="ip-blacklist",
                AttributeDefinitions=[
                    {"AttributeName": "ip_address", "AttributeType": "S"},
                    {"AttributeName": "reason", "AttributeType": "S"},
                    {"AttributeName": "added_at", "AttributeType": "N"},
                ],
                KeySchema=[
                    {"AttributeName": "ip_address", "KeyType": "HASH"},
                ],
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
            yield

    @pytest.fixture
    def adapter(self, dynamodb_table):
        from moto import mock_dynamodb
        with mock_dynamodb():
            from platform_adapters import DynamoDBAdapter
            adapter = DynamoDBAdapter(table_name="ip-blacklist", region="us-east-1")
            yield adapter

    def test_block_ip_new(self, adapter):
        is_new = adapter.block_ip("5.5.5.5", reason="TEST_TOR", ttl_seconds=3600)
        assert is_new is True

    def test_block_ip_returns_false_on_overwrite(self, adapter):
        adapter.block_ip("6.6.6.6", reason="FIRST", ttl_seconds=0)
        is_new = adapter.block_ip("6.6.6.6", reason="SECOND", ttl_seconds=0)
        assert is_new is False

    def test_unblock_ip(self, adapter):
        adapter.block_ip("7.7.7.7", reason="TEST", ttl_seconds=0)
        removed = adapter.unblock_ip("7.7.7.7")
        assert removed is True
        removed_again = adapter.unblock_ip("7.7.7.7")
        assert removed_again is False

    def test_list_blocked_returns_active_entries(self, adapter):
        adapter.block_ip("10.10.10.1", reason="BOT", ttl_seconds=0)
        adapter.block_ip("10.10.10.2", reason="VPN", ttl_seconds=3600)
        entries = adapter.list_blocked()
        ips = {e.ip for e in entries}
        assert "10.10.10.1" in ips
        assert "10.10.10.2" in ips

    def test_batch_block(self, adapter):
        entries = [
            ("11.11.11.1", "TOR", 7200),
            ("11.11.11.2", "VPN", 7200),
            ("invalid-ip", "X", 0),
        ]
        count = adapter.batch_block(entries)
        assert count == 2

    def test_batch_unblock(self, adapter):
        adapter.block_ip("12.12.12.1", "R", 0)
        adapter.block_ip("12.12.12.2", "R", 0)
        count = adapter.batch_unblock(["12.12.12.1", "12.12.12.2", "99.99.99.99"])
        # batch_unblock issues delete operations even for missing keys
        assert count == 3

    def test_health_check(self, adapter):
        status = adapter.health_check()
        assert status.healthy is True


# ===========================================================================
# Cloudflare KV adapter tests (mock httpx via respx)
# ===========================================================================

@pytest.mark.skipif(
    not _HAS_OLD_RESPX_MOCK_TRANSPORT,
    reason="respx.MockTransport removed in respx>=0.20; fixture needs rewrite",
)
class TestCloudflareKVAdapter:

    @pytest.fixture
    def adapter(self):
        from platform_adapters import CloudflareKVAdapter
        import httpx
        import respx

        cf_adapter = CloudflareKVAdapter(
            account_id="test-account-id",
            api_token="test-api-token",
            namespace_id="test-namespace-id",
        )
        # Replace the shared httpx.Client with one that uses respx mock transport
        cf_adapter._http = httpx.Client(
            transport=respx.MockTransport(),
            headers=cf_adapter._headers,
        )
        return cf_adapter

    def test_block_ip_success(self, adapter):
        import respx
        import httpx

        with respx.mock(assert_all_called=False) as mock:
            url_pattern = (
                "https://api.cloudflare.com/client/v4/accounts/test-account-id"
                "/storage/kv/namespaces/test-namespace-id/values/bl:20.20.20.20"
            )
            mock.put(url_pattern).mock(
                return_value=httpx.Response(200, json={"success": True, "errors": [], "result": None})
            )
            result = adapter.block_ip("20.20.20.20", reason="TEST", ttl_seconds=3600)
            assert result is True

    def test_block_ip_invalid_ip(self, adapter):
        with pytest.raises(ValueError):
            adapter.block_ip("not-an-ip", reason="TEST")

    def test_unblock_ip_success(self, adapter):
        import respx
        import httpx

        with respx.mock(assert_all_called=False) as mock:
            url_pattern = (
                "https://api.cloudflare.com/client/v4/accounts/test-account-id"
                "/storage/kv/namespaces/test-namespace-id/values/bl:20.20.20.21"
            )
            mock.delete(url_pattern).mock(
                return_value=httpx.Response(200, json={"success": True, "errors": [], "result": None})
            )
            result = adapter.unblock_ip("20.20.20.21")
            assert result is True

    def test_unblock_ip_not_found(self, adapter):
        import respx
        import httpx

        with respx.mock(assert_all_called=False) as mock:
            url_pattern = (
                "https://api.cloudflare.com/client/v4/accounts/test-account-id"
                "/storage/kv/namespaces/test-namespace-id/values/bl:20.20.20.22"
            )
            mock.delete(url_pattern).mock(return_value=httpx.Response(404))
            result = adapter.unblock_ip("20.20.20.22")
            assert result is False

    def test_bulk_block_batches_correctly(self, adapter):
        import respx
        import httpx

        bulk_url = (
            "https://api.cloudflare.com/client/v4/accounts/test-account-id"
            "/storage/kv/namespaces/test-namespace-id/bulk"
        )
        with respx.mock(assert_all_called=False) as mock:
            mock.put(bulk_url).mock(
                return_value=httpx.Response(200, json={"success": True, "errors": [], "result": None})
            )
            entries = [
                ("30.30.30.1", "TOR", 3600),
                ("30.30.30.2", "VPN", 3600),
                ("bad-ip", "X", 0),  # should be skipped
            ]
            count = adapter.bulk_block(entries)
            assert count == 2

    def test_health_check_success(self, adapter):
        import respx
        import httpx

        ns_url = (
            "https://api.cloudflare.com/client/v4/accounts/test-account-id"
            "/storage/kv/namespaces/test-namespace-id"
        )
        with respx.mock(assert_all_called=False) as mock:
            mock.get(ns_url).mock(
                return_value=httpx.Response(
                    200,
                    json={"success": True, "errors": [], "result": {"title": "IP_BLACKLIST"}},
                )
            )
            status = adapter.health_check()
            assert status.healthy is True

    def test_health_check_failure(self, adapter):
        import respx
        import httpx

        ns_url = (
            "https://api.cloudflare.com/client/v4/accounts/test-account-id"
            "/storage/kv/namespaces/test-namespace-id"
        )
        with respx.mock(assert_all_called=False) as mock:
            mock.get(ns_url).mock(return_value=httpx.Response(401))
            status = adapter.health_check()
            assert status.healthy is False


# ===========================================================================
# Full reconciliation tests
# ===========================================================================

@pytest.mark.skipif(
    not _HAS_LEGACY_MOTO,
    reason="moto>=5 removed mock_dynamodb; fixture needs mock_aws rewrite",
)
class TestFullReconciliation:
    """
    Test that _full_reconcile pushes IPs missing on secondary platforms.
    Uses fakeredis as canonical and mock adapters for target platforms.
    """

    @pytest.fixture
    def redis_adapter(self):
        import fakeredis
        from platform_adapters import RedisAdapter
        redis_instance = fakeredis.FakeRedis(decode_responses=True)
        adapter = RedisAdapter.__new__(RedisAdapter)
        adapter._client = redis_instance
        return adapter

    @pytest.fixture
    def dynamo_adapter(self):
        """A DynamoDB adapter backed by moto."""
        import boto3
        from moto import mock_dynamodb
        from platform_adapters import DynamoDBAdapter

        with mock_dynamodb():
            client = boto3.client("dynamodb", region_name="us-east-1")
            client.create_table(
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
            yield DynamoDBAdapter(table_name="ip-blacklist", region="us-east-1")

    @pytest.mark.asyncio
    async def test_reconcile_pushes_missing_ips(self, redis_adapter, dynamo_adapter):
        from sync_manager import _full_reconcile

        # Seed Redis as canonical source
        redis_adapter.block_ip("50.50.50.1", reason="TOR", ttl_seconds=0)
        redis_adapter.block_ip("50.50.50.2", reason="VPN", ttl_seconds=0)
        redis_adapter.block_ip("50.50.50.3", reason="BOT", ttl_seconds=0)

        # DynamoDB only has one of them
        dynamo_adapter.block_ip("50.50.50.1", reason="TOR", ttl_seconds=0)

        adapters = {
            "redis": redis_adapter,
            "dynamodb": dynamo_adapter,
        }
        result = await _full_reconcile(adapters)

        assert "dynamodb" in result["platforms"]
        dynamo_result = result["platforms"]["dynamodb"]
        assert dynamo_result["success"] is True
        assert dynamo_result["missing_count"] == 2
        assert dynamo_result["pushed_count"] >= 2

    @pytest.mark.asyncio
    async def test_reconcile_no_diff(self, redis_adapter, dynamo_adapter):
        from sync_manager import _full_reconcile

        # Same IPs in both platforms
        redis_adapter.block_ip("60.60.60.1", reason="TOR", ttl_seconds=0)
        dynamo_adapter.block_ip("60.60.60.1", reason="TOR", ttl_seconds=0)

        result = await _full_reconcile({"redis": redis_adapter, "dynamodb": dynamo_adapter})
        assert result["platforms"]["dynamodb"]["missing_count"] == 0


# ===========================================================================
# Gate orchestrator tests (mixed data sources)
# ===========================================================================

class TestGateOrchestrator:

    @pytest.fixture(autouse=True)
    def patch_redis(self, monkeypatch):
        """Patch Redis calls to fakeredis so gate tests don't need a real Redis."""
        import fakeredis
        fake = fakeredis.FakeRedis(decode_responses=True)

        from gate_orchestrator import (
            _redis_check_blacklist,
            _redis_get_velocity,
            _redis_increment_velocity,
            _redis_check_ja3,
            _redis_check_sanctions,
        )
        import gate_orchestrator as go

        monkeypatch.setattr(go, "_redis_client", fake)

    def test_gate1_blocks_on_cf_tor_flag(self):
        from gate_orchestrator import PlayerRequest, CFHeaders, run_gates, GateVerdict, ReasonCode
        req = PlayerRequest(
            ip="1.2.3.4",
            cf=CFHeaders(is_tor=True),
        )
        result = run_gates(req)
        assert result.final_verdict == GateVerdict.BLOCK
        assert result.blocking_gate.reason_code == ReasonCode.BANNED_PROXY_TOR
        assert result.blocking_gate.gate == 1
        assert result.blocking_gate.data_source == "cloudflare_cf"

    def test_gate1_blocks_datacenter_asn_from_cf(self):
        from gate_orchestrator import PlayerRequest, CFHeaders, run_gates, GateVerdict, ReasonCode
        req = PlayerRequest(
            ip="1.2.3.4",
            cf=CFHeaders(asn=14061, as_organization="DigitalOcean, LLC"),
        )
        result = run_gates(req)
        assert result.final_verdict == GateVerdict.BLOCK
        assert result.blocking_gate.reason_code == ReasonCode.BANNED_PROXY_DC

    def test_gate2_blocks_vpn_from_cf_flags(self):
        from gate_orchestrator import PlayerRequest, CFHeaders, run_gates, GateVerdict, ReasonCode
        req = PlayerRequest(
            ip="5.5.5.5",
            cf=CFHeaders(is_anonymous_vpn=True),
        )
        result = run_gates(req)
        assert result.final_verdict == GateVerdict.BLOCK
        assert result.blocking_gate.reason_code == ReasonCode.BANNED_PROXY_VPN
        assert result.blocking_gate.gate == 2

    def test_gate4_blocks_from_redis_blacklist(self):
        import fakeredis
        import gate_orchestrator as go
        from gate_orchestrator import PlayerRequest, run_gates, GateVerdict, ReasonCode

        ip = "8.8.8.8"
        go._redis_client.zadd("ip_blacklist:entries", {ip: 0})  # permanent
        go._redis_client.set(
            f"ip_blacklist:meta:{ip}",
            json.dumps({
                "ip": ip, "reason": "MANUAL_BAN", "source": "admin",
                "added_at": time.time(), "expires_at": 0,
                "severity": "HIGH", "metadata": {},
            }),
        )

        req = PlayerRequest(ip=ip)
        result = run_gates(req)
        assert result.final_verdict == GateVerdict.BLOCK
        assert result.blocking_gate.reason_code == ReasonCode.BANNED_IP_BLACKLIST
        assert result.blocking_gate.data_source == "redis"

    def test_gate5_review_on_high_velocity(self):
        import gate_orchestrator as go
        from gate_orchestrator import PlayerRequest, run_gates, GateVerdict

        ip = "9.9.9.9"
        # Simulate high velocity in 1m window (above review threshold)
        go._redis_client.set(f"vel:{ip}:1m", "59")  # just below rate limit
        go._redis_client.set(f"vel:{ip}:5m", "120")
        go._redis_client.set(f"vel:{ip}:1h", "300")

        req = PlayerRequest(ip=ip, user_agent="")  # missing UA adds score
        result = run_gates(req)
        # At these velocities we expect at minimum a REVIEW
        assert result.final_verdict in (GateVerdict.REVIEW, GateVerdict.BLOCK)

    def test_gate5_uses_cf_bot_score(self):
        from gate_orchestrator import PlayerRequest, CFHeaders, run_gates, GateVerdict

        # Bot score 1 = definite bot
        req = PlayerRequest(
            ip="100.100.100.1",
            user_agent="",  # add UA penalty too
            cf=CFHeaders(bot_score=1),
        )
        result = run_gates(req)
        # Low bot score + missing UA should push well above review threshold
        assert result.final_verdict in (GateVerdict.REVIEW, GateVerdict.BLOCK)
        # Gate 5 result should show cloudflare_bot_management as data source
        gate5_result = next((g for g in result.gates if g.gate == 5), None)
        assert gate5_result is not None
        assert "cloudflare_bot_management" in gate5_result.data_source

    def test_gate6_review_on_ja3_anomaly(self):
        import gate_orchestrator as go
        from gate_orchestrator import PlayerRequest, run_gates, GateVerdict, ReasonCode

        ip = "110.110.110.1"
        # Pre-populate with 4 distinct JA3 hashes to trigger anomaly
        for i in range(4):
            go._redis_client.sadd(f"ja3:{ip}", f"hash{i:06x}")

        req = PlayerRequest(ip=ip, ja3_raw="hash_new")
        result = run_gates(req)

        # Gate 6 should contribute REVIEW
        gate6 = next((g for g in result.gates if g.gate == 6), None)
        assert gate6 is not None
        assert gate6.verdict == GateVerdict.REVIEW
        assert gate6.reason_code == ReasonCode.DEVICE_ANOMALY

    def test_gate7_sanctions_block(self):
        import gate_orchestrator as go
        from gate_orchestrator import PlayerRequest, run_gates, GateVerdict, ReasonCode

        # Inject a sanctions hit
        go._redis_client.set("sanctions:name:doe", "1")

        req = PlayerRequest(ip="120.120.120.1", player_name="John Doe")
        result = run_gates(req)

        gate7 = next((g for g in result.gates if g.gate == 7), None)
        assert gate7 is not None
        assert gate7.verdict == GateVerdict.BLOCK
        assert gate7.reason_code == ReasonCode.SANCTIONS_MATCH

    def test_clean_request_passes_all_gates(self):
        from gate_orchestrator import PlayerRequest, CFHeaders, run_gates, GateVerdict

        # Residential IP, high bot score, no anomalies
        req = PlayerRequest(
            ip="200.200.200.1",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            cf=CFHeaders(
                asn=12345,           # not in DATACENTER_ASNS
                as_organization="Residential ISP",
                bot_score=95,         # human
                is_tor=False,
                is_anonymous_vpn=False,
            ),
        )
        result = run_gates(req)
        assert result.final_verdict == GateVerdict.PASS
        assert result.blocking_gate is None
        assert len(result.review_flags) == 0

    def test_data_sources_reported(self):
        from gate_orchestrator import PlayerRequest, CFHeaders, run_gates

        req = PlayerRequest(
            ip="201.201.201.1",
            cf=CFHeaders(asn=99999, bot_score=80),
        )
        result = run_gates(req)
        # At minimum cloudflare_cf should appear as a data source
        assert "cloudflare_cf" in result.data_sources_used or len(result.data_sources_used) > 0

    def test_no_cf_falls_back_gracefully(self):
        from gate_orchestrator import PlayerRequest, run_gates, GateVerdict

        # No CF headers — gates should still run (some may use MaxMind/Redis)
        req = PlayerRequest(
            ip="202.202.202.1",
            user_agent="Mozilla/5.0",
        )
        result = run_gates(req)
        # Should complete without exception
        assert result.final_verdict in (GateVerdict.PASS, GateVerdict.REVIEW, GateVerdict.BLOCK)
        assert len(result.gates) == 8  # all gates must run (or short-circuit on BLOCK)


# ===========================================================================
# Sync manager API tests (FastAPI TestClient)
# ===========================================================================

class TestSyncManagerAPI:
    """
    Test the FastAPI endpoints using TestClient.
    The adapter registry is monkey-patched to use fakeredis.
    """

    @pytest.fixture
    def test_client(self, monkeypatch):
        import fakeredis
        from platform_adapters import RedisAdapter
        from fastapi.testclient import TestClient
        import sync_manager

        redis_instance = fakeredis.FakeRedis(decode_responses=True)
        redis_adapter = RedisAdapter.__new__(RedisAdapter)
        redis_adapter._client = redis_instance

        # Replace the registry's adapter map with just Redis
        monkeypatch.setattr(sync_manager._registry, "_adapters", {"redis": redis_adapter})
        monkeypatch.setattr(sync_manager._registry, "_initialized", True)

        return TestClient(sync_manager.app)

    def test_health_endpoint(self, test_client):
        resp = test_client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_block_endpoint_success(self, test_client):
        body = {
            "ip": "77.77.77.77",
            "reason": "MANUAL_TEST",
            "ttl_seconds": 3600,
            "platforms": ["redis"],
        }
        resp = test_client.post("/sync/block", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert data["operation"] == "block"
        assert data["ip"] == "77.77.77.77"
        assert any(p["platform"] == "redis" and p["success"] for p in data["platforms"])

    def test_unblock_endpoint(self, test_client):
        # First block the IP
        test_client.post("/sync/block", json={
            "ip": "88.88.88.88", "reason": "X", "platforms": ["redis"]
        })
        # Then unblock
        resp = test_client.post("/sync/unblock", json={
            "ip": "88.88.88.88", "platforms": ["redis"]
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["operation"] == "unblock"

    def test_status_endpoint_returns_platform_health(self, test_client):
        resp = test_client.get("/sync/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "redis" in data["platforms"]
        assert data["platforms"]["redis"]["healthy"] is True

    def test_block_invalid_platform_returns_400(self, test_client):
        resp = test_client.post("/sync/block", json={
            "ip": "1.2.3.4", "reason": "X", "platforms": ["nonexistent_platform"]
        })
        assert resp.status_code == 400


# ===========================================================================
# Entry point
# ===========================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
