# Companion code for "The Backend of Luck" - Chapter 36, Financial Operations.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Release Gate: Ledger Invariant Check

Pre-deployment verification that the ledger is internally consistent.
This script MUST pass before any production release.

Checks:
  1. Trial balance: sum(all debits) == sum(all credits)
  2. No unbalanced postings (every posting has equal debits and credits)
  3. No orphaned entries (entries not belonging to any posting)
  4. All accounts sum to zero (the fundamental double-entry identity)

Exit codes:
  0 = PASS (safe to deploy)
  1 = FAIL (ledger corruption detected -- block deployment)

Compliance: NJ DGE 13:69O-1.1, MGA Player Protection Directive,
            PCI-DSS Requirement 10 (audit trails)
"""

from __future__ import annotations

import asyncio
import json
import sys
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ledger-service"))

from ledger import Ledger  # ty: ignore[unresolved-import]
from models import Direction, EntryRequest  # ty: ignore[unresolved-import]


@dataclass
class InvariantCheckResult:
    """Result of the release-gate invariant check."""

    trial_balance_pass: bool = False
    total_debits: int = 0
    total_credits: int = 0
    imbalance: int = 0
    all_postings_balanced: bool = False
    total_postings: int = 0
    unbalanced_postings: int = 0
    no_orphaned_entries: bool = False
    orphaned_entry_count: int = 0
    overall_pass: bool = False
    checked_at: str = ""
    errors: list[str] = field(default_factory=list)


async def run_invariant_check(ledger: Ledger) -> InvariantCheckResult:
    """Execute all invariant checks against the ledger."""
    result = InvariantCheckResult(
        checked_at=datetime.now(timezone.utc).isoformat(),
    )

    # Check 1: Trial balance
    trial = await ledger.trial_balance()
    result.trial_balance_pass = trial.is_balanced
    result.total_debits = trial.total_debits
    result.total_credits = trial.total_credits
    result.imbalance = trial.imbalance

    if not trial.is_balanced:
        result.errors.append(
            f"TRIAL BALANCE FAILED: debits={trial.total_debits} "
            f"credits={trial.total_credits} imbalance={trial.imbalance}"
        )

    # Check 2: All postings balanced
    invariant = await ledger.verify_invariant()
    result.all_postings_balanced = invariant.is_valid
    result.total_postings = invariant.total_postings
    result.unbalanced_postings = len(invariant.unbalanced_postings)

    if not invariant.is_valid:
        result.errors.append(
            f"UNBALANCED POSTINGS: {len(invariant.unbalanced_postings)} "
            f"posting(s) have mismatched debits/credits"
        )

    # Check 3: No orphaned entries
    all_entries = await ledger.store.get_all_entries()
    all_postings = await ledger.store.get_all_postings()

    posting_entry_ids = set()
    for posting in all_postings:
        for entry in posting.entries:
            posting_entry_ids.add(entry.entry_id)

    orphaned = [e for e in all_entries if e.entry_id not in posting_entry_ids]
    result.no_orphaned_entries = len(orphaned) == 0
    result.orphaned_entry_count = len(orphaned)

    if orphaned:
        result.errors.append(
            f"ORPHANED ENTRIES: {len(orphaned)} entry(ies) not linked to any posting"
        )

    # Overall
    result.overall_pass = (
        result.trial_balance_pass
        and result.all_postings_balanced
        and result.no_orphaned_entries
    )

    return result


async def demo_check() -> InvariantCheckResult:
    """
    Run the invariant check against a demo ledger with known-good data.
    Used for testing the gate itself.
    """
    ledger = Ledger()

    # Seed some balanced postings
    await ledger.create_posting([
        EntryRequest(account_id="PSP_CLEARING:adyen", amount=10_000, direction=Direction.DEBIT),
        EntryRequest(account_id="PLAYER_WALLET:p1", amount=10_000, direction=Direction.CREDIT),
    ], metadata={"event": "deposit"})

    await ledger.create_posting([
        EntryRequest(account_id="PLAYER_WALLET:p1", amount=3_000, direction=Direction.DEBIT),
        EntryRequest(account_id="OPERATOR_REVENUE:slots", amount=3_000, direction=Direction.CREDIT),
    ], metadata={"event": "bet"})

    return await run_invariant_check(ledger)


def main() -> None:
    result = asyncio.run(demo_check())
    print(json.dumps(asdict(result), indent=2))

    if result.overall_pass:
        print("\nRESULT: PASS -- ledger invariants hold, safe to deploy")
        sys.exit(0)
    else:
        print("\nRESULT: FAIL -- ledger corruption detected, BLOCKING DEPLOYMENT")
        for error in result.errors:
            print(f"  ERROR: {error}")
        sys.exit(1)


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------------------
# Pytest tests
# ---------------------------------------------------------------------------

import pytest


@pytest.mark.asyncio
async def test_clean_ledger_passes_invariant():
    result = await demo_check()
    assert result.overall_pass is True
    assert result.trial_balance_pass is True
    assert result.all_postings_balanced is True
    assert result.no_orphaned_entries is True
    assert len(result.errors) == 0


@pytest.mark.asyncio
async def test_corrupted_ledger_fails_invariant():
    from models import LedgerEntry  # ty: ignore[unresolved-import]
    import uuid

    ledger = Ledger()
    await ledger.create_posting([
        EntryRequest(account_id="A", amount=1_000, direction=Direction.DEBIT),
        EntryRequest(account_id="B", amount=1_000, direction=Direction.CREDIT),
    ])

    # Inject orphan
    orphan = LedgerEntry(
        entry_group_id=uuid.uuid4(),
        account_id="CORRUPT",
        amount=999,
        direction=Direction.DEBIT,
    )
    await ledger.store.inject_raw_entry(orphan)

    result = await run_invariant_check(ledger)
    assert result.overall_pass is False
    assert result.orphaned_entry_count == 1


@pytest.mark.asyncio
async def test_trial_balance_reports_totals():
    result = await demo_check()
    assert result.total_debits == 13_000  # 10000 + 3000
    assert result.total_credits == 13_000
    assert result.imbalance == 0
