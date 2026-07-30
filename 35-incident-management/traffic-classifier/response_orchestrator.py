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
Response Orchestrator - Automated incident response for classified traffic events.

Consumes ClassificationResult and executes the appropriate playbook across
Cloudflare, AWS WAF, on-premises Redis, and alerting systems.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import aiohttp
import boto3
import redis.asyncio as aioredis

from traffic_classifier import ClassificationResult, TrafficClass  # ty:ignore[unresolved-import]

logger = logging.getLogger("response_orchestrator")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


# ---------------------------------------------------------------------------
# Configuration (loaded from environment variables)
# ---------------------------------------------------------------------------
@dataclass
class OrchestratorConfig:
    # Cloudflare
    cf_api_token: str = field(default_factory=lambda: os.getenv("CF_API_TOKEN", ""))
    cf_zone_id: str = field(default_factory=lambda: os.getenv("CF_ZONE_ID", ""))
    cf_account_id: str = field(default_factory=lambda: os.getenv("CF_ACCOUNT_ID", ""))

    # AWS
    aws_region: str = field(default_factory=lambda: os.getenv("AWS_REGION", "us-east-1"))
    aws_waf_ipset_id: str = field(default_factory=lambda: os.getenv("AWS_WAF_IPSET_ID", ""))
    aws_waf_ipset_name: str = field(
        default_factory=lambda: os.getenv("AWS_WAF_IPSET_NAME", "ddos-blocklist")
    )
    aws_waf_scope: str = field(
        default_factory=lambda: os.getenv("AWS_WAF_SCOPE", "REGIONAL")
    )
    aws_asg_name: str = field(
        default_factory=lambda: os.getenv("AWS_ASG_NAME", "igaming-asg")
    )
    aws_sns_topic_arn: str = field(
        default_factory=lambda: os.getenv("AWS_SNS_TOPIC_ARN", "")
    )

    # Redis
    redis_url: str = field(
        default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost:6379/0")
    )

    # PagerDuty
    pagerduty_routing_key: str = field(
        default_factory=lambda: os.getenv("PAGERDUTY_ROUTING_KEY", "")
    )

    # Wazuh SIEM
    wazuh_manager_url: str = field(
        default_factory=lambda: os.getenv("WAZUH_MANAGER_URL", "https://wazuh-manager:55000")
    )
    wazuh_api_user: str = field(
        default_factory=lambda: os.getenv("WAZUH_API_USER", "wazuh")
    )
    wazuh_api_password: str = field(
        default_factory=lambda: os.getenv("WAZUH_API_PASSWORD", "")
    )

    # K8s HPA
    k8s_namespace: str = field(
        default_factory=lambda: os.getenv("K8S_NAMESPACE", "igaming")
    )
    k8s_hpa_name: str = field(
        default_factory=lambda: os.getenv("K8S_HPA_NAME", "igaming-frontend-hpa")
    )

    # Evidence storage
    evidence_dir: str = field(
        default_factory=lambda: os.getenv("EVIDENCE_DIR", "/var/log/traffic-classifier/evidence")
    )

    # Thresholds
    attack_confidence_threshold: float = 0.8
    campaign_confidence_threshold: float = 0.7

    # Rate limit for graduated response (requests/minute per IP)
    graduated_rate_limit: int = 120


# ---------------------------------------------------------------------------
# Action result tracking
# ---------------------------------------------------------------------------
class ActionStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    DRY_RUN = "DRY_RUN"


@dataclass
class ActionResult:
    action: str
    status: ActionStatus
    message: str
    duration_ms: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class PlaybookResult:
    classification: str
    confidence: float
    actions: list[ActionResult] = field(default_factory=list)
    total_duration_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def summary(self) -> str:
        successes = sum(1 for a in self.actions if a.status == ActionStatus.SUCCESS)
        failures = sum(1 for a in self.actions if a.status == ActionStatus.FAILED)
        return (
            f"Playbook [{self.classification}] completed in {self.total_duration_ms:.0f}ms: "
            f"{successes} succeeded, {failures} failed."
        )


# ---------------------------------------------------------------------------
# Individual action implementations
# ---------------------------------------------------------------------------
class CloudflareActions:
    BASE = "https://api.cloudflare.com/client/v4"

    def __init__(self, config: OrchestratorConfig) -> None:
        self._cfg = config
        self._headers = {
            "Authorization": f"Bearer {config.cf_api_token}",
            "Content-Type": "application/json",
        }

    async def set_under_attack_mode(self, enable: bool) -> ActionResult:
        """Toggle the Cloudflare 'Under Attack' security level."""
        t0 = time.monotonic()
        if not self._cfg.cf_api_token or not self._cfg.cf_zone_id:
            return ActionResult(
                "cf_under_attack_mode",
                ActionStatus.SKIPPED,
                "CF credentials not configured.",
            )
        level = "under_attack" if enable else "medium"
        url = f"{self.BASE}/zones/{self._cfg.cf_zone_id}/settings/security_level"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.patch(
                    url,
                    headers=self._headers,
                    json={"value": level},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    body = await resp.json()
                    duration = (time.monotonic() - t0) * 1000
                    if resp.status == 200 and body.get("success"):
                        logger.info("Cloudflare security level set to '%s'.", level)
                        return ActionResult(
                            "cf_under_attack_mode",
                            ActionStatus.SUCCESS,
                            f"Security level set to '{level}'.",
                            duration_ms=duration,
                            details={"level": level},
                        )
                    logger.error("Cloudflare API error: %s", body)
                    return ActionResult(
                        "cf_under_attack_mode",
                        ActionStatus.FAILED,
                        f"CF API returned {resp.status}: {body}",
                        duration_ms=duration,
                    )
        except Exception as exc:
            return ActionResult(
                "cf_under_attack_mode",
                ActionStatus.FAILED,
                f"Exception: {exc}",
                duration_ms=(time.monotonic() - t0) * 1000,
            )

    async def add_cache_everything_rule(self, path: str = "/*") -> ActionResult:
        """Create a Cache-Everything Page Rule for campaign traffic."""
        t0 = time.monotonic()
        if not self._cfg.cf_api_token or not self._cfg.cf_zone_id:
            return ActionResult(
                "cf_cache_everything",
                ActionStatus.SKIPPED,
                "CF credentials not configured.",
            )
        url = f"{self.BASE}/zones/{self._cfg.cf_zone_id}/pagerules"
        rule = {
            "targets": [{"target": "url", "constraint": {"operator": "matches", "value": f"*{path}"}}],
            "actions": [{"id": "cache_level", "value": "cache_everything"}],
            "status": "active",
            "priority": 1,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    headers=self._headers,
                    json=rule,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    body = await resp.json()
                    duration = (time.monotonic() - t0) * 1000
                    if resp.status in (200, 201) and body.get("success"):
                        logger.info("CF cache-everything rule created for '%s'.", path)
                        return ActionResult(
                            "cf_cache_everything",
                            ActionStatus.SUCCESS,
                            f"Cache-everything rule created for '{path}'.",
                            duration_ms=duration,
                        )
                    return ActionResult(
                        "cf_cache_everything",
                        ActionStatus.FAILED,
                        f"CF API returned {resp.status}: {body}",
                        duration_ms=duration,
                    )
        except Exception as exc:
            return ActionResult(
                "cf_cache_everything",
                ActionStatus.FAILED,
                f"Exception: {exc}",
                duration_ms=(time.monotonic() - t0) * 1000,
            )


class AWSActions:
    def __init__(self, config: OrchestratorConfig) -> None:
        self._cfg = config

    def _wafv2(self) -> Any:
        return boto3.client("wafv2", region_name=self._cfg.aws_region)

    def _autoscaling(self) -> Any:
        return boto3.client("autoscaling", region_name=self._cfg.aws_region)

    def _sns(self) -> Any:
        return boto3.client("sns", region_name=self._cfg.aws_region)

    async def add_ips_to_waf_ipset(self, ips: list[str]) -> ActionResult:
        """Add CIDR addresses to an AWS WAF IP set (blocking)."""
        t0 = time.monotonic()
        if not self._cfg.aws_waf_ipset_id or not ips:
            return ActionResult(
                "aws_waf_block_ips",
                ActionStatus.SKIPPED,
                "No IPs or WAF IPSet ID not configured.",
            )
        # Ensure CIDR notation
        cidrs = [ip if "/" in ip else f"{ip}/32" for ip in ips[:10_000]]
        try:
            client = self._wafv2()
            # Get current lock token
            response = client.get_ip_set(
                Name=self._cfg.aws_waf_ipset_name,
                Scope=self._cfg.aws_waf_scope,
                Id=self._cfg.aws_waf_ipset_id,
            )
            lock_token = response["LockToken"]
            existing = response["IPSet"]["Addresses"]
            merged = list(set(existing + cidrs))

            client.update_ip_set(
                Name=self._cfg.aws_waf_ipset_name,
                Scope=self._cfg.aws_waf_scope,
                Id=self._cfg.aws_waf_ipset_id,
                Addresses=merged,
                LockToken=lock_token,
            )
            duration = (time.monotonic() - t0) * 1000
            logger.info("Added %d IPs to AWS WAF IPSet.", len(cidrs))
            return ActionResult(
                "aws_waf_block_ips",
                ActionStatus.SUCCESS,
                f"Added {len(cidrs)} IPs to WAF IPSet.",
                duration_ms=duration,
                details={"added_count": len(cidrs)},
            )
        except Exception as exc:
            return ActionResult(
                "aws_waf_block_ips",
                ActionStatus.FAILED,
                f"Exception: {exc}",
                duration_ms=(time.monotonic() - t0) * 1000,
            )

    async def set_asg_desired_capacity(self, desired: int) -> ActionResult:
        """Scale an Auto Scaling Group to the desired capacity."""
        t0 = time.monotonic()
        if not self._cfg.aws_asg_name:
            return ActionResult(
                "aws_asg_scale",
                ActionStatus.SKIPPED,
                "ASG name not configured.",
            )
        try:
            client = self._autoscaling()
            client.set_desired_capacity(
                AutoScalingGroupName=self._cfg.aws_asg_name,
                DesiredCapacity=desired,
                HonorCooldown=False,
            )
            duration = (time.monotonic() - t0) * 1000
            logger.info("ASG '%s' desired capacity set to %d.", self._cfg.aws_asg_name, desired)
            return ActionResult(
                "aws_asg_scale",
                ActionStatus.SUCCESS,
                f"ASG desired capacity set to {desired}.",
                duration_ms=duration,
                details={"desired": desired, "asg": self._cfg.aws_asg_name},
            )
        except Exception as exc:
            return ActionResult(
                "aws_asg_scale",
                ActionStatus.FAILED,
                f"Exception: {exc}",
                duration_ms=(time.monotonic() - t0) * 1000,
            )

    async def send_sns_alert(self, subject: str, message: str) -> ActionResult:
        """Publish an SNS notification to the NOC topic."""
        t0 = time.monotonic()
        if not self._cfg.aws_sns_topic_arn:
            return ActionResult(
                "aws_sns_alert",
                ActionStatus.SKIPPED,
                "SNS topic ARN not configured.",
            )
        try:
            client = self._sns()
            client.publish(
                TopicArn=self._cfg.aws_sns_topic_arn,
                Subject=subject[:100],
                Message=message,
            )
            duration = (time.monotonic() - t0) * 1000
            logger.info("SNS alert published: '%s'.", subject)
            return ActionResult(
                "aws_sns_alert",
                ActionStatus.SUCCESS,
                "SNS alert published.",
                duration_ms=duration,
            )
        except Exception as exc:
            return ActionResult(
                "aws_sns_alert",
                ActionStatus.FAILED,
                f"Exception: {exc}",
                duration_ms=(time.monotonic() - t0) * 1000,
            )


class RedisActions:
    def __init__(self, config: OrchestratorConfig) -> None:
        self._cfg = config
        self._redis: aioredis.Redis | None = None

    async def connect(self) -> None:
        self._redis = aioredis.from_url(
            self._cfg.redis_url, encoding="utf-8", decode_responses=True
        )

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()

    async def blacklist_ips(
        self, ips: list[str], ttl_seconds: int = 86400
    ) -> ActionResult:
        """Add attacking IPs to Redis blacklist with TTL."""
        t0 = time.monotonic()
        if not self._redis or not ips:
            return ActionResult(
                "redis_blacklist",
                ActionStatus.SKIPPED,
                "Redis not connected or no IPs provided.",
            )
        try:
            pipe = self._redis.pipeline()
            ts = int(time.time())
            for ip in ips:
                pipe.setex(f"blacklist:{ip}", ttl_seconds, ts)
            # Also add to a sorted set for bulk export
            for ip in ips:
                pipe.zadd("blacklist:sorted", {ip: ts})
            await pipe.execute()
            duration = (time.monotonic() - t0) * 1000
            logger.info("Blacklisted %d IPs in Redis (TTL=%ds).", len(ips), ttl_seconds)
            return ActionResult(
                "redis_blacklist",
                ActionStatus.SUCCESS,
                f"Blacklisted {len(ips)} IPs (TTL={ttl_seconds}s).",
                duration_ms=duration,
                details={"count": len(ips), "ttl": ttl_seconds},
            )
        except Exception as exc:
            return ActionResult(
                "redis_blacklist",
                ActionStatus.FAILED,
                f"Exception: {exc}",
                duration_ms=(time.monotonic() - t0) * 1000,
            )

    async def set_rate_limit(self, limit_rpm: int) -> ActionResult:
        """Store current rate limit in Redis for nginx/proxy consumption."""
        t0 = time.monotonic()
        if not self._redis:
            return ActionResult(
                "redis_rate_limit",
                ActionStatus.SKIPPED,
                "Redis not connected.",
            )
        try:
            await self._redis.set("config:rate_limit_rpm", limit_rpm)
            await self._redis.set("config:rate_limit_updated_at", int(time.time()))
            duration = (time.monotonic() - t0) * 1000
            return ActionResult(
                "redis_rate_limit",
                ActionStatus.SUCCESS,
                f"Rate limit set to {limit_rpm} rpm.",
                duration_ms=duration,
            )
        except Exception as exc:
            return ActionResult(
                "redis_rate_limit",
                ActionStatus.FAILED,
                f"Exception: {exc}",
                duration_ms=(time.monotonic() - t0) * 1000,
            )

    async def set_campaign_mode_banner(self, active: bool, campaign_name: str | None = None) -> ActionResult:
        """Update dashboard banner flag in Redis."""
        t0 = time.monotonic()
        if not self._redis:
            return ActionResult(
                "redis_campaign_banner",
                ActionStatus.SKIPPED,
                "Redis not connected.",
            )
        try:
            payload = json.dumps({
                "active": active,
                "name": campaign_name or "",
                "updated_at": int(time.time()),
            })
            await self._redis.set("ui:campaign_mode_banner", payload)
            duration = (time.monotonic() - t0) * 1000
            return ActionResult(
                "redis_campaign_banner",
                ActionStatus.SUCCESS,
                f"Campaign banner {'enabled' if active else 'disabled'}.",
                duration_ms=duration,
            )
        except Exception as exc:
            return ActionResult(
                "redis_campaign_banner",
                ActionStatus.FAILED,
                f"Exception: {exc}",
                duration_ms=(time.monotonic() - t0) * 1000,
            )


class AlertActions:
    def __init__(self, config: OrchestratorConfig) -> None:
        self._cfg = config

    async def send_pagerduty(
        self,
        summary: str,
        severity: str = "critical",
        details: dict[str, Any] | None = None,
    ) -> ActionResult:
        """Trigger a PagerDuty incident via Events API v2."""
        t0 = time.monotonic()
        if not self._cfg.pagerduty_routing_key:
            return ActionResult(
                "pagerduty_alert",
                ActionStatus.SKIPPED,
                "PagerDuty routing key not configured.",
            )
        payload = {
            "routing_key": self._cfg.pagerduty_routing_key,
            "event_action": "trigger",
            "payload": {
                "summary": summary,
                "severity": severity,
                "source": "traffic-classifier",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "custom_details": details or {},
            },
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://events.pagerduty.com/v2/enqueue",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    duration = (time.monotonic() - t0) * 1000
                    if resp.status in (200, 202):
                        logger.info("PagerDuty alert triggered: '%s'.", summary)
                        return ActionResult(
                            "pagerduty_alert",
                            ActionStatus.SUCCESS,
                            "PagerDuty incident triggered.",
                            duration_ms=duration,
                        )
                    body = await resp.text()
                    return ActionResult(
                        "pagerduty_alert",
                        ActionStatus.FAILED,
                        f"PagerDuty returned {resp.status}: {body}",
                        duration_ms=duration,
                    )
        except Exception as exc:
            return ActionResult(
                "pagerduty_alert",
                ActionStatus.FAILED,
                f"Exception: {exc}",
                duration_ms=(time.monotonic() - t0) * 1000,
            )

    async def log_to_wazuh_siem(self, event: dict[str, Any]) -> ActionResult:
        """POST a custom alert event to the Wazuh Manager REST API."""
        t0 = time.monotonic()
        if not self._cfg.wazuh_manager_url or not self._cfg.wazuh_api_password:
            return ActionResult(
                "wazuh_siem",
                ActionStatus.SKIPPED,
                "Wazuh credentials not configured.",
            )
        # Wazuh event format
        wazuh_event = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "rule": {
                "level": 12 if event.get("classification") == "ATTACK" else 6,
                "description": "Traffic classifier: " + event.get("classification", "UNKNOWN"),
                "id": "99001",
                "groups": ["igaming", "traffic_classifier", "ddos"],
            },
            "agent": {"id": "001", "name": "traffic-classifier"},
            "data": event,
        }
        try:
            # Authenticate first
            auth_url = f"{self._cfg.wazuh_manager_url}/security/user/authenticate"
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    auth_url,
                    auth=aiohttp.BasicAuth(self._cfg.wazuh_api_user, self._cfg.wazuh_api_password),
                    verify_ssl=False,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as auth_resp:
                    if auth_resp.status != 200:
                        return ActionResult(
                            "wazuh_siem",
                            ActionStatus.FAILED,
                            f"Wazuh auth failed: {auth_resp.status}",
                            duration_ms=(time.monotonic() - t0) * 1000,
                        )
                    token = (await auth_resp.json()).get("data", {}).get("token", "")

                # POST event
                async with session.post(
                    f"{self._cfg.wazuh_manager_url}/events",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"events": [wazuh_event]},
                    verify_ssl=False,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as ev_resp:
                    duration = (time.monotonic() - t0) * 1000
                    if ev_resp.status in (200, 201, 204):
                        logger.info("Event logged to Wazuh SIEM.")
                        return ActionResult(
                            "wazuh_siem",
                            ActionStatus.SUCCESS,
                            "Event logged to Wazuh.",
                            duration_ms=duration,
                        )
                    body = await ev_resp.text()
                    return ActionResult(
                        "wazuh_siem",
                        ActionStatus.FAILED,
                        f"Wazuh events API returned {ev_resp.status}: {body}",
                        duration_ms=duration,
                    )
        except Exception as exc:
            return ActionResult(
                "wazuh_siem",
                ActionStatus.FAILED,
                f"Exception: {exc}",
                duration_ms=(time.monotonic() - t0) * 1000,
            )


class EvidenceCollector:
    def __init__(self, config: OrchestratorConfig) -> None:
        self._cfg = config
        os.makedirs(config.evidence_dir, exist_ok=True)

    async def save_attack_evidence(
        self,
        result: ClassificationResult,
        attacking_ips: list[str],
    ) -> ActionResult:
        """Persist attack fingerprint and IP list to disk for post-incident analysis."""
        t0 = time.monotonic()
        ts_str = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
        filepath = os.path.join(self._cfg.evidence_dir, f"attack_{ts_str}.json")
        try:
            evidence = {
                "timestamp": ts_str,
                "classification": result.traffic_class.value,
                "confidence": result.confidence,
                "fingerprint": result.fingerprint.to_dict(),
                "explanation": result.explanation,
                "attacking_ips": attacking_ips,
                "metrics": result.metrics_snapshot,
            }
            with open(filepath, "w") as fh:
                json.dump(evidence, fh, indent=2)
            duration = (time.monotonic() - t0) * 1000
            logger.info("Attack evidence saved to %s.", filepath)
            return ActionResult(
                "save_evidence",
                ActionStatus.SUCCESS,
                f"Evidence saved to {filepath}.",
                duration_ms=duration,
                details={"filepath": filepath},
            )
        except Exception as exc:
            return ActionResult(
                "save_evidence",
                ActionStatus.FAILED,
                f"Exception: {exc}",
                duration_ms=(time.monotonic() - t0) * 1000,
            )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
class ResponseOrchestrator:
    """
    Executes the correct playbook for each traffic classification.

    All playbook steps are fired concurrently within each phase to minimise
    total response time.  Individual failures are recorded but do not abort
    the playbook.
    """

    def __init__(self, config: OrchestratorConfig | None = None) -> None:
        self._cfg = config or OrchestratorConfig()
        self._cf = CloudflareActions(self._cfg)
        self._aws = AWSActions(self._cfg)
        self._redis = RedisActions(self._cfg)
        self._alert = AlertActions(self._cfg)
        self._evidence = EvidenceCollector(self._cfg)

    async def start(self) -> None:
        await self._redis.connect()

    async def stop(self) -> None:
        await self._redis.close()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    async def handle(
        self,
        result: ClassificationResult,
        attacking_ips: list[str] | None = None,
    ) -> PlaybookResult:
        t0 = time.monotonic()
        attacking_ips = attacking_ips or []

        if result.traffic_class == TrafficClass.ATTACK:
            actions = await self._run_attack_playbook(result, attacking_ips)
        elif result.traffic_class == TrafficClass.MARKETING_CAMPAIGN:
            actions = await self._run_campaign_playbook(result)
        elif result.traffic_class == TrafficClass.ORGANIC_SURGE:
            actions = await self._run_organic_surge_playbook(result)
        else:
            actions = await self._run_unknown_playbook(result)

        pr = PlaybookResult(
            classification=result.traffic_class.value,
            confidence=result.confidence,
            actions=actions,
            total_duration_ms=(time.monotonic() - t0) * 1000,
        )
        logger.info(pr.summary())
        return pr

    # ------------------------------------------------------------------
    # Attack playbook
    # ------------------------------------------------------------------
    async def _run_attack_playbook(
        self, result: ClassificationResult, attacking_ips: list[str]
    ) -> list[ActionResult]:
        """
        ATTACK playbook (confidence > attack_confidence_threshold):
          1. Enable Cloudflare Under Attack mode
          2. Block IPs in AWS WAF
          3. Blacklist IPs in on-premises Redis
          4. Save evidence to disk
          5. Alert NOC via PagerDuty + SNS
          6. Log to Wazuh SIEM
        """
        logger.warning(
            "ATTACK playbook triggered (confidence=%.2f, %d IPs).",
            result.confidence,
            len(attacking_ips),
        )

        if result.confidence < self._cfg.attack_confidence_threshold:
            # Graduated response: only rate-limit, no full block
            logger.info(
                "Attack confidence %.2f < threshold %.2f — applying graduated rate limit.",
                result.confidence,
                self._cfg.attack_confidence_threshold,
            )
            rate_limit_result = await self._redis.set_rate_limit(
                self._cfg.graduated_rate_limit
            )
            return [
                rate_limit_result,
                ActionResult(
                    "full_block_skipped",
                    ActionStatus.SKIPPED,
                    f"Confidence {result.confidence:.2f} below threshold "
                    f"{self._cfg.attack_confidence_threshold} — rate limiting only.",
                ),
            ]

        # High-confidence attack — execute all actions concurrently
        tasks = [
            self._cf.set_under_attack_mode(True),
            self._aws.add_ips_to_waf_ipset(attacking_ips),
            self._redis.blacklist_ips(attacking_ips),
            self._evidence.save_attack_evidence(result, attacking_ips),
            self._alert.send_pagerduty(
                summary=f"DDoS ATTACK detected — confidence {result.confidence:.0%}",
                severity="critical",
                details={
                    "classification": result.traffic_class.value,
                    "confidence": result.confidence,
                    "ip_count": len(attacking_ips),
                    "fingerprint": result.fingerprint.to_dict(),
                    "explanation": result.explanation,
                },
            ),
            self._aws.send_sns_alert(
                subject=f"[CRITICAL] DDoS Attack detected (confidence {result.confidence:.0%})",
                message=json.dumps(result.to_dict(), indent=2),
            ),
            self._alert.log_to_wazuh_siem({
                "classification": result.traffic_class.value,
                "confidence": result.confidence,
                "ip_count": len(attacking_ips),
                "fingerprint": result.fingerprint.to_dict(),
            }),
        ]
        return list(await asyncio.gather(*tasks))

    # ------------------------------------------------------------------
    # Campaign playbook
    # ------------------------------------------------------------------
    async def _run_campaign_playbook(self, result: ClassificationResult) -> list[ActionResult]:
        """
        MARKETING_CAMPAIGN playbook:
          1. Set campaign-mode banner in Redis
          2. Increase rate limits
          3. Enable CF cache-everything for landing pages
          4. Scale up AWS ASG
          5. Log to Wazuh SIEM
        """
        logger.info(
            "CAMPAIGN playbook triggered (confidence=%.2f, campaign=%s).",
            result.confidence,
            result.campaign_name,
        )
        tasks = [
            self._redis.set_campaign_mode_banner(True, result.campaign_name),
            self._redis.set_rate_limit(600),  # 10 req/s per IP for campaign traffic
            self._cf.add_cache_everything_rule("/promo/*"),
            self._aws.set_asg_desired_capacity(10),  # Scale up ASG
            self._alert.log_to_wazuh_siem({
                "classification": result.traffic_class.value,
                "confidence": result.confidence,
                "campaign_name": result.campaign_name,
            }),
        ]
        return list(await asyncio.gather(*tasks))

    # ------------------------------------------------------------------
    # Organic surge playbook
    # ------------------------------------------------------------------
    async def _run_organic_surge_playbook(
        self, result: ClassificationResult
    ) -> list[ActionResult]:
        """
        ORGANIC_SURGE: gradual scale-up, no defensive actions.
        """
        logger.info("ORGANIC_SURGE playbook triggered.")
        tasks = [
            self._redis.set_rate_limit(300),
            self._aws.set_asg_desired_capacity(6),
        ]
        return list(await asyncio.gather(*tasks))

    # ------------------------------------------------------------------
    # Unknown / ambiguous playbook
    # ------------------------------------------------------------------
    async def _run_unknown_playbook(
        self, result: ClassificationResult
    ) -> list[ActionResult]:
        """
        UNKNOWN: graduated rate limit + NOC alert.  Never block immediately.
        """
        logger.info("UNKNOWN playbook triggered — alerting NOC for manual review.")
        tasks = [
            self._redis.set_rate_limit(self._cfg.graduated_rate_limit),
            self._alert.send_pagerduty(
                summary="Traffic classifier: UNKNOWN spike — manual review required",
                severity="warning",
                details={
                    "classification": result.traffic_class.value,
                    "confidence": result.confidence,
                    "explanation": result.explanation,
                    "fingerprint": result.fingerprint.to_dict(),
                },
            ),
            self._aws.send_sns_alert(
                subject="[WARNING] Unknown traffic spike — manual classification required",
                message=json.dumps(result.to_dict(), indent=2),
            ),
        ]
        return list(await asyncio.gather(*tasks))


# ---------------------------------------------------------------------------
# Entry point for standalone execution / testing
# ---------------------------------------------------------------------------
async def _demo() -> None:
    """Quick smoke-test of the orchestrator using a mock classification."""
    from dataclasses import dataclass
    import math

    from traffic_classifier import TrafficClass, TrafficFingerprint  # ty:ignore[unresolved-import]

    fp = TrafficFingerprint(
        ua_diversity=0.05,
        path_diversity=0.02,
        session_depth=0.1,
        geo_concentration=0.8,
        conversion_signals=0.0,
        request_timing_regularity=0.95,
        tls_fingerprint_diversity=0.04,
        referrer_presence=0.01,
        datacenter_ip_ratio=0.92,
        new_ip_ratio=0.88,
        raw_score=8.5,
        normalized_score=0.99,
    )

    mock_result = ClassificationResult(
        traffic_class=TrafficClass.ATTACK,
        confidence=0.95,
        fingerprint=fp,
        explanation=["High-confidence DDoS from demo."],
    )

    config = OrchestratorConfig()
    orch = ResponseOrchestrator(config)
    await orch.start()
    pr = await orch.handle(mock_result, attacking_ips=["1.2.3.4", "5.6.7.8"])
    print(pr.summary())
    for a in pr.actions:
        print(f"  [{a.status.value}] {a.action}: {a.message}")
    await orch.stop()


if __name__ == "__main__":
    asyncio.run(_demo())
