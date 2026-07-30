# Companion code for "The Backend of Luck" - Chapter 41, Case Study.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Behavioral tests for Chapter 41 — World Cup Scaling Capacity Planning."""

import sys
import os

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "world_cup_scaling")
)

from capacity_planning import WorldCupCapacityPlanning


class TestWorldCupCapacityPlanning:
    """Validate capacity planning calculations and predictions."""

    def _make_planner(self):
        config = {"event": "FIFA World Cup 2026", "base_region": "eu-west-1"}
        return WorldCupCapacityPlanning(event_config=config)

    def test_base_prediction_scales_from_euro_data(self):
        planner = self._make_planner()
        euro_data = {
            "peak_concurrent_users": 450_000,
            "traffic_multiplier": 2.8,
            "revenue_multiplier": 3.2,
        }
        wc_factors = {
            "global_reach_multiplier": 2.5,
            "cultural_impact_multiplier": 2.2,
            "duration_multiplier": 1.8,
        }
        prediction = planner._calculate_base_prediction(euro_data, wc_factors)

        # Should multiply euro peak by global_reach * cultural_impact
        expected_peak = int(450_000 * 2.5 * 2.2)
        assert prediction["peak_concurrent_users"] == expected_peak
        assert prediction["peak_concurrent_users"] > euro_data["peak_concurrent_users"]

    def test_revenue_multiplier_accounts_for_duration(self):
        planner = self._make_planner()
        euro_data = {"peak_concurrent_users": 450_000, "revenue_multiplier": 3.2}
        wc_factors = {
            "global_reach_multiplier": 2.5,
            "cultural_impact_multiplier": 2.2,
            "duration_multiplier": 1.8,
        }
        prediction = planner._calculate_base_prediction(euro_data, wc_factors)
        expected_revenue = 3.2 * 1.8
        assert abs(prediction["revenue_multiplier"] - expected_revenue) < 0.01

    def test_confidence_intervals_bracket_prediction(self):
        planner = self._make_planner()
        prediction = {"peak_concurrent_users": 2_000_000, "confidence": 0.85}
        ci = planner._calculate_prediction_confidence(prediction)

        assert ci["lower_bound"] < 2_000_000
        assert ci["upper_bound"] > 2_000_000
        assert ci["lower_bound"] > 0
        assert ci["confidence_level"] > 0.5

    def test_capacity_recommendations_are_nonempty(self):
        planner = self._make_planner()
        scaling_reqs = {
            "final": {"capacity_multiplier": 8.5, "duration_hours": 5.0}
        }
        recs = planner._generate_capacity_recommendations(scaling_reqs)
        assert len(recs) >= 1
        # Should mention the final multiplier
        assert any("8.5" in r for r in recs)

    def test_strategy_confidence_returns_valid_score(self):
        planner = self._make_planner()
        score = planner._calculate_strategy_confidence([{}, {}])
        assert 0.0 < score <= 1.0

    def test_adjust_for_competition_adds_confidence(self):
        planner = self._make_planner()
        base = {"peak_concurrent_users": 1_000_000, "revenue_multiplier": 5.0}
        adjusted = planner._adjust_for_competition_and_market(base)
        assert "confidence" in adjusted
        assert "confidence_interval" in adjusted
        # Original data preserved
        assert adjusted["peak_concurrent_users"] == 1_000_000
