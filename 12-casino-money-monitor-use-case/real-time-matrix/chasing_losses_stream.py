# Companion code for "The Backend of Luck" - Chapter 12, Real-Time Cash Flow Management for Online Casinos.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Chasing Losses Stream
# Source: Production casino platform (sanitized)
# Chapter 12 - Casino Money Monitor
#
# Kafka Streams-style processor (implemented with kafka-python) that detects
# "chasing losses" patterns: players escalating bet sizes after losing streaks
# or making progressively larger deposits.
#
# The detection uses a "two-triplets" algorithm:
#   - Group wagers/deposits into sliding windows of 3 (a "triplet")
#   - Compare the average of the most-recent triplet vs the previous one
#   - If the ratio exceeds a configured threshold and the player's net
#     cash position is negative, flag as an actionable case
# =============================================================================

from __future__ import annotations

import json
import logging
import os
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from models import (
    AccountsEvent,
    CHASING_LOSSES_DEBITS_CRITERIA_MET,
    CHASING_LOSSES_DEPOSITS_CRITERIA_MET,
    RgActionableCaseMessage,
    RtmxScoreMessage,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Settings (in production these come from a DB-backed settings service)
# ---------------------------------------------------------------------------

CASH_HOLD_TIME_WINDOW_HOURS: int = int(os.getenv("CHASING_LOSSES_CASH_HOLD_TIME_WINDOW", "24"))
CASH_HOLD_MIN_GBP: float = float(os.getenv("CHASING_LOSSES_CASH_HOLD_MIN", "250"))
IGNORE_CASH_HOLD_CRITERIA: bool = os.getenv("CHASING_LOSSES_IGNORE_CASH_HOLD", "false") == "true"
AUDIT_SINK_SWITCH: bool = os.getenv("CHASING_LOSSES_AUDIT_SINK_SWITCH", "true") == "true"
INTERACTIONS_SINK_SWITCH: bool = os.getenv("CHASING_LOSSES_INTERACTIONS_SINK_SWITCH", "false") == "true"
RG_ACTION_COOLDOWN_HOURS: int = int(os.getenv("CHASING_LOSSES_RG_ACTION_COOLDOWN", "24"))

CASH_HOLD_RELEVANT_TYPES = {"debit", "credit", "refund"}
DEBIT_TRIPLET_TYPE = "debit"
DEPOSIT_TRIPLET_TYPE = "deposit"


# ---------------------------------------------------------------------------
# Triplet data structures
# ---------------------------------------------------------------------------


@dataclass
class AccountsEventLite:
    timestamp: datetime
    amount: int
    event_type: str
    global_id: int

    @classmethod
    def from_event(cls, event: AccountsEvent) -> "AccountsEventLite":
        sign = 1 if event.transaction_details.is_increment else -1
        return cls(
            timestamp=event.core_info.timestamp,
            amount=event.transaction_details.total_amount * sign,
            event_type=event.core_info.event_type,
            global_id=event.core_info.global_id or -1000,
        )


@dataclass
class Triplet:
    """Sliding window of the last 3 events of a given type."""
    _events: deque = field(default_factory=lambda: deque(maxlen=3))

    def append(self, event: AccountsEvent) -> Optional[AccountsEvent]:
        """Append event; if the window overflows, return the evicted item."""
        evicted: Optional[AccountsEvent] = None
        if len(self._events) == 3:
            evicted = self._events[0]
        self._events.append(event)
        return evicted

    def get_average(self, to_gbp_fn) -> float:
        if not self._events:
            return 0.0
        total = sum(e.transaction_details.total_amount for e in self._events)
        currency = self._events[0].transaction_details.currency
        return to_gbp_fn(total / len(self._events), currency)

    def get_timestamps(self) -> str:
        return ",".join(str(e.core_info.timestamp) for e in self._events)

    @property
    def size(self) -> int:
        return len(self._events)


@dataclass
class TwoTriplets:
    """Maintains the last two triplets for comparison."""
    last_triplet: Triplet = field(default_factory=Triplet)
    second_last_triplet: Triplet = field(default_factory=Triplet)

    def append(self, event: AccountsEvent) -> None:
        evicted = self.last_triplet.append(event)
        if evicted is not None:
            self.second_last_triplet.append(evicted)

    def get_ratio(self, to_gbp_fn) -> float:
        last_avg = self.last_triplet.get_average(to_gbp_fn)
        second_avg = self.second_last_triplet.get_average(to_gbp_fn)
        if second_avg == 0:
            return 0.0
        return last_avg / second_avg

    def get_timestamps(self) -> str:
        return f"{self.last_triplet.get_timestamps()};{self.second_last_triplet.get_timestamps()}"


# ---------------------------------------------------------------------------
# Aggregator state per player (keyed by global_id)
# ---------------------------------------------------------------------------


@dataclass
class Aggregator:
    window_hours: int = CASH_HOLD_TIME_WINDOW_HOURS
    cash_hold: int = 0
    user_id: Optional[int] = None
    global_id: Optional[int] = None
    last_action_timestamp: Optional[datetime] = None
    currency: Optional[str] = None
    debit_triplets: TwoTriplets = field(default_factory=TwoTriplets)
    deposit_triplets: TwoTriplets = field(default_factory=TwoTriplets)
    _events: deque = field(default_factory=deque)

    def get_cash_hold(self, to_gbp_fn) -> float:
        currency = self.currency or "GBP"
        return to_gbp_fn(self.cash_hold, currency) * -1

    def update(self, event: AccountsEvent) -> "Aggregator":
        lite = AccountsEventLite.from_event(event)
        window = timedelta(hours=self.window_hours)

        # Add to sliding window queue
        self._events.append(lite)

        # Evict events outside the time window
        while self._events and (lite.timestamp - self._events[0].timestamp) > window:
            removed = self._events.popleft()
            if removed.event_type in CASH_HOLD_RELEVANT_TYPES:
                self.cash_hold -= removed.amount

        # Update cash hold
        if lite.event_type in CASH_HOLD_RELEVANT_TYPES:
            self.cash_hold += lite.amount

        # Update triplets
        if lite.event_type == DEBIT_TRIPLET_TYPE:
            self.debit_triplets.append(event)
        if lite.event_type == DEPOSIT_TRIPLET_TYPE:
            self.deposit_triplets.append(event)

        self.user_id = self.user_id or event.core_info.user_id
        self.global_id = self.global_id or event.core_info.global_id
        self.last_action_timestamp = lite.timestamp
        self.currency = self.currency or event.transaction_details.currency
        return self


# ---------------------------------------------------------------------------
# Matched case detection
# ---------------------------------------------------------------------------

def _check_debit_match(agg: Aggregator, to_gbp_fn) -> Optional[int]:
    """
    Case matching table (default parameters):
    +------------------------------+----------------+--------------+----------+
    | secondLastDebitTriplet       | debitRatio     | lastTriplet  | cashHold |
    +------------------------------+----------------+--------------+----------+
    | <= 100 GBP                   | -              | >= 500 GBP   | > 250    |
    | >100 and <=200               | >= 2.5         | -            | > 250    |
    | >200 and <=300               | >= 2.0         | -            | > 250    |
    | >300                         | >= 1.5         | -            | > 250    |
    +------------------------------+----------------+--------------+----------+
    """
    t = agg.debit_triplets
    if t.last_triplet.size < 3 or t.second_last_triplet.size < 3:
        return None

    cash_hold = agg.get_cash_hold(to_gbp_fn)
    if cash_hold < 250:
        return None

    second_avg = t.second_last_triplet.get_average(to_gbp_fn) / 100
    last_avg   = t.last_triplet.get_average(to_gbp_fn) / 100
    ratio      = t.get_ratio(to_gbp_fn)

    if second_avg <= 100 and last_avg >= 500:
        return 1
    if 100 < second_avg <= 200 and ratio >= 2.5:
        return 2
    if 200 < second_avg <= 300 and ratio >= 2.0:
        return 3
    if second_avg > 300 and ratio >= 1.5:
        return 4
    return None


def _check_deposit_match(agg: Aggregator, to_gbp_fn) -> Optional[int]:
    """
    Case matching table (default parameters):
    +------------------------------+----------------+---------------+----------+
    | secondLastDepositTriplet     | depositRatio   | lastTriplet   | cashHold |
    +------------------------------+----------------+---------------+----------+
    | <= 50 GBP                    | -              | >= 500 GBP    | > 250    |
    | >50 and <=100                | >= 5           | -             | > 250    |
    | >100 and <=250               | >= 2.5         | -             | > 250    |
    | >250                         | >= 2           | -             | > 250    |
    +------------------------------+----------------+---------------+----------+
    """
    t = agg.deposit_triplets
    if t.last_triplet.size < 3 or t.second_last_triplet.size < 3:
        return None

    cash_hold = agg.get_cash_hold(to_gbp_fn)
    if cash_hold < 250:
        return None

    second_avg = t.second_last_triplet.get_average(to_gbp_fn) / 100
    last_avg   = t.last_triplet.get_average(to_gbp_fn) / 100
    ratio      = t.get_ratio(to_gbp_fn)

    if second_avg <= 50 and last_avg >= 500:
        return 1
    if 50 < second_avg <= 100 and ratio >= 5:
        return 2
    if 100 < second_avg <= 250 and ratio >= 2.5:
        return 3
    if second_avg > 250 and ratio >= 2.0:
        return 4
    return None


# ---------------------------------------------------------------------------
# Stream processor
# ---------------------------------------------------------------------------


class ChasingLossesProcessor:
    """
    Processes AccountsEvent records from Kafka, maintains per-player
    aggregation state, and emits RgActionableCaseMessage alerts when
    chasing-losses patterns are detected.

    In a full production setup this uses Kafka Streams with RocksDB-backed
    state stores. Here the state is kept in-memory as a dict keyed by
    global_id, suitable for single-instance or demonstration usage.
    """

    def __init__(self, to_gbp_fn=None) -> None:
        self._state: dict[int, Aggregator] = {}
        # to_gbp_fn(amount_minor_units, currency) -> float in GBP minor units
        self._to_gbp = to_gbp_fn or (lambda amount, currency: float(amount))

    def _get_or_create(self, global_id: int) -> Aggregator:
        if global_id not in self._state:
            self._state[global_id] = Aggregator()
        return self._state[global_id]

    def process(self, event: AccountsEvent) -> list[RgActionableCaseMessage]:
        if event.core_info.global_id is None:
            return []
        if event.core_info.event_type not in (
            CASH_HOLD_RELEVANT_TYPES | {DEBIT_TRIPLET_TYPE, DEPOSIT_TRIPLET_TYPE}
        ):
            return []

        global_id = event.core_info.global_id
        agg = self._get_or_create(global_id)
        agg.update(event)

        if not AUDIT_SINK_SWITCH:
            return []

        results: list[RgActionableCaseMessage] = []

        # Check debits
        debit_case = _check_debit_match(agg, self._to_gbp)
        if debit_case is not None:
            t = agg.debit_triplets
            results.append(RgActionableCaseMessage(
                global_id=global_id,
                user_id=agg.user_id,
                brand_id=None,
                stream_tag="chasing-losses",
                description="chasing losses interaction type",
                params={
                    "globalId": global_id,
                    "cashHold": agg.get_cash_hold(self._to_gbp),
                    "lastTripletAverage": t.last_triplet.get_average(self._to_gbp),
                    "secondLastTripletAverage": t.second_last_triplet.get_average(self._to_gbp),
                    "ratio": t.get_ratio(self._to_gbp),
                    "chasingLossesCase": debit_case,
                    "modelGroup": "CL_BETS",
                    "lastActionTimestamp": str(agg.last_action_timestamp),
                    "caseHash": t.get_timestamps(),
                    "matrixEvent": CHASING_LOSSES_DEBITS_CRITERIA_MET,
                    "matrixData": {
                        "secondLastDebitTriplet": t.second_last_triplet.get_average(self._to_gbp) / 100,
                        "lastDebitTriplet": t.last_triplet.get_average(self._to_gbp) / 100,
                        "debitTripletsRatio": t.get_ratio(self._to_gbp),
                        "cashHold": agg.get_cash_hold(self._to_gbp) / 100,
                    },
                },
                created_at=datetime.now(timezone.utc),
            ))

        # Check deposits
        deposit_case = _check_deposit_match(agg, self._to_gbp)
        if deposit_case is not None:
            t = agg.deposit_triplets
            results.append(RgActionableCaseMessage(
                global_id=global_id,
                user_id=agg.user_id,
                brand_id=None,
                stream_tag="chasing-losses",
                description="chasing losses interaction type",
                params={
                    "globalId": global_id,
                    "cashHold": agg.get_cash_hold(self._to_gbp),
                    "lastTripletAverage": t.last_triplet.get_average(self._to_gbp),
                    "secondLastTripletAverage": t.second_last_triplet.get_average(self._to_gbp),
                    "ratio": t.get_ratio(self._to_gbp),
                    "chasingLossesCase": deposit_case,
                    "modelGroup": "CL_DEPOSITS",
                    "lastActionTimestamp": str(agg.last_action_timestamp),
                    "caseHash": t.get_timestamps(),
                    "matrixEvent": CHASING_LOSSES_DEPOSITS_CRITERIA_MET,
                    "matrixData": {
                        "secondLastDepositTriplet": t.second_last_triplet.get_average(self._to_gbp) / 100,
                        "lastDepositTriplet": t.last_triplet.get_average(self._to_gbp) / 100,
                        "depositTripletsRatio": t.get_ratio(self._to_gbp),
                        "cashHold": agg.get_cash_hold(self._to_gbp) / 100,
                    },
                },
                created_at=datetime.now(timezone.utc),
            ))

        return results
