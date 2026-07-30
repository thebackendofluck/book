#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 43, Future Technology & Innovation in iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Real-Time ML Inference Pipeline for Player Risk Scoring
=========================================================

End-to-end pipeline for computing player risk scores in real time,
covering feature computation, model serving, and decision engine.
Designed for sub-50ms latency at thousands of requests per second.

Covers:
- Feature store integration (real-time + batch features)
- Online feature computation from streaming events
- Model serving with fallback and circuit breaker
- Decision engine with configurable risk thresholds
- A/B testing support for model variants
- Audit logging for regulatory compliance

Feasibility Assessment:
- Feature computation uses pre-aggregated counters (Redis pattern)
- Model inference simulates sklearn/ONNX scoring (no deps for core)
- Circuit breaker pattern prevents cascade failures
- Audit log is append-only for regulatory examination
- Production: serve via ONNX Runtime or TensorFlow Serving behind gRPC

Dependencies: None for core. Production: onnxruntime, redis, kafka-python
"""

import json
import math
import time
import random
import logging
import hashlib
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional, Callable
from enum import Enum
from collections import defaultdict, deque

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core types
# ---------------------------------------------------------------------------

class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FeatureSource(Enum):
    REAL_TIME = "real_time"     # computed from streaming events
    BATCH = "batch"            # pre-computed daily/hourly
    STATIC = "static"          # player profile attributes


class ModelStatus(Enum):
    ACTIVE = "active"
    FALLBACK = "fallback"
    CIRCUIT_OPEN = "circuit_open"


class DecisionAction(Enum):
    ALLOW = "allow"
    MONITOR = "monitor"
    REVIEW = "review"
    BLOCK = "block"
    INTERVENE = "intervene"


@dataclass
class PlayerEvent:
    """A real-time event from a player session."""
    event_id: str
    player_id: str
    event_type: str  # "bet", "deposit", "login", "withdrawal", "session_start"
    amount: float = 0.0
    currency: str = "EUR"
    game_id: str = ""
    timestamp: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class FeatureVector:
    """Computed feature vector for model input."""
    player_id: str
    features: dict[str, float] = field(default_factory=dict)
    feature_sources: dict[str, FeatureSource] = field(default_factory=dict)
    computation_time_ms: float = 0.0
    timestamp: str = ""


@dataclass
class RiskScore:
    """Model output: risk score with metadata."""
    player_id: str
    score: float  # 0.0 (safe) to 1.0 (dangerous)
    risk_level: RiskLevel = RiskLevel.LOW
    model_version: str = ""
    model_status: ModelStatus = ModelStatus.ACTIVE
    inference_time_ms: float = 0.0
    feature_importances: dict[str, float] = field(default_factory=dict)
    timestamp: str = ""


@dataclass
class RiskDecision:
    """Decision engine output."""
    decision_id: str
    player_id: str
    action: DecisionAction
    risk_score: float
    risk_level: RiskLevel
    reason: str
    auto_executed: bool = False
    total_latency_ms: float = 0.0
    timestamp: str = ""


# ---------------------------------------------------------------------------
# Feature store (in-memory simulation)
# ---------------------------------------------------------------------------

class FeatureStore:
    """
    Computes and caches features for real-time inference.

    Real-time features: computed from sliding windows over event streams.
    Batch features: pre-computed daily aggregates (simulated here).
    Static features: player profile attributes.

    Production: Redis for real-time counters, BigQuery/Snowflake for batch,
    Feast or Tecton for feature store management.
    """

    def __init__(self):
        # Real-time sliding window counters
        self._event_windows: dict[str, deque] = defaultdict(lambda: deque(maxlen=500))
        # Batch features (pre-computed)
        self._batch_features: dict[str, dict] = {}
        # Static player attributes
        self._static_features: dict[str, dict] = {}

    def ingest_event(self, event: PlayerEvent):
        """Ingest a real-time event for feature computation."""
        key = event.player_id
        self._event_windows[key].append({
            "type": event.event_type,
            "amount": event.amount,
            "timestamp": event.timestamp,
            "game_id": event.game_id,
        })

    def set_batch_features(self, player_id: str, features: dict):
        """Set pre-computed batch features for a player."""
        self._batch_features[player_id] = features

    def set_static_features(self, player_id: str, features: dict):
        """Set static profile features for a player."""
        self._static_features[player_id] = features

    def compute_features(self, player_id: str) -> FeatureVector:
        """
        Compute full feature vector combining real-time, batch, and static features.
        This is called on every inference request.
        """
        start = time.monotonic()
        features = {}
        sources = {}

        # --- Real-time features (from event window) ---
        events = list(self._event_windows.get(player_id, []))

        # Bet count in last N events
        bets = [e for e in events if e["type"] == "bet"]
        deposits = [e for e in events if e["type"] == "deposit"]

        features["bet_count_session"] = len(bets)
        sources["bet_count_session"] = FeatureSource.REAL_TIME

        features["total_wagered_session"] = sum(e["amount"] for e in bets)
        sources["total_wagered_session"] = FeatureSource.REAL_TIME

        features["avg_bet_size"] = (
            features["total_wagered_session"] / max(len(bets), 1)
        )
        sources["avg_bet_size"] = FeatureSource.REAL_TIME

        features["max_bet_size"] = max((e["amount"] for e in bets), default=0)
        sources["max_bet_size"] = FeatureSource.REAL_TIME

        features["deposit_count_session"] = len(deposits)
        sources["deposit_count_session"] = FeatureSource.REAL_TIME

        features["total_deposited_session"] = sum(e["amount"] for e in deposits)
        sources["total_deposited_session"] = FeatureSource.REAL_TIME

        # Bet velocity (bets per minute, estimated)
        if len(bets) >= 2:
            features["bet_velocity"] = len(bets) / max(len(events) / 10.0, 1.0)
        else:
            features["bet_velocity"] = 0.0
        sources["bet_velocity"] = FeatureSource.REAL_TIME

        # Unique games played
        features["unique_games_session"] = len(set(e["game_id"] for e in bets if e["game_id"]))
        sources["unique_games_session"] = FeatureSource.REAL_TIME

        # Bet size variance (chasing losses indicator)
        if len(bets) >= 3:
            amounts = [e["amount"] for e in bets]
            mean_amt = sum(amounts) / len(amounts)
            variance = sum((a - mean_amt) ** 2 for a in amounts) / len(amounts)
            features["bet_size_variance"] = round(math.sqrt(variance), 2)
        else:
            features["bet_size_variance"] = 0.0
        sources["bet_size_variance"] = FeatureSource.REAL_TIME

        # --- Batch features ---
        batch = self._batch_features.get(player_id, {})
        for key, value in batch.items():
            features[f"batch_{key}"] = value
            sources[f"batch_{key}"] = FeatureSource.BATCH

        # --- Static features ---
        static = self._static_features.get(player_id, {})
        for key, value in static.items():
            features[f"static_{key}"] = value
            sources[f"static_{key}"] = FeatureSource.STATIC

        elapsed = (time.monotonic() - start) * 1000

        return FeatureVector(
            player_id=player_id,
            features=features,
            feature_sources=sources,
            computation_time_ms=round(elapsed, 3),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )


# ---------------------------------------------------------------------------
# Model server (simulated)
# ---------------------------------------------------------------------------

class ModelServer:
    """
    Serves ML model predictions with fallback and circuit breaker.

    Primary model: simulated gradient-boosted tree (production: ONNX Runtime)
    Fallback model: simple rule-based scoring (always available)

    Circuit breaker: opens after 5 failures in 60 seconds,
    half-opens after 30 seconds to test recovery.
    """

    def __init__(self, model_version: str = "v3.2.1"):
        self.model_version = model_version
        self.status = ModelStatus.ACTIVE
        self._failure_count = 0
        self._failure_window: deque = deque(maxlen=10)
        self._circuit_open_time: Optional[float] = None
        self._circuit_breaker_threshold = 5
        self._circuit_breaker_timeout = 30.0  # seconds

        # Simulated model weights (in production, loaded from ONNX/pickle)
        self._weights = {
            "bet_count_session": 0.08,
            "total_wagered_session": 0.15,
            "avg_bet_size": 0.12,
            "max_bet_size": 0.10,
            "deposit_count_session": 0.10,
            "total_deposited_session": 0.12,
            "bet_velocity": 0.15,
            "bet_size_variance": 0.10,
            "unique_games_session": -0.05,  # more variety = lower risk
            "batch_avg_daily_loss_30d": 0.08,
            "batch_deposit_frequency_30d": 0.05,
            "static_days_since_registration": -0.03,
            "static_has_deposit_limit": -0.10,
        }

        # Normalization ranges for features
        self._norms = {
            "bet_count_session": (0, 200),
            "total_wagered_session": (0, 10000),
            "avg_bet_size": (0, 500),
            "max_bet_size": (0, 1000),
            "deposit_count_session": (0, 10),
            "total_deposited_session": (0, 5000),
            "bet_velocity": (0, 5),
            "bet_size_variance": (0, 200),
            "unique_games_session": (0, 20),
            "batch_avg_daily_loss_30d": (0, 1000),
            "batch_deposit_frequency_30d": (0, 30),
            "static_days_since_registration": (0, 1000),
            "static_has_deposit_limit": (0, 1),
        }

    def predict(self, features: FeatureVector) -> RiskScore:
        """
        Run model inference. Uses circuit breaker pattern.
        """
        start = time.monotonic()

        # Check circuit breaker
        if self.status == ModelStatus.CIRCUIT_OPEN:
            if self._circuit_open_time and (time.monotonic() - self._circuit_open_time) > self._circuit_breaker_timeout:
                self.status = ModelStatus.ACTIVE  # half-open, try again
                logger.info("Circuit breaker half-open, attempting primary model")
            else:
                return self._fallback_predict(features, start)

        try:
            score = self._primary_predict(features)
            self._failure_count = 0  # reset on success
            elapsed = (time.monotonic() - start) * 1000

            # Compute feature importances
            importances = {}
            for feat, weight in self._weights.items():
                if feat in features.features:
                    norm_val = self._normalize(feat, features.features[feat])
                    importances[feat] = round(abs(weight * norm_val), 4)

            return RiskScore(
                player_id=features.player_id,
                score=round(score, 4),
                risk_level=self._score_to_level(score),
                model_version=self.model_version,
                model_status=ModelStatus.ACTIVE,
                inference_time_ms=round(elapsed, 3),
                feature_importances=importances,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

        except Exception as e:
            self._failure_count += 1
            self._failure_window.append(time.monotonic())
            logger.warning(f"Primary model failed: {e}")

            if self._failure_count >= self._circuit_breaker_threshold:
                self.status = ModelStatus.CIRCUIT_OPEN
                self._circuit_open_time = time.monotonic()
                logger.error("Circuit breaker OPEN - switching to fallback model")

            return self._fallback_predict(features, start)

    def _primary_predict(self, features: FeatureVector) -> float:
        """Simulated primary model (gradient-boosted tree)."""
        score = 0.0
        for feat, weight in self._weights.items():
            value = features.features.get(feat, 0.0)
            norm_value = self._normalize(feat, value)
            score += weight * norm_value

        # Sigmoid to constrain 0-1
        score = 1.0 / (1.0 + math.exp(-score * 3))
        return max(0.0, min(1.0, score))

    def _fallback_predict(self, features: FeatureVector, start: float) -> RiskScore:
        """Rule-based fallback model. Always available, less accurate."""
        score = 0.3  # baseline

        wagered = features.features.get("total_wagered_session", 0)
        if wagered > 5000:
            score += 0.3
        elif wagered > 1000:
            score += 0.15

        velocity = features.features.get("bet_velocity", 0)
        if velocity > 3:
            score += 0.2

        deposits = features.features.get("deposit_count_session", 0)
        if deposits > 3:
            score += 0.15

        score = max(0.0, min(1.0, score))
        elapsed = (time.monotonic() - start) * 1000

        return RiskScore(
            player_id=features.player_id,
            score=round(score, 4),
            risk_level=self._score_to_level(score),
            model_version="fallback-rules-v1",
            model_status=ModelStatus.FALLBACK,
            inference_time_ms=round(elapsed, 3),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    def _normalize(self, feature: str, value: float) -> float:
        """Min-max normalize a feature value to [0, 1]."""
        if feature not in self._norms:
            return value
        min_val, max_val = self._norms[feature]
        if max_val == min_val:
            return 0.0
        return max(0.0, min(1.0, (value - min_val) / (max_val - min_val)))

    def _score_to_level(self, score: float) -> RiskLevel:
        if score >= 0.8:
            return RiskLevel.CRITICAL
        elif score >= 0.6:
            return RiskLevel.HIGH
        elif score >= 0.35:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW


# ---------------------------------------------------------------------------
# Decision engine
# ---------------------------------------------------------------------------

class DecisionEngine:
    """
    Converts risk scores into actionable decisions with configurable thresholds.

    Thresholds are jurisdiction-specific (UK GC has stricter requirements
    than some other regulators).
    """

    DEFAULT_THRESHOLDS = {
        RiskLevel.LOW: DecisionAction.ALLOW,
        RiskLevel.MEDIUM: DecisionAction.MONITOR,
        RiskLevel.HIGH: DecisionAction.REVIEW,
        RiskLevel.CRITICAL: DecisionAction.BLOCK,
    }

    # Stricter thresholds for UK players
    UK_THRESHOLDS = {
        RiskLevel.LOW: DecisionAction.ALLOW,
        RiskLevel.MEDIUM: DecisionAction.REVIEW,
        RiskLevel.HIGH: DecisionAction.INTERVENE,
        RiskLevel.CRITICAL: DecisionAction.BLOCK,
    }

    def __init__(self):
        self._decision_counter = 0
        self.audit_log: list[dict] = []

    def decide(
        self,
        risk_score: RiskScore,
        jurisdiction: str = "default",
        player_context: Optional[dict] = None,
    ) -> RiskDecision:
        """Make a risk decision and log it for audit."""
        thresholds = self.UK_THRESHOLDS if jurisdiction == "UK" else self.DEFAULT_THRESHOLDS
        action = thresholds[risk_score.risk_level]

        # Override: if player has self-exclusion history, always intervene on HIGH+
        if player_context and player_context.get("self_exclusion_history"):
            if risk_score.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
                action = DecisionAction.INTERVENE

        self._decision_counter += 1
        decision = RiskDecision(
            decision_id=f"DEC-{self._decision_counter:06d}",
            player_id=risk_score.player_id,
            action=action,
            risk_score=risk_score.score,
            risk_level=risk_score.risk_level,
            reason=self._generate_reason(risk_score, action),
            auto_executed=action in (DecisionAction.ALLOW, DecisionAction.MONITOR),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        # Audit log
        self.audit_log.append({
            "decision_id": decision.decision_id,
            "player_id": decision.player_id,
            "action": decision.action.value,
            "risk_score": decision.risk_score,
            "model_version": risk_score.model_version,
            "model_status": risk_score.model_status.value,
            "jurisdiction": jurisdiction,
            "timestamp": decision.timestamp,
        })

        return decision

    def _generate_reason(self, risk_score: RiskScore, action: DecisionAction) -> str:
        reasons = {
            DecisionAction.ALLOW: "Risk score within acceptable range",
            DecisionAction.MONITOR: "Elevated risk indicators - passive monitoring activated",
            DecisionAction.REVIEW: "High risk score requires manual review by compliance team",
            DecisionAction.BLOCK: "Critical risk level - automatic block pending investigation",
            DecisionAction.INTERVENE: "Responsible gambling intervention triggered",
        }
        base = reasons.get(action, "Unknown action")

        # Add top contributing features
        if risk_score.feature_importances:
            top_features = sorted(
                risk_score.feature_importances.items(),
                key=lambda x: x[1],
                reverse=True,
            )[:3]
            feature_str = ", ".join(f"{f[0]}={f[1]:.3f}" for f in top_features)
            base += f". Top factors: {feature_str}"

        return base


# ---------------------------------------------------------------------------
# Pipeline orchestrator
# ---------------------------------------------------------------------------

class RealTimeInferencePipeline:
    """
    Orchestrates the full inference pipeline:
    Event -> Feature Store -> Model Server -> Decision Engine

    Production deployment:
        - Feature store: Redis + Feast
        - Model server: ONNX Runtime behind Triton Inference Server
        - Decision engine: gRPC microservice
        - Total target latency: <50ms p99
    """

    def __init__(self, model_version: str = "v3.2.1"):
        self.feature_store = FeatureStore()
        self.model_server = ModelServer(model_version)
        self.decision_engine = DecisionEngine()
        self._total_requests = 0
        self._latency_samples: deque = deque(maxlen=1000)

    def ingest_event(self, event: PlayerEvent):
        """Ingest player event into feature store."""
        self.feature_store.ingest_event(event)

    def score_player(
        self,
        player_id: str,
        jurisdiction: str = "default",
        player_context: Optional[dict] = None,
    ) -> RiskDecision:
        """
        Full pipeline: compute features -> run model -> make decision.
        This is the main entry point called on every risk check.
        """
        start = time.monotonic()
        self._total_requests += 1

        # Step 1: Compute features
        features = self.feature_store.compute_features(player_id)

        # Step 2: Run model inference
        risk_score = self.model_server.predict(features)

        # Step 3: Make decision
        decision = self.decision_engine.decide(risk_score, jurisdiction, player_context)

        # Track total latency
        total_ms = (time.monotonic() - start) * 1000
        decision.total_latency_ms = round(total_ms, 3)
        self._latency_samples.append(total_ms)

        return decision

    def get_pipeline_metrics(self) -> dict:
        """Get pipeline performance metrics."""
        latencies = list(self._latency_samples)
        if not latencies:
            return {"total_requests": 0}

        latencies.sort()
        p50 = latencies[len(latencies) // 2]
        p95 = latencies[int(len(latencies) * 0.95)]
        p99 = latencies[int(len(latencies) * 0.99)]

        decisions = self.decision_engine.audit_log
        action_counts = defaultdict(int)
        for d in decisions:
            action_counts[d["action"]] += 1

        return {
            "total_requests": self._total_requests,
            "model_status": self.model_server.status.value,
            "model_version": self.model_server.model_version,
            "latency_p50_ms": round(p50, 2),
            "latency_p95_ms": round(p95, 2),
            "latency_p99_ms": round(p99, 2),
            "action_distribution": dict(action_counts),
            "audit_log_size": len(decisions),
        }


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def demo():
    """Simulate real-time risk scoring pipeline."""
    random.seed(42)

    pipeline = RealTimeInferencePipeline()

    print("\n" + "=" * 70)
    print("  Real-Time ML Inference Pipeline - Player Risk Scoring")
    print("=" * 70)

    # Set up batch and static features for test players
    players = {
        "PLR-001": {
            "batch": {"avg_daily_loss_30d": 50, "deposit_frequency_30d": 5},
            "static": {"days_since_registration": 365, "has_deposit_limit": 1},
            "jurisdiction": "UK",
        },
        "PLR-002": {
            "batch": {"avg_daily_loss_30d": 500, "deposit_frequency_30d": 20},
            "static": {"days_since_registration": 30, "has_deposit_limit": 0},
            "jurisdiction": "UK",
        },
        "PLR-003": {
            "batch": {"avg_daily_loss_30d": 15, "deposit_frequency_30d": 2},
            "static": {"days_since_registration": 800, "has_deposit_limit": 1},
            "jurisdiction": "Malta",
        },
    }

    for pid, config in players.items():
        pipeline.feature_store.set_batch_features(pid, config["batch"])  # ty:ignore[invalid-argument-type]
        pipeline.feature_store.set_static_features(pid, config["static"])  # ty:ignore[invalid-argument-type]

    # Simulate events
    print("\n  Simulating player events...")

    # PLR-001: normal casual player
    for i in range(20):
        pipeline.ingest_event(PlayerEvent(
            event_id=f"E-001-{i}",
            player_id="PLR-001",
            event_type="bet",
            amount=round(random.uniform(1, 10), 2),
            game_id=random.choice(["slots-a", "slots-b", "roulette"]),
            timestamp=f"2026-03-08T20:{i:02d}:00Z",
        ))

    # PLR-002: high-risk behavior (chasing losses, increasing bets)
    for i in range(50):
        bet_size = 20 + i * 3 + random.uniform(-5, 5)  # escalating bets
        pipeline.ingest_event(PlayerEvent(
            event_id=f"E-002-{i}",
            player_id="PLR-002",
            event_type="bet",
            amount=round(bet_size, 2),
            game_id="slots-a",  # single game fixation
            timestamp=f"2026-03-08T20:{i % 60:02d}:00Z",
        ))
    # Multiple deposits
    for i in range(4):
        pipeline.ingest_event(PlayerEvent(
            event_id=f"E-002-D{i}",
            player_id="PLR-002",
            event_type="deposit",
            amount=500,
            timestamp=f"2026-03-08T20:{i * 15:02d}:00Z",
        ))

    # PLR-003: low activity
    for i in range(5):
        pipeline.ingest_event(PlayerEvent(
            event_id=f"E-003-{i}",
            player_id="PLR-003",
            event_type="bet",
            amount=round(random.uniform(2, 8), 2),
            game_id=random.choice(["blackjack", "roulette", "baccarat"]),
            timestamp=f"2026-03-08T20:{i * 10:02d}:00Z",
        ))

    # Score each player
    print("\n  Scoring players...\n")
    for pid, config in players.items():
        decision = pipeline.score_player(
            pid,
            jurisdiction=config["jurisdiction"],  # ty:ignore[invalid-argument-type]
        )

        status = {
            DecisionAction.ALLOW: "PASS",
            DecisionAction.MONITOR: "WARN",
            DecisionAction.REVIEW: "REVW",
            DecisionAction.BLOCK: "BLCK",
            DecisionAction.INTERVENE: "INTV",
        }.get(decision.action, "????")

        print(f"  [{status}] {decision.player_id} | "
              f"Score: {decision.risk_score:.3f} ({decision.risk_level.value}) | "
              f"Action: {decision.action.value} | "
              f"Latency: {decision.total_latency_ms:.2f}ms")
        print(f"         {decision.reason[:90]}...")

    # Bulk scoring for latency benchmark
    print(f"\n  Running bulk scoring benchmark (1000 requests)...")
    for _ in range(1000):
        pid = random.choice(list(players.keys()))
        pipeline.score_player(pid, jurisdiction=players[pid]["jurisdiction"])  # ty:ignore[invalid-argument-type]

    metrics = pipeline.get_pipeline_metrics()
    print(f"\n  Pipeline Metrics:")
    print(f"    Total requests: {metrics['total_requests']}")
    print(f"    Model: {metrics['model_version']} ({metrics['model_status']})")
    print(f"    Latency p50: {metrics['latency_p50_ms']:.2f}ms | "
          f"p95: {metrics['latency_p95_ms']:.2f}ms | "
          f"p99: {metrics['latency_p99_ms']:.2f}ms")
    print(f"    Decisions: {metrics['action_distribution']}")

    print(f"\n  Production architecture:")
    print("    Kafka -> Flink feature computation -> Redis feature store")
    print("    -> Triton Inference Server (ONNX) -> Decision gRPC service")
    print("    -> Action execution (block/intervene) + Audit log (S3/BigQuery)\n")


if __name__ == "__main__":
    demo()
