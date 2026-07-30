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
AWS WAF v2 Managed Rule Group Controller for AcmeToCasino iGaming Platform.

Dynamically switches AWS Managed Rule Groups between COUNT (observe) and
BLOCK (enforce) modes to harden the platform during active attacks and
revert to passive monitoring once the attack subsides.

Managed rule groups tracked:
  - AWSManagedRulesCommonRuleSet        (OWASP Top 10)
  - AWSManagedRulesSQLiRuleSet          (SQL injection)
  - AWSManagedRulesKnownBadInputsRuleSet (bad inputs / LFI / SSRF)
  - AWSManagedRulesBotControlRuleSet    (bot traffic)
  - AWSManagedRulesATPRuleSet           (account take-over prevention)

Usage:
    # Harden all rules on the ALB ACL during an active attack
    python waf_managed_rules.py enforce-all \
        --acl-id <id> --acl-name ALB_WebACL --scope REGIONAL

    # Revert to COUNT mode once the threat subsides
    python waf_managed_rules.py revert-all \
        --acl-id <id> --acl-name ALB_WebACL --scope REGIONAL

    # Toggle a single rule group
    python waf_managed_rules.py set-mode \
        --acl-id <id> --acl-name ALB_WebACL --scope REGIONAL \
        --rule AWS-AWSManagedRulesSQLiRuleSet --mode BLOCK

    # Show current mode of all tracked rule groups
    python waf_managed_rules.py status \
        --acl-id <id> --acl-name ALB_WebACL --scope REGIONAL
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
# Logging – structured JSON
# ---------------------------------------------------------------------------

class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level":     record.levelname,
            "logger":    record.name,
            "message":   record.getMessage(),
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


log = _build_logger("waf_managed_rules")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CLOUDFRONT_REGION = "us-east-1"

# Rule names must match exactly the Name field in the Web ACL rules (as set
# in waf.tf and/or waf_soar_rules.tf).
TRACKED_RULE_NAMES: list[str] = [
    "AWS-AWSManagedRulesCommonRuleSet",
    "AWS-AWSManagedRulesSQLiRuleSet",
    "AWS-AWSManagedRulesKnownBadInputsRuleSet",
    "AWS-AWSManagedRulesBotControlRuleSet",
    "AWS-AWSManagedRulesATPRuleSet",
]

MAX_LOCK_RETRIES  = 5
LOCK_RETRY_DELAY_S = 2

# ---------------------------------------------------------------------------
# AWS client helpers
# ---------------------------------------------------------------------------

def _waf_client(scope: str, region: str | None = None) -> Any:
    target_region = CLOUDFRONT_REGION if scope == "CLOUDFRONT" else (
        region or boto3.session.Session().region_name
    )
    return boto3.client("wafv2", region_name=target_region)


# ---------------------------------------------------------------------------
# Web ACL helpers
# ---------------------------------------------------------------------------

def _fetch_acl(
    client: Any,
    acl_name: str,
    acl_id: str,
    scope: str,
    retries: int = MAX_LOCK_RETRIES,
) -> dict[str, Any]:
    """Fetch Web ACL with retry on optimistic-lock conflicts."""
    for attempt in range(retries):
        try:
            return client.get_web_acl(Name=acl_name, Scope=scope, Id=acl_id)
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code == "WAFOptimisticLockException" and attempt < retries - 1:
                delay = LOCK_RETRY_DELAY_S * (attempt + 1)
                log.warning("Lock conflict fetching ACL, retrying in %ds (%d/%d)", delay, attempt + 1, retries)
                time.sleep(delay)
            else:
                raise
    raise RuntimeError("Exhausted retries fetching Web ACL")  # pragma: no cover


def _current_override_mode(rule: dict[str, Any]) -> str:
    """
    Return 'COUNT' or 'NONE' based on the rule's override_action.

    AWS WAF v2 represents:
      - COUNT mode  → override_action = {"Count": {}}
      - BLOCK mode  → override_action = {"None": {}}   (respects the managed rule's own action)
    """
    override = rule.get("OverrideAction", {})
    if "Count" in override:
        return "COUNT"
    return "NONE"   # "None" override = rule's own actions take effect (BLOCK)


def _set_override_action(rule: dict[str, Any], mode: str) -> None:
    """
    Mutate *rule* in place to set the desired override action.

    Args:
        rule: Rule dict from the Web ACL.
        mode: "COUNT" or "BLOCK".
    """
    normalized = mode.upper()
    if normalized == "COUNT":
        rule["OverrideAction"] = {"Count": {}}
        # Remove any direct Action if present (managed rules use OverrideAction)
        rule.pop("Action", None)
    elif normalized == "BLOCK":
        rule["OverrideAction"] = {"None": {}}
        rule.pop("Action", None)
    else:
        raise ValueError(f"Unknown mode '{mode}': must be COUNT or BLOCK")


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def get_rule_statuses(
    acl_id: str,
    acl_name: str,
    scope: str,
    region: str | None = None,
) -> dict[str, str]:
    """
    Return a mapping of tracked-rule-name -> current mode string.

    Mode strings: "COUNT", "NONE" (= block), or "NOT_FOUND".

    Args:
        acl_id:   Web ACL resource ID.
        acl_name: Web ACL name.
        scope:    "CLOUDFRONT" or "REGIONAL".
        region:   AWS region override.
    """
    client   = _waf_client(scope, region)
    acl_resp = _fetch_acl(client, acl_name, acl_id, scope)
    rules    = acl_resp["WebACL"].get("Rules", [])

    statuses: dict[str, str] = {}
    rule_map  = {r["Name"]: r for r in rules}

    for name in TRACKED_RULE_NAMES:
        if name in rule_map:
            statuses[name] = _current_override_mode(rule_map[name])
        else:
            statuses[name] = "NOT_FOUND"

    return statuses


def set_rule_mode(
    acl_id: str,
    acl_name: str,
    scope: str,
    rule_name: str,
    mode: str,
    region: str | None = None,
    dry_run: bool = False,
) -> None:
    """
    Switch a single managed rule group between COUNT and BLOCK mode.

    Args:
        acl_id:    Web ACL resource ID.
        acl_name:  Web ACL name.
        scope:     "CLOUDFRONT" or "REGIONAL".
        rule_name: Exact name of the rule in the Web ACL.
        mode:      "COUNT" or "BLOCK".
        region:    AWS region override.
        dry_run:   When True, log intended change without calling the API.
    """
    client = _waf_client(scope, region)

    for attempt in range(MAX_LOCK_RETRIES):
        acl_resp   = _fetch_acl(client, acl_name, acl_id, scope)
        acl        = acl_resp["WebACL"]
        lock_token = acl_resp["LockToken"]
        rules      = list(acl.get("Rules", []))

        target_rule = next((r for r in rules if r["Name"] == rule_name), None)
        if target_rule is None:
            raise ValueError(
                f"Rule '{rule_name}' not found in ACL '{acl_name}' ({acl_id}). "
                f"Tracked rule names: {TRACKED_RULE_NAMES}"
            )

        current_mode = _current_override_mode(target_rule)
        if current_mode == mode.upper() or (mode.upper() == "BLOCK" and current_mode == "NONE"):
            log.info("Rule '%s' is already in %s mode – no-op", rule_name, mode.upper())
            return

        _set_override_action(target_rule, mode)

        if dry_run:
            log.info("[DRY-RUN] Would switch '%s' to %s mode on ACL %s", rule_name, mode.upper(), acl_id)
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
            log.info(
                "Switched rule '%s' to %s mode on ACL %s (%s)",
                rule_name, mode.upper(), acl_id, scope,
            )
            return
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code == "WAFOptimisticLockException" and attempt < MAX_LOCK_RETRIES - 1:
                delay = LOCK_RETRY_DELAY_S * (attempt + 1)
                log.warning("Lock conflict, retrying in %ds (%d/%d)", delay, attempt + 1, MAX_LOCK_RETRIES)
                time.sleep(delay)
            else:
                log.error("Failed to set rule mode: %s", exc)
                raise

    raise RuntimeError(f"Exhausted retries setting mode on rule '{rule_name}'")


def enforce_all(
    acl_id: str,
    acl_name: str,
    scope: str,
    region: str | None = None,
    dry_run: bool = False,
) -> None:
    """
    Switch all tracked managed rule groups to BLOCK mode (active attack response).

    Performs a single Web ACL update to minimise lock-token round trips.

    Args:
        acl_id:   Web ACL resource ID.
        acl_name: Web ACL name.
        scope:    "CLOUDFRONT" or "REGIONAL".
        region:   AWS region override.
        dry_run:  When True, log intended changes without calling the API.
    """
    _bulk_mode_change(acl_id, acl_name, scope, "BLOCK", region, dry_run)


def revert_all(
    acl_id: str,
    acl_name: str,
    scope: str,
    region: str | None = None,
    dry_run: bool = False,
) -> None:
    """
    Revert all tracked managed rule groups to COUNT mode (post-attack normalisation).

    Args:
        acl_id:   Web ACL resource ID.
        acl_name: Web ACL name.
        scope:    "CLOUDFRONT" or "REGIONAL".
        region:   AWS region override.
        dry_run:  When True, log intended changes without calling the API.
    """
    _bulk_mode_change(acl_id, acl_name, scope, "COUNT", region, dry_run)


def _bulk_mode_change(
    acl_id: str,
    acl_name: str,
    scope: str,
    mode: str,
    region: str | None = None,
    dry_run: bool = False,
) -> None:
    """Internal helper that applies *mode* to all tracked rules in one update."""
    client = _waf_client(scope, region)

    for attempt in range(MAX_LOCK_RETRIES):
        acl_resp   = _fetch_acl(client, acl_name, acl_id, scope)
        acl        = acl_resp["WebACL"]
        lock_token = acl_resp["LockToken"]
        rules      = list(acl.get("Rules", []))

        changed: list[str] = []
        for rule in rules:
            if rule["Name"] in TRACKED_RULE_NAMES:
                _set_override_action(rule, mode)
                changed.append(rule["Name"])

        if not changed:
            log.warning(
                "No tracked managed rules found in ACL %s. "
                "Ensure rules are provisioned by waf_soar_rules.tf.",
                acl_id,
            )
            return

        if dry_run:
            log.info("[DRY-RUN] Would switch %d rule(s) to %s mode on ACL %s: %s",
                     len(changed), mode.upper(), acl_id, changed)
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
            log.info(
                "Bulk switch: %d rule(s) set to %s on ACL %s (%s): %s",
                len(changed), mode.upper(), acl_id, scope, changed,
            )
            return
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code == "WAFOptimisticLockException" and attempt < MAX_LOCK_RETRIES - 1:
                delay = LOCK_RETRY_DELAY_S * (attempt + 1)
                log.warning("Lock conflict on bulk update, retrying in %ds", delay)
                time.sleep(delay)
            else:
                log.error("Bulk mode change failed: %s", exc)
                raise

    raise RuntimeError("Exhausted retries on bulk mode change")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AWS WAF v2 managed rule group controller for AcmeToCasino",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Log intended actions without making API calls")
    parser.add_argument("--region", default=None,
                        help="AWS region (REGIONAL scope only)")

    sub = parser.add_subparsers(dest="command", required=True)

    # Common ACL arguments shared across subcommands
    def _add_acl_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--acl-id",   required=True, help="Web ACL resource ID")
        p.add_argument("--acl-name", required=True, help="Web ACL name")
        p.add_argument("--scope",    required=True, choices=["CLOUDFRONT", "REGIONAL"])

    # status
    p_status = sub.add_parser("status", help="Show current mode of all tracked rule groups")
    _add_acl_args(p_status)

    # set-mode
    p_set = sub.add_parser("set-mode", help="Switch a single managed rule group mode")
    _add_acl_args(p_set)
    p_set.add_argument(
        "--rule", required=True, choices=TRACKED_RULE_NAMES,
        help="Rule name to modify",
    )
    p_set.add_argument(
        "--mode", required=True, choices=["COUNT", "BLOCK"],
        help="Target mode",
    )

    # enforce-all
    p_enforce = sub.add_parser("enforce-all", help="Switch all tracked rules to BLOCK (active attack)")
    _add_acl_args(p_enforce)

    # revert-all
    p_revert = sub.add_parser("revert-all", help="Revert all tracked rules to COUNT (normal ops)")
    _add_acl_args(p_revert)

    return parser


def main() -> None:
    """Entry point for CLI execution."""
    parser = _build_parser()
    args   = parser.parse_args()

    try:
        if args.command == "status":
            statuses = get_rule_statuses(args.acl_id, args.acl_name, args.scope, args.region)
            print(json.dumps(statuses, indent=2))

        elif args.command == "set-mode":
            set_rule_mode(
                acl_id=args.acl_id,
                acl_name=args.acl_name,
                scope=args.scope,
                rule_name=args.rule,
                mode=args.mode,
                region=args.region,
                dry_run=args.dry_run,
            )

        elif args.command == "enforce-all":
            enforce_all(args.acl_id, args.acl_name, args.scope, args.region, args.dry_run)

        elif args.command == "revert-all":
            revert_all(args.acl_id, args.acl_name, args.scope, args.region, args.dry_run)

    except (ClientError, RuntimeError, ValueError) as exc:
        log.error("Command '%s' failed: %s", args.command, exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
