# Companion code for "The Backend of Luck" - Chapter 19, Anti-Fraud System Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Feature Store for Fraud Detection
==================================

Dual-layer feature store supporting both real-time and batch feature access:

  Real-time layer (Redis):
    - Sub-millisecond lookups for online scoring
    - Player profiles, velocity counters, session state
    - TTL-based expiration for sliding window features
    - Used during live transaction scoring

  Batch layer (PostgreSQL/Parquet):
    - Historical feature snapshots for model training
    - Point-in-time correct feature retrieval (prevents data leakage)
    - Feature versioning and lineage tracking
    - Used during model training and backtesting

Why a feature store matters for fraud detection:
  - Training/serving skew is the #1 cause of model degradation
  - Features must be identical at training time and serving time
  - Temporal features need point-in-time correctness to avoid look-ahead bias
  - Regulatory audits require feature lineage (what features scored this transaction)
"""

import json
import logging
import time
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional, Any

import polars as pl  # ty:ignore[unresolved-import]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fraud.feature_store")


# =============================================================================
# Configuration
# =============================================================================

@dataclass
class FeatureStoreConfig:
    """Configuration for the feature store."""
    # Redis (real-time layer)
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: Optional[str] = None
    redis_key_prefix: str = "fraud:features:"
    redis_default_ttl_seconds: int = 86400  # 24h default TTL

    # PostgreSQL (batch layer)
    postgres_dsn: str = "postgresql://fraud:fraud@localhost:5432/fraud_features"

    # Parquet (offline storage)
    parquet_base_path: str = "/data/feature_store/snapshots"

    # Feature versioning
    feature_version: str = "v1"


# =============================================================================
# Real-Time Feature Store (Redis-backed)
# =============================================================================

class RealTimeFeatureStore:
    """
    Redis-backed feature store for sub-millisecond feature lookups during scoring.

    Data model in Redis:
      - Player profile: HASH  fraud:features:profile:{player_id}
      - Velocity counters: HASH fraud:features:velocity:{player_id}
      - Session state: HASH  fraud:features:session:{session_id}
      - Sliding windows: SORTED SET fraud:features:window:{player_id}:{feature}
    """

    def __init__(self, config: FeatureStoreConfig):
        self.config = config
        self._prefix = config.redis_key_prefix
        try:
            import redis
            self._redis = redis.Redis(
                host=config.redis_host,
                port=config.redis_port,
                db=config.redis_db,
                password=config.redis_password,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=2,
                retry_on_timeout=True,
            )
            self._redis.ping()
            logger.info("Redis feature store connected: %s:%d", config.redis_host, config.redis_port)
        except ImportError:
            logger.warning("Redis not available. Using in-memory fallback.")
            self._redis = None
            self._memory_store: dict[str, dict] = {}

    def _key(self, *parts: str) -> str:
        return self._prefix + ":".join(parts)

    # -------------------------------------------------------------------------
    # Player Profile Features
    # -------------------------------------------------------------------------

    def update_player_profile(self, player_id: str, features: dict[str, Any]) -> None:
        """
        Update player's feature profile. Features are stored as a Redis HASH
        for O(1) individual field access during scoring.

        Args:
            player_id: Unique player identifier
            features: Dict of feature_name -> value
        """
        key = self._key("profile", player_id)
        features["_updated_at"] = datetime.now(timezone.utc).isoformat()
        features["_version"] = self.config.feature_version

        if self._redis:
            pipeline = self._redis.pipeline()
            pipeline.hset(key, mapping={k: json.dumps(v) for k, v in features.items()})
            pipeline.expire(key, self.config.redis_default_ttl_seconds)
            pipeline.execute()
        else:
            self._memory_store[key] = features

    def get_player_profile(self, player_id: str) -> dict[str, Any]:
        """
        Retrieve player's complete feature profile for model scoring.

        Returns:
            Dict of feature_name -> value. Empty dict if player not found.
        """
        key = self._key("profile", player_id)

        if self._redis:
            raw = self._redis.hgetall(key)
            if not raw:
                return {}
            return {k: json.loads(v) for k, v in raw.items()}  # ty:ignore[unresolved-attribute]
        else:
            return self._memory_store.get(key, {})

    def get_player_features_batch(self, player_ids: list[str]) -> dict[str, dict]:
        """
        Batch retrieve features for multiple players (for batch scoring).

        Uses Redis pipeline for efficient batch access.
        """
        if self._redis:
            pipeline = self._redis.pipeline()
            for pid in player_ids:
                pipeline.hgetall(self._key("profile", pid))
            results = pipeline.execute()

            return {
                pid: {k: json.loads(v) for k, v in raw.items()} if raw else {}
                for pid, raw in zip(player_ids, results)
            }
        else:
            return {
                pid: self._memory_store.get(self._key("profile", pid), {})
                for pid in player_ids
            }

    # -------------------------------------------------------------------------
    # Velocity Counters (Sliding Windows)
    # -------------------------------------------------------------------------

    def increment_velocity(
        self,
        player_id: str,
        counter_name: str,
        amount: float = 1.0,
        window_seconds: int = 3600,
    ) -> float:
        """
        Increment a sliding window velocity counter.

        Uses Redis SORTED SET with timestamp scores for O(log N) window queries.
        Members outside the window are automatically pruned.

        Example counters:
          - tx_count_1h: Number of transactions in last hour
          - deposit_amount_24h: Total deposit volume in 24 hours
          - failed_auth_1h: Failed login attempts in last hour

        Args:
            player_id: Player identifier
            counter_name: Name of the velocity counter
            amount: Value to add (1.0 for count, or actual amount for sums)
            window_seconds: Sliding window size in seconds

        Returns:
            Current counter value after increment
        """
        key = self._key("velocity", player_id, counter_name)
        now = time.time()
        window_start = now - window_seconds

        if self._redis:
            pipeline = self._redis.pipeline()
            # Add new entry with timestamp as score
            member = f"{now}:{amount}"
            pipeline.zadd(key, {member: now})
            # Remove entries outside the window
            pipeline.zremrangebyscore(key, "-inf", window_start)
            # Get all entries in window
            pipeline.zrangebyscore(key, window_start, "+inf")
            # Set TTL slightly longer than window
            pipeline.expire(key, window_seconds + 60)
            results = pipeline.execute()

            # Sum all values in window
            entries = results[2]
            total = sum(float(e.split(":")[1]) for e in entries)
            return total
        else:
            if key not in self._memory_store:
                self._memory_store[key] = []  # ty:ignore[invalid-assignment]
            self._memory_store[key].append((now, amount))  # ty:ignore[unresolved-attribute]
            # Prune old entries
            self._memory_store[key] = [
                (ts, val) for ts, val in self._memory_store[key]
                if ts > window_start
            ]  # ty:ignore[invalid-assignment]
            return sum(val for _, val in self._memory_store[key])

    def get_velocity(
        self,
        player_id: str,
        counter_name: str,
        window_seconds: int = 3600,
    ) -> float:
        """Get current velocity counter value without incrementing."""
        key = self._key("velocity", player_id, counter_name)
        window_start = time.time() - window_seconds

        if self._redis:
            entries = self._redis.zrangebyscore(key, window_start, "+inf")
            return sum(float(e.split(":")[1]) for e in entries)  # ty:ignore[not-iterable]
        else:
            entries = self._memory_store.get(key, [])
            return sum(val for ts, val in entries if ts > window_start)

    # -------------------------------------------------------------------------
    # Session State
    # -------------------------------------------------------------------------

    def update_session_state(
        self,
        session_id: str,
        state: dict[str, Any],
        ttl_seconds: int = 7200,  # 2h session TTL
    ) -> None:
        """Store session-level state (aggregated features within a session)."""
        key = self._key("session", session_id)
        state["_updated_at"] = datetime.now(timezone.utc).isoformat()

        if self._redis:
            pipeline = self._redis.pipeline()
            pipeline.hset(key, mapping={k: json.dumps(v) for k, v in state.items()})
            pipeline.expire(key, ttl_seconds)
            pipeline.execute()
        else:
            self._memory_store[key] = state

    def get_session_state(self, session_id: str) -> dict[str, Any]:
        """Retrieve session-level state."""
        key = self._key("session", session_id)
        if self._redis:
            raw = self._redis.hgetall(key)
            return {k: json.loads(v) for k, v in raw.items()} if raw else {}  # ty:ignore[unresolved-attribute]
        else:
            return self._memory_store.get(key, {})


# =============================================================================
# Batch Feature Store (for training data)
# =============================================================================

class BatchFeatureStore:
    """
    Offline feature store for model training with point-in-time correct lookups.

    Stores feature snapshots as partitioned Parquet files:
      /data/feature_store/snapshots/
        feature_version=v1/
          date=2024-01-15/
            player_features.parquet
            session_features.parquet

    Point-in-time correctness:
      When training a model, features must reflect what was known AT THE TIME
      of each transaction, not future information. This prevents data leakage
      that causes models to perform well in training but fail in production.
    """

    def __init__(self, config: FeatureStoreConfig):
        self.config = config
        self._base_path = config.parquet_base_path

    def save_feature_snapshot(
        self,
        features_df: pl.DataFrame,
        feature_group: str,
        snapshot_date: Optional[datetime] = None,
    ) -> str:
        """
        Save a feature snapshot as Parquet with partitioning.

        Args:
            features_df: DataFrame with computed features
            feature_group: Name (e.g., 'player_features', 'session_features')
            snapshot_date: Date for this snapshot

        Returns:
            Path where snapshot was saved
        """
        if snapshot_date is None:
            snapshot_date = datetime.now(timezone.utc)

        date_str = snapshot_date.strftime("%Y-%m-%d")
        path = (
            f"{self._base_path}/"
            f"feature_version={self.config.feature_version}/"
            f"date={date_str}/"
            f"{feature_group}.parquet"
        )

        # Add metadata columns
        features_df = features_df.with_columns([
            pl.lit(self.config.feature_version).alias("_feature_version"),
            pl.lit(snapshot_date.isoformat()).alias("_snapshot_time"),
            pl.lit(feature_group).alias("_feature_group"),
        ])

        features_df.write_parquet(path, compression="zstd")
        logger.info(
            "Saved feature snapshot: %s (%d rows, %d cols)",
            path, features_df.height, features_df.width,
        )
        return path

    def load_features_for_training(
        self,
        feature_group: str,
        start_date: datetime,
        end_date: datetime,
        player_ids: Optional[list[str]] = None,
    ) -> pl.DataFrame:
        """
        Load feature snapshots for a date range (point-in-time correct).

        This ensures training data uses features as they existed at each
        transaction time, not current feature values.

        Args:
            feature_group: Feature group name
            start_date: Start of training window
            end_date: End of training window
            player_ids: Optional filter for specific players

        Returns:
            DataFrame with historical features
        """
        path_pattern = (
            f"{self._base_path}/"
            f"feature_version={self.config.feature_version}/"
            f"date=*/"
            f"{feature_group}.parquet"
        )

        try:
            df = pl.scan_parquet(path_pattern).filter(
                pl.col("_snapshot_time").str.to_datetime() >= start_date,
                pl.col("_snapshot_time").str.to_datetime() <= end_date,
            )

            if player_ids:
                df = df.filter(pl.col("player_id").is_in(player_ids))

            result = df.collect()
            logger.info(
                "Loaded training features: %d rows from %s to %s",
                result.height,
                start_date.strftime("%Y-%m-%d"),
                end_date.strftime("%Y-%m-%d"),
            )
            return result

        except Exception as e:
            logger.error("Error loading features: %s", e)
            return pl.DataFrame()

    def get_feature_lineage(self, feature_version: str) -> dict:
        """
        Get metadata about a feature version for audit/compliance.

        Returns:
            Dict with feature names, computation logic hash, creation date
        """
        return {
            "feature_version": feature_version,
            "base_path": self._base_path,
            "feature_groups": ["player_features", "session_features", "velocity_features"],
            "description": "Fraud detection features v1: velocity, temporal, behavioral",
            "created_by": "fraud-feature-pipeline",
        }


# =============================================================================
# Unified Feature Store Interface
# =============================================================================

class FraudFeatureStore:
    """
    Unified interface combining real-time and batch feature stores.

    Usage in scoring pipeline:
        store = FraudFeatureStore(config)

        # During real-time scoring:
        features = store.get_scoring_features("player_123", "session_abc")

        # During model training:
        training_data = store.get_training_features(start_date, end_date)
    """

    def __init__(self, config: Optional[FeatureStoreConfig] = None):
        self.config = config or FeatureStoreConfig()
        self.realtime = RealTimeFeatureStore(self.config)
        self.batch = BatchFeatureStore(self.config)

    def get_scoring_features(
        self,
        player_id: str,
        session_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Get all features needed for real-time fraud scoring.

        Combines:
          - Player profile features (historical aggregates)
          - Velocity counters (sliding window counts)
          - Session state (current session aggregates)

        Returns:
            Dict of feature_name -> value ready for model input
        """
        features = {}

        # Player profile
        profile = self.realtime.get_player_profile(player_id)
        features.update(profile)

        # Velocity counters
        velocity_counters = [
            ("tx_count_1h", 3600),
            ("tx_count_24h", 86400),
            ("deposit_amount_1h", 3600),
            ("deposit_amount_24h", 86400),
            ("failed_deposits_1h", 3600),
            ("game_switches_1h", 3600),
            ("unique_ips_24h", 86400),
        ]
        for counter, window in velocity_counters:
            features[counter] = self.realtime.get_velocity(player_id, counter, window)

        # Session state
        if session_id:
            session = self.realtime.get_session_state(session_id)
            features.update({f"session_{k}": v for k, v in session.items()
                            if not k.startswith("_")})

        return features

    def update_on_event(self, event: dict) -> None:
        """
        Update feature store when a new event arrives.

        Called by the feature pipeline after computing features for a new event.
        Updates both player profile and velocity counters.
        """
        player_id = event.get("player_id", "")
        session_id = event.get("session_id", "")
        amount = event.get("amount_eur", 0)
        event_type = event.get("event_type", "")

        # Update velocity counters
        self.realtime.increment_velocity(player_id, "tx_count_1h", 1.0, 3600)
        self.realtime.increment_velocity(player_id, "tx_count_24h", 1.0, 86400)

        if event_type in ("DEPOSIT_COMPLETED", "DEPOSIT_INITIATED"):
            self.realtime.increment_velocity(player_id, "deposit_amount_1h", amount, 3600)
            self.realtime.increment_velocity(player_id, "deposit_amount_24h", amount, 86400)

        if event_type == "DEPOSIT_FAILED":
            self.realtime.increment_velocity(player_id, "failed_deposits_1h", 1.0, 3600)

        # Update session state
        if session_id:
            session = self.realtime.get_session_state(session_id)
            session["event_count"] = session.get("event_count", 0) + 1
            session["total_wagered"] = session.get("total_wagered", 0) + amount
            self.realtime.update_session_state(session_id, session)


# =============================================================================
# Entry point
# =============================================================================

def main():
    """Demo: Feature store operations."""
    config = FeatureStoreConfig()
    store = FraudFeatureStore(config)

    # Simulate updating features for a player
    player_id = "player_0042"

    # Update profile
    store.realtime.update_player_profile(player_id, {
        "lifetime_deposits_eur": 5000.0,
        "lifetime_withdrawals_eur": 3200.0,
        "account_age_days": 180,
        "avg_bet_size_eur": 25.0,
        "preferred_game": "SLOTS",
        "risk_tier": "MEDIUM",
        "chargeback_count": 0,
    })

    # Simulate events
    for i in range(10):
        store.update_on_event({
            "player_id": player_id,
            "session_id": f"session_{player_id}_001",
            "amount_eur": 25.0 + i * 5,
            "event_type": "BET_PLACED",
        })

    # Retrieve scoring features
    features = store.get_scoring_features(player_id, f"session_{player_id}_001")
    logger.info("Scoring features for %s:", player_id)
    for k, v in sorted(features.items()):
        if not k.startswith("_"):
            logger.info("  %s = %s", k, v)


if __name__ == "__main__":
    main()
