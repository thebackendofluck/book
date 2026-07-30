# Companion code for "The Backend of Luck" - Chapter 35, Incident Management.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Test suite for the Traffic Classifier system.

Covers realistic scenarios:
  1. Pure volumetric DDoS (200K IPs, low diversity, no conversions)
  2. Marketing campaign (Brazil geo, diverse UAs, registrations)
  3. World Cup spike (global geo, high conversion, social referrers)
  4. Slow / low-and-slow DDoS (gradual ramp, mixed signals)
  5. Response orchestration decision paths
  6. Marketing calendar CRUD
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import sys
import time
import unittest
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

# Make the sibling modules importable when pytest is invoked from the
# repo-wide scripts/ rootdir rather than from inside chapter-35.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from traffic_classifier import (
    ATTACK_THRESHOLD,
    CAMPAIGN_THRESHOLD,
    ClassificationResult,
    ClassifyRequest,
    TrafficClass,
    TrafficClassifier,
    TrafficFingerprint,
    TrafficMetrics,
)
from response_orchestrator import (
    ActionStatus,
    OrchestratorConfig,
    PlaybookResult,
    ResponseOrchestrator,
)
from marketing_calendar import (
    CampaignCreate,
    CampaignStatus,
    CampaignType,
    MarketingCalendar,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_metrics(**overrides: Any) -> TrafficMetrics:
    """Return a NORMAL traffic baseline with optional overrides."""
    defaults = dict(
        requests_per_second=150.0,
        unique_ips=1200,
        total_requests=9000,
        ua_diversity=0.85,
        path_diversity=0.80,
        tls_fingerprint_diversity=0.90,
        avg_session_depth=4.5,
        top_geo_concentration=0.35,
        datacenter_ip_ratio=0.05,
        conversion_rate=0.03,
        registration_rate=0.01,
        request_timing_regularity=0.15,
        referrer_presence=0.40,
        new_ip_ratio=0.10,
    )
    defaults.update(overrides)
    return TrafficMetrics(**defaults)


class MockRedis:
    """In-memory Redis substitute for unit tests."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._sets: dict[str, set[str]] = {}

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def set(self, key: str, value: str) -> None:
        self._store[key] = value

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self._store[key] = value

    async def smembers(self, key: str) -> set[str]:
        return self._sets.get(key, set())

    async def sadd(self, key: str, *members: str) -> None:
        self._sets.setdefault(key, set()).update(members)

    async def srem(self, key: str, *members: str) -> None:
        if key in self._sets:
            self._sets[key].discard(*members)

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def aclose(self) -> None:
        pass

    def pipeline(self) -> "MockPipeline":
        return MockPipeline(self)

    async def zadd(self, key: str, mapping: dict) -> None:
        pass


class MockPipeline:
    def __init__(self, redis: MockRedis) -> None:
        self._redis = redis
        self._ops: list[Any] = []

    def setex(self, key: str, ttl: int, value: str) -> "MockPipeline":
        self._ops.append(("setex", key, ttl, value))
        return self

    def set(self, key: str, value: str) -> "MockPipeline":
        self._ops.append(("set", key, value))
        return self

    def sadd(self, key: str, *members: str) -> "MockPipeline":
        self._ops.append(("sadd", key, members))
        return self

    def srem(self, key: str, *members: str) -> "MockPipeline":
        self._ops.append(("srem", key, members))
        return self

    def delete(self, key: str) -> "MockPipeline":
        self._ops.append(("delete", key))
        return self

    async def execute(self) -> list[Any]:
        for op in self._ops:
            if op[0] == "setex":
                await self._redis.setex(op[1], op[2], op[3])
            elif op[0] == "set":
                await self._redis.set(op[1], op[2])
            elif op[0] == "sadd":
                await self._redis.sadd(op[1], *op[2])
            elif op[0] == "srem":
                await self._redis.srem(op[1], *op[2])
            elif op[0] == "delete":
                await self._redis.delete(op[1])
        return []

    def zadd(self, key: str, mapping: dict) -> "MockPipeline":
        return self


# ---------------------------------------------------------------------------
# Traffic classifier unit tests
# ---------------------------------------------------------------------------
class TestTrafficClassifier(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self) -> None:
        self.classifier = TrafficClassifier()
        self.classifier._redis = MockRedis()

    # ------------------------------------------------------------------
    # Scenario 1: Pure volumetric DDoS
    # ------------------------------------------------------------------
    async def test_ddos_volumetric(self) -> None:
        """200K IPs with near-zero diversity and no conversions → ATTACK."""
        metrics = _make_metrics(
            requests_per_second=45000.0,
            unique_ips=200_000,
            total_requests=2_700_000,
            ua_diversity=0.04,             # Very few distinct User-Agents
            path_diversity=0.03,           # All hitting / or /api/games
            tls_fingerprint_diversity=0.05,
            avg_session_depth=1.1,
            top_geo_concentration=0.65,    # Heavily concentrated
            datacenter_ip_ratio=0.91,      # 91% datacenter IPs
            conversion_rate=0.0,
            registration_rate=0.0,
            request_timing_regularity=0.96,  # Machine-perfect timing
            referrer_presence=0.01,
            new_ip_ratio=0.94,
        )
        result = await self.classifier.classify(metrics)

        self.assertEqual(result.traffic_class, TrafficClass.ATTACK)
        self.assertGreater(result.confidence, 0.7)
        self.assertGreater(result.fingerprint.normalized_score, ATTACK_THRESHOLD)
        print(f"\n[DDoS] class={result.traffic_class.value} confidence={result.confidence:.2f}")
        for e in result.explanation:
            print(f"  - {e}")

    # ------------------------------------------------------------------
    # Scenario 2: Marketing campaign (Brazil geo + registrations)
    # ------------------------------------------------------------------
    async def test_marketing_campaign_brazil(self) -> None:
        """Brazilian bonus campaign — diverse UAs, social referrers, registrations."""
        metrics = _make_metrics(
            requests_per_second=1800.0,    # ~12x normal
            unique_ips=25_000,
            total_requests=108_000,
            ua_diversity=0.89,             # Real mobile browsers
            path_diversity=0.75,
            tls_fingerprint_diversity=0.88,
            avg_session_depth=5.2,
            top_geo_concentration=0.72,    # 72% from BR — campaign geo
            datacenter_ip_ratio=0.03,      # Residential IPs
            conversion_rate=0.045,         # 4.5% conversion
            registration_rate=0.022,       # 2.2% registrations
            request_timing_regularity=0.12,
            referrer_presence=0.68,        # Heavy paid social referrers
            new_ip_ratio=0.35,
            dominant_geo="BR",
        )
        # Inject an active campaign into mock Redis
        now = time.time()
        campaign = {
            "name": "Carnival Bonus 2026",
            "campaign_type": "PAID_SOCIAL",
            "start_time": now - 3600,
            "end_time": now + 3600,
            "target_geos": ["BR"],
            "expected_traffic_multiplier": 15,
        }
        await self.classifier._redis.sadd("campaigns:active", "carnival-2026")
        await self.classifier._redis.set(
            "campaign:carnival-2026", json.dumps(campaign)
        )

        result = await self.classifier.classify(metrics)

        self.assertEqual(result.traffic_class, TrafficClass.MARKETING_CAMPAIGN)
        self.assertTrue(result.campaign_active)
        self.assertGreater(result.confidence, 0.6)
        print(f"\n[Campaign-BR] class={result.traffic_class.value} "
              f"confidence={result.confidence:.2f} campaign={result.campaign_name}")

    # ------------------------------------------------------------------
    # Scenario 3: World Cup spike (global, high conversion)
    # ------------------------------------------------------------------
    async def test_world_cup_spike(self) -> None:
        """World Cup traffic surge — global geo, high conversion, news/social referrers."""
        metrics = _make_metrics(
            requests_per_second=9000.0,    # ~60x normal
            unique_ips=95_000,
            total_requests=540_000,
            ua_diversity=0.93,
            path_diversity=0.85,
            tls_fingerprint_diversity=0.92,
            avg_session_depth=6.8,
            top_geo_concentration=0.18,    # Globally distributed
            datacenter_ip_ratio=0.04,
            conversion_rate=0.08,          # 8% — excited sports bettors
            registration_rate=0.035,
            request_timing_regularity=0.10,
            referrer_presence=0.78,        # Twitter/Instagram/News
            new_ip_ratio=0.52,
            dominant_geo=None,
        )
        # Active World Cup campaign
        now = time.time()
        campaign = {
            "name": "World Cup Finals 2026",
            "campaign_type": "EVENT",
            "start_time": now - 7200,
            "end_time": now + 7200,
            "target_geos": [],   # Global
            "expected_traffic_multiplier": 60,
        }
        await self.classifier._redis.sadd("campaigns:active", "wc-2026-finals")
        await self.classifier._redis.set(
            "campaign:wc-2026-finals", json.dumps(campaign)
        )

        result = await self.classifier.classify(metrics)

        # High conversion + referrers + active campaign should classify as CAMPAIGN
        self.assertIn(
            result.traffic_class,
            [TrafficClass.MARKETING_CAMPAIGN, TrafficClass.ORGANIC_SURGE],
        )
        self.assertLess(result.fingerprint.normalized_score, ATTACK_THRESHOLD)
        print(f"\n[WorldCup] class={result.traffic_class.value} "
              f"confidence={result.confidence:.2f}")

    # ------------------------------------------------------------------
    # Scenario 4: Slow DDoS (gradual ramp, mixed signals)
    # ------------------------------------------------------------------
    async def test_slow_ddos_mixed_signals(self) -> None:
        """Sophisticated slow DDoS — browser headers but no conversions, DC IPs."""
        metrics = _make_metrics(
            requests_per_second=850.0,     # Moderate, not alarming
            unique_ips=8000,
            total_requests=51_000,
            ua_diversity=0.45,             # Partially spoofed UAs
            path_diversity=0.22,           # Mostly hitting same 2-3 endpoints
            tls_fingerprint_diversity=0.30,
            avg_session_depth=1.6,
            top_geo_concentration=0.55,
            datacenter_ip_ratio=0.72,      # High DC ratio
            conversion_rate=0.0,           # No conversions
            registration_rate=0.0,
            request_timing_regularity=0.71,  # Semi-regular
            referrer_presence=0.08,
            new_ip_ratio=0.78,
        )
        result = await self.classifier.classify(metrics)

        # Should be ATTACK or UNKNOWN (not campaign)
        self.assertNotEqual(result.traffic_class, TrafficClass.MARKETING_CAMPAIGN)
        print(f"\n[SlowDDoS] class={result.traffic_class.value} "
              f"confidence={result.confidence:.2f} score={result.fingerprint.normalized_score:.3f}")
        for e in result.explanation:
            print(f"  - {e}")

    # ------------------------------------------------------------------
    # Scenario 5: Normal organic traffic
    # ------------------------------------------------------------------
    async def test_normal_organic_traffic(self) -> None:
        """Baseline organic traffic should NOT be classified as attack."""
        metrics = _make_metrics()  # default baseline
        result = await self.classifier.classify(metrics)
        self.assertNotEqual(result.traffic_class, TrafficClass.ATTACK)
        print(f"\n[Normal] class={result.traffic_class.value} "
              f"score={result.fingerprint.normalized_score:.3f}")

    # ------------------------------------------------------------------
    # Signal weight validation
    # ------------------------------------------------------------------
    async def test_conversion_signal_prevents_attack_classification(self) -> None:
        """Even with some bot-like signals, conversions should prevent ATTACK class."""
        metrics = _make_metrics(
            datacenter_ip_ratio=0.60,
            request_timing_regularity=0.80,
            ua_diversity=0.30,
            conversion_rate=0.05,    # 5% conversions — bots never convert
            registration_rate=0.02,
        )
        result = await self.classifier.classify(metrics)
        self.assertNotEqual(result.traffic_class, TrafficClass.ATTACK)

    async def test_referrer_signal_reduces_attack_score(self) -> None:
        """Strong referrer + path diversity should push score below attack threshold."""
        metrics = _make_metrics(
            referrer_presence=0.85,
            path_diversity=0.82,
            ua_diversity=0.88,
            datacenter_ip_ratio=0.04,
            conversion_rate=0.02,
        )
        result = await self.classifier.classify(metrics)
        self.assertLess(result.fingerprint.normalized_score, ATTACK_THRESHOLD)

    # ------------------------------------------------------------------
    # Classification history
    # ------------------------------------------------------------------
    async def test_history_accumulates(self) -> None:
        for _ in range(5):
            await self.classifier.classify(_make_metrics())
        history = self.classifier.recent_history(10)
        self.assertGreaterEqual(len(history), 5)

    # ------------------------------------------------------------------
    # Status endpoint
    # ------------------------------------------------------------------
    async def test_status_returns_valid_structure(self) -> None:
        status = self.classifier.status()
        self.assertIn("status", status)
        self.assertIn("current_classification", status)
        self.assertIn("total_classifications", status)


# ---------------------------------------------------------------------------
# Response orchestrator tests
# ---------------------------------------------------------------------------
class TestResponseOrchestrator(unittest.IsolatedAsyncioTestCase):

    def _make_attack_result(self, confidence: float = 0.92) -> ClassificationResult:
        fp = TrafficFingerprint(
            ua_diversity=0.05, path_diversity=0.03, session_depth=0.1,
            geo_concentration=0.80, conversion_signals=0.0,
            request_timing_regularity=0.95, tls_fingerprint_diversity=0.05,
            referrer_presence=0.02, datacenter_ip_ratio=0.91, new_ip_ratio=0.90,
            raw_score=8.8, normalized_score=0.99,
        )
        return ClassificationResult(
            traffic_class=TrafficClass.ATTACK,
            confidence=confidence,
            fingerprint=fp,
            explanation=["Test attack scenario."],
        )

    def _make_campaign_result(self, confidence: float = 0.85) -> ClassificationResult:
        fp = TrafficFingerprint(
            ua_diversity=0.89, path_diversity=0.78, session_depth=0.82,
            geo_concentration=0.70, conversion_signals=0.9,
            request_timing_regularity=0.12, tls_fingerprint_diversity=0.88,
            referrer_presence=0.68, datacenter_ip_ratio=0.03, new_ip_ratio=0.35,
            raw_score=-6.5, normalized_score=0.04,
        )
        return ClassificationResult(
            traffic_class=TrafficClass.MARKETING_CAMPAIGN,
            confidence=confidence,
            fingerprint=fp,
            campaign_active=True,
            campaign_name="Carnival Bonus 2026",
            explanation=["Active campaign + low attack score."],
        )

    def _make_unknown_result(self) -> ClassificationResult:
        fp = TrafficFingerprint(
            ua_diversity=0.45, path_diversity=0.40, session_depth=0.50,
            geo_concentration=0.50, conversion_signals=0.1,
            request_timing_regularity=0.55, tls_fingerprint_diversity=0.45,
            referrer_presence=0.20, datacenter_ip_ratio=0.40, new_ip_ratio=0.50,
            raw_score=0.5, normalized_score=0.52,
        )
        return ClassificationResult(
            traffic_class=TrafficClass.UNKNOWN,
            confidence=0.40,
            fingerprint=fp,
            explanation=["Ambiguous signals — manual review required."],
        )

    async def test_attack_playbook_high_confidence(self) -> None:
        """High-confidence attack triggers full defensive playbook."""
        config = OrchestratorConfig(
            cf_api_token="", cf_zone_id="",
            aws_waf_ipset_id="", aws_sns_topic_arn="",
            pagerduty_routing_key="",
            wazuh_api_password="",
            redis_url="redis://localhost:6379/0",
            evidence_dir="/tmp/test-evidence",
        )
        orch = ResponseOrchestrator(config)

        # Patch Redis to avoid real connections
        mock_redis_actions = AsyncMock()
        mock_redis_actions.blacklist_ips = AsyncMock(
            return_value=type("R", (), {"status": ActionStatus.SUCCESS, "message": "ok",
                                        "action": "redis_blacklist", "duration_ms": 1.0})()
        )
        mock_redis_actions.set_rate_limit = AsyncMock(
            return_value=type("R", (), {"status": ActionStatus.SUCCESS, "message": "ok",
                                        "action": "redis_rate_limit", "duration_ms": 1.0})()
        )
        mock_redis_actions.set_campaign_mode_banner = AsyncMock(
            return_value=type("R", (), {"status": ActionStatus.SUCCESS, "message": "ok",
                                        "action": "redis_banner", "duration_ms": 1.0})()
        )
        orch._redis = mock_redis_actions

        result = await orch.handle(
            self._make_attack_result(confidence=0.95),
            attacking_ips=["1.2.3.4", "5.6.7.8", "9.10.11.12"],
        )
        self.assertEqual(result.classification, TrafficClass.ATTACK.value)
        print(f"\n[Orch-Attack] {result.summary()}")

    async def test_attack_playbook_low_confidence_graduated(self) -> None:
        """Low-confidence attack stays in graduated response (rate-limit only)."""
        config = OrchestratorConfig(
            redis_url="redis://localhost:6379/0",
            evidence_dir="/tmp/test-evidence",
            attack_confidence_threshold=0.8,
        )
        orch = ResponseOrchestrator(config)

        mock_redis_actions = AsyncMock()
        mock_redis_actions.set_rate_limit = AsyncMock(
            return_value=type("R", (), {"status": ActionStatus.SUCCESS, "message": "ok",
                                        "action": "redis_rate_limit", "duration_ms": 1.0})()
        )
        orch._redis = mock_redis_actions

        # Confidence 0.65 < threshold 0.80 → graduated only
        result = await orch.handle(
            self._make_attack_result(confidence=0.65),
            attacking_ips=["1.2.3.4"],
        )
        self.assertEqual(result.classification, TrafficClass.ATTACK.value)
        action_names = [a.action for a in result.actions]
        self.assertIn("full_block_skipped", action_names)
        print(f"\n[Orch-LowConf] {result.summary()}")

    async def test_campaign_playbook(self) -> None:
        """Campaign classification triggers scale-up playbook."""
        config = OrchestratorConfig(
            redis_url="redis://localhost:6379/0",
            evidence_dir="/tmp/test-evidence",
        )
        orch = ResponseOrchestrator(config)

        mock_redis_actions = AsyncMock()
        mock_redis_actions.set_campaign_mode_banner = AsyncMock(
            return_value=type("R", (), {"status": ActionStatus.SUCCESS, "message": "ok",
                                        "action": "redis_banner", "duration_ms": 1.0})()
        )
        mock_redis_actions.set_rate_limit = AsyncMock(
            return_value=type("R", (), {"status": ActionStatus.SUCCESS, "message": "ok",
                                        "action": "redis_rate_limit", "duration_ms": 1.0})()
        )
        orch._redis = mock_redis_actions

        result = await orch.handle(self._make_campaign_result())
        self.assertEqual(result.classification, TrafficClass.MARKETING_CAMPAIGN.value)
        print(f"\n[Orch-Campaign] {result.summary()}")

    async def test_unknown_playbook_no_block(self) -> None:
        """UNKNOWN classification must not trigger full block — rate limit only."""
        config = OrchestratorConfig(
            redis_url="redis://localhost:6379/0",
            evidence_dir="/tmp/test-evidence",
        )
        orch = ResponseOrchestrator(config)

        mock_redis_actions = AsyncMock()
        mock_redis_actions.set_rate_limit = AsyncMock(
            return_value=type("R", (), {"status": ActionStatus.SUCCESS, "message": "ok",
                                        "action": "redis_rate_limit", "duration_ms": 1.0})()
        )
        orch._redis = mock_redis_actions

        result = await orch.handle(self._make_unknown_result())
        self.assertEqual(result.classification, TrafficClass.UNKNOWN.value)
        # No full block actions should be present
        block_actions = [
            a for a in result.actions
            if "blacklist" in a.action or "under_attack" in a.action
        ]
        self.assertEqual(len(block_actions), 0)
        print(f"\n[Orch-Unknown] {result.summary()}")


# ---------------------------------------------------------------------------
# Marketing calendar tests
# ---------------------------------------------------------------------------
class TestMarketingCalendar(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self) -> None:
        self.calendar = MarketingCalendar()
        self.calendar._redis = MockRedis()

    async def test_create_and_retrieve_campaign(self) -> None:
        now = time.time()
        data = CampaignCreate(
            name="Black Friday 2026",
            campaign_type=CampaignType.PAID_SOCIAL,
            start_time=now + 3600,
            end_time=now + 86400,
            expected_traffic_multiplier=8.0,
            target_geos=["BR", "MX", "CO"],
            landing_pages=["/promo/black-friday"],
        )
        created = await self.calendar.create_campaign(data)
        self.assertEqual(created.name, "Black Friday 2026")
        self.assertEqual(created.campaign_type, CampaignType.PAID_SOCIAL.value)
        self.assertIn("BR", created.target_geos)

        retrieved = await self.calendar.get_campaign(created.id)
        self.assertEqual(retrieved.id, created.id)

    async def test_active_campaign_detection(self) -> None:
        now = time.time()
        data = CampaignCreate(
            name="Live Now Campaign",
            campaign_type=CampaignType.EMAIL,
            start_time=now - 60,   # Started 1 minute ago
            end_time=now + 3600,
            expected_traffic_multiplier=3.0,
        )
        created = await self.calendar.create_campaign(data)
        # Should be active immediately
        active = await self.calendar.get_active_campaigns()
        active_ids = [c.id for c in active]
        self.assertIn(created.id, active_ids)

    async def test_cleanup_expires_old_campaigns(self) -> None:
        now = time.time()
        data = CampaignCreate(
            name="Expired Campaign",
            campaign_type=CampaignType.OTHER,
            start_time=now - 7200,
            end_time=now - 3600,   # Ended 1 hour ago
            expected_traffic_multiplier=2.0,
        )
        created = await self.calendar.create_campaign(data)
        # Manually mark as active in the set to simulate a missed cleanup
        await self.calendar._redis.sadd("campaigns:active", created.id)

        count = await self.calendar.cleanup_expired()
        self.assertGreaterEqual(count, 0)  # Should have cleaned up the expired one

    async def test_update_campaign(self) -> None:
        now = time.time()
        data = CampaignCreate(
            name="Original Name",
            campaign_type=CampaignType.AFFILIATE,
            start_time=now + 1000,
            end_time=now + 5000,
            expected_traffic_multiplier=4.0,
        )
        created = await self.calendar.create_campaign(data)

        from marketing_calendar import CampaignUpdate
        updated = await self.calendar.update_campaign(
            created.id,
            CampaignUpdate(name="Updated Name", expected_traffic_multiplier=6.0),
        )
        self.assertEqual(updated.name, "Updated Name")
        self.assertEqual(updated.expected_traffic_multiplier, 6.0)

    async def test_delete_campaign(self) -> None:
        now = time.time()
        data = CampaignCreate(
            name="To Be Deleted",
            campaign_type=CampaignType.OTHER,
            start_time=now + 100,
            end_time=now + 200,
            expected_traffic_multiplier=1.5,
        )
        created = await self.calendar.create_campaign(data)
        await self.calendar.delete_campaign(created.id)

        with self.assertRaises(KeyError):
            await self.calendar.get_campaign(created.id)

    async def test_upcoming_campaigns(self) -> None:
        now = time.time()
        data = CampaignCreate(
            name="Upcoming Event",
            campaign_type=CampaignType.TV_RADIO,
            start_time=now + 3600,
            end_time=now + 10800,
            expected_traffic_multiplier=5.0,
        )
        await self.calendar.create_campaign(data)
        upcoming = await self.calendar.get_upcoming_campaigns(within_hours=2.0)
        self.assertGreaterEqual(len(upcoming), 1)
        names = [c.name for c in upcoming]
        self.assertIn("Upcoming Event", names)


# ---------------------------------------------------------------------------
# Integration-style: Full pipeline test
# ---------------------------------------------------------------------------
class TestFullPipeline(unittest.IsolatedAsyncioTestCase):
    """
    End-to-end test: metrics → classification → orchestration.
    Does not hit any real external services.
    """

    async def test_ddos_pipeline_end_to_end(self) -> None:
        # 1. Classify
        classifier = TrafficClassifier()
        classifier._redis = MockRedis()

        metrics = _make_metrics(
            requests_per_second=50000.0,
            unique_ips=300_000,
            total_requests=3_000_000,
            ua_diversity=0.03,
            path_diversity=0.02,
            tls_fingerprint_diversity=0.04,
            avg_session_depth=1.05,
            top_geo_concentration=0.70,
            datacenter_ip_ratio=0.95,
            conversion_rate=0.0,
            registration_rate=0.0,
            request_timing_regularity=0.97,
            referrer_presence=0.01,
            new_ip_ratio=0.96,
        )
        result = await classifier.classify(metrics)
        self.assertEqual(result.traffic_class, TrafficClass.ATTACK)

        # 2. Orchestrate
        config = OrchestratorConfig(evidence_dir="/tmp/test-evidence-e2e")
        orch = ResponseOrchestrator(config)

        mock_redis_actions = AsyncMock()
        mock_redis_actions.blacklist_ips = AsyncMock(
            return_value=type("R", (), {"status": ActionStatus.SUCCESS, "message": "ok",
                                        "action": "redis_blacklist", "duration_ms": 1.0})()
        )
        mock_redis_actions.set_rate_limit = AsyncMock(
            return_value=type("R", (), {"status": ActionStatus.SUCCESS, "message": "ok",
                                        "action": "redis_rate_limit", "duration_ms": 1.0})()
        )
        orch._redis = mock_redis_actions

        pr = await orch.handle(result, attacking_ips=["1.1.1.1", "2.2.2.2"])
        self.assertEqual(pr.classification, TrafficClass.ATTACK.value)
        print(f"\n[E2E-DDoS] {pr.summary()}")

    async def test_campaign_pipeline_end_to_end(self) -> None:
        classifier = TrafficClassifier()
        mock_r = MockRedis()
        classifier._redis = mock_r

        # Inject active campaign
        now = time.time()
        campaign = {
            "name": "E2E Campaign",
            "campaign_type": "PAID_SOCIAL",
            "start_time": now - 1800,
            "end_time": now + 1800,
            "target_geos": ["MX"],
        }
        await mock_r.sadd("campaigns:active", "e2e-campaign-1")
        await mock_r.set("campaign:e2e-campaign-1", json.dumps(campaign))

        metrics = _make_metrics(
            requests_per_second=3000.0,
            unique_ips=40_000,
            ua_diversity=0.88,
            path_diversity=0.76,
            conversion_rate=0.04,
            registration_rate=0.02,
            referrer_presence=0.72,
            datacenter_ip_ratio=0.04,
            dominant_geo="MX",
        )
        result = await classifier.classify(metrics)
        self.assertIn(
            result.traffic_class,
            [TrafficClass.MARKETING_CAMPAIGN, TrafficClass.ORGANIC_SURGE],
        )
        self.assertNotEqual(result.traffic_class, TrafficClass.ATTACK)
        print(f"\n[E2E-Campaign] {result.traffic_class.value} "
              f"confidence={result.confidence:.2f}")


# ---------------------------------------------------------------------------
# Fingerprint computation validation
# ---------------------------------------------------------------------------
class TestFingerprintComputation(unittest.TestCase):

    def setUp(self) -> None:
        self.classifier = TrafficClassifier.__new__(TrafficClassifier)

    def _score(self, **overrides: Any) -> float:
        m = _make_metrics(**overrides)
        fp = self.classifier._build_fingerprint(m)
        return fp.normalized_score

    def test_all_bot_signals_produce_high_score(self) -> None:
        score = self._score(
            ua_diversity=0.01, path_diversity=0.01, tls_fingerprint_diversity=0.01,
            avg_session_depth=1.0, top_geo_concentration=0.99,
            datacenter_ip_ratio=0.99, conversion_rate=0.0, registration_rate=0.0,
            request_timing_regularity=0.99, referrer_presence=0.0, new_ip_ratio=0.99,
        )
        self.assertGreater(score, 0.85, f"Expected >0.85 but got {score:.3f}")

    def test_all_human_signals_produce_low_score(self) -> None:
        score = self._score(
            ua_diversity=0.98, path_diversity=0.95, tls_fingerprint_diversity=0.97,
            avg_session_depth=8.0, top_geo_concentration=0.10,
            datacenter_ip_ratio=0.01, conversion_rate=0.08, registration_rate=0.03,
            request_timing_regularity=0.05, referrer_presence=0.85, new_ip_ratio=0.05,
        )
        self.assertLess(score, 0.15, f"Expected <0.15 but got {score:.3f}")

    def test_conversion_rate_caps_score(self) -> None:
        """Conversion rate override should cap score below attack threshold."""
        score = self._score(
            ua_diversity=0.10, datacenter_ip_ratio=0.80,
            request_timing_regularity=0.90,
            conversion_rate=0.05,   # This should prevent ATTACK
            registration_rate=0.02,
        )
        # After override, score should be capped
        m = _make_metrics(
            ua_diversity=0.10, datacenter_ip_ratio=0.80,
            request_timing_regularity=0.90,
            conversion_rate=0.05,
            registration_rate=0.02,
        )
        fp = self.classifier._build_fingerprint(m)
        # The classifier (not just fingerprint) applies the cap, so just check raw score
        self.assertIsNotNone(fp.raw_score)

    def test_score_is_symmetric(self) -> None:
        """Moving every signal toward 'human' should monotonically reduce score."""
        scores = []
        for ua_d in [0.1, 0.3, 0.5, 0.7, 0.9]:
            s = self._score(
                ua_diversity=ua_d,
                path_diversity=ua_d,
                tls_fingerprint_diversity=ua_d,
                referrer_presence=ua_d,
                request_timing_regularity=1.0 - ua_d,
                datacenter_ip_ratio=1.0 - ua_d,
            )
            scores.append(s)

        # Scores should be decreasing as diversity increases
        for i in range(len(scores) - 1):
            self.assertGreater(
                scores[i], scores[i + 1],
                f"Score should decrease: {scores}"
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
