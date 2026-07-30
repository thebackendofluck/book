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

"""Tests for market lifecycle, oracle and settlement (chapter 43c)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from market_lifecycle import (  # noqa: E402
    AttestationOracle,
    InvalidTransition,
    Market,
    MarketCategory,
    MarketStatus,
    Outcome,
    ResolutionCriteria,
    SettlementError,
)


class FakeClock:
    def __init__(self, start: float = 1_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_market(clock, window=3600.0, deadline=1_000_000.0):
    return Market(
        market_id="m1",
        category=MarketCategory.FOOTBALL,
        criteria=ResolutionCriteria(
            question="Will the home side win the final?",
            source="FIFA official result",
            resolves_by_epoch=deadline,
        ),
        clock=clock,
        dispute_window_seconds=window,
    )


class TestStateMachine:
    def test_happy_path_history(self):
        clock = FakeClock()
        m = make_market(clock)
        oracle = AttestationOracle(quorum=1)
        oracle.attest("m1", "ops-1", Outcome.YES)
        m.open()
        m.close()
        m.resolve(oracle)
        assert m.history == [MarketStatus.DRAFT, MarketStatus.OPEN,
                             MarketStatus.CLOSED, MarketStatus.RESOLVED]

    def test_cannot_open_twice(self):
        m = make_market(FakeClock())
        m.open()
        with pytest.raises(InvalidTransition):
            m.open()

    def test_suspend_and_reopen(self):
        m = make_market(FakeClock())
        m.open()
        m.suspend()
        m.reopen()
        assert m.status == MarketStatus.OPEN

    def test_cannot_resolve_open_market(self):
        m = make_market(FakeClock())
        m.open()
        with pytest.raises(InvalidTransition):
            m.resolve(AttestationOracle(quorum=1))

    def test_settled_is_terminal(self):
        clock = FakeClock()
        m = make_market(clock)
        oracle = AttestationOracle(quorum=1)
        oracle.attest("m1", "ops-1", Outcome.NO)
        m.open(); m.close(); m.resolve(oracle)
        clock.advance(4000)
        m.settle({})
        with pytest.raises(InvalidTransition):
            m.close()


class TestOracle:
    def test_quorum_not_reached(self):
        oracle = AttestationOracle(quorum=2)
        oracle.attest("m1", "ops-1", Outcome.YES)
        assert oracle.outcome_for("m1") == Outcome.UNDETERMINED

    def test_quorum_reached(self):
        oracle = AttestationOracle(quorum=2)
        oracle.attest("m1", "ops-1", Outcome.YES)
        oracle.attest("m1", "ops-2", Outcome.YES)
        assert oracle.outcome_for("m1") == Outcome.YES

    def test_conflicting_votes_stay_undetermined(self):
        oracle = AttestationOracle(quorum=2)
        oracle.attest("m1", "ops-1", Outcome.YES)
        oracle.attest("m1", "ops-2", Outcome.NO)
        assert oracle.outcome_for("m1") == Outcome.UNDETERMINED

    def test_attester_cannot_post_undetermined(self):
        oracle = AttestationOracle(quorum=1)
        with pytest.raises(ValueError):
            oracle.attest("m1", "ops-1", Outcome.UNDETERMINED)

    def test_undetermined_before_deadline_raises(self):
        clock = FakeClock(start=10.0)
        m = make_market(clock, deadline=99_999.0)
        m.open(); m.close()
        with pytest.raises(SettlementError):
            m.resolve(AttestationOracle(quorum=2))

    def test_undetermined_past_deadline_voids(self):
        clock = FakeClock(start=10.0)
        m = make_market(clock, deadline=5.0)   # already past
        m.open(); m.close()
        outcome = m.resolve(AttestationOracle(quorum=2))
        assert outcome == Outcome.UNDETERMINED
        assert m.status == MarketStatus.VOIDED


class TestDisputes:
    def _resolved_market(self, clock):
        m = make_market(clock)
        oracle = AttestationOracle(quorum=1)
        oracle.attest("m1", "ops-1", Outcome.YES)
        m.open(); m.close(); m.resolve(oracle)
        return m

    def test_settle_blocked_inside_window(self):
        clock = FakeClock()
        m = self._resolved_market(clock)
        with pytest.raises(SettlementError):
            m.settle({"a1": 5})

    def test_settle_blocked_by_pending_dispute(self):
        clock = FakeClock()
        m = self._resolved_market(clock)
        m.raise_dispute("a9", "source shows opposite result")
        clock.advance(4000)
        with pytest.raises(SettlementError):
            m.settle({"a1": 5})

    def test_accepted_dispute_reverts_to_closed(self):
        clock = FakeClock()
        m = self._resolved_market(clock)
        d = m.raise_dispute("a9", "wrong feed used")
        m.accept_dispute(d)
        assert m.status == MarketStatus.CLOSED
        assert m.outcome is None

    def test_dispute_after_window_rejected(self):
        clock = FakeClock()
        m = self._resolved_market(clock)
        clock.advance(4000)
        with pytest.raises(SettlementError):
            m.raise_dispute("a9", "too late")


class TestSettlement:
    def test_yes_outcome_pays_longs(self):
        clock = FakeClock()
        m = make_market(clock)
        oracle = AttestationOracle(quorum=1)
        oracle.attest("m1", "ops-1", Outcome.YES)
        m.open(); m.close(); m.resolve(oracle)
        clock.advance(4000)
        report = m.settle({"long": 7, "short": -7})
        assert report.payouts_cents == {"long": 700}
        assert report.total_paid_cents == 700

    def test_no_outcome_pays_shorts(self):
        clock = FakeClock()
        m = make_market(clock)
        oracle = AttestationOracle(quorum=1)
        oracle.attest("m1", "ops-1", Outcome.NO)
        m.open(); m.close(); m.resolve(oracle)
        clock.advance(4000)
        report = m.settle({"long": 7, "short": -7})
        assert report.payouts_cents == {"short": 700}

    def test_void_refunds_cost_basis(self):
        clock = FakeClock(start=10.0)
        m = make_market(clock, deadline=5.0)
        m.open(); m.close()
        m.resolve(AttestationOracle(quorum=2))    # voids
        report = m.refund_void({"a1": 320, "a2": 280})
        assert report.voided is True
        assert report.total_paid_cents == 600
