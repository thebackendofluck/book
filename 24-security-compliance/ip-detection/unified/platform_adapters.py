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
platform_adapters.py — Adapter pattern for each IP-blocking platform.

Implements a common interface across:
  - RedisAdapter      on-premises Redis (ZSET + meta hash)
  - DynamoDBAdapter   AWS DynamoDB (ip-blacklist table)
  - CloudflareKVAdapter  CF Workers KV via REST API (IP_BLACKLIST namespace)
  - AWSWAFAdapter     AWS WAF IP set (wafv2, with overflow handling)

Every adapter exposes:
  block_ip(ip, reason, ttl_seconds)  -> bool
  unblock_ip(ip)                     -> bool
  list_blocked()                     -> list[BlockedIP]
  health_check()                     -> HealthStatus

IP keys in Redis and CF KV follow a prefix so lookups by the worker/pipeline
are prefix-aware:
  Redis  : member in sorted-set "ip_blacklist:entries"  (score = expiry or 0)
           metadata key "ip_blacklist:meta:<ip>"
  CF KV  : key "bl:<ip>" — matches the pattern in cloudflare/src/gates/blacklist.ts
  DynamoDB: partition key "ip_address"
  WAF    : CIDR notation (IPv4 /32, IPv6 /128 added to the managed IP set)

Environment variables consumed (all optional with safe defaults):
  REDIS_URL
  AWS_REGION, DYNAMODB_TABLE, WAF_IP_SET_ID, WAF_IP_SET_NAME, WAF_SCOPE
  CF_ACCOUNT_ID, CF_API_TOKEN, CF_KV_NAMESPACE_ID
"""

from __future__ import annotations

import ipaddress
import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared data model
# ---------------------------------------------------------------------------

@dataclass
class BlockedIP:
    ip: str
    reason: str
    source: str
    added_at: float       # unix epoch float
    expires_at: float     # unix epoch float; 0 = permanent
    severity: str = "MEDIUM"
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_expired(self) -> bool:
        if self.expires_at == 0:
            return False
        return time.time() > self.expires_at


@dataclass
class HealthStatus:
    platform: str
    healthy: bool
    latency_ms: float
    error: Optional[str] = None
    detail: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class PlatformAdapter(ABC):
    """Common interface every platform adapter must implement."""

    @property
    @abstractmethod
    def platform_name(self) -> str: ...

    @abstractmethod
    def block_ip(self, ip: str, reason: str, ttl_seconds: int = 0, **kwargs: Any) -> bool:
        """
        Add ip to the block list.
        ttl_seconds=0 means permanent.
        Returns True if the IP was newly added; False if it already existed.
        Raises on unrecoverable errors.
        """

    @abstractmethod
    def unblock_ip(self, ip: str) -> bool:
        """
        Remove ip from the block list.
        Returns True if the IP was present and removed; False if it was not found.
        """

    @abstractmethod
    def list_blocked(self) -> list[BlockedIP]:
        """Return all currently active (non-expired) blocked IPs."""

    @abstractmethod
    def health_check(self) -> HealthStatus:
        """Probe connectivity and return a HealthStatus."""

    # Shared helpers
    @staticmethod
    def _validate_ip(ip: str) -> str:
        try:
            return str(ipaddress.ip_address(ip.strip()))
        except ValueError as exc:
            raise ValueError(f"Invalid IP address: {ip!r}") from exc

    @staticmethod
    def _normalise_cidr(ip: str) -> str:
        """Return CIDR string suitable for AWS WAF (IPv4/32 or IPv6/128)."""
        addr = ipaddress.ip_address(ip.strip())
        if isinstance(addr, ipaddress.IPv4Address):
            return f"{addr}/32"
        return f"{addr}/128"


# ---------------------------------------------------------------------------
# Redis adapter
# ---------------------------------------------------------------------------

class RedisAdapter(PlatformAdapter):
    """
    On-premises Redis adapter.

    Key layout (matches ip_blacklist_service.py):
      Sorted set  : ip_blacklist:entries        member=ip, score=expiry|0
      Metadata    : ip_blacklist:meta:<ip>       JSON blob (BlacklistEntry)
      Stats hash  : ip_blacklist:stats
    """

    ZSET_KEY    = "ip_blacklist:entries"
    META_PREFIX = "ip_blacklist:meta:"
    STATS_KEY   = "ip_blacklist:stats"

    def __init__(self, redis_url: str = "redis://localhost:6379/0") -> None:
        import redis as redis_lib
        self._client = redis_lib.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
        )

    @property
    def platform_name(self) -> str:
        return "redis"

    def block_ip(
        self,
        ip: str,
        reason: str,
        ttl_seconds: int = 0,
        source: str = "sync_manager",
        severity: str = "MEDIUM",
        metadata: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> bool:
        ip = self._validate_ip(ip)
        now = time.time()
        expires_at = now + ttl_seconds if ttl_seconds > 0 else 0.0
        score = expires_at  # 0 = permanent; positive = TTL

        existing_score = self._client.zscore(self.ZSET_KEY, ip)
        already_exists = existing_score is not None

        entry = {
            "ip": ip,
            "reason": reason,
            "source": source,
            "added_at": now,
            "expires_at": expires_at,
            "severity": severity,
            "metadata": metadata or {},
        }
        meta_key = f"{self.META_PREFIX}{ip}"

        pipe = self._client.pipeline(transaction=True)
        pipe.zadd(self.ZSET_KEY, {ip: score})
        pipe.set(meta_key, json.dumps(entry))
        if expires_at > 0:
            pipe.expireat(meta_key, int(expires_at) + 60)
        pipe.hincrby(self.STATS_KEY, "total_added", 1)
        pipe.execute()

        logger.debug("redis.block_ip ip=%s ttl=%d new=%s", ip, ttl_seconds, not already_exists)
        return not already_exists

    def unblock_ip(self, ip: str, **_kwargs: Any) -> bool:
        ip = self._validate_ip(ip)
        meta_key = f"{self.META_PREFIX}{ip}"

        pipe = self._client.pipeline(transaction=True)
        pipe.zrem(self.ZSET_KEY, ip)
        pipe.delete(meta_key)
        pipe.hincrby(self.STATS_KEY, "total_removed", 1)
        results = pipe.execute()

        removed = bool(results[0])
        logger.debug("redis.unblock_ip ip=%s removed=%s", ip, removed)
        return removed

    def list_blocked(self) -> list[BlockedIP]:
        now = time.time()
        entries: list[BlockedIP] = []
        all_items = self._client.zrange(self.ZSET_KEY, 0, -1, withscores=True)
        for ip, score in all_items:
            if score > 0 and now > score:
                continue  # lazy-skip expired
            raw = self._client.get(f"{self.META_PREFIX}{ip}")
            if not raw:
                continue
            try:
                data = json.loads(raw)
                entries.append(BlockedIP(
                    ip=data.get("ip", ip),
                    reason=data.get("reason", ""),
                    source=data.get("source", ""),
                    added_at=float(data.get("added_at", 0)),
                    expires_at=float(data.get("expires_at", 0)),
                    severity=data.get("severity", "MEDIUM"),
                    metadata=data.get("metadata", {}),
                ))
            except (json.JSONDecodeError, TypeError):
                logger.warning("redis.list_blocked corrupt meta for ip=%s", ip)
        return entries

    def health_check(self) -> HealthStatus:
        t0 = time.perf_counter()
        try:
            self._client.ping()
            count = self._client.zcard(self.ZSET_KEY) or 0
            latency_ms = (time.perf_counter() - t0) * 1000
            return HealthStatus(
                platform=self.platform_name,
                healthy=True,
                latency_ms=round(latency_ms, 2),
                detail={"blocked_count": int(count)},
            )
        except Exception as exc:  # noqa: BLE001
            latency_ms = (time.perf_counter() - t0) * 1000
            return HealthStatus(
                platform=self.platform_name,
                healthy=False,
                latency_ms=round(latency_ms, 2),
                error=str(exc),
            )

    def bulk_block(
        self,
        entries: list[tuple[str, str, int]],  # (ip, reason, ttl_seconds)
        source: str = "bulk_import",
        severity: str = "MEDIUM",
    ) -> int:
        """
        Efficient pipeline-based bulk insert.
        Returns count of successfully processed entries.
        """
        now = time.time()
        pipe = self._client.pipeline(transaction=False)
        count = 0

        for ip_raw, reason, ttl_seconds in entries:
            try:
                ip = self._validate_ip(ip_raw)
            except ValueError:
                continue
            expires_at = now + ttl_seconds if ttl_seconds > 0 else 0.0
            score = expires_at
            entry = {
                "ip": ip,
                "reason": reason,
                "source": source,
                "added_at": now,
                "expires_at": expires_at,
                "severity": severity,
                "metadata": {},
            }
            meta_key = f"{self.META_PREFIX}{ip}"
            pipe.zadd(self.ZSET_KEY, {ip: score})
            pipe.set(meta_key, json.dumps(entry))
            if expires_at > 0:
                pipe.expireat(meta_key, int(expires_at) + 60)
            count += 1

        if count > 0:
            pipe.hincrby(self.STATS_KEY, "total_added", count)
            pipe.execute()

        logger.info("redis.bulk_block processed=%d", count)
        return count


# ---------------------------------------------------------------------------
# DynamoDB adapter
# ---------------------------------------------------------------------------

class DynamoDBAdapter(PlatformAdapter):
    """
    AWS DynamoDB adapter.

    Table: ip-blacklist (matches dynamodb_blacklist.py schema)
    PK   : ip_address (S)
    TTL  : expires_at (N)  — DynamoDB native TTL
    """

    def __init__(
        self,
        table_name: str = "ip-blacklist",
        region: str = "us-east-1",
    ) -> None:
        import boto3
        self._table_name = table_name
        self._region = region
        self._dynamodb = boto3.resource("dynamodb", region_name=region)
        self._table = self._dynamodb.Table(table_name)

    @property
    def platform_name(self) -> str:
        return "dynamodb"

    def block_ip(
        self,
        ip: str,
        reason: str,
        ttl_seconds: int = 0,
        source: str = "sync_manager",
        severity: str = "MEDIUM",
        metadata: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> bool:
        from boto3.dynamodb.conditions import Attr
        from botocore.exceptions import ClientError

        ip = self._validate_ip(ip)
        now = int(time.time())
        ttl_hours = ttl_seconds // 3600 if ttl_seconds > 0 else 0
        expires_at = (now + ttl_seconds) if ttl_seconds > 0 else 0

        item: dict[str, Any] = {
            "ip_address": ip,
            "reason": reason,
            "added_by": source,
            "added_at": now,
            "severity": severity,
            "comment": f"synced via {source}",
        }
        if expires_at:
            item["expires_at"] = expires_at
        if metadata:
            item["metadata"] = json.dumps(metadata)

        try:
            # Use a condition to detect whether the item was new
            self._table.put_item(
                Item=item,
                ConditionExpression=Attr("ip_address").not_exists(),
            )
            logger.debug("dynamodb.block_ip ip=%s new=True", ip)
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                # Already existed — overwrite anyway
                self._table.put_item(Item=item)
                logger.debug("dynamodb.block_ip ip=%s new=False (overwritten)", ip)
                return False
            logger.error("dynamodb.block_ip failed ip=%s: %s", ip, exc)
            raise

    def unblock_ip(self, ip: str, **_kwargs: Any) -> bool:
        from botocore.exceptions import ClientError

        ip = self._validate_ip(ip)
        try:
            resp = self._table.delete_item(
                Key={"ip_address": ip},
                ReturnValues="ALL_OLD",
            )
            removed = bool(resp.get("Attributes"))
            logger.debug("dynamodb.unblock_ip ip=%s removed=%s", ip, removed)
            return removed
        except ClientError as exc:
            logger.error("dynamodb.unblock_ip failed ip=%s: %s", ip, exc)
            raise

    def list_blocked(self) -> list[BlockedIP]:
        from boto3.dynamodb.conditions import Attr
        from botocore.exceptions import ClientError

        now = int(time.time())
        entries: list[BlockedIP] = []
        last_key = None

        while True:
            kwargs: dict[str, Any] = {
                "FilterExpression": (
                    Attr("expires_at").not_exists() | Attr("expires_at").gt(now)
                ),
                "Limit": 500,
            }
            if last_key:
                kwargs["ExclusiveStartKey"] = last_key

            try:
                resp = self._table.scan(**kwargs)
            except ClientError as exc:
                logger.error("dynamodb.list_blocked scan failed: %s", exc)
                raise

            for item in resp.get("Items", []):
                try:
                    meta: dict[str, Any] = {}
                    if raw_meta := item.get("metadata"):
                        meta = json.loads(raw_meta) if isinstance(raw_meta, str) else raw_meta
                    entries.append(BlockedIP(
                        ip=item["ip_address"],
                        reason=item.get("reason", ""),
                        source=item.get("added_by", ""),
                        added_at=float(item.get("added_at", 0)),
                        expires_at=float(item.get("expires_at", 0)),
                        severity=item.get("severity", "MEDIUM"),
                        metadata=meta,
                    ))
                except (KeyError, TypeError, json.JSONDecodeError) as exc:
                    logger.warning("dynamodb.list_blocked malformed item: %s", exc)

            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break

        return entries

    def health_check(self) -> HealthStatus:
        import boto3
        from botocore.exceptions import ClientError

        t0 = time.perf_counter()
        try:
            client = boto3.client("dynamodb", region_name=self._region)
            resp = client.describe_table(TableName=self._table_name)
            table_info = resp.get("Table", {})
            latency_ms = (time.perf_counter() - t0) * 1000
            return HealthStatus(
                platform=self.platform_name,
                healthy=table_info.get("TableStatus") == "ACTIVE",
                latency_ms=round(latency_ms, 2),
                detail={
                    "status": table_info.get("TableStatus"),
                    "item_count_approx": table_info.get("ItemCount", 0),
                },
            )
        except ClientError as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            return HealthStatus(
                platform=self.platform_name,
                healthy=False,
                latency_ms=round(latency_ms, 2),
                error=str(exc),
            )

    def batch_block(
        self,
        entries: list[tuple[str, str, int]],  # (ip, reason, ttl_seconds)
        source: str = "bulk_import",
        severity: str = "MEDIUM",
    ) -> int:
        """
        DynamoDB batch_writer in chunks of 25.
        Returns count of successfully written items.
        """
        now = int(time.time())
        written = 0

        with self._table.batch_writer(overwrite_by_pkeys=["ip_address"]) as writer:
            for ip_raw, reason, ttl_seconds in entries:
                try:
                    ip = self._validate_ip(ip_raw)
                except ValueError:
                    continue
                expires_at = (now + ttl_seconds) if ttl_seconds > 0 else 0
                item: dict[str, Any] = {
                    "ip_address": ip,
                    "reason": reason,
                    "added_by": source,
                    "added_at": now,
                    "severity": severity,
                    "comment": f"synced via {source}",
                }
                if expires_at:
                    item["expires_at"] = expires_at
                writer.put_item(Item=item)
                written += 1

        logger.info("dynamodb.batch_block written=%d", written)
        return written

    def batch_unblock(self, ips: list[str]) -> int:
        count = 0
        with self._table.batch_writer() as writer:
            for ip_raw in ips:
                try:
                    ip = self._validate_ip(ip_raw)
                except ValueError:
                    continue
                writer.delete_item(Key={"ip_address": ip})
                count += 1
        logger.info("dynamodb.batch_unblock deleted=%d", count)
        return count


# ---------------------------------------------------------------------------
# Cloudflare KV adapter
# ---------------------------------------------------------------------------

class CloudflareKVAdapter(PlatformAdapter):
    """
    Cloudflare Workers KV adapter via the CF REST API.

    KV key format: "bl:<ip>"  (matches cloudflare/src/gates/blacklist.ts)
    KV value     : JSON BlacklistEntry (bannedAt, reason, expiresAt, createdBy)

    CF KV limits:
      - Bulk writes : max 10,000 key-value pairs per request
      - Key size    : 512 bytes max
      - Value size  : 25 MB max (we stay well under with per-IP JSON)
      - Free tier   : 1,000 writes/day; paid: 1M writes/month

    Ref: reference_cloudflare_kv_limits.md in project memory.
    """

    CF_API_BASE = "https://api.cloudflare.com/client/v4"
    BULK_BATCH_SIZE = 10_000  # CF KV bulk endpoint maximum

    def __init__(
        self,
        account_id: str,
        api_token: str,
        namespace_id: str,
    ) -> None:
        import httpx
        self._account_id = account_id
        self._namespace_id = namespace_id
        self._headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }
        # Shared client with retry-friendly timeout profile
        self._http = httpx.Client(
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers=self._headers,
        )

    @property
    def platform_name(self) -> str:
        return "cloudflare_kv"

    def _kv_url(self, suffix: str = "") -> str:
        base = (
            f"{self.CF_API_BASE}/accounts/{self._account_id}"
            f"/storage/kv/namespaces/{self._namespace_id}"
        )
        return f"{base}{suffix}"

    def _cf_raise(self, resp: Any) -> None:
        """Raise RuntimeError with CF error detail if the response indicates failure."""
        import httpx
        resp.raise_for_status()
        body = resp.json()
        if not body.get("success", True):
            errors = body.get("errors", [])
            raise RuntimeError(f"Cloudflare API error: {errors}")

    def block_ip(
        self,
        ip: str,
        reason: str,
        ttl_seconds: int = 86400,
        source: str = "sync_manager",
        **_kwargs: Any,
    ) -> bool:
        ip = self._validate_ip(ip)
        key = f"bl:{ip}"
        now_iso = _epoch_to_iso(time.time())
        entry = {
            "bannedAt": now_iso,
            "reason": reason,
            "createdBy": source,
        }
        if ttl_seconds > 0:
            entry["expiresAt"] = _epoch_to_iso(time.time() + ttl_seconds)

        url = self._kv_url(f"/values/{key}")
        params = {"expiration_ttl": ttl_seconds} if ttl_seconds > 0 else {}

        # CF KV put for a single key uses form-encoded body (metadata separately)
        resp = self._http.put(
            url,
            params=params,
            content=json.dumps(entry).encode(),
            headers={**self._headers, "Content-Type": "application/octet-stream"},
        )
        try:
            self._cf_raise(resp)
        except Exception as exc:
            logger.error("cf_kv.block_ip failed ip=%s: %s", ip, exc)
            raise

        logger.debug("cf_kv.block_ip ip=%s ttl=%d", ip, ttl_seconds)
        # CF KV PUT always overwrites; we cannot cheaply determine if it was new
        return True

    def unblock_ip(self, ip: str, **_kwargs: Any) -> bool:
        ip = self._validate_ip(ip)
        key = f"bl:{ip}"
        url = self._kv_url(f"/values/{key}")
        resp = self._http.delete(url)
        if resp.status_code == 404:
            return False
        try:
            self._cf_raise(resp)
        except Exception as exc:
            logger.error("cf_kv.unblock_ip failed ip=%s: %s", ip, exc)
            raise
        logger.debug("cf_kv.unblock_ip ip=%s", ip)
        return True

    def list_blocked(self) -> list[BlockedIP]:
        """
        Paginate through all KV keys with the "bl:" prefix.
        Returns a BlockedIP for each valid entry.
        """
        entries: list[BlockedIP] = []
        cursor: str | None = None

        while True:
            params: dict[str, Any] = {"prefix": "bl:", "limit": 1000}
            if cursor:
                params["cursor"] = cursor

            resp = self._http.get(self._kv_url("/keys"), params=params)
            try:
                self._cf_raise(resp)
            except Exception as exc:
                logger.error("cf_kv.list_blocked keys failed: %s", exc)
                raise

            body = resp.json()
            keys = body.get("result", [])

            for key_meta in keys:
                key: str = key_meta.get("name", "")
                if not key.startswith("bl:"):
                    continue
                ip = key[3:]  # strip "bl:" prefix
                # Fetch the value for each key individually (no bulk GET in CF KV REST API)
                val_resp = self._http.get(self._kv_url(f"/values/{key}"))
                if val_resp.status_code == 404:
                    continue
                try:
                    data = val_resp.json()
                    # expiresAt may be ISO string or missing (permanent)
                    expires_at = 0.0
                    if "expiresAt" in data:
                        expires_at = _iso_to_epoch(data["expiresAt"])
                    entries.append(BlockedIP(
                        ip=ip,
                        reason=data.get("reason", ""),
                        source=data.get("createdBy", ""),
                        added_at=_iso_to_epoch(data.get("bannedAt", "")),
                        expires_at=expires_at,
                    ))
                except (json.JSONDecodeError, TypeError, ValueError):
                    # Malformed value — still counts as blocked but we skip metadata
                    logger.warning("cf_kv.list_blocked malformed value for key=%s", key)

            result_info = body.get("result_info", {})
            cursor = result_info.get("cursor")
            if not cursor or not keys:
                break

        return entries

    def health_check(self) -> HealthStatus:
        t0 = time.perf_counter()
        try:
            resp = self._http.get(
                self._kv_url(""),
                timeout=10.0,
            )
            self._cf_raise(resp)
            latency_ms = (time.perf_counter() - t0) * 1000
            ns_info = resp.json().get("result", {})
            return HealthStatus(
                platform=self.platform_name,
                healthy=True,
                latency_ms=round(latency_ms, 2),
                detail={"namespace_id": self._namespace_id, "title": ns_info.get("title")},
            )
        except Exception as exc:  # noqa: BLE001
            latency_ms = (time.perf_counter() - t0) * 1000
            return HealthStatus(
                platform=self.platform_name,
                healthy=False,
                latency_ms=round(latency_ms, 2),
                error=str(exc),
            )

    def bulk_block(
        self,
        entries: list[tuple[str, str, int]],  # (ip, reason, ttl_seconds)
        source: str = "bulk_import",
    ) -> int:
        """
        Use the CF KV bulk write endpoint.
        Batches into chunks of BULK_BATCH_SIZE (10,000).
        Returns total count of entries submitted.
        """
        now_iso = _epoch_to_iso(time.time())
        kv_pairs: list[dict[str, Any]] = []

        for ip_raw, reason, ttl_seconds in entries:
            try:
                ip = self._validate_ip(ip_raw)
            except ValueError:
                continue
            entry: dict[str, Any] = {
                "bannedAt": now_iso,
                "reason": reason,
                "createdBy": source,
            }
            if ttl_seconds > 0:
                entry["expiresAt"] = _epoch_to_iso(time.time() + ttl_seconds)

            kv_item: dict[str, Any] = {
                "key": f"bl:{ip}",
                "value": json.dumps(entry),
            }
            if ttl_seconds > 0:
                kv_item["expiration_ttl"] = ttl_seconds
            kv_pairs.append(kv_item)

        total = 0
        for chunk_start in range(0, len(kv_pairs), self.BULK_BATCH_SIZE):
            chunk = kv_pairs[chunk_start : chunk_start + self.BULK_BATCH_SIZE]
            resp = self._http.put(
                self._kv_url("/bulk"),
                content=json.dumps(chunk).encode(),
                headers={**self._headers, "Content-Type": "application/json"},
            )
            try:
                self._cf_raise(resp)
            except Exception as exc:
                logger.error(
                    "cf_kv.bulk_block chunk failed start=%d size=%d: %s",
                    chunk_start, len(chunk), exc,
                )
                raise
            total += len(chunk)

        logger.info("cf_kv.bulk_block submitted=%d", total)
        return total

    def bulk_unblock(self, ips: list[str]) -> int:
        """
        Bulk delete via the CF KV bulk delete endpoint.
        CF limit: 10,000 keys per request.
        """
        keys = []
        for ip_raw in ips:
            try:
                ip = self._validate_ip(ip_raw)
                keys.append(f"bl:{ip}")
            except ValueError:
                continue

        total = 0
        for chunk_start in range(0, len(keys), self.BULK_BATCH_SIZE):
            chunk = keys[chunk_start : chunk_start + self.BULK_BATCH_SIZE]
            resp = self._http.request(
                "DELETE",
                self._kv_url("/bulk"),
                content=json.dumps(chunk).encode(),
                headers={**self._headers, "Content-Type": "application/json"},
            )
            try:
                self._cf_raise(resp)
            except Exception as exc:
                logger.error("cf_kv.bulk_unblock failed: %s", exc)
                raise
            total += len(chunk)

        logger.info("cf_kv.bulk_unblock deleted=%d", total)
        return total

    def __del__(self) -> None:
        try:
            self._http.close()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# AWS WAF adapter
# ---------------------------------------------------------------------------

class AWSWAFAdapter(PlatformAdapter):
    """
    AWS WAF v2 IP set adapter (matches waf_integration.py).

    Manages a single WAF IP set.  WAF has a hard limit of 10,000 addresses
    per IP set.  When the set is full, overflow IPs are logged and rejected
    rather than silently dropping older entries.

    The WAF IP set is updated using optimistic locking (lock token) with
    exponential back-off on WAFOptimisticLockException.
    """

    WAF_MAX_SIZE = 10_000
    MAX_RETRIES  = 3

    def __init__(
        self,
        ip_set_id: str,
        ip_set_name: str,
        scope: str = "REGIONAL",
        region: str = "us-east-1",
    ) -> None:
        import boto3
        self._ip_set_id   = ip_set_id
        self._ip_set_name = ip_set_name
        self._scope       = scope
        waf_region = "us-east-1" if scope == "CLOUDFRONT" else region
        self._waf = boto3.client("wafv2", region_name=waf_region)
        self._region = region

    @property
    def platform_name(self) -> str:
        return "aws_waf"

    def _get_ip_set(self) -> tuple[list[str], str]:
        resp = self._waf.get_ip_set(
            Name=self._ip_set_name,
            Scope=self._scope,
            Id=self._ip_set_id,
        )
        return resp["IPSet"].get("Addresses", []), resp["LockToken"]

    def _update_ip_set(self, addresses: list[str], lock_token: str) -> None:
        self._waf.update_ip_set(
            Name=self._ip_set_name,
            Scope=self._scope,
            Id=self._ip_set_id,
            Addresses=addresses,
            LockToken=lock_token,
            Description=f"Updated by sync_manager at {int(time.time())}",
        )

    def block_ip(
        self,
        ip: str,
        reason: str,
        ttl_seconds: int = 0,  # WAF has no TTL concept; ignored
        **_kwargs: Any,
    ) -> bool:
        from botocore.exceptions import ClientError

        try:
            cidr = self._normalise_cidr(self._validate_ip(ip))
        except ValueError as exc:
            logger.warning("waf.block_ip invalid ip=%s: %s", ip, exc)
            return False

        for attempt in range(self.MAX_RETRIES):
            try:
                current, lock_token = self._get_ip_set()
                if cidr in current:
                    return False  # already present
                if len(current) >= self.WAF_MAX_SIZE:
                    logger.warning(
                        "waf.block_ip capacity reached (%d), cannot add %s",
                        self.WAF_MAX_SIZE, cidr,
                    )
                    return False
                self._update_ip_set(current + [cidr], lock_token)
                logger.debug("waf.block_ip ip=%s cidr=%s", ip, cidr)
                return True
            except ClientError as exc:
                code = exc.response["Error"]["Code"]
                if code == "WAFOptimisticLockException" and attempt < self.MAX_RETRIES - 1:
                    time.sleep(0.1 * (2 ** attempt))
                    continue
                logger.error("waf.block_ip failed ip=%s: %s", ip, exc)
                raise
        return False

    def unblock_ip(self, ip: str, **_kwargs: Any) -> bool:
        from botocore.exceptions import ClientError

        try:
            cidr = self._normalise_cidr(self._validate_ip(ip))
        except ValueError:
            return False

        for attempt in range(self.MAX_RETRIES):
            try:
                current, lock_token = self._get_ip_set()
                if cidr not in current:
                    return False
                new_list = [a for a in current if a != cidr]
                self._update_ip_set(new_list, lock_token)
                logger.debug("waf.unblock_ip ip=%s", ip)
                return True
            except ClientError as exc:
                code = exc.response["Error"]["Code"]
                if code == "WAFOptimisticLockException" and attempt < self.MAX_RETRIES - 1:
                    time.sleep(0.1 * (2 ** attempt))
                    continue
                logger.error("waf.unblock_ip failed ip=%s: %s", ip, exc)
                raise
        return False

    def list_blocked(self) -> list[BlockedIP]:
        from botocore.exceptions import ClientError

        try:
            addresses, _ = self._get_ip_set()
        except ClientError as exc:
            logger.error("waf.list_blocked failed: %s", exc)
            return []

        now = time.time()
        return [
            BlockedIP(
                ip=cidr,
                reason="WAF_IPSET",
                source="aws_waf",
                added_at=now,
                expires_at=0.0,
            )
            for cidr in addresses
        ]

    def health_check(self) -> HealthStatus:
        t0 = time.perf_counter()
        try:
            addresses, _ = self._get_ip_set()
            latency_ms = (time.perf_counter() - t0) * 1000
            ipv4 = sum(1 for a in addresses if "/" in a and "." in a)
            return HealthStatus(
                platform=self.platform_name,
                healthy=True,
                latency_ms=round(latency_ms, 2),
                detail={
                    "total_addresses": len(addresses),
                    "ipv4_count": ipv4,
                    "ipv6_count": len(addresses) - ipv4,
                    "capacity_pct": round(len(addresses) / self.WAF_MAX_SIZE * 100, 1),
                    "ip_set_name": self._ip_set_name,
                },
            )
        except Exception as exc:  # noqa: BLE001
            latency_ms = (time.perf_counter() - t0) * 1000
            return HealthStatus(
                platform=self.platform_name,
                healthy=False,
                latency_ms=round(latency_ms, 2),
                error=str(exc),
            )

    def batch_block(
        self,
        ips: list[str],
        only_high_severity: bool = True,
    ) -> dict[str, int]:
        """
        Rebuild the WAF IP set from a provided list of IPs.
        WAF UpdateIPSet replaces the full list atomically.
        Returns {"added": N, "skipped": N, "invalid": N, "overflow": N}.
        """
        from botocore.exceptions import ClientError

        stats = {"added": 0, "skipped": 0, "invalid": 0, "overflow": 0}
        valid_cidrs: list[str] = []

        for ip_raw in ips:
            try:
                ip = self._validate_ip(ip_raw)
                valid_cidrs.append(self._normalise_cidr(ip))
            except ValueError:
                stats["invalid"] += 1

        # Deduplicate preserving order
        seen: set[str] = set()
        deduped: list[str] = []
        for c in valid_cidrs:
            if c not in seen:
                seen.add(c)
                deduped.append(c)

        if len(deduped) > self.WAF_MAX_SIZE:
            stats["overflow"] = len(deduped) - self.WAF_MAX_SIZE
            logger.warning(
                "waf.batch_block: %d IPs exceed WAF limit (%d), truncating to %d",
                len(deduped), self.WAF_MAX_SIZE, self.WAF_MAX_SIZE,
            )
            deduped = deduped[: self.WAF_MAX_SIZE]

        try:
            current, lock_token = self._get_ip_set()
            stats["skipped"] = len([c for c in deduped if c in set(current)])
            self._update_ip_set(deduped, lock_token)
            stats["added"] = len(deduped) - stats["skipped"]
        except ClientError as exc:
            logger.error("waf.batch_block failed: %s", exc)
            raise

        logger.info(
            "waf.batch_block result: added=%d skipped=%d invalid=%d overflow=%d",
            stats["added"], stats["skipped"], stats["invalid"], stats["overflow"],
        )
        return stats


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _epoch_to_iso(epoch: float) -> str:
    """Convert a unix epoch float to an ISO-8601 UTC string."""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _iso_to_epoch(iso: str) -> float:
    """Convert an ISO-8601 string to a unix epoch float. Returns 0.0 on failure."""
    from datetime import datetime, timezone
    if not iso:
        return 0.0
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.timestamp()
    except (ValueError, TypeError):
        return 0.0
