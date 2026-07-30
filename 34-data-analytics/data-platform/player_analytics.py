# Companion code for "The Backend of Luck" - Chapter 34, Data and Analytics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Player Analytics Module for iGaming Platforms

This module provides comprehensive player behavior analysis including:
- ML feature engineering for player modeling
- Engagement scoring and segmentation
- Risk indicator detection
- Churn prediction features
- Value-based player classification

Features:
- Historical behavior analysis
- Real-time engagement tracking
- Predictive feature generation
- Risk signal aggregation

Usage:
    analytics = PlayerAnalytics(clickhouse_client)
    features = await analytics.create_ml_features("player_123", days=30)
    segment = analytics.classify_player_value(features)

Dependencies:
    pip install clickhouse-driver numpy
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Protocol
import logging


class ClickHouseClientProtocol(Protocol):
    """Protocol for ClickHouse client interface."""

    def execute(self, query: str, params: Optional[dict[str, Any]] = None) -> list[Any]: ...


@dataclass
class PlayerFeatures:
    """ML features for player behavior modeling."""

    player_id: str
    feature_window_days: int

    # Activity metrics
    total_events: int = 0
    total_bets: float = 0.0
    total_wins: float = 0.0
    total_deposits: float = 0.0
    total_withdrawals: float = 0.0

    # Derived metrics
    avg_bet_size: float = 0.0
    max_bet_size: float = 0.0
    bet_frequency: float = 0.0
    win_rate: float = 0.0
    deposit_frequency: float = 0.0
    avg_session_duration: float = 0.0

    # Preferences
    preferred_games: list[str] = field(default_factory=list)
    playing_hours: list[int] = field(default_factory=list)

    # Risk indicators
    risk_indicators: list[str] = field(default_factory=list)
    risk_score: float = 0.0

    # Engagement
    engagement_score: float = 0.0
    days_since_last_activity: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert features to dictionary."""
        return {
            "player_id": self.player_id,
            "feature_window_days": self.feature_window_days,
            "total_events": self.total_events,
            "total_bets": self.total_bets,
            "total_wins": self.total_wins,
            "total_deposits": self.total_deposits,
            "total_withdrawals": self.total_withdrawals,
            "avg_bet_size": self.avg_bet_size,
            "max_bet_size": self.max_bet_size,
            "bet_frequency": self.bet_frequency,
            "win_rate": self.win_rate,
            "deposit_frequency": self.deposit_frequency,
            "avg_session_duration": self.avg_session_duration,
            "preferred_games": self.preferred_games,
            "playing_hours": self.playing_hours,
            "risk_indicators": self.risk_indicators,
            "risk_score": self.risk_score,
            "engagement_score": self.engagement_score,
            "days_since_last_activity": self.days_since_last_activity,
        }


@dataclass
class PlayerSegment:
    """Player segmentation result."""

    segment_name: str
    segment_tier: str  # VIP, HIGH, MEDIUM, LOW, AT_RISK
    lifetime_value: float
    predicted_churn_probability: float
    recommended_actions: list[str]


class PlayerAnalytics:
    """
    Player behavior analytics for iGaming platforms.

    Provides ML feature engineering, segmentation, and risk analysis
    for player behavior modeling and personalization.
    """

    def __init__(self, clickhouse_client: ClickHouseClientProtocol):
        self.clickhouse = clickhouse_client
        self.logger = logging.getLogger(__name__)

        # Risk thresholds
        self.risk_thresholds = {
            "unusual_win_rate": 0.6,  # Win rate > 60%
            "high_deposit_frequency": 2.0,  # > 2 deposits per day
            "large_bet_ratio": 0.5,  # Single bet > 50% of total deposits
            "rapid_loss_recovery": 5,  # > 5 deposits after losses
        }

        # Engagement weights
        self.engagement_weights = {
            "event_diversity": 10.0,
            "event_frequency": 0.5,
            "session_duration": 0.1,
            "deposit_frequency": 5.0,
        }

    def create_ml_features(
        self,
        player_id: str,
        feature_window_days: int = 30,
    ) -> PlayerFeatures:
        """
        Create ML features for player behavior modeling.

        Args:
            player_id: Player ID to analyze
            feature_window_days: Number of days to analyze

        Returns:
            PlayerFeatures object with computed features
        """
        try:
            # Get historical data from ClickHouse
            historical_data = self.clickhouse.execute(
                """
                SELECT
                    timestamp,
                    event_type,
                    JSONExtractFloat(properties, 'amount') as amount,
                    JSONExtractString(properties, 'game_id') as game_id
                FROM casino_events
                WHERE entity_id = %(player_id)s
                  AND timestamp >= now() - INTERVAL %(days)s DAY
                ORDER BY timestamp
            """,
                {"player_id": player_id, "days": feature_window_days},
            )

            # Initialize features
            features = PlayerFeatures(
                player_id=player_id,
                feature_window_days=feature_window_days,
                total_events=len(historical_data),
            )

            # Process event data
            bets: list[float] = []
            deposits: list[datetime] = []
            games: list[str] = []
            hours: list[int] = []

            for row in historical_data:
                timestamp = row[0]
                event_type = row[1]
                amount = float(row[2]) if row[2] else 0.0
                game_id = row[3]

                # Track playing hours
                if isinstance(timestamp, datetime):
                    hours.append(timestamp.hour)

                # Track game preferences
                if game_id:
                    games.append(game_id)

                # Aggregate by event type
                if event_type == "bet_placed":
                    features.total_bets += amount
                    bets.append(amount)
                elif event_type == "win":
                    features.total_wins += amount
                elif event_type == "deposit":
                    features.total_deposits += amount
                    if isinstance(timestamp, datetime):
                        deposits.append(timestamp)
                elif event_type == "withdrawal":
                    features.total_withdrawals += amount

            # Calculate derived features
            if bets:
                features.avg_bet_size = sum(bets) / len(bets)
                features.max_bet_size = max(bets)
                features.bet_frequency = len(bets) / feature_window_days

            if features.total_bets > 0:
                features.win_rate = features.total_wins / features.total_bets

            if deposits:
                features.deposit_frequency = len(deposits) / feature_window_days

            # Game preferences (top 5)
            if games:
                game_counts: dict[str, int] = {}
                for game in games:
                    game_counts[game] = game_counts.get(game, 0) + 1
                sorted_games = sorted(game_counts.items(), key=lambda x: x[1], reverse=True)
                features.preferred_games = [g[0] for g in sorted_games[:5]]

            # Playing hours distribution
            if hours:
                hour_counts: dict[int, int] = {}
                for hour in hours:
                    hour_counts[hour] = hour_counts.get(hour, 0) + 1
                sorted_hours = sorted(hour_counts.items(), key=lambda x: x[1], reverse=True)
                features.playing_hours = [h[0] for h in sorted_hours[:5]]

            # Calculate risk indicators
            self._calculate_risk_indicators(features)

            # Calculate engagement score
            features.engagement_score = self._calculate_engagement_score(
                historical_data,
                features,
            )

            return features

        except Exception as e:
            self.logger.error(f"Failed to create ML features for {player_id}: {e}")
            return PlayerFeatures(
                player_id=player_id,
                feature_window_days=feature_window_days,
            )

    def _calculate_risk_indicators(self, features: PlayerFeatures) -> None:
        """Calculate risk indicators for player."""
        # Unusual win rate
        if features.win_rate > self.risk_thresholds["unusual_win_rate"]:
            features.risk_indicators.append("unusual_win_rate")

        # High deposit frequency
        if features.deposit_frequency > self.risk_thresholds["high_deposit_frequency"]:
            features.risk_indicators.append("high_deposit_frequency")

        # Large bets relative to deposits
        if features.total_deposits > 0:
            ratio = features.max_bet_size / features.total_deposits
            if ratio > self.risk_thresholds["large_bet_ratio"]:
                features.risk_indicators.append("large_bet_relative_to_deposits")

        # Negative net position (withdrawals > deposits)
        if features.total_withdrawals > features.total_deposits * 1.5:
            features.risk_indicators.append("high_withdrawal_ratio")

        # Calculate risk score (0-100)
        base_score = len(features.risk_indicators) * 20
        features.risk_score = min(100.0, base_score)

    def _calculate_engagement_score(
        self,
        activity_data: list[Any],
        features: PlayerFeatures,
    ) -> float:
        """Calculate player engagement score."""
        if not activity_data:
            return 0.0

        # Event diversity
        event_types = len(set(row[1] for row in activity_data))
        diversity_score = event_types * self.engagement_weights["event_diversity"]

        # Event frequency
        frequency_score = len(activity_data) * self.engagement_weights["event_frequency"]

        # Deposit frequency bonus
        deposit_score = features.deposit_frequency * self.engagement_weights["deposit_frequency"]

        # Combine scores
        total_score = diversity_score + frequency_score + deposit_score

        # Normalize to 0-100
        return min(100.0, total_score)

    def classify_player_value(self, features: PlayerFeatures) -> PlayerSegment:
        """
        Classify player into value segments.

        Args:
            features: Player features

        Returns:
            PlayerSegment with classification and recommendations
        """
        # Calculate lifetime value estimate
        ltv = features.total_deposits - features.total_wins

        # Determine segment tier
        if ltv > 10000 and features.engagement_score > 70:
            tier = "VIP"
            segment_name = "High-Value Active"
            churn_prob = 0.1
            actions = [
                "Assign dedicated account manager",
                "Offer exclusive promotions",
                "Priority customer support",
            ]
        elif ltv > 5000 or features.engagement_score > 60:
            tier = "HIGH"
            segment_name = "Growth Potential"
            churn_prob = 0.2
            actions = [
                "Personalized bonus offers",
                "VIP program invitation",
                "Game recommendations",
            ]
        elif ltv > 1000:
            tier = "MEDIUM"
            segment_name = "Regular Player"
            churn_prob = 0.35
            actions = [
                "Loyalty program engagement",
                "Weekly promotions",
                "New game notifications",
            ]
        elif features.engagement_score < 20 or features.days_since_last_activity > 14:
            tier = "AT_RISK"
            segment_name = "Churn Risk"
            churn_prob = 0.7
            actions = [
                "Win-back campaign",
                "Reactivation bonus",
                "Personalized outreach",
            ]
        else:
            tier = "LOW"
            segment_name = "Casual Player"
            churn_prob = 0.5
            actions = [
                "Engagement campaigns",
                "First deposit bonus",
                "Tutorial content",
            ]

        return PlayerSegment(
            segment_name=segment_name,
            segment_tier=tier,
            lifetime_value=ltv,
            predicted_churn_probability=churn_prob,
            recommended_actions=actions,
        )

    def get_player_insights(
        self,
        player_id: str,
        days: int = 30,
    ) -> dict[str, Any]:
        """
        Get comprehensive player insights.

        Args:
            player_id: Player ID
            days: Analysis window

        Returns:
            Dictionary with player insights
        """
        # Create features
        features = self.create_ml_features(player_id, days)

        # Classify player
        segment = self.classify_player_value(features)

        return {
            "player_id": player_id,
            "analysis_period_days": days,
            "features": features.to_dict(),
            "segment": {
                "name": segment.segment_name,
                "tier": segment.segment_tier,
                "lifetime_value": segment.lifetime_value,
                "churn_probability": segment.predicted_churn_probability,
            },
            "recommendations": segment.recommended_actions,
            "risk_assessment": {
                "risk_score": features.risk_score,
                "risk_level": self._get_risk_level(features.risk_score),
                "risk_indicators": features.risk_indicators,
            },
            "engagement": {
                "score": features.engagement_score,
                "level": self._get_engagement_level(features.engagement_score),
            },
        }

    def _get_risk_level(self, risk_score: float) -> str:
        """Convert risk score to level."""
        if risk_score >= 70:
            return "CRITICAL"
        elif risk_score >= 50:
            return "HIGH"
        elif risk_score >= 30:
            return "MEDIUM"
        else:
            return "LOW"

    def _get_engagement_level(self, engagement_score: float) -> str:
        """Convert engagement score to level."""
        if engagement_score >= 80:
            return "HIGHLY_ENGAGED"
        elif engagement_score >= 60:
            return "ENGAGED"
        elif engagement_score >= 40:
            return "MODERATE"
        elif engagement_score >= 20:
            return "LOW"
        else:
            return "INACTIVE"


def main() -> None:
    """Example usage of Player Analytics."""
    print("Player Analytics Module")
    print("=" * 50)
    print()
    print("This module provides:")
    print("  - ML feature engineering for player modeling")
    print("  - Player segmentation (VIP, HIGH, MEDIUM, LOW, AT_RISK)")
    print("  - Risk indicator detection")
    print("  - Engagement scoring")
    print()
    print("Example usage:")
    print("  analytics = PlayerAnalytics(clickhouse_client)")
    print("  features = analytics.create_ml_features('player_123', days=30)")
    print("  insights = analytics.get_player_insights('player_123')")


if __name__ == "__main__":
    main()
