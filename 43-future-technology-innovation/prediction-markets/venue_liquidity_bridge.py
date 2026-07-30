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
Cross-venue price mirroring and order routing for co-branded prediction
markets.

Chapter 43c reference implementation, Pattern 3 (the Kalshi <-> ADI
Predictstreet model). Two venues under different regulatory regimes want
to co-brand: each wants to show the other's prices to its own users.
That much is a market-data licensing question and is genuinely solvable
today -- ``MirrorFeed`` implements it. What is *not* solvable today is
letting a user on one venue's front end place an order that executes on
the other venue's book: a CFTC-designated contract market and a
Gibraltar-licensed betting book are different legal animals, and no
framework yet lets order flow cross that line. ``CrossVenueRouter``
exists to make that boundary impossible to route around by accident --
every cross-regime call raises, with the regulatory reason attached, so
the mechanical readiness of a routing API is never mistaken for legal
readiness.

Conventions
-----------
- Prices are integer cents, per order_book.py's contract convention.
- ``MirrorQuote`` never carries a quantity or an order id -- there is
  nothing in it a client could submit as an order. That is the whole
  point of a read-only mirror.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple

from order_book import CONTRACT_PAYOUT_CENTS, BookSnapshot

__all__ = [
    "VenueRegime",
    "Venue",
    "ROUTING_COMPATIBILITY",
    "DualRegimeViolation",
    "MirrorQuote",
    "MirrorFeed",
    "CrossVenueRouter",
]


class VenueRegime(str, Enum):
    CFTC_DCM = "CFTC_DCM"
    GIBRALTAR_BETTING_INTERMEDIARY = "GIBRALTAR_BETTING_INTERMEDIARY"
    UK_POOL_BETTING = "UK_POOL_BETTING"
    UNLICENSED = "UNLICENSED"


@dataclass(frozen=True)
class Venue:
    venue_id: str            # "kalshi", "adi-predictstreet"
    regime: VenueRegime


# Reasons cross-regime order routing is blocked, keyed by the unordered
# pair of regimes. Same-regime pairs are "allowed" (routing mechanics
# work fine when both venues answer to the same regulator). Every
# cross-regime pair, and anything touching UNLICENSED, is a reason
# string -- this table is the legal gate; CrossVenueRouter never
# overrides it.
_CROSS_REGIME_REASONS: Dict[frozenset, str] = {
    frozenset({VenueRegime.CFTC_DCM, VenueRegime.GIBRALTAR_BETTING_INTERMEDIARY}):
        "CFTC-designated order flow cannot execute on a gambling-licensed "
        "book without CFTC designation of that book, and a Gibraltar "
        "betting-intermediary order cannot execute on a CFTC DCM without "
        "the DCM's own CFTC authorization.",
    frozenset({VenueRegime.CFTC_DCM, VenueRegime.UK_POOL_BETTING}):
        "CFTC-designated order flow cannot execute on a UK Gambling "
        "Commission pool-betting book without CFTC designation of that "
        "book, and a UK pool-betting order cannot execute on a CFTC DCM "
        "without the DCM's own CFTC authorization.",
    frozenset({VenueRegime.GIBRALTAR_BETTING_INTERMEDIARY, VenueRegime.UK_POOL_BETTING}):
        "Gibraltar and UK gambling licences are not mutually passportable "
        "for order execution; each regulator authorizes order flow only "
        "on its own licensed book.",
}


def _unlicensed_reason() -> str:
    return (
        "one side of the route holds no recognized licence "
        f"({VenueRegime.UNLICENSED.value}); routing is blocked regardless "
        "of the other side's regime."
    )


def _build_routing_matrix() -> Dict[Tuple[VenueRegime, VenueRegime], str]:
    matrix: Dict[Tuple[VenueRegime, VenueRegime], str] = {}
    for a in VenueRegime:
        for b in VenueRegime:
            if a == b:
                matrix[(a, b)] = "allowed"
            elif VenueRegime.UNLICENSED in (a, b):
                matrix[(a, b)] = _unlicensed_reason()
            else:
                matrix[(a, b)] = _CROSS_REGIME_REASONS[frozenset({a, b})]
    return matrix


ROUTING_COMPATIBILITY: Dict[Tuple[VenueRegime, VenueRegime], str] = (
    _build_routing_matrix()
)


class DualRegimeViolation(Exception):
    pass


@dataclass(frozen=True)
class MirrorQuote:
    """A display-only quote. There is nothing here a client can submit
    as an order: no quantity, no order id, no side."""
    market_id: str
    source_venue_id: str
    yes_price_cents: Optional[int]
    implied_probability: Optional[float]
    disclaimer: str
    display_only: bool = True


class MirrorFeed:
    """Read-only price mirroring for co-branded display."""

    def __init__(self, source_venue: Venue):
        self.source_venue = source_venue

    def mirror(self, market_id: str, snapshot: BookSnapshot) -> MirrorQuote:
        mid = snapshot.mid_cents  # None-safe: BookSnapshot handles the empty book
        yes_price_cents = None if mid is None else round(mid)
        implied_probability = (
            None if yes_price_cents is None
            else yes_price_cents / CONTRACT_PAYOUT_CENTS
        )
        return MirrorQuote(
            market_id=market_id,
            source_venue_id=self.source_venue.venue_id,
            yes_price_cents=yes_price_cents,
            implied_probability=implied_probability,
            disclaimer=(
                f"prices sourced from {self.source_venue.venue_id}; "
                "display only -- orders execute on your local venue"
            ),
            display_only=True,
        )


class CrossVenueRouter:
    """Order routing across venues, gated by regulatory regime.

    The routing mechanics here are deliberately trivial -- that is the
    point. Once two venues expose an order API to each other, nothing
    technical stops order flow from crossing. What stops it is
    ROUTING_COMPATIBILITY: every cross-regime pair raises, unconditionally,
    so a platform team cannot accidentally ship the "would work fine"
    code path before the legal framework exists to permit it.
    """

    def __init__(self):
        self.audit_log: List[Tuple[str, Venue, Venue, str]] = []

    def route_order(
        self,
        from_venue: Venue,
        to_venue: Venue,
        market_id: str,
        side: str,
        price_cents: int,
        quantity: int,
    ) -> None:
        if from_venue.venue_id == to_venue.venue_id:
            raise ValueError(
                f"routing to self: {from_venue.venue_id!r}"
            )

        reason = ROUTING_COMPATIBILITY[(from_venue.regime, to_venue.regime)]
        if reason != "allowed":
            raise DualRegimeViolation(
                f"{from_venue.venue_id} ({from_venue.regime.value}) -> "
                f"{to_venue.venue_id} ({to_venue.regime.value}): {reason}"
            )

        self.audit_log.append(
            ("would-route", from_venue, to_venue, market_id)
        )
