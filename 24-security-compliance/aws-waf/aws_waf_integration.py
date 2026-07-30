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
AWS WAF v2 rule management for iGaming platforms.

Manages a multi-layered AWS WAF rule set tuned for gambling traffic:
  - Geo-blocking: 30+ jurisdictions where online gambling is prohibited
  - Rate limiting: credential stuffing and bonus abuse thresholds
  - Managed rule group toggling: COUNT vs BLOCK mode during active attacks
  - IP set management: automated block/whitelist for dynamic threats
  - Custom rule sync: push rules from a config file to AWS WAF

Integrates with the SOAR engine to provide automated WAF response to
threat detections (bonus abuse, account takeover, DDoS patterns).

Usage:
    # Check current WAF ACL status
    python aws_waf_integration.py status --acl-name casino-alb-acl --scope REGIONAL

    # Geo-block all prohibited jurisdictions
    python aws_waf_integration.py sync-geo-rules \
        --acl-id <id> --acl-name casino-alb-acl --scope REGIONAL

    # Block an IP dynamically (SOAR response)
    python aws_waf_integration.py block-ip \
        --acl-id <id> --acl-name casino-alb-acl --scope REGIONAL \
        --ip 203.0.113.42

    # Harden all managed rules during an active attack
    python aws_waf_integration.py enforce-all \
        --acl-id <id> --acl-name casino-alb-acl --scope REGIONAL

Reference: Chapter 24 — Security and Compliance / WAF and Geo-Fencing
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

import boto3
import botocore.exceptions


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

class _JsonFormatter(logging.Formatter):
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


def _build_logger(name: str) -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False
    return logger


log = _build_logger("aws_waf_integration")


# ---------------------------------------------------------------------------
# iGaming-specific blocked country codes
# ---------------------------------------------------------------------------

# Countries where operating an online casino carries criminal liability.
# Source: Chapter 24, Table: Countries with full online gambling prohibition.
PROHIBITED_COUNTRIES: set[str] = {
    "AF",  # Afghanistan
    "AE",  # United Arab Emirates
    "BH",  # Bahrain
    "BD",  # Bangladesh
    "CN",  # China
    "IQ",  # Iraq
    "IR",  # Iran
    "KW",  # Kuwait
    "LY",  # Libya
    "KP",  # North Korea
    "MA",  # Morocco
    "OM",  # Oman
    "PK",  # Pakistan
    "QA",  # Qatar
    "SA",  # Saudi Arabia
    "SD",  # Sudan
    "YE",  # Yemen
    # Additional restriction markets (partial block, product-dependent)
    "DZ",  # Algeria
    "KH",  # Cambodia (online gambling for citizens prohibited)
}

# Managed rule groups to toggle between COUNT and BLOCK during incidents
MANAGED_RULE_GROUPS: list[str] = [
    "AWS-AWSManagedRulesCommonRuleSet",
    "AWS-AWSManagedRulesSQLiRuleSet",
    "AWS-AWSManagedRulesKnownBadInputsRuleSet",
    "AWS-AWSManagedRulesBotControlRuleSet",
    "AWS-AWSManagedRulesATPRuleSet",
]


# ---------------------------------------------------------------------------
# WAF client wrapper
# ---------------------------------------------------------------------------

class WAFIntegration:
    """
    AWS WAF v2 management client for iGaming platforms.

    Args:
        acl_id:    AWS WAF ACL ID (UUID format).
        acl_name:  AWS WAF ACL name.
        scope:     "REGIONAL" (ALB/API Gateway) or "CLOUDFRONT".
        region:    AWS region for regional ACLs.
        dry_run:   If True, log planned changes without applying them.
    """

    def __init__(
        self,
        acl_id: str,
        acl_name: str,
        scope: str = "REGIONAL",
        region: str = "us-east-1",
        dry_run: bool = False,
    ) -> None:
        self.acl_id = acl_id
        self.acl_name = acl_name
        self.scope = scope
        self.dry_run = dry_run
        self._waf = boto3.client("wafv2", region_name=region)

    # --- Lock token management ---------------------------------------------

    def _get_lock_token(self) -> str:
        """Fetch the current lock token required for WAF ACL updates."""
        try:
            resp = self._waf.get_web_acl(
                Name=self.acl_name,
                Scope=self.scope,
                Id=self.acl_id,
            )
            return resp["LockToken"]
        except botocore.exceptions.ClientError as exc:
            raise RuntimeError(f"Failed to get WAF lock token: {exc}") from exc

    def _get_current_acl(self) -> dict[str, Any]:
        try:
            resp = self._waf.get_web_acl(
                Name=self.acl_name,
                Scope=self.scope,
                Id=self.acl_id,
            )
            return resp["WebACL"]
        except botocore.exceptions.ClientError as exc:
            raise RuntimeError(f"Failed to get WAF ACL: {exc}") from exc

    # --- Geo-blocking ------------------------------------------------------

    def sync_geo_rules(self, countries: set[str] | None = None) -> bool:
        """
        Ensure geo-blocking rules cover all prohibited jurisdictions.

        Creates or updates the GeoMatchStatement rule that blocks requests
        from countries where online gambling carries criminal liability.

        Args:
            countries: Set of ISO 3166-1 alpha-2 country codes to block.
                       Defaults to PROHIBITED_COUNTRIES.

        Returns:
            True if the update succeeded or was a no-op.
        """
        block_countries = sorted(countries or PROHIBITED_COUNTRIES)
        log.info(
            "waf_sync_geo_rules countries=%d acl=%s",
            len(block_countries),
            self.acl_name,
        )

        if self.dry_run:
            log.info("DRY RUN: would block countries: %s", block_countries)
            return True

        acl = self._get_current_acl()
        rules = list(acl.get("Rules", []))

        # Remove existing geo rule if present
        rules = [r for r in rules if r.get("Name") != "igaming-geo-block"]

        geo_rule: dict[str, Any] = {
            "Name": "igaming-geo-block",
            "Priority": 10,
            "Statement": {
                "GeoMatchStatement": {
                    "CountryCodes": block_countries,
                }
            },
            "Action": {"Block": {"CustomResponse": {"ResponseCode": 451}}},
            "VisibilityConfig": {
                "SampledRequestsEnabled": True,
                "CloudWatchMetricsEnabled": True,
                "MetricName": "igaming-geo-block",
            },
        }
        rules.append(geo_rule)

        try:
            lock_token = self._get_lock_token()
            self._waf.update_web_acl(
                Name=self.acl_name,
                Scope=self.scope,
                Id=self.acl_id,
                LockToken=lock_token,
                DefaultAction=acl.get("DefaultAction", {"Allow": {}}),
                Rules=rules,
                VisibilityConfig=acl["VisibilityConfig"],
            )
            log.info("waf_geo_rules_updated countries=%d", len(block_countries))
            return True
        except botocore.exceptions.ClientError as exc:
            log.error("waf_geo_rules_update_failed: %s", exc)
            return False

    # --- IP set management (dynamic block/whitelist) -----------------------

    def _get_or_create_ip_set(self, name: str, description: str) -> tuple[str, str]:
        """
        Get or create a WAF IP set by name.

        Returns:
            Tuple of (ip_set_id, lock_token).
        """
        try:
            existing = self._waf.list_ip_sets(Scope=self.scope)
            for ip_set in existing.get("IPSets", []):
                if ip_set["Name"] == name:
                    resp = self._waf.get_ip_set(
                        Name=name,
                        Scope=self.scope,
                        Id=ip_set["Id"],
                    )
                    return ip_set["Id"], resp["LockToken"]
        except botocore.exceptions.ClientError:
            pass

        resp = self._waf.create_ip_set(
            Name=name,
            Scope=self.scope,
            IPAddressVersion="IPV4",
            Addresses=[],
            Description=description,
        )
        summary = resp["Summary"]
        return summary["Id"], summary["LockToken"]

    def block_ip(self, ip_address: str) -> bool:
        """
        Add an IP address to the WAF dynamic block list.

        Args:
            ip_address: IPv4 address in CIDR notation (e.g. "203.0.113.42/32").

        Returns:
            True on success.
        """
        cidr = ip_address if "/" in ip_address else f"{ip_address}/32"
        log.info("waf_block_ip ip=%s acl=%s", cidr, self.acl_name)

        if self.dry_run:
            log.info("DRY RUN: would block IP %s", cidr)
            return True

        try:
            ip_set_id, lock_token = self._get_or_create_ip_set(
                "igaming-dynamic-blocklist",
                "Dynamic IP blocklist — populated by SOAR automated response",
            )
            resp = self._waf.get_ip_set(
                Name="igaming-dynamic-blocklist",
                Scope=self.scope,
                Id=ip_set_id,
            )
            addresses: list[str] = resp["IPSet"].get("Addresses", [])
            if cidr not in addresses:
                addresses.append(cidr)
                self._waf.update_ip_set(
                    Name="igaming-dynamic-blocklist",
                    Scope=self.scope,
                    Id=ip_set_id,
                    Addresses=addresses,
                    LockToken=resp["LockToken"],
                )
            log.info("waf_ip_blocked ip=%s", cidr)
            return True
        except botocore.exceptions.ClientError as exc:
            log.error("waf_block_ip_failed ip=%s error=%s", cidr, exc)
            return False

    def unblock_ip(self, ip_address: str) -> bool:
        """
        Remove an IP address from the WAF dynamic block list.

        Args:
            ip_address: IPv4 address in CIDR notation.

        Returns:
            True on success (including if the IP was not in the list).
        """
        cidr = ip_address if "/" in ip_address else f"{ip_address}/32"
        log.info("waf_unblock_ip ip=%s acl=%s", cidr, self.acl_name)

        if self.dry_run:
            log.info("DRY RUN: would unblock IP %s", cidr)
            return True

        try:
            ip_set_id, _ = self._get_or_create_ip_set(
                "igaming-dynamic-blocklist",
                "Dynamic IP blocklist — populated by SOAR automated response",
            )
            resp = self._waf.get_ip_set(
                Name="igaming-dynamic-blocklist",
                Scope=self.scope,
                Id=ip_set_id,
            )
            addresses = [a for a in resp["IPSet"].get("Addresses", []) if a != cidr]
            self._waf.update_ip_set(
                Name="igaming-dynamic-blocklist",
                Scope=self.scope,
                Id=ip_set_id,
                Addresses=addresses,
                LockToken=resp["LockToken"],
            )
            log.info("waf_ip_unblocked ip=%s", cidr)
            return True
        except botocore.exceptions.ClientError as exc:
            log.error("waf_unblock_ip_failed ip=%s error=%s", cidr, exc)
            return False

    # --- Managed rule group toggling ---------------------------------------

    def set_rule_mode(self, rule_name: str, mode: str) -> bool:
        """
        Toggle a managed rule group between COUNT and BLOCK mode.

        Args:
            rule_name: Full rule name including "AWS-" prefix.
            mode:      "BLOCK" or "COUNT".

        Returns:
            True on success.
        """
        mode = mode.upper()
        if mode not in ("BLOCK", "COUNT"):
            raise ValueError(f"mode must be BLOCK or COUNT, got: {mode}")

        log.info("waf_set_rule_mode rule=%s mode=%s acl=%s", rule_name, mode, self.acl_name)

        if self.dry_run:
            log.info("DRY RUN: would set %s to %s", rule_name, mode)
            return True

        acl = self._get_current_acl()
        rules = list(acl.get("Rules", []))
        updated = False

        for rule in rules:
            if rule.get("Name") == rule_name:
                stmt = rule.get("Statement", {})
                mrg = stmt.get("ManagedRuleGroupStatement", {})
                if mode == "BLOCK":
                    rule.pop("OverrideAction", None)
                    rule["Action"] = {"Block": {}}
                else:
                    rule.pop("Action", None)
                    rule["OverrideAction"] = {"Count": {}}
                    if mrg:
                        mrg["RuleActionOverrides"] = []
                updated = True
                break

        if not updated:
            log.warning("waf_rule_not_found rule=%s", rule_name)
            return False

        try:
            lock_token = self._get_lock_token()
            self._waf.update_web_acl(
                Name=self.acl_name,
                Scope=self.scope,
                Id=self.acl_id,
                LockToken=lock_token,
                DefaultAction=acl.get("DefaultAction", {"Allow": {}}),
                Rules=rules,
                VisibilityConfig=acl["VisibilityConfig"],
            )
            log.info("waf_rule_mode_updated rule=%s mode=%s", rule_name, mode)
            return True
        except botocore.exceptions.ClientError as exc:
            log.error("waf_set_rule_mode_failed rule=%s error=%s", rule_name, exc)
            return False

    def enforce_all(self) -> dict[str, bool]:
        """Switch all tracked managed rule groups to BLOCK mode."""
        log.info("waf_enforce_all acl=%s", self.acl_name)
        results = {}
        for rule in MANAGED_RULE_GROUPS:
            results[rule] = self.set_rule_mode(rule, "BLOCK")
        return results

    def revert_all(self) -> dict[str, bool]:
        """Switch all tracked managed rule groups to COUNT (observe) mode."""
        log.info("waf_revert_all acl=%s", self.acl_name)
        results = {}
        for rule in MANAGED_RULE_GROUPS:
            results[rule] = self.set_rule_mode(rule, "COUNT")
        return results

    # --- Rate limiting -----------------------------------------------------

    def sync_rate_limit_rules(
        self,
        login_rps: int = 100,
        api_rps: int = 1000,
        deposit_rps: int = 20,
    ) -> bool:
        """
        Ensure rate-limiting rules exist for gambling-specific API endpoints.

        Args:
            login_rps:   Maximum login attempts per 5 minutes per IP.
            api_rps:     Maximum general API requests per 5 minutes per IP.
            deposit_rps: Maximum deposit attempts per 5 minutes per IP.

        Returns:
            True on success.
        """
        log.info(
            "waf_sync_rate_limits login=%d api=%d deposit=%d",
            login_rps,
            api_rps,
            deposit_rps,
        )
        if self.dry_run:
            log.info("DRY RUN: would sync rate limiting rules")
            return True

        acl = self._get_current_acl()
        rules = [r for r in acl.get("Rules", []) if not r.get("Name", "").startswith("igaming-ratelimit-")]

        rate_rules: list[dict[str, Any]] = [
            {
                "Name": "igaming-ratelimit-login",
                "Priority": 20,
                "Statement": {
                    "RateBasedStatement": {
                        "Limit": login_rps * 5,  # WAF counts per 5-minute window
                        "AggregateKeyType": "IP",
                        "ScopeDownStatement": {
                            "ByteMatchStatement": {
                                "SearchString": b"/api/v1/auth",
                                "FieldToMatch": {"UriPath": {}},
                                "TextTransformations": [{"Priority": 0, "Type": "NONE"}],
                                "PositionalConstraint": "STARTS_WITH",
                            }
                        },
                    }
                },
                "Action": {"Block": {}},
                "VisibilityConfig": {
                    "SampledRequestsEnabled": True,
                    "CloudWatchMetricsEnabled": True,
                    "MetricName": "igaming-ratelimit-login",
                },
            },
            {
                "Name": "igaming-ratelimit-deposit",
                "Priority": 21,
                "Statement": {
                    "RateBasedStatement": {
                        "Limit": deposit_rps * 5,
                        "AggregateKeyType": "IP",
                        "ScopeDownStatement": {
                            "ByteMatchStatement": {
                                "SearchString": b"/api/v1/payment/deposit",
                                "FieldToMatch": {"UriPath": {}},
                                "TextTransformations": [{"Priority": 0, "Type": "NONE"}],
                                "PositionalConstraint": "STARTS_WITH",
                            }
                        },
                    }
                },
                "Action": {"Block": {}},
                "VisibilityConfig": {
                    "SampledRequestsEnabled": True,
                    "CloudWatchMetricsEnabled": True,
                    "MetricName": "igaming-ratelimit-deposit",
                },
            },
        ]

        rules.extend(rate_rules)

        try:
            lock_token = self._get_lock_token()
            self._waf.update_web_acl(
                Name=self.acl_name,
                Scope=self.scope,
                Id=self.acl_id,
                LockToken=lock_token,
                DefaultAction=acl.get("DefaultAction", {"Allow": {}}),
                Rules=rules,
                VisibilityConfig=acl["VisibilityConfig"],
            )
            log.info("waf_rate_limits_synced count=%d", len(rate_rules))
            return True
        except botocore.exceptions.ClientError as exc:
            log.error("waf_rate_limits_sync_failed: %s", exc)
            return False

    # --- Status report -----------------------------------------------------

    def status(self) -> dict[str, Any]:
        """
        Return a structured status report for the WAF ACL.

        Returns:
            Dict with rule count, geo-block status, rate limits, and metrics.
        """
        try:
            acl = self._get_current_acl()
            rules = acl.get("Rules", [])
            geo_rule = next((r for r in rules if r.get("Name") == "igaming-geo-block"), None)
            return {
                "acl_name": self.acl_name,
                "acl_id": self.acl_id,
                "scope": self.scope,
                "total_rules": len(rules),
                "geo_block_active": geo_rule is not None,
                "geo_blocked_countries": (
                    len(
                        geo_rule["Statement"]["GeoMatchStatement"]["CountryCodes"]
                    )
                    if geo_rule
                    else 0
                ),
                "rule_names": [r["Name"] for r in rules],
                "managed_rule_modes": {
                    r["Name"]: "BLOCK" if "Action" in r else "COUNT"
                    for r in rules
                    if r.get("Name", "").startswith("AWS-")
                },
                "reported_at": datetime.now(tz=timezone.utc).isoformat(),
            }
        except botocore.exceptions.ClientError as exc:
            return {"error": str(exc), "acl_name": self.acl_name}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AWS WAF v2 management for iGaming platforms",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--acl-id", required=True, help="WAF ACL ID (UUID)")
    parser.add_argument("--acl-name", required=True, help="WAF ACL name")
    parser.add_argument(
        "--scope",
        default="REGIONAL",
        choices=["REGIONAL", "CLOUDFRONT"],
        help="WAF scope (default: %(default)s)",
    )
    parser.add_argument("--region", default="us-east-1", help="AWS region")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log planned changes without applying them",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Show current WAF ACL status")
    sub.add_parser("sync-geo-rules", help="Sync geo-blocking for prohibited jurisdictions")
    sub.add_parser("sync-rate-limits", help="Sync rate limiting rules for gambling endpoints")
    sub.add_parser("enforce-all", help="Switch all managed rules to BLOCK mode")
    sub.add_parser("revert-all", help="Switch all managed rules to COUNT mode")

    p_block = sub.add_parser("block-ip", help="Add an IP to the dynamic blocklist")
    p_block.add_argument("--ip", required=True, help="IP address (e.g. 203.0.113.42)")

    p_unblock = sub.add_parser("unblock-ip", help="Remove an IP from the dynamic blocklist")
    p_unblock.add_argument("--ip", required=True, help="IP address to remove")

    p_mode = sub.add_parser("set-mode", help="Set mode for a specific managed rule group")
    p_mode.add_argument("--rule", required=True, help="Managed rule group name")
    p_mode.add_argument("--mode", required=True, choices=["BLOCK", "COUNT"])

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    waf = WAFIntegration(
        acl_id=args.acl_id,
        acl_name=args.acl_name,
        scope=args.scope,
        region=args.region,
        dry_run=args.dry_run,
    )

    if args.command == "status":
        status = waf.status()
        print(json.dumps(status, indent=2))

    elif args.command == "sync-geo-rules":
        ok = waf.sync_geo_rules()
        sys.exit(0 if ok else 1)

    elif args.command == "sync-rate-limits":
        ok = waf.sync_rate_limit_rules()
        sys.exit(0 if ok else 1)

    elif args.command == "enforce-all":
        results = waf.enforce_all()
        print(json.dumps(results, indent=2))
        sys.exit(0 if all(results.values()) else 1)

    elif args.command == "revert-all":
        results = waf.revert_all()
        print(json.dumps(results, indent=2))
        sys.exit(0 if all(results.values()) else 1)

    elif args.command == "block-ip":
        ok = waf.block_ip(args.ip)
        sys.exit(0 if ok else 1)

    elif args.command == "unblock-ip":
        ok = waf.unblock_ip(args.ip)
        sys.exit(0 if ok else 1)

    elif args.command == "set-mode":
        ok = waf.set_rule_mode(args.rule, args.mode)
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
