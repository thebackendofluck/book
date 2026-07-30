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
sync_manager.py — Central synchronisation service for IP threat intelligence.

Exposes a FastAPI application that:
  - Pushes blocked IPs to all three platforms simultaneously (Redis, DynamoDB,
    Cloudflare KV, AWS WAF)
  - Imports from the consolidate-lists.py output (307K+ IPs)
  - Runs per-platform health checks and reports sync state
  - Performs full reconciliation on demand

Environment variables (no hardcoded secrets):
  REDIS_URL               — Redis connection string
  AWS_REGION              — AWS region (default: us-east-1)
  DYNAMODB_TABLE          — DynamoDB table name (default: ip-blacklist)
  WAF_IP_SET_ID           — WAF IP set ID
  WAF_IP_SET_NAME         — WAF IP set name
  WAF_SCOPE               — REGIONAL | CLOUDFRONT (default: REGIONAL)
  CF_ACCOUNT_ID           — Cloudflare account ID
  CF_API_TOKEN            — Cloudflare API token
  CF_KV_NAMESPACE_ID      — Cloudflare KV namespace ID for IP_BLACKLIST
  THREAT_LIST_OUTPUT_DIR  — Path to consolidate-lists.py output directory
  CONSOLIDATE_SCRIPT_PATH — Absolute path to consolidate-lists.py
  SYNC_DEFAULT_TTL_HOURS  — Default TTL for threat-list imports (default: 168 = 7 days)
  SYNC_BATCH_SIZE         — Batch size for parallel platform writes (default: 500)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from platform_adapters import (  # ty:ignore[unresolved-import]
    AWSWAFAdapter,
    BlockedIP,
    CloudflareKVAdapter,
    DynamoDBAdapter,
    HealthStatus,
    PlatformAdapter,
    RedisAdapter,
)

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------

REDIS_URL              = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
AWS_REGION             = os.environ.get("AWS_REGION", "us-east-1")
DYNAMODB_TABLE         = os.environ.get("DYNAMODB_TABLE", "ip-blacklist")
WAF_IP_SET_ID          = os.environ.get("WAF_IP_SET_ID", "")
WAF_IP_SET_NAME        = os.environ.get("WAF_IP_SET_NAME", "igaming-blocked-ips")
WAF_SCOPE              = os.environ.get("WAF_SCOPE", "REGIONAL")
CF_ACCOUNT_ID          = os.environ.get("CF_ACCOUNT_ID", "")
CF_API_TOKEN           = os.environ.get("CF_API_TOKEN", "")
CF_KV_NAMESPACE_ID     = os.environ.get("CF_KV_NAMESPACE_ID", "")
THREAT_LIST_OUTPUT_DIR = os.environ.get(
    "THREAT_LIST_OUTPUT_DIR",
    str(Path(__file__).parent.parent / "threat-lists" / "output"),
)
CONSOLIDATE_SCRIPT_PATH = os.environ.get(
    "CONSOLIDATE_SCRIPT_PATH",
    str(Path(__file__).parent.parent / "threat-lists" / "consolidate-lists.py"),
)
SYNC_DEFAULT_TTL_HOURS  = int(os.environ.get("SYNC_DEFAULT_TTL_HOURS", "168"))
SYNC_BATCH_SIZE         = int(os.environ.get("SYNC_BATCH_SIZE", "500"))

# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class BlockRequest(BaseModel):
    ip: str
    reason: str
    ttl_seconds: int = Field(default=0, ge=0, description="0 = permanent")
    severity: str = Field(default="MEDIUM", pattern="^(LOW|MEDIUM|HIGH|PERMANENT)$")
    source: str = Field(default="api")
    platforms: list[str] = Field(
        default=["redis", "dynamodb", "cloudflare_kv", "aws_waf"],
        description="Which platforms to target. Omit to target all.",
    )


class UnblockRequest(BaseModel):
    ip: str
    platforms: list[str] = Field(default=["redis", "dynamodb", "cloudflare_kv", "aws_waf"])


class ImportThreatsRequest(BaseModel):
    run_consolidator: bool = Field(
        default=False,
        description="If True, re-run consolidate-lists.py before importing.",
    )
    categories: list[str] = Field(
        default=["tor", "vpn", "proxy", "bot", "abuse"],
        description="Which category files to import (datacenter is large; import separately).",
    )
    ttl_hours: int = Field(
        default=SYNC_DEFAULT_TTL_HOURS,
        ge=1,
        description="TTL in hours applied to all imported IPs.",
    )
    platforms: list[str] = Field(default=["redis", "dynamodb", "cloudflare_kv", "aws_waf"])


# ---------------------------------------------------------------------------
# Per-platform sync result
# ---------------------------------------------------------------------------

@dataclass
class PlatformSyncResult:
    platform: str
    success: bool
    count: int = 0
    error: Optional[str] = None
    latency_ms: float = 0.0


@dataclass
class SyncResult:
    ip: Optional[str]
    operation: str
    platforms: list[PlatformSyncResult] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    def any_success(self) -> bool:
        return any(r.success for r in self.platforms)

    def all_success(self) -> bool:
        return all(r.success for r in self.platforms)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ip": self.ip,
            "operation": self.operation,
            "timestamp": self.timestamp,
            "all_success": self.all_success(),
            "platforms": [asdict(p) for p in self.platforms],
        }


# ---------------------------------------------------------------------------
# Adapter registry
# ---------------------------------------------------------------------------

class AdapterRegistry:
    """Lazy-initialised registry of platform adapters."""

    def __init__(self) -> None:
        self._adapters: dict[str, PlatformAdapter] = {}
        self._initialized = False

    def _init_if_needed(self) -> None:
        if self._initialized:
            return
        self._adapters["redis"] = RedisAdapter(REDIS_URL)
        self._adapters["dynamodb"] = DynamoDBAdapter(DYNAMODB_TABLE, AWS_REGION)
        if CF_ACCOUNT_ID and CF_API_TOKEN and CF_KV_NAMESPACE_ID:
            self._adapters["cloudflare_kv"] = CloudflareKVAdapter(
                account_id=CF_ACCOUNT_ID,
                api_token=CF_API_TOKEN,
                namespace_id=CF_KV_NAMESPACE_ID,
            )
        else:
            logger.warning(
                "Cloudflare KV adapter disabled: CF_ACCOUNT_ID, CF_API_TOKEN, "
                "or CF_KV_NAMESPACE_ID not set."
            )
        if WAF_IP_SET_ID:
            self._adapters["aws_waf"] = AWSWAFAdapter(
                ip_set_id=WAF_IP_SET_ID,
                ip_set_name=WAF_IP_SET_NAME,
                scope=WAF_SCOPE,
                region=AWS_REGION,
            )
        else:
            logger.warning("AWS WAF adapter disabled: WAF_IP_SET_ID not set.")
        self._initialized = True

    def get(self, name: str) -> Optional[PlatformAdapter]:
        self._init_if_needed()
        return self._adapters.get(name)

    def all(self) -> dict[str, PlatformAdapter]:
        self._init_if_needed()
        return dict(self._adapters)

    def for_platforms(self, names: list[str]) -> dict[str, PlatformAdapter]:
        self._init_if_needed()
        return {n: a for n, a in self._adapters.items() if n in names}


_registry = AdapterRegistry()

# ---------------------------------------------------------------------------
# Core sync logic
# ---------------------------------------------------------------------------

async def _sync_block_to_platform(
    adapter: PlatformAdapter,
    ip: str,
    reason: str,
    ttl_seconds: int,
    severity: str,
    source: str,
) -> PlatformSyncResult:
    t0 = time.perf_counter()
    try:
        loop = asyncio.get_event_loop()
        is_new = await loop.run_in_executor(
            None,
            lambda: adapter.block_ip(
                ip=ip,
                reason=reason,
                ttl_seconds=ttl_seconds,
                severity=severity,
                source=source,
            ),
        )
        latency_ms = (time.perf_counter() - t0) * 1000
        return PlatformSyncResult(
            platform=adapter.platform_name,
            success=True,
            count=1,
            latency_ms=round(latency_ms, 2),
        )
    except Exception as exc:  # noqa: BLE001
        latency_ms = (time.perf_counter() - t0) * 1000
        logger.error(
            "sync.block_ip platform=%s ip=%s error=%s",
            adapter.platform_name, ip, exc,
        )
        return PlatformSyncResult(
            platform=adapter.platform_name,
            success=False,
            error=str(exc),
            latency_ms=round(latency_ms, 2),
        )


async def _sync_unblock_to_platform(
    adapter: PlatformAdapter,
    ip: str,
) -> PlatformSyncResult:
    t0 = time.perf_counter()
    try:
        loop = asyncio.get_event_loop()
        removed = await loop.run_in_executor(None, lambda: adapter.unblock_ip(ip))
        latency_ms = (time.perf_counter() - t0) * 1000
        return PlatformSyncResult(
            platform=adapter.platform_name,
            success=True,
            count=1 if removed else 0,
            latency_ms=round(latency_ms, 2),
        )
    except Exception as exc:  # noqa: BLE001
        latency_ms = (time.perf_counter() - t0) * 1000
        logger.error(
            "sync.unblock_ip platform=%s ip=%s error=%s",
            adapter.platform_name, ip, exc,
        )
        return PlatformSyncResult(
            platform=adapter.platform_name,
            success=False,
            error=str(exc),
            latency_ms=round(latency_ms, 2),
        )


# ---------------------------------------------------------------------------
# Threat list import
# ---------------------------------------------------------------------------

# Category file mapping (output of consolidate-lists.py)
CATEGORY_FILE_MAP: dict[str, str] = {
    "tor":        "tor-exits.txt",
    "vpn":        "vpn-ips.txt",
    "proxy":      "proxy-ips.txt",
    "datacenter": "datacenter-ranges.txt",
    "bot":        "bot-ips.txt",
    "abuse":      "abuse-ips.txt",
}

CATEGORY_SEVERITY: dict[str, str] = {
    "tor":        "HIGH",
    "vpn":        "HIGH",
    "proxy":      "MEDIUM",
    "datacenter": "LOW",
    "bot":        "HIGH",
    "abuse":      "MEDIUM",
}


def _run_consolidator(script_path: str, output_dir: str) -> dict[str, Any]:
    """
    Run consolidate-lists.py as a subprocess and return the parsed run-stats.json.
    Raises RuntimeError on non-zero exit.
    """
    result = subprocess.run(
        [
            "python3", script_path,
            "--output-dir", output_dir,
            "--cache-dir", str(Path(script_path).parent / "cache"),
        ],
        capture_output=True,
        text=True,
        timeout=600,  # 10-minute max for network downloads
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"consolidate-lists.py exited {result.returncode}: {result.stderr[:500]}"
        )

    stats_path = Path(output_dir) / "run-stats.json"
    if stats_path.exists():
        return json.loads(stats_path.read_text())
    return {}


def _load_category_ips(
    output_dir: str,
    categories: list[str],
    ttl_hours: int,
) -> list[tuple[str, str, int]]:
    """
    Read category text files and return a list of (ip, reason, ttl_seconds).
    Lines starting with '#' or blank lines are ignored.
    """
    ttl_seconds = ttl_hours * 3600
    entries: list[tuple[str, str, int]] = []
    seen: set[str] = set()

    for category in categories:
        filename = CATEGORY_FILE_MAP.get(category)
        if not filename:
            logger.warning("import_threats: unknown category %s, skipping", category)
            continue
        file_path = Path(output_dir) / filename
        if not file_path.exists():
            logger.warning("import_threats: file not found %s, skipping", file_path)
            continue

        severity = CATEGORY_SEVERITY.get(category, "MEDIUM")
        reason = f"THREAT_LIST_{category.upper()}"
        count = 0

        with file_path.open() as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # Strip port if present (proxy lists sometimes include port)
                ip_part = line.split()[0].split(":")[0]
                if ip_part in seen:
                    continue
                seen.add(ip_part)
                entries.append((ip_part, reason, ttl_seconds))
                count += 1

        logger.info("import_threats: category=%s loaded=%d from %s", category, count, filename)

    return entries


async def _bulk_push_to_platform(
    adapter: PlatformAdapter,
    entries: list[tuple[str, str, int]],
    source: str,
) -> PlatformSyncResult:
    """
    Push a bulk list to a single platform using the most efficient path available.
    Falls back to per-IP block_ip if the adapter lacks a bulk method.
    """
    t0 = time.perf_counter()
    loop = asyncio.get_event_loop()

    try:
        if isinstance(adapter, RedisAdapter):
            count = await loop.run_in_executor(
                None, lambda: adapter.bulk_block(entries, source=source)
            )
        elif isinstance(adapter, DynamoDBAdapter):
            count = await loop.run_in_executor(
                None, lambda: adapter.batch_block(entries, source=source)
            )
        elif isinstance(adapter, CloudflareKVAdapter):
            count = await loop.run_in_executor(
                None, lambda: adapter.bulk_block(entries, source=source)
            )
        elif isinstance(adapter, AWSWAFAdapter):
            # WAF only takes the IP list (no reason/TTL per entry)
            ips = [e[0] for e in entries]
            result = await loop.run_in_executor(None, lambda: adapter.batch_block(ips))
            count = result.get("added", 0)
        else:
            # Generic fallback
            count = 0
            for ip_raw, reason, ttl_seconds in entries:
                try:
                    await loop.run_in_executor(
                        None,
                        lambda: adapter.block_ip(
                            ip=ip_raw, reason=reason, ttl_seconds=ttl_seconds
                        ),
                    )
                    count += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "bulk_push fallback: platform=%s ip=%s error=%s",
                        adapter.platform_name, ip_raw, exc,
                    )

        latency_ms = (time.perf_counter() - t0) * 1000
        return PlatformSyncResult(
            platform=adapter.platform_name,
            success=True,
            count=count,
            latency_ms=round(latency_ms, 2),
        )

    except Exception as exc:  # noqa: BLE001
        latency_ms = (time.perf_counter() - t0) * 1000
        logger.error("bulk_push platform=%s error=%s", adapter.platform_name, exc)
        return PlatformSyncResult(
            platform=adapter.platform_name,
            success=False,
            error=str(exc),
            latency_ms=round(latency_ms, 2),
        )


# ---------------------------------------------------------------------------
# Full reconciliation
# ---------------------------------------------------------------------------

async def _full_reconcile(
    adapters: dict[str, PlatformAdapter],
) -> dict[str, Any]:
    """
    Read the canonical list from Redis (on-premises, considered authoritative)
    and push any IPs missing on the other two platforms.

    Strategy:
      1. Fetch the full blocked list from Redis.
      2. Fetch the full blocked list from DynamoDB.
      3. Fetch the full blocked list from CF KV (expensive for large sets).
      4. Compute the delta (in Redis but not in target platform).
      5. Push the delta to each lagging platform.

    Returns a dict with per-platform reconciliation counts.
    """
    result: dict[str, Any] = {"started_at": time.time(), "platforms": {}}
    loop = asyncio.get_event_loop()

    # Step 1: fetch canonical set from Redis
    redis_adapter = adapters.get("redis")
    if not redis_adapter:
        return {"error": "Redis adapter not configured — cannot reconcile"}

    try:
        canonical = await loop.run_in_executor(None, redis_adapter.list_blocked)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Failed to fetch canonical Redis list: {exc}"}

    canonical_map: dict[str, BlockedIP] = {e.ip: e for e in canonical}
    logger.info("reconcile: canonical set size=%d", len(canonical_map))

    # Step 2: reconcile each other platform
    for name, adapter in adapters.items():
        if name == "redis":
            continue

        t0 = time.perf_counter()
        try:
            current = await loop.run_in_executor(None, adapter.list_blocked)
            current_ips = {e.ip for e in current}
            # Strip CIDR suffix for WAF comparisons
            current_ips_normalized = set()
            for ip in current_ips:
                current_ips_normalized.add(ip.split("/")[0])  # "1.2.3.4/32" -> "1.2.3.4"

            missing = [
                e for ip, e in canonical_map.items()
                if ip not in current_ips_normalized
            ]

            if missing:
                to_push = [
                    (e.ip, e.reason, int(e.expires_at - time.time()) if e.expires_at > 0 else 0)
                    for e in missing
                ]
                push_result = await _bulk_push_to_platform(adapter, to_push, source="reconcile")
            else:
                push_result = PlatformSyncResult(
                    platform=name, success=True, count=0, latency_ms=0.0
                )

            latency_ms = (time.perf_counter() - t0) * 1000
            result["platforms"][name] = {
                "canonical_count": len(canonical_map),
                "platform_count": len(current),
                "missing_count": len(missing) if missing else 0,
                "pushed_count": push_result.count,
                "success": push_result.success,
                "error": push_result.error,
                "latency_ms": round(latency_ms, 2),
            }

        except Exception as exc:  # noqa: BLE001
            latency_ms = (time.perf_counter() - t0) * 1000
            logger.error("reconcile platform=%s error=%s", name, exc)
            result["platforms"][name] = {
                "success": False,
                "error": str(exc),
                "latency_ms": round(latency_ms, 2),
            }

    result["completed_at"] = time.time()
    result["elapsed_seconds"] = round(result["completed_at"] - result["started_at"], 2)
    return result


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="IP Sync Manager",
    description=(
        "Central synchronisation service for IP threat intelligence across "
        "on-premises Redis, AWS DynamoDB, Cloudflare KV, and AWS WAF."
    ),
    version="1.0.0",
)


@app.post("/sync/block", summary="Block an IP across all (or selected) platforms")
async def sync_block(req: BlockRequest) -> JSONResponse:
    """
    Block an IP across all configured platforms simultaneously.
    All platform calls are issued in parallel; partial failures are reported
    in the response but do not cause an HTTP error status.
    """
    adapters = _registry.for_platforms(req.platforms)
    if not adapters:
        raise HTTPException(
            status_code=400,
            detail=f"No configured adapters match platforms: {req.platforms}",
        )

    tasks = [
        _sync_block_to_platform(
            adapter=adapter,
            ip=req.ip,
            reason=req.reason,
            ttl_seconds=req.ttl_seconds,
            severity=req.severity,
            source=req.source,
        )
        for adapter in adapters.values()
    ]
    platform_results = await asyncio.gather(*tasks)
    sync = SyncResult(ip=req.ip, operation="block", platforms=list(platform_results))

    logger.info(
        "sync.block ip=%s all_success=%s platforms=%s",
        req.ip, sync.all_success(), [p.platform for p in platform_results],
    )
    return JSONResponse(content=sync.to_dict())


@app.post("/sync/unblock", summary="Remove an IP from all (or selected) platforms")
async def sync_unblock(req: UnblockRequest) -> JSONResponse:
    adapters = _registry.for_platforms(req.platforms)
    if not adapters:
        raise HTTPException(status_code=400, detail="No configured adapters match requested platforms")

    tasks = [_sync_unblock_to_platform(adapter, req.ip) for adapter in adapters.values()]
    platform_results = await asyncio.gather(*tasks)
    sync = SyncResult(ip=req.ip, operation="unblock", platforms=list(platform_results))

    logger.info("sync.unblock ip=%s all_success=%s", req.ip, sync.all_success())
    return JSONResponse(content=sync.to_dict())


@app.post("/sync/import-threats", summary="Import from consolidate-lists.py output")
async def import_threats(req: ImportThreatsRequest, background_tasks: BackgroundTasks) -> JSONResponse:
    """
    Load threat intelligence from the consolidate-lists.py output directory and
    push to all requested platforms.

    If run_consolidator=True, re-runs consolidate-lists.py first (takes ~10–60 s
    depending on cache state and network).  The actual push is performed
    synchronously (may take several minutes for 300K+ IPs).
    """
    consolidator_stats: dict[str, Any] = {}
    if req.run_consolidator:
        try:
            consolidator_stats = _run_consolidator(CONSOLIDATE_SCRIPT_PATH, THREAT_LIST_OUTPUT_DIR)
            logger.info("import_threats: consolidator finished total=%s",
                        consolidator_stats.get("total_unique_entries"))
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    # Load IPs from category files
    entries = _load_category_ips(THREAT_LIST_OUTPUT_DIR, req.categories, req.ttl_hours)
    if not entries:
        return JSONResponse(content={
            "operation": "import_threats",
            "warning": "No IP entries loaded — check category files exist in THREAT_LIST_OUTPUT_DIR",
            "consolidator_stats": consolidator_stats,
        })

    logger.info("import_threats: total entries to push=%d", len(entries))

    adapters = _registry.for_platforms(req.platforms)
    tasks = [
        _bulk_push_to_platform(adapter, entries, source="import_threats")
        for adapter in adapters.values()
    ]
    platform_results = await asyncio.gather(*tasks)

    return JSONResponse(content={
        "operation": "import_threats",
        "entries_loaded": len(entries),
        "categories": req.categories,
        "ttl_hours": req.ttl_hours,
        "consolidator_stats": consolidator_stats,
        "platforms": [asdict(r) for r in platform_results],
    })


@app.get("/sync/status", summary="Health and sync state for each platform")
async def sync_status() -> JSONResponse:
    """
    Run health checks against all configured platforms in parallel.
    Returns connectivity status, latency, and blocked IP counts.
    """
    adapters = _registry.all()
    if not adapters:
        return JSONResponse(content={"error": "No adapters configured"})

    loop = asyncio.get_event_loop()
    tasks = {
        name: loop.run_in_executor(None, adapter.health_check)
        for name, adapter in adapters.items()
    }
    statuses: dict[str, HealthStatus] = {}
    for name, coro in tasks.items():
        try:
            statuses[name] = await coro
        except Exception as exc:  # noqa: BLE001
            statuses[name] = HealthStatus(
                platform=name, healthy=False, latency_ms=0.0, error=str(exc)
            )

    return JSONResponse(content={
        "timestamp": time.time(),
        "platforms": {
            name: asdict(status) for name, status in statuses.items()
        },
        "all_healthy": all(s.healthy for s in statuses.values()),
    })


@app.post("/sync/full-sync", summary="Force full reconciliation across all platforms")
async def full_sync() -> JSONResponse:
    """
    Fetch the canonical IP list from Redis and push any missing IPs to
    DynamoDB, Cloudflare KV, and AWS WAF.

    This is the recovery operation: run it when a platform has diverged
    (e.g., after a WAF IP set rebuild or KV namespace was re-created).
    """
    adapters = _registry.all()
    result = await _full_reconcile(adapters)
    return JSONResponse(content=result)


@app.get("/health", summary="Service liveness probe")
async def health() -> JSONResponse:
    return JSONResponse(content={"status": "ok", "timestamp": time.time()})


# ---------------------------------------------------------------------------
# Entry point for direct execution / debugging
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "sync_manager:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
        log_level="info",
        reload=False,
    )
