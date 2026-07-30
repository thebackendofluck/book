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

"""Tests for the sportsbook <-> prediction-market bridge (chapter 43c)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sportsbook_bridge import (  # noqa: E402
    compare_costs,
    decimal_odds_to_probability,
    detect_discrepancy,
    overround,
    price_cents_to_decimal_odds,
    probability_to_price_cents,
    vig_free_probabilities,
)


class TestConversions:
    def test_odds_to_probability(self):
        assert decimal_odds_to_probability(2.0) == 0.5
        assert decimal_odds_to_probability(4.0) == 0.25

    def test_invalid_odds_raise(self):
        with pytest.raises(ValueError):
            decimal_odds_to_probability(1.0)

    def test_probability_to_price_rounding(self):
        assert probability_to_price_cents(0.5) == 50
        assert probability_to_price_cents(0.634) == 63

    def test_probability_to_price_clamps_extremes(self):
        assert probability_to_price_cents(0.001) == 1
        assert probability_to_price_cents(0.999) == 99

    def test_price_to_odds_round_trip(self):
        odds = price_cents_to_decimal_odds(50)
        assert odds == 2.0
        assert probability_to_price_cents(1 / odds) == 50

    def test_price_to_odds_validates_range(self):
        with pytest.raises(ValueError):
            price_cents_to_decimal_odds(0)


class TestOverround:
    def test_classic_190_190_book(self):
        assert overround(1.90, 1.90) == pytest.approx(0.0526, abs=1e-3)

    def test_fair_book_has_zero_margin(self):
        assert overround(2.0, 2.0) == pytest.approx(0.0)

    def test_vig_free_probabilities_sum_to_one(self):
        pa, pb = vig_free_probabilities(1.90, 1.90)
        assert pa + pb == pytest.approx(1.0)
        assert pa == pytest.approx(0.5)

    def test_vig_free_asymmetric_book(self):
        pa, pb = vig_free_probabilities(1.50, 2.80)
        assert pa + pb == pytest.approx(1.0)
        assert pa > pb


class TestCostComparison:
    def test_exchange_cheaper_at_typical_fees(self):
        # 5.3% overround book vs 100 bps (1%) exchange fee
        c = compare_costs(10_000, 1.90, 1.90, exchange_fee_bps=100)
        assert c.exchange_fee_cents == 100
        assert c.sportsbook_margin_cost_cents > c.exchange_fee_cents
        assert c.cheaper_venue == "prediction_market"

    def test_book_cheaper_when_fees_are_high(self):
        # near-fair book vs a 5% trading fee
        c = compare_costs(10_000, 1.99, 1.99, exchange_fee_bps=500)
        assert c.cheaper_venue == "sportsbook"

    def test_rejects_bad_inputs(self):
        with pytest.raises(ValueError):
            compare_costs(0, 1.9, 1.9, 100)
        with pytest.raises(ValueError):
            compare_costs(100, 1.9, 1.9, -1)


class TestDiscrepancy:
    def test_aligned_venues_not_actionable(self):
        d = detect_discrepancy(2.0, 2.0, 50, exchange_fee_bps=100)
        assert d.actionable is False

    def test_underpriced_market_is_actionable(self):
        # book says 50% fair; market sells YES at 40 cents
        d = detect_discrepancy(2.0, 2.0, 40, exchange_fee_bps=100)
        assert d.edge_after_fees == pytest.approx(0.09)
        assert d.actionable is True

    def test_fees_absorb_small_edges(self):
        # 2 cent gap, 1% fee, 2% threshold -> not actionable
        d = detect_discrepancy(2.0, 2.0, 48, exchange_fee_bps=100)
        assert d.actionable is False
