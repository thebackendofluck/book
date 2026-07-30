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
ip_blacklist_service.py — Redis-backed IP blacklist with TTL support.

Stores banned IPs in a Redis sorted set (score = expiry unix timestamp, 0 =
permanent) and a hash of per-entry metadata.  Provides bulk import from
AbuseIPDB CSV exports.

Key design choices:
  - Permanent bans: score = 0 in ZSET + metadata hash
  - TTL bans:       score = expiry epoch; background scanner (or ZRANGEBYSCORE)
                    prunes expired entries at check time (lazy expiry)
  - Thread-safe:    all mutations are MULTI/EXEC pipelines
  - No secrets:     Redis URL from environment variable REDIS_URL
"""

from __future__ import annotations

import csv
import io
import ipaddress
import json
import os
import time
from dataclasses import asdict, dataclass
from typing import Iterator, Optional

import redis
import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REDIS_URL: str = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

BLACKLIST_ZSET_KEY = "ip_blacklist:entries"      # sorted set: ip -> expiry (0 = permanent)
BLACKLIST_META_PREFIX = "ip_blacklist:meta:"     # hash key per IP
BLACKLIST_STATS_KEY = "ip_blacklist:stats"       # hash: total_added, total_removed

# AbuseIPDB confidence threshold: IPs below this score are not imported
ABUSEIPDB_MIN_CONFIDENCE = 75


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class BlacklistEntry:
    ip: str
    reason: str
    source: str                     # e.g. "abuseipdb", "manual", "automated"
    added_at: float                 # unix timestamp
    expires_at: float               # unix timestamp; 0 = permanent
    confidence_score: int = 0       # 0-100, from source DB
    abuse_categories: list[int] | None = None

    def is_expired(self) -> bool:
        if self.expires_at == 0:
            return False
        return time.time() > self.expires_at


@dataclass
class BlacklistCheckResult:
    ip: str
    is_blacklisted: bool
    entry: Optional[BlacklistEntry] = None
    checked_at: float = 0.0

    def __post_init__(self) -> None:
        if self.checked_at == 0.0:
            self.checked_at = time.time()


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class IPBlacklistService:
    """
    Redis-backed IP blacklist.

    Usage:
        svc = IPBlacklistService()
        svc.add("1.2.3.4", reason="AbuseIPDB report", source="abuseipdb",
                confidence_score=95, ttl_seconds=86400)
        result = svc.check("1.2.3.4")
        if result.is_blacklisted:
            print(result.entry.reason)
    """

    def __init__(self, redis_url: str = REDIS_URL) -> None:
        self._client: redis.Redis[str] = redis.from_url(  # ty:ignore[invalid-assignment]
            redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
        )

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def add(
        self,
        ip: str,
        reason: str,
        source: str = "manual",
        ttl_seconds: int = 0,
        confidence_score: int = 0,
        abuse_categories: list[int] | None = None,
    ) -> None:
        """
        Add an IP to the blacklist.

        Args:
            ip:                IPv4 or IPv6 address (validated on entry).
            reason:            Human-readable reason for the ban.
            source:            Origin of the ban decision.
            ttl_seconds:       0 = permanent; >0 = expires after N seconds.
            confidence_score:  0-100 score from originating database.
            abuse_categories:  AbuseIPDB category IDs.
        """
        ip = _validate_ip(ip)
        now = time.time()
        expires_at = now + ttl_seconds if ttl_seconds > 0 else 0.0
        score = expires_at  # 0 = permanent; score encodes expiry for range queries

        entry = BlacklistEntry(
            ip=ip,
            reason=reason,
            source=source,
            added_at=now,
            expires_at=expires_at,
            confidence_score=confidence_score,
            abuse_categories=abuse_categories or [],
        )

        meta_key = f"{BLACKLIST_META_PREFIX}{ip}"

        pipe = self._client.pipeline(transaction=True)
        pipe.zadd(BLACKLIST_ZSET_KEY, {ip: score})
        pipe.set(meta_key, json.dumps(asdict(entry)))
        if expires_at > 0:
            pipe.expireat(meta_key, int(expires_at) + 60)  # meta expires shortly after entry
        pipe.hincrby(BLACKLIST_STATS_KEY, "total_added", 1)
        pipe.execute()

        logger.info(
            "ip_blacklisted",
            ip=ip,
            source=source,
            confidence=confidence_score,
            permanent=(ttl_seconds == 0),
        )

    def remove(self, ip: str) -> bool:
        """
        Remove an IP from the blacklist.

        Returns True if the IP was present and removed, False if it was not found.
        """
        ip = _validate_ip(ip)
        meta_key = f"{BLACKLIST_META_PREFIX}{ip}"

        pipe = self._client.pipeline(transaction=True)
        pipe.zrem(BLACKLIST_ZSET_KEY, ip)
        pipe.delete(meta_key)
        pipe.hincrby(BLACKLIST_STATS_KEY, "total_removed", 1)
        results = pipe.execute()

        removed = bool(results[0])
        if removed:
            logger.info("ip_un_blacklisted", ip=ip)
        return removed

    def check(self, ip: str) -> BlacklistCheckResult:
        """
        Check whether an IP is currently blacklisted.

        Handles lazy expiry: if the TTL-based entry has expired since it was
        added, it is purged on this read and treated as clean.
        """
        try:
            ip = _validate_ip(ip)
        except ValueError:
            return BlacklistCheckResult(ip=ip, is_blacklisted=False)

        score = self._client.zscore(BLACKLIST_ZSET_KEY, ip)
        if score is None:
            return BlacklistCheckResult(ip=ip, is_blacklisted=False)

        # Lazy expiry check: score > 0 means TTL-based; score == 0 means permanent
        if score > 0 and time.time() > score:
            # Entry has expired — purge it
            self._purge_expired(ip)
            return BlacklistCheckResult(ip=ip, is_blacklisted=False)

        # Load metadata
        meta_key = f"{BLACKLIST_META_PREFIX}{ip}"
        raw = self._client.get(meta_key)
        entry: Optional[BlacklistEntry] = None
        if raw:
            try:
                data = json.loads(raw)
                entry = BlacklistEntry(**data)
            except (json.JSONDecodeError, TypeError):
                logger.warning("blacklist_meta_corrupt", ip=ip)

        return BlacklistCheckResult(ip=ip, is_blacklisted=True, entry=entry)

    def bulk_check(self, ips: list[str]) -> dict[str, BlacklistCheckResult]:
        """Check multiple IPs at once using a pipeline."""
        results: dict[str, BlacklistCheckResult] = {}
        pipe = self._client.pipeline(transaction=False)
        valid_ips: list[str] = []

        for raw_ip in ips:
            try:
                ip = _validate_ip(raw_ip)
                valid_ips.append(ip)
                pipe.zscore(BLACKLIST_ZSET_KEY, ip)
            except ValueError:
                results[raw_ip] = BlacklistCheckResult(ip=raw_ip, is_blacklisted=False)

        scores = pipe.execute()

        for ip, score in zip(valid_ips, scores):
            if score is None:
                results[ip] = BlacklistCheckResult(ip=ip, is_blacklisted=False)
            elif score > 0 and time.time() > score:
                self._purge_expired(ip)
                results[ip] = BlacklistCheckResult(ip=ip, is_blacklisted=False)
            else:
                meta_key = f"{BLACKLIST_META_PREFIX}{ip}"
                raw = self._client.get(meta_key)
                entry: Optional[BlacklistEntry] = None
                if raw:
                    try:
                        entry = BlacklistEntry(**json.loads(raw))
                    except (json.JSONDecodeError, TypeError):
                        pass
                results[ip] = BlacklistCheckResult(ip=ip, is_blacklisted=True, entry=entry)

        return results

    # ------------------------------------------------------------------
    # Import
    # ------------------------------------------------------------------

    def import_abuseipdb_csv(
        self,
        csv_content: str,
        min_confidence: int = ABUSEIPDB_MIN_CONFIDENCE,
        ttl_seconds: int = 86400 * 7,   # 7 days by default
    ) -> tuple[int, int]:
        """
        Import IPs from an AbuseIPDB CSV export.

        AbuseIPDB CSV format (header row expected):
            ipAddress,countryCode,abuseConfidenceScore,lastReportedAt,
            totalReports,numDistinctUsers,usageType,isp,domain,isTor,
            isWhitelisted,countryName,abuseCategories

        Returns:
            (imported_count, skipped_count)
        """
        imported = 0
        skipped = 0

        reader = csv.DictReader(io.StringIO(csv_content))
        for row in reader:
            try:
                confidence = int(row.get("abuseConfidenceScore", 0))
                if confidence < min_confidence:
                    skipped += 1
                    continue

                ip = (row.get("ipAddress") or "").strip()
                if not ip:
                    skipped += 1
                    continue

                # Parse abuse categories
                cats_raw = row.get("abuseCategories", "") or ""
                categories: list[int] = []
                for c in cats_raw.split(","):
                    c = c.strip()
                    if c.isdigit():
                        categories.append(int(c))

                self.add(
                    ip=ip,
                    reason=f"AbuseIPDB — {row.get('usageType', 'unknown')} — score {confidence}",
                    source="abuseipdb",
                    ttl_seconds=ttl_seconds,
                    confidence_score=confidence,
                    abuse_categories=categories,
                )
                imported += 1

            except (ValueError, KeyError) as exc:
                logger.warning("abuseipdb_import_row_error", error=str(exc))
                skipped += 1

        logger.info(
            "abuseipdb_import_complete",
            imported=imported,
            skipped=skipped,
            min_confidence=min_confidence,
        )
        return imported, skipped

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def purge_expired(self) -> int:
        """
        Eagerly remove all TTL-expired entries.

        Returns number of entries removed.  Safe to call from a cron job.
        """
        now = time.time()
        # All entries with score in (0.001, now] are TTL-based and expired
        # Permanent entries have score == 0 which is excluded by the lower bound
        expired_ips = self._client.zrangebyscore(
            BLACKLIST_ZSET_KEY, 0.001, now
        )
        if not expired_ips:
            return 0

        pipe = self._client.pipeline(transaction=True)
        for ip in expired_ips:
            pipe.zrem(BLACKLIST_ZSET_KEY, ip)
            pipe.delete(f"{BLACKLIST_META_PREFIX}{ip}")
        pipe.execute()

        logger.info("blacklist_purge_expired", count=len(expired_ips))
        return len(expired_ips)

    def stats(self) -> dict[str, int]:
        """Return operational statistics."""
        raw = self._client.hgetall(BLACKLIST_STATS_KEY) or {}
        total_active = self._client.zcard(BLACKLIST_ZSET_KEY) or 0
        permanent = self._client.zcount(BLACKLIST_ZSET_KEY, 0, 0) or 0
        return {
            "total_active": int(total_active),
            "permanent": int(permanent),
            "ttl_based": int(total_active) - int(permanent),
            "total_added": int(raw.get("total_added", 0)),
            "total_removed": int(raw.get("total_removed", 0)),
        }

    def iter_entries(self) -> Iterator[BlacklistEntry]:
        """Iterate over all active (non-expired) entries."""
        all_ips = self._client.zrange(BLACKLIST_ZSET_KEY, 0, -1, withscores=True)
        now = time.time()
        for ip, score in all_ips:
            if score > 0 and now > score:
                continue  # skip expired
            raw = self._client.get(f"{BLACKLIST_META_PREFIX}{ip}")
            if raw:
                try:
                    yield BlacklistEntry(**json.loads(raw))
                except (json.JSONDecodeError, TypeError):
                    continue

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _purge_expired(self, ip: str) -> None:
        pipe = self._client.pipeline(transaction=True)
        pipe.zrem(BLACKLIST_ZSET_KEY, ip)
        pipe.delete(f"{BLACKLIST_META_PREFIX}{ip}")
        pipe.execute()


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _validate_ip(ip: str) -> str:
    """Validate and normalise an IPv4/IPv6 address. Raises ValueError on invalid input."""
    try:
        return str(ipaddress.ip_address(ip.strip()))
    except ValueError as exc:
        raise ValueError(f"Invalid IP address: {ip!r}") from exc
