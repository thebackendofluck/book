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
Traffic Monitor & DDoS/Campaign Classification API.

Architecture:
  traffic-push.sh -> Redis traffic:status (every 30s)
  This service:
    - Reads /var/log/nginx/access.log for deep analysis
    - Exposes REST API under /api/v2/traffic/
    - Stores rolling 1h history in Redis (traffic:history list)
    - Classifies traffic as NORMAL / ELEVATED / CAMPAIGN / ATTACK

Deploy on: ops-host (10.0.0.11) alongside other FastAPI services.
Included as an APIRouter in /opt/new-platform/app/main.py OR run standalone.

Standalone usage:
    uvicorn traffic_monitor_api:app --host 0.0.0.0 --port 8097
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import time
from collections import Counter, defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import redis as redis_lib
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

NGINX_ACCESS_LOG = Path("/var/log/nginx/access.log")

# Redis: new-casino-redis on host port 6381
REDIS_URL = "redis://127.0.0.1:6381/0"

TRAFFIC_STATUS_KEY = "traffic:status"
TRAFFIC_HISTORY_KEY = "traffic:history"
TRAFFIC_CAMPAIGN_KEY = "traffic:campaign"
TRAFFIC_OVERRIDE_KEY = "traffic:override"

STATUS_TTL = 60          # seconds; push script refreshes every 30s
HISTORY_MAX = 120        # 120 samples × 30s = 1 hour
HISTORY_TTL = 3700       # list TTL slightly over 1 hour

# ip-api.com batch endpoint (free, 15 req/min, 100 IPs/batch)
IP_API_BATCH_URL = "https://ip-api.com/batch"
IP_API_BATCH_SIZE = 100
IP_API_TIMEOUT = 5.0

# Classification thresholds
THRESHOLD_ELEVATED_RPS = 50.0        # req/s
THRESHOLD_ATTACK_RPS = 200.0         # req/s
THRESHOLD_BOT_SCORE_ATTACK = 0.60    # 60% bot traffic
THRESHOLD_UA_DIVERSITY_LOW = 0.10    # UA diversity below 10% is suspicious
THRESHOLD_ERROR_RATE_HIGH = 0.15     # 15% error rate
THRESHOLD_CAMPAIGN_MIN_RPS = 10.0    # min rps to consider a campaign active

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Nginx combined log parser
# ---------------------------------------------------------------------------

# Standard nginx "combined" format:
# $remote_addr - $remote_user [$time_local] "$request" $status $body_bytes_sent "$http_referer" "$http_user_agent"
_LOG_RE = re.compile(
    r'(?P<ip>\S+)\s+-\s+\S+\s+\[(?P<time>[^\]]+)\]\s+'
    r'"(?P<method>\S+)?\s*(?P<path>\S+)?\s*\S*"\s+'
    r'(?P<status>\d{3})\s+(?P<bytes>\d+)\s+'
    r'"[^"]*"\s+"(?P<ua>[^"]*)"'
)

# Known bot/crawler User-Agent fragments (case-insensitive)
_BOT_UA_PATTERNS = re.compile(
    r'(bot|crawl|spider|slurp|mediapartners|facebookexternalhit|whatsapp|'
    r'wget|curl|python-requests|go-http|java|okhttp|axios|libwww|petalbot|'
    r'baiduspider|yandex|duckduckbot|semrush|ahrefsbot|mj12bot|dotbot|'
    r'scrapy|headlesschrome|phantomjs|selenium|puppeteer|playwright)',
    re.IGNORECASE,
)


def parse_log_tail(path: Path, window_seconds: int = 300) -> list[dict]:
    """
    Read the last `window_seconds` of nginx access log entries.

    Returns a list of parsed log dicts. Reads backward from EOF to avoid
    loading multi-GB logs into memory.
    """
    entries: list[dict] = []
    cutoff = time.time() - window_seconds

    try:
        with path.open("rb") as fh:
            # Seek backward in 256 KB chunks to find entries within window
            chunk_size = 256 * 1024
            fh.seek(0, 2)
            file_size = fh.tell()
            pos = file_size
            leftover = b""
            done = False

            while pos > 0 and not done:
                read_size = min(chunk_size, pos)
                pos -= read_size
                fh.seek(pos)
                chunk = fh.read(read_size) + leftover
                lines = chunk.split(b"\n")
                # Keep partial first line as leftover for next iteration
                leftover = lines[0]
                # Process lines in reverse (newest first)
                for raw in reversed(lines[1:]):
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    m = _LOG_RE.match(line)
                    if not m:
                        continue
                    try:
                        ts = _parse_nginx_time(m.group("time"))
                    except ValueError:
                        continue
                    if ts < cutoff:
                        done = True
                        break
                    entries.append(
                        {
                            "ip": m.group("ip"),
                            "ts": ts,
                            "method": m.group("method") or "GET",
                            "path": (m.group("path") or "/").split("?")[0],
                            "status": int(m.group("status")),
                            "bytes": int(m.group("bytes")),
                            "ua": m.group("ua"),
                        }
                    )
            # Handle leftover first line
            if not done and leftover:
                line = leftover.decode("utf-8", errors="replace").strip()
                m = _LOG_RE.match(line)
                if m:
                    try:
                        ts = _parse_nginx_time(m.group("time"))
                        if ts >= cutoff:
                            entries.append(
                                {
                                    "ip": m.group("ip"),
                                    "ts": ts,
                                    "method": m.group("method") or "GET",
                                    "path": (m.group("path") or "/").split("?")[0],
                                    "status": int(m.group("status")),
                                    "bytes": int(m.group("bytes")),
                                    "ua": m.group("ua"),
                                }
                            )
                    except ValueError:
                        pass
    except FileNotFoundError:
        logger.warning("nginx access log not found: %s", path)
    except PermissionError:
        logger.warning("No read permission for nginx access log: %s", path)

    return entries


_NGINX_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def _parse_nginx_time(s: str) -> float:
    """Parse nginx time_local string like '31/Mar/2026:19:54:06 +0000'."""
    # Fast path: avoid datetime parsing overhead
    # Format: DD/Mon/YYYY:HH:MM:SS +ZZZZ
    try:
        day = int(s[0:2])
        mon = _NGINX_MONTHS[s[3:6]]
        year = int(s[7:11])
        hour = int(s[12:14])
        minute = int(s[15:17])
        sec = int(s[18:20])
        tz_sign = 1 if s[21] == "+" else -1
        tz_h = int(s[22:24])
        tz_m = int(s[24:26])
        tz_offset = tz_sign * (tz_h * 3600 + tz_m * 60)
        epoch = (
            datetime(year, mon, day, hour, minute, sec, tzinfo=timezone.utc).timestamp()
            - tz_offset
        )
        return epoch
    except (IndexError, KeyError, ValueError) as exc:
        raise ValueError(f"Cannot parse nginx time: {s!r}") from exc


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------

def compute_metrics(entries: list[dict], window_sec: int = 300) -> dict[str, Any]:
    """Compute traffic metrics from a list of parsed log entries."""
    now = time.time()
    total = len(entries)

    if total == 0:
        return _empty_metrics(now)

    # Time boundaries
    ts_values = [e["ts"] for e in entries]
    actual_span = max(now - min(ts_values), 1.0)

    # Req/s in last 60s
    recent_60 = [e for e in entries if e["ts"] >= now - 60]
    rps_current = len(recent_60) / min(60.0, actual_span)

    # 5m avg
    rps_5m = total / min(300.0, actual_span)

    # 1h: we pass 1h entries separately if needed; use 5m window rps as proxy
    rps_1h = rps_5m  # will be overridden by caller if 1h entries available

    # Unique IPs in last 5 minutes
    ips_5m = {e["ip"] for e in entries if e["ts"] >= now - 300}
    unique_ips_5m = len(ips_5m)

    # Top 10 paths
    path_counts = Counter(e["path"] for e in entries)
    top_paths = [{"path": p, "count": c} for p, c in path_counts.most_common(10)]

    # Status code distribution
    status_counts: dict[str, int] = defaultdict(int)
    for e in entries:
        bucket = f"{e['status'] // 100}xx"
        status_counts[bucket] += 1

    error_count = status_counts.get("4xx", 0) + status_counts.get("5xx", 0)
    error_rate = error_count / total if total > 0 else 0.0

    # User-Agent diversity
    uas = [e["ua"] for e in entries if e["ua"] and e["ua"] != "-"]
    ua_unique = len(set(uas))
    ua_diversity = ua_unique / len(uas) if uas else 1.0
    ua_diversity_score = round(ua_diversity * 100, 1)

    # Bot score
    bot_count = sum(1 for ua in uas if _BOT_UA_PATTERNS.search(ua))
    bot_score = (bot_count / len(uas)) if uas else 0.0

    # Top IPs (5m window)
    ip_counts_5m = Counter(e["ip"] for e in entries if e["ts"] >= now - 300)
    top_ips = [{"ip": ip, "count": c} for ip, c in ip_counts_5m.most_common(5)]

    # Top user agents
    ua_counts = Counter(uas)
    top_uas = [{"ua": ua[:80], "count": c} for ua, c in ua_counts.most_common(5)]

    return {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "window_seconds": window_sec,
        "total_requests": total,
        "rps": {
            "current": round(rps_current, 2),
            "avg_5m": round(rps_5m, 2),
            "avg_1h": round(rps_1h, 2),
        },
        "unique_ips_5m": unique_ips_5m,
        "ua_diversity_score": ua_diversity_score,
        "bot_score": round(bot_score * 100, 1),
        "error_rate": round(error_rate * 100, 2),
        "status_counts": dict(status_counts),
        "top_paths": top_paths,
        "top_ips": top_ips,
        "top_uas": top_uas,
        # geo/asn filled in by async enrichment
        "top_countries": [],
        "top_asns": [],
    }


def _empty_metrics(now: float) -> dict[str, Any]:
    return {
        "computed_at": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
        "window_seconds": 300,
        "total_requests": 0,
        "rps": {"current": 0.0, "avg_5m": 0.0, "avg_1h": 0.0},
        "unique_ips_5m": 0,
        "ua_diversity_score": 100.0,
        "bot_score": 0.0,
        "error_rate": 0.0,
        "status_counts": {},
        "top_paths": [],
        "top_ips": [],
        "top_uas": [],
        "top_countries": [],
        "top_asns": [],
    }


# ---------------------------------------------------------------------------
# Classification engine
# ---------------------------------------------------------------------------

def classify_traffic(
    metrics: dict[str, Any],
    campaign_active: bool,
    override: str | None,
) -> tuple[str, float]:
    """
    Determine traffic classification and confidence score.

    Returns (status, confidence_pct) where status is one of:
        NORMAL | ELEVATED | CAMPAIGN | ATTACK
    """
    if override:
        return override.upper(), 100.0

    rps = metrics["rps"]["current"]
    bot_score = metrics["bot_score"]
    ua_div = metrics["ua_diversity_score"]
    error_rate = metrics["error_rate"]

    signals: list[tuple[str, float, float]] = []  # (classification, weight, score)

    # --- ATTACK signals ---
    if rps >= THRESHOLD_ATTACK_RPS:
        signals.append(("ATTACK", 3.0, min(rps / THRESHOLD_ATTACK_RPS, 2.0)))
    if bot_score >= THRESHOLD_BOT_SCORE_ATTACK * 100:
        signals.append(("ATTACK", 2.0, bot_score / 100))
    if ua_div < 5.0 and rps > 20:
        signals.append(("ATTACK", 2.0, 1.0 - ua_div / 100))

    # --- ELEVATED signals ---
    if rps >= THRESHOLD_ELEVATED_RPS:
        signals.append(("ELEVATED", 1.5, min(rps / THRESHOLD_ELEVATED_RPS, 2.0)))
    if error_rate >= THRESHOLD_ERROR_RATE_HIGH * 100:
        signals.append(("ELEVATED", 1.0, min(error_rate / 20, 1.5)))
    if ua_div < THRESHOLD_UA_DIVERSITY_LOW * 100:
        signals.append(("ELEVATED", 1.0, 1.0 - ua_div / 100))

    # --- CAMPAIGN signals ---
    if campaign_active and rps >= THRESHOLD_CAMPAIGN_MIN_RPS:
        signals.append(("CAMPAIGN", 2.0, 1.0))

    if not signals:
        return "NORMAL", 95.0

    # Weighted vote: tally weight*score per classification
    votes: dict[str, float] = defaultdict(float)
    for cls, weight, score in signals:
        votes[cls] += weight * score

    winner = max(votes, key=lambda k: votes[k])
    total_weight = sum(votes.values())
    confidence = min((votes[winner] / total_weight) * 100, 99.0) if total_weight > 0 else 50.0

    return winner, round(confidence, 1)


# ---------------------------------------------------------------------------
# Geo/ASN enrichment via ip-api.com
# ---------------------------------------------------------------------------

async def enrich_geo_asn(ips: list[str]) -> tuple[list[dict], list[dict]]:
    """
    Batch-query ip-api.com for country and ASN data.

    Returns (top_countries, top_asns) each sorted by count descending.
    Caps at IP_API_BATCH_SIZE IPs. Falls back gracefully on timeout/error.
    """
    if not ips:
        return [], []

    unique_ips = list({ip for ip in ips if _is_routable(ip)})[:IP_API_BATCH_SIZE]
    if not unique_ips:
        return [], []

    payload = [{"query": ip, "fields": "countryCode,country,as,org,status"} for ip in unique_ips]

    try:
        async with httpx.AsyncClient(timeout=IP_API_TIMEOUT) as client:
            resp = await client.post(IP_API_BATCH_URL, json=payload)
            resp.raise_for_status()
            results = resp.json()
    except Exception as exc:
        logger.debug("ip-api.com enrichment failed: %s", exc)
        return [], []

    country_counts: Counter = Counter()
    asn_counts: Counter = Counter()

    for item in results:
        if item.get("status") != "success":
            continue
        cc = item.get("countryCode") or "??"
        country = item.get("country") or "Unknown"
        asn_raw = item.get("as") or ""
        org = item.get("org") or asn_raw

        country_counts[f"{cc}|{country}"] += 1
        if asn_raw:
            asn_num = asn_raw.split(" ")[0] if " " in asn_raw else asn_raw
            asn_counts[f"{asn_num}|{org[:40]}"] += 1

    top_countries = [
        {"code": k.split("|")[0], "name": k.split("|")[1], "count": v}
        for k, v in country_counts.most_common(10)
    ]
    top_asns = [
        {"asn": k.split("|")[0], "org": k.split("|")[1], "count": v}
        for k, v in asn_counts.most_common(10)
    ]
    return top_countries, top_asns


def _is_routable(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
        return not (addr.is_private or addr.is_loopback or addr.is_reserved)
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Redis helpers
# ---------------------------------------------------------------------------

_redis_client: redis_lib.Redis | None = None


def get_redis() -> redis_lib.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis_lib.Redis.from_url(REDIS_URL, decode_responses=True)
    assert _redis_client is not None
    return _redis_client


def redis_get(key: str) -> str | None:
    try:
        result = get_redis().get(key)
        return result  # ty:ignore[invalid-return-type]
    except Exception as exc:
        logger.warning("Redis GET %s failed: %s", key, exc)
        return None


def redis_setex(key: str, ttl: int, value: str) -> None:
    try:
        get_redis().setex(key, ttl, value)
    except Exception as exc:
        logger.warning("Redis SETEX %s failed: %s", key, exc)


def redis_lpush_trim(key: str, value: str, maxlen: int, ttl: int) -> None:
    try:
        r = get_redis()
        pipe = r.pipeline()
        pipe.lpush(key, value)
        pipe.ltrim(key, 0, maxlen - 1)
        pipe.expire(key, ttl)
        pipe.execute()
    except Exception as exc:
        logger.warning("Redis LPUSH %s failed: %s", key, exc)


def redis_lrange(key: str, start: int, stop: int) -> list[str]:
    try:
        return get_redis().lrange(key, start, stop)  # type: ignore[return-value]
    except Exception as exc:
        logger.warning("Redis LRANGE %s failed: %s", key, exc)
        return []


# ---------------------------------------------------------------------------
# Background collector task
# ---------------------------------------------------------------------------

_collector_running = False


async def _run_collector(interval: int = 30) -> None:
    """Collect metrics every `interval` seconds and push to Redis."""
    global _collector_running
    _collector_running = True
    logger.info("Traffic collector started (interval=%ds)", interval)

    while True:
        try:
            await _collect_and_push()
        except Exception:
            logger.exception("Collector cycle failed")
        await asyncio.sleep(interval)


async def _collect_and_push() -> None:
    """Single collection cycle: parse logs, classify, push to Redis."""
    # --- Parse 5m window (for current metrics) ---
    entries_5m = parse_log_tail(NGINX_ACCESS_LOG, window_seconds=300)

    # --- Parse 1h window for rps_1h calculation ---
    entries_1h = parse_log_tail(NGINX_ACCESS_LOG, window_seconds=3600)
    now = time.time()
    rps_1h = len(entries_1h) / min(3600.0, max(now - min((e["ts"] for e in entries_1h), default=now), 1.0))

    metrics = compute_metrics(entries_5m)
    metrics["rps"]["avg_1h"] = round(rps_1h, 2)

    # --- Geo/ASN enrichment (sample up to 100 unique IPs from 5m) ---
    all_ips = [e["ip"] for e in entries_5m]
    unique_ips = list({ip for ip in all_ips})
    top_countries, top_asns = await enrich_geo_asn(unique_ips)
    metrics["top_countries"] = top_countries
    metrics["top_asns"] = top_asns

    # --- Read campaign / override state ---
    campaign_raw = redis_get(TRAFFIC_CAMPAIGN_KEY)
    campaign_data: dict = json.loads(campaign_raw) if campaign_raw else {}
    campaign_active = bool(campaign_data.get("active"))

    override_raw = redis_get(TRAFFIC_OVERRIDE_KEY)
    override: str | None = json.loads(override_raw).get("status") if override_raw else None

    # --- Classify ---
    status, confidence = classify_traffic(metrics, campaign_active, override)

    payload: dict[str, Any] = {
        "status": status,
        "confidence": confidence,
        "metrics": metrics,
        "campaign": campaign_data,
        "override": override,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "collector",
    }

    payload_json = json.dumps(payload, default=str)
    redis_setex(TRAFFIC_STATUS_KEY, STATUS_TTL, payload_json)

    # Push snapshot to history list (for sparklines)
    snapshot = {
        "ts": time.time(),
        "status": status,
        "rps": metrics["rps"]["current"],
        "unique_ips_5m": metrics["unique_ips_5m"],
        "error_rate": metrics["error_rate"],
        "bot_score": metrics["bot_score"],
        "ua_diversity_score": metrics["ua_diversity_score"],
    }
    redis_lpush_trim(TRAFFIC_HISTORY_KEY, json.dumps(snapshot), HISTORY_MAX, HISTORY_TTL)

    logger.info(
        "Traffic: status=%s confidence=%.1f%% rps=%.2f ips=%d",
        status, confidence, metrics["rps"]["current"], metrics["unique_ips_5m"],
    )


# ---------------------------------------------------------------------------
# Pydantic models for request bodies
# ---------------------------------------------------------------------------

class CampaignStartRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120, description="Campaign name / label")
    expected_rps: float | None = Field(None, description="Expected requests/sec during campaign")
    duration_minutes: int | None = Field(None, ge=1, le=1440, description="Estimated duration in minutes")
    notes: str | None = Field(None, max_length=500)


class OverrideRequest(BaseModel):
    status: str = Field(..., description="One of: NORMAL, ELEVATED, CAMPAIGN, ATTACK")
    reason: str = Field(..., min_length=1, max_length=300)
    ttl_minutes: int = Field(default=60, ge=1, le=480)


# ---------------------------------------------------------------------------
# FastAPI router (mounts under /api/v2/traffic/)
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/traffic", tags=["Traffic Intelligence"])


@router.get("/status")
async def traffic_status() -> JSONResponse:
    """
    Current traffic classification and key metrics.

    Returns cached data from Redis (written by background collector, TTL 60s).
    Falls back to a live read from nginx log if Redis key is missing.
    Response time target: <5ms (Redis read only).
    """
    raw = redis_get(TRAFFIC_STATUS_KEY)
    if raw:
        payload = json.loads(raw)
        payload["cache_hit"] = True
        return JSONResponse(content=payload)

    # Cache miss: do a lightweight synchronous read (no geo enrichment)
    entries = parse_log_tail(NGINX_ACCESS_LOG, window_seconds=300)
    metrics = compute_metrics(entries)
    campaign_raw = redis_get(TRAFFIC_CAMPAIGN_KEY)
    campaign_data: dict = json.loads(campaign_raw) if campaign_raw else {}
    override_raw = redis_get(TRAFFIC_OVERRIDE_KEY)
    override: str | None = json.loads(override_raw).get("status") if override_raw else None
    status, confidence = classify_traffic(metrics, bool(campaign_data.get("active")), override)

    return JSONResponse(content={
        "status": status,
        "confidence": confidence,
        "metrics": metrics,
        "campaign": campaign_data,
        "override": override,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "live",
        "cache_hit": False,
    })


@router.get("/metrics")
async def traffic_metrics() -> JSONResponse:
    """
    Full traffic metrics: req/s, IPs, countries, ASNs, UA diversity, bot score, error rate.

    Triggers a fresh log read (no Redis cache) for live accuracy.
    Includes geo/ASN enrichment. Response time may be 1-6s due to ip-api.com call.
    """
    entries_5m = parse_log_tail(NGINX_ACCESS_LOG, window_seconds=300)
    entries_1h = parse_log_tail(NGINX_ACCESS_LOG, window_seconds=3600)
    now = time.time()

    metrics = compute_metrics(entries_5m)
    if entries_1h:
        rps_1h = len(entries_1h) / min(3600.0, max(now - min(e["ts"] for e in entries_1h), 1.0))
        metrics["rps"]["avg_1h"] = round(rps_1h, 2)

    unique_ips = list({e["ip"] for e in entries_5m})
    top_countries, top_asns = await enrich_geo_asn(unique_ips)
    metrics["top_countries"] = top_countries
    metrics["top_asns"] = top_asns

    return JSONResponse(content={
        "metrics": metrics,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    })


@router.get("/history")
def traffic_history() -> JSONResponse:
    """
    Last 1 hour of 30s metric snapshots for sparkline charts.

    Returns list ordered newest-first, max 120 entries.
    """
    raw_list = redis_lrange(TRAFFIC_HISTORY_KEY, 0, HISTORY_MAX - 1)
    history = []
    for raw in raw_list:
        try:
            history.append(json.loads(raw))
        except json.JSONDecodeError:
            continue

    return JSONResponse(content={
        "history": history,
        "count": len(history),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    })


@router.post("/campaign/start")
async def campaign_start(req: CampaignStartRequest) -> JSONResponse:
    """
    Mark a marketing/traffic campaign as active.

    While active, elevated traffic matching campaign patterns is classified
    as CAMPAIGN instead of ELEVATED, suppressing false-positive alerts.
    """
    started_at = datetime.now(timezone.utc).isoformat()
    expires_at: str | None = None
    if req.duration_minutes:
        from datetime import timedelta
        expires_at = (
            datetime.now(timezone.utc) + timedelta(minutes=req.duration_minutes)
        ).isoformat()

    campaign = {
        "active": True,
        "name": req.name,
        "expected_rps": req.expected_rps,
        "duration_minutes": req.duration_minutes,
        "notes": req.notes,
        "started_at": started_at,
        "expires_at": expires_at,
    }
    ttl = (req.duration_minutes * 60 + 300) if req.duration_minutes else 86400
    redis_setex(TRAFFIC_CAMPAIGN_KEY, ttl, json.dumps(campaign))

    logger.info("Campaign started: %s (expires=%s)", req.name, expires_at)
    return JSONResponse(content={"ok": True, "campaign": campaign}, status_code=201)


@router.post("/campaign/stop")
def campaign_stop() -> JSONResponse:
    """Mark the current campaign as ended."""
    raw = redis_get(TRAFFIC_CAMPAIGN_KEY)
    if not raw:
        return JSONResponse(content={"ok": True, "message": "No active campaign"})

    campaign = json.loads(raw)
    campaign["active"] = False
    campaign["ended_at"] = datetime.now(timezone.utc).isoformat()
    redis_setex(TRAFFIC_CAMPAIGN_KEY, 3600, json.dumps(campaign))

    logger.info("Campaign stopped: %s", campaign.get("name"))
    return JSONResponse(content={"ok": True, "campaign": campaign})


@router.post("/override")
def traffic_override(req: OverrideRequest) -> JSONResponse:
    """
    Manually override the traffic classification for up to `ttl_minutes`.

    Useful for incident response: immediately flag as ATTACK to trigger
    downstream alerting, or set NORMAL to suppress alerts during maintenance.
    """
    allowed = {"NORMAL", "ELEVATED", "CAMPAIGN", "ATTACK"}
    status = req.status.upper()
    if status not in allowed:
        raise HTTPException(status_code=422, detail=f"status must be one of {sorted(allowed)}")

    ttl = req.ttl_minutes * 60
    override_data = {
        "status": status,
        "reason": req.reason,
        "set_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": datetime.fromtimestamp(
            time.time() + ttl, tz=timezone.utc
        ).isoformat(),
    }
    redis_setex(TRAFFIC_OVERRIDE_KEY, ttl + 60, json.dumps(override_data))

    logger.info("Traffic override set: %s for %dm (%s)", status, req.ttl_minutes, req.reason)
    return JSONResponse(content={"ok": True, "override": override_data}, status_code=201)


@router.delete("/override")
def traffic_override_clear() -> JSONResponse:
    """Remove any active manual override and return to automatic classification."""
    try:
        get_redis().delete(TRAFFIC_OVERRIDE_KEY)
    except Exception as exc:
        logger.warning("Failed to delete override key: %s", exc)
    return JSONResponse(content={"ok": True, "message": "Override cleared"})


# ---------------------------------------------------------------------------
# Standalone FastAPI app (when not mounted into main.py)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _lifespan(application: FastAPI):
    task = asyncio.create_task(_run_collector(interval=30))
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="Traffic Monitor API",
    description="DDoS / Campaign traffic classification for AcmeToCasino.",
    version="1.0.0",
    lifespan=_lifespan,
)

app.add_middleware(
    CORSMiddleware,  # ty:ignore[invalid-argument-type]
    allow_origins=["https://new.acmetocasino.com", "https://thebackendofluck.com"],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

# Mount under /api/v2 to match the existing dashboard convention
app.include_router(router, prefix="/api/v2")


# ---------------------------------------------------------------------------
# Integration note for /opt/new-platform/app/main.py
# ---------------------------------------------------------------------------
# To integrate into the existing monolith instead of running standalone:
#
#   from traffic_monitor_api import router as traffic_router
#   app.include_router(traffic_router, prefix="/api/v2")
#
# And start the background collector in the lifespan:
#
#   asyncio.create_task(_run_collector(interval=30))
#
# Ensure /var/log/nginx/access.log is accessible by the app process
# (add the api user to the adm group, or adjust log permissions in nginx).
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run("traffic_monitor_api:app", host="0.0.0.0", port=8097, reload=False)
