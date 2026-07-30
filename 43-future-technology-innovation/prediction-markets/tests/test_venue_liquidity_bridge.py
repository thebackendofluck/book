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

"""Tests for cross-venue price mirroring and order routing
(chapter 43c, Pattern 3)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from order_book import BookSnapshot  # noqa: E402
from venue_liquidity_bridge import (  # noqa: E402
    CrossVenueRouter,
    DualRegimeViolation,
    MirrorFeed,
    Venue,
    VenueRegime,
)

KALSHI = Venue("kalshi", VenueRegime.CFTC_DCM)
ADI = Venue("adi-predictstreet", VenueRegime.GIBRALTAR_BETTING_INTERMEDIARY)
TOTE = Venue("tote-uk", VenueRegime.UK_POOL_BETTING)
GREY_MARKET = Venue("grey-market-book", VenueRegime.UNLICENSED)


class TestMirrorFeed:
    def test_mirror_computes_mid_and_implied_probability(self):
        feed = MirrorFeed(KALSHI)
        snapshot = BookSnapshot(
            best_bid_cents=48,
            best_ask_cents=52,
            bid_depth=100,
            ask_depth=100,
            last_trade_cents=50,
        )
        quote = feed.mirror("EVT-1", snapshot)
        assert quote.yes_price_cents == 50
        assert quote.implied_probability == 0.5
        assert quote.display_only is True
        assert "kalshi" in quote.disclaimer

    def test_mirror_empty_book_is_none_safe(self):
        feed = MirrorFeed(KALSHI)
        snapshot = BookSnapshot(
            best_bid_cents=None,
            best_ask_cents=None,
            bid_depth=0,
            ask_depth=0,
            last_trade_cents=None,
        )
        quote = feed.mirror("EVT-1", snapshot)
        assert quote.yes_price_cents is None
        assert quote.implied_probability is None
        assert quote.display_only is True

    def test_mirror_never_carries_executable_fields(self):
        feed = MirrorFeed(ADI)
        snapshot = BookSnapshot(
            best_bid_cents=30,
            best_ask_cents=34,
            bid_depth=10,
            ask_depth=10,
            last_trade_cents=32,
        )
        quote = feed.mirror("EVT-2", snapshot)
        assert not hasattr(quote, "quantity")
        assert not hasattr(quote, "order_id")
        assert quote.source_venue_id == "adi-predictstreet"


class TestCrossVenueRouterViolations:
    def test_kalshi_to_adi_raises_dual_regime_violation(self):
        router = CrossVenueRouter()
        with pytest.raises(DualRegimeViolation) as exc:
            router.route_order(
                KALSHI, ADI, "EVT-1", "BUY", price_cents=50, quantity=10
            )
        message = str(exc.value)
        assert "CFTC" in message

    def test_adi_to_kalshi_raises_both_directions(self):
        router = CrossVenueRouter()
        with pytest.raises(DualRegimeViolation):
            router.route_order(
                ADI, KALSHI, "EVT-1", "BUY", price_cents=50, quantity=10
            )

    def test_unlicensed_blocked_both_directions(self):
        router = CrossVenueRouter()
        with pytest.raises(DualRegimeViolation):
            router.route_order(
                KALSHI, GREY_MARKET, "EVT-1", "BUY", price_cents=50, quantity=10
            )
        with pytest.raises(DualRegimeViolation):
            router.route_order(
                GREY_MARKET, KALSHI, "EVT-1", "BUY", price_cents=50, quantity=10
            )

    def test_gibraltar_to_uk_pool_betting_raises(self):
        router = CrossVenueRouter()
        with pytest.raises(DualRegimeViolation):
            router.route_order(
                ADI, TOTE, "EVT-1", "BUY", price_cents=50, quantity=10
            )

    def test_routing_to_self_raises_value_error(self):
        router = CrossVenueRouter()
        with pytest.raises(ValueError):
            router.route_order(
                KALSHI, KALSHI, "EVT-1", "BUY", price_cents=50, quantity=10
            )


class TestCrossVenueRouterSameRegime:
    def test_same_regime_different_venue_appends_would_route(self):
        router = CrossVenueRouter()
        other_cftc_venue = Venue("cme-event-contracts", VenueRegime.CFTC_DCM)
        result = router.route_order(
            KALSHI, other_cftc_venue, "EVT-1", "BUY",
            price_cents=50, quantity=10,
        )
        assert result is None
        assert len(router.audit_log) == 1
        kind, from_venue, to_venue, market_id = router.audit_log[0]
        assert kind == "would-route"
        assert from_venue == KALSHI
        assert to_venue == other_cftc_venue
        assert market_id == "EVT-1"
