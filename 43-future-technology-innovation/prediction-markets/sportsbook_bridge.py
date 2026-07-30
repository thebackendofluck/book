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

"""
Bridge between sportsbook odds and prediction-market contract prices.

Chapter 43c reference implementation. The economic difference between a
sportsbook and a prediction market is where the margin lives:

- A sportsbook prices its margin *into the odds* (the overround). A
  two-way market quoted 1.90 / 1.90 implies 52.6% + 52.6% = 105.3% total
  probability -- the extra 5.3% is the book's vig.
- A prediction market quotes vig-free probabilities (YES + NO = 100 by
  construction) and charges an explicit *trading fee* instead.

This module converts between the two representations, extracts the
overround, compares effective cost for the customer, and detects
cross-venue discrepancies large enough to matter after fees.

Odds are decimal odds passed as floats (they are quotes, not money).
Every monetary value is integer cents. Probabilities are floats in
[0, 1]. Contract prices are integer cents in [1, 99].
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from order_book import CONTRACT_PAYOUT_CENTS, MAX_PRICE_CENTS, MIN_PRICE_CENTS


def decimal_odds_to_probability(odds: float) -> float:
    """Implied probability of decimal odds (includes the book's margin)."""
    if odds <= 1.0:
        raise ValueError(f"decimal odds must be > 1.0, got {odds}")
    return 1.0 / odds


def probability_to_price_cents(probability: float) -> int:
    """Round a probability to the nearest valid contract price."""
    if not 0.0 < probability < 1.0:
        raise ValueError(f"probability must be in (0, 1), got {probability}")
    price = round(probability * CONTRACT_PAYOUT_CENTS)
    return max(MIN_PRICE_CENTS, min(MAX_PRICE_CENTS, price))


def price_cents_to_decimal_odds(price_cents: int) -> float:
    """Contract price -> equivalent (vig-free) decimal odds."""
    if not MIN_PRICE_CENTS <= price_cents <= MAX_PRICE_CENTS:
        raise ValueError(f"price {price_cents} outside [1, 99]")
    return CONTRACT_PAYOUT_CENTS / price_cents


def overround(odds_a: float, odds_b: float) -> float:
    """Total implied probability of a two-way book minus 1.

    1.90 / 1.90 -> 0.0526... (a 5.3% margin). A fair book returns 0.
    """
    return decimal_odds_to_probability(odds_a) + \
        decimal_odds_to_probability(odds_b) - 1.0


def vig_free_probabilities(odds_a: float, odds_b: float) -> tuple:
    """Strip the overround proportionally (the standard normalization)."""
    pa = decimal_odds_to_probability(odds_a)
    pb = decimal_odds_to_probability(odds_b)
    total = pa + pb
    return (pa / total, pb / total)


@dataclass(frozen=True)
class CostComparison:
    """Effective cost of the same position at both venue types."""
    stake_cents: int
    sportsbook_margin_cost_cents: int   # what the overround costs the bettor
    exchange_fee_cents: int             # explicit trading fee on the market
    cheaper_venue: str                  # "sportsbook" | "prediction_market"


def compare_costs(stake_cents: int, odds_a: float, odds_b: float,
                  exchange_fee_bps: int) -> CostComparison:
    """Compare margin cost of a sportsbook bet vs an exchange trade.

    The sportsbook's cost to the customer is the overround share of the
    stake; the prediction market's cost is the explicit fee (basis
    points of notional). Both in integer cents, rounded up -- venues
    round in their own favor.
    """
    if stake_cents <= 0:
        raise ValueError("stake must be positive")
    if exchange_fee_bps < 0:
        raise ValueError("fee cannot be negative")

    margin = overround(odds_a, odds_b)
    # bettor pays their proportional share of the margin
    book_cost = -(-int(stake_cents * margin) // 2)          # ceil div by 2
    fee_cost = -(-stake_cents * exchange_fee_bps // 10_000)  # ceil
    return CostComparison(
        stake_cents=stake_cents,
        sportsbook_margin_cost_cents=book_cost,
        exchange_fee_cents=fee_cost,
        cheaper_venue=("prediction_market" if fee_cost < book_cost
                       else "sportsbook"),
    )


@dataclass(frozen=True)
class Discrepancy:
    """A price gap between sportsbook and prediction market on one event."""
    book_implied_probability: float      # vig-free
    market_probability: float
    edge_after_fees: float               # positive = market undervalues YES
    actionable: bool


def detect_discrepancy(book_odds_yes: float, book_odds_no: float,
                       market_yes_price_cents: int,
                       exchange_fee_bps: int,
                       threshold: float = 0.02) -> Discrepancy:
    """Flag when the market's YES price diverges from the book's vig-free
    probability by more than fees + threshold.

    This is the signal market makers watch to keep venues aligned -- and
    the reason liquid prediction markets track sportsbook consensus
    closely during live events.
    """
    fair_yes, _ = vig_free_probabilities(book_odds_yes, book_odds_no)
    market_prob = market_yes_price_cents / CONTRACT_PAYOUT_CENTS
    fee = exchange_fee_bps / 10_000
    edge = fair_yes - market_prob - fee
    return Discrepancy(
        book_implied_probability=fair_yes,
        market_probability=market_prob,
        edge_after_fees=edge,
        actionable=edge > threshold,
    )
