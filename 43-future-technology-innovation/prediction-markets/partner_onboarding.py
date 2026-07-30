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
Formal supplier-onboarding state machine, gating any pattern before go-live.

Chapter 43c reference implementation, mirroring the book's chapter 47
supplier-certification logic applied to a prediction-market hub partner.
No pattern in this chapter -- embedded hub, licensed-market feed, whatever
comes next -- goes live against real player money until its supplier has
walked this machine end to end and produced the evidence each phase demands.

The phases are deliberately ordered from paperwork to production:

PRE_CONTRACT   the supplier is who they say they are (licence, corporate
               chain, and a first pass of ``jurisdiction_gate`` against
               their claimed footprint)
SANDBOX        the integration actually works against sandboxed traffic --
               session handoff, wallet idempotency and category filtering
               each get a dedicated test artifact
CERTIFICATION  the integration is safe at scale -- reconciliation,
               responsible-gambling controls, a pentest, an incident runbook
GO_LIVE        terminal; the partner is live. Nothing to advance to.
SUSPENDED      an off-ramp from *any* phase for cause; reactivation returns
               the partner to the exact phase it left, evidence intact.

Evidence is submitted one artifact at a time and checked against the
current phase's requirements; a phase cannot be left with evidence still
outstanding. Every transition and every evidence submission is appended to
an in-memory audit trail (``history``), because "who approved this partner
and when" is the first question compliance asks after an incident.

The clock is injected as a callable so tests are deterministic.
"""

from __future__ import annotations

from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple


class OnboardingPhase(str, Enum):
    PRE_CONTRACT = "PRE_CONTRACT"
    SANDBOX = "SANDBOX"
    CERTIFICATION = "CERTIFICATION"
    GO_LIVE = "GO_LIVE"
    SUSPENDED = "SUSPENDED"


# Evidence required to *leave* the given phase. GO_LIVE and SUSPENDED have
# no entry: GO_LIVE is terminal, SUSPENDED is not a normal waypoint.
REQUIRED_EVIDENCE: Dict[OnboardingPhase, Tuple[str, ...]] = {
    OnboardingPhase.PRE_CONTRACT: (
        "licence_copy", "corporate_chain_dd", "jurisdiction_matrix_review",
    ),
    OnboardingPhase.SANDBOX: (
        "session_handoff_test", "wallet_idempotency_test", "category_filter_test",
    ),
    OnboardingPhase.CERTIFICATION: (
        "settlement_reconciliation_report", "rg_controls_review",
        "pentest_report", "incident_runbook",
    ),
}

_PHASE_ORDER = (
    OnboardingPhase.PRE_CONTRACT,
    OnboardingPhase.SANDBOX,
    OnboardingPhase.CERTIFICATION,
    OnboardingPhase.GO_LIVE,
)


class OnboardingError(Exception):
    """Base class for onboarding state-machine failures."""


class InvalidEvidence(OnboardingError):
    """Evidence key not required for the current phase, or already submitted."""


class InvalidPhaseTransition(OnboardingError):
    """advance()/suspend()/reactivate() called from a phase that forbids it."""


class IncompleteEvidence(OnboardingError):
    """advance() called while required evidence for the phase is still missing."""

    def __init__(self, phase: OnboardingPhase, missing: Tuple[str, ...]):
        self.phase = phase
        self.missing = missing
        super().__init__(
            f"cannot leave {phase.value}: missing evidence "
            f"({', '.join(missing)})"
        )


class PartnerOnboarding:
    """Tracks one supplier partner's progress through certification."""

    def __init__(self, partner_id: str, clock: Callable[[], float]):
        self.partner_id = partner_id
        self._clock = clock
        self.phase = OnboardingPhase.PRE_CONTRACT
        self._evidence: Dict[OnboardingPhase, Dict[str, str]] = {
            phase: {} for phase in REQUIRED_EVIDENCE
        }
        self._suspended_from: Optional[OnboardingPhase] = None
        self.history: List[Tuple[float, str]] = []
        self._log(f"onboarding started, phase={self.phase.value}")

    def _log(self, event: str) -> None:
        self.history.append((self._clock(), event))

    def submit_evidence(self, key: str, reference: str) -> None:
        required = REQUIRED_EVIDENCE.get(self.phase, ())
        if key not in required:
            raise InvalidEvidence(
                f"{key!r} is not required evidence for phase {self.phase.value}"
            )
        submitted = self._evidence[self.phase]
        if key in submitted:
            raise InvalidEvidence(
                f"{key!r} was already submitted for phase {self.phase.value}"
            )
        submitted[key] = reference
        self._log(f"evidence submitted for {self.phase.value}: {key}={reference}")

    def missing_evidence(self) -> tuple:
        required = REQUIRED_EVIDENCE.get(self.phase, ())
        submitted = self._evidence.get(self.phase, {})
        return tuple(key for key in required if key not in submitted)

    def advance(self) -> OnboardingPhase:
        if self.phase == OnboardingPhase.SUSPENDED:
            raise InvalidPhaseTransition(
                "cannot advance a suspended partner; reactivate() first"
            )
        if self.phase == OnboardingPhase.GO_LIVE:
            raise InvalidPhaseTransition("GO_LIVE is terminal; nothing to advance to")

        missing = self.missing_evidence()
        if missing:
            raise IncompleteEvidence(self.phase, missing)

        next_phase = _PHASE_ORDER[_PHASE_ORDER.index(self.phase) + 1]
        self.phase = next_phase
        self._log(f"advanced to {next_phase.value}")
        return self.phase

    def suspend(self, reason: str) -> None:
        if self.phase == OnboardingPhase.SUSPENDED:
            raise InvalidPhaseTransition("partner is already suspended")
        self._suspended_from = self.phase
        self.phase = OnboardingPhase.SUSPENDED
        self._log(f"suspended from {self._suspended_from.value}: {reason}")

    def reactivate(self) -> OnboardingPhase:
        if self.phase != OnboardingPhase.SUSPENDED:
            raise InvalidPhaseTransition("partner is not suspended")
        assert self._suspended_from is not None
        self.phase = self._suspended_from
        self._suspended_from = None
        self._log(f"reactivated to {self.phase.value}")
        return self.phase
