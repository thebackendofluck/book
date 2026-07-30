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

"""Tests for the supplier-onboarding state machine (chapter 43c)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from partner_onboarding import (  # noqa: E402
    IncompleteEvidence,
    InvalidEvidence,
    InvalidPhaseTransition,
    OnboardingPhase,
    PartnerOnboarding,
    REQUIRED_EVIDENCE,
)


class FakeClock:
    def __init__(self, start: float = 1_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def submit_all(onboarding: PartnerOnboarding, phase: OnboardingPhase) -> None:
    for key in REQUIRED_EVIDENCE[phase]:
        onboarding.submit_evidence(key, reference=f"ref-{key}")


class TestHappyPath:
    def test_starts_in_pre_contract(self):
        onboarding = PartnerOnboarding("matchbook", FakeClock())
        assert onboarding.phase == OnboardingPhase.PRE_CONTRACT

    def test_full_walk_to_go_live(self):
        clock = FakeClock()
        onboarding = PartnerOnboarding("matchbook", clock)

        submit_all(onboarding, OnboardingPhase.PRE_CONTRACT)
        assert onboarding.advance() == OnboardingPhase.SANDBOX

        submit_all(onboarding, OnboardingPhase.SANDBOX)
        assert onboarding.advance() == OnboardingPhase.CERTIFICATION

        submit_all(onboarding, OnboardingPhase.CERTIFICATION)
        assert onboarding.advance() == OnboardingPhase.GO_LIVE

    def test_history_records_events(self):
        clock = FakeClock()
        onboarding = PartnerOnboarding("matchbook", clock)
        clock.advance(10)
        onboarding.submit_evidence("licence_copy", "doc-1")
        assert len(onboarding.history) == 2  # start + submission
        timestamps = [t for t, _ in onboarding.history]
        assert timestamps == sorted(timestamps)


class TestAdvanceBlocked:
    def test_advance_lists_missing_keys(self):
        onboarding = PartnerOnboarding("matchbook", FakeClock())
        onboarding.submit_evidence("licence_copy", "doc-1")
        with pytest.raises(IncompleteEvidence) as exc_info:
            onboarding.advance()
        assert exc_info.value.missing == (
            "corporate_chain_dd", "jurisdiction_matrix_review",
        )
        assert exc_info.value.phase == OnboardingPhase.PRE_CONTRACT

    def test_missing_evidence_reports_full_gap_before_any_submission(self):
        onboarding = PartnerOnboarding("matchbook", FakeClock())
        assert onboarding.missing_evidence() == REQUIRED_EVIDENCE[
            OnboardingPhase.PRE_CONTRACT
        ]


class TestEvidenceValidation:
    def test_evidence_for_wrong_phase_rejected(self):
        onboarding = PartnerOnboarding("matchbook", FakeClock())
        with pytest.raises(InvalidEvidence):
            onboarding.submit_evidence("pentest_report", "doc-1")

    def test_duplicate_evidence_rejected(self):
        onboarding = PartnerOnboarding("matchbook", FakeClock())
        onboarding.submit_evidence("licence_copy", "doc-1")
        with pytest.raises(InvalidEvidence):
            onboarding.submit_evidence("licence_copy", "doc-2")

    def test_evidence_while_suspended_rejected(self):
        onboarding = PartnerOnboarding("matchbook", FakeClock())
        onboarding.suspend("incident under review")
        with pytest.raises(InvalidEvidence):
            onboarding.submit_evidence("licence_copy", "doc-1")


class TestSuspendReactivate:
    def test_suspend_and_reactivate_round_trip_preserves_phase(self):
        onboarding = PartnerOnboarding("matchbook", FakeClock())
        submit_all(onboarding, OnboardingPhase.PRE_CONTRACT)
        onboarding.advance()
        assert onboarding.phase == OnboardingPhase.SANDBOX

        onboarding.suspend("failed sandbox smoke test")
        assert onboarding.phase == OnboardingPhase.SUSPENDED

        assert onboarding.reactivate() == OnboardingPhase.SANDBOX
        assert onboarding.phase == OnboardingPhase.SANDBOX

    def test_suspend_preserves_already_submitted_evidence(self):
        onboarding = PartnerOnboarding("matchbook", FakeClock())
        onboarding.submit_evidence("licence_copy", "doc-1")
        onboarding.suspend("pause")
        onboarding.reactivate()
        assert onboarding.missing_evidence() == (
            "corporate_chain_dd", "jurisdiction_matrix_review",
        )

    def test_suspend_from_any_phase(self):
        clock = FakeClock()
        onboarding = PartnerOnboarding("matchbook", clock)
        submit_all(onboarding, OnboardingPhase.PRE_CONTRACT)
        onboarding.advance()
        submit_all(onboarding, OnboardingPhase.SANDBOX)
        onboarding.advance()
        assert onboarding.phase == OnboardingPhase.CERTIFICATION
        onboarding.suspend("pentest flagged critical finding")
        assert onboarding.reactivate() == OnboardingPhase.CERTIFICATION

    def test_double_suspend_rejected(self):
        onboarding = PartnerOnboarding("matchbook", FakeClock())
        onboarding.suspend("first")
        with pytest.raises(InvalidPhaseTransition):
            onboarding.suspend("second")

    def test_reactivate_without_suspend_rejected(self):
        onboarding = PartnerOnboarding("matchbook", FakeClock())
        with pytest.raises(InvalidPhaseTransition):
            onboarding.reactivate()

    def test_advance_while_suspended_rejected(self):
        onboarding = PartnerOnboarding("matchbook", FakeClock())
        onboarding.suspend("pause")
        with pytest.raises(InvalidPhaseTransition):
            onboarding.advance()


class TestGoLiveTerminal:
    def test_go_live_cannot_advance_further(self):
        onboarding = PartnerOnboarding("matchbook", FakeClock())
        submit_all(onboarding, OnboardingPhase.PRE_CONTRACT)
        onboarding.advance()
        submit_all(onboarding, OnboardingPhase.SANDBOX)
        onboarding.advance()
        submit_all(onboarding, OnboardingPhase.CERTIFICATION)
        onboarding.advance()
        assert onboarding.phase == OnboardingPhase.GO_LIVE

        with pytest.raises(InvalidPhaseTransition):
            onboarding.advance()

    def test_go_live_can_still_be_suspended(self):
        onboarding = PartnerOnboarding("matchbook", FakeClock())
        submit_all(onboarding, OnboardingPhase.PRE_CONTRACT)
        onboarding.advance()
        submit_all(onboarding, OnboardingPhase.SANDBOX)
        onboarding.advance()
        submit_all(onboarding, OnboardingPhase.CERTIFICATION)
        onboarding.advance()

        onboarding.suspend("compliance hold")
        assert onboarding.reactivate() == OnboardingPhase.GO_LIVE
