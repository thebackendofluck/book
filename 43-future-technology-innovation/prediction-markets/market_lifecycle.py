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
Market lifecycle, oracle resolution and settlement for a prediction market.

Chapter 43c reference implementation. Covers the part of the stack that
gambling regulators care about most: how a market is defined, who decides
the outcome, what happens when the outcome is disputed, and how winners
get paid.

Design points
-------------
- A market moves through an explicit state machine. Invalid transitions
  raise ``InvalidTransition`` -- state is never silently coerced.
- Resolution criteria are written down at market creation (question,
  authoritative source, resolution deadline). Gibraltar licensed
  ADI Predictstreet as a *betting intermediary* partly because its markets
  publish exactly this: a yes/no question with a defined resolution date
  and criteria.
- Outcome determination goes through an oracle adapter. The bundled
  ``AttestationOracle`` requires a quorum of independent attesters (the
  two-person rule applied to market resolution).
- After resolution there is a dispute window. Settlement is only allowed
  once the window has elapsed without an accepted dispute.
- Settlement pays 100 cents per winning contract (integer cents only).
  A voided market refunds each account's cost basis instead.

The clock is injected as a callable so tests are deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, List, Optional, Protocol

from order_book import CONTRACT_PAYOUT_CENTS


class MarketStatus(str, Enum):
    DRAFT = "DRAFT"
    OPEN = "OPEN"
    SUSPENDED = "SUSPENDED"
    CLOSED = "CLOSED"          # trading halted, awaiting outcome
    RESOLVED = "RESOLVED"      # outcome posted, dispute window running
    SETTLED = "SETTLED"        # payouts computed, terminal
    VOIDED = "VOIDED"          # refunds computed, terminal


class Outcome(str, Enum):
    YES = "YES"
    NO = "NO"
    UNDETERMINED = "UNDETERMINED"


class MarketCategory(str, Enum):
    """Categories mirroring the Gibraltar phased-approval model."""
    FOOTBALL = "FOOTBALL"
    OTHER_SPORTS = "OTHER_SPORTS"
    ENTERTAINMENT = "ENTERTAINMENT"
    CULTURE = "CULTURE"
    WEATHER = "WEATHER"
    POLITICS = "POLITICS"
    ECONOMICS = "ECONOMICS"
    FINANCE = "FINANCE"


_ALLOWED_TRANSITIONS = {
    MarketStatus.DRAFT: {MarketStatus.OPEN},
    MarketStatus.OPEN: {MarketStatus.SUSPENDED, MarketStatus.CLOSED},
    MarketStatus.SUSPENDED: {MarketStatus.OPEN, MarketStatus.CLOSED},
    MarketStatus.CLOSED: {MarketStatus.RESOLVED, MarketStatus.VOIDED},
    MarketStatus.RESOLVED: {MarketStatus.SETTLED, MarketStatus.CLOSED,
                            MarketStatus.VOIDED},
    MarketStatus.SETTLED: set(),
    MarketStatus.VOIDED: set(),
}


class InvalidTransition(Exception):
    pass


class SettlementError(Exception):
    pass


@dataclass(frozen=True)
class ResolutionCriteria:
    """The contract's fine print, fixed at market creation."""
    question: str                 # "Will team X win match Y?"
    source: str                   # authoritative source, e.g. "FIFA official result"
    resolves_by_epoch: float      # hard deadline for resolution
    void_if_unresolvable: bool = True


class OracleAdapter(Protocol):
    """Anything that can produce an outcome for a market."""

    def outcome_for(self, market_id: str) -> Outcome: ...


class AttestationOracle:
    """Quorum-based manual oracle (two-person rule for resolutions).

    Attesters independently post an outcome; the oracle only reports a
    determined outcome once ``quorum`` attesters agree. Conflicting
    attestations below quorum keep the market UNDETERMINED, which in
    practice routes it to a supervisor queue.
    """

    def __init__(self, quorum: int = 2):
        if quorum < 1:
            raise ValueError("quorum must be >= 1")
        self.quorum = quorum
        self._attestations: Dict[str, Dict[str, Outcome]] = {}

    def attest(self, market_id: str, attester_id: str,
               outcome: Outcome) -> None:
        if outcome == Outcome.UNDETERMINED:
            raise ValueError("attesters must post YES or NO")
        self._attestations.setdefault(market_id, {})[attester_id] = outcome

    def outcome_for(self, market_id: str) -> Outcome:
        votes = self._attestations.get(market_id, {})
        for candidate in (Outcome.YES, Outcome.NO):
            if sum(1 for v in votes.values() if v == candidate) >= self.quorum:
                return candidate
        return Outcome.UNDETERMINED


@dataclass
class Dispute:
    account_id: str
    reason: str
    raised_at_epoch: float
    accepted: Optional[bool] = None


@dataclass
class SettlementReport:
    market_id: str
    outcome: Outcome
    payouts_cents: Dict[str, int]
    total_paid_cents: int
    voided: bool = False


class Market:
    """One binary event contract with lifecycle enforcement."""

    def __init__(self, market_id: str, category: MarketCategory,
                 criteria: ResolutionCriteria,
                 clock: Callable[[], float],
                 dispute_window_seconds: float = 3600.0):
        self.market_id = market_id
        self.category = category
        self.criteria = criteria
        self._clock = clock
        self.dispute_window_seconds = dispute_window_seconds
        self.status = MarketStatus.DRAFT
        self.outcome: Optional[Outcome] = None
        self.resolved_at_epoch: Optional[float] = None
        self.disputes: List[Dispute] = []
        self.history: List[MarketStatus] = [MarketStatus.DRAFT]

    # -- state machine -------------------------------------------------

    def _transition(self, target: MarketStatus) -> None:
        if target not in _ALLOWED_TRANSITIONS[self.status]:
            raise InvalidTransition(
                f"{self.market_id}: {self.status.value} -> {target.value}"
            )
        self.status = target
        self.history.append(target)

    def open(self) -> None:
        self._transition(MarketStatus.OPEN)

    def suspend(self) -> None:
        self._transition(MarketStatus.SUSPENDED)

    def reopen(self) -> None:
        if self.status != MarketStatus.SUSPENDED:
            raise InvalidTransition(f"cannot reopen from {self.status.value}")
        self._transition(MarketStatus.OPEN)

    def close(self) -> None:
        self._transition(MarketStatus.CLOSED)

    # -- resolution ----------------------------------------------------

    def resolve(self, oracle: OracleAdapter) -> Outcome:
        if self.status != MarketStatus.CLOSED:
            raise InvalidTransition(
                f"resolve requires CLOSED, market is {self.status.value}"
            )
        outcome = oracle.outcome_for(self.market_id)
        if outcome == Outcome.UNDETERMINED:
            if (self._clock() >= self.criteria.resolves_by_epoch
                    and self.criteria.void_if_unresolvable):
                self._transition(MarketStatus.VOIDED)
                self.outcome = Outcome.UNDETERMINED
                return self.outcome
            raise SettlementError(
                f"{self.market_id}: oracle undetermined before deadline"
            )
        self.outcome = outcome
        self.resolved_at_epoch = self._clock()
        self._transition(MarketStatus.RESOLVED)
        return outcome

    # -- disputes --------------------------------------------------------

    def raise_dispute(self, account_id: str, reason: str) -> Dispute:
        if self.status != MarketStatus.RESOLVED:
            raise InvalidTransition("disputes only while RESOLVED")
        if self._dispute_window_elapsed():
            raise SettlementError("dispute window has elapsed")
        dispute = Dispute(account_id=account_id, reason=reason,
                          raised_at_epoch=self._clock())
        self.disputes.append(dispute)
        return dispute

    def accept_dispute(self, dispute: Dispute) -> None:
        """Accepted dispute reverts the market to CLOSED for re-resolution."""
        dispute.accepted = True
        self.outcome = None
        self.resolved_at_epoch = None
        self._transition(MarketStatus.CLOSED)

    def _dispute_window_elapsed(self) -> bool:
        assert self.resolved_at_epoch is not None
        return (self._clock() - self.resolved_at_epoch
                >= self.dispute_window_seconds)

    # -- settlement ------------------------------------------------------

    def settle(self, positions: Dict[str, int]) -> SettlementReport:
        """Pay 100 cents per winning contract.

        ``positions`` maps account -> signed YES contracts (positive =
        long YES, negative = long NO), as tracked by the order book.
        """
        if self.status != MarketStatus.RESOLVED:
            raise SettlementError(
                f"settle requires RESOLVED, market is {self.status.value}"
            )
        if not self._dispute_window_elapsed():
            raise SettlementError("dispute window still open")
        if any(d.accepted is None for d in self.disputes):
            raise SettlementError("unreviewed dispute pending")
        assert self.outcome in (Outcome.YES, Outcome.NO)

        payouts: Dict[str, int] = {}
        for account, contracts in positions.items():
            if self.outcome == Outcome.YES and contracts > 0:
                payouts[account] = contracts * CONTRACT_PAYOUT_CENTS
            elif self.outcome == Outcome.NO and contracts < 0:
                payouts[account] = -contracts * CONTRACT_PAYOUT_CENTS
        self._transition(MarketStatus.SETTLED)
        return SettlementReport(
            market_id=self.market_id,
            outcome=self.outcome,
            payouts_cents=payouts,
            total_paid_cents=sum(payouts.values()),
        )

    def refund_void(self, cost_basis_cents: Dict[str, int]) -> SettlementReport:
        """Refund each account's cost basis after a void.

        ``cost_basis_cents`` maps account -> net cents paid in (collateral
        posted for open positions). The ledger owns this number; here it
        is an input so the refund math stays auditable.
        """
        if self.status != MarketStatus.VOIDED:
            raise SettlementError("refund_void requires VOIDED market")
        return SettlementReport(
            market_id=self.market_id,
            outcome=Outcome.UNDETERMINED,
            payouts_cents=dict(cost_basis_cents),
            total_paid_cents=sum(cost_basis_cents.values()),
            voided=True,
        )
