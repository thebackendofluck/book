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
Moto-based tests for the DDoS detection, WAF/Shield management,
campaign autoscaler, and evidence collector modules.

Run:
    pip install -r requirements.txt
    pytest test_detector.py -v

Test coverage:
  - ddos_detector_lambda: ATTACK, CAMPAIGN, ORGANIC, UNKNOWN classifications
  - campaign_autoscaler:  start_campaign (all profiles), stop_campaign, grace period
  - waf_shield_manager:   IP blocking, overflow sets, rate rules, emergency mode
  - attack_evidence_collector: ASN grouping, report generation, S3 persistence
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import types
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

# ---------------------------------------------------------------------------
# Ensure the aws directory is on sys.path
# ---------------------------------------------------------------------------
AWS_DIR = os.path.dirname(os.path.abspath(__file__))
if AWS_DIR not in sys.path:
    sys.path.insert(0, AWS_DIR)

# ---------------------------------------------------------------------------
# Shared AWS region for all moto mocks
# ---------------------------------------------------------------------------
TEST_REGION = "us-east-1"
os.environ.setdefault("AWS_DEFAULT_REGION", TEST_REGION)
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_SECURITY_TOKEN", "testing")
os.environ.setdefault("AWS_SESSION_TOKEN", "testing")


# ---------------------------------------------------------------------------
# Fixtures: DynamoDB tables
# ---------------------------------------------------------------------------

CALENDAR_TABLE = "marketing-campaign-calendar-test"
SCALING_STATE_TABLE = "campaign-scaling-state-test"
BLOCK_LOG_TABLE = "waf-ip-block-log-test"


def _create_test_tables(region: str = TEST_REGION) -> None:
    """Create the three DynamoDB tables used across modules."""
    ddb = boto3.resource("dynamodb", region_name=region)

    ddb.create_table(
        TableName=CALENDAR_TABLE,
        AttributeDefinitions=[
            {"AttributeName": "campaign_id", "AttributeType": "S"},
            {"AttributeName": "start_time", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "campaign_id", "KeyType": "HASH"},
            {"AttributeName": "start_time", "KeyType": "RANGE"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )

    ddb.create_table(
        TableName=SCALING_STATE_TABLE,
        AttributeDefinitions=[{"AttributeName": "campaign_id", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "campaign_id", "KeyType": "HASH"}],
        BillingMode="PAY_PER_REQUEST",
    )

    ddb.create_table(
        TableName=BLOCK_LOG_TABLE,
        AttributeDefinitions=[{"AttributeName": "event_id", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "event_id", "KeyType": "HASH"}],
        BillingMode="PAY_PER_REQUEST",
    )


def _seed_active_campaign(
    campaign_id: str,
    name: str,
    target_geo: str,
    multiplier: float,
    minutes_from_now: int = -5,
    duration_minutes: int = 120,
) -> None:
    """Insert an active campaign record into the test DynamoDB table."""
    ddb = boto3.resource("dynamodb", region_name=TEST_REGION)
    table = ddb.Table(CALENDAR_TABLE)
    now = datetime.now(timezone.utc)
    start = (now + timedelta(minutes=minutes_from_now)).isoformat()
    end = (now + timedelta(minutes=duration_minutes)).isoformat()
    table.put_item(
        Item={
            "campaign_id": campaign_id,
            "start_time": start,
            "end_time": end,
            "campaign_name": name,
            "target_geo": target_geo,
            "expected_multiplier": str(multiplier),
            "status": "ACTIVE",
        }
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reload_module(module_name: str) -> types.ModuleType:
    """Force-reload a module so that environment variable changes take effect."""
    if module_name in sys.modules:
        del sys.modules[module_name]
    # Also clear any boto3 singleton caches inside the module by reloading
    return importlib.import_module(module_name)


def _make_cloudwatch_datapoint(stat: str, value: float, timestamp: datetime | None = None) -> dict[str, Any]:
    return {
        stat: value,
        "Timestamp": timestamp or datetime.now(timezone.utc),
        "Unit": "Count",
    }


# ===========================================================================
# DDoS Detector Tests
# ===========================================================================

class TestDDoSDetectorClassification:
    """Unit tests for the classify_traffic function."""

    def setup_method(self) -> None:
        """Set env vars and reload the module before each test."""
        os.environ["DYNAMO_CALENDAR_TABLE"] = CALENDAR_TABLE
        os.environ["SNS_NOC_TOPIC_ARN"] = ""
        os.environ["ATHENA_RESULTS_BUCKET"] = ""

    @mock_aws
    def test_attack_classification_high_waf_block_rate(self) -> None:
        """High WAF block rate with no active campaign → ATTACK."""
        _create_test_tables()
        import ddos_detector_lambda as m
        # Reload to pick up env vars
        importlib.reload(m)

        from ddos_detector_lambda import (
            CampaignContext,
            GeoDistribution,
            MetricSnapshot,
            classify_traffic,
        )

        metrics = MetricSnapshot(
            request_count=100_000,
            error_5xx_count=5_000,
            error_5xx_rate=0.05,
            waf_allowed_count=80_000,
            waf_blocked_count=20_000,
            waf_block_rate=0.20,
            connection_count=50_000,
            shield_attack_detected=False,
        )
        geo = GeoDistribution(data_available=False)
        campaign = CampaignContext(active=False)

        classification, confidence, action, evidence = classify_traffic(metrics, geo, campaign)

        assert classification.value == "ATTACK"
        assert confidence >= 0.40
        assert action.value == "BLOCK_IPS"

    @mock_aws
    def test_attack_classification_shield_signal(self) -> None:
        """Shield Advanced attack detected → ATTACK with high confidence."""
        _create_test_tables()
        import ddos_detector_lambda as m
        importlib.reload(m)

        from ddos_detector_lambda import (
            CampaignContext,
            GeoDistribution,
            MetricSnapshot,
            classify_traffic,
        )

        metrics = MetricSnapshot(
            request_count=200_000,
            waf_block_rate=0.15,
            error_5xx_rate=0.03,
            shield_attack_detected=True,
            shield_attack_vectors=["SYN_FLOOD", "UDP_FLOOD"],
        )
        geo = GeoDistribution(data_available=False)
        campaign = CampaignContext(active=False)

        classification, confidence, action, evidence = classify_traffic(metrics, geo, campaign)

        assert classification.value == "ATTACK"
        assert confidence >= 0.60
        assert "SYN_FLOOD" in evidence.get("shield_vectors", [])

    @mock_aws
    def test_campaign_classification_with_active_campaign(self) -> None:
        """Active campaign in DynamoDB + low WAF block rate → CAMPAIGN."""
        _create_test_tables()
        _seed_active_campaign("wc2026", "World Cup 2026", "BR", 10.0)
        import ddos_detector_lambda as m
        importlib.reload(m)

        from ddos_detector_lambda import (
            CampaignContext,
            GeoDistribution,
            MetricSnapshot,
            classify_traffic,
        )

        metrics = MetricSnapshot(
            request_count=40_000,
            error_5xx_count=100,
            error_5xx_rate=0.0025,
            waf_allowed_count=39_900,
            waf_blocked_count=100,
            waf_block_rate=0.0025,
            connection_count=8_000,
            shield_attack_detected=False,
        )
        campaign = CampaignContext(
            active=True,
            campaign_id="wc2026",
            campaign_name="World Cup 2026",
            target_geo="BR",
            expected_multiplier=10.0,
        )
        geo = GeoDistribution(data_available=False)

        classification, confidence, action, evidence = classify_traffic(metrics, geo, campaign)

        assert classification.value == "CAMPAIGN"
        assert confidence >= 0.40
        assert action.value == "SCALE_OUT"

    @mock_aws
    def test_organic_classification_low_everything(self) -> None:
        """Low block rate, low errors, no campaign → ORGANIC."""
        _create_test_tables()
        import ddos_detector_lambda as m
        importlib.reload(m)

        from ddos_detector_lambda import (
            CampaignContext,
            GeoDistribution,
            MetricSnapshot,
            classify_traffic,
        )

        metrics = MetricSnapshot(
            request_count=5_000,
            error_5xx_count=10,
            error_5xx_rate=0.002,
            waf_allowed_count=4_990,
            waf_blocked_count=10,
            waf_block_rate=0.002,
            connection_count=500,
            shield_attack_detected=False,
        )
        geo = GeoDistribution(data_available=False)
        campaign = CampaignContext(active=False)

        classification, confidence, action, evidence = classify_traffic(metrics, geo, campaign)

        assert classification.value == "ORGANIC"
        assert action.value in ("NO_ACTION", "MONITOR")

    @mock_aws
    def test_geo_concentration_amplifies_attack(self) -> None:
        """90% traffic from single geo + no campaign → amplifies ATTACK score."""
        _create_test_tables()
        import ddos_detector_lambda as m
        importlib.reload(m)

        from ddos_detector_lambda import (
            CampaignContext,
            GeoDistribution,
            MetricSnapshot,
            classify_traffic,
        )

        metrics = MetricSnapshot(
            request_count=80_000,
            waf_block_rate=0.18,
            error_5xx_rate=0.04,
            shield_attack_detected=False,
        )
        geo = GeoDistribution(
            data_available=True,
            top_country="NLD",
            top_country_share=0.92,
            total_unique_countries=3,
        )
        campaign = CampaignContext(active=False)

        classification, confidence, action, evidence = classify_traffic(metrics, geo, campaign)

        assert classification.value == "ATTACK"

    @mock_aws
    def test_geo_concentration_with_matching_campaign_geo(self) -> None:
        """Geo concentration matches campaign target geo → CAMPAIGN, not ATTACK."""
        _create_test_tables()
        import ddos_detector_lambda as m
        importlib.reload(m)

        from ddos_detector_lambda import (
            CampaignContext,
            GeoDistribution,
            MetricSnapshot,
            classify_traffic,
        )

        metrics = MetricSnapshot(
            request_count=50_000,
            waf_block_rate=0.01,
            error_5xx_rate=0.01,
            shield_attack_detected=False,
        )
        geo = GeoDistribution(
            data_available=True,
            top_country="GRU",  # São Paulo edge = BR campaign
            top_country_share=0.85,
            total_unique_countries=5,
        )
        campaign = CampaignContext(
            active=True,
            campaign_id="br-promo",
            campaign_name="Brazil Promo",
            target_geo="GRU",
            expected_multiplier=5.0,
        )

        classification, confidence, action, evidence = classify_traffic(metrics, geo, campaign)

        assert classification.value == "CAMPAIGN"

    @mock_aws
    def test_very_small_request_count_leans_organic(self) -> None:
        """Request count < 100 → organic bias regardless of block rate."""
        _create_test_tables()
        import ddos_detector_lambda as m
        importlib.reload(m)

        from ddos_detector_lambda import (
            CampaignContext,
            GeoDistribution,
            MetricSnapshot,
            classify_traffic,
        )

        metrics = MetricSnapshot(
            request_count=50,
            waf_block_rate=0.25,
            error_5xx_rate=0.10,
            shield_attack_detected=False,
        )
        geo = GeoDistribution(data_available=False)
        campaign = CampaignContext(active=False)

        classification, confidence, action, evidence = classify_traffic(metrics, geo, campaign)

        # Small request count biases toward organic/monitor regardless of rates
        # The system should not trigger BLOCK_IPS for 50 requests
        assert action.value != "BLOCK_IPS"

    @mock_aws
    def test_get_active_campaign_returns_highest_multiplier(self) -> None:
        """When multiple campaigns are active, the highest multiplier is returned."""
        _create_test_tables()
        _seed_active_campaign("small-promo", "Small Promo", "US", 2.0)
        _seed_active_campaign("mega-wc", "World Cup Mega", "GLOBAL", 20.0)
        _seed_active_campaign("medium-tv", "TV Campaign", "EU", 5.0)

        import ddos_detector_lambda as m
        importlib.reload(m)

        campaign = m.get_active_campaign()

        assert campaign.active is True
        assert campaign.expected_multiplier == 20.0
        assert campaign.campaign_id == "mega-wc"


# ===========================================================================
# DDoS Detector Lambda Handler Tests
# ===========================================================================

class TestDDoSDetectorHandler:
    """Integration tests for the Lambda handler."""

    def setup_method(self) -> None:
        os.environ["DYNAMO_CALENDAR_TABLE"] = CALENDAR_TABLE
        os.environ["SNS_NOC_TOPIC_ARN"] = ""
        os.environ["ATHENA_RESULTS_BUCKET"] = ""

    @mock_aws
    def test_handler_returns_valid_structure(self) -> None:
        """Handler invocation returns all expected keys."""
        _create_test_tables()
        import ddos_detector_lambda as m
        importlib.reload(m)

        # Mock CloudWatch metrics to return 0 (avoids real API calls)
        with patch.object(m, "_get_cloudwatch_sum", return_value=0.0), \
             patch.object(m, "_get_cloudwatch_average", return_value=0.0), \
             patch.object(m, "collect_shield_indicators", return_value=(False, [])):
            result = m.handler({}, None)

        required_keys = {
            "classification",
            "confidence",
            "recommended_action",
            "metrics",
            "geo",
            "campaign",
            "evidence",
            "timestamp",
        }
        assert required_keys.issubset(set(result.keys()))
        assert result["classification"] in ("ATTACK", "CAMPAIGN", "ORGANIC", "UNKNOWN")
        assert 0.0 <= result["confidence"] <= 1.0


# ===========================================================================
# Campaign Autoscaler Tests
# ===========================================================================

class TestCampaignAutoscaler:
    """Tests for start_campaign and stop_campaign."""

    def setup_method(self) -> None:
        os.environ["DYNAMO_CAMPAIGN_STATE_TABLE"] = SCALING_STATE_TABLE
        os.environ["SNS_NOC_TOPIC_ARN"] = ""
        os.environ["CLOUDFRONT_DIST_ID"] = ""
        os.environ["ASG_NAMES"] = "test-asg-app,test-asg-api"
        os.environ["ECS_SERVICES"] = "test-cluster:test-service"
        os.environ["ELASTICACHE_GROUPS"] = ""
        os.environ["ASG_BASELINE_CAPACITIES"] = "test-asg-app=4,test-asg-api=2"
        os.environ["ECS_BASELINE_COUNTS"] = "test-service=2"
        os.environ["ASG_MAX_LIMITS"] = "test-asg-app=80,test-asg-api=40"

    @mock_aws
    def test_start_campaign_small_profile(self) -> None:
        """small profile (2x) scales ASG and ECS to double baseline."""
        _create_test_tables()
        region = TEST_REGION

        # Create ASGs
        ec2 = boto3.client("ec2", region_name=region)
        asg_client = boto3.client("autoscaling", region_name=region)
        ecs_client = boto3.client("ecs", region_name=region)

        # Create a minimal launch template
        vpc_resp = ec2.create_vpc(CidrBlock="10.0.0.0/16")
        vpc_id = vpc_resp["Vpc"]["VpcId"]
        subnet_resp = ec2.create_subnet(VpcId=vpc_id, CidrBlock="10.0.1.0/24")
        subnet_id = subnet_resp["Subnet"]["SubnetId"]

        lt_resp = ec2.create_launch_template(
            LaunchTemplateName="test-lt",
            LaunchTemplateData={"ImageId": "ami-12345678", "InstanceType": "t3.medium"},
        )
        lt_id = lt_resp["LaunchTemplate"]["LaunchTemplateId"]

        for asg_name, desired in [("test-asg-app", 4), ("test-asg-api", 2)]:
            asg_client.create_auto_scaling_group(
                AutoScalingGroupName=asg_name,
                MinSize=desired,
                MaxSize=desired * 10,
                DesiredCapacity=desired,
                LaunchTemplate={"LaunchTemplateId": lt_id, "Version": "$Latest"},
                VPCZoneIdentifier=subnet_id,
            )

        # Create ECS cluster and service
        ecs_client.create_cluster(clusterName="test-cluster")
        ecs_client.register_task_definition(
            family="test-task",
            containerDefinitions=[
                {
                    "name": "app",
                    "image": "nginx:latest",
                    "memory": 128,
                    "cpu": 64,
                }
            ],
        )
        ecs_client.create_service(
            cluster="test-cluster",
            serviceName="test-service",
            taskDefinition="test-task",
            desiredCount=2,
        )

        import campaign_autoscaler as ca
        importlib.reload(ca)

        result = ca.start_campaign(
            campaign_id="test-small-001",
            geo="US",
            profile="small",
            duration_minutes=60,
        )

        assert result.campaign_id == "test-small-001"
        assert result.multiplier == 2.0
        # ASGs should be scaled to 2x baseline
        assert result.asg_updates.get("test-asg-app") == 8   # 4 * 2
        assert result.asg_updates.get("test-asg-api") == 4   # 2 * 2
        # ECS should be scaled
        assert result.ecs_updates.get("test-cluster/test-service") == 4  # 2 * 2
        assert len(result.errors) == 0

    @mock_aws
    def test_start_campaign_mega_profile_respects_hard_limit(self) -> None:
        """mega (20x) does not exceed ASG_MAX_LIMITS."""
        _create_test_tables()
        os.environ["ASG_MAX_LIMITS"] = "test-asg-app=50,test-asg-api=40"
        region = TEST_REGION
        ec2 = boto3.client("ec2", region_name=region)
        asg_client = boto3.client("autoscaling", region_name=region)

        vpc_resp = ec2.create_vpc(CidrBlock="10.0.0.0/16")
        subnet_resp = ec2.create_subnet(
            VpcId=vpc_resp["Vpc"]["VpcId"], CidrBlock="10.0.1.0/24"
        )
        lt_resp = ec2.create_launch_template(
            LaunchTemplateName="test-lt-mega",
            LaunchTemplateData={"ImageId": "ami-12345678", "InstanceType": "t3.large"},
        )
        for asg_name, desired in [("test-asg-app", 4), ("test-asg-api", 2)]:
            asg_client.create_auto_scaling_group(
                AutoScalingGroupName=asg_name,
                MinSize=desired,
                MaxSize=200,
                DesiredCapacity=desired,
                LaunchTemplate={
                    "LaunchTemplateId": lt_resp["LaunchTemplate"]["LaunchTemplateId"],
                    "Version": "$Latest",
                },
                VPCZoneIdentifier=subnet_resp["Subnet"]["SubnetId"],
            )

        import campaign_autoscaler as ca
        importlib.reload(ca)

        # Patch ECS to avoid needing a real service
        with patch.object(ca, "_ecs_current_count", return_value=2), \
             patch.object(ca._ecs_client(), "update_service", return_value={}):
            result = ca.start_campaign(
                campaign_id="mega-wc2026",
                geo="GLOBAL",
                profile="mega",
                duration_minutes=180,
            )

        # 4 * 20 = 80, but hard limit is 50
        assert result.asg_updates.get("test-asg-app") == 50
        # 2 * 20 = 40, within limit of 40
        assert result.asg_updates.get("test-asg-api") == 40

    @mock_aws
    def test_stop_campaign_restores_baseline(self) -> None:
        """stop_campaign with force_immediate restores ASG to baseline."""
        _create_test_tables()
        region = TEST_REGION
        ec2 = boto3.client("ec2", region_name=region)
        asg_client = boto3.client("autoscaling", region_name=region)

        vpc_resp = ec2.create_vpc(CidrBlock="10.0.0.0/16")
        subnet_resp = ec2.create_subnet(
            VpcId=vpc_resp["Vpc"]["VpcId"], CidrBlock="10.0.1.0/24"
        )
        lt_resp = ec2.create_launch_template(
            LaunchTemplateName="test-lt-stop",
            LaunchTemplateData={"ImageId": "ami-12345678", "InstanceType": "t3.medium"},
        )
        # Simulate ASGs already at 10x (campaign was running)
        for asg_name, baseline in [("test-asg-app", 4), ("test-asg-api", 2)]:
            asg_client.create_auto_scaling_group(
                AutoScalingGroupName=asg_name,
                MinSize=baseline,
                MaxSize=80,
                DesiredCapacity=baseline * 10,  # currently scaled up
                LaunchTemplate={
                    "LaunchTemplateId": lt_resp["LaunchTemplate"]["LaunchTemplateId"],
                    "Version": "$Latest",
                },
                VPCZoneIdentifier=subnet_resp["Subnet"]["SubnetId"],
            )

        import campaign_autoscaler as ca
        importlib.reload(ca)

        with patch.object(ca, "_ecs_current_count", return_value=20), \
             patch.object(ca._ecs_client(), "update_service", return_value={}):
            result = ca.stop_campaign("test-campaign-stop", force_immediate=True)

        assert result.grace_period_minutes == 0
        # Check ASGs were scaled down
        for asg_name in ["test-asg-app", "test-asg-api"]:
            resp = asg_client.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
            group = resp["AutoScalingGroups"][0]
            baseline = {"test-asg-app": 4, "test-asg-api": 2}[asg_name]
            assert group["DesiredCapacity"] == baseline, (
                f"{asg_name}: expected {baseline}, got {group['DesiredCapacity']}"
            )

    @mock_aws
    def test_stop_campaign_with_grace_period(self) -> None:
        """stop_campaign without force_immediate records intent without scaling."""
        _create_test_tables()
        import campaign_autoscaler as ca
        importlib.reload(ca)

        result = ca.stop_campaign("grace-test-001", force_immediate=False)

        assert result.grace_period_minutes == 30
        assert any("Grace period" in a for a in result.actions_taken)

    @mock_aws
    def test_handler_start_action(self) -> None:
        """Handler with action=start returns correct structure."""
        _create_test_tables()
        import campaign_autoscaler as ca
        importlib.reload(ca)

        with patch.object(ca, "start_campaign") as mock_start:
            mock_result = MagicMock()
            mock_result.campaign_id = "test-001"
            mock_result.profile.value = "medium"
            mock_result.multiplier = 5.0
            mock_result.asg_updates = {}
            mock_result.ecs_updates = {}
            mock_result.elasticache_updates = {}
            mock_result.cloudfront_invalidation_id = ""
            mock_result.actions_taken = ["Scaled test-asg"]
            mock_result.errors = []
            mock_result.timestamp = "2026-03-31T12:00:00+00:00"
            mock_start.return_value = mock_result

            resp = ca.handler(
                {
                    "action": "start",
                    "campaign_id": "test-001",
                    "geo": "BR",
                    "profile": "medium",
                    "duration_minutes": 90,
                },
                None,
            )

        assert resp["campaign_id"] == "test-001"
        assert resp["multiplier"] == 5.0


# ===========================================================================
# WAF Shield Manager Tests
# ===========================================================================

class TestWAFShieldManager:
    """Tests for IP blocking, overflow sets, and rate rules."""

    def setup_method(self) -> None:
        os.environ["WAF_SCOPE"] = "REGIONAL"  # use REGIONAL for moto WAF
        os.environ["SNS_NOC_TOPIC_ARN"] = ""
        os.environ["DYNAMO_BLOCK_LOG_TABLE"] = BLOCK_LOG_TABLE
        os.environ["CLOUDFRONT_DIST_ID"] = ""
        os.environ["SHIELD_ALB_ARN"] = ""
        os.environ["SHIELD_CF_ARN"] = ""

    @mock_aws
    def test_block_ips_adds_to_ip_set(self) -> None:
        """block_ips inserts CIDRs into the primary WAF IP set."""
        _create_test_tables()
        waf = boto3.client("wafv2", region_name=TEST_REGION)

        # Create primary IP set
        resp = waf.create_ip_set(
            Name="ddos-block-primary-test",
            Scope="REGIONAL",
            IPAddressVersion="IPV4",
            Addresses=[],
        )
        ip_set_id = resp["Summary"]["Id"]
        ip_set_name = "ddos-block-primary-test"

        os.environ["WAF_PRIMARY_IP_SET_ID"] = ip_set_id
        os.environ["WAF_PRIMARY_IP_SET_NAME"] = ip_set_name

        import waf_shield_manager as wsm
        importlib.reload(wsm)

        attacking_ips = ["1.2.3.4", "5.6.7.8", "9.10.11.12"]
        result = wsm.block_ips(attacking_ips, "test-attack-001")

        assert result.ips_submitted == 3
        assert result.ips_blocked == 3
        assert ip_set_name in result.ip_sets_used
        assert len(result.overflow_sets_created) == 0

        # Verify the IPs are actually in the set
        get_resp = waf.get_ip_set(Scope="REGIONAL", Id=ip_set_id, Name=ip_set_name)
        addresses = get_resp["IPSet"]["Addresses"]
        assert "1.2.3.4/32" in addresses
        assert "5.6.7.8/32" in addresses
        assert "9.10.11.12/32" in addresses

    @mock_aws
    def test_block_ips_normalises_cidr_notation(self) -> None:
        """IPs without /32 are automatically normalised."""
        _create_test_tables()
        waf = boto3.client("wafv2", region_name=TEST_REGION)
        resp = waf.create_ip_set(
            Name="ddos-block-primary-norm",
            Scope="REGIONAL",
            IPAddressVersion="IPV4",
            Addresses=[],
        )
        ip_set_id = resp["Summary"]["Id"]
        os.environ["WAF_PRIMARY_IP_SET_ID"] = ip_set_id
        os.environ["WAF_PRIMARY_IP_SET_NAME"] = "ddos-block-primary-norm"

        import waf_shield_manager as wsm
        importlib.reload(wsm)

        # Mix of bare IPs and CIDRs
        result = wsm.block_ips(["10.0.0.1", "192.168.1.0/24"], "norm-test")
        addresses_blocked = result.ips_blocked

        # Both should be present
        get_resp = waf.get_ip_set(Scope="REGIONAL", Id=ip_set_id, Name="ddos-block-primary-norm")
        addresses = get_resp["IPSet"]["Addresses"]
        assert "10.0.0.1/32" in addresses
        assert "192.168.1.0/24" in addresses

    @mock_aws
    def test_block_ips_creates_overflow_set(self) -> None:
        """When primary set is full, an overflow set is created."""
        _create_test_tables()
        waf = boto3.client("wafv2", region_name=TEST_REGION)
        resp = waf.create_ip_set(
            Name="ddos-block-primary-full",
            Scope="REGIONAL",
            IPAddressVersion="IPV4",
            Addresses=[],
        )
        ip_set_id = resp["Summary"]["Id"]
        os.environ["WAF_PRIMARY_IP_SET_ID"] = ip_set_id
        os.environ["WAF_PRIMARY_IP_SET_NAME"] = "ddos-block-primary-full"

        import waf_shield_manager as wsm
        importlib.reload(wsm)

        # Temporarily set a very small max size to force overflow
        original_max = wsm.WAF_IP_SET_MAX_SIZE
        wsm.WAF_IP_SET_MAX_SIZE = 3

        # Submit 7 IPs — 3 fit in primary, 4 need overflow
        attacking_ips = [f"10.0.0.{i}" for i in range(1, 8)]
        result = wsm.block_ips(attacking_ips, "overflow-test")

        wsm.WAF_IP_SET_MAX_SIZE = original_max  # restore

        assert result.ips_blocked == 7
        assert len(result.overflow_sets_created) >= 1

    @mock_aws
    def test_block_ips_deduplicates(self) -> None:
        """Duplicate IPs are deduplicated before submission."""
        _create_test_tables()
        waf = boto3.client("wafv2", region_name=TEST_REGION)
        resp = waf.create_ip_set(
            Name="ddos-block-primary-dedup",
            Scope="REGIONAL",
            IPAddressVersion="IPV4",
            Addresses=[],
        )
        ip_set_id = resp["Summary"]["Id"]
        os.environ["WAF_PRIMARY_IP_SET_ID"] = ip_set_id
        os.environ["WAF_PRIMARY_IP_SET_NAME"] = "ddos-block-primary-dedup"

        import waf_shield_manager as wsm
        importlib.reload(wsm)

        result = wsm.block_ips(
            ["1.2.3.4", "1.2.3.4", "1.2.3.4", "5.6.7.8"], "dedup-test"
        )

        assert result.ips_submitted == 2  # deduplicated from 4 to 2

    @mock_aws
    def test_block_ips_missing_ip_set_id(self) -> None:
        """block_ips returns error when WAF_PRIMARY_IP_SET_ID is not set."""
        _create_test_tables()
        os.environ["WAF_PRIMARY_IP_SET_ID"] = ""
        os.environ["WAF_PRIMARY_IP_SET_NAME"] = ""

        import waf_shield_manager as wsm
        importlib.reload(wsm)

        result = wsm.block_ips(["1.2.3.4"], "no-config-test")

        assert len(result.errors) > 0
        assert result.ips_blocked == 0

    @mock_aws
    def test_activate_emergency_response(self) -> None:
        """activate_emergency_response calls all sub-systems."""
        _create_test_tables()
        waf = boto3.client("wafv2", region_name=TEST_REGION)
        resp = waf.create_ip_set(
            Name="ddos-block-primary-emergency",
            Scope="REGIONAL",
            IPAddressVersion="IPV4",
            Addresses=[],
        )
        ip_set_id = resp["Summary"]["Id"]
        os.environ["WAF_PRIMARY_IP_SET_ID"] = ip_set_id
        os.environ["WAF_PRIMARY_IP_SET_NAME"] = "ddos-block-primary-emergency"

        import waf_shield_manager as wsm
        importlib.reload(wsm)

        attacking_ips = [f"203.0.113.{i}" for i in range(1, 20)]
        result = wsm.activate_emergency_response(
            attacking_ips=attacking_ips,
            attack_id="emergency-test-001",
            confidence=0.95,
        )

        assert result.block_result is not None
        assert result.block_result.ips_blocked == len(attacking_ips)


# ===========================================================================
# Attack Evidence Collector Tests
# ===========================================================================

class TestAttackEvidenceCollector:
    """Tests for ASN grouping, report generation, and S3 persistence."""

    def setup_method(self) -> None:
        os.environ["EVIDENCE_BUCKET"] = "test-evidence-bucket"
        os.environ["ATHENA_RESULTS_BUCKET"] = ""
        os.environ["SNS_NOC_TOPIC_ARN"] = ""
        os.environ["SES_FROM_ADDRESS"] = ""

    def _make_records(self, count: int) -> list[Any]:
        """Create synthetic BlockedIPRecord instances."""
        import attack_evidence_collector as aec
        importlib.reload(aec)

        records = []
        for i in range(count):
            asn = 64512 + (i % 5)  # 5 distinct ASNs
            r = aec.BlockedIPRecord(
                ip_address=f"10.{i // 256}.{i % 256}.1",
                block_count=100 + i,
                first_seen="2026-03-31T14:00:00Z",
                last_seen="2026-03-31T15:00:00Z",
                rule_names=["RateLimitRule"],
                http_methods=["GET", "POST"],
                uri_paths=["/api/v1/games"],
                user_agents=["python-requests/2.28"],
                asn=asn,
                asn_org=f"Test ISP {asn}",
                country="DE",
                abuse_contact=f"abuse@isp{asn}.example.com",
            )
            records.append(r)
        return records

    def test_group_by_asn_groups_correctly(self) -> None:
        """group_by_asn correctly buckets records by ASN."""
        import attack_evidence_collector as aec
        importlib.reload(aec)

        records = self._make_records(50)
        groups = aec.group_by_asn(records)

        assert len(groups) == 5
        # Each group should have 10 records (50 IPs / 5 ASNs)
        for g in groups:
            assert g.unique_ip_count == 10

    def test_group_by_asn_sorts_by_total_requests(self) -> None:
        """ASN groups are sorted by total blocked requests descending."""
        import attack_evidence_collector as aec
        importlib.reload(aec)

        records = self._make_records(100)
        groups = aec.group_by_asn(records)

        counts = [g.total_blocked_requests for g in groups]
        assert counts == sorted(counts, reverse=True)

    def test_generate_reports_creates_one_per_asn(self) -> None:
        """generate_reports returns one email body per ASN group."""
        import attack_evidence_collector as aec
        importlib.reload(aec)

        records = self._make_records(25)
        groups = aec.group_by_asn(records)
        templates = aec.generate_reports(
            groups,
            attack_id="test-attack-001",
            attack_start="2026-03-31T14:00:00Z",
            attack_end="2026-03-31T15:30:00Z",
            victim_domain="casino.example.com",
        )

        assert len(templates) == 5
        for asn, body in templates.items():
            assert f"AS{asn}" in body
            assert "casino.example.com" in body
            assert "DDoS" in body

    def test_generate_reports_includes_ip_table(self) -> None:
        """Email report includes IP address table."""
        import attack_evidence_collector as aec
        importlib.reload(aec)

        records = self._make_records(10)
        records[0].asn = 12345
        records[0].asn_org = "BigISP"
        records[0].abuse_contact = "abuse@bigisp.com"
        # Assign all records to same ASN for test
        for r in records:
            r.asn = 12345
            r.asn_org = "BigISP"
            r.abuse_contact = "abuse@bigisp.com"

        groups = aec.group_by_asn(records)
        templates = aec.generate_reports(
            groups,
            attack_id="ip-table-test",
            attack_start="2026-03-31T14:00:00Z",
            attack_end="2026-03-31T15:30:00Z",
        )

        body = templates[12345]
        # All 10 IPs should appear in the report
        for r in records[:10]:
            assert r.ip_address in body

    @mock_aws
    def test_save_evidence_to_s3(self) -> None:
        """save_evidence_to_s3 writes JSON, CSV, and text report files."""
        import attack_evidence_collector as aec
        importlib.reload(aec)

        # Create the S3 bucket
        s3 = boto3.client("s3", region_name=TEST_REGION)
        s3.create_bucket(Bucket="test-evidence-bucket")

        records = self._make_records(20)
        groups = aec.group_by_asn(records)
        templates = aec.generate_reports(
            groups,
            "s3-test-attack",
            "2026-03-31T14:00:00Z",
            "2026-03-31T15:00:00Z",
        )

        bundle = aec.EvidenceBundle(
            attack_id="s3-test-attack",
            analysis_start="2026-03-31T14:00:00Z",
            analysis_end="2026-03-31T15:00:00Z",
            total_blocked_ips=20,
            total_blocked_requests=sum(r.block_count for r in records),
        )

        keys = aec.save_evidence_to_s3("s3-test-attack", bundle, groups, templates)

        assert any("evidence.json" in k for k in keys)
        assert any("summary.csv" in k for k in keys)
        # One .txt per ASN
        txt_keys = [k for k in keys if k.endswith(".txt")]
        assert len(txt_keys) == len(groups)

        # Verify content of evidence.json
        evidence_key = next(k for k in keys if "evidence.json" in k)
        obj = s3.get_object(Bucket="test-evidence-bucket", Key=evidence_key)
        data = json.loads(obj["Body"].read())
        assert data["attack_id"] == "s3-test-attack"
        assert data["total_blocked_ips"] == 20

    def test_parse_athena_array_standard(self) -> None:
        """_parse_athena_array handles standard Athena array output."""
        import attack_evidence_collector as aec
        importlib.reload(aec)

        result = aec._parse_athena_array('[RateLimitRule, IPBlockRule]')
        assert "RateLimitRule" in result
        assert "IPBlockRule" in result

    def test_parse_athena_array_empty(self) -> None:
        """_parse_athena_array handles empty arrays."""
        import attack_evidence_collector as aec
        importlib.reload(aec)

        assert aec._parse_athena_array("[]") == []
        assert aec._parse_athena_array("") == []
        assert aec._parse_athena_array("null") == []

    def test_normalise_cidr(self) -> None:
        """_normalise_cidr adds /32 to bare IPs."""
        import waf_shield_manager as wsm
        importlib.reload(wsm)

        assert wsm._normalise_cidr("1.2.3.4") == "1.2.3.4/32"
        assert wsm._normalise_cidr("1.2.3.0/24") == "1.2.3.0/24"
        assert wsm._normalise_cidr("  5.5.5.5  ") == "5.5.5.5/32"

    def test_enrich_records_with_asn_uses_fallback(self) -> None:
        """enrich_records_with_asn falls back gracefully when MaxMind unavailable."""
        import attack_evidence_collector as aec
        importlib.reload(aec)

        records = self._make_records(5)
        # Reset ASN info to ensure enrichment runs
        for r in records:
            r.asn = 0
            r.asn_org = ""
            r.abuse_contact = ""

        # MaxMind DB not available in test environment — fallback path runs
        enriched = aec.enrich_records_with_asn(records)

        # All records should have been processed (even with fallback returning 0)
        assert len(enriched) == 5


# ===========================================================================
# Handler Integration Tests
# ===========================================================================

class TestHandlers:
    """Basic handler contract tests for all Lambda handlers."""

    @mock_aws
    def test_waf_handler_block_ips(self) -> None:
        """WAF manager handler with action=block_ips returns correct keys."""
        _create_test_tables()
        waf = boto3.client("wafv2", region_name=TEST_REGION)
        resp = waf.create_ip_set(
            Name="handler-test-primary",
            Scope="REGIONAL",
            IPAddressVersion="IPV4",
            Addresses=[],
        )
        ip_set_id = resp["Summary"]["Id"]
        os.environ["WAF_PRIMARY_IP_SET_ID"] = ip_set_id
        os.environ["WAF_PRIMARY_IP_SET_NAME"] = "handler-test-primary"
        os.environ["WAF_SCOPE"] = "REGIONAL"

        import waf_shield_manager as wsm
        importlib.reload(wsm)

        result = wsm.handler(
            {
                "action": "block_ips",
                "attacking_ips": ["10.0.0.1", "10.0.0.2"],
                "attack_id": "handler-test-001",
            },
            None,
        )

        assert "ips_blocked" in result
        assert "ips_submitted" in result
        assert result["ips_blocked"] == 2

    @mock_aws
    def test_waf_handler_unknown_action(self) -> None:
        """WAF manager handler returns error for unknown action."""
        import waf_shield_manager as wsm
        importlib.reload(wsm)

        result = wsm.handler({"action": "fly_to_moon"}, None)
        assert "error" in result

    @mock_aws
    def test_evidence_handler_missing_params(self) -> None:
        """Evidence collector handler returns error when timestamps are missing."""
        import attack_evidence_collector as aec
        importlib.reload(aec)

        result = aec.handler({"attack_id": "test-001"}, None)
        assert "error" in result

    @mock_aws
    def test_campaign_handler_unknown_action(self) -> None:
        """Campaign autoscaler handler returns error for unknown action."""
        _create_test_tables()
        import campaign_autoscaler as ca
        importlib.reload(ca)

        result = ca.handler({"action": "teleport"}, None)
        assert "error" in result
