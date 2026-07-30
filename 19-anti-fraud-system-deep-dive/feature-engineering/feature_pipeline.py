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
Feature Engineering Pipeline for Fraud Detection
=================================================

Uses Polars for high-performance feature computation across three categories:
  1. Velocity Features   - Transaction rates, bet frequency, session density
  2. Temporal Features   - Time-of-day patterns, day-of-week shifts, seasonality
  3. Behavioral Features - Bet sizing patterns, game switching, session anomalies

Why Polars over Pandas:
  - 5-10x faster for aggregation operations on large datasets
  - Native lazy evaluation for query optimization
  - Zero-copy memory sharing for streaming feature computation
  - Rust-based engine handles 100K+ events/sec without GIL bottleneck

iGaming-Specific Feature Rationale:
  - Velocity: Bots place bets at inhuman speeds; money launderers structure deposits
  - Temporal: Legitimate players have natural patterns; fraudsters operate on schedules
  - Behavioral: Bonus abusers show distinct game selection; colluders have correlated actions
"""

import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import polars as pl  # ty:ignore[unresolved-import]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fraud.features")


# =============================================================================
# Velocity Features
# =============================================================================

def compute_velocity_features(
    events: pl.LazyFrame,
    player_id_col: str = "player_id",
    timestamp_col: str = "timestamp",
    amount_col: str = "amount_eur",
) -> pl.LazyFrame:
    """
    Compute transaction velocity features per player over multiple time windows.

    These features detect:
      - Bot activity: Inhuman transaction rates (>1 bet/sec sustained)
      - Structuring: Many small deposits staying below reporting thresholds
      - Bonus abuse: Rapid bonus claim + wagering + withdrawal cycles
      - Card testing: Multiple small deposits from different cards

    Returns LazyFrame with velocity features joined to original events.
    """
    # Ensure timestamp is in datetime format
    events = events.with_columns(
        pl.col(timestamp_col).cast(pl.Datetime("ms", time_zone="UTC")).alias("_ts")
    )

    # Define time windows for velocity calculation
    windows = [
        ("1m", timedelta(minutes=1)),
        ("5m", timedelta(minutes=5)),
        ("1h", timedelta(hours=1)),
        ("24h", timedelta(hours=24)),
        ("7d", timedelta(days=7)),
        ("30d", timedelta(days=30)),
    ]

    velocity_exprs = []

    for window_name, window_delta in windows:
        window_ms = int(window_delta.total_seconds() * 1000)

        # Transaction count in window
        velocity_exprs.append(
            pl.col(timestamp_col)
            .rolling_count_by(pl.col("_ts"), window_size=f"{int(window_delta.total_seconds())}s")
            .over(player_id_col)
            .alias(f"tx_count_{window_name}")
        )

    # Compute inter-event time (critical for bot detection)
    events = events.sort(player_id_col, "_ts").with_columns([
        # Time between consecutive events for same player (milliseconds)
        (pl.col("_ts").diff().over(player_id_col).dt.total_milliseconds())
        .alias("inter_event_ms"),

        # Rolling statistics on inter-event time
        pl.col("_ts").diff().over(player_id_col).dt.total_milliseconds()
        .rolling_mean(window_size=10)
        .alias("inter_event_mean_10"),

        pl.col("_ts").diff().over(player_id_col).dt.total_milliseconds()
        .rolling_std(window_size=10)
        .alias("inter_event_std_10"),
    ])

    # Amount-based velocity features
    if amount_col:
        events = events.with_columns([
            # Cumulative sum in session (detect rapid wagering)
            pl.col(amount_col).cum_sum().over(player_id_col).alias("cumulative_amount"),

            # Rolling average bet size (sudden changes flag account takeover)
            pl.col(amount_col).rolling_mean(window_size=20).alias("rolling_avg_amount_20"),

            # Rolling max bet (sudden large bets after small ones = exploitation)
            pl.col(amount_col).rolling_max(window_size=50).alias("rolling_max_amount_50"),

            # Coefficient of variation (consistent amounts = bot; high variation = human)
            (
                pl.col(amount_col).rolling_std(window_size=20)
                / pl.col(amount_col).rolling_mean(window_size=20).clip(lower_bound=0.01)
            ).alias("amount_cv_20"),
        ])

    # Bot detection: inter-event time consistency
    # Real humans have high variance; bots are mechanically consistent
    events = events.with_columns([
        pl.when(pl.col("inter_event_std_10") < 50)  # < 50ms std = suspiciously consistent
        .then(pl.lit(1.0))
        .when(pl.col("inter_event_std_10") < 200)
        .then(pl.lit(0.5))
        .otherwise(pl.lit(0.0))
        .alias("bot_timing_score"),
    ])

    logger.info("Computed velocity features for %d events", events.collect().height)
    return events


# =============================================================================
# Temporal Features
# =============================================================================

def compute_temporal_features(
    events: pl.LazyFrame,
    player_id_col: str = "player_id",
    timestamp_col: str = "timestamp",
) -> pl.LazyFrame:
    """
    Compute time-based pattern features.

    Detects:
      - Unusual play times (e.g., professional fraudster working hours)
      - Day-of-week pattern shifts (weekday-only play = possible organized fraud)
      - Session timing anomalies (playing at 4 AM after months of evening play)
      - Holiday/event correlation (fraud spikes during major sporting events)
    """
    events = events.with_columns(
        pl.col(timestamp_col).cast(pl.Datetime("ms", time_zone="UTC")).alias("_ts")
    )

    events = events.with_columns([
        # Basic temporal components
        pl.col("_ts").dt.hour().alias("hour_of_day"),
        pl.col("_ts").dt.weekday().alias("day_of_week"),  # 0=Monday, 6=Sunday
        pl.col("_ts").dt.day().alias("day_of_month"),

        # Time-of-day buckets (for pattern detection)
        pl.when(pl.col("_ts").dt.hour().is_between(6, 11))
        .then(pl.lit("morning"))
        .when(pl.col("_ts").dt.hour().is_between(12, 17))
        .then(pl.lit("afternoon"))
        .when(pl.col("_ts").dt.hour().is_between(18, 23))
        .then(pl.lit("evening"))
        .otherwise(pl.lit("night"))  # 0-5 AM
        .alias("time_bucket"),

        # Weekend flag (different fraud patterns on weekends)
        pl.col("_ts").dt.weekday().is_in([5, 6]).alias("is_weekend"),
    ])

    # Cyclical encoding for hour (so 23:00 and 01:00 are "close")
    # Neural networks handle cyclical features better with sin/cos encoding
    import math
    events = events.with_columns([
        (pl.col("hour_of_day") * 2 * math.pi / 24).sin().alias("hour_sin"),
        (pl.col("hour_of_day") * 2 * math.pi / 24).cos().alias("hour_cos"),
        (pl.col("day_of_week") * 2 * math.pi / 7).sin().alias("dow_sin"),
        (pl.col("day_of_week") * 2 * math.pi / 7).cos().alias("dow_cos"),
    ])

    # Player's historical time pattern deviation
    # If a player usually plays 6-10 PM but suddenly plays at 3 AM, flag it
    events = events.with_columns([
        # Player's mean hour of activity
        pl.col("hour_of_day").mean().over(player_id_col).alias("player_avg_hour"),
        pl.col("hour_of_day").std().over(player_id_col).alias("player_std_hour"),
    ])

    events = events.with_columns([
        # Z-score of current hour vs player's typical pattern
        pl.when(pl.col("player_std_hour") > 0)
        .then(
            (pl.col("hour_of_day") - pl.col("player_avg_hour"))
            / pl.col("player_std_hour")
        )
        .otherwise(pl.lit(0.0))
        .abs()
        .alias("hour_deviation_zscore"),
    ])

    # Session gap: time since player's last activity
    # Long gaps followed by unusual behavior = possible account takeover
    events = events.sort(player_id_col, "_ts").with_columns([
        pl.col("_ts").diff().over(player_id_col).dt.total_hours().alias("hours_since_last_activity"),
    ])

    events = events.with_columns([
        # Flag if player returns after long absence (>7 days)
        pl.when(pl.col("hours_since_last_activity") > 168)
        .then(pl.lit(1.0))
        .when(pl.col("hours_since_last_activity") > 48)
        .then(pl.lit(0.5))
        .otherwise(pl.lit(0.0))
        .alias("long_absence_flag"),
    ])

    logger.info("Computed temporal features")
    return events


# =============================================================================
# Behavioral Features
# =============================================================================

def compute_behavioral_features(
    events: pl.LazyFrame,
    player_id_col: str = "player_id",
    amount_col: str = "amount_eur",
    game_col: str = "game_category",
) -> pl.LazyFrame:
    """
    Compute behavioral pattern features.

    Detects:
      - Bonus abuse: Claim bonus -> play minimum required -> withdraw immediately
      - Multi-accounting: Similar behavior patterns across different accounts
      - Collusion: Coordinated actions between accounts at same table
      - Advantage play: Exploiting game mechanics (not fraud but tracked)
      - Money laundering: Deposit -> minimal play -> withdraw pattern
    """
    # Bet sizing patterns
    events = events.with_columns([
        # Bet size relative to player's typical bet
        (
            pl.col(amount_col)
            / pl.col(amount_col).mean().over(player_id_col).clip(lower_bound=0.01)
        ).alias("bet_size_ratio"),

        # Bet size entropy (how varied are bet sizes - low entropy = bot-like)
        pl.col(amount_col)
        .rolling_std(window_size=20)
        .over(player_id_col)
        .alias("bet_size_volatility"),

        # Max single bet as ratio of total deposited (large single bet = suspicious)
        (
            pl.col(amount_col)
            / pl.col(amount_col).sum().over(player_id_col).clip(lower_bound=0.01)
        ).alias("bet_to_total_ratio"),
    ])

    # Martingale detection: doubling bets after losses
    # Common in bonus abuse (guaranteed to clear wagering requirements)
    events = events.with_columns([
        (pl.col(amount_col) / pl.col(amount_col).shift(1).over(player_id_col))
        .alias("bet_change_ratio"),
    ])

    events = events.with_columns([
        # Flag if bet doubles (ratio ~2.0) - classic Martingale
        pl.when(pl.col("bet_change_ratio").is_between(1.8, 2.2))
        .then(pl.lit(1))
        .otherwise(pl.lit(0))
        .rolling_sum(window_size=10)
        .alias("martingale_count_10"),
    ])

    # Game diversity features
    if game_col:
        events = events.with_columns([
            # Number of unique games played (low diversity = advantage play targeting specific game)
            pl.col(game_col).n_unique().over(player_id_col).alias("unique_games_played"),

            # Game switching frequency (rapid game changes = searching for exploitable game)
            pl.when(pl.col(game_col) != pl.col(game_col).shift(1).over(player_id_col))
            .then(pl.lit(1))
            .otherwise(pl.lit(0))
            .rolling_sum(window_size=20)
            .alias("game_switch_count_20"),
        ])

    # Deposit-to-play ratio features (money laundering detection)
    # Legitimate players: deposit $100, play $500+ worth of bets over time
    # Launderers: deposit $1000, play $50 in minimum bets, withdraw $950
    events = events.with_columns([
        # Running wagering multiple (total wagered / total deposited)
        # Low multiple (<1.5x) with withdrawal request = laundering indicator
        pl.col(amount_col)
        .cum_sum()
        .over(player_id_col)
        .alias("cumulative_wagered"),
    ])

    # Session behavior features
    events = events.with_columns([
        # Events per session (very high = bot; very low = just depositing)
        pl.col(player_id_col).count().over([player_id_col, "session_id"]).alias("events_in_session")
        if "session_id" in events.columns else pl.lit(None).alias("events_in_session"),
    ])

    logger.info("Computed behavioral features")
    return events


# =============================================================================
# Full Feature Pipeline
# =============================================================================

class FraudFeaturePipeline:
    """
    Complete feature engineering pipeline combining velocity, temporal,
    and behavioral features for fraud detection model input.
    """

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        self._feature_names: list[str] = []

    def transform(self, events_df: pl.DataFrame) -> pl.DataFrame:
        """
        Apply all feature transformations to a batch of events.

        Args:
            events_df: Raw events DataFrame with columns:
                - player_id, timestamp, amount_eur, game_category, session_id

        Returns:
            DataFrame enriched with all computed features
        """
        start_time = time.time()
        lazy = events_df.lazy()

        # Apply feature groups
        lazy = compute_velocity_features(lazy)
        lazy = compute_temporal_features(lazy)
        lazy = compute_behavioral_features(lazy)

        # Collect and fill nulls
        result = lazy.collect()

        # Fill NaN/null with sensible defaults for model consumption
        result = result.fill_nan(0.0).fill_null(strategy="zero")

        elapsed = time.time() - start_time
        logger.info(
            "Feature pipeline complete: %d events, %d features, %.2f seconds (%.0f events/sec)",
            result.height,
            result.width,
            elapsed,
            result.height / max(elapsed, 0.001),
        )

        self._feature_names = result.columns
        return result

    def get_feature_names(self) -> list[str]:
        """Return list of all computed feature names."""
        return self._feature_names

    def transform_streaming(
        self,
        event: dict,
        player_history: pl.DataFrame,
    ) -> dict:
        """
        Compute features for a single event using player's historical context.

        For real-time scoring: append new event to player history,
        compute features, return latest row as dict.

        Args:
            event: Single event as dict
            player_history: Recent history for this player (last 1000 events)

        Returns:
            Dict of feature name -> value for model input
        """
        # Append new event to history
        new_row = pl.DataFrame([event])
        combined = pl.concat([player_history, new_row])

        # Run pipeline on combined history
        features = self.transform(combined)

        # Return only the latest row (the new event with context)
        return features.tail(1).to_dicts()[0]


# =============================================================================
# Entry point for batch processing
# =============================================================================

def main():
    """Demo: Generate sample data and compute features."""
    import random
    import uuid

    logger.info("Generating sample gaming events...")

    # Generate realistic sample data
    n_events = 10_000
    n_players = 100
    player_ids = [f"player_{i:04d}" for i in range(n_players)]

    events = []
    base_time = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)

    for i in range(n_events):
        player = random.choice(player_ids)
        events.append({
            "event_id": str(uuid.uuid4()),
            "player_id": player,
            "timestamp": base_time + i * random.randint(500, 30000),
            "amount_eur": round(random.lognormvariate(2.5, 1.2), 2),
            "game_category": random.choice(["SLOTS", "TABLE_GAMES", "LIVE_CASINO", "POKER", "SPORTSBOOK"]),
            "session_id": f"session_{player}_{i // 50}",
        })

    df = pl.DataFrame(events)
    logger.info("Sample data: %d events, %d players", df.height, n_players)

    # Run pipeline
    pipeline = FraudFeaturePipeline()
    result = pipeline.transform(df)

    logger.info("Output shape: %d rows x %d columns", result.height, result.width)
    logger.info("Feature columns: %s", result.columns)
    logger.info("\nSample output (first 5 rows):")
    print(result.head(5))


if __name__ == "__main__":
    main()
