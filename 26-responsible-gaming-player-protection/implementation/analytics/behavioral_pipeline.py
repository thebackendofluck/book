# Companion code for "The Backend of Luck" - Chapter 26, Responsible Gaming and Player Protection Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Behavioral Analytics Pipeline
Chapter 10 - Responsible Gaming and Player Protection

Real-time behavioral analytics pipeline that ingests player events from Kafka,
extracts behavioral features, and feeds them into the risk scoring engine
for early intervention.

Compliance References:
- UKGC LCCP 3.4.1: Operators must identify customers at risk using behavioral data
- MGA PPD 2018: Automated monitoring systems required
- UKGC Guidance: "Operators should use algorithms to detect harmful play patterns"

Architecture:
    Game Events (Kafka) --> Feature Extraction --> Feature Store (Redis)
                                               --> Risk Score Update
                                               --> Anomaly Detection
                                               --> Intervention Trigger

    Event types consumed:
    - bet_placed, bet_settled, bet_cashed_out
    - deposit_completed, withdrawal_requested, withdrawal_reversed
    - session_started, session_ended
    - game_launched, game_closed
    - limit_changed, reality_check_acknowledged

Usage:
    pipeline = BehavioralPipeline(kafka_config, redis, db_pool)
    await pipeline.start()  # Run as long-lived service
"""

import asyncio
import json
import logging
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import asyncpg  # ty:ignore[unresolved-import]
import redis.asyncio as aioredis

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class PipelineConfig:
    """Pipeline configuration."""
    # Kafka settings
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_group_id: str = "rg-behavioral-analytics"
    kafka_topics: list[str] = field(default_factory=lambda: [
        "player.bets",
        "player.deposits",
        "player.sessions",
        "player.games",
        "player.limits",
    ])

    # Feature computation windows
    short_window_minutes: int = 30      # Recent activity window
    medium_window_minutes: int = 360    # 6-hour window
    long_window_hours: int = 168        # 7-day window

    # Anomaly detection thresholds
    zscore_threshold: float = 2.5       # Standard deviations for anomaly
    min_history_points: int = 10        # Min data points before anomaly detection

    # Risk score update frequency
    risk_update_interval_seconds: int = 60

    # Feature store TTL
    feature_ttl_hours: int = 720        # 30 days


@dataclass
class PlayerFeatures:
    """Extracted behavioral features for a player."""
    player_id: str
    computed_at: datetime

    # Session features
    current_session_duration_min: float = 0.0
    sessions_today: int = 0
    avg_session_duration_7d: float = 0.0
    late_night_sessions_7d: int = 0

    # Betting features
    bets_last_30min: int = 0
    bets_last_6h: int = 0
    avg_stake_30min: float = 0.0
    avg_stake_7d: float = 0.0
    stake_variance_30min: float = 0.0
    max_stake_30min: float = 0.0
    loss_streak_current: int = 0
    win_rate_30min: float = 0.0
    win_rate_7d: float = 0.0
    stake_increase_after_loss: bool = False
    bet_frequency_per_min: float = 0.0

    # Financial features
    deposits_24h: int = 0
    deposit_total_24h: float = 0.0
    deposit_velocity_ratio: float = 0.0  # vs 30-day average
    net_loss_session: float = 0.0
    net_loss_24h: float = 0.0
    withdrawal_reversals_30d: int = 0

    # Game features
    game_switches_1h: int = 0           # Switching games frequently
    high_volatility_game_time_pct: float = 0.0

    # Anomaly flags
    anomalies: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Feature Extractors
# ---------------------------------------------------------------------------

class FeatureExtractor:
    """
    Extracts behavioral features from raw player events.
    Features are designed based on gambling harm research:

    Key indicators (Braverman & Shaffer, 2012):
    - Increasing bet frequency
    - Increasing bet size
    - Chasing losses (larger bets after losses)
    - Session duration increases
    - Time-of-day shifts (late night)
    - Game type switching (seeking "luckier" game)
    """

    def __init__(self, redis: aioredis.Redis, config: PipelineConfig):
        self.redis = redis
        self.config = config

    async def extract_features(self, player_id: str) -> PlayerFeatures:
        """Compute all features for a player from the event stream data."""
        features = PlayerFeatures(
            player_id=player_id,
            computed_at=datetime.now(timezone.utc),
        )

        # Fetch raw events from Redis sorted sets
        bets = await self._get_events(player_id, "bets", hours=168)
        deposits = await self._get_events(player_id, "deposits", hours=24)
        sessions = await self._get_events(player_id, "sessions", hours=168)

        now = datetime.now(timezone.utc)

        # --- Session features ---
        if sessions:
            today_sessions = [
                s for s in sessions
                if datetime.fromisoformat(s["timestamp"]).date() == now.date()
            ]
            features.sessions_today = len(today_sessions)

            durations = [
                s.get("duration_minutes", 0) for s in sessions
                if s.get("duration_minutes")
            ]
            if durations:
                features.avg_session_duration_7d = statistics.mean(durations)

            features.late_night_sessions_7d = sum(
                1 for s in sessions
                if 2 <= datetime.fromisoformat(s["timestamp"]).hour < 5
            )

            # Current session
            active = [s for s in sessions if s.get("status") == "active"]
            if active:
                started = datetime.fromisoformat(active[0]["timestamp"])
                features.current_session_duration_min = (now - started).total_seconds() / 60

        # --- Betting features ---
        if bets:
            cutoff_30m = now - timedelta(minutes=30)
            cutoff_6h = now - timedelta(hours=6)

            recent_bets = [
                b for b in bets
                if datetime.fromisoformat(b["timestamp"]) >= cutoff_30m
            ]
            medium_bets = [
                b for b in bets
                if datetime.fromisoformat(b["timestamp"]) >= cutoff_6h
            ]

            features.bets_last_30min = len(recent_bets)
            features.bets_last_6h = len(medium_bets)

            if recent_bets:
                stakes = [b.get("stake", 0) for b in recent_bets]
                features.avg_stake_30min = statistics.mean(stakes)
                features.max_stake_30min = max(stakes)
                if len(stakes) > 1:
                    features.stake_variance_30min = statistics.variance(stakes)

                wins = sum(1 for b in recent_bets if b.get("result") == "win")
                features.win_rate_30min = wins / len(recent_bets)

                # Bet frequency
                if features.current_session_duration_min > 0:
                    features.bet_frequency_per_min = (
                        len(recent_bets) / min(30, features.current_session_duration_min)
                    )

            # 7-day averages
            all_stakes = [b.get("stake", 0) for b in bets if b.get("stake")]
            if all_stakes:
                features.avg_stake_7d = statistics.mean(all_stakes)
                all_wins = sum(1 for b in bets if b.get("result") == "win")
                features.win_rate_7d = all_wins / len(bets)

            # Loss streak detection
            features.loss_streak_current = self._current_loss_streak(bets)

            # Loss chasing detection
            features.stake_increase_after_loss = self._detect_loss_chasing(bets)

        # --- Financial features ---
        if deposits:
            features.deposits_24h = len(deposits)
            features.deposit_total_24h = sum(d.get("amount", 0) for d in deposits)

        # Deposit velocity ratio
        avg_daily = await self._get_avg_daily_deposit(player_id)
        if avg_daily > 0:
            features.deposit_velocity_ratio = features.deposit_total_24h / avg_daily

        # Net loss
        features.net_loss_session = await self._get_session_net_loss(player_id)
        features.net_loss_24h = await self._get_24h_net_loss(player_id)

        # Withdrawal reversals
        features.withdrawal_reversals_30d = await self._get_withdrawal_reversals(player_id)

        # --- Game features ---
        game_events = await self._get_events(player_id, "games", hours=1)
        if game_events:
            unique_games = set(g.get("game_id") for g in game_events)
            features.game_switches_1h = max(0, len(unique_games) - 1)

        # --- Anomaly detection ---
        features.anomalies = await self._detect_anomalies(player_id, features)

        # Store computed features
        await self._store_features(features)

        return features

    def _current_loss_streak(self, bets: list[dict]) -> int:
        """Count consecutive losses from most recent bet."""
        streak = 0
        for bet in bets:  # Assumes sorted newest first
            if bet.get("result") == "loss":
                streak += 1
            else:
                break
        return streak

    def _detect_loss_chasing(self, bets: list[dict]) -> bool:
        """
        Detect if player increases stake after consecutive losses.
        Braverman & Shaffer (2012): stake escalation after losses is
        the strongest single predictor of future problem gambling.
        """
        if len(bets) < 4:
            return False

        # Look at the most recent sequence
        for i in range(len(bets) - 2):
            if (
                bets[i + 1].get("result") == "loss"
                and bets[i + 2].get("result") == "loss"
                and bets[i].get("stake", 0) > bets[i + 1].get("stake", 0) * 1.5
            ):
                return True
        return False

    async def _detect_anomalies(
        self, player_id: str, features: PlayerFeatures
    ) -> list[str]:
        """
        Statistical anomaly detection using z-scores against player's own history.
        Flag features that deviate significantly from the player's baseline.
        """
        anomalies = []
        history_key = f"rg:feature_history:{player_id}"
        raw = await self.redis.lrange(history_key, 0, 99)  # ty:ignore[invalid-await]

        if len(raw) < self.config.min_history_points:
            return anomalies

        history = [json.loads(h) for h in raw]

        # Check key metrics for anomalies
        checks = [
            ("avg_stake_30min", features.avg_stake_30min, "stake_anomaly"),
            ("bets_last_30min", features.bets_last_30min, "bet_frequency_anomaly"),
            ("deposit_total_24h", features.deposit_total_24h, "deposit_anomaly"),
        ]

        for metric_name, current_value, anomaly_name in checks:
            hist_values = [h.get(metric_name, 0) for h in history if h.get(metric_name) is not None]
            if len(hist_values) < self.config.min_history_points:
                continue
            mean = statistics.mean(hist_values)
            stdev = statistics.stdev(hist_values) if len(hist_values) > 1 else 0
            if stdev > 0 and current_value > 0:
                zscore = (current_value - mean) / stdev
                if zscore > self.config.zscore_threshold:
                    anomalies.append(f"{anomaly_name}:zscore={zscore:.2f}")

        return anomalies

    async def _store_features(self, features: PlayerFeatures) -> None:
        """Store features in Redis for real-time access and history."""
        # Current features (overwrite)
        current_key = f"rg:features:{features.player_id}"
        feature_dict = {
            k: v for k, v in features.__dict__.items()
            if k not in ("player_id", "computed_at", "anomalies")
        }
        feature_dict["computed_at"] = features.computed_at.isoformat()
        feature_dict["anomalies"] = features.anomalies

        await self.redis.set(
            current_key,
            json.dumps(feature_dict),
            ex=self.config.feature_ttl_hours * 3600,
        )

        # Append to history for anomaly detection baseline
        history_key = f"rg:feature_history:{features.player_id}"
        await self.redis.lpush(history_key, json.dumps(feature_dict))  # ty:ignore[invalid-await]
        await self.redis.ltrim(history_key, 0, 99)  # Keep last 100 snapshots  # ty:ignore[invalid-await]
        await self.redis.expire(history_key, self.config.feature_ttl_hours * 3600)

    async def _get_events(
        self, player_id: str, event_type: str, hours: int
    ) -> list[dict]:
        """Fetch recent events from Redis sorted set."""
        key = f"rg:events:{player_id}:{event_type}"
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp()
        raw = await self.redis.zrangebyscore(key, cutoff, "+inf")
        return [json.loads(r) for r in raw]

    async def _get_avg_daily_deposit(self, player_id: str) -> float:
        cached = await self.redis.get(f"rg:avg_deposit:{player_id}")
        return float(cached) if cached else 0.0

    async def _get_session_net_loss(self, player_id: str) -> float:
        cached = await self.redis.hget(f"rg:session:{player_id}", "net_loss")  # ty:ignore[invalid-await]
        return float(cached) if cached else 0.0

    async def _get_24h_net_loss(self, player_id: str) -> float:
        cached = await self.redis.get(f"rg:net_loss_24h:{player_id}")
        return float(cached) if cached else 0.0

    async def _get_withdrawal_reversals(self, player_id: str) -> int:
        cached = await self.redis.get(f"rg:wd_reversals:{player_id}")
        return int(cached) if cached else 0


# ---------------------------------------------------------------------------
# Kafka Consumer (Event Ingestion)
# ---------------------------------------------------------------------------

class BehavioralPipeline:
    """
    Main pipeline: consumes events from Kafka, stores them, and triggers
    feature extraction and risk scoring.
    """

    def __init__(
        self,
        config: PipelineConfig,
        redis: aioredis.Redis,
        db_pool: asyncpg.Pool,
    ):
        self.config = config
        self.redis = redis
        self.db = db_pool
        self.extractor = FeatureExtractor(redis, config)
        self._running = False

    async def start(self) -> None:
        """Start the Kafka consumer loop."""
        from aiokafka import AIOKafkaConsumer  # ty:ignore[unresolved-import]

        self._running = True
        consumer = AIOKafkaConsumer(
            *self.config.kafka_topics,
            bootstrap_servers=self.config.kafka_bootstrap_servers,
            group_id=self.config.kafka_group_id,
            value_deserializer=lambda m: json.loads(m.decode()),
            auto_offset_reset="latest",
            enable_auto_commit=True,
        )

        await consumer.start()
        logger.info(
            "Behavioral pipeline started, consuming topics: %s",
            self.config.kafka_topics,
        )

        # Background task for periodic feature computation
        feature_task = asyncio.create_task(self._periodic_feature_computation())

        try:
            async for message in consumer:
                if not self._running:
                    break
                try:
                    await self._process_event(message.topic, message.value)
                except Exception:
                    logger.exception("Error processing event from %s", message.topic)
        finally:
            await consumer.stop()
            feature_task.cancel()

    async def stop(self) -> None:
        self._running = False

    async def _process_event(self, topic: str, event: dict) -> None:
        """Process a single event: store it and update counters."""
        player_id = event.get("player_id")
        if not player_id:
            return

        timestamp = event.get("timestamp", datetime.now(timezone.utc).isoformat())
        event["timestamp"] = timestamp
        score = datetime.fromisoformat(timestamp).timestamp()

        # Determine event type from topic
        event_type_map = {
            "player.bets": "bets",
            "player.deposits": "deposits",
            "player.sessions": "sessions",
            "player.games": "games",
            "player.limits": "limits",
        }
        event_type = event_type_map.get(topic, "unknown")

        # Store event in Redis sorted set (score = timestamp)
        key = f"rg:events:{player_id}:{event_type}"
        await self.redis.zadd(key, {json.dumps(event): score})

        # Trim old events (keep 7 days)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).timestamp()
        await self.redis.zremrangebyscore(key, 0, cutoff)

        # Set TTL on the key
        await self.redis.expire(key, 7 * 86400)

        # Update real-time counters
        if event_type == "bets":
            await self._update_bet_counters(player_id, event)
        elif event_type == "deposits":
            await self._update_deposit_counters(player_id, event)

        # Check if immediate risk evaluation needed
        if await self._should_evaluate_immediately(player_id, event):
            asyncio.create_task(self._trigger_risk_evaluation(player_id))

    async def _update_bet_counters(self, player_id: str, event: dict) -> None:
        """Update real-time betting counters."""
        pipe = self.redis.pipeline()

        if event.get("result") == "loss":
            pipe.incr(f"rg:loss_streak:{player_id}")
            loss_amount = event.get("stake", 0) - event.get("returns", 0)
            pipe.incrbyfloat(f"rg:net_loss_24h:{player_id}", loss_amount)
        else:
            pipe.set(f"rg:loss_streak:{player_id}", 0)
            win_amount = event.get("returns", 0) - event.get("stake", 0)
            pipe.incrbyfloat(f"rg:net_loss_24h:{player_id}", -win_amount)

        # Set 24h expiry on counters
        pipe.expire(f"rg:loss_streak:{player_id}", 86400)
        pipe.expire(f"rg:net_loss_24h:{player_id}", 86400)

        await pipe.execute()

    async def _update_deposit_counters(self, player_id: str, event: dict) -> None:
        """Track deposit frequency and amount."""
        pipe = self.redis.pipeline()
        pipe.incr(f"rg:deposit_count_24h:{player_id}")
        pipe.expire(f"rg:deposit_count_24h:{player_id}", 86400)
        amount = event.get("amount", 0)
        pipe.incrbyfloat(f"rg:deposit_total_24h:{player_id}", amount)
        pipe.expire(f"rg:deposit_total_24h:{player_id}", 86400)
        await pipe.execute()

    async def _should_evaluate_immediately(
        self, player_id: str, event: dict
    ) -> bool:
        """
        Determine if an event warrants immediate risk re-evaluation.
        Most events are batched; some trigger immediate scoring.
        """
        # Immediate evaluation triggers:
        # 1. Large deposit (> 2x average)
        if event.get("event_type") == "deposit":
            avg = await self.redis.get(f"rg:avg_deposit:{player_id}")
            if avg and event.get("amount", 0) > float(avg) * 2:
                return True

        # 2. Long loss streak
        streak = await self.redis.get(f"rg:loss_streak:{player_id}")
        if streak and int(streak) >= 5:
            return True

        # 3. Withdrawal reversal
        if event.get("event_type") == "withdrawal_reversed":
            return True

        return False

    async def _trigger_risk_evaluation(self, player_id: str) -> None:
        """Trigger an immediate risk score computation."""
        await self.redis.publish(
            "rg:evaluate_risk",
            json.dumps({
                "player_id": player_id,
                "trigger": "behavioral_pipeline",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }),
        )

    async def _periodic_feature_computation(self) -> None:
        """Periodically recompute features for active players."""
        while self._running:
            try:
                # Get all players with recent events
                active_players = set()
                async for key in self.redis.scan_iter("rg:events:*:bets"):
                    key_str = key.decode() if isinstance(key, bytes) else key
                    parts = key_str.split(":")
                    if len(parts) >= 3:
                        active_players.add(parts[2])

                for player_id in active_players:
                    try:
                        features = await self.extractor.extract_features(player_id)
                        if features.anomalies:
                            logger.info(
                                "Anomalies detected for %s: %s",
                                player_id, features.anomalies,
                            )
                            await self._trigger_risk_evaluation(player_id)
                    except Exception:
                        logger.exception(
                            "Feature extraction failed for %s", player_id
                        )

            except Exception:
                logger.exception("Periodic feature computation error")

            await asyncio.sleep(self.config.risk_update_interval_seconds)
