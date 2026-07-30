# Companion code for "The Backend of Luck" - Chapter 34, Data and Analytics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Behavioral tests for Chapter 34 — Player Analytics models and features."""

import sys
import os

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "data-platform")
)

from player_analytics import PlayerFeatures, PlayerSegment


class TestPlayerFeatures:
    """Validate PlayerFeatures construction and serialization."""

    def test_default_values_are_zero(self):
        features = PlayerFeatures(player_id="p001", feature_window_days=30)
        assert features.total_bets == 0.0
        assert features.total_wins == 0.0
        assert features.risk_score == 0.0
        assert features.engagement_score == 0.0

    def test_to_dict_contains_all_fields(self):
        features = PlayerFeatures(
            player_id="p123",
            feature_window_days=7,
            total_bets=500.0,
            total_wins=450.0,
            avg_bet_size=5.0,
            risk_score=0.3,
        )
        d = features.to_dict()
        assert d["player_id"] == "p123"
        assert d["total_bets"] == 500.0
        assert d["risk_score"] == 0.3
        assert "preferred_games" in d
        assert isinstance(d["preferred_games"], list)

    def test_to_dict_round_trip_preserves_data(self):
        features = PlayerFeatures(
            player_id="p999",
            feature_window_days=30,
            total_events=1000,
            engagement_score=0.85,
        )
        d = features.to_dict()
        restored = PlayerFeatures(**d)
        assert restored.player_id == features.player_id
        assert restored.total_events == features.total_events
        assert restored.engagement_score == features.engagement_score


class TestHourlyStatsAggregator:
    """Validate affiliate stats aggregator logic."""

    def test_debit_events_increment_bet_count(self):
        # Import from affiliate-stats
        sys.path.insert(
            0, os.path.join(os.path.dirname(__file__), "..", "affiliate-stats")
        )
        from affiliate_stats import HourlyStatsAggregator

        agg = HourlyStatsAggregator()
        assert agg.bet_count == 0

        agg.update({"coreInfo": {"userId": 1, "globalId": 10, "eventType": "debit"}})
        assert agg.bet_count == 1
        assert agg.user_id == 1
        assert agg.global_id == 10

    def test_non_debit_events_do_not_increment(self):
        sys.path.insert(
            0, os.path.join(os.path.dirname(__file__), "..", "affiliate-stats")
        )
        from affiliate_stats import HourlyStatsAggregator

        agg = HourlyStatsAggregator()
        agg.update({"coreInfo": {"userId": 2, "globalId": 20, "eventType": "credit"}})
        agg.update({"coreInfo": {"userId": 2, "globalId": 20, "eventType": "deposit"}})
        assert agg.bet_count == 0

    def test_multiple_debits_accumulate(self):
        sys.path.insert(
            0, os.path.join(os.path.dirname(__file__), "..", "affiliate-stats")
        )
        from affiliate_stats import HourlyStatsAggregator

        agg = HourlyStatsAggregator()
        for _ in range(5):
            agg.update({"coreInfo": {"userId": 3, "globalId": 30, "eventType": "debit"}})
        assert agg.bet_count == 5
