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

"""Tests for the binary-contract order book (chapter 43c)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from order_book import (  # noqa: E402
    CONTRACT_PAYOUT_CENTS,
    OrderBook,
    OrderStatus,
    Side,
    normalize_no_order,
)


@pytest.fixture
def book():
    return OrderBook("wc2026-final-yes")


class TestValidation:
    def test_rejects_price_zero(self, book):
        with pytest.raises(ValueError):
            book.submit("a1", Side.BUY, 0, 10)

    def test_rejects_price_100(self, book):
        with pytest.raises(ValueError):
            book.submit("a1", Side.BUY, 100, 10)

    def test_rejects_non_positive_quantity(self, book):
        with pytest.raises(ValueError):
            book.submit("a1", Side.BUY, 50, 0)

    def test_no_normalization(self):
        assert normalize_no_order(40) == 60
        assert normalize_no_order(1) == 99
        with pytest.raises(ValueError):
            normalize_no_order(0)


class TestMatching:
    def test_no_match_when_spread_open(self, book):
        book.submit("a1", Side.BUY, 40, 10)
        book.submit("a2", Side.SELL, 60, 10)
        snap = book.snapshot()
        assert snap.best_bid_cents == 40
        assert snap.best_ask_cents == 60
        assert book.trades == []

    def test_crossing_order_executes_at_maker_price(self, book):
        book.submit("a1", Side.SELL, 55, 10)      # maker
        taker = book.submit("a2", Side.BUY, 60, 10)  # willing to pay 60
        assert len(book.trades) == 1
        assert book.trades[0].price_cents == 55   # price improvement
        assert taker.status == OrderStatus.FILLED

    def test_partial_fill_rests_remainder(self, book):
        book.submit("a1", Side.SELL, 50, 4)
        taker = book.submit("a2", Side.BUY, 50, 10)
        assert taker.filled == 4
        assert taker.remaining == 6
        assert taker.status == OrderStatus.PARTIALLY_FILLED
        assert book.snapshot().best_bid_cents == 50

    def test_price_priority_beats_time(self, book):
        book.submit("a1", Side.SELL, 58, 5)
        book.submit("a2", Side.SELL, 55, 5)   # better ask, later in time
        book.submit("a3", Side.BUY, 60, 5)
        assert book.trades[0].maker_account == "a2"
        assert book.trades[0].price_cents == 55

    def test_time_priority_within_level(self, book):
        book.submit("a1", Side.SELL, 55, 5)
        book.submit("a2", Side.SELL, 55, 5)
        book.submit("a3", Side.BUY, 55, 5)
        assert book.trades[0].maker_account == "a1"

    def test_sweep_through_multiple_levels(self, book):
        book.submit("a1", Side.SELL, 52, 5)
        book.submit("a2", Side.SELL, 54, 5)
        taker = book.submit("a3", Side.BUY, 55, 8)
        assert taker.filled == 8
        prices = [t.price_cents for t in book.trades]
        assert prices == [52, 54]

    def test_buy_no_matches_yes_buyer(self, book):
        # a1 buys YES at 60; a2 buys NO at 40 == sells YES at 60
        book.submit("a1", Side.BUY, 60, 10)
        no_order = book.submit_no("a2", 40, 10)
        assert no_order.status == OrderStatus.FILLED
        assert book.trades[0].price_cents == 60


class TestSelfTradePrevention:
    def test_cancel_taker_on_self_cross(self, book):
        book.submit("a1", Side.SELL, 50, 10)
        taker = book.submit("a1", Side.BUY, 55, 10)
        assert taker.status == OrderStatus.CANCELLED
        assert book.trades == []
        # resting order untouched
        assert book.snapshot().ask_depth == 10

    def test_self_prevention_after_partial_fill(self, book):
        book.submit("a2", Side.SELL, 50, 4)   # other account, better price
        book.submit("a1", Side.SELL, 51, 10)  # own order next level
        taker = book.submit("a1", Side.BUY, 55, 10)
        assert taker.filled == 4              # took the stranger's 4
        assert taker.status == OrderStatus.CANCELLED  # then cancelled


class TestPositionsAndData:
    def test_positions_are_zero_sum(self, book):
        book.submit("a1", Side.SELL, 50, 7)
        book.submit("a2", Side.BUY, 50, 7)
        assert book.positions["a2"] == 7
        assert book.positions["a1"] == -7
        assert sum(book.positions.values()) == 0
        assert book.open_interest == 7

    def test_cancel_removes_liquidity(self, book):
        order = book.submit("a1", Side.BUY, 45, 10)
        assert book.cancel(order.order_id) is True
        assert book.snapshot().best_bid_cents is None
        assert book.cancel(order.order_id) is False  # idempotent

    def test_snapshot_mid_and_probability(self, book):
        book.submit("a1", Side.BUY, 48, 10)
        book.submit("a2", Side.SELL, 52, 10)
        snap = book.snapshot()
        assert snap.mid_cents == 50
        assert snap.implied_probability == 0.5

    def test_trade_notional(self, book):
        book.submit("a1", Side.SELL, 63, 10)
        book.submit("a2", Side.BUY, 63, 10)
        assert book.trades[0].notional_cents == 630
        assert CONTRACT_PAYOUT_CENTS == 100
