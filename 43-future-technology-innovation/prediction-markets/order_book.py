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
Binary-contract central limit order book (CLOB) for a prediction market.

Chapter 43c reference implementation. Models the matching core used by
event-contract venues (Kalshi-style): every market is a binary contract
that settles at 100 cents if the event resolves YES and 0 cents if NO.

Conventions
-----------
- All prices are integer cents in the open interval (0, 100). A price of
  63 means the market currently implies a 63% probability of YES.
- All monetary amounts are integer cents (see the book's integer-cents
  rule: floats never touch money).
- The book is quoted on the YES side only. "Buy NO at q" is normalized to
  "Sell YES at 100 - q" before it reaches the book -- this is the standard
  complementary-contract transformation and keeps a single price ladder.
- Matching is price-time priority. The taker receives price improvement:
  executions happen at the resting (maker) order's price.
- Self-trade prevention policy is CANCEL_TAKER: when an incoming order
  would trade against the same account's resting order, the remaining
  taker quantity is cancelled instead of executing a wash trade.

Positions are tracked as signed contract counts per account: positive =
long YES, negative = short YES (equivalently long NO). Open interest is
the sum of long positions, which market_lifecycle.py uses at settlement.
"""

from __future__ import annotations

import itertools
from collections import defaultdict, deque
from dataclasses import dataclass
from enum import Enum
from typing import Deque, Dict, List, Optional

MIN_PRICE_CENTS = 1
MAX_PRICE_CENTS = 99
CONTRACT_PAYOUT_CENTS = 100


class Side(str, Enum):
    BUY = "BUY"    # buy YES
    SELL = "SELL"  # sell YES (== buy NO at 100 - price)


class OrderStatus(str, Enum):
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"


def normalize_no_order(no_price_cents: int) -> int:
    """Convert a 'buy NO at q' order into its 'sell YES' price.

    Buying NO at q cents is economically identical to selling YES at
    100 - q cents: both positions pay out when the event resolves NO.
    """
    if not MIN_PRICE_CENTS <= no_price_cents <= MAX_PRICE_CENTS:
        raise ValueError(f"NO price {no_price_cents} outside [1, 99]")
    return CONTRACT_PAYOUT_CENTS - no_price_cents


@dataclass
class Order:
    order_id: int
    account_id: str
    side: Side
    price_cents: int
    quantity: int
    remaining: int
    sequence: int
    status: OrderStatus = OrderStatus.OPEN

    @property
    def filled(self) -> int:
        return self.quantity - self.remaining


@dataclass(frozen=True)
class Trade:
    trade_id: int
    maker_order_id: int
    taker_order_id: int
    maker_account: str
    taker_account: str
    price_cents: int
    quantity: int

    @property
    def notional_cents(self) -> int:
        return self.price_cents * self.quantity


@dataclass
class BookSnapshot:
    best_bid_cents: Optional[int]
    best_ask_cents: Optional[int]
    bid_depth: int
    ask_depth: int
    last_trade_cents: Optional[int]

    @property
    def mid_cents(self) -> Optional[float]:
        if self.best_bid_cents is None or self.best_ask_cents is None:
            return None
        return (self.best_bid_cents + self.best_ask_cents) / 2

    @property
    def implied_probability(self) -> Optional[float]:
        """Mid-price as implied probability of YES, in [0, 1]."""
        mid = self.mid_cents
        return None if mid is None else mid / CONTRACT_PAYOUT_CENTS


class OrderBook:
    """Price-time priority CLOB for one binary market."""

    def __init__(self, market_id: str):
        self.market_id = market_id
        self._order_seq = itertools.count(1)
        self._trade_seq = itertools.count(1)
        # price level -> FIFO queue of resting orders
        self._bids: Dict[int, Deque[Order]] = defaultdict(deque)
        self._asks: Dict[int, Deque[Order]] = defaultdict(deque)
        self._orders: Dict[int, Order] = {}
        self.trades: List[Trade] = []
        self.positions: Dict[str, int] = defaultdict(int)
        self._last_trade_cents: Optional[int] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def submit(self, account_id: str, side: Side, price_cents: int,
               quantity: int) -> Order:
        """Submit a limit order; matches immediately, rests any remainder."""
        if not MIN_PRICE_CENTS <= price_cents <= MAX_PRICE_CENTS:
            raise ValueError(f"price {price_cents} outside [1, 99] cents")
        if quantity <= 0:
            raise ValueError("quantity must be positive")

        seq = next(self._order_seq)
        order = Order(
            order_id=seq,
            account_id=account_id,
            side=side,
            price_cents=price_cents,
            quantity=quantity,
            remaining=quantity,
            sequence=seq,
        )
        self._orders[order.order_id] = order
        self._match(order)
        if order.remaining > 0 and order.status != OrderStatus.CANCELLED:
            self._rest(order)
        return order

    def submit_no(self, account_id: str, no_price_cents: int,
                  quantity: int) -> Order:
        """Submit a 'buy NO' order, normalized to a YES-side sell."""
        return self.submit(
            account_id, Side.SELL, normalize_no_order(no_price_cents), quantity
        )

    def cancel(self, order_id: int) -> bool:
        order = self._orders.get(order_id)
        if order is None or order.remaining == 0:
            return False
        if order.status == OrderStatus.CANCELLED:
            return False
        order.status = OrderStatus.CANCELLED
        self._purge(order)
        return True

    def snapshot(self) -> BookSnapshot:
        return BookSnapshot(
            best_bid_cents=self._best_price(self._bids, is_bid=True),
            best_ask_cents=self._best_price(self._asks, is_bid=False),
            bid_depth=sum(o.remaining for q in self._bids.values() for o in q),
            ask_depth=sum(o.remaining for q in self._asks.values() for o in q),
            last_trade_cents=self._last_trade_cents,
        )

    @property
    def open_interest(self) -> int:
        """Total long-YES contracts outstanding (== total short-YES)."""
        return sum(p for p in self.positions.values() if p > 0)

    # ------------------------------------------------------------------
    # Matching internals
    # ------------------------------------------------------------------

    def _match(self, taker: Order) -> None:
        opposite = self._asks if taker.side == Side.BUY else self._bids
        while taker.remaining > 0:
            best = self._best_price(opposite, is_bid=(taker.side == Side.SELL))
            if best is None or not self._crosses(taker, best):
                break
            queue = opposite[best]
            maker = queue[0]
            if maker.account_id == taker.account_id:
                # CANCEL_TAKER self-trade prevention
                taker.status = OrderStatus.CANCELLED
                return
            fill = min(taker.remaining, maker.remaining)
            self._execute(maker, taker, best, fill)
            if maker.remaining == 0:
                queue.popleft()
                if not queue:
                    del opposite[best]

    def _crosses(self, taker: Order, best_opposite: int) -> bool:
        if taker.side == Side.BUY:
            return taker.price_cents >= best_opposite
        return taker.price_cents <= best_opposite

    def _execute(self, maker: Order, taker: Order, price_cents: int,
                 quantity: int) -> None:
        maker.remaining -= quantity
        taker.remaining -= quantity
        for order in (maker, taker):
            order.status = (
                OrderStatus.FILLED if order.remaining == 0
                else OrderStatus.PARTIALLY_FILLED
            )
        buyer, seller = (taker, maker) if taker.side == Side.BUY else (maker, taker)
        self.positions[buyer.account_id] += quantity
        self.positions[seller.account_id] -= quantity
        trade = Trade(
            trade_id=next(self._trade_seq),
            maker_order_id=maker.order_id,
            taker_order_id=taker.order_id,
            maker_account=maker.account_id,
            taker_account=taker.account_id,
            price_cents=price_cents,
            quantity=quantity,
        )
        self.trades.append(trade)
        self._last_trade_cents = price_cents

    def _rest(self, order: Order) -> None:
        book = self._bids if order.side == Side.BUY else self._asks
        book[order.price_cents].append(order)

    def _purge(self, order: Order) -> None:
        book = self._bids if order.side == Side.BUY else self._asks
        queue = book.get(order.price_cents)
        if queue is None:
            return
        try:
            queue.remove(order)
        except ValueError:
            pass
        if not queue:
            del book[order.price_cents]

    @staticmethod
    def _best_price(levels: Dict[int, Deque[Order]],
                    is_bid: bool) -> Optional[int]:
        if not levels:
            return None
        return max(levels) if is_bid else min(levels)
