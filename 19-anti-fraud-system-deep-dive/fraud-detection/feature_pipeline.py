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
Feature Engineering Pipeline -- Polars-based

Transforms raw player events into ML-ready features across six categories:
  1. Player behavior   (session analysis, event distribution)
  2. Transaction        (amounts, velocity, trends)
  3. Game activity      (betting patterns, win/loss, RTP)
  4. Temporal           (circadian rhythm, recency, concentration)
  5. Network            (IP diversity, device changes, geo shifts)
  6. Risk               (outliers, burst detection, composite score)

Uses Polars instead of pandas for 10-50x faster aggregations --
critical when you need sub-50ms feature computation at 100K TPS.

Reference implementation for Chapter 41: Anti-Fraud System Deep Dive.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import polars as pl  # ty:ignore[unresolved-import]
import structlog

logger = structlog.get_logger(__name__)


class FeatureEngineeringPipeline:
    """High-performance feature engineering pipeline using Polars."""

    def __init__(self):
        self.feature_categories = {
            "player_behavior": self._create_player_behavior_features,
            "transaction": self._create_transaction_features,
            "game_activity": self._create_game_activity_features,
            "temporal": self._create_temporal_features,
            "network": self._create_network_features,
            "risk": self._create_risk_features,
        }

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def process_player_data(
        self,
        player_id: str,
        events_df: pl.DataFrame,
        historical_df: Optional[pl.DataFrame] = None,
    ) -> Dict[str, Any]:
        """
        Build a full feature vector for *player_id* by combining
        current events with an optional historical window.
        """
        try:
            if historical_df is not None and not historical_df.is_empty():
                combined_df = pl.concat([historical_df, events_df])
            else:
                combined_df = events_df

            # Normalise timestamp column
            if "timestamp" in combined_df.columns:
                combined_df = combined_df.with_columns(
                    pl.col("timestamp")
                    .str.strptime(pl.Datetime, "%Y-%m-%d %H:%M:%S")
                    .alias("timestamp")
                )

            all_features: Dict[str, Any] = {"player_id": player_id}

            for category_name, feature_func in self.feature_categories.items():
                try:
                    category_features = await feature_func(combined_df, events_df)
                    all_features.update(category_features)
                except Exception as e:
                    logger.error(
                        f"Error creating {category_name} features",
                        error=str(e),
                        player_id=player_id,
                    )

            all_features["feature_timestamp"] = datetime.utcnow().isoformat()  # ty:ignore[deprecated]
            all_features["feature_version"] = "v1.0"
            all_features["data_points_used"] = len(combined_df)
            return all_features

        except Exception as e:
            logger.error(
                "Feature pipeline error", error=str(e), player_id=player_id
            )
            return {"player_id": player_id, "error": str(e)}

    # ------------------------------------------------------------------
    # 1. Player behaviour features
    # ------------------------------------------------------------------

    async def _create_player_behavior_features(
        self, combined_df: pl.DataFrame, events_df: pl.DataFrame
    ) -> Dict[str, Any]:
        features: Dict[str, Any] = {}

        # Session-level statistics
        if "session_id" in combined_df.columns:
            session_stats = combined_df.group_by("session_id").agg(
                [
                    pl.col("timestamp").count().alias("events_per_session"),
                    (
                        pl.col("timestamp").max() - pl.col("timestamp").min()
                    )
                    .dt.minutes()
                    .alias("session_duration_minutes"),
                ]
            )
            if not session_stats.is_empty():
                features["avg_events_per_session"] = session_stats.select(
                    pl.col("events_per_session").mean()
                ).item()
                features["avg_session_duration"] = session_stats.select(
                    pl.col("session_duration_minutes").mean()
                ).item()
                features["total_sessions"] = len(session_stats)

        # Event-type distribution
        if "event_type" in combined_df.columns:
            event_dist = (
                combined_df.group_by("event_type")
                .count()
                .with_columns(
                    (pl.col("count") / pl.col("count").sum()).alias("percentage")
                )
            )
            for row in event_dist.iter_rows():
                event_type, count, pct = row
                features[f"{event_type}_count"] = count
                features[f"{event_type}_percentage"] = pct

        # Hourly activity pattern + peak hour
        if "timestamp" in combined_df.columns:
            combined_df = combined_df.with_columns(
                pl.col("timestamp").dt.hour().alias("hour")
            )
            hourly = combined_df.group_by("hour").count().sort("hour")
            if not hourly.is_empty():
                peak_idx = hourly.select(pl.col("count").arg_max()).item()
                features["peak_activity_hour"] = peak_idx

        return features

    # ------------------------------------------------------------------
    # 2. Transaction features
    # ------------------------------------------------------------------

    async def _create_transaction_features(
        self, combined_df: pl.DataFrame, events_df: pl.DataFrame
    ) -> Dict[str, Any]:
        features: Dict[str, Any] = {}

        txn_df = combined_df.filter(pl.col("event_type") == "transaction")
        if txn_df.is_empty():
            return features

        # Descriptive statistics
        txn_stats = txn_df.select(
            [
                pl.col("amount").sum().alias("total_amount"),
                pl.col("amount").mean().alias("avg_amount"),
                pl.col("amount").std().alias("amount_std"),
                pl.col("amount").max().alias("max_amount"),
                pl.col("amount").min().alias("min_amount"),
                pl.col("amount").count().alias("total_transactions"),
            ]
        )
        features.update(txn_stats.to_dicts()[0])

        # Transaction-type breakdown
        if "transaction_type" in txn_df.columns:
            dist = (
                txn_df.group_by("transaction_type")
                .count()
                .with_columns(
                    (pl.col("count") / pl.col("count").sum()).alias("percentage")
                )
            )
            for row in dist.iter_rows():
                tt, count, pct = row
                features[f"{tt}_count"] = count
                features[f"{tt}_percentage"] = pct

        # Velocity -- transactions per hour
        if "timestamp" in txn_df.columns:
            hourly_txns = (
                txn_df.with_columns(
                    pl.col("timestamp").dt.truncate("1h").alias("hour_bucket")
                )
                .group_by("hour_bucket")
                .count()
            )
            if not hourly_txns.is_empty():
                features["avg_txns_per_hour"] = hourly_txns.select(
                    pl.col("count").mean()
                ).item()
                features["max_txns_per_hour"] = hourly_txns.select(
                    pl.col("count").max()
                ).item()

                one_hour_ago = datetime.utcnow() - timedelta(hours=1)  # ty:ignore[deprecated]
                recent = txn_df.filter(pl.col("timestamp") >= one_hour_ago)
                features["recent_txns_1h"] = len(recent)

        # Simple linear trend on amounts
        if features.get("total_transactions", 0) > 1:
            y = (
                txn_df.select(["timestamp", "amount"])
                .sort("timestamp")
                .select("amount")
                .to_series()
                .to_list()
            )
            if len(y) > 1:
                features["amount_trend"] = (y[-1] - y[0]) / (len(y) - 1)

        return features

    # ------------------------------------------------------------------
    # 3. Game activity features
    # ------------------------------------------------------------------

    async def _create_game_activity_features(
        self, combined_df: pl.DataFrame, events_df: pl.DataFrame
    ) -> Dict[str, Any]:
        features: Dict[str, Any] = {}

        game_df = combined_df.filter(pl.col("event_type") == "game_event")
        if game_df.is_empty():
            return features

        # Per-session aggregates
        if "game_session_id" in game_df.columns:
            sess = (
                game_df.group_by("game_session_id")
                .agg(
                    [
                        pl.col("timestamp").count().alias("events_per_session"),
                        (
                            pl.col("timestamp").max() - pl.col("timestamp").min()
                        )
                        .dt.minutes()
                        .alias("session_duration"),
                        pl.col("bet_amount").sum().alias("total_bet_session"),
                        pl.col("win_amount").sum().alias("total_win_session"),
                    ]
                )
                .with_columns(
                    (
                        pl.col("total_win_session") - pl.col("total_bet_session")
                    ).alias("net_result_session")
                )
            )
            if not sess.is_empty():
                features["avg_session_duration"] = sess.select(
                    pl.col("session_duration").mean()
                ).item()
                features["total_game_sessions"] = len(sess)
                features["profitable_sessions"] = sess.filter(
                    pl.col("net_result_session") > 0
                ).height

        # Betting stats
        if "bet_amount" in game_df.columns:
            bet_stats = game_df.select(
                [
                    pl.col("bet_amount").sum().alias("total_bet_amount"),
                    pl.col("bet_amount").mean().alias("avg_bet_amount"),
                    pl.col("bet_amount").std().alias("bet_std"),
                    pl.col("bet_amount").max().alias("max_bet"),
                    pl.col("bet_amount").count().alias("total_bets"),
                ]
            )
            features.update(bet_stats.to_dicts()[0])

        # Win stats + RTP
        if "win_amount" in game_df.columns:
            win_stats = game_df.select(
                [
                    pl.col("win_amount").sum().alias("total_win_amount"),
                    pl.col("win_amount").mean().alias("avg_win_amount"),
                    pl.col("win_amount").count().alias("total_wins"),
                ]
            )
            features.update(win_stats.to_dicts()[0])

            total_bet = features.get("total_bet_amount", 0)
            total_bets = features.get("total_bets", 0)
            if total_bets > 0:
                features["win_rate"] = features.get("total_wins", 0) / total_bets
            if total_bet > 0:
                features["rtp"] = features.get("total_win_amount", 0) / total_bet

        # Preferred game type
        if "game_type" in game_df.columns:
            prefs = game_df.group_by("game_type").count().sort("count", descending=True)
            if not prefs.is_empty():
                features["preferred_game"] = prefs.select("game_type").item(0)
                features["preferred_game_count"] = prefs.select("count").item(0)

        return features

    # ------------------------------------------------------------------
    # 4. Temporal features
    # ------------------------------------------------------------------

    async def _create_temporal_features(
        self, combined_df: pl.DataFrame, events_df: pl.DataFrame
    ) -> Dict[str, Any]:
        features: Dict[str, Any] = {}

        if "timestamp" not in combined_df.columns:
            return features

        combined_df = combined_df.with_columns(
            [
                pl.col("timestamp").dt.hour().alias("hour"),
                pl.col("timestamp").dt.weekday().alias("weekday"),
            ]
        )

        # Activity concentration (entropy proxy)
        hourly = combined_df.group_by("hour").count().sort("hour")
        if not hourly.is_empty():
            total = hourly.select(pl.col("count").sum()).item()
            max_h = hourly.select(pl.col("count").max()).item()
            if total > 0:
                features["activity_concentration"] = max_h / total
            peak_idx = hourly.select(pl.col("count").arg_max()).item()
            features["peak_activity_hour"] = hourly.select("hour").item(peak_idx)

        # Weekly pattern
        weekly = combined_df.group_by("weekday").count().sort("weekday")
        if not weekly.is_empty():
            features["most_active_weekday"] = weekly.select(
                pl.col("count").arg_max()
            ).item()

        # Recency
        now = datetime.utcnow()  # ty:ignore[deprecated]
        latest = combined_df.select(pl.col("timestamp").max()).item()
        if latest:
            hours_since = (now - latest).total_seconds() / 3600
            features["hours_since_last_event"] = hours_since

            last_24h = combined_df.filter(
                pl.col("timestamp") >= (now - timedelta(hours=24))
            )
            features["events_last_24h"] = len(last_24h)
            if hours_since < 24:
                features["events_per_hour_last_24h"] = (
                    features["events_last_24h"] / min(hours_since, 24)
                )

        return features

    # ------------------------------------------------------------------
    # 5. Network features
    # ------------------------------------------------------------------

    async def _create_network_features(
        self, combined_df: pl.DataFrame, events_df: pl.DataFrame
    ) -> Dict[str, Any]:
        features: Dict[str, Any] = {}

        if "ip_address" in combined_df.columns:
            features["unique_ip_addresses"] = combined_df.select(
                pl.col("ip_address").n_unique()
            ).item()

        if "device_fingerprint" in combined_df.columns:
            features["unique_devices"] = combined_df.select(
                pl.col("device_fingerprint").n_unique()
            ).item()

        if "location_data" in combined_df.columns:
            countries = []
            for loc in combined_df.select("location_data").to_series():
                if isinstance(loc, dict) and "country" in loc:
                    countries.append(loc["country"])
            if countries:
                features["unique_countries"] = len(set(countries))
                if len(countries) > 1:
                    features["country_changes"] = sum(
                        1 for i in range(1, len(countries)) if countries[i] != countries[i - 1]
                    )

        if "session_id" in combined_df.columns:
            uniq = combined_df.select(pl.col("session_id").n_unique()).item()
            features["unique_sessions"] = uniq
            features["sessions_per_day"] = uniq / 30  # assume 30-day window

        return features

    # ------------------------------------------------------------------
    # 6. Risk features (composite scoring)
    # ------------------------------------------------------------------

    async def _create_risk_features(
        self, combined_df: pl.DataFrame, events_df: pl.DataFrame
    ) -> Dict[str, Any]:
        features: Dict[str, Any] = {}

        # Amount outliers (IQR method)
        if "amount" in combined_df.columns:
            amounts = combined_df.select("amount").to_series()
            if len(amounts) > 1:
                q75, q25 = amounts.quantile(0.75), amounts.quantile(0.25)
                iqr = q75 - q25
                upper = q75 + 1.5 * iqr
                outliers = amounts.filter(amounts > upper).len()
                features["amount_outliers"] = outliers
                features["outlier_percentage"] = (
                    outliers / len(amounts) if len(amounts) > 0 else 0
                )

        # Burst detection
        if "timestamp" in combined_df.columns:
            recent = combined_df.filter(
                pl.col("timestamp") >= (datetime.utcnow() - timedelta(minutes=5))  # ty:ignore[deprecated]
            )
            features["events_last_5min"] = len(recent)

            if len(combined_df) > 10:
                sorted_t = combined_df.select("timestamp").sort("timestamp").to_series()
                diffs = sorted_t.diff().drop_nulls()
                if not diffs.is_empty():
                    features["avg_time_between_events"] = diffs.mean().total_seconds()
                    features["rapid_events_count"] = diffs.filter(
                        diffs.dt.total_seconds() < 1
                    ).len()

        # Suspicious-pattern counter
        features["suspicious_patterns"] = 0
        if features.get("win_rate", 0) > 0.9:
            features["suspicious_patterns"] += 1
        if features.get("events_last_5min", 0) > 50:
            features["suspicious_patterns"] += 1
        if features.get("ip_address_changes", 0) > 10:
            features["suspicious_patterns"] += 1
        if features.get("outlier_percentage", 0) > 0.3:
            features["suspicious_patterns"] += 1

        # Weighted composite risk score
        weights = {
            "suspicious_patterns": 2.0,
            "outlier_percentage": 1.5,
            "events_last_5min": 0.1,
            "ip_address_changes": 0.5,
            "win_rate": 1.0 if features.get("win_rate", 0) > 0.8 else 0,
        }
        risk_score = sum(
            features.get(k, 0) * w for k, w in weights.items()
        )
        features["composite_risk_score"] = risk_score

        if risk_score < 1:
            features["risk_level"] = "low"
        elif risk_score < 3:
            features["risk_level"] = "medium"
        elif risk_score < 5:
            features["risk_level"] = "high"
        else:
            features["risk_level"] = "critical"

        return features

    # ------------------------------------------------------------------
    # Feature validation
    # ------------------------------------------------------------------

    async def validate_features(
        self, features: Dict[str, Any]
    ) -> Tuple[bool, List[str]]:
        """Check feature quality before they reach the model."""
        errors: List[str] = []

        for f in ("player_id", "total_transactions", "total_amount"):
            if f not in features or features[f] is None:
                errors.append(f"Missing required feature: {f}")

        if "total_amount" in features:
            v = features["total_amount"]
            if not isinstance(v, (int, float)) or v < 0:
                errors.append(f"Invalid total_amount: {v}")

        if "win_rate" in features:
            v = features["win_rate"]
            if isinstance(v, (int, float)) and not (0 <= v <= 1):
                errors.append(f"Invalid win_rate: {v}")

        for key, value in features.items():
            if isinstance(value, float) and (
                str(value) in ("nan", "inf", "-inf")
            ):
                errors.append(f"Invalid value for {key}: {value}")

        return len(errors) == 0, errors
