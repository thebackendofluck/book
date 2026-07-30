#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 23, DevSecOps for iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
AWS WAF v2 Automated IP Block/Unblock Script for AcmeToCasino iGaming Platform.

Integrates with existing IP sets defined in infra-terraform/waf.tf:
  - CLOUDFRONT scope: whs_ipset  (whitelist, us-east-1)
  - REGIONAL scope:   UsAndLicencees (whitelist, ALB)

This script manages a separate SOAR-controlled blocklist IP set alongside
the existing whitelists. It also creates and updates rate-based rules and
publishes CloudWatch metrics for every blocking action.

Usage:
    python waf_auto_block.py block   --ip 1.2.3.4/32 --scope CLOUDFRONT
    python waf_auto_block.py unblock --ip 1.2.3.4/32 --scope REGIONAL
    python waf_auto_block.py create-rate-rule  --acl-id <id> --scope REGIONAL
    python waf_auto_block.py update-rate-rule  --rule-name soar-rate-limit \
                                                --acl-id <id> --limit 500 \
                                                --scope REGIONAL
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

# ---------------------------------------------------------------------------
# Logging – structured JSON so CloudWatch Logs Insights can parse it easily
# ---------------------------------------------------------------------------

class _JsonFormatter(logging.Formatter):
    """Emit each log record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def _build_logger(name: str = __name__) -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False
    return logger


log = _build_logger("waf_auto_block")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CLOUDFRONT_REGION = "us-east-1"

# Names must match resources that already exist (or will be created by the
# SOAR Terraform module in waf_soar_rules.tf).
BLOCKLIST_IP_SET_NAMES: dict[str, str] = {
    "CLOUDFRONT": "soar-blocklist-cloudfront",
    "REGIONAL":   "soar-blocklist-regional",
}

DEFAULT_RATE_RULE_NAME = "soar-rate-limit"
DEFAULT_RATE_LIMIT     = 2000   # requests per 5-minute window
DEFAULT_RATE_RULE_PRIORITY = 1  # sits right after whitelist rules

CLOUDWATCH_NAMESPACE = "AcmeToCasino/WAF"
MAX_LOCK_RETRIES     = 5
LOCK_RETRY_DELAY_S   = 2

# ---------------------------------------------------------------------------
# AWS client helpers
# ---------------------------------------------------------------------------

def _waf_client(scope: str, region: str | None = None) -> Any:
    """Return a boto3 WAFv2 client for the correct region."""
    target_region = CLOUDFRONT_REGION if scope == "CLOUDFRONT" else (region or boto3.session.Session().region_name)
    return boto3.client("wafv2", region_name=target_region)


def _cloudwatch_client(region: str | None = None) -> Any:
    return boto3.client("cloudwatch", region_name=region or boto3.session.Session().region_name)


# ---------------------------------------------------------------------------
# Lock-token aware update helpers
# ---------------------------------------------------------------------------

def _get_ip_set(
    client: Any,
    name: str,
    ip_set_id: str,
    scope: str,
) -> dict[str, Any]:
    """Fetch an IP set and return the full response dict (includes LockToken)."""
    response = client.get_ip_set(Name=name, Scope=scope, Id=ip_set_id)
    return response


def _get_web_acl_with_retry(
    client: Any,
    name: str,
    acl_id: str,
    scope: str,
    retries: int = MAX_LOCK_RETRIES,
) -> dict[str, Any]:
    """Fetch a Web ACL, retrying on lock-token conflicts."""
    for attempt in range(retries):
        try:
            return client.get_web_acl(Name=name, Scope=scope, Id=acl_id)
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "WAFOptimisticLockException" and attempt < retries - 1:
                log.warning(
                    "Lock conflict fetching ACL %s, retrying (%d/%d)",
                    acl_id,
                    attempt + 1,
                    retries,
                )
                time.sleep(LOCK_RETRY_DELAY_S * (attempt + 1))
            else:
                raise
    raise RuntimeError("Exhausted retries fetching Web ACL")  # pragma: no cover


# ---------------------------------------------------------------------------
# IP set resolution
# ---------------------------------------------------------------------------

def _resolve_ip_set(client: Any, name: str, scope: str) -> dict[str, str]:
    """
    Return {'id': ..., 'arn': ...} for a named IP set.

    Raises RuntimeError when the set does not exist.
    """
    paginator = client.get_paginator("list_ip_sets")
    for page in paginator.paginate(Scope=scope):
        for item in page.get("IPSets", []):
            if item["Name"] == name:
                return {"id": item["Id"], "arn": item["ARN"]}
    raise RuntimeError(
        f"IP set '{name}' not found for scope {scope}. "
        "Create it first via waf_soar_rules.tf."
    )


# ---------------------------------------------------------------------------
# Core blocking functions
# ---------------------------------------------------------------------------

def add_ip_to_blocklist(
    ip_cidr: str,
    scope: str,
    region: str | None = None,
    dry_run: bool = False,
) -> None:
    """
    Add *ip_cidr* to the SOAR-managed blocklist IP set.

    Handles lock-token management automatically with exponential backoff.

    Args:
        ip_cidr:  IPv4 or IPv6 CIDR, e.g. "1.2.3.4/32".
        scope:    "CLOUDFRONT" or "REGIONAL".
        region:   AWS region override for REGIONAL scope.
        dry_run:  When True, log the action but do not call the API.
    """
    client = _waf_client(scope, region)
    set_name = BLOCKLIST_IP_SET_NAMES[scope]

    set_meta = _resolve_ip_set(client, set_name, scope)
    set_id   = set_meta["id"]

    for attempt in range(MAX_LOCK_RETRIES):
        response   = _get_ip_set(client, set_name, set_id, scope)
        ip_set     = response["IPSet"]
        lock_token = response["LockToken"]
        addresses  = list(ip_set["Addresses"])

        if ip_cidr in addresses:
            log.info("IP %s is already in blocklist %s (%s) – no-op", ip_cidr, set_name, scope)
            return

        addresses.append(ip_cidr)

        if dry_run:
            log.info("[DRY-RUN] Would add %s to %s (%s)", ip_cidr, set_name, scope)
            return

        try:
            client.update_ip_set(
                Name=set_name,
                Scope=scope,
                Id=set_id,
                Addresses=addresses,
                LockToken=lock_token,
            )
            log.info(
                "Blocked IP %s in set %s (%s), total addresses: %d",
                ip_cidr, set_name, scope, len(addresses),
            )
            _publish_block_metric(ip_cidr, scope, "blocked", region)
            return
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code == "WAFOptimisticLockException" and attempt < MAX_LOCK_RETRIES - 1:
                delay = LOCK_RETRY_DELAY_S * (attempt + 1)
                log.warning("Lock conflict on attempt %d, sleeping %ds", attempt + 1, delay)
                time.sleep(delay)
            else:
                log.error("Failed to add IP %s: %s", ip_cidr, exc)
                raise

    raise RuntimeError(f"Exhausted {MAX_LOCK_RETRIES} retries adding {ip_cidr}")


def remove_ip_from_blocklist(
    ip_cidr: str,
    scope: str,
    region: str | None = None,
    dry_run: bool = False,
) -> None:
    """
    Remove *ip_cidr* from the SOAR-managed blocklist IP set.

    Args:
        ip_cidr:  IPv4 or IPv6 CIDR.
        scope:    "CLOUDFRONT" or "REGIONAL".
        region:   AWS region override for REGIONAL scope.
        dry_run:  When True, log the action but do not call the API.
    """
    client = _waf_client(scope, region)
    set_name = BLOCKLIST_IP_SET_NAMES[scope]

    set_meta = _resolve_ip_set(client, set_name, scope)
    set_id   = set_meta["id"]

    for attempt in range(MAX_LOCK_RETRIES):
        response   = _get_ip_set(client, set_name, set_id, scope)
        ip_set     = response["IPSet"]
        lock_token = response["LockToken"]
        addresses  = list(ip_set["Addresses"])

        if ip_cidr not in addresses:
            log.info("IP %s not found in blocklist %s (%s) – no-op", ip_cidr, set_name, scope)
            return

        addresses.remove(ip_cidr)

        if dry_run:
            log.info("[DRY-RUN] Would remove %s from %s (%s)", ip_cidr, set_name, scope)
            return

        try:
            client.update_ip_set(
                Name=set_name,
                Scope=scope,
                Id=set_id,
                Addresses=addresses,
                LockToken=lock_token,
            )
            log.info(
                "Unblocked IP %s from set %s (%s), remaining: %d",
                ip_cidr, set_name, scope, len(addresses),
            )
            _publish_block_metric(ip_cidr, scope, "unblocked", region)
            return
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code == "WAFOptimisticLockException" and attempt < MAX_LOCK_RETRIES - 1:
                delay = LOCK_RETRY_DELAY_S * (attempt + 1)
                log.warning("Lock conflict on attempt %d, sleeping %ds", attempt + 1, delay)
                time.sleep(delay)
            else:
                log.error("Failed to remove IP %s: %s", ip_cidr, exc)
                raise

    raise RuntimeError(f"Exhausted {MAX_LOCK_RETRIES} retries removing {ip_cidr}")


# ---------------------------------------------------------------------------
# Rate-based rule management
# ---------------------------------------------------------------------------

def _find_rule_in_acl(rules: list[dict[str, Any]], rule_name: str) -> dict[str, Any] | None:
    """Return the rule dict matching *rule_name*, or None."""
    for rule in rules:
        if rule.get("Name") == rule_name:
            return rule
    return None


def create_rate_rule(
    acl_id: str,
    acl_name: str,
    scope: str,
    rule_name: str = DEFAULT_RATE_RULE_NAME,
    limit: int = DEFAULT_RATE_LIMIT,
    priority: int = DEFAULT_RATE_RULE_PRIORITY,
    region: str | None = None,
    dry_run: bool = False,
) -> None:
    """
    Add a rate-based rule to an existing Web ACL if one doesn't already exist.

    The rule blocks any single IP that exceeds *limit* requests in any
    5-minute window (AWS WAF fixed window).

    Args:
        acl_id:   Web ACL resource ID.
        acl_name: Web ACL name (required by WAF v2 update API).
        scope:    "CLOUDFRONT" or "REGIONAL".
        rule_name: Name to assign to the new rule.
        limit:    Maximum requests per 5-minute window before blocking.
        priority: Rule evaluation priority (lower = evaluated first).
        region:   AWS region override.
        dry_run:  When True, log the action without calling the API.
    """
    client = _waf_client(scope, region)

    for attempt in range(MAX_LOCK_RETRIES):
        acl_resp   = _get_web_acl_with_retry(client, acl_name, acl_id, scope)
        acl        = acl_resp["WebACL"]
        lock_token = acl_resp["LockToken"]
        rules      = list(acl.get("Rules", []))

        if _find_rule_in_acl(rules, rule_name):
            log.info("Rate rule '%s' already exists in ACL %s – use update-rate-rule", rule_name, acl_id)
            return

        new_rule: dict[str, Any] = {
            "Name":     rule_name,
            "Priority": priority,
            "Action":   {"Block": {}},
            "Statement": {
                "RateBasedStatement": {
                    "Limit":               limit,
                    "AggregateKeyType":    "IP",
                }
            },
            "VisibilityConfig": {
                "SampledRequestsEnabled":   True,
                "CloudWatchMetricsEnabled": True,
                "MetricName":               rule_name,
            },
        }
        rules.append(new_rule)

        if dry_run:
            log.info("[DRY-RUN] Would add rate rule '%s' (limit=%d) to ACL %s", rule_name, limit, acl_id)
            return

        try:
            client.update_web_acl(
                Name=acl_name,
                Scope=scope,
                Id=acl_id,
                DefaultAction=acl["DefaultAction"],
                Rules=rules,
                VisibilityConfig=acl["VisibilityConfig"],
                LockToken=lock_token,
            )
            log.info("Created rate rule '%s' (limit=%d/5min) on ACL %s (%s)", rule_name, limit, acl_id, scope)
            return
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code == "WAFOptimisticLockException" and attempt < MAX_LOCK_RETRIES - 1:
                delay = LOCK_RETRY_DELAY_S * (attempt + 1)
                log.warning("Lock conflict creating rate rule, retrying in %ds", delay)
                time.sleep(delay)
            else:
                log.error("Failed to create rate rule: %s", exc)
                raise

    raise RuntimeError("Exhausted retries creating rate rule")


def update_rate_rule(
    acl_id: str,
    acl_name: str,
    scope: str,
    rule_name: str = DEFAULT_RATE_RULE_NAME,
    limit: int | None = None,
    action: str | None = None,
    region: str | None = None,
    dry_run: bool = False,
) -> None:
    """
    Update the limit or action of an existing rate-based rule in a Web ACL.

    Args:
        acl_id:    Web ACL resource ID.
        acl_name:  Web ACL name.
        scope:     "CLOUDFRONT" or "REGIONAL".
        rule_name: Name of the rate rule to update.
        limit:     New request limit (None = keep current).
        action:    "BLOCK" or "COUNT" (None = keep current).
        region:    AWS region override.
        dry_run:   When True, log the action without calling the API.
    """
    if limit is None and action is None:
        log.warning("update_rate_rule called with no changes – nothing to do")
        return

    client = _waf_client(scope, region)

    for attempt in range(MAX_LOCK_RETRIES):
        acl_resp   = _get_web_acl_with_retry(client, acl_name, acl_id, scope)
        acl        = acl_resp["WebACL"]
        lock_token = acl_resp["LockToken"]
        rules      = list(acl.get("Rules", []))

        rule = _find_rule_in_acl(rules, rule_name)
        if rule is None:
            raise ValueError(
                f"Rate rule '{rule_name}' not found in ACL {acl_id}. "
                "Use create-rate-rule first."
            )

        if limit is not None:
            rule["Statement"]["RateBasedStatement"]["Limit"] = limit
            log.info("Updating rate rule '%s' limit to %d", rule_name, limit)

        if action is not None:
            normalized = action.upper()
            if normalized == "BLOCK":
                rule["Action"] = {"Block": {}}
            elif normalized == "COUNT":
                rule["Action"] = {"Count": {}}
            else:
                raise ValueError(f"Unknown action '{action}': must be BLOCK or COUNT")
            log.info("Updating rate rule '%s' action to %s", rule_name, normalized)

        if dry_run:
            log.info("[DRY-RUN] Would update rate rule '%s' on ACL %s", rule_name, acl_id)
            return

        try:
            client.update_web_acl(
                Name=acl_name,
                Scope=scope,
                Id=acl_id,
                DefaultAction=acl["DefaultAction"],
                Rules=rules,
                VisibilityConfig=acl["VisibilityConfig"],
                LockToken=lock_token,
            )
            log.info("Updated rate rule '%s' on ACL %s (%s)", rule_name, acl_id, scope)
            return
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code == "WAFOptimisticLockException" and attempt < MAX_LOCK_RETRIES - 1:
                delay = LOCK_RETRY_DELAY_S * (attempt + 1)
                log.warning("Lock conflict updating rate rule, retrying in %ds", delay)
                time.sleep(delay)
            else:
                log.error("Failed to update rate rule: %s", exc)
                raise

    raise RuntimeError("Exhausted retries updating rate rule")


# ---------------------------------------------------------------------------
# CloudWatch metric publishing
# ---------------------------------------------------------------------------

def _publish_block_metric(
    ip_cidr: str,
    scope: str,
    action: str,
    region: str | None = None,
) -> None:
    """
    Publish a custom CloudWatch metric for every block/unblock event.

    Dimensions:
      Scope  – CLOUDFRONT | REGIONAL
      Action – blocked    | unblocked
    """
    cw = _cloudwatch_client(region)
    try:
        cw.put_metric_data(
            Namespace=CLOUDWATCH_NAMESPACE,
            MetricData=[
                {
                    "MetricName": "IPBlockActions",
                    "Dimensions": [
                        {"Name": "Scope",  "Value": scope},
                        {"Name": "Action", "Value": action},
                    ],
                    "Timestamp": datetime.now(tz=timezone.utc),
                    "Value":     1,
                    "Unit":      "Count",
                }
            ],
        )
        log.info("Published CloudWatch metric: %s %s (%s)", action, ip_cidr, scope)
    except ClientError as exc:
        # Non-fatal – metric publishing should never block the core action.
        log.warning("Could not publish CloudWatch metric: %s", exc)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AWS WAF v2 automated threat response for AcmeToCasino",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log intended actions without making API calls",
    )
    parser.add_argument(
        "--region",
        default=None,
        help="AWS region (REGIONAL scope only; CLOUDFRONT always uses us-east-1)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # block
    p_block = sub.add_parser("block", help="Add an IP CIDR to the SOAR blocklist")
    p_block.add_argument("--ip",    required=True, help="IP CIDR, e.g. 1.2.3.4/32")
    p_block.add_argument("--scope", required=True, choices=["CLOUDFRONT", "REGIONAL"])

    # unblock
    p_unblock = sub.add_parser("unblock", help="Remove an IP CIDR from the SOAR blocklist")
    p_unblock.add_argument("--ip",    required=True, help="IP CIDR to remove")
    p_unblock.add_argument("--scope", required=True, choices=["CLOUDFRONT", "REGIONAL"])

    # create-rate-rule
    p_crr = sub.add_parser("create-rate-rule", help="Add a rate-based rule to a Web ACL")
    p_crr.add_argument("--acl-id",   required=True, help="Web ACL resource ID")
    p_crr.add_argument("--acl-name", required=True, help="Web ACL name")
    p_crr.add_argument("--scope",    required=True, choices=["CLOUDFRONT", "REGIONAL"])
    p_crr.add_argument("--rule-name", default=DEFAULT_RATE_RULE_NAME)
    p_crr.add_argument("--limit",    type=int, default=DEFAULT_RATE_LIMIT,
                        help="Max requests per 5-minute window (default: %(default)s)")
    p_crr.add_argument("--priority", type=int, default=DEFAULT_RATE_RULE_PRIORITY)

    # update-rate-rule
    p_urr = sub.add_parser("update-rate-rule", help="Modify an existing rate-based rule")
    p_urr.add_argument("--acl-id",   required=True, help="Web ACL resource ID")
    p_urr.add_argument("--acl-name", required=True, help="Web ACL name")
    p_urr.add_argument("--scope",    required=True, choices=["CLOUDFRONT", "REGIONAL"])
    p_urr.add_argument("--rule-name", default=DEFAULT_RATE_RULE_NAME)
    p_urr.add_argument("--limit",    type=int, default=None)
    p_urr.add_argument("--action",   choices=["BLOCK", "COUNT"], default=None)

    return parser


def main() -> None:
    """Entry point for CLI execution."""
    parser = _build_parser()
    args   = parser.parse_args()

    try:
        if args.command == "block":
            add_ip_to_blocklist(args.ip, args.scope, args.region, args.dry_run)

        elif args.command == "unblock":
            remove_ip_from_blocklist(args.ip, args.scope, args.region, args.dry_run)

        elif args.command == "create-rate-rule":
            create_rate_rule(
                acl_id=args.acl_id,
                acl_name=args.acl_name,
                scope=args.scope,
                rule_name=args.rule_name,
                limit=args.limit,
                priority=args.priority,
                region=args.region,
                dry_run=args.dry_run,
            )

        elif args.command == "update-rate-rule":
            update_rate_rule(
                acl_id=args.acl_id,
                acl_name=args.acl_name,
                scope=args.scope,
                rule_name=args.rule_name,
                limit=args.limit,
                action=args.action,
                region=args.region,
                dry_run=args.dry_run,
            )

    except (ClientError, RuntimeError, ValueError) as exc:
        log.error("Command '%s' failed: %s", args.command, exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
