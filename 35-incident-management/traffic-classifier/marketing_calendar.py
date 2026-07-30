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
Marketing Calendar - Redis-backed campaign schedule management.

Provides CRUD operations for marketing campaigns and exposes a FastAPI
sub-application for integration into the dashboard.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import redis.asyncio as aioredis
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger("marketing_calendar")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------
class CampaignType(str, Enum):
    PAID_SOCIAL = "PAID_SOCIAL"
    PAID_SEARCH = "PAID_SEARCH"
    EMAIL = "EMAIL"
    AFFILIATE = "AFFILIATE"
    INFLUENCER = "INFLUENCER"
    TV_RADIO = "TV_RADIO"
    EVENT = "EVENT"          # World Cup, Olympics, etc.
    SEASONAL = "SEASONAL"    # Christmas, Carnival
    OTHER = "OTHER"


class CampaignStatus(str, Enum):
    SCHEDULED = "SCHEDULED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


# ---------------------------------------------------------------------------
# API schemas
# ---------------------------------------------------------------------------
class CampaignCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    campaign_type: CampaignType
    start_time: float = Field(..., description="Unix timestamp")
    end_time: float = Field(..., description="Unix timestamp")
    expected_traffic_multiplier: float = Field(
        1.0,
        ge=1.0,
        le=100.0,
        description="Expected traffic increase factor (e.g. 5 = 5x normal)",
    )
    target_geos: list[str] = Field(
        default_factory=list,
        description="ISO-3166-1 alpha-2 country codes targeted by this campaign",
    )
    landing_pages: list[str] = Field(
        default_factory=list,
        description="URL paths that will receive increased traffic",
    )
    notes: str = ""

    @field_validator("end_time")
    @classmethod
    def end_after_start(cls, v: float, info: Any) -> float:
        start = (info.data or {}).get("start_time", 0)
        if v <= start:
            raise ValueError("end_time must be after start_time")
        return v


class CampaignUpdate(BaseModel):
    name: str | None = None
    expected_traffic_multiplier: float | None = None
    end_time: float | None = None
    target_geos: list[str] | None = None
    landing_pages: list[str] | None = None
    notes: str | None = None
    status: CampaignStatus | None = None


class CampaignResponse(BaseModel):
    id: str
    name: str
    campaign_type: str
    status: str
    start_time: float
    end_time: float
    expected_traffic_multiplier: float
    target_geos: list[str]
    landing_pages: list[str]
    notes: str
    created_at: float
    updated_at: float


class ActiveCampaignSummary(BaseModel):
    """Lightweight structure consumed by the dashboard widget."""
    id: str
    name: str
    campaign_type: str
    expected_multiplier: float
    target_geos: list[str]
    ends_in_seconds: float
    landing_pages: list[str]


# ---------------------------------------------------------------------------
# Redis key helpers
# ---------------------------------------------------------------------------
def _campaign_key(campaign_id: str) -> str:
    return f"campaign:{campaign_id}"


def _active_set() -> str:
    return "campaigns:active"


def _all_set() -> str:
    return "campaigns:all"


# ---------------------------------------------------------------------------
# Calendar service
# ---------------------------------------------------------------------------
class MarketingCalendar:
    """
    CRUD layer for marketing campaigns backed by Redis.

    Campaigns are stored as JSON strings at `campaign:<id>`.
    Active campaign IDs are maintained in a Redis set `campaigns:active`.
    All campaign IDs (past + future) live in `campaigns:all`.

    Background task `cleanup_expired` sweeps for finished campaigns and
    removes them from the active set.
    """

    def __init__(self, redis_url: str = REDIS_URL) -> None:
        self._redis_url = redis_url
        self._redis: aioredis.Redis | None = None

    async def connect(self) -> None:
        self._redis = aioredis.from_url(
            self._redis_url, encoding="utf-8", decode_responses=True
        )
        logger.info("MarketingCalendar connected to Redis.")

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()

    def _assert_connected(self) -> aioredis.Redis:
        if not self._redis:
            raise RuntimeError("Redis not connected. Call connect() first.")
        return self._redis

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------
    async def create_campaign(self, data: CampaignCreate) -> CampaignResponse:
        r = self._assert_connected()
        now = time.time()
        campaign_id = str(uuid.uuid4())
        payload = {
            "id": campaign_id,
            "name": data.name,
            "campaign_type": data.campaign_type.value,
            "status": CampaignStatus.SCHEDULED.value,
            "start_time": data.start_time,
            "end_time": data.end_time,
            "expected_traffic_multiplier": data.expected_traffic_multiplier,
            "target_geos": [g.upper() for g in data.target_geos],
            "landing_pages": data.landing_pages,
            "notes": data.notes,
            "created_at": now,
            "updated_at": now,
        }

        # Compute TTL so Redis auto-expires old campaigns (end_time + 7 days)
        ttl = max(1, int(data.end_time - now) + 604800)
        pipe = r.pipeline()
        pipe.setex(_campaign_key(campaign_id), ttl, json.dumps(payload))
        pipe.sadd(_all_set(), campaign_id)

        # If campaign is already active, add to active set
        if data.start_time <= now <= data.end_time:
            payload["status"] = CampaignStatus.ACTIVE.value
            pipe.sadd(_active_set(), campaign_id)

        await pipe.execute()
        logger.info("Campaign created: %s (%s).", data.name, campaign_id)
        return CampaignResponse(**payload)  # ty:ignore[invalid-argument-type]

    async def update_campaign(
        self, campaign_id: str, update: CampaignUpdate
    ) -> CampaignResponse:
        r = self._assert_connected()
        raw = await r.get(_campaign_key(campaign_id))
        if not raw:
            raise KeyError(f"Campaign {campaign_id} not found.")
        payload = json.loads(raw)

        for attr, value in update.model_dump(exclude_none=True).items():
            if attr == "target_geos" and isinstance(value, list):
                payload[attr] = [g.upper() for g in value]
            elif attr == "status":
                payload[attr] = value if isinstance(value, str) else value.value
            else:
                payload[attr] = value

        payload["updated_at"] = time.time()
        now = time.time()
        ttl = max(1, int(payload["end_time"] - now) + 604800)
        pipe = r.pipeline()
        pipe.setex(_campaign_key(campaign_id), ttl, json.dumps(payload))

        # Sync active set
        status = payload["status"]
        if status == CampaignStatus.ACTIVE.value:
            pipe.sadd(_active_set(), campaign_id)
        elif status in (CampaignStatus.COMPLETED.value, CampaignStatus.CANCELLED.value):
            pipe.srem(_active_set(), campaign_id)

        await pipe.execute()
        logger.info("Campaign updated: %s.", campaign_id)
        return CampaignResponse(**payload)  # ty:ignore[invalid-argument-type]

    async def delete_campaign(self, campaign_id: str) -> None:
        r = self._assert_connected()
        pipe = r.pipeline()
        pipe.delete(_campaign_key(campaign_id))
        pipe.srem(_active_set(), campaign_id)
        pipe.srem(_all_set(), campaign_id)
        await pipe.execute()
        logger.info("Campaign deleted: %s.", campaign_id)

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------
    async def get_campaign(self, campaign_id: str) -> CampaignResponse:
        r = self._assert_connected()
        raw = await r.get(_campaign_key(campaign_id))
        if not raw:
            raise KeyError(f"Campaign {campaign_id} not found.")
        return CampaignResponse(**json.loads(raw))

    async def list_campaigns(
        self,
        status_filter: CampaignStatus | None = None,
        geo_filter: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[CampaignResponse]:
        r = self._assert_connected()
        all_ids = await r.smembers(_all_set())  # ty:ignore[invalid-await]
        results: list[CampaignResponse] = []
        for campaign_id in all_ids:
            raw = await r.get(_campaign_key(campaign_id))
            if not raw:
                continue
            try:
                c = CampaignResponse(**json.loads(raw))
            except Exception:
                continue

            if status_filter and c.status != status_filter.value:
                continue
            if geo_filter and geo_filter.upper() not in [g.upper() for g in c.target_geos]:
                continue
            results.append(c)

        # Sort by start_time descending
        results.sort(key=lambda x: x.start_time, reverse=True)
        return results[offset: offset + limit]

    async def get_active_campaigns(self) -> list[CampaignResponse]:
        r = self._assert_connected()
        now = time.time()
        active_ids = await r.smembers(_active_set())  # ty:ignore[invalid-await]
        results: list[CampaignResponse] = []
        for campaign_id in active_ids:
            raw = await r.get(_campaign_key(campaign_id))
            if not raw:
                continue
            try:
                c = CampaignResponse(**json.loads(raw))
            except Exception:
                continue
            # Double-check time range
            if c.start_time <= now <= c.end_time:
                results.append(c)
        return results

    async def get_active_summary(self) -> list[ActiveCampaignSummary]:
        now = time.time()
        campaigns = await self.get_active_campaigns()
        return [
            ActiveCampaignSummary(
                id=c.id,
                name=c.name,
                campaign_type=c.campaign_type,
                expected_multiplier=c.expected_traffic_multiplier,
                target_geos=c.target_geos,
                ends_in_seconds=max(0.0, c.end_time - now),
                landing_pages=c.landing_pages,
            )
            for c in campaigns
        ]

    async def get_upcoming_campaigns(self, within_hours: float = 48.0) -> list[CampaignResponse]:
        """Return campaigns that start within the next `within_hours` hours."""
        r = self._assert_connected()
        now = time.time()
        cutoff = now + within_hours * 3600
        all_ids = await r.smembers(_all_set())  # ty:ignore[invalid-await]
        results: list[CampaignResponse] = []
        for campaign_id in all_ids:
            raw = await r.get(_campaign_key(campaign_id))
            if not raw:
                continue
            try:
                c = CampaignResponse(**json.loads(raw))
            except Exception:
                continue
            if now < c.start_time <= cutoff:
                results.append(c)
        results.sort(key=lambda x: x.start_time)
        return results

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------
    async def cleanup_expired(self) -> int:
        """
        Move expired campaigns out of the active set.
        Returns the number of campaigns deactivated.
        """
        r = self._assert_connected()
        now = time.time()
        active_ids = set(await r.smembers(_active_set()))  # ty:ignore[invalid-await]  # snapshot before iteration
        deactivated = 0
        for campaign_id in list(active_ids):
            raw = await r.get(_campaign_key(campaign_id))
            if not raw:
                # Key expired in Redis — remove from set
                await r.srem(_active_set(), campaign_id)  # ty:ignore[invalid-await]
                deactivated += 1
                continue
            c = json.loads(raw)
            if c["end_time"] < now:
                c["status"] = CampaignStatus.COMPLETED.value
                c["updated_at"] = now
                ttl = 604800  # keep for 7 days after completion
                pipe = r.pipeline()
                pipe.setex(_campaign_key(campaign_id), ttl, json.dumps(c))
                pipe.srem(_active_set(), campaign_id)
                await pipe.execute()
                deactivated += 1
                logger.info("Campaign '%s' marked COMPLETED.", c.get("name"))

        # Activate campaigns whose start_time has arrived
        all_ids = await r.smembers(_all_set())  # ty:ignore[invalid-await]
        for campaign_id in all_ids:
            raw = await r.get(_campaign_key(campaign_id))
            if not raw:
                continue
            c = json.loads(raw)
            if (
                c["status"] == CampaignStatus.SCHEDULED.value
                and c["start_time"] <= now <= c["end_time"]
            ):
                c["status"] = CampaignStatus.ACTIVE.value
                c["updated_at"] = now
                pipe = r.pipeline()
                pipe.set(_campaign_key(campaign_id), json.dumps(c))
                pipe.sadd(_active_set(), campaign_id)
                await pipe.execute()
                logger.info("Campaign '%s' auto-activated.", c.get("name"))

        return deactivated


calendar: MarketingCalendar | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[override]
    global calendar
    calendar = MarketingCalendar(redis_url=REDIS_URL)
    await calendar.connect()
    yield
    if calendar:
        await calendar.close()

# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
app = FastAPI(
    lifespan=lifespan,
    title="iGaming Marketing Calendar",
    version="1.0.0",
    description="Redis-backed marketing campaign schedule API.",
)

app.add_middleware(
    CORSMiddleware,  # ty:ignore[invalid-argument-type]
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)



def _get_calendar() -> MarketingCalendar:
    if calendar is None:
        raise HTTPException(status_code=503, detail="Calendar not initialised.")
    return calendar


# ------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------
@app.post("/campaigns", response_model=CampaignResponse, status_code=201)
async def create_campaign(data: CampaignCreate) -> CampaignResponse:
    """Create a new marketing campaign in the calendar."""
    cal = _get_calendar()
    return await cal.create_campaign(data)


@app.get("/campaigns", response_model=list[CampaignResponse])
async def list_campaigns(
    status: CampaignStatus | None = Query(None),
    geo: str | None = Query(None, description="Filter by target geo (ISO-3166-1 alpha-2)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[CampaignResponse]:
    cal = _get_calendar()
    return await cal.list_campaigns(status_filter=status, geo_filter=geo, limit=limit, offset=offset)


@app.get("/campaigns/active", response_model=list[CampaignResponse])
async def get_active_campaigns() -> list[CampaignResponse]:
    """Return all currently active campaigns."""
    cal = _get_calendar()
    return await cal.get_active_campaigns()


@app.get("/campaigns/active/summary", response_model=list[ActiveCampaignSummary])
async def get_active_summary() -> list[ActiveCampaignSummary]:
    """Lightweight summary for dashboard widgets."""
    cal = _get_calendar()
    return await cal.get_active_summary()


@app.get("/campaigns/upcoming", response_model=list[CampaignResponse])
async def get_upcoming_campaigns(
    within_hours: float = Query(48.0, ge=1.0, le=720.0)
) -> list[CampaignResponse]:
    """Return campaigns starting within the next N hours."""
    cal = _get_calendar()
    return await cal.get_upcoming_campaigns(within_hours=within_hours)


@app.get("/campaigns/{campaign_id}", response_model=CampaignResponse)
async def get_campaign(campaign_id: str) -> CampaignResponse:
    cal = _get_calendar()
    try:
        return await cal.get_campaign(campaign_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.patch("/campaigns/{campaign_id}", response_model=CampaignResponse)
async def update_campaign(campaign_id: str, update: CampaignUpdate) -> CampaignResponse:
    cal = _get_calendar()
    try:
        return await cal.update_campaign(campaign_id, update)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/campaigns/{campaign_id}", status_code=204)
async def delete_campaign(campaign_id: str) -> None:
    cal = _get_calendar()
    await cal.delete_campaign(campaign_id)


@app.post("/campaigns/maintenance/cleanup", response_model=dict[str, int])
async def cleanup_expired() -> dict[str, int]:
    """Manually trigger cleanup of expired campaigns."""
    cal = _get_calendar()
    count = await cal.cleanup_expired()
    return {"deactivated": count}


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(
        "marketing_calendar:app",
        host="0.0.0.0",
        port=8081,
        log_level="info",
    )
