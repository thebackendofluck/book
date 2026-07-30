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
Campaign-Aware Autoscaler for iGaming Platforms

Pre-scales AWS Auto Scaling Groups, ECS services, and ElastiCache clusters
BEFORE a marketing campaign begins, preventing cold-start latency spikes.
Manages warm-up, grace periods, and staged scale-down.

Scale profiles:
  small  — 2x  (email newsletter, small influencer)
  medium — 5x  (TV ad, mid-tier affiliate burst)
  large  — 10x (national TV, major event)
  mega   — 20x (World Cup, Super Bowl, IPL Final)

AWS services used:
  - EC2 Auto Scaling
  - ECS (Fargate clusters)
  - ElastiCache (Redis)
  - CloudFront (cache invalidation to seed edge caches before campaign)
  - DynamoDB (persisting campaign state for stop_campaign correlation)
  - CloudWatch (publishing custom scaling metrics)
  - SNS (NOC notifications)
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AWS_REGION: str = os.environ.get("AWS_REGION", "us-east-1")

# ASG names (comma-separated for multiple groups)
ASG_NAMES: list[str] = [
    n.strip()
    for n in os.environ.get("ASG_NAMES", "casino-app-asg,casino-api-asg").split(",")
    if n.strip()
]

# ECS cluster/service pairs — format: "cluster:service,cluster:service"
ECS_SERVICES_RAW: str = os.environ.get(
    "ECS_SERVICES", "casino-prod:casino-app,casino-prod:casino-worker"
)
def _parse_ecs_pair(raw: str) -> tuple[str, str]:
    parts = raw.split(":", 1)
    return (parts[0].strip(), parts[1].strip())


ECS_SERVICES: list[tuple[str, str]] = [
    _parse_ecs_pair(pair)
    for pair in ECS_SERVICES_RAW.split(",")
    if ":" in pair
]

# ElastiCache replication group IDs (comma-separated)
ELASTICACHE_GROUPS: list[str] = [
    n.strip()
    for n in os.environ.get("ELASTICACHE_GROUPS", "casino-session-cache,casino-odds-cache").split(",")
    if n.strip()
]

CLOUDFRONT_DIST_ID: str = os.environ.get("CLOUDFRONT_DIST_ID", "E1ABCDEF123456")
DYNAMO_CAMPAIGN_STATE_TABLE: str = os.environ.get(
    "DYNAMO_CAMPAIGN_STATE_TABLE", "campaign-scaling-state"
)
SNS_NOC_TOPIC_ARN: str = os.environ.get("SNS_NOC_TOPIC_ARN", "")

# Warm-up lead time before campaign start (minutes)
WARMUP_LEAD_MINUTES: int = int(os.environ.get("WARMUP_LEAD_MINUTES", "15"))

# Scale-down grace period after campaign ends (minutes)
SCALEDOWN_GRACE_MINUTES: int = int(os.environ.get("SCALEDOWN_GRACE_MINUTES", "30"))

# ASG baseline capacities (at 1x)
ASG_BASELINE_CAPACITIES: dict[str, int] = {
    name: int(cap)
    for name, cap in (
        pair.split("=", 1)
        for pair in os.environ.get(
            "ASG_BASELINE_CAPACITIES",
            "casino-app-asg=4,casino-api-asg=2",
        ).split(",")
        if "=" in pair
    )
}

# ECS baseline task counts
ECS_BASELINE_COUNTS: dict[str, int] = {
    svc: int(cnt)
    for svc, cnt in (
        pair.split("=", 1)
        for pair in os.environ.get(
            "ECS_BASELINE_COUNTS",
            "casino-app=4,casino-worker=2",
        ).split(",")
        if "=" in pair
    )
}

# ElastiCache baseline node counts
ELASTICACHE_BASELINE_NODES: dict[str, int] = {
    grp: int(cnt)
    for grp, cnt in (
        pair.split("=", 1)
        for pair in os.environ.get(
            "ELASTICACHE_BASELINE_NODES",
            "casino-session-cache=3,casino-odds-cache=2",
        ).split(",")
        if "=" in pair
    )
}

# ASG hard limits per profile to avoid runaway scaling
ASG_MAX_LIMITS: dict[str, int] = {
    name: int(cap)
    for name, cap in (
        pair.split("=", 1)
        for pair in os.environ.get(
            "ASG_MAX_LIMITS",
            "casino-app-asg=80,casino-api-asg=40",
        ).split(",")
        if "=" in pair
    )
}


# ---------------------------------------------------------------------------
# Scale profiles
# ---------------------------------------------------------------------------


class ScaleProfile(str, Enum):
    SMALL = "small"    # 2x
    MEDIUM = "medium"  # 5x
    LARGE = "large"    # 10x
    MEGA = "mega"      # 20x


SCALE_MULTIPLIERS: dict[ScaleProfile, float] = {
    ScaleProfile.SMALL: 2.0,
    ScaleProfile.MEDIUM: 5.0,
    ScaleProfile.LARGE: 10.0,
    ScaleProfile.MEGA: 20.0,
}


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


@dataclass
class CampaignScaleResult:
    campaign_id: str
    profile: ScaleProfile
    multiplier: float
    geo: str
    actions_taken: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    asg_updates: dict[str, int] = field(default_factory=dict)
    ecs_updates: dict[str, int] = field(default_factory=dict)
    elasticache_updates: dict[str, int] = field(default_factory=dict)
    cloudfront_invalidation_id: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class ScaleDownResult:
    campaign_id: str
    grace_period_minutes: int
    actions_taken: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ---------------------------------------------------------------------------
# AWS clients
# ---------------------------------------------------------------------------

_autoscaling = None
_ecs = None
_elasticache = None
_cloudfront = None
_dynamodb = None
_sns = None
_cloudwatch = None


def _asg() -> Any:
    global _autoscaling
    if _autoscaling is None:
        _autoscaling = boto3.client("autoscaling", region_name=AWS_REGION)
    return _autoscaling


def _ecs_client() -> Any:
    global _ecs
    if _ecs is None:
        _ecs = boto3.client("ecs", region_name=AWS_REGION)
    return _ecs


def _ec_client() -> Any:
    global _elasticache
    if _elasticache is None:
        _elasticache = boto3.client("elasticache", region_name=AWS_REGION)
    return _elasticache


def _cf_client() -> Any:
    global _cloudfront
    if _cloudfront is None:
        _cloudfront = boto3.client("cloudfront", region_name="us-east-1")
    return _cloudfront


def _dynamo() -> Any:
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
    return _dynamodb


def _sns_client() -> Any:
    global _sns
    if _sns is None:
        _sns = boto3.client("sns", region_name=AWS_REGION)
    return _sns


def _cw() -> Any:
    global _cloudwatch
    if _cloudwatch is None:
        _cloudwatch = boto3.client("cloudwatch", region_name=AWS_REGION)
    return _cloudwatch


# ---------------------------------------------------------------------------
# Capacity calculation helpers
# ---------------------------------------------------------------------------


def _target_capacity(baseline: int, multiplier: float, hard_limit: int | None = None) -> int:
    """Calculate scaled capacity, respecting hard limits."""
    target = max(baseline, int(baseline * multiplier))
    if hard_limit is not None:
        target = min(target, hard_limit)
    return target


def _asg_current_capacity(asg_name: str) -> int:
    """Return the current desired capacity of an ASG."""
    try:
        resp = _asg().describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
        groups = resp.get("AutoScalingGroups", [])
        if groups:
            return groups[0].get("DesiredCapacity", 0)
        return 0
    except ClientError as exc:
        logger.warning("Could not describe ASG %s: %s", asg_name, exc)
        return 0


def _ecs_current_count(cluster: str, service: str) -> int:
    """Return the current desired count for an ECS service."""
    try:
        resp = _ecs_client().describe_services(cluster=cluster, services=[service])
        services = resp.get("services", [])
        if services:
            return services[0].get("desiredCount", 0)
        return 0
    except ClientError as exc:
        logger.warning("Could not describe ECS %s/%s: %s", cluster, service, exc)
        return 0


# ---------------------------------------------------------------------------
# Pre-scale implementation
# ---------------------------------------------------------------------------


def _scale_asg(asg_name: str, multiplier: float, result: CampaignScaleResult) -> None:
    """Set ASG desired and max capacity to support the given multiplier."""
    baseline = ASG_BASELINE_CAPACITIES.get(asg_name, 4)
    hard_limit = ASG_MAX_LIMITS.get(asg_name)
    target = _target_capacity(baseline, multiplier, hard_limit)
    current = _asg_current_capacity(asg_name)

    if target <= current:
        logger.info("ASG %s already at %d >= target %d — skipping", asg_name, current, target)
        result.actions_taken.append(f"ASG {asg_name}: already sufficient ({current} instances)")
        return

    try:
        _asg().update_auto_scaling_group(
            AutoScalingGroupName=asg_name,
            MinSize=baseline,
            DesiredCapacity=target,
            MaxSize=hard_limit or target * 2,
        )
        result.asg_updates[asg_name] = target
        result.actions_taken.append(
            f"ASG {asg_name}: scaled {current} → {target} instances "
            f"({multiplier:.1f}x baseline={baseline})"
        )
        logger.info("ASG %s: %d → %d (%.1fx)", asg_name, current, target, multiplier)
    except ClientError as exc:
        error_msg = f"ASG {asg_name} scale failed: {exc}"
        logger.error(error_msg)
        result.errors.append(error_msg)


def _scale_ecs_service(cluster: str, service: str, multiplier: float, result: CampaignScaleResult) -> None:
    """Update ECS service desired count."""
    baseline = ECS_BASELINE_COUNTS.get(service, 2)
    target = max(baseline, int(baseline * multiplier))
    current = _ecs_current_count(cluster, service)

    if target <= current:
        result.actions_taken.append(f"ECS {cluster}/{service}: already at {current} tasks")
        return

    try:
        _ecs_client().update_service(
            cluster=cluster,
            service=service,
            desiredCount=target,
        )
        result.ecs_updates[f"{cluster}/{service}"] = target
        result.actions_taken.append(
            f"ECS {cluster}/{service}: {current} → {target} tasks ({multiplier:.1f}x)"
        )
        logger.info("ECS %s/%s: %d → %d", cluster, service, current, target)
    except ClientError as exc:
        error_msg = f"ECS {cluster}/{service} scale failed: {exc}"
        logger.error(error_msg)
        result.errors.append(error_msg)


def _scale_elasticache(group_id: str, multiplier: float, result: CampaignScaleResult) -> None:
    """
    Scale an ElastiCache Redis replication group by adding read replicas.

    ElastiCache does not scale as quickly as compute — we add replicas
    only when multiplier >= 5x to avoid thrashing small campaigns.
    """
    if multiplier < 5.0:
        result.actions_taken.append(
            f"ElastiCache {group_id}: multiplier {multiplier:.1f}x below threshold — no change"
        )
        return

    baseline = ELASTICACHE_BASELINE_NODES.get(group_id, 2)
    # ElastiCache max replicas per shard is 5
    target_replicas = min(5, max(1, int(baseline * (multiplier / 5.0))))

    try:
        resp = _ec_client().describe_replication_groups(ReplicationGroupId=group_id)
        groups = resp.get("ReplicationGroups", [])
        if not groups:
            result.errors.append(f"ElastiCache {group_id}: group not found")
            return

        group = groups[0]
        current_node_groups = group.get("NodeGroups", [])
        current_replicas = 0
        if current_node_groups:
            current_replicas = len(current_node_groups[0].get("NodeGroupMembers", [])) - 1

        if target_replicas <= current_replicas:
            result.actions_taken.append(
                f"ElastiCache {group_id}: already has {current_replicas} replicas"
            )
            return

        _ec_client().increase_replica_count(
            ReplicationGroupId=group_id,
            NewReplicaCount=target_replicas,
            ApplyImmediately=True,
        )
        result.elasticache_updates[group_id] = target_replicas
        result.actions_taken.append(
            f"ElastiCache {group_id}: {current_replicas} → {target_replicas} replicas"
        )
        logger.info("ElastiCache %s: %d → %d replicas", group_id, current_replicas, target_replicas)
    except ClientError as exc:
        # Not all ElastiCache instance types support replica scaling
        error_msg = f"ElastiCache {group_id} scale failed: {exc}"
        logger.warning(error_msg)
        result.errors.append(error_msg)


def _invalidate_cloudfront(result: CampaignScaleResult) -> None:
    """
    Create a CloudFront invalidation to flush and re-warm edge caches
    before the campaign fires.  Seeded caches reduce origin load.
    """
    if not CLOUDFRONT_DIST_ID:
        return

    paths = [
        "/",
        "/lobby*",
        "/games*",
        "/promotions*",
        "/api/v1/games/featured*",
        "/api/v1/promotions*",
        "/static/js/*",
        "/static/css/*",
    ]
    try:
        resp = _cf_client().create_invalidation(
            DistributionId=CLOUDFRONT_DIST_ID,
            InvalidationBatch={
                "Paths": {"Quantity": len(paths), "Items": paths},
                "CallerReference": f"campaign-warmup-{int(time.time())}",
            },
        )
        invalidation_id = resp["Invalidation"]["Id"]
        result.cloudfront_invalidation_id = invalidation_id
        result.actions_taken.append(
            f"CloudFront invalidation created: {invalidation_id} ({len(paths)} paths)"
        )
        logger.info("CloudFront invalidation %s created", invalidation_id)
    except ClientError as exc:
        error_msg = f"CloudFront invalidation failed: {exc}"
        logger.warning(error_msg)
        result.errors.append(error_msg)


def _persist_campaign_state(campaign_id: str, state: dict[str, Any]) -> None:
    """Write campaign scaling state to DynamoDB for stop_campaign correlation."""
    try:
        table = _dynamo().Table(DYNAMO_CAMPAIGN_STATE_TABLE)
        table.put_item(
            Item={
                "campaign_id": campaign_id,
                "state": json.dumps(state, default=str),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "ttl": int(time.time()) + 86400 * 7,  # 7-day TTL
            }
        )
    except ClientError as exc:
        logger.warning("Failed to persist campaign state for %s: %s", campaign_id, exc)


def _load_campaign_state(campaign_id: str) -> dict[str, Any] | None:
    """Load campaign scaling state from DynamoDB."""
    try:
        table = _dynamo().Table(DYNAMO_CAMPAIGN_STATE_TABLE)
        resp = table.get_item(Key={"campaign_id": campaign_id})
        item = resp.get("Item")
        if item:
            return json.loads(item["state"])
        return None
    except ClientError as exc:
        logger.warning("Failed to load campaign state for %s: %s", campaign_id, exc)
        return None


def _publish_scaling_metric(campaign_id: str, multiplier: float, phase: str) -> None:
    """Publish a custom CloudWatch metric for scaling events."""
    try:
        _cw().put_metric_data(
            Namespace="iGaming/CampaignScaling",
            MetricData=[
                {
                    "MetricName": "ScaleMultiplier",
                    "Dimensions": [
                        {"Name": "CampaignId", "Value": campaign_id},
                        {"Name": "Phase", "Value": phase},
                    ],
                    "Value": multiplier,
                    "Unit": "Count",
                    "Timestamp": datetime.now(timezone.utc),
                }
            ],
        )
    except ClientError as exc:
        logger.debug("Failed to publish scaling metric: %s", exc)


def _notify_noc(subject: str, message: dict[str, Any]) -> None:
    """Send scaling event notification to NOC SNS topic."""
    if not SNS_NOC_TOPIC_ARN:
        return
    try:
        _sns_client().publish(
            TopicArn=SNS_NOC_TOPIC_ARN,
            Subject=subject,
            Message=json.dumps(message, indent=2, default=str),
        )
    except ClientError as exc:
        logger.warning("NOC notification failed: %s", exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def start_campaign(
    campaign_id: str,
    geo: str,
    profile: str | ScaleProfile,
    duration_minutes: int,
    multiplier: float | None = None,
) -> CampaignScaleResult:
    """
    Pre-scale all resources for a marketing campaign.

    Args:
        campaign_id:       Unique identifier (used for state tracking).
        geo:               Target geography (informational; used in NOC alerts).
        profile:           One of small / medium / large / mega.
        duration_minutes:  Expected campaign duration (stored in state).
        multiplier:        Override the profile's default multiplier if provided.

    Returns:
        CampaignScaleResult with full audit trail of actions taken.
    """
    if isinstance(profile, str):
        profile = ScaleProfile(profile.lower())

    effective_multiplier = multiplier if multiplier is not None else SCALE_MULTIPLIERS[profile]

    result = CampaignScaleResult(
        campaign_id=campaign_id,
        profile=profile,
        multiplier=effective_multiplier,
        geo=geo,
    )

    logger.info(
        "Starting campaign pre-scale: id=%s geo=%s profile=%s multiplier=%.1fx",
        campaign_id,
        geo,
        profile.value,
        effective_multiplier,
    )

    # --- Scale ASGs ---
    for asg_name in ASG_NAMES:
        _scale_asg(asg_name, effective_multiplier, result)

    # --- Scale ECS services ---
    for cluster, service in ECS_SERVICES:
        _scale_ecs_service(cluster, service, effective_multiplier, result)

    # --- Scale ElastiCache ---
    for group_id in ELASTICACHE_GROUPS:
        _scale_elasticache(group_id, effective_multiplier, result)

    # --- Warm CloudFront edge caches ---
    _invalidate_cloudfront(result)

    # --- Persist state for stop_campaign ---
    state = {
        "campaign_id": campaign_id,
        "profile": profile.value,
        "multiplier": effective_multiplier,
        "geo": geo,
        "duration_minutes": duration_minutes,
        "start_time": result.timestamp,
        "scheduled_stop": (
            datetime.now(timezone.utc) + timedelta(minutes=duration_minutes)
        ).isoformat(),
        "asg_updates": result.asg_updates,
        "ecs_updates": result.ecs_updates,
        "elasticache_updates": result.elasticache_updates,
    }
    _persist_campaign_state(campaign_id, state)

    # --- Metrics and alerts ---
    _publish_scaling_metric(campaign_id, effective_multiplier, "start")
    _notify_noc(
        f"[SCALING] Campaign {campaign_id} pre-scale complete ({profile.value}, {effective_multiplier:.1f}x)",
        {
            "campaign_id": campaign_id,
            "profile": profile.value,
            "multiplier": effective_multiplier,
            "geo": geo,
            "asg_updates": result.asg_updates,
            "ecs_updates": result.ecs_updates,
            "elasticache_updates": result.elasticache_updates,
            "errors": result.errors,
            "timestamp": result.timestamp,
        },
    )

    if result.errors:
        logger.warning(
            "Campaign %s pre-scale completed with %d errors: %s",
            campaign_id,
            len(result.errors),
            result.errors,
        )
    else:
        logger.info(
            "Campaign %s pre-scale complete. Actions: %d",
            campaign_id,
            len(result.actions_taken),
        )

    return result


def stop_campaign(campaign_id: str, force_immediate: bool = False) -> ScaleDownResult:
    """
    Schedule or execute a scale-down after campaign ends.

    By default, a 30-minute grace period is applied before returning
    resources to baseline.  Pass force_immediate=True to skip the grace
    period (useful for emergency cost control or false-start campaigns).

    The function records the stop intent and applies baseline capacities.
    Actual instance termination is governed by ASG cooldown policies.
    """
    grace = 0 if force_immediate else SCALEDOWN_GRACE_MINUTES
    result = ScaleDownResult(campaign_id=campaign_id, grace_period_minutes=grace)

    logger.info(
        "Stopping campaign %s (grace=%d min, immediate=%s)",
        campaign_id,
        grace,
        force_immediate,
    )

    if grace > 0:
        result.actions_taken.append(
            f"Grace period of {grace} minutes registered — scale-down will be applied "
            f"at {(datetime.now(timezone.utc) + timedelta(minutes=grace)).isoformat()}"
        )
        # In a real deployment this would invoke itself via EventBridge scheduler
        # or Step Functions after the grace period.  Here we record the intent
        # and return; the CloudFormation EventBridge rule handles the delayed call.
        logger.info(
            "Scale-down scheduled for %s + %d minutes",
            campaign_id,
            grace,
        )
    else:
        _apply_scale_down(campaign_id, result)

    state = _load_campaign_state(campaign_id)
    stop_meta: dict[str, Any] = {
        "campaign_id": campaign_id,
        "stop_requested_at": result.timestamp,
        "grace_period_minutes": grace,
        "force_immediate": force_immediate,
    }
    if state:
        stop_meta.update(
            {
                "original_start": state.get("start_time"),
                "original_multiplier": state.get("multiplier"),
                "original_profile": state.get("profile"),
            }
        )

    _publish_scaling_metric(campaign_id, 1.0, "stop")
    _notify_noc(
        f"[SCALING] Campaign {campaign_id} scale-down initiated (grace={grace}min)",
        {**stop_meta, "actions": result.actions_taken, "errors": result.errors},
    )

    return result


def _apply_scale_down(campaign_id: str, result: ScaleDownResult) -> None:
    """Restore all resources to baseline capacity."""
    logger.info("Applying scale-down for campaign %s", campaign_id)

    # Restore ASGs
    for asg_name in ASG_NAMES:
        baseline = ASG_BASELINE_CAPACITIES.get(asg_name, 4)
        current = _asg_current_capacity(asg_name)
        if current <= baseline:
            result.actions_taken.append(f"ASG {asg_name}: already at baseline ({baseline})")
            continue
        try:
            _asg().update_auto_scaling_group(
                AutoScalingGroupName=asg_name,
                MinSize=baseline,
                DesiredCapacity=baseline,
                MaxSize=ASG_MAX_LIMITS.get(asg_name, baseline * 4),
            )
            result.actions_taken.append(
                f"ASG {asg_name}: {current} → {baseline} instances (baseline restore)"
            )
            logger.info("ASG %s: %d → %d (scale-down)", asg_name, current, baseline)
        except ClientError as exc:
            error_msg = f"ASG {asg_name} scale-down failed: {exc}"
            logger.error(error_msg)
            result.errors.append(error_msg)

    # Restore ECS
    for cluster, service in ECS_SERVICES:
        baseline = ECS_BASELINE_COUNTS.get(service, 2)
        current = _ecs_current_count(cluster, service)
        if current <= baseline:
            result.actions_taken.append(f"ECS {cluster}/{service}: already at baseline")
            continue
        try:
            _ecs_client().update_service(
                cluster=cluster, service=service, desiredCount=baseline
            )
            result.actions_taken.append(
                f"ECS {cluster}/{service}: {current} → {baseline} tasks (scale-down)"
            )
            logger.info("ECS %s/%s: %d → %d", cluster, service, current, baseline)
        except ClientError as exc:
            error_msg = f"ECS {cluster}/{service} scale-down failed: {exc}"
            logger.error(error_msg)
            result.errors.append(error_msg)

    # ElastiCache: reduce replicas back to baseline
    for group_id in ELASTICACHE_GROUPS:
        baseline_replicas = max(1, ELASTICACHE_BASELINE_NODES.get(group_id, 2) - 1)
        try:
            resp = _ec_client().describe_replication_groups(ReplicationGroupId=group_id)
            groups = resp.get("ReplicationGroups", [])
            if not groups:
                continue
            node_groups = groups[0].get("NodeGroups", [])
            if not node_groups:
                continue
            current_replicas = len(node_groups[0].get("NodeGroupMembers", [])) - 1

            if current_replicas <= baseline_replicas:
                result.actions_taken.append(
                    f"ElastiCache {group_id}: already at baseline replicas"
                )
                continue

            # Remove excess replicas
            members = node_groups[0].get("NodeGroupMembers", [])
            # Keep primary + baseline_replicas replicas; remove the rest
            nodes_to_remove = []
            replica_count = 0
            for member in members:
                if member.get("CurrentRole") == "replica":
                    if replica_count >= baseline_replicas:
                        nodes_to_remove.append(member["CacheClusterId"])
                    else:
                        replica_count += 1

            if nodes_to_remove:
                _ec_client().decrease_replica_count(
                    ReplicationGroupId=group_id,
                    ReplicasToRemove=nodes_to_remove,
                    ApplyImmediately=True,
                )
                result.actions_taken.append(
                    f"ElastiCache {group_id}: removed {len(nodes_to_remove)} replicas"
                )
                logger.info("ElastiCache %s: removed %d replicas", group_id, len(nodes_to_remove))
        except ClientError as exc:
            error_msg = f"ElastiCache {group_id} scale-down failed: {exc}"
            logger.warning(error_msg)
            result.errors.append(error_msg)

    result.actions_taken.append(f"Scale-down complete for campaign {campaign_id}")


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Lambda entry point.

    Expected event shapes:
      { "action": "start", "campaign_id": "...", "geo": "BR",
        "profile": "large", "duration_minutes": 120 }

      { "action": "stop", "campaign_id": "...", "force_immediate": false }

      { "action": "stop_immediate", "campaign_id": "..." }
    """
    logger.info("Campaign autoscaler invoked: %s", json.dumps(event, default=str))

    action = event.get("action", "").lower()
    campaign_id = event.get("campaign_id", f"campaign-{int(time.time())}")

    if action == "start":
        geo = event.get("geo", "GLOBAL")
        profile_str = event.get("profile", ScaleProfile.MEDIUM.value)
        duration = int(event.get("duration_minutes", 120))
        multiplier_override = event.get("multiplier")
        result = start_campaign(campaign_id, geo, profile_str, duration, multiplier_override)
        return {
            "campaign_id": result.campaign_id,
            "profile": result.profile.value,
            "multiplier": result.multiplier,
            "asg_updates": result.asg_updates,
            "ecs_updates": result.ecs_updates,
            "elasticache_updates": result.elasticache_updates,
            "cloudfront_invalidation_id": result.cloudfront_invalidation_id,
            "actions_taken": result.actions_taken,
            "errors": result.errors,
            "timestamp": result.timestamp,
        }

    if action in ("stop", "stop_immediate"):
        force = action == "stop_immediate" or bool(event.get("force_immediate", False))
        result_stop = stop_campaign(campaign_id, force_immediate=force)
        return {
            "campaign_id": result_stop.campaign_id,
            "grace_period_minutes": result_stop.grace_period_minutes,
            "actions_taken": result_stop.actions_taken,
            "errors": result_stop.errors,
            "timestamp": result_stop.timestamp,
        }

    return {
        "error": f"Unknown action '{action}'. Valid: start, stop, stop_immediate",
        "event": event,
    }
