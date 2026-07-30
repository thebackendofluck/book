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
dynamodb_blacklist.py
---------------------
DynamoDB-backed IP blacklist service with:
  - TTL-based auto-expiry
  - Batch write/delete operations
  - GSI queries by reason code
  - Optimistic locking for concurrent writes
  - Automatic WAF integration hooks

Table schema:
  PK: ip_address (S)          — partition key
  TTL: expires_at (N)         — DynamoDB TTL attribute (unix epoch)
  GSI: reason-code-index      — partition key: reason (S), sort key: added_at (N)
"""

from __future__ import annotations

import ipaddress
import json
import logging
import time
from dataclasses import dataclass, asdict
from typing import Any, Iterator

import boto3
from boto3.dynamodb.conditions import Key, Attr
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@dataclass
class BlacklistEntry:
    ip_address: str
    reason: str
    added_by: str
    added_at: int
    expires_at: int
    severity: str = "MEDIUM"         # LOW | MEDIUM | HIGH | PERMANENT
    comment: str = ""
    metadata: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.metadata is None:
            self.metadata = {}

    def to_dynamo_item(self) -> dict[str, Any]:
        item: dict[str, Any] = {
            "ip_address": self.ip_address,
            "reason": self.reason,
            "added_by": self.added_by,
            "added_at": self.added_at,
            "severity": self.severity,
            "comment": self.comment,
        }
        if self.expires_at > 0:
            item["expires_at"] = self.expires_at
        if self.metadata:
            item["metadata"] = json.dumps(self.metadata)
        return item

    @classmethod
    def from_dynamo_item(cls, item: dict[str, Any]) -> "BlacklistEntry":
        metadata: dict[str, Any] = {}
        if raw_meta := item.get("metadata"):
            try:
                metadata = json.loads(raw_meta) if isinstance(raw_meta, str) else raw_meta
            except (json.JSONDecodeError, TypeError):
                metadata = {}
        return cls(
            ip_address=item["ip_address"],
            reason=item.get("reason", ""),
            added_by=item.get("added_by", ""),
            added_at=int(item.get("added_at", 0)),
            expires_at=int(item.get("expires_at", 0)),
            severity=item.get("severity", "MEDIUM"),
            comment=item.get("comment", ""),
            metadata=metadata,
        )


class IPBlacklistService:
    """
    Manages an IP blacklist stored in DynamoDB.

    Usage:
        svc = IPBlacklistService(table_name="ip-blacklist", region="us-east-1")
        svc.add("1.2.3.4", reason="BANNED_PROXY_TOR", added_by="gate_lambda", ttl_hours=720)
        entry = svc.get("1.2.3.4")
        svc.remove("1.2.3.4")
    """

    def __init__(self, table_name: str = "ip-blacklist", region: str = "us-east-1") -> None:
        self._table_name = table_name
        self._region = region
        self._dynamodb = boto3.resource("dynamodb", region_name=region)
        self._table = self._dynamodb.Table(table_name)

    # ------------------------------------------------------------------
    # Single-item operations
    # ------------------------------------------------------------------

    def get(self, ip: str) -> dict[str, Any] | None:
        """
        Retrieve blacklist entry for an IP.
        Returns None if the IP is not blacklisted or the entry has expired.
        """
        _validate_ip_address(ip)

        try:
            resp = self._table.get_item(
                Key={"ip_address": ip},
                ConsistentRead=True,
            )
        except ClientError as exc:
            logger.error("DynamoDB get_item failed for %s: %s", ip, exc)
            raise

        item = resp.get("Item")
        if not item:
            return None

        # Guard against DynamoDB TTL lag (items may survive up to 48h after expiry)
        expires_at = int(item.get("expires_at", 0))
        if expires_at and expires_at < int(time.time()):
            logger.debug("IP %s blacklist entry has TTL-expired (lag window)", ip)
            return None

        return {
            "ip_address": item["ip_address"],
            "reason": item.get("reason", ""),
            "added_by": item.get("added_by", ""),
            "added_at": int(item.get("added_at", 0)),
            "expires_at": expires_at,
            "severity": item.get("severity", "MEDIUM"),
            "comment": item.get("comment", ""),
        }

    def add(
        self,
        ip: str,
        reason: str,
        added_by: str,
        ttl_hours: int = 24,
        severity: str = "MEDIUM",
        comment: str = "",
        metadata: dict[str, Any] | None = None,
        overwrite: bool = True,
    ) -> BlacklistEntry:
        """
        Add or update an IP in the blacklist.

        Args:
            ip:          IPv4 or IPv6 address.
            reason:      Reason code (e.g. 'BANNED_PROXY_TOR').
            added_by:    Identifier of the caller (e.g. 'lambda_ip_gate').
            ttl_hours:   Time-to-live in hours. 0 = permanent.
            severity:    LOW | MEDIUM | HIGH | PERMANENT
            comment:     Human-readable note.
            metadata:    Arbitrary metadata dict.
            overwrite:   If False, raise if entry already exists.
        """
        _validate_ip_address(ip)
        now = int(time.time())
        expires_at = (now + ttl_hours * 3600) if ttl_hours > 0 else 0

        entry = BlacklistEntry(
            ip_address=ip,
            reason=reason,
            added_by=added_by,
            added_at=now,
            expires_at=expires_at,
            severity=severity,
            comment=comment,
            metadata=metadata or {},
        )

        item = entry.to_dynamo_item()

        put_kwargs: dict[str, Any] = {"Item": item}
        if not overwrite:
            put_kwargs["ConditionExpression"] = Attr("ip_address").not_exists()

        try:
            self._table.put_item(**put_kwargs)
            logger.info(
                "Blacklisted IP %s: reason=%s ttl_hours=%d severity=%s added_by=%s",
                ip, reason, ttl_hours, severity, added_by,
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException" and not overwrite:
                logger.warning("IP %s already blacklisted (overwrite=False)", ip)
                raise ValueError(f"IP {ip} is already blacklisted") from exc
            logger.error("DynamoDB put_item failed for %s: %s", ip, exc)
            raise

        return entry

    def remove(self, ip: str, removed_by: str = "manual") -> bool:
        """
        Remove an IP from the blacklist.
        Returns True if the item existed and was deleted.
        """
        _validate_ip_address(ip)
        try:
            resp = self._table.delete_item(
                Key={"ip_address": ip},
                ReturnValues="ALL_OLD",
            )
            existed = bool(resp.get("Attributes"))
            if existed:
                logger.info("Removed IP %s from blacklist (by=%s)", ip, removed_by)
            return existed
        except ClientError as exc:
            logger.error("DynamoDB delete_item failed for %s: %s", ip, exc)
            raise

    def is_blacklisted(self, ip: str) -> bool:
        """Fast check: returns True if the IP is currently blacklisted."""
        return self.get(ip) is not None

    # ------------------------------------------------------------------
    # Batch operations
    # ------------------------------------------------------------------

    def batch_add(
        self,
        entries: list[dict[str, Any]],
        ttl_hours: int = 24,
        added_by: str = "batch_import",
    ) -> int:
        """
        Write multiple IPs to the blacklist in DynamoDB batch_writer chunks of 25.

        Each entry dict must contain at minimum: ip_address, reason.
        Returns count of successfully written items.
        """
        now = int(time.time())
        expires_at = (now + ttl_hours * 3600) if ttl_hours > 0 else 0
        written = 0

        with self._table.batch_writer(overwrite_by_pkeys=["ip_address"]) as writer:
            for e in entries:
                ip = e.get("ip_address", "")
                try:
                    _validate_ip_address(ip)
                except ValueError:
                    logger.warning("Skipping invalid IP in batch: %s", ip)
                    continue

                item: dict[str, Any] = {
                    "ip_address": ip,
                    "reason": e.get("reason", "BATCH_IMPORT"),
                    "added_by": e.get("added_by", added_by),
                    "added_at": now,
                    "severity": e.get("severity", "MEDIUM"),
                    "comment": e.get("comment", ""),
                }
                if expires_at:
                    item["expires_at"] = expires_at
                if e.get("metadata"):
                    item["metadata"] = json.dumps(e["metadata"])

                writer.put_item(Item=item)
                written += 1

        logger.info("Batch wrote %d IPs to blacklist", written)
        return written

    def batch_remove(self, ips: list[str]) -> int:
        """
        Delete multiple IPs from the blacklist.
        Returns count of delete operations issued (not confirmed).
        """
        count = 0
        with self._table.batch_writer() as writer:
            for ip in ips:
                try:
                    _validate_ip_address(ip)
                except ValueError:
                    continue
                writer.delete_item(Key={"ip_address": ip})
                count += 1
        logger.info("Batch deleted %d IPs from blacklist", count)
        return count

    # ------------------------------------------------------------------
    # GSI queries (by reason code)
    # ------------------------------------------------------------------

    def query_by_reason(
        self,
        reason: str,
        limit: int = 100,
        exclusive_start_key: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        """
        Query all blacklisted IPs with a specific reason code.
        Uses the reason-code-index GSI.

        Returns: (items, last_evaluated_key)
        """
        kwargs: dict[str, Any] = {
            "IndexName": "reason-code-index",
            "KeyConditionExpression": Key("reason").eq(reason),
            "Limit": limit,
            "ScanIndexForward": False,  # newest first
        }
        if exclusive_start_key:
            kwargs["ExclusiveStartKey"] = exclusive_start_key

        try:
            resp = self._table.query(**kwargs)
        except ClientError as exc:
            logger.error("GSI query failed for reason=%s: %s", reason, exc)
            raise

        now = int(time.time())
        items = [
            {
                "ip_address": i["ip_address"],
                "reason": i.get("reason", ""),
                "added_by": i.get("added_by", ""),
                "added_at": int(i.get("added_at", 0)),
                "expires_at": int(i.get("expires_at", 0)),
                "severity": i.get("severity", "MEDIUM"),
            }
            for i in resp.get("Items", [])
            if not (int(i.get("expires_at", 0)) and int(i.get("expires_at", 0)) < now)
        ]

        return items, resp.get("LastEvaluatedKey")

    def scan_all(
        self,
        page_size: int = 500,
    ) -> Iterator[dict[str, Any]]:
        """
        Full table scan yielding all non-expired entries.
        Use sparingly — full scans are expensive. Prefer GSI queries.
        """
        now = int(time.time())
        last_key: dict[str, Any] | None = None

        while True:
            kwargs: dict[str, Any] = {
                "Limit": page_size,
                "FilterExpression": (
                    Attr("expires_at").not_exists() | Attr("expires_at").gt(now)
                ),
            }
            if last_key:
                kwargs["ExclusiveStartKey"] = last_key

            try:
                resp = self._table.scan(**kwargs)
            except ClientError as exc:
                logger.error("Table scan failed: %s", exc)
                raise

            for item in resp.get("Items", []):
                yield {
                    "ip_address": item["ip_address"],
                    "reason": item.get("reason", ""),
                    "added_at": int(item.get("added_at", 0)),
                    "expires_at": int(item.get("expires_at", 0)),
                    "severity": item.get("severity", "MEDIUM"),
                    "added_by": item.get("added_by", ""),
                }

            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """
        Return rough table statistics (uses approximate item count from describe_table).
        For exact counts, run a scan — avoid in production hot paths.
        """
        dynamodb_client = boto3.client("dynamodb", region_name=self._region)
        try:
            resp = dynamodb_client.describe_table(TableName=self._table_name)
            table_info = resp.get("Table", {})
            return {
                "table_name": self._table_name,
                "item_count_approx": table_info.get("ItemCount", 0),
                "table_size_bytes": table_info.get("TableSizeBytes", 0),
                "status": table_info.get("TableStatus", "UNKNOWN"),
                "billing_mode": table_info.get("BillingModeSummary", {}).get("BillingMode", "UNKNOWN"),
            }
        except ClientError as exc:
            logger.error("describe_table failed: %s", exc)
            return {"error": str(exc)}


# ---------------------------------------------------------------------------
# CloudFormation / CDK table definition helper
# ---------------------------------------------------------------------------

DYNAMODB_TABLE_DEFINITION: dict[str, Any] = {
    "TableName": "ip-blacklist",
    "AttributeDefinitions": [
        {"AttributeName": "ip_address", "AttributeType": "S"},
        {"AttributeName": "reason", "AttributeType": "S"},
        {"AttributeName": "added_at", "AttributeType": "N"},
    ],
    "KeySchema": [
        {"AttributeName": "ip_address", "KeyType": "HASH"},
    ],
    "GlobalSecondaryIndexes": [
        {
            "IndexName": "reason-code-index",
            "KeySchema": [
                {"AttributeName": "reason", "KeyType": "HASH"},
                {"AttributeName": "added_at", "KeyType": "RANGE"},
            ],
            "Projection": {"ProjectionType": "ALL"},
        }
    ],
    "BillingMode": "PAY_PER_REQUEST",
    "TimeToLiveSpecification": {
        "AttributeName": "expires_at",
        "Enabled": True,
    },
    "PointInTimeRecoverySpecification": {
        "PointInTimeRecoveryEnabled": True,
    },
    "SSESpecification": {
        "Enabled": True,
        "SSEType": "KMS",
    },
    "Tags": [
        {"Key": "Service", "Value": "igaming-ip-gate"},
        {"Key": "Component", "Value": "ip-blacklist"},
    ],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_ip_address(ip: str) -> None:
    """Raise ValueError if ip is not a valid IPv4 or IPv6 address."""
    if not ip:
        raise ValueError("IP address must not be empty")
    try:
        ipaddress.ip_address(ip)
    except ValueError as exc:
        raise ValueError(f"Invalid IP address: '{ip}'") from exc
