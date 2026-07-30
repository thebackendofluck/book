#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 13, Live Casino Streaming Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Adaptive Bitrate Streaming with Bandwidth Estimation
Chapter 6 - Live Casino Streaming Infrastructure

Purpose: Server-side adaptive bitrate (ABR) controller for live casino streams.
Implements Google's GCC (Google Congestion Control) inspired bandwidth estimation
with casino-specific optimizations:
  - Prioritize latency over throughput (live casino requires < 500ms)
  - Smooth quality transitions to avoid jarring visual switches during gameplay
  - Per-player quality tracking with session-aware decisions
  - Integration with mediasoup SFU layer selection

Architecture:
  Player Client <-> WebSocket <-> ABR Controller <-> mediasoup SFU
                                                   -> Metrics (Prometheus)

Usage:
  python bandwidth_estimator.py
  ABR_PORT=8090 python bandwidth_estimator.py

Dependencies:
  pip install aiohttp websockets prometheus-client numpy
"""

import asyncio
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional

import aiohttp
import numpy as np
from aiohttp import web
from prometheus_client import Counter, Gauge, Histogram, generate_latest

# =============================================================================
# Configuration
# =============================================================================
ABR_PORT = int(os.getenv("ABR_PORT", "8090"))
SFU_URL = os.getenv("SFU_URL", "http://mediasoup-sfu.live-casino:3000")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("abr-controller")


# =============================================================================
# Quality Tiers (matching mediasoup simulcast layers)
# =============================================================================
class QualityTier(IntEnum):
    LOW = 0      # 480p, ~500 Kbps
    MEDIUM = 1   # 720p, ~1.5 Mbps
    HIGH = 2     # 1080p, ~4.5 Mbps


@dataclass
class TierSpec:
    tier: QualityTier
    spatial_layer: int
    temporal_layer: int
    min_bandwidth_kbps: int
    target_bandwidth_kbps: int
    resolution: str


QUALITY_TIERS = [
    TierSpec(QualityTier.LOW, 0, 2, 400, 600, "854x480"),
    TierSpec(QualityTier.MEDIUM, 1, 2, 1200, 1800, "1280x720"),
    TierSpec(QualityTier.HIGH, 2, 2, 3500, 5000, "1920x1080"),
]


# =============================================================================
# Bandwidth Estimator (GCC-inspired)
# =============================================================================
@dataclass
class BandwidthSample:
    timestamp: float
    bytes_received: int
    rtt_ms: float
    jitter_ms: float
    packets_lost: int
    packets_received: int


@dataclass
class PlayerSession:
    player_id: str
    table_id: str
    consumer_id: str
    current_tier: QualityTier = QualityTier.HIGH
    estimated_bandwidth_kbps: float = 5000.0
    samples: deque = field(default_factory=lambda: deque(maxlen=60))
    last_switch_time: float = 0.0
    switch_count: int = 0
    created_at: float = field(default_factory=time.time)

    # Kalman filter state for bandwidth estimation
    kalman_estimate: float = 5000.0
    kalman_variance: float = 1000.0


class BandwidthEstimator:
    """
    GCC-inspired bandwidth estimation with Kalman filtering.

    Live casino adaptations:
    - Aggressive upshift: players should see highest quality ASAP
    - Conservative downshift: avoid quality drops during active betting rounds
    - Minimum hold time between switches: 5 seconds
    - Hysteresis: require 20% bandwidth headroom before upshifting
    """

    # Kalman filter parameters
    PROCESS_NOISE = 50.0        # Bandwidth variability expectation
    MEASUREMENT_NOISE = 200.0   # Measurement uncertainty
    UPSHIFT_HEADROOM = 1.2      # 20% headroom required for upshift
    DOWNSHIFT_THRESHOLD = 0.85  # Downshift when below 85% of tier minimum
    MIN_SWITCH_INTERVAL_S = 5.0 # Minimum seconds between quality switches
    EWMA_ALPHA = 0.3            # Exponential weighted moving average factor

    def __init__(self):
        self.sessions: dict[str, PlayerSession] = {}

    def update_estimate(self, session: PlayerSession, sample: BandwidthSample) -> None:
        """Update bandwidth estimate using Kalman filter + EWMA hybrid."""
        session.samples.append(sample)

        if len(session.samples) < 2:
            return

        # Calculate instantaneous throughput
        prev = session.samples[-2]
        dt = sample.timestamp - prev.timestamp
        if dt <= 0:
            return

        bytes_delta = sample.bytes_received - prev.bytes_received
        throughput_kbps = (bytes_delta * 8) / (dt * 1000)

        # Kalman filter prediction step
        predicted_estimate = session.kalman_estimate
        predicted_variance = session.kalman_variance + self.PROCESS_NOISE

        # Kalman filter update step
        kalman_gain = predicted_variance / (predicted_variance + self.MEASUREMENT_NOISE)
        session.kalman_estimate = predicted_estimate + kalman_gain * (throughput_kbps - predicted_estimate)
        session.kalman_variance = (1 - kalman_gain) * predicted_variance

        # Hybrid: blend Kalman with EWMA for stability
        ewma_estimate = (
            self.EWMA_ALPHA * throughput_kbps
            + (1 - self.EWMA_ALPHA) * session.estimated_bandwidth_kbps
        )

        # Weight Kalman more when stable, EWMA more when volatile
        jitter_factor = min(sample.jitter_ms / 50.0, 1.0)  # Normalize jitter
        session.estimated_bandwidth_kbps = (
            (1 - jitter_factor) * session.kalman_estimate
            + jitter_factor * ewma_estimate
        )

        # Floor at 100 Kbps
        session.estimated_bandwidth_kbps = max(session.estimated_bandwidth_kbps, 100.0)

        logger.debug(
            "BW estimate for %s: %.0f Kbps (throughput=%.0f, rtt=%.0f, jitter=%.1f, loss=%.2f%%)",
            session.player_id,
            session.estimated_bandwidth_kbps,
            throughput_kbps,
            sample.rtt_ms,
            sample.jitter_ms,
            (sample.packets_lost / max(sample.packets_received, 1)) * 100,
        )

    def select_tier(self, session: PlayerSession) -> Optional[QualityTier]:
        """
        Select the optimal quality tier based on estimated bandwidth.
        Returns new tier if a switch is recommended, None otherwise.
        """
        now = time.time()
        bw = session.estimated_bandwidth_kbps
        current = session.current_tier

        # Enforce minimum switch interval
        if now - session.last_switch_time < self.MIN_SWITCH_INTERVAL_S:
            return None

        # Check for downshift need (urgent - can override interval for severe drops)
        current_spec = QUALITY_TIERS[current]
        if bw < current_spec.min_bandwidth_kbps * self.DOWNSHIFT_THRESHOLD:
            # Find highest tier we can sustain
            for tier_spec in reversed(QUALITY_TIERS):
                if tier_spec.tier < current and bw >= tier_spec.min_bandwidth_kbps:
                    return tier_spec.tier
            return QualityTier.LOW

        # Check for upshift opportunity
        for tier_spec in reversed(QUALITY_TIERS):
            if tier_spec.tier > current:
                if bw >= tier_spec.target_bandwidth_kbps * self.UPSHIFT_HEADROOM:
                    # Verify sustained bandwidth (check last 5 samples)
                    if len(session.samples) >= 5:
                        recent_stable = all(
                            s.jitter_ms < 30 and s.rtt_ms < 150
                            for s in list(session.samples)[-5:]
                        )
                        if recent_stable:
                            return tier_spec.tier

        return None


# =============================================================================
# Prometheus Metrics
# =============================================================================
metrics_bandwidth_estimate = Gauge(
    "abr_bandwidth_estimate_kbps",
    "Estimated player bandwidth in Kbps",
    ["player_id", "table_id"],
)
metrics_current_tier = Gauge(
    "abr_current_quality_tier",
    "Current quality tier (0=LOW, 1=MED, 2=HIGH)",
    ["player_id", "table_id"],
)
metrics_tier_switches = Counter(
    "abr_tier_switches_total",
    "Total quality tier switches",
    ["table_id", "direction"],
)
metrics_rtt = Histogram(
    "abr_rtt_ms",
    "Round-trip time in milliseconds",
    ["table_id"],
    buckets=[10, 25, 50, 100, 150, 200, 300, 500, 1000],
)
metrics_jitter = Histogram(
    "abr_jitter_ms",
    "Jitter in milliseconds",
    ["table_id"],
    buckets=[1, 5, 10, 20, 30, 50, 100],
)


# =============================================================================
# ABR Controller HTTP API
# =============================================================================
class ABRController:
    def __init__(self):
        self.estimator = BandwidthEstimator()
        self.http_session: Optional[aiohttp.ClientSession] = None

    async def start(self):
        self.http_session = aiohttp.ClientSession()

    async def stop(self):
        if self.http_session:
            await self.http_session.close()

    async def register_player(self, request: web.Request) -> web.Response:
        """Register a new player session for ABR tracking."""
        data = await request.json()
        player_id = data["player_id"]
        table_id = data["table_id"]
        consumer_id = data["consumer_id"]

        session = PlayerSession(
            player_id=player_id,
            table_id=table_id,
            consumer_id=consumer_id,
        )
        self.estimator.sessions[player_id] = session

        metrics_current_tier.labels(player_id=player_id, table_id=table_id).set(
            session.current_tier.value
        )

        logger.info("Registered player %s at table %s (consumer: %s)", player_id, table_id, consumer_id)
        return web.json_response({"status": "registered", "initial_tier": session.current_tier.name})

    async def unregister_player(self, request: web.Request) -> web.Response:
        """Remove a player session."""
        player_id = request.match_info["player_id"]
        session = self.estimator.sessions.pop(player_id, None)
        if session:
            logger.info(
                "Unregistered player %s (duration: %.0fs, switches: %d)",
                player_id,
                time.time() - session.created_at,
                session.switch_count,
            )
        return web.json_response({"status": "unregistered"})

    async def report_stats(self, request: web.Request) -> web.Response:
        """
        Receive WebRTC stats from player client and return quality decision.

        Expected payload (from client-side getStats()):
        {
            "player_id": "p123",
            "bytes_received": 12345678,
            "rtt_ms": 45.2,
            "jitter_ms": 3.1,
            "packets_lost": 2,
            "packets_received": 15000
        }
        """
        data = await request.json()
        player_id = data["player_id"]

        session = self.estimator.sessions.get(player_id)
        if not session:
            return web.json_response({"error": "Player not registered"}, status=404)

        # Create bandwidth sample
        sample = BandwidthSample(
            timestamp=time.time(),
            bytes_received=data["bytes_received"],
            rtt_ms=data.get("rtt_ms", 0),
            jitter_ms=data.get("jitter_ms", 0),
            packets_lost=data.get("packets_lost", 0),
            packets_received=data.get("packets_received", 1),
        )

        # Update bandwidth estimate
        self.estimator.update_estimate(session, sample)

        # Check if quality tier should change
        new_tier = self.estimator.select_tier(session)

        response = {
            "estimated_bandwidth_kbps": round(session.estimated_bandwidth_kbps),
            "current_tier": session.current_tier.name,
            "switch": None,
        }

        if new_tier is not None and new_tier != session.current_tier:
            old_tier = session.current_tier
            direction = "up" if new_tier > old_tier else "down"

            # Apply the switch
            session.current_tier = new_tier
            session.last_switch_time = time.time()
            session.switch_count += 1

            tier_spec = QUALITY_TIERS[new_tier]

            # Notify mediasoup SFU to change simulcast layer
            await self._set_sfu_layer(
                session.consumer_id,
                tier_spec.spatial_layer,
                tier_spec.temporal_layer,
            )

            # Update metrics
            metrics_tier_switches.labels(table_id=session.table_id, direction=direction).inc()
            metrics_current_tier.labels(
                player_id=player_id, table_id=session.table_id
            ).set(new_tier.value)

            response["switch"] = {
                "from": old_tier.name,
                "to": new_tier.name,
                "direction": direction,
                "resolution": tier_spec.resolution,
            }

            logger.info(
                "Quality switch for %s: %s -> %s (bw=%.0f Kbps)",
                player_id, old_tier.name, new_tier.name,
                session.estimated_bandwidth_kbps,
            )

        # Update metrics
        metrics_bandwidth_estimate.labels(
            player_id=player_id, table_id=session.table_id
        ).set(session.estimated_bandwidth_kbps)
        metrics_rtt.labels(table_id=session.table_id).observe(sample.rtt_ms)
        metrics_jitter.labels(table_id=session.table_id).observe(sample.jitter_ms)

        return web.json_response(response)

    async def _set_sfu_layer(
        self, consumer_id: str, spatial_layer: int, temporal_layer: int
    ) -> None:
        """Tell mediasoup to change the simulcast layer for a consumer."""
        try:
            async with self.http_session.post(  # ty:ignore[possibly-missing-attribute]
                f"{SFU_URL}/api/v1/consumers/{consumer_id}/preferred-layers",
                json={"spatialLayer": spatial_layer, "temporalLayer": temporal_layer},
                timeout=aiohttp.ClientTimeout(total=3),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error("SFU layer switch failed: %s %s", resp.status, body)
        except Exception as e:
            logger.error("SFU layer switch error: %s", e)

    async def get_session_info(self, request: web.Request) -> web.Response:
        """Get current ABR state for a player (debugging/dashboard)."""
        player_id = request.match_info["player_id"]
        session = self.estimator.sessions.get(player_id)
        if not session:
            return web.json_response({"error": "Not found"}, status=404)

        return web.json_response({
            "player_id": session.player_id,
            "table_id": session.table_id,
            "consumer_id": session.consumer_id,
            "current_tier": session.current_tier.name,
            "estimated_bandwidth_kbps": round(session.estimated_bandwidth_kbps),
            "kalman_estimate_kbps": round(session.kalman_estimate),
            "switch_count": session.switch_count,
            "session_duration_s": round(time.time() - session.created_at),
            "sample_count": len(session.samples),
        })

    async def metrics_handler(self, request: web.Request) -> web.Response:
        """Prometheus metrics endpoint."""
        return web.Response(
            body=generate_latest(),
            content_type="text/plain; version=0.0.4",
        )

    async def health(self, request: web.Request) -> web.Response:
        return web.json_response({
            "status": "healthy",
            "active_sessions": len(self.estimator.sessions),
        })


# =============================================================================
# Main
# =============================================================================
def create_app() -> web.Application:
    controller = ABRController()
    app = web.Application()

    app.on_startup.append(lambda _: controller.start())
    app.on_cleanup.append(lambda _: controller.stop())

    app.router.add_post("/api/v1/abr/register", controller.register_player)
    app.router.add_delete("/api/v1/abr/players/{player_id}", controller.unregister_player)
    app.router.add_post("/api/v1/abr/stats", controller.report_stats)
    app.router.add_get("/api/v1/abr/players/{player_id}", controller.get_session_info)
    app.router.add_get("/metrics", controller.metrics_handler)
    app.router.add_get("/health", controller.health)

    return app


if __name__ == "__main__":
    app = create_app()
    logger.info("ABR Controller starting on port %d", ABR_PORT)
    web.run_app(app, host="0.0.0.0", port=ABR_PORT, access_log=logger)
