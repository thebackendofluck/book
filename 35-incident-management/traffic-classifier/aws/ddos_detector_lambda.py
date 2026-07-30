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
AWS DDoS Detection Lambda — Traffic Classifier for iGaming Platforms

Triggered by CloudWatch alarms (high request rate, connection spike, 5xx surge).
Queries ALB, WAF, CloudFront, Shield Advanced, and a marketing-calendar DynamoDB
table to classify incoming traffic as ATTACK, CAMPAIGN, or ORGANIC.

Classification returns a confidence score (0.0–1.0) and a recommended action.

AWS services used:
  - CloudWatch Metrics (ALB, WAF counters)
  - AWS Athena (CloudFront access logs in S3 — geo distribution)
  - AWS Shield Advanced (attack indicators, if enabled)
  - DynamoDB (marketing campaign calendar)
  - SNS (NOC alert publishing)
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
# Configuration (resolved from environment at cold-start)
# ---------------------------------------------------------------------------

AWS_REGION: str = os.environ.get("AWS_REGION", "us-east-1")
ALB_NAME: str = os.environ.get("ALB_NAME", "casino-prod-alb")
ALB_FULL_NAME: str = os.environ.get("ALB_FULL_NAME", "app/casino-prod-alb/0123456789abcdef")
CLOUDFRONT_DIST_ID: str = os.environ.get("CLOUDFRONT_DIST_ID", "E1ABCDEF123456")
WAF_WEB_ACL_ARN: str = os.environ.get("WAF_WEB_ACL_ARN", "")
SHIELD_PROTECTION_ID: str = os.environ.get("SHIELD_PROTECTION_ID", "")
DYNAMO_CALENDAR_TABLE: str = os.environ.get("DYNAMO_CALENDAR_TABLE", "marketing-campaign-calendar")
ATHENA_DATABASE: str = os.environ.get("ATHENA_DATABASE", "cloudfront_logs")
ATHENA_TABLE: str = os.environ.get("ATHENA_TABLE", "cf_access_logs")
ATHENA_RESULTS_BUCKET: str = os.environ.get("ATHENA_RESULTS_BUCKET", "")
SNS_NOC_TOPIC_ARN: str = os.environ.get("SNS_NOC_TOPIC_ARN", "")

# How many minutes of metrics to look back
LOOKBACK_MINUTES: int = int(os.environ.get("LOOKBACK_MINUTES", "5"))

# Thresholds
WAF_BLOCK_RATE_ATTACK_THRESHOLD: float = float(os.environ.get("WAF_BLOCK_RATE_ATTACK_THRESHOLD", "0.20"))
ERROR_RATE_OVERLOAD_THRESHOLD: float = float(os.environ.get("ERROR_RATE_OVERLOAD_THRESHOLD", "0.05"))
GEO_CONCENTRATION_THRESHOLD: float = float(os.environ.get("GEO_CONCENTRATION_THRESHOLD", "0.80"))
REQUEST_MULTIPLIER_CAMPAIGN: float = float(os.environ.get("REQUEST_MULTIPLIER_CAMPAIGN", "3.0"))
ATHENA_POLL_INTERVAL_SECONDS: int = int(os.environ.get("ATHENA_POLL_INTERVAL_SECONDS", "3"))
ATHENA_MAX_POLLS: int = int(os.environ.get("ATHENA_MAX_POLLS", "20"))


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


class TrafficClassification(str, Enum):
    ATTACK = "ATTACK"
    CAMPAIGN = "CAMPAIGN"
    ORGANIC = "ORGANIC"
    UNKNOWN = "UNKNOWN"


class RecommendedAction(str, Enum):
    BLOCK_IPS = "BLOCK_IPS"
    SCALE_OUT = "SCALE_OUT"
    MONITOR = "MONITOR"
    NO_ACTION = "NO_ACTION"


@dataclass
class MetricSnapshot:
    """Point-in-time aggregated metric values."""

    request_count: float = 0.0
    error_5xx_count: float = 0.0
    error_5xx_rate: float = 0.0
    waf_allowed_count: float = 0.0
    waf_blocked_count: float = 0.0
    waf_block_rate: float = 0.0
    connection_count: float = 0.0
    target_response_time_p99: float = 0.0
    shield_attack_detected: bool = False
    shield_attack_vectors: list[str] = field(default_factory=list)


@dataclass
class GeoDistribution:
    """Top-N countries and their request share."""

    top_country: str = "UNKNOWN"
    top_country_share: float = 0.0
    top_5_countries: list[dict[str, Any]] = field(default_factory=list)
    total_unique_countries: int = 0
    data_available: bool = False


@dataclass
class CampaignContext:
    """Active marketing campaign if one exists."""

    active: bool = False
    campaign_id: str = ""
    campaign_name: str = ""
    target_geo: str = ""
    expected_multiplier: float = 1.0
    start_time: str = ""
    end_time: str = ""


@dataclass
class DetectionResult:
    """Final classification output."""

    classification: TrafficClassification
    confidence: float  # 0.0–1.0
    recommended_action: RecommendedAction
    metrics: MetricSnapshot
    geo: GeoDistribution
    campaign: CampaignContext
    evidence: dict[str, Any]
    timestamp: str


# ---------------------------------------------------------------------------
# AWS client helpers (module-level singletons — reused across warm invocations)
# ---------------------------------------------------------------------------

_cloudwatch = None
_waf = None
_shield = None
_dynamodb = None
_athena = None
_sns = None


def _cw() -> Any:
    global _cloudwatch
    if _cloudwatch is None:
        _cloudwatch = boto3.client("cloudwatch", region_name=AWS_REGION)
    return _cloudwatch


def _waf_client() -> Any:
    global _waf
    if _waf is None:
        _waf = boto3.client("wafv2", region_name=AWS_REGION)
    return _waf


def _shield_client() -> Any:
    global _shield
    if _shield is None:
        _shield = boto3.client("shield", region_name="us-east-1")  # Shield is global
    return _shield


def _dynamo() -> Any:
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
    return _dynamodb


def _athena_client() -> Any:
    global _athena
    if _athena is None:
        _athena = boto3.client("athena", region_name=AWS_REGION)
    return _athena


def _sns_client() -> Any:
    global _sns
    if _sns is None:
        _sns = boto3.client("sns", region_name=AWS_REGION)
    return _sns


# ---------------------------------------------------------------------------
# Metric collection
# ---------------------------------------------------------------------------


def _get_cloudwatch_sum(
    namespace: str,
    metric_name: str,
    dimensions: list[dict[str, str]],
    period_seconds: int,
    stat: str = "Sum",
) -> float:
    """Return the aggregated CloudWatch metric value over the lookback window."""
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(minutes=LOOKBACK_MINUTES)

    try:
        resp = _cw().get_metric_statistics(
            Namespace=namespace,
            MetricName=metric_name,
            Dimensions=dimensions,
            StartTime=start_time,
            EndTime=end_time,
            Period=period_seconds,
            Statistics=[stat],
        )
        datapoints = resp.get("Datapoints", [])
        if not datapoints:
            return 0.0
        return sum(dp[stat] for dp in datapoints)
    except ClientError as exc:
        logger.warning("CloudWatch metric %s/%s failed: %s", namespace, metric_name, exc)
        return 0.0


def _get_cloudwatch_average(
    namespace: str,
    metric_name: str,
    dimensions: list[dict[str, str]],
    period_seconds: int,
) -> float:
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(minutes=LOOKBACK_MINUTES)

    try:
        resp = _cw().get_metric_statistics(
            Namespace=namespace,
            MetricName=metric_name,
            Dimensions=dimensions,
            StartTime=start_time,
            EndTime=end_time,
            Period=period_seconds,
            Statistics=["Average"],
        )
        datapoints = resp.get("Datapoints", [])
        if not datapoints:
            return 0.0
        return sum(dp["Average"] for dp in datapoints) / len(datapoints)
    except ClientError as exc:
        logger.warning("CloudWatch average %s/%s failed: %s", namespace, metric_name, exc)
        return 0.0


def collect_alb_metrics() -> MetricSnapshot:
    """Pull ALB and WAF metrics from CloudWatch."""
    period = LOOKBACK_MINUTES * 60
    alb_dims = [{"Name": "LoadBalancer", "Value": ALB_FULL_NAME}]

    request_count = _get_cloudwatch_sum("AWS/ApplicationELB", "RequestCount", alb_dims, period)
    error_5xx = _get_cloudwatch_sum("AWS/ApplicationELB", "HTTPCode_Target_5XX_Count", alb_dims, period)
    error_5xx += _get_cloudwatch_sum("AWS/ApplicationELB", "HTTPCode_ELB_5XX_Count", alb_dims, period)
    connection_count = _get_cloudwatch_sum("AWS/ApplicationELB", "ActiveConnectionCount", alb_dims, period)
    response_time = _get_cloudwatch_average(
        "AWS/ApplicationELB", "TargetResponseTime", alb_dims, period
    )

    # WAF counters — keyed by WebACL name extracted from ARN
    waf_acl_name = WAF_WEB_ACL_ARN.split("/")[-1] if WAF_WEB_ACL_ARN else ALB_NAME
    waf_dims = [
        {"Name": "WebACL", "Value": waf_acl_name},
        {"Name": "Region", "Value": AWS_REGION},
        {"Name": "Rule", "Value": "ALL"},
    ]
    waf_allowed = _get_cloudwatch_sum("AWS/WAFV2", "AllowedRequests", waf_dims, period)
    waf_blocked = _get_cloudwatch_sum("AWS/WAFV2", "BlockedRequests", waf_dims, period)

    total = (waf_allowed + waf_blocked) or 1.0
    block_rate = waf_blocked / total

    error_rate = error_5xx / request_count if request_count > 0 else 0.0

    snapshot = MetricSnapshot(
        request_count=request_count,
        error_5xx_count=error_5xx,
        error_5xx_rate=error_rate,
        waf_allowed_count=waf_allowed,
        waf_blocked_count=waf_blocked,
        waf_block_rate=block_rate,
        connection_count=connection_count,
        target_response_time_p99=response_time,
    )

    logger.info(
        "ALB metrics: requests=%.0f 5xx_rate=%.3f waf_block_rate=%.3f",
        request_count,
        error_rate,
        block_rate,
    )
    return snapshot


def collect_shield_indicators() -> tuple[bool, list[str]]:
    """Query Shield Advanced for active attack indicators."""
    if not SHIELD_PROTECTION_ID:
        logger.info("Shield Advanced not configured — skipping")
        return False, []

    try:
        resp = _shield_client().describe_attack_statistics()
        # Shield Advanced: check for attacks in the last hour
        attacks = resp.get("DataItems", [])
        if attacks:
            vectors = []
            for item in attacks:
                for att_type in item.get("AttackVolume", {}).keys():
                    vectors.append(att_type)
            return True, vectors
        return False, []
    except ClientError as exc:
        # Shield Advanced may not be enabled in this account
        logger.warning("Shield Advanced query failed (may not be subscribed): %s", exc)
        return False, []


# ---------------------------------------------------------------------------
# Geo distribution via Athena
# ---------------------------------------------------------------------------


def _run_athena_query(sql: str) -> str | None:
    """Submit an Athena query and return the execution ID, or None on error."""
    if not ATHENA_RESULTS_BUCKET:
        return None
    try:
        resp = _athena_client().start_query_execution(
            QueryString=sql,
            QueryExecutionContext={"Database": ATHENA_DATABASE},
            ResultConfiguration={
                "OutputLocation": f"s3://{ATHENA_RESULTS_BUCKET}/athena-results/ddos-detector/"
            },
        )
        return resp["QueryExecutionId"]
    except ClientError as exc:
        logger.warning("Athena query start failed: %s", exc)
        return None


def _wait_athena_query(execution_id: str) -> bool:
    """Poll until the Athena query succeeds or fails. Returns True on success."""
    for _ in range(ATHENA_MAX_POLLS):
        try:
            resp = _athena_client().get_query_execution(QueryExecutionId=execution_id)
            state = resp["QueryExecution"]["Status"]["State"]
            if state == "SUCCEEDED":
                return True
            if state in ("FAILED", "CANCELLED"):
                reason = resp["QueryExecution"]["Status"].get("StateChangeReason", "")
                logger.warning("Athena query %s ended with %s: %s", execution_id, state, reason)
                return False
        except ClientError as exc:
            logger.warning("Athena poll error: %s", exc)
            return False
        time.sleep(ATHENA_POLL_INTERVAL_SECONDS)

    logger.warning("Athena query %s timed out after %d polls", execution_id, ATHENA_MAX_POLLS)
    return False


def _fetch_athena_results(execution_id: str) -> list[dict[str, str]]:
    """Retrieve rows from a completed Athena query."""
    try:
        resp = _athena_client().get_query_results(QueryExecutionId=execution_id, MaxResults=20)
        rows = resp.get("ResultSet", {}).get("Rows", [])
        if len(rows) < 2:
            return []

        headers = [col["VarCharValue"] for col in rows[0]["Data"]]
        result = []
        for row in rows[1:]:
            values = [cell.get("VarCharValue", "") for cell in row["Data"]]
            result.append(dict(zip(headers, values)))
        return result
    except ClientError as exc:
        logger.warning("Athena results fetch failed: %s", exc)
        return []


def collect_geo_distribution() -> GeoDistribution:
    """
    Query CloudFront access logs in S3 via Athena for geographic distribution
    of requests in the last LOOKBACK_MINUTES minutes.
    """
    if not ATHENA_RESULTS_BUCKET:
        logger.info("Athena not configured — skipping geo distribution")
        return GeoDistribution(data_available=False)

    # CloudFront logs include the x-edge-location field; we approximate
    # country from the edge location prefix (first 3 chars = IATA code).
    # If you have a custom geo field in your logs, substitute it here.
    sql = f"""
        SELECT
            SUBSTRING(x_edge_location, 1, 3)  AS edge_prefix,
            COUNT(*)                           AS req_count
        FROM {ATHENA_DATABASE}.{ATHENA_TABLE}
        WHERE date >= DATE_FORMAT(NOW() - INTERVAL '{LOOKBACK_MINUTES}' MINUTE, '%Y-%m-%d')
          AND time >= DATE_FORMAT(NOW() - INTERVAL '{LOOKBACK_MINUTES}' MINUTE, '%H:%i')
        GROUP BY 1
        ORDER BY req_count DESC
        LIMIT 20
    """

    exec_id = _run_athena_query(sql)
    if not exec_id:
        return GeoDistribution(data_available=False)

    if not _wait_athena_query(exec_id):
        return GeoDistribution(data_available=False)

    rows = _fetch_athena_results(exec_id)
    if not rows:
        return GeoDistribution(data_available=False)

    total_requests = sum(int(r.get("req_count", 0)) for r in rows)
    if total_requests == 0:
        return GeoDistribution(data_available=True)

    top_5 = []
    for row in rows[:5]:
        count = int(row.get("req_count", 0))
        top_5.append(
            {
                "edge_prefix": row.get("edge_prefix", "UNK"),
                "count": count,
                "share": count / total_requests,
            }
        )

    top = top_5[0] if top_5 else {}
    return GeoDistribution(
        top_country=top.get("edge_prefix", "UNKNOWN"),
        top_country_share=top.get("share", 0.0),
        top_5_countries=top_5,
        total_unique_countries=len(rows),
        data_available=True,
    )


# ---------------------------------------------------------------------------
# Marketing calendar lookup
# ---------------------------------------------------------------------------


def get_active_campaign() -> CampaignContext:
    """
    Check DynamoDB marketing-campaign-calendar for any campaign currently running.

    Table schema:
        PK: campaign_id (String)
        SK: start_time (ISO-8601 String)
        Attributes: campaign_name, target_geo, expected_multiplier, end_time, status
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        table = _dynamo().Table(DYNAMO_CALENDAR_TABLE)
        resp = table.scan(
            FilterExpression="start_time <= :now AND end_time >= :now AND #s = :active",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":now": now_iso,
                ":active": "ACTIVE",
            },
            Limit=5,
        )
        items = resp.get("Items", [])
        if not items:
            return CampaignContext(active=False)

        # Take the highest-multiplier active campaign
        items.sort(key=lambda x: float(x.get("expected_multiplier", 1.0)), reverse=True)
        item = items[0]

        ctx = CampaignContext(
            active=True,
            campaign_id=item.get("campaign_id", ""),
            campaign_name=item.get("campaign_name", ""),
            target_geo=item.get("target_geo", "GLOBAL"),
            expected_multiplier=float(item.get("expected_multiplier", 1.0)),
            start_time=item.get("start_time", ""),
            end_time=item.get("end_time", ""),
        )
        logger.info(
            "Active campaign found: %s (%.1fx, geo=%s)",
            ctx.campaign_name,
            ctx.expected_multiplier,
            ctx.target_geo,
        )
        return ctx

    except ClientError as exc:
        logger.warning("DynamoDB campaign lookup failed: %s", exc)
        return CampaignContext(active=False)


# ---------------------------------------------------------------------------
# Classification logic
# ---------------------------------------------------------------------------


def classify_traffic(
    metrics: MetricSnapshot,
    geo: GeoDistribution,
    campaign: CampaignContext,
) -> tuple[TrafficClassification, float, RecommendedAction, dict[str, Any]]:
    """
    Multi-signal classifier:

    Evidence weights:
      - WAF block rate:          high block rate indicates volumetric attack
      - 5xx error rate:          high error rate indicates origin overload
      - Geo concentration:       spike from a single edge prefix suggests botnet
      - Shield Advanced:         definitive attack signal when triggered
      - Campaign calendar:       known multiplier means the spike is expected
      - Request magnitude:       small spikes are noise, large spikes are signals

    Returns: (classification, confidence, action, evidence_dict)
    """
    evidence: dict[str, Any] = {
        "waf_block_rate": metrics.waf_block_rate,
        "error_5xx_rate": metrics.error_5xx_rate,
        "request_count": metrics.request_count,
        "connection_count": metrics.connection_count,
        "shield_attack_detected": metrics.shield_attack_detected,
        "geo_top_share": geo.top_country_share if geo.data_available else None,
        "campaign_active": campaign.active,
        "campaign_multiplier": campaign.expected_multiplier if campaign.active else None,
    }

    attack_score: float = 0.0
    campaign_score: float = 0.0
    organic_score: float = 0.0

    # --- Signal 1: Shield Advanced (hard signal) ---
    if metrics.shield_attack_detected:
        attack_score += 0.45
        evidence["shield_vectors"] = metrics.shield_attack_vectors

    # --- Signal 2: WAF block rate ---
    if metrics.waf_block_rate >= WAF_BLOCK_RATE_ATTACK_THRESHOLD:
        # High block rate: bots/attack traffic hitting WAF rules
        attack_score += min(0.30, metrics.waf_block_rate * 1.2)
    elif metrics.waf_block_rate < 0.02:
        # Very low block rate: clean-looking traffic
        organic_score += 0.15
        campaign_score += 0.10

    # --- Signal 3: 5xx error rate (origin overload) ---
    if metrics.error_5xx_rate >= ERROR_RATE_OVERLOAD_THRESHOLD:
        # Overloaded origin could be attack OR campaign without pre-scaling
        attack_score += 0.10
        campaign_score += 0.10
    else:
        organic_score += 0.10

    # --- Signal 4: Geographic concentration ---
    if geo.data_available and geo.top_country_share >= GEO_CONCENTRATION_THRESHOLD:
        # 80%+ from a single edge prefix: strongly suggestive of botnet or targeted traffic
        if not campaign.active:
            attack_score += 0.20
        else:
            # Campaign may be geo-targeted — soften the attack signal
            if campaign.target_geo.upper() in geo.top_country.upper():
                campaign_score += 0.20
            else:
                attack_score += 0.15
    elif geo.data_available:
        organic_score += 0.10

    # --- Signal 5: Marketing campaign calendar ---
    if campaign.active:
        # Request count aligns with expected campaign multiplier
        multiplier_expected = campaign.expected_multiplier
        # We can't know the baseline here without historical data;
        # the presence of an active campaign is itself a strong signal.
        campaign_score += 0.35
        if multiplier_expected >= REQUEST_MULTIPLIER_CAMPAIGN:
            campaign_score += 0.10
        evidence["campaign_id"] = campaign.campaign_id
        evidence["campaign_name"] = campaign.campaign_name

    # --- Signal 6: Absolute request magnitude sanity check ---
    # Very small request counts (alarm false-positive) → lean organic
    if metrics.request_count < 100:
        organic_score += 0.20
        attack_score = max(0.0, attack_score - 0.10)

    # --- Normalise & decide ---
    total_score = attack_score + campaign_score + organic_score
    if total_score == 0.0:
        return TrafficClassification.UNKNOWN, 0.0, RecommendedAction.MONITOR, evidence

    attack_p = attack_score / total_score
    campaign_p = campaign_score / total_score
    organic_p = organic_score / total_score

    evidence["scores"] = {
        "attack": round(attack_score, 4),
        "campaign": round(campaign_score, 4),
        "organic": round(organic_score, 4),
        "attack_probability": round(attack_p, 4),
        "campaign_probability": round(campaign_p, 4),
        "organic_probability": round(organic_p, 4),
    }

    if attack_p > campaign_p and attack_p > organic_p:
        confidence = min(attack_p, 1.0)
        if confidence >= 0.60:
            action = RecommendedAction.BLOCK_IPS
        else:
            action = RecommendedAction.MONITOR
        return TrafficClassification.ATTACK, round(confidence, 3), action, evidence

    if campaign_p > organic_p:
        confidence = min(campaign_p, 1.0)
        action = RecommendedAction.SCALE_OUT
        return TrafficClassification.CAMPAIGN, round(confidence, 3), action, evidence

    confidence = min(organic_p, 1.0)
    action = RecommendedAction.MONITOR if metrics.error_5xx_rate > 0.02 else RecommendedAction.NO_ACTION
    return TrafficClassification.ORGANIC, round(confidence, 3), action, evidence


# ---------------------------------------------------------------------------
# NOC notification
# ---------------------------------------------------------------------------


def publish_noc_alert(result: DetectionResult) -> None:
    """Publish classification result to the NOC SNS topic."""
    if not SNS_NOC_TOPIC_ARN:
        return

    subject_map = {
        TrafficClassification.ATTACK: "[CRITICAL] DDoS ATTACK DETECTED",
        TrafficClassification.CAMPAIGN: "[INFO] Campaign Traffic Spike",
        TrafficClassification.ORGANIC: "[INFO] Organic Traffic Spike",
        TrafficClassification.UNKNOWN: "[WARN] Unclassified Traffic Spike",
    }

    subject = subject_map.get(result.classification, "[WARN] Traffic Event")
    message = json.dumps(
        {
            "classification": result.classification.value,
            "confidence": result.confidence,
            "recommended_action": result.recommended_action.value,
            "request_count": result.metrics.request_count,
            "waf_block_rate": round(result.metrics.waf_block_rate, 4),
            "error_5xx_rate": round(result.metrics.error_5xx_rate, 4),
            "shield_attack": result.metrics.shield_attack_detected,
            "campaign_active": result.campaign.active,
            "evidence": result.evidence,
            "timestamp": result.timestamp,
        },
        indent=2,
    )

    try:
        _sns_client().publish(
            TopicArn=SNS_NOC_TOPIC_ARN,
            Subject=subject,
            Message=message,
        )
        logger.info("NOC alert published: %s", subject)
    except ClientError as exc:
        logger.error("Failed to publish NOC alert: %s", exc)


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Lambda entry point.

    The function can be invoked by:
      - CloudWatch Alarm → EventBridge → Lambda (alarm state change event)
      - Direct invocation for testing

    Returns a DetectionResult serialised as a dict.
    """
    logger.info("DDoS detector invoked. Event: %s", json.dumps(event, default=str))

    # 1. Collect metrics
    metrics = collect_alb_metrics()
    shield_attack, shield_vectors = collect_shield_indicators()
    metrics.shield_attack_detected = shield_attack
    metrics.shield_attack_vectors = shield_vectors

    # 2. Geo distribution (async-ish — Athena has latency; failures are tolerated)
    geo = collect_geo_distribution()

    # 3. Marketing calendar
    campaign = get_active_campaign()

    # 4. Classify
    classification, confidence, action, evidence = classify_traffic(metrics, geo, campaign)

    result = DetectionResult(
        classification=classification,
        confidence=confidence,
        recommended_action=action,
        metrics=metrics,
        geo=geo,
        campaign=campaign,
        evidence=evidence,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    logger.info(
        "Classification: %s (confidence=%.3f, action=%s)",
        result.classification.value,
        result.confidence,
        result.recommended_action.value,
    )

    # 5. Notify NOC
    publish_noc_alert(result)

    # Serialise dataclasses for API Gateway / EventBridge response
    return {
        "classification": result.classification.value,
        "confidence": result.confidence,
        "recommended_action": result.recommended_action.value,
        "metrics": {
            "request_count": result.metrics.request_count,
            "error_5xx_rate": round(result.metrics.error_5xx_rate, 5),
            "waf_block_rate": round(result.metrics.waf_block_rate, 5),
            "connection_count": result.metrics.connection_count,
            "shield_attack_detected": result.metrics.shield_attack_detected,
            "shield_attack_vectors": result.metrics.shield_attack_vectors,
        },
        "geo": {
            "top_country": result.geo.top_country,
            "top_country_share": round(result.geo.top_country_share, 4),
            "top_5_countries": result.geo.top_5_countries,
            "data_available": result.geo.data_available,
        },
        "campaign": {
            "active": result.campaign.active,
            "campaign_id": result.campaign.campaign_id,
            "campaign_name": result.campaign.campaign_name,
            "target_geo": result.campaign.target_geo,
            "expected_multiplier": result.campaign.expected_multiplier,
        },
        "evidence": result.evidence,
        "timestamp": result.timestamp,
    }
