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
waf_integration.py
------------------
AWS WAF IP set integration for automated edge-level IP blocking.

Manages a WAF IP set (IPv4 + IPv6) that blocks traffic before it
reaches the Lambda function. Handles the WAF lock token optimistic
concurrency protocol, batch operations, and sync from DynamoDB.

Supports both:
  - REGIONAL scope (API Gateway, ALB, AppSync)
  - CLOUDFRONT scope (CloudFront distributions — must be us-east-1)

Features:
  - Atomic add/remove with WAF lock tokens (optimistic concurrency)
  - Batch sync: rebuild WAF IP set from DynamoDB blacklist
  - CIDR normalisation (handles /32 for IPv4, /128 for IPv6)
  - Automatic de-duplication
  - CloudWatch metrics on every operation
"""

from __future__ import annotations

import ipaddress
import logging
import time
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# WAF has a hard limit of 10,000 addresses per IP set
WAF_IP_SET_MAX_SIZE = 10_000

# Maximum CIDR blocks per WAF UpdateIPSet call
WAF_MAX_BATCH_SIZE = 1_000


# ---------------------------------------------------------------------------
# CIDR helpers
# ---------------------------------------------------------------------------

def _normalise_cidr(ip_or_cidr: str) -> str:
    """
    Convert an IP address or CIDR to a WAF-compatible CIDR string.
    IPv4 → /32, IPv6 → /128 if no prefix supplied.
    Raises ValueError on invalid input.
    """
    ip_or_cidr = ip_or_cidr.strip()

    if "/" in ip_or_cidr:
        network = ipaddress.ip_network(ip_or_cidr, strict=False)
        return str(network)

    addr = ipaddress.ip_address(ip_or_cidr)
    if isinstance(addr, ipaddress.IPv4Address):
        return f"{addr}/32"
    return f"{addr}/128"


def _is_ipv4_cidr(cidr: str) -> bool:
    try:
        return isinstance(ipaddress.ip_network(cidr, strict=False), ipaddress.IPv4Network)
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# WAF Integration service
# ---------------------------------------------------------------------------

class WAFIntegration:
    """
    Manages an AWS WAF IP set for automated edge blocking.

    Usage:
        waf = WAFIntegration(ip_set_id="...", ip_set_name="igaming-blocked-ips")
        waf.add_ip("1.2.3.4")
        waf.remove_ip("1.2.3.4")
        waf.sync_from_blacklist(blacklist_service)
    """

    def __init__(
        self,
        ip_set_id: str,
        ip_set_name: str,
        scope: str = "REGIONAL",
        region: str = "us-east-1",
    ) -> None:
        self._ip_set_id = ip_set_id
        self._ip_set_name = ip_set_name
        self._scope = scope  # REGIONAL or CLOUDFRONT

        # CloudFront WAF must use us-east-1 regardless of deployment region
        waf_region = "us-east-1" if scope == "CLOUDFRONT" else region
        self._waf = boto3.client("wafv2", region_name=waf_region)
        self._cw = boto3.client("cloudwatch", region_name=region)

    # ------------------------------------------------------------------
    # Lock token management
    # ------------------------------------------------------------------

    def _get_ip_set(self) -> tuple[list[str], str]:
        """
        Fetch current IP set addresses and lock token.
        Returns: (list_of_cidrs, lock_token)
        """
        resp = self._waf.get_ip_set(
            Name=self._ip_set_name,
            Scope=self._scope,
            Id=self._ip_set_id,
        )
        ip_set = resp["IPSet"]
        lock_token = resp["LockToken"]
        return ip_set.get("Addresses", []), lock_token

    def _update_ip_set(
        self,
        addresses: list[str],
        lock_token: str,
        description: str = "",
    ) -> None:
        """
        Atomically update the WAF IP set.
        WAF requires the full address list + current lock token.
        """
        self._waf.update_ip_set(
            Name=self._ip_set_name,
            Scope=self._scope,
            Id=self._ip_set_id,
            Addresses=addresses,
            LockToken=lock_token,
            Description=description or f"Updated at {int(time.time())}",
        )

    # ------------------------------------------------------------------
    # Single IP operations
    # ------------------------------------------------------------------

    def add_ip(
        self,
        ip: str,
        description: str = "",
        max_retries: int = 3,
    ) -> bool:
        """
        Add an IP address (or CIDR) to the WAF block list.

        Uses optimistic locking with retries to handle concurrent updates.
        Returns True if the IP was added, False if it was already present.
        """
        try:
            cidr = _normalise_cidr(ip)
        except ValueError as exc:
            logger.error("Invalid IP for WAF add: %s — %s", ip, exc)
            return False

        for attempt in range(max_retries):
            try:
                current_addresses, lock_token = self._get_ip_set()

                if cidr in current_addresses:
                    logger.debug("IP %s already in WAF IP set", cidr)
                    return False

                if len(current_addresses) >= WAF_IP_SET_MAX_SIZE:
                    logger.warning(
                        "WAF IP set at capacity (%d). Cannot add %s.",
                        WAF_IP_SET_MAX_SIZE, cidr,
                    )
                    return False

                new_addresses = current_addresses + [cidr]
                self._update_ip_set(
                    new_addresses,
                    lock_token,
                    description=description or f"Auto-blocked: {cidr}",
                )
                logger.info("Added %s to WAF IP set %s", cidr, self._ip_set_name)
                self._emit_metric("IPAdded", 1)
                return True

            except ClientError as exc:
                code = exc.response["Error"]["Code"]
                if code == "WAFOptimisticLockException" and attempt < max_retries - 1:
                    logger.debug("WAF lock conflict on add, retrying (%d/%d)", attempt + 1, max_retries)
                    time.sleep(0.1 * (2 ** attempt))  # exponential back-off
                    continue
                logger.error("WAF add_ip failed for %s: %s", ip, exc)
                raise

        return False

    def remove_ip(self, ip: str, max_retries: int = 3) -> bool:
        """
        Remove an IP address from the WAF block list.
        Returns True if the IP was removed, False if it was not present.
        """
        try:
            cidr = _normalise_cidr(ip)
        except ValueError as exc:
            logger.error("Invalid IP for WAF remove: %s — %s", ip, exc)
            return False

        for attempt in range(max_retries):
            try:
                current_addresses, lock_token = self._get_ip_set()

                if cidr not in current_addresses:
                    logger.debug("IP %s not in WAF IP set", cidr)
                    return False

                new_addresses = [a for a in current_addresses if a != cidr]
                self._update_ip_set(new_addresses, lock_token)
                logger.info("Removed %s from WAF IP set %s", cidr, self._ip_set_name)
                self._emit_metric("IPRemoved", 1)
                return True

            except ClientError as exc:
                code = exc.response["Error"]["Code"]
                if code == "WAFOptimisticLockException" and attempt < max_retries - 1:
                    logger.debug("WAF lock conflict on remove, retrying (%d/%d)", attempt + 1, max_retries)
                    time.sleep(0.1 * (2 ** attempt))
                    continue
                logger.error("WAF remove_ip failed for %s: %s", ip, exc)
                raise

        return False

    # ------------------------------------------------------------------
    # Batch operations
    # ------------------------------------------------------------------

    def add_batch(self, ips: list[str], description: str = "") -> dict[str, int]:
        """
        Add a batch of IPs to the WAF IP set.
        Handles the WAF capacity limit and deduplication.

        Returns: {"added": N, "skipped": N, "invalid": N}
        """
        stats = {"added": 0, "skipped": 0, "invalid": 0}
        valid_cidrs: list[str] = []

        for ip in ips:
            try:
                valid_cidrs.append(_normalise_cidr(ip))
            except ValueError:
                stats["invalid"] += 1

        if not valid_cidrs:
            return stats

        try:
            current_addresses, lock_token = self._get_ip_set()
        except ClientError as exc:
            logger.error("Failed to fetch WAF IP set for batch add: %s", exc)
            raise

        existing_set = set(current_addresses)
        new_cidrs = [c for c in valid_cidrs if c not in existing_set]
        stats["skipped"] = len(valid_cidrs) - len(new_cidrs)

        available_slots = WAF_IP_SET_MAX_SIZE - len(current_addresses)
        if len(new_cidrs) > available_slots:
            logger.warning(
                "WAF capacity: can only add %d of %d requested IPs",
                available_slots, len(new_cidrs),
            )
            new_cidrs = new_cidrs[:available_slots]

        if not new_cidrs:
            return stats

        # WAF UpdateIPSet requires the complete list; build it
        combined = list(existing_set) + new_cidrs

        # Chunk if over WAF's per-call limit
        for chunk_start in range(0, len(combined), WAF_MAX_BATCH_SIZE * 2):
            chunk = combined[chunk_start: chunk_start + WAF_MAX_BATCH_SIZE * 2]
            try:
                current_addresses, lock_token = self._get_ip_set()
                self._update_ip_set(
                    chunk,
                    lock_token,
                    description=description or f"Batch add {len(new_cidrs)} IPs",
                )
                stats["added"] += len(new_cidrs)
                self._emit_metric("IPAdded", len(new_cidrs))
            except ClientError as exc:
                logger.error("WAF batch add failed: %s", exc)
                raise

        return stats

    def sync_from_blacklist(self, blacklist_service: Any) -> dict[str, Any]:
        """
        Rebuild the WAF IP set from the DynamoDB blacklist.
        Only syncs HIGH and PERMANENT severity entries.

        Returns sync statistics dict.
        """
        logger.info("Starting WAF sync from DynamoDB blacklist")
        t0 = time.perf_counter()

        high_severity_ips: list[str] = []
        for entry in blacklist_service.scan_all():
            severity = entry.get("severity", "MEDIUM")
            if severity in ("HIGH", "PERMANENT"):
                high_severity_ips.append(entry["ip_address"])

        if len(high_severity_ips) > WAF_IP_SET_MAX_SIZE:
            logger.warning(
                "Blacklist has %d HIGH/PERMANENT entries, WAF limit is %d. "
                "Truncating to most recently added.",
                len(high_severity_ips), WAF_IP_SET_MAX_SIZE,
            )
            high_severity_ips = high_severity_ips[:WAF_IP_SET_MAX_SIZE]

        # Normalise all CIDRs
        valid_cidrs: list[str] = []
        invalid_count = 0
        for ip in high_severity_ips:
            try:
                valid_cidrs.append(_normalise_cidr(ip))
            except ValueError:
                invalid_count += 1

        # Get current lock token and replace entire set
        try:
            _, lock_token = self._get_ip_set()
            self._update_ip_set(
                list(dict.fromkeys(valid_cidrs)),  # deduplicate
                lock_token,
                description=f"Synced from blacklist at {int(time.time())}",
            )
        except ClientError as exc:
            logger.error("WAF sync failed: %s", exc)
            raise

        elapsed = (time.perf_counter() - t0) * 1000
        result = {
            "synced_count": len(valid_cidrs),
            "invalid_count": invalid_count,
            "elapsed_ms": round(elapsed, 1),
            "ip_set_id": self._ip_set_id,
        }
        logger.info("WAF sync complete: %s", result)
        self._emit_metric("SyncedIPs", len(valid_cidrs))
        return result

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def list_blocked_ips(self) -> list[str]:
        """Return all CIDRs currently in the WAF IP set."""
        try:
            addresses, _ = self._get_ip_set()
            return addresses
        except ClientError as exc:
            logger.error("list_blocked_ips failed: %s", exc)
            return []

    def get_stats(self) -> dict[str, Any]:
        """Return current WAF IP set statistics."""
        try:
            addresses, lock_token = self._get_ip_set()
            ipv4_count = sum(1 for a in addresses if _is_ipv4_cidr(a))
            return {
                "ip_set_id": self._ip_set_id,
                "ip_set_name": self._ip_set_name,
                "scope": self._scope,
                "total_addresses": len(addresses),
                "ipv4_addresses": ipv4_count,
                "ipv6_addresses": len(addresses) - ipv4_count,
                "capacity_used_pct": round(len(addresses) / WAF_IP_SET_MAX_SIZE * 100, 1),
            }
        except ClientError as exc:
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # CloudWatch metrics
    # ------------------------------------------------------------------

    def _emit_metric(self, metric_name: str, value: float) -> None:
        """Emit a WAF integration metric to CloudWatch."""
        try:
            self._cw.put_metric_data(
                Namespace="iGaming/WAFIntegration",
                MetricData=[
                    {
                        "MetricName": metric_name,
                        "Value": value,
                        "Unit": "Count",
                        "Dimensions": [
                            {"Name": "IPSetName", "Value": self._ip_set_name},
                            {"Name": "Scope", "Value": self._scope},
                        ],
                    }
                ],
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("CloudWatch metric emit failed (non-critical): %s", exc)


# ---------------------------------------------------------------------------
# Factory: create a WAF IP set via CloudFormation / SDK
# ---------------------------------------------------------------------------

def create_ip_set(
    name: str,
    scope: str = "REGIONAL",
    region: str = "us-east-1",
    description: str = "iGaming blocked IPs",
    tags: dict[str, str] | None = None,
) -> dict[str, str]:
    """
    Create a new WAF IP set and return its ID and ARN.
    Call once during initial deployment.
    """
    waf_region = "us-east-1" if scope == "CLOUDFRONT" else region
    waf = boto3.client("wafv2", region_name=waf_region)

    tag_list = [{"Key": k, "Value": v} for k, v in (tags or {}).items()]

    resp = waf.create_ip_set(
        Name=name,
        Scope=scope,
        IPAddressVersion="IPV4",
        Addresses=[],
        Description=description,
        Tags=tag_list or [{"Key": "Service", "Value": "igaming-ip-gate"}],
    )

    summary = resp["Summary"]
    logger.info(
        "Created WAF IP set: name=%s id=%s arn=%s",
        name, summary["Id"], summary["ARN"],
    )
    return {
        "id": summary["Id"],
        "name": summary["Name"],
        "arn": summary["ARN"],
        "lock_token": summary["LockToken"],
    }
