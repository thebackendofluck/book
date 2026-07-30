#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 10, Complete Platform Architecture.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Release Gate: Game Round State Integrity Checker
==================================================

Validates that all game rounds follow the proper lifecycle:

    OPEN -> BET -> RESULT -> CLOSE

Detects anomalies:
  - Rounds stuck in OPEN or BET without a result (orphaned rounds)
  - Rounds with BET but no matching CLOSE (settlement leak)
  - Rounds with RESULT but no prior BET (phantom results)
  - Duplicate state transitions for the same round
  - Out-of-order transitions (e.g., RESULT before BET)

This script processes a fixture set of game round events and reports
violations. In production, it would query the game round event store
(Kafka topic or database table).

Usage:
    python check_round_state_integrity.py           # Run checks
    python check_round_state_integrity.py --json    # JSON report

Exit codes:
    0 — All rounds have valid lifecycles
    1 — Integrity violations detected
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("round-state-integrity")


# ---------------------------------------------------------------------------
# Round lifecycle model
# ---------------------------------------------------------------------------


class RoundState(str, Enum):
    OPEN = "OPEN"
    BET = "BET"
    RESULT = "RESULT"
    CLOSE = "CLOSE"
    CANCELLED = "CANCELLED"


# Valid transitions
VALID_TRANSITIONS: dict[RoundState, set[RoundState]] = {
    RoundState.OPEN: {RoundState.BET, RoundState.CANCELLED},
    RoundState.BET: {RoundState.RESULT, RoundState.CANCELLED},
    RoundState.RESULT: {RoundState.CLOSE},
    RoundState.CLOSE: set(),       # terminal state
    RoundState.CANCELLED: set(),   # terminal state
}


@dataclass
class RoundEvent:
    """A single state transition event for a game round."""
    round_id: str
    supplier_id: str
    player_id: str
    state: RoundState
    amount: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class RoundTracker:
    """Tracks the lifecycle of a single game round."""
    round_id: str
    supplier_id: str
    player_id: str
    current_state: Optional[RoundState] = None
    transitions: list[RoundState] = field(default_factory=list)
    bet_amount: float = 0.0
    result_amount: float = 0.0
    violations: list[str] = field(default_factory=list)

    def apply_event(self, event: RoundEvent) -> None:
        """Apply a state transition and check for violations."""
        new_state = event.state

        if self.current_state is None:
            # First event must be OPEN
            if new_state != RoundState.OPEN:
                self.violations.append(
                    f"First event must be OPEN, got {new_state.value}"
                )
        else:
            # Check valid transition
            allowed = VALID_TRANSITIONS.get(self.current_state, set())
            if new_state not in allowed:
                self.violations.append(
                    f"Invalid transition: {self.current_state.value} -> {new_state.value}"
                )

            # Check for duplicate state
            if new_state in self.transitions:
                self.violations.append(
                    f"Duplicate transition to {new_state.value}"
                )

        self.current_state = new_state
        self.transitions.append(new_state)

        if new_state == RoundState.BET:
            self.bet_amount = event.amount
        elif new_state == RoundState.RESULT:
            self.result_amount = event.amount

    @property
    def is_complete(self) -> bool:
        return self.current_state in (RoundState.CLOSE, RoundState.CANCELLED)

    @property
    def is_orphaned(self) -> bool:
        return not self.is_complete

    @property
    def has_violations(self) -> bool:
        return len(self.violations) > 0


# ---------------------------------------------------------------------------
# Fixture data — simulates game round events
# ---------------------------------------------------------------------------


FIXTURE_EVENTS = [
    # Round 1: Complete lifecycle (valid)
    RoundEvent("R-001", "evolution", "PLR-1", RoundState.OPEN),
    RoundEvent("R-001", "evolution", "PLR-1", RoundState.BET, amount=25.0),
    RoundEvent("R-001", "evolution", "PLR-1", RoundState.RESULT, amount=50.0),
    RoundEvent("R-001", "evolution", "PLR-1", RoundState.CLOSE),

    # Round 2: Complete lifecycle (valid)
    RoundEvent("R-002", "pragmatic", "PLR-2", RoundState.OPEN),
    RoundEvent("R-002", "pragmatic", "PLR-2", RoundState.BET, amount=10.0),
    RoundEvent("R-002", "pragmatic", "PLR-2", RoundState.RESULT, amount=0.0),
    RoundEvent("R-002", "pragmatic", "PLR-2", RoundState.CLOSE),

    # Round 3: Cancelled (valid)
    RoundEvent("R-003", "evolution", "PLR-1", RoundState.OPEN),
    RoundEvent("R-003", "evolution", "PLR-1", RoundState.CANCELLED),

    # Round 4: Orphaned — stuck at BET (violation)
    RoundEvent("R-004", "netent", "PLR-3", RoundState.OPEN),
    RoundEvent("R-004", "netent", "PLR-3", RoundState.BET, amount=50.0),

    # Round 5: Out of order — RESULT before BET (violation)
    RoundEvent("R-005", "evolution", "PLR-4", RoundState.OPEN),
    RoundEvent("R-005", "evolution", "PLR-4", RoundState.RESULT, amount=100.0),

    # Round 6: Complete lifecycle (valid)
    RoundEvent("R-006", "pragmatic", "PLR-5", RoundState.OPEN),
    RoundEvent("R-006", "pragmatic", "PLR-5", RoundState.BET, amount=5.0),
    RoundEvent("R-006", "pragmatic", "PLR-5", RoundState.RESULT, amount=15.0),
    RoundEvent("R-006", "pragmatic", "PLR-5", RoundState.CLOSE),

    # Round 7: Missing OPEN — starts with BET (violation)
    RoundEvent("R-007", "netent", "PLR-6", RoundState.BET, amount=20.0),
    RoundEvent("R-007", "netent", "PLR-6", RoundState.RESULT, amount=40.0),
    RoundEvent("R-007", "netent", "PLR-6", RoundState.CLOSE),
]


# ---------------------------------------------------------------------------
# Check results
# ---------------------------------------------------------------------------


@dataclass
class CheckResult:
    name: str
    passed: bool
    message: str = ""


@dataclass
class IntegrityReport:
    total_rounds: int = 0
    complete_rounds: int = 0
    orphaned_rounds: int = 0
    violated_rounds: int = 0
    checks: list[CheckResult] = field(default_factory=list)
    round_details: list[dict] = field(default_factory=list)

    def add_check(self, result: CheckResult) -> None:
        self.checks.append(result)

    @property
    def all_passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    @property
    def failed_count(self) -> int:
        return sum(1 for c in self.checks if not c.passed)

    def to_dict(self) -> dict:
        return {
            "total_rounds": self.total_rounds,
            "complete_rounds": self.complete_rounds,
            "orphaned_rounds": self.orphaned_rounds,
            "violated_rounds": self.violated_rounds,
            "all_passed": self.all_passed,
            "checks": [
                {"name": c.name, "passed": c.passed, "message": c.message}
                for c in self.checks
            ],
            "round_details": self.round_details,
        }


# ---------------------------------------------------------------------------
# Integrity checker
# ---------------------------------------------------------------------------


def check_round_integrity(events: list[RoundEvent]) -> IntegrityReport:
    """Process all events and generate an integrity report."""
    report = IntegrityReport()
    trackers: dict[str, RoundTracker] = {}

    # Build trackers from events
    for event in events:
        if event.round_id not in trackers:
            trackers[event.round_id] = RoundTracker(
                round_id=event.round_id,
                supplier_id=event.supplier_id,
                player_id=event.player_id,
            )
        trackers[event.round_id].apply_event(event)

    report.total_rounds = len(trackers)

    # Analyze each round
    for rid, tracker in sorted(trackers.items()):
        detail = {
            "round_id": rid,
            "supplier_id": tracker.supplier_id,
            "player_id": tracker.player_id,
            "current_state": tracker.current_state.value if tracker.current_state else "NONE",
            "transitions": [t.value for t in tracker.transitions],
            "complete": tracker.is_complete,
            "violations": tracker.violations,
        }
        report.round_details.append(detail)

        if tracker.is_complete:
            report.complete_rounds += 1
        else:
            report.orphaned_rounds += 1

        if tracker.has_violations:
            report.violated_rounds += 1

    # Generate checks

    # Check 1: No orphaned rounds
    report.add_check(CheckResult(
        name="no_orphaned_rounds",
        passed=report.orphaned_rounds == 0,
        message=(
            f"{report.orphaned_rounds} orphaned rounds detected"
            if report.orphaned_rounds > 0
            else "All rounds reached terminal state"
        ),
    ))

    # Check 2: No transition violations
    report.add_check(CheckResult(
        name="no_transition_violations",
        passed=report.violated_rounds == 0,
        message=(
            f"{report.violated_rounds} rounds with violations"
            if report.violated_rounds > 0
            else "All transitions follow valid lifecycle"
        ),
    ))

    # Check 3: All complete rounds have OPEN -> BET -> RESULT -> CLOSE
    full_lifecycle = all(
        tracker.transitions == [
            RoundState.OPEN, RoundState.BET, RoundState.RESULT, RoundState.CLOSE,
        ]
        for tracker in trackers.values()
        if tracker.is_complete and tracker.current_state == RoundState.CLOSE
    )
    report.add_check(CheckResult(
        name="complete_rounds_full_lifecycle",
        passed=full_lifecycle,
        message=(
            "All completed rounds have full OPEN->BET->RESULT->CLOSE lifecycle"
            if full_lifecycle
            else "Some completed rounds have unexpected lifecycle"
        ),
    ))

    # Check 4: No duplicate transitions
    has_dupes = any(
        len(t.transitions) != len(set(t.transitions))
        for t in trackers.values()
    )
    report.add_check(CheckResult(
        name="no_duplicate_transitions",
        passed=not has_dupes,
        message=(
            "Duplicate transitions detected"
            if has_dupes
            else "No duplicate transitions"
        ),
    ))

    # Check 5: Bet amounts are positive for BET states
    bad_bets = [
        t.round_id for t in trackers.values()
        if RoundState.BET in t.transitions and t.bet_amount <= 0
    ]
    report.add_check(CheckResult(
        name="positive_bet_amounts",
        passed=len(bad_bets) == 0,
        message=(
            f"Rounds with zero/negative bets: {bad_bets}"
            if bad_bets
            else "All bet amounts are positive"
        ),
    ))

    return report


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_check(as_json: bool = False) -> bool:
    logger.info("=== Game Round State Integrity Check ===")
    t0 = time.monotonic()

    report = check_round_integrity(FIXTURE_EVENTS)

    duration_ms = (time.monotonic() - t0) * 1000

    if as_json:
        output = report.to_dict()
        output["duration_ms"] = round(duration_ms, 2)
        print(json.dumps(output, indent=2))
    else:
        print(f"\n{'='*60}")
        print(f"Game Round State Integrity Report")
        print(f"{'='*60}")
        print(f"Total rounds:    {report.total_rounds}")
        print(f"Complete:        {report.complete_rounds}")
        print(f"Orphaned:        {report.orphaned_rounds}")
        print(f"With violations: {report.violated_rounds}")
        print(f"Duration:        {duration_ms:.1f}ms")
        print(f"{'='*60}")

        for check in report.checks:
            icon = "PASS" if check.passed else "FAIL"
            print(f"  [{icon}] {check.name}: {check.message}")

        if report.violated_rounds > 0 or report.orphaned_rounds > 0:
            print(f"\n{'='*60}")
            print("Problematic rounds:")
            for detail in report.round_details:
                if detail["violations"] or not detail["complete"]:
                    print(f"  Round {detail['round_id']}:")
                    print(f"    Supplier:    {detail['supplier_id']}")
                    print(f"    State:       {detail['current_state']}")
                    print(f"    Transitions: {' -> '.join(detail['transitions'])}")
                    if detail["violations"]:
                        for v in detail["violations"]:
                            print(f"    Violation:   {v}")
                    if not detail["complete"]:
                        print(f"    Status:      ORPHANED")

        print(f"{'='*60}")
        print(f"Result: {'ALL PASSED' if report.all_passed else 'FAILED'}")

    return report.all_passed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Game Round State Integrity Checker",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    success = run_check(as_json=args.json)
    sys.exit(0 if success else 1)
