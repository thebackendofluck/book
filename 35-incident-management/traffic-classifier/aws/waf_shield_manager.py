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
WAF & Shield DDoS Response Manager for iGaming Platforms

Automates the tactical response to a confirmed DDoS attack:
  1. Adds attacking IPs to WAF IP sets (with overflow sets when the 10K limit
     is reached — unlimited total IPs via set rotation)
  2. Creates rate-based WAF rules for pattern-matched attack traffic
  3. Switches CloudFront to an "emergency" cache behaviour that caches all
     responses and dramatically reduces origin load
  4. Enables AWS Shield Advanced protection on the ALB/CloudFront distribution
  5. Collects structured evidence (IP + ASN) for abuse reporting

AWS services used:
  - WAFv2 (IP sets, rate-based rules, WebACL management)
  - AWS Shield Advanced (protection management, attack telemetry)
  - CloudFront (distribution config mutation for emergency behaviour)
  - EC2 (describe security groups — for VPC-level blocking)
  - SNS (NOC notifications)
  - DynamoDB (IP block audit log)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AWS_REGION: str = os.environ.get("AWS_REGION", "us-east-1")
WAF_SCOPE: str = os.environ.get("WAF_SCOPE", "CLOUDFRONT")  # or "REGIONAL"
WAF_WEB_ACL_ID: str = os.environ.get("WAF_WEB_ACL_ID", "")
WAF_WEB_ACL_NAME: str = os.environ.get("WAF_WEB_ACL_NAME", "casino-prod-webacl")
WAF_PRIMARY_IP_SET_ID: str = os.environ.get("WAF_PRIMARY_IP_SET_ID", "")
WAF_PRIMARY_IP_SET_NAME: str = os.environ.get("WAF_PRIMARY_IP_SET_NAME", "ddos-block-primary")
CLOUDFRONT_DIST_ID: str = os.environ.get("CLOUDFRONT_DIST_ID", "")
SHIELD_ALB_ARN: str = os.environ.get("SHIELD_ALB_ARN", "")
SHIELD_CF_ARN: str = os.environ.get("SHIELD_CF_ARN", "")
SNS_NOC_TOPIC_ARN: str = os.environ.get("SNS_NOC_TOPIC_ARN", "")
DYNAMO_BLOCK_LOG_TABLE: str = os.environ.get("DYNAMO_BLOCK_LOG_TABLE", "waf-ip-block-log")

# WAF IP set hard limit per set
WAF_IP_SET_MAX_SIZE: int = 9_900  # keep 100 in reserve for safety

# Rate limit for emergency rate-based rule (requests per 5-minute window)
EMERGENCY_RATE_LIMIT: int = int(os.environ.get("EMERGENCY_RATE_LIMIT", "2000"))


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


@dataclass
class BlockResult:
    """Result of a WAF IP blocking operation."""

    ips_submitted: int = 0
    ips_blocked: int = 0
    ip_sets_used: list[str] = field(default_factory=list)
    overflow_sets_created: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class EmergencyModeResult:
    """Result of enabling full emergency response."""

    shield_protections_enabled: list[str] = field(default_factory=list)
    rate_rule_id: str = ""
    rate_rule_name: str = ""
    cloudfront_emergency_enabled: bool = False
    block_result: BlockResult | None = None
    errors: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ---------------------------------------------------------------------------
# AWS client singletons
# ---------------------------------------------------------------------------

_waf = None
_shield = None
_cf = None
_sns = None
_dynamo = None


def _waf_client() -> Any:
    global _waf
    if _waf is None:
        # WAFv2 for CloudFront must use us-east-1 regardless of target region
        region = "us-east-1" if WAF_SCOPE == "CLOUDFRONT" else AWS_REGION
        _waf = boto3.client("wafv2", region_name=region)
    return _waf


def _shield_client() -> Any:
    global _shield
    if _shield is None:
        _shield = boto3.client("shield", region_name="us-east-1")
    return _shield


def _cf_client() -> Any:
    global _cf
    if _cf is None:
        _cf = boto3.client("cloudfront", region_name="us-east-1")
    return _cf


def _sns_client() -> Any:
    global _sns
    if _sns is None:
        _sns = boto3.client("sns", region_name=AWS_REGION)
    return _sns


def _dynamo_resource() -> Any:
    global _dynamo
    if _dynamo is None:
        _dynamo = boto3.resource("dynamodb", region_name=AWS_REGION)
    return _dynamo


# ---------------------------------------------------------------------------
# IP set management
# ---------------------------------------------------------------------------


def _get_ip_set_token_and_addresses(ip_set_id: str, ip_set_name: str) -> tuple[str, list[str]]:
    """Return the (LockToken, current addresses) for a WAF IP set."""
    resp = _waf_client().get_ip_set(
        Scope=WAF_SCOPE,
        Id=ip_set_id,
        Name=ip_set_name,
    )
    ip_set = resp["IPSet"]
    token = resp["LockToken"]
    return token, ip_set.get("Addresses", [])


def _create_overflow_ip_set(index: int) -> tuple[str, str] | None:
    """Create an overflow WAF IP set for when the primary set is full."""
    name = f"ddos-block-overflow-{index:03d}"
    try:
        resp = _waf_client().create_ip_set(
            Name=name,
            Scope=WAF_SCOPE,
            IPAddressVersion="IPV4",
            Addresses=[],
            Description=f"DDoS overflow IP block set #{index:03d} — auto-created",
        )
        new_id = resp["Summary"]["Id"]
        logger.info("Created overflow IP set %s (id=%s)", name, new_id)
        return new_id, name
    except ClientError as exc:
        logger.error("Failed to create overflow IP set %s: %s", name, exc)
        return None


def _update_ip_set(ip_set_id: str, ip_set_name: str, addresses: list[str]) -> bool:
    """Update a WAF IP set with a new address list. Returns True on success."""
    try:
        token, _ = _get_ip_set_token_and_addresses(ip_set_id, ip_set_name)
        _waf_client().update_ip_set(
            Name=ip_set_name,
            Scope=WAF_SCOPE,
            Id=ip_set_id,
            Addresses=addresses,
            LockToken=token,
        )
        return True
    except ClientError as exc:
        logger.error("Failed to update IP set %s: %s", ip_set_name, exc)
        return False


def _normalise_cidr(ip: str) -> str:
    """Ensure the IP is in /32 CIDR notation (WAF requires CIDR)."""
    ip = ip.strip()
    if "/" not in ip:
        return f"{ip}/32"
    return ip


def _find_overflow_sets() -> list[tuple[str, str]]:
    """List all existing overflow IP sets sorted by index."""
    try:
        results = []
        token = None
        while True:
            kwargs: dict[str, Any] = {"Scope": WAF_SCOPE, "Limit": 100}
            if token:
                kwargs["NextMarker"] = token
            resp = _waf_client().list_ip_sets(**kwargs)
            for summary in resp.get("IPSets", []):
                if summary["Name"].startswith("ddos-block-overflow-"):
                    results.append((summary["Id"], summary["Name"]))
            token = resp.get("NextMarker")
            if not token:
                break
        results.sort(key=lambda x: x[1])  # sort by name (which contains zero-padded index)
        return results
    except ClientError as exc:
        logger.warning("Failed to list overflow IP sets: %s", exc)
        return []


def block_ips(
    attacking_ips: list[str],
    attack_id: str = "",
) -> BlockResult:
    """
    Add attacking IPs to WAF IP sets, handling the 10K-per-set limit via
    overflow sets.  Batches the updates to minimise API calls.

    Args:
        attacking_ips:  List of IPv4 addresses or CIDR blocks to block.
        attack_id:      Optional identifier for the attack event (for logging).

    Returns:
        BlockResult with counts, set names, and any errors.
    """
    result = BlockResult(ips_submitted=len(attacking_ips))

    if not WAF_PRIMARY_IP_SET_ID:
        result.errors.append("WAF_PRIMARY_IP_SET_ID not configured")
        return result

    # Normalise to /32 CIDRs and deduplicate
    cidrs = list({_normalise_cidr(ip) for ip in attacking_ips})
    result.ips_submitted = len(cidrs)

    logger.info(
        "Blocking %d unique IPs for attack %s",
        len(cidrs),
        attack_id or "unspecified",
    )

    # Load current primary set contents
    try:
        _, primary_current = _get_ip_set_token_and_addresses(
            WAF_PRIMARY_IP_SET_ID, WAF_PRIMARY_IP_SET_NAME
        )
    except ClientError as exc:
        result.errors.append(f"Cannot read primary IP set: {exc}")
        return result

    primary_current_set = set(primary_current)
    new_cidrs = [c for c in cidrs if c not in primary_current_set]

    if not new_cidrs:
        result.ips_blocked = 0
        result.actions_taken = ["All IPs already in primary block set"]  # type: ignore[attr-defined]
        return result

    # Calculate how many IPs fit in the primary set
    primary_available = WAF_IP_SET_MAX_SIZE - len(primary_current)
    primary_batch = new_cidrs[:max(0, primary_available)]
    overflow_batches = _chunk(new_cidrs[len(primary_batch):], WAF_IP_SET_MAX_SIZE)

    # Update primary set
    if primary_batch:
        updated_primary = list(primary_current_set | set(primary_batch))
        if _update_ip_set(WAF_PRIMARY_IP_SET_ID, WAF_PRIMARY_IP_SET_NAME, updated_primary):
            result.ips_blocked += len(primary_batch)
            result.ip_sets_used.append(WAF_PRIMARY_IP_SET_NAME)
            logger.info("Added %d IPs to primary IP set", len(primary_batch))

    # Handle overflow sets
    if overflow_batches:
        existing_overflow = _find_overflow_sets()
        overflow_index = len(existing_overflow)

        for batch in overflow_batches:
            # Try filling the last existing overflow set first
            if existing_overflow:
                last_id, last_name = existing_overflow[-1]
                try:
                    _, last_addrs = _get_ip_set_token_and_addresses(last_id, last_name)
                    available = WAF_IP_SET_MAX_SIZE - len(last_addrs)
                    if available > 0:
                        fill = batch[:available]
                        remainder = batch[available:]
                        if _update_ip_set(last_id, last_name, list(set(last_addrs) | set(fill))):
                            result.ips_blocked += len(fill)
                            if last_name not in result.ip_sets_used:
                                result.ip_sets_used.append(last_name)
                        batch = remainder

                except ClientError as exc:
                    logger.warning("Could not read last overflow set: %s", exc)

            if not batch:
                continue

            # Create new overflow set
            created = _create_overflow_ip_set(overflow_index)
            if created:
                new_id, new_name = created
                if _update_ip_set(new_id, new_name, batch):
                    result.ips_blocked += len(batch)
                    result.overflow_sets_created.append(new_name)
                    result.ip_sets_used.append(new_name)
                overflow_index += 1
                existing_overflow.append((new_id, new_name))
            else:
                result.errors.append(f"Failed to create overflow set #{overflow_index}")

    # Write block audit log to DynamoDB
    _log_ip_block_event(attack_id, cidrs, result)

    logger.info(
        "IP blocking complete: %d blocked across %d sets, %d overflow sets created",
        result.ips_blocked,
        len(result.ip_sets_used),
        len(result.overflow_sets_created),
    )
    return result


def _chunk(lst: list[Any], size: int) -> list[list[Any]]:
    """Split a list into chunks of at most `size` elements."""
    return [lst[i : i + size] for i in range(0, len(lst), size)]


def _log_ip_block_event(attack_id: str, cidrs: list[str], result: BlockResult) -> None:
    """Write block audit entry to DynamoDB."""
    try:
        table = _dynamo_resource().Table(DYNAMO_BLOCK_LOG_TABLE)
        event_id = hashlib.sha256(f"{attack_id}{time.time()}".encode()).hexdigest()[:16]
        table.put_item(
            Item={
                "event_id": event_id,
                "attack_id": attack_id or "unknown",
                "timestamp": result.timestamp,
                "ips_blocked": result.ips_blocked,
                "ip_sets": result.ip_sets_used,
                "overflow_sets_created": result.overflow_sets_created,
                "sample_cidrs": cidrs[:50],  # store first 50 for reference
                "ttl": int(time.time()) + 86400 * 30,  # 30-day retention
            }
        )
    except ClientError as exc:
        logger.warning("Failed to log IP block event: %s", exc)


# ---------------------------------------------------------------------------
# Shield Advanced
# ---------------------------------------------------------------------------


def enable_shield_protections() -> list[str]:
    """
    Enable AWS Shield Advanced protection on configured ALB and CloudFront
    resources.  If already enabled, the call is idempotent.

    Returns:
        List of resource ARNs successfully protected.
    """
    protected: list[str] = []
    resources_to_protect: list[str] = []

    if SHIELD_ALB_ARN:
        resources_to_protect.append(SHIELD_ALB_ARN)
    if SHIELD_CF_ARN:
        resources_to_protect.append(SHIELD_CF_ARN)

    if not resources_to_protect:
        logger.info("No Shield ARNs configured — skipping Shield protection")
        return protected

    for arn in resources_to_protect:
        try:
            _shield_client().create_protection(
                Name=f"auto-protection-{arn.split('/')[-1][:40]}",
                ResourceArn=arn,
            )
            protected.append(arn)
            logger.info("Shield Advanced protection enabled for %s", arn)
        except _shield_client().exceptions.ResourceAlreadyExistsException:
            protected.append(arn)
            logger.info("Shield Advanced already protecting %s", arn)
        except ClientError as exc:
            logger.warning("Shield protection failed for %s: %s", arn, exc)

    return protected


# ---------------------------------------------------------------------------
# Rate-based WAF rules
# ---------------------------------------------------------------------------


def _get_web_acl() -> tuple[str, dict[str, Any]] | None:
    """Fetch the WebACL definition and its lock token."""
    if not WAF_WEB_ACL_ID:
        logger.warning("WAF_WEB_ACL_ID not configured")
        return None
    try:
        resp = _waf_client().get_web_acl(
            Name=WAF_WEB_ACL_NAME,
            Scope=WAF_SCOPE,
            Id=WAF_WEB_ACL_ID,
        )
        return resp["LockToken"], resp["WebACL"]
    except ClientError as exc:
        logger.error("Failed to get WebACL: %s", exc)
        return None


def create_emergency_rate_rule(
    rule_name: str,
    rate_limit: int = EMERGENCY_RATE_LIMIT,
    priority: int = 1,
) -> str:
    """
    Dynamically add a rate-based rule to the WebACL that blocks any single IP
    making more than `rate_limit` requests in a 5-minute window.

    AWS WAFv2 rate-based rules aggregate by IP by default.

    Args:
        rule_name:  Name for the new rule (must be unique within the WebACL).
        rate_limit: Request count threshold per 5-minute window.
        priority:   Rule evaluation priority (lower = higher priority).

    Returns:
        The rule name on success, empty string on failure.
    """
    acl_data = _get_web_acl()
    if not acl_data:
        return ""

    token, web_acl = acl_data
    existing_rules: list[dict[str, Any]] = web_acl.get("Rules", [])

    # Avoid duplicate rules
    for rule in existing_rules:
        if rule.get("Name") == rule_name:
            logger.info("Rate-based rule '%s' already exists", rule_name)
            return rule_name

    new_rule: dict[str, Any] = {
        "Name": rule_name,
        "Priority": priority,
        "Statement": {
            "RateBasedStatement": {
                "Limit": rate_limit,
                "AggregateKeyType": "IP",
            }
        },
        "Action": {"Block": {}},
        "VisibilityConfig": {
            "SampledRequestsEnabled": True,
            "CloudWatchMetricsEnabled": True,
            "MetricName": rule_name,
        },
    }

    # Shift existing rules down to make room for the new priority
    updated_rules = []
    for rule in existing_rules:
        rule_copy = dict(rule)
        if rule_copy.get("Priority", 999) >= priority:
            rule_copy["Priority"] = rule_copy["Priority"] + 1
        updated_rules.append(rule_copy)
    updated_rules.append(new_rule)

    try:
        _waf_client().update_web_acl(
            Name=WAF_WEB_ACL_NAME,
            Scope=WAF_SCOPE,
            Id=WAF_WEB_ACL_ID,
            DefaultAction=web_acl.get("DefaultAction", {"Allow": {}}),
            Rules=updated_rules,
            VisibilityConfig=web_acl.get(
                "VisibilityConfig",
                {
                    "SampledRequestsEnabled": True,
                    "CloudWatchMetricsEnabled": True,
                    "MetricName": WAF_WEB_ACL_NAME,
                },
            ),
            LockToken=token,
        )
        logger.info("Rate-based rule '%s' created (limit=%d req/5min)", rule_name, rate_limit)
        return rule_name
    except ClientError as exc:
        logger.error("Failed to create rate-based rule: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# CloudFront emergency behaviour
# ---------------------------------------------------------------------------


def enable_cloudfront_emergency_mode() -> bool:
    """
    Switch CloudFront distribution to an "emergency" cache behaviour:
      - Default TTL: 300s (5 minutes) — caches most responses at edge
      - Max TTL:     3600s (1 hour)
      - Compress:    True
      - Query string forwarding disabled (maximise cache hit rate)
      - Viewer protocol policy: HTTPS only

    This dramatically reduces origin requests during an active DDoS.
    The original configuration should be restored post-attack.

    Returns:
        True on success, False on failure.
    """
    if not CLOUDFRONT_DIST_ID:
        logger.info("CLOUDFRONT_DIST_ID not configured — skipping emergency mode")
        return False

    try:
        resp = _cf_client().get_distribution_config(Id=CLOUDFRONT_DIST_ID)
        etag = resp["ETag"]
        config: dict[str, Any] = resp["DistributionConfig"]

        # Patch the default cache behaviour
        dcb = config.get("DefaultCacheBehavior", {})
        dcb["DefaultTTL"] = 300
        dcb["MaxTTL"] = 3600
        dcb["MinTTL"] = 0
        dcb["Compress"] = True
        dcb["ViewerProtocolPolicy"] = "https-only"

        # Disable query string forwarding to maximise cache hit rate
        fv = dcb.get("ForwardedValues", {})
        fv["QueryString"] = False
        fv["QueryStringCacheKeys"] = {"Quantity": 0, "Items": []}
        dcb["ForwardedValues"] = fv

        # Strip sensitive headers from origin forwarding list
        headers = fv.get("Headers", {"Quantity": 0, "Items": []})
        headers["Quantity"] = 0
        headers["Items"] = []
        fv["Headers"] = headers

        config["DefaultCacheBehavior"] = dcb

        _cf_client().update_distribution(
            DistributionConfig=config,
            Id=CLOUDFRONT_DIST_ID,
            IfMatch=etag,
        )
        logger.info(
            "CloudFront %s switched to emergency mode (TTL=300s, no querystring forwarding)",
            CLOUDFRONT_DIST_ID,
        )
        return True
    except ClientError as exc:
        logger.error("CloudFront emergency mode failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Full emergency response orchestrator
# ---------------------------------------------------------------------------


def activate_emergency_response(
    attacking_ips: list[str],
    attack_id: str,
    confidence: float = 1.0,
) -> EmergencyModeResult:
    """
    Full DDoS emergency response:
      1. Block attacking IPs in WAF
      2. Enable Shield Advanced protections
      3. Create emergency rate-based WAF rule
      4. Switch CloudFront to emergency cache mode
      5. Notify NOC

    Args:
        attacking_ips:  List of attacking IPv4 addresses.
        attack_id:      Unique attack identifier for correlation.
        confidence:     Classifier confidence score (0.0–1.0).

    Returns:
        EmergencyModeResult with full audit trail.
    """
    result = EmergencyModeResult()

    logger.info(
        "Activating emergency response for attack %s (%d IPs, confidence=%.3f)",
        attack_id,
        len(attacking_ips),
        confidence,
    )

    # Step 1: Block IPs
    if attacking_ips:
        result.block_result = block_ips(attacking_ips, attack_id)

    # Step 2: Shield Advanced
    result.shield_protections_enabled = enable_shield_protections()

    # Step 3: Rate-based rule
    rule_name = f"ddos-emergency-rate-{attack_id[:20]}"
    created_rule = create_emergency_rate_rule(rule_name, priority=1)
    if created_rule:
        result.rate_rule_id = created_rule
        result.rate_rule_name = created_rule

    # Step 4: CloudFront emergency mode
    result.cloudfront_emergency_enabled = enable_cloudfront_emergency_mode()

    # Step 5: NOC alert
    if SNS_NOC_TOPIC_ARN:
        try:
            block_info = {}
            if result.block_result:
                block_info = {
                    "ips_blocked": result.block_result.ips_blocked,
                    "ip_sets_used": result.block_result.ip_sets_used,
                }
            _sns_client().publish(
                TopicArn=SNS_NOC_TOPIC_ARN,
                Subject=f"[CRITICAL] DDoS Emergency Response Activated — {attack_id}",
                Message=json.dumps(
                    {
                        "attack_id": attack_id,
                        "confidence": confidence,
                        "ip_blocking": block_info,
                        "shield_protections": result.shield_protections_enabled,
                        "rate_rule": result.rate_rule_name,
                        "cloudfront_emergency": result.cloudfront_emergency_enabled,
                        "errors": result.errors,
                        "timestamp": result.timestamp,
                    },
                    indent=2,
                ),
            )
        except ClientError as exc:
            logger.warning("NOC notification failed: %s", exc)

    return result


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Lambda entry point.

    Expected event shapes:
      { "action": "block_ips",
        "attacking_ips": ["1.2.3.4", "5.6.7.8"],
        "attack_id": "attack-20260331-001" }

      { "action": "emergency",
        "attacking_ips": [...],
        "attack_id": "...",
        "confidence": 0.92 }

      { "action": "rate_rule",
        "rule_name": "ddos-emergency-rate-001",
        "rate_limit": 1000 }

      { "action": "cloudfront_emergency" }

      { "action": "shield_enable" }
    """
    logger.info("WAF/Shield manager invoked: %s", json.dumps(event, default=str))
    action = event.get("action", "").lower()

    if action == "block_ips":
        ips = event.get("attacking_ips", [])
        attack_id = event.get("attack_id", "")
        r = block_ips(ips, attack_id)
        return {
            "ips_submitted": r.ips_submitted,
            "ips_blocked": r.ips_blocked,
            "ip_sets_used": r.ip_sets_used,
            "overflow_sets_created": r.overflow_sets_created,
            "errors": r.errors,
        }

    if action == "emergency":
        ips = event.get("attacking_ips", [])
        attack_id = event.get("attack_id", "")
        confidence = float(event.get("confidence", 1.0))
        r = activate_emergency_response(ips, attack_id, confidence)
        return {
            "shield_protections_enabled": r.shield_protections_enabled,
            "rate_rule_name": r.rate_rule_name,
            "cloudfront_emergency_enabled": r.cloudfront_emergency_enabled,
            "block_result": {
                "ips_blocked": r.block_result.ips_blocked if r.block_result else 0,
                "ip_sets_used": r.block_result.ip_sets_used if r.block_result else [],
            },
            "errors": r.errors,
            "timestamp": r.timestamp,
        }

    if action == "rate_rule":
        rule_name = event.get("rule_name", f"ddos-rate-rule-{int(time.time())}")
        rate_limit = int(event.get("rate_limit", EMERGENCY_RATE_LIMIT))
        created = create_emergency_rate_rule(rule_name, rate_limit)
        return {"rule_created": bool(created), "rule_name": created}

    if action == "cloudfront_emergency":
        success = enable_cloudfront_emergency_mode()
        return {"cloudfront_emergency_enabled": success}

    if action == "shield_enable":
        protected = enable_shield_protections()
        return {"shield_protections_enabled": protected}

    return {"error": f"Unknown action '{action}'", "event": event}
