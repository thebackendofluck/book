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
Traffic Classifier - DDoS vs Marketing Campaign detection engine.

Ingests real-time traffic metrics and computes a traffic fingerprint to
classify incoming spikes as ATTACK, MARKETING_CAMPAIGN, ORGANIC_SURGE,
or UNKNOWN. Integrates with marketing calendar for confirmation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Deque

import redis.asyncio as aioredis
import uvicorn
from contextlib import asynccontextmanager
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from prometheus_client import Counter, Gauge, Histogram, generate_latest
from starlette.responses import PlainTextResponse

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("traffic_classifier")


# ---------------------------------------------------------------------------
# Enums & constants
# ---------------------------------------------------------------------------
class TrafficClass(str, Enum):
    ATTACK = "ATTACK"
    MARKETING_CAMPAIGN = "MARKETING_CAMPAIGN"
    ORGANIC_SURGE = "ORGANIC_SURGE"
    UNKNOWN = "UNKNOWN"
    NORMAL = "NORMAL"


# Weights for each signal in the final score.  Positive weight pushes toward
# ATTACK; negative weight pushes toward MARKETING_CAMPAIGN / ORGANIC_SURGE.
SIGNAL_WEIGHTS: dict[str, float] = {
    "ua_diversity": -2.0,          # high diversity → legit
    "path_diversity": -1.5,        # high diversity → legit
    "session_depth": -1.8,         # deep sessions → legit
    "geo_concentration": 1.2,      # concentrated random geos → attack
    "conversion_signals": -3.0,    # conversions → definitely legit
    "request_timing_regularity": 2.5,  # machine-regular → attack
    "tls_fingerprint_diversity": -1.5,  # diverse JA3 → legit
    "referrer_presence": -1.8,     # referrers present → legit (ads/social)
    "datacenter_ip_ratio": 2.0,    # high DC ratio → attack
    "new_ip_ratio": 1.0,           # many brand-new IPs → potential attack
}

# Classification thresholds
ATTACK_THRESHOLD = 0.65
CAMPAIGN_THRESHOLD = -0.50
ORGANIC_THRESHOLD = -0.25


# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------
CLASSIFICATION_COUNTER = Counter(
    "tc_classifications_total",
    "Total classifications by result",
    ["result"],
)
CONFIDENCE_HISTOGRAM = Histogram(
    "tc_confidence_score",
    "Distribution of classification confidence scores",
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)
CURRENT_RPS_GAUGE = Gauge("tc_current_rps", "Current requests per second")
CURRENT_CLASS_GAUGE = Gauge(
    "tc_current_classification",
    "Current traffic classification (0=NORMAL,1=CAMPAIGN,2=ORGANIC,3=UNKNOWN,4=ATTACK)",
)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------
class TrafficMetrics(BaseModel):
    """Snapshot of traffic signals at a given moment."""

    timestamp: float = Field(default_factory=time.time)
    window_seconds: int = Field(60, ge=10, le=3600)

    # Volume
    requests_per_second: float = Field(..., ge=0)
    unique_ips: int = Field(..., ge=0)
    total_requests: int = Field(..., ge=0)

    # Diversity signals (0.0–1.0; 1.0 = fully diverse)
    ua_diversity: float = Field(..., ge=0.0, le=1.0)
    path_diversity: float = Field(..., ge=0.0, le=1.0)
    tls_fingerprint_diversity: float = Field(..., ge=0.0, le=1.0)

    # Behavioural signals
    avg_session_depth: float = Field(..., ge=0.0)
    session_depth_normalized: float = Field(0.0, ge=0.0, le=1.0)

    # Geographic signals
    top_geo_concentration: float = Field(
        ..., ge=0.0, le=1.0,
        description="Fraction of traffic from top geo (0.5 = 50% from one country)",
    )
    datacenter_ip_ratio: float = Field(
        ..., ge=0.0, le=1.0,
        description="Fraction of IPs belonging to known datacenter ASNs",
    )

    # Conversion signals (0.0–1.0)
    conversion_rate: float = Field(..., ge=0.0, le=1.0)
    registration_rate: float = Field(..., ge=0.0, le=1.0)

    # Timing regularity (0.0 = human-random, 1.0 = machine-perfect)
    request_timing_regularity: float = Field(..., ge=0.0, le=1.0)

    # Referrer signals (fraction of requests carrying a referrer header)
    referrer_presence: float = Field(..., ge=0.0, le=1.0)

    # New IP ratio (IPs not seen in last 24h)
    new_ip_ratio: float = Field(..., ge=0.0, le=1.0)

    # Geo hint (optional – e.g. "BR", "MX") for campaign matching
    dominant_geo: str | None = None

    @field_validator("session_depth_normalized", mode="before")
    @classmethod
    def compute_session_depth(cls, v: float, info: Any) -> float:
        if v == 0.0 and "avg_session_depth" in (info.data or {}):
            depth = info.data["avg_session_depth"]
            # Sigmoid normalisation: saturates at ~10 pages
            return 1.0 / (1.0 + math.exp(-0.5 * (depth - 3)))
        return v


@dataclass
class TrafficFingerprint:
    """Computed fingerprint derived from raw metrics."""

    ua_diversity: float
    path_diversity: float
    session_depth: float
    geo_concentration: float
    conversion_signals: float
    request_timing_regularity: float
    tls_fingerprint_diversity: float
    referrer_presence: float
    datacenter_ip_ratio: float
    new_ip_ratio: float

    # Composite score: negative → legit, positive → attack
    raw_score: float = 0.0
    normalized_score: float = 0.0  # 0.0–1.0 attack probability

    def to_dict(self) -> dict[str, float]:
        return {
            "ua_diversity": self.ua_diversity,
            "path_diversity": self.path_diversity,
            "session_depth": self.session_depth,
            "geo_concentration": self.geo_concentration,
            "conversion_signals": self.conversion_signals,
            "request_timing_regularity": self.request_timing_regularity,
            "tls_fingerprint_diversity": self.tls_fingerprint_diversity,
            "referrer_presence": self.referrer_presence,
            "datacenter_ip_ratio": self.datacenter_ip_ratio,
            "new_ip_ratio": self.new_ip_ratio,
            "raw_score": self.raw_score,
            "normalized_score": self.normalized_score,
        }


@dataclass
class ClassificationResult:
    traffic_class: TrafficClass
    confidence: float  # 0.0–1.0
    fingerprint: TrafficFingerprint
    campaign_active: bool = False
    campaign_name: str | None = None
    explanation: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    metrics_snapshot: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "traffic_class": self.traffic_class.value,
            "confidence": round(self.confidence, 4),
            "fingerprint": self.fingerprint.to_dict(),
            "campaign_active": self.campaign_active,
            "campaign_name": self.campaign_name,
            "explanation": self.explanation,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------
class ClassifyRequest(BaseModel):
    metrics: TrafficMetrics
    force_check_calendar: bool = True


class ClassifyResponse(BaseModel):
    traffic_class: str
    confidence: float
    fingerprint: dict[str, float]
    campaign_active: bool
    campaign_name: str | None
    explanation: list[str]
    timestamp: float
    recommended_action: str


class StatusResponse(BaseModel):
    status: str
    current_classification: str
    uptime_seconds: float
    total_classifications: int
    last_attack_at: float | None
    last_campaign_at: float | None


# ---------------------------------------------------------------------------
# Classification engine
# ---------------------------------------------------------------------------
class TrafficClassifier:
    """
    Core classification engine.

    Builds a weighted signal vector from raw traffic metrics and computes a
    composite attack-probability score.  Integrates with marketing calendar
    for context-aware decisions.
    """

    def __init__(self, redis_url: str = "redis://localhost:6379/0") -> None:
        self._redis_url = redis_url
        self._redis: aioredis.Redis | None = None
        self._history: Deque[ClassificationResult] = deque(maxlen=500)
        self._start_time = time.time()
        self._total_classifications = 0
        self._last_attack_at: float | None = None
        self._last_campaign_at: float | None = None
        self._current_class = TrafficClass.NORMAL

    async def connect(self) -> None:
        self._redis = aioredis.from_url(
            self._redis_url, encoding="utf-8", decode_responses=True
        )
        logger.info("Redis connected: %s", self._redis_url)

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()

    # ------------------------------------------------------------------
    # Fingerprint computation
    # ------------------------------------------------------------------
    def _build_fingerprint(self, m: TrafficMetrics) -> TrafficFingerprint:
        """Map raw metrics to normalised signal values in [0, 1]."""

        fp = TrafficFingerprint(
            ua_diversity=m.ua_diversity,
            path_diversity=m.path_diversity,
            session_depth=m.session_depth_normalized,
            geo_concentration=m.top_geo_concentration,
            conversion_signals=min(
                1.0, (m.conversion_rate * 0.6 + m.registration_rate * 0.4) * 10
            ),
            request_timing_regularity=m.request_timing_regularity,
            tls_fingerprint_diversity=m.tls_fingerprint_diversity,
            referrer_presence=m.referrer_presence,
            datacenter_ip_ratio=m.datacenter_ip_ratio,
            new_ip_ratio=m.new_ip_ratio,
        )

        # Weighted sum – positive = more attack-like
        raw = 0.0
        for signal, weight in SIGNAL_WEIGHTS.items():
            value = getattr(fp, signal)
            # For attack signals (positive weight): raw value pushes up.
            # For legit signals (negative weight): raw value pushes down.
            raw += weight * value

        fp.raw_score = raw

        # Normalise to [0, 1] attack probability via sigmoid
        # raw_score range roughly [-9, +9] based on weights
        fp.normalized_score = 1.0 / (1.0 + math.exp(-raw * 0.5))

        return fp

    # ------------------------------------------------------------------
    # Marketing calendar lookup
    # ------------------------------------------------------------------
    async def _check_campaign(
        self, metrics: TrafficMetrics
    ) -> tuple[bool, str | None]:
        """Return (is_active, campaign_name) from Redis calendar."""
        if not self._redis:
            return False, None

        now = time.time()
        try:
            keys = await self._redis.smembers("campaigns:active")  # ty:ignore[invalid-await]
            for key in keys:
                raw = await self._redis.get(f"campaign:{key}")
                if not raw:
                    continue
                campaign = json.loads(raw)
                start = campaign.get("start_time", 0)
                end = campaign.get("end_time", 0)
                if not (start <= now <= end):
                    continue

                # Geo check: if campaign targets specific geos, check match
                target_geos: list[str] = campaign.get("target_geos", [])
                if target_geos and metrics.dominant_geo:
                    if metrics.dominant_geo.upper() not in [
                        g.upper() for g in target_geos
                    ]:
                        continue  # this campaign targets different geo

                return True, campaign.get("name", key)
        except Exception as exc:
            logger.warning("Redis campaign lookup failed: %s", exc)

        return False, None

    # ------------------------------------------------------------------
    # Classification logic
    # ------------------------------------------------------------------
    async def classify(self, metrics: TrafficMetrics) -> ClassificationResult:
        fp = self._build_fingerprint(metrics)
        score = fp.normalized_score  # 0.0 = definitely legit, 1.0 = attack
        explanation: list[str] = []

        campaign_active, campaign_name = await self._check_campaign(metrics)

        # ----------------------------------------------------------
        # Rule-based overrides (high-confidence edge cases)
        # ----------------------------------------------------------
        if metrics.conversion_rate > 0.02 or metrics.registration_rate > 0.01:
            explanation.append(
                "Conversion/registration signals detected — bots do not convert."
            )
            # Strong evidence this is real traffic; cap score
            score = min(score, 0.3)

        if metrics.datacenter_ip_ratio > 0.85 and metrics.ua_diversity < 0.15:
            explanation.append(
                "85%+ datacenter IPs with <15% UA diversity — high-confidence botnet."
            )
            score = max(score, 0.85)

        if metrics.referrer_presence > 0.4 and metrics.path_diversity > 0.4:
            explanation.append(
                "Strong referrer signal with diverse path distribution — consistent with paid campaign."
            )
            score = min(score, 0.4)

        if campaign_active:
            explanation.append(
                f"Active marketing campaign '{campaign_name}' found in calendar — "
                "lowering attack probability by 0.15."
            )
            score = max(0.0, score - 0.15)

        # ----------------------------------------------------------
        # Classify
        # ----------------------------------------------------------
        if score >= ATTACK_THRESHOLD:
            traffic_class = TrafficClass.ATTACK
            confidence = (score - ATTACK_THRESHOLD) / (1.0 - ATTACK_THRESHOLD)
            explanation.append(
                f"Attack score {score:.2f} exceeds threshold {ATTACK_THRESHOLD}."
            )

        elif score <= (1.0 - abs(CAMPAIGN_THRESHOLD)):
            # Score very low → highly legit
            if campaign_active:
                traffic_class = TrafficClass.MARKETING_CAMPAIGN
                confidence = (
                    (1.0 - score) / 1.0
                    if score < 0.5
                    else 0.5
                )
                explanation.append("Low attack score + active campaign = MARKETING_CAMPAIGN.")
            else:
                traffic_class = TrafficClass.ORGANIC_SURGE
                confidence = max(0.0, 1.0 - score * 2)
                explanation.append("Low attack score, no active campaign = ORGANIC_SURGE.")

        else:
            traffic_class = TrafficClass.UNKNOWN
            confidence = 1.0 - abs(score - 0.5) * 2
            explanation.append(
                f"Score {score:.2f} is in ambiguous range — manual review recommended."
            )

        # Clamp confidence
        confidence = max(0.0, min(1.0, confidence))

        result = ClassificationResult(
            traffic_class=traffic_class,
            confidence=confidence,
            fingerprint=fp,
            campaign_active=campaign_active,
            campaign_name=campaign_name,
            explanation=explanation,
            metrics_snapshot=metrics.model_dump(),
        )

        # Bookkeeping
        self._history.append(result)
        self._total_classifications += 1
        self._current_class = traffic_class

        if traffic_class == TrafficClass.ATTACK:
            self._last_attack_at = time.time()
        elif traffic_class == TrafficClass.MARKETING_CAMPAIGN:
            self._last_campaign_at = time.time()

        # Prometheus
        CLASSIFICATION_COUNTER.labels(result=traffic_class.value).inc()
        CONFIDENCE_HISTOGRAM.observe(confidence)
        CURRENT_RPS_GAUGE.set(metrics.requests_per_second)
        _class_map = {
            TrafficClass.NORMAL: 0,
            TrafficClass.MARKETING_CAMPAIGN: 1,
            TrafficClass.ORGANIC_SURGE: 2,
            TrafficClass.UNKNOWN: 3,
            TrafficClass.ATTACK: 4,
        }
        CURRENT_CLASS_GAUGE.set(_class_map.get(traffic_class, 3))

        logger.info(
            "Classification: %s (confidence=%.2f, score=%.3f, campaign=%s)",
            traffic_class.value,
            confidence,
            score,
            campaign_name,
        )
        return result

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "current_classification": self._current_class.value,
            "uptime_seconds": time.time() - self._start_time,
            "total_classifications": self._total_classifications,
            "last_attack_at": self._last_attack_at,
            "last_campaign_at": self._last_campaign_at,
            "history_size": len(self._history),
        }

    def recent_history(self, n: int = 20) -> list[dict[str, Any]]:
        results = list(self._history)[-n:]
        return [r.to_dict() for r in reversed(results)]


classifier: TrafficClassifier | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[override]
    import os
    global classifier
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    classifier = TrafficClassifier(redis_url=redis_url)
    await classifier.connect()
    logger.info("Traffic classifier started.")
    yield
    if classifier:
        await classifier.close()

# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
app = FastAPI(
    lifespan=lifespan,
    title="iGaming Traffic Classifier",
    version="1.0.0",
    description="DDoS vs Marketing Campaign real-time traffic classification engine.",
)

app.add_middleware(
    CORSMiddleware,  # ty:ignore[invalid-argument-type]
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)



def _get_classifier() -> TrafficClassifier:
    if classifier is None:
        raise HTTPException(status_code=503, detail="Classifier not initialised.")
    return classifier


def _recommended_action(result: ClassificationResult) -> str:
    if result.traffic_class == TrafficClass.ATTACK:
        if result.confidence >= 0.8:
            return "ENABLE_DDOS_PROTECTION_IMMEDIATELY"
        return "RATE_LIMIT_AND_MONITOR"
    if result.traffic_class == TrafficClass.MARKETING_CAMPAIGN:
        return "SCALE_UP_AND_INCREASE_CACHE_TTL"
    if result.traffic_class == TrafficClass.ORGANIC_SURGE:
        return "SCALE_UP_GRADUALLY"
    return "ALERT_NOC_FOR_MANUAL_REVIEW"


@app.post("/classify", response_model=ClassifyResponse)
async def classify_traffic(
    request: ClassifyRequest,
    background_tasks: BackgroundTasks,
) -> ClassifyResponse:
    """Classify a traffic snapshot.  Returns classification + recommended action."""
    clf = _get_classifier()
    result = await clf.classify(request.metrics)

    return ClassifyResponse(
        traffic_class=result.traffic_class.value,
        confidence=result.confidence,
        fingerprint=result.fingerprint.to_dict(),
        campaign_active=result.campaign_active,
        campaign_name=result.campaign_name,
        explanation=result.explanation,
        timestamp=result.timestamp,
        recommended_action=_recommended_action(result),
    )


@app.get("/status", response_model=StatusResponse)
async def get_status() -> StatusResponse:
    clf = _get_classifier()
    s = clf.status()
    return StatusResponse(**s)


@app.get("/metrics")
async def prometheus_metrics() -> PlainTextResponse:
    return PlainTextResponse(generate_latest(), media_type="text/plain; version=0.0.4")


@app.get("/history")
async def get_history(n: int = 20) -> list[dict[str, Any]]:
    """Return the last N classification results."""
    clf = _get_classifier()
    return clf.recent_history(n=min(n, 100))


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(
        "traffic_classifier:app",
        host="0.0.0.0",
        port=8080,
        log_level="info",
        workers=1,  # Single worker — shared in-process state
    )
