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
Feature Engineering Pipeline

High-performance feature engineering using Polars for casino fraud detection.
"""

import asyncio
from typing import Dict, Any, List, Optional, Tuple
import polars as pl  # ty:ignore[unresolved-import]
from datetime import datetime, timedelta
import structlog

logger = structlog.get_logger(__name__)


class FeatureEngineeringPipeline:
    """High-performance feature engineering pipeline using Polars"""

    def __init__(self):
        # Configure Polars for optimal performance
        pl.Config.set_global_string_cache(True)
        pl.Config.set_global_float_width(4)

        # Feature categories
        self.feature_categories = {
            "player_behavior": self._create_player_behavior_features,
            "transaction": self._create_transaction_features,
            "game_activity": self._create_game_activity_features,
            "temporal": self._create_temporal_features,
            "network": self._create_network_features,
            "risk": self._create_risk_features
        }

    async def process_player_data(self, player_id: str, events_df: pl.DataFrame,
                                historical_df: Optional[pl.DataFrame] = None) -> Dict[str, Any]:
        """
        Process all events for a player and create comprehensive features

        Args:
            player_id: Player identifier
            events_df: DataFrame with current events
            historical_df: DataFrame with historical events

        Returns:
            Dictionary of engineered features
        """

        try:
            # Combine current and historical data
            if historical_df is not None and not historical_df.is_empty():
                combined_df = pl.concat([historical_df, events_df])
            else:
                combined_df = events_df

            # Ensure timestamp column exists and is properly typed
            if "timestamp" in combined_df.columns:
                combined_df = combined_df.with_columns([
                    pl.col("timestamp").str.strptime(pl.Datetime, "%Y-%m-%d %H:%M:%S").alias("timestamp")
                ])

            # Create features for each category
            all_features: Dict[str, Any] = {"player_id": player_id}

            for category_name, feature_func in self.feature_categories.items():
                try:
                    category_features = await feature_func(combined_df, events_df)
                    all_features.update(category_features)
                except Exception as e:
                    logger.error(f"Error creating {category_name} features",
                               error=str(e), player_id=player_id)
                    continue

            # Add metadata
            all_features["feature_timestamp"] = datetime.utcnow().isoformat()  # ty:ignore[deprecated]
            all_features["feature_version"] = "v1.0"
            all_features["data_points_used"] = len(combined_df)

            return all_features

        except Exception as e:
            logger.error("Error in feature engineering pipeline",
                        error=str(e), player_id=player_id)
            return {"player_id": player_id, "error": str(e)}

    async def _create_player_behavior_features(self, combined_df: pl.DataFrame,
                                             events_df: pl.DataFrame) -> Dict[str, Any]:
        """Create player behavior features"""

        features = {}

        try:
            # Session analysis
            if "session_id" in combined_df.columns:
                session_stats = (
                    combined_df
                    .group_by("session_id")
                    .agg([
                        pl.col("timestamp").count().alias("events_per_session"),
                        (pl.col("timestamp").max() - pl.col("timestamp").min()).dt.minutes().alias("session_duration_minutes"),
                        pl.col("timestamp").min().alias("session_start"),
                        pl.col("timestamp").max().alias("session_end")
                    ])
                )

                if not session_stats.is_empty():
                    features["avg_events_per_session"] = session_stats.select(pl.col("events_per_session").mean()).item()
                    features["avg_session_duration"] = session_stats.select(pl.col("session_duration_minutes").mean()).item()
                    features["total_sessions"] = len(session_stats)

            # Event type distribution
            if "event_type" in combined_df.columns:
                event_distribution = (
                    combined_df
                    .group_by("event_type")
                    .count()
                    .with_columns([
                        (pl.col("count") / pl.col("count").sum()).alias("percentage")
                    ])
                )

                for row in event_distribution.iter_rows():
                    event_type, count, percentage = row
                    features[f"{event_type}_count"] = count
                    features[f"{event_type}_percentage"] = percentage

            # Time-based patterns
            if "timestamp" in combined_df.columns:
                combined_df = combined_df.with_columns([
                    pl.col("timestamp").dt.hour().alias("hour"),
                    pl.col("timestamp").dt.weekday().alias("weekday")
                ])

                # Hourly activity pattern
                hourly_pattern = (
                    combined_df
                    .group_by("hour")
                    .count()
                    .sort("hour")
                )

                for row in hourly_pattern.iter_rows():
                    hour, count = row
                    features[f"activity_hour_{hour}"] = count

                # Peak activity hour
                peak_hour = hourly_pattern.select(pl.col("count").arg_max()).item()
                features["peak_activity_hour"] = peak_hour

        except Exception as e:
            logger.error("Error creating player behavior features", error=str(e))

        return features

    async def _create_transaction_features(self, combined_df: pl.DataFrame,
                                         events_df: pl.DataFrame) -> Dict[str, Any]:
        """Create transaction-based features"""

        features = {}

        try:
            # Filter for transaction events
            txn_df = combined_df.filter(pl.col("event_type") == "transaction")

            if txn_df.is_empty():
                return features

            # Basic transaction statistics
            txn_stats = txn_df.select([
                pl.col("amount").sum().alias("total_amount"),
                pl.col("amount").mean().alias("avg_amount"),
                pl.col("amount").std().alias("amount_std"),
                pl.col("amount").max().alias("max_amount"),
                pl.col("amount").min().alias("min_amount"),
                pl.col("amount").count().alias("total_transactions")
            ])

            features.update(txn_stats.to_dicts()[0])

            # Transaction type distribution
            if "transaction_type" in txn_df.columns:
                txn_type_dist = (
                    txn_df
                    .group_by("transaction_type")
                    .count()
                    .with_columns([
                        (pl.col("count") / pl.col("count").sum()).alias("percentage")
                    ])
                )

                for row in txn_type_dist.iter_rows():
                    txn_type, count, percentage = row
                    features[f"{txn_type}_count"] = count
                    features[f"{txn_type}_percentage"] = percentage

            # Velocity features (transactions per hour)
            if "timestamp" in txn_df.columns:
                # Group by hour and count transactions
                hourly_txns = (
                    txn_df
                    .with_columns([
                        pl.col("timestamp").dt.truncate("1h").alias("hour_bucket")
                    ])
                    .group_by("hour_bucket")
                    .count()
                )

                if not hourly_txns.is_empty():
                    features["avg_txns_per_hour"] = hourly_txns.select(pl.col("count").mean()).item()
                    features["max_txns_per_hour"] = hourly_txns.select(pl.col("count").max()).item()

                    # Recent activity (last 1 hour)
                    one_hour_ago = datetime.utcnow() - timedelta(hours=1)  # ty:ignore[deprecated]
                    recent_txns = txn_df.filter(pl.col("timestamp") >= one_hour_ago)
                    features["recent_txns_1h"] = len(recent_txns)

            # Amount patterns
            if features.get("total_transactions", 0) > 1:
                # Amount trend (are amounts increasing?)
                amounts_with_time = txn_df.select([
                    pl.col("timestamp"),
                    pl.col("amount")
                ]).sort("timestamp")

                if len(amounts_with_time) > 1:
                    # Simple linear trend
                    x = list(range(len(amounts_with_time)))
                    y = amounts_with_time.select("amount").to_series().to_list()

                    # Calculate slope (simplified trend indicator)
                    if len(x) > 1:
                        slope = (y[-1] - y[0]) / (x[-1] - x[0]) if x[-1] != x[0] else 0
                        features["amount_trend"] = slope

        except Exception as e:
            logger.error("Error creating transaction features", error=str(e))

        return features

    async def _create_game_activity_features(self, combined_df: pl.DataFrame,
                                           events_df: pl.DataFrame) -> Dict[str, Any]:
        """Create game activity features"""

        features = {}

        try:
            # Filter for game events
            game_df = combined_df.filter(pl.col("event_type") == "game_event")

            if game_df.is_empty():
                return features

            # Game session analysis
            if "game_session_id" in game_df.columns:
                session_stats = (
                    game_df
                    .group_by("game_session_id")
                    .agg([
                        pl.col("timestamp").count().alias("events_per_session"),
                        (pl.col("timestamp").max() - pl.col("timestamp").min()).dt.minutes().alias("session_duration"),
                        pl.col("bet_amount").sum().alias("total_bet_session"),
                        pl.col("win_amount").sum().alias("total_win_session")
                    ])
                    .with_columns([
                        (pl.col("total_win_session") - pl.col("total_bet_session")).alias("net_result_session")
                    ])
                )

                if not session_stats.is_empty():
                    features["avg_session_duration"] = session_stats.select(pl.col("session_duration").mean()).item()
                    features["total_game_sessions"] = len(session_stats)
                    features["profitable_sessions"] = session_stats.filter(pl.col("net_result_session") > 0).height

            # Betting patterns
            if "bet_amount" in game_df.columns:
                bet_stats = game_df.select([
                    pl.col("bet_amount").sum().alias("total_bet_amount"),
                    pl.col("bet_amount").mean().alias("avg_bet_amount"),
                    pl.col("bet_amount").std().alias("bet_std"),
                    pl.col("bet_amount").max().alias("max_bet"),
                    pl.col("bet_amount").count().alias("total_bets")
                ])

                features.update(bet_stats.to_dicts()[0])

            # Win/loss analysis
            if "win_amount" in game_df.columns:
                win_stats = game_df.select([
                    pl.col("win_amount").sum().alias("total_win_amount"),
                    pl.col("win_amount").mean().alias("avg_win_amount"),
                    pl.col("win_amount").count().alias("total_wins")
                ])

                features.update(win_stats.to_dicts()[0])

                # Calculate win rate and RTP (Return to Player)
                total_bets = features.get("total_bets", 0)
                total_win = features.get("total_win_amount", 0)
                total_bet = features.get("total_bet_amount", 0)

                if total_bets > 0:
                    features["win_rate"] = features.get("total_wins", 0) / total_bets

                if total_bet > 0:
                    features["rtp"] = total_win / total_bet

            # Game type preferences
            if "game_type" in game_df.columns:
                game_preferences = (
                    game_df
                    .group_by("game_type")
                    .count()
                    .sort("count", descending=True)
                )

                if not game_preferences.is_empty():
                    top_game = game_preferences.select("game_type").item(0)
                    features["preferred_game"] = top_game
                    features["preferred_game_count"] = game_preferences.select("count").item(0)

        except Exception as e:
            logger.error("Error creating game activity features", error=str(e))

        return features

    async def _create_temporal_features(self, combined_df: pl.DataFrame,
                                       events_df: pl.DataFrame) -> Dict[str, Any]:
        """Create temporal features"""

        features = {}

        try:
            if "timestamp" not in combined_df.columns:
                return features

            # Time-based aggregations
            combined_df = combined_df.with_columns([
                pl.col("timestamp").dt.hour().alias("hour"),
                pl.col("timestamp").dt.weekday().alias("weekday"),
                pl.col("timestamp").dt.month().alias("month"),
                pl.col("timestamp").dt.day().alias("day")
            ])

            # Hourly activity pattern
            hourly_activity = (
                combined_df
                .group_by("hour")
                .count()
                .sort("hour")
            )

            # Calculate activity concentration (how focused activity is)
            if not hourly_activity.is_empty():
                total_activity = hourly_activity.select(pl.col("count").sum()).item()
                max_hourly = hourly_activity.select(pl.col("count").max()).item()

                if total_activity > 0:
                    features["activity_concentration"] = max_hourly / total_activity

                # Peak activity hour
                peak_hour_idx = hourly_activity.select(pl.col("count").arg_max()).item()
                features["peak_activity_hour"] = hourly_activity.select("hour").item(peak_hour_idx)

            # Weekly patterns
            weekly_activity = (
                combined_df
                .group_by("weekday")
                .count()
                .sort("weekday")
            )

            if not weekly_activity.is_empty():
                # Most active day
                most_active_day = weekly_activity.select(pl.col("count").arg_max()).item()
                features["most_active_weekday"] = most_active_day

            # Recency features
            now = datetime.utcnow()  # ty:ignore[deprecated]
            latest_event = combined_df.select(pl.col("timestamp").max()).item()

            if latest_event:
                time_since_last_event = (now - latest_event).total_seconds() / 3600  # hours
                features["hours_since_last_event"] = time_since_last_event

                # Activity level (events per hour over last 24h)
                last_24h = combined_df.filter(pl.col("timestamp") >= (now - timedelta(hours=24)))
                features["events_last_24h"] = len(last_24h)

                if time_since_last_event < 24:
                    features["events_per_hour_last_24h"] = features["events_last_24h"] / min(time_since_last_event, 24)

        except Exception as e:
            logger.error("Error creating temporal features", error=str(e))

        return features

    async def _create_network_features(self, combined_df: pl.DataFrame,
                                     events_df: pl.DataFrame) -> Dict[str, Any]:
        """Create network and relationship features"""

        features = {}

        try:
            # IP address analysis
            if "ip_address" in combined_df.columns:
                unique_ips = combined_df.select(pl.col("ip_address").n_unique()).item()
                features["unique_ip_addresses"] = unique_ips

                # IP changes (indicating different locations/devices)
                if len(combined_df) > 1:
                    ip_changes = (
                        combined_df
                        .sort("timestamp")
                        .select("ip_address")
                        .to_series()
                        .diff()
                        .ne(0)
                        .sum()
                    )
                    features["ip_address_changes"] = ip_changes

            # Device fingerprint analysis
            if "device_fingerprint" in combined_df.columns:
                unique_devices = combined_df.select(pl.col("device_fingerprint").n_unique()).item()
                features["unique_devices"] = unique_devices

                # Device changes
                if len(combined_df) > 1:
                    device_changes = (
                        combined_df
                        .sort("timestamp")
                        .select("device_fingerprint")
                        .to_series()
                        .diff()
                        .ne(0)
                        .sum()
                    )
                    features["device_changes"] = device_changes

            # Location analysis
            if "location_data" in combined_df.columns and combined_df.select("location_data").item(0):
                # Extract country information
                countries = []
                for loc_data in combined_df.select("location_data").to_series():
                    if isinstance(loc_data, dict) and "country" in loc_data:
                        countries.append(loc_data["country"])

                if countries:
                    unique_countries = len(set(countries))
                    features["unique_countries"] = unique_countries

                    # Country changes
                    if len(countries) > 1:
                        country_changes = sum(1 for i in range(1, len(countries)) if countries[i] != countries[i-1])
                        features["country_changes"] = country_changes

            # Session analysis for multi-session behavior
            if "session_id" in combined_df.columns:
                unique_sessions = combined_df.select(pl.col("session_id").n_unique()).item()
                features["unique_sessions"] = unique_sessions

                # Sessions per day (estimate)
                if "timestamp" in combined_df.columns and unique_sessions > 0:
                    time_span_days = 30  # Assume 30-day window
                    features["sessions_per_day"] = unique_sessions / time_span_days

        except Exception as e:
            logger.error("Error creating network features", error=str(e))

        return features

    async def _create_risk_features(self, combined_df: pl.DataFrame,
                                   events_df: pl.DataFrame) -> Dict[str, Any]:
        """Create risk assessment features"""

        features = {}

        try:
            # Amount-based risk indicators
            if "amount" in combined_df.columns:
                amounts = combined_df.select("amount").to_series()

                # Statistical outliers
                if len(amounts) > 1:
                    q75, q25 = amounts.quantile(0.75), amounts.quantile(0.25)
                    iqr = q75 - q25
                    upper_bound = q75 + 1.5 * iqr

                    outlier_count = amounts.filter(amounts > upper_bound).len()
                    features["amount_outliers"] = outlier_count
                    features["outlier_percentage"] = outlier_count / len(amounts) if len(amounts) > 0 else 0

            # Velocity-based risk
            if "timestamp" in combined_df.columns:
                # Events per minute (recent activity)
                recent_events = combined_df.filter(
                    pl.col("timestamp") >= (datetime.utcnow() - timedelta(minutes=5))  # ty:ignore[deprecated]
                )
                features["events_last_5min"] = len(recent_events)

                # Burst activity detection
                if len(combined_df) > 10:
                    # Sort by timestamp and check for clustering
                    sorted_times = combined_df.select("timestamp").sort("timestamp").to_series()

                    # Calculate time differences between consecutive events
                    time_diffs = sorted_times.diff().drop_nulls()
                    if not time_diffs.is_empty():
                        avg_time_diff = time_diffs.mean().total_seconds()
                        features["avg_time_between_events"] = avg_time_diff

                        # Very rapid events (less than 1 second apart)
                        rapid_events = time_diffs.filter(time_diffs.dt.total_seconds() < 1).len()
                        features["rapid_events_count"] = rapid_events

            # Pattern-based risk indicators
            features["suspicious_patterns"] = 0

            # Check for unusual betting patterns
            if features.get("win_rate", 0) > 0.9:  # Winning 90%+ of bets
                features["suspicious_patterns"] += 1

            # Check for extreme velocity
            if features.get("events_last_5min", 0) > 50:
                features["suspicious_patterns"] += 1

            # Check for frequent IP changes
            if features.get("ip_address_changes", 0) > 10:
                features["suspicious_patterns"] += 1

            # Check for high amount outliers
            if features.get("outlier_percentage", 0) > 0.3:
                features["suspicious_patterns"] += 1

            # Composite risk score (simple weighted sum)
            risk_weights = {
                "suspicious_patterns": 2.0,
                "outlier_percentage": 1.5,
                "events_last_5min": 0.1,
                "ip_address_changes": 0.5,
                "win_rate": 1.0 if features.get("win_rate", 0) > 0.8 else 0
            }

            risk_score = 0
            for feature_name, weight in risk_weights.items():
                value = features.get(feature_name, 0)
                risk_score += value * weight

            features["composite_risk_score"] = risk_score

            # Risk level classification
            if risk_score < 1:
                features["risk_level"] = "low"
            elif risk_score < 3:
                features["risk_level"] = "medium"
            elif risk_score < 5:
                features["risk_level"] = "high"
            else:
                features["risk_level"] = "critical"

        except Exception as e:
            logger.error("Error creating risk features", error=str(e))

        return features

    async def validate_features(self, features: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """
        Validate engineered features for quality and completeness

        Returns:
            Tuple of (is_valid, error_messages)
        """

        errors = []

        # Check for required features
        required_features = ["player_id", "total_transactions", "total_amount"]

        for feature in required_features:
            if feature not in features or features[feature] is None:
                errors.append(f"Missing required feature: {feature}")

        # Check for reasonable value ranges
        if "total_amount" in features:
            amount = features["total_amount"]
            if not isinstance(amount, (int, float)) or amount < 0:
                errors.append(f"Invalid total_amount: {amount}")

        if "avg_amount" in features:
            avg_amount = features["avg_amount"]
            if isinstance(avg_amount, (int, float)) and avg_amount < 0:
                errors.append(f"Invalid avg_amount: {avg_amount}")

        if "win_rate" in features:
            win_rate = features["win_rate"]
            if isinstance(win_rate, (int, float)) and not (0 <= win_rate <= 1):
                errors.append(f"Invalid win_rate: {win_rate}")

        # Check for NaN or infinite values
        for key, value in features.items():
            if isinstance(value, float) and (str(value) == 'nan' or str(value) == 'inf' or str(value) == '-inf'):
                errors.append(f"Invalid value for {key}: {value}")

        return len(errors) == 0, errors