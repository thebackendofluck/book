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
Release Gate: Wallet-Ledger Divergence Check

Compares every player's wallet service balance against the ledger's
computed balance. Any divergence indicates a booking gap or ghost
posting that MUST be investigated before go-live.

Exit codes:
  0 = PASS (all wallets match ledger)
  1 = FAIL (divergence detected -- block deployment)

Compliance: MGA Player Protection Directive (player fund segregation),
            NJ DGE 13:69O-1.9 (patron account accuracy)
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
class DivergenceRecord:
    """One player's divergence detail."""

    player_id: str
    ledger_balance: int
    wallet_balance: int
    delta: int
    is_critical: bool  # True if delta > threshold


@dataclass
class DivergenceCheckResult:
    """Result of the wallet-ledger divergence check."""

    total_players_checked: int = 0
    total_matched: int = 0
    total_diverged: int = 0
    total_critical: int = 0
    max_divergence: int = 0
    sum_divergence: int = 0
    divergences: list[DivergenceRecord] = field(default_factory=list)
    overall_pass: bool = False
    checked_at: str = ""

    # Configurable threshold: divergence below this is tolerated
    # (e.g., rounding from currency conversion)
    tolerance_minor_units: int = 0


async def check_divergence(
    ledger: Ledger,
    wallet_balances: dict[str, int],
    tolerance: int = 0,
) -> DivergenceCheckResult:
    """
    Compare every player wallet balance against ledger.

    Args:
        ledger: The ledger instance (source of truth)
        wallet_balances: {player_id: balance_in_minor_units} from wallet service
        tolerance: Minor units of tolerance before flagging (0 = exact match required)
    """
    result = DivergenceCheckResult(
        checked_at=datetime.now(timezone.utc).isoformat(),
        tolerance_minor_units=tolerance,
    )

    for player_id, wallet_bal in wallet_balances.items():
        result.total_players_checked += 1

        account_id = f"PLAYER_WALLET:{player_id}"
        ledger_bal_obj = await ledger.get_account_balance(account_id)
        # Wallet perspective: credits increase, debits decrease
        ledger_bal = ledger_bal_obj.total_credits - ledger_bal_obj.total_debits

        delta = abs(ledger_bal - wallet_bal)

        if delta <= tolerance:
            result.total_matched += 1
        else:
            is_critical = delta > 10_000  # > 100 EUR is critical
            result.total_diverged += 1
            if is_critical:
                result.total_critical += 1

            result.sum_divergence += delta
            result.max_divergence = max(result.max_divergence, delta)

            result.divergences.append(DivergenceRecord(
                player_id=player_id,
                ledger_balance=ledger_bal,
                wallet_balance=wallet_bal,
                delta=delta,
                is_critical=is_critical,
            ))

    result.overall_pass = result.total_diverged == 0
    return result


async def demo_check() -> DivergenceCheckResult:
    """Run against demo data: 3 players, 1 divergence."""
    ledger = Ledger()

    # Player 1: deposit 10000
    await ledger.create_posting([
        EntryRequest(account_id="PSP_CLEARING:adyen", amount=10_000, direction=Direction.DEBIT),
        EntryRequest(account_id="PLAYER_WALLET:p1", amount=10_000, direction=Direction.CREDIT),
    ])

    # Player 2: deposit 20000, bet 5000
    await ledger.create_posting([
        EntryRequest(account_id="PSP_CLEARING:adyen", amount=20_000, direction=Direction.DEBIT),
        EntryRequest(account_id="PLAYER_WALLET:p2", amount=20_000, direction=Direction.CREDIT),
    ])
    await ledger.create_posting([
        EntryRequest(account_id="PLAYER_WALLET:p2", amount=5_000, direction=Direction.DEBIT),
        EntryRequest(account_id="OPERATOR_REVENUE:slots", amount=5_000, direction=Direction.CREDIT),
    ])

    # Player 3: deposit 5000
    await ledger.create_posting([
        EntryRequest(account_id="PSP_CLEARING:adyen", amount=5_000, direction=Direction.DEBIT),
        EntryRequest(account_id="PLAYER_WALLET:p3", amount=5_000, direction=Direction.CREDIT),
    ])

    # Wallet balances (p2 has a 500 cent divergence -- stale cache)
    wallet_balances = {
        "p1": 10_000,   # matches
        "p2": 14_500,   # off by 500 (ledger says 15000)
        "p3": 5_000,    # matches
    }

    return await check_divergence(ledger, wallet_balances)


def main() -> None:
    result = asyncio.run(demo_check())
    print(json.dumps(asdict(result), indent=2))

    if result.overall_pass:
        print(f"\nRESULT: PASS -- {result.total_players_checked} players checked, all match")
        sys.exit(0)
    else:
        print(f"\nRESULT: FAIL -- {result.total_diverged} divergence(s) detected")
        for d in result.divergences:
            marker = "CRITICAL" if d.is_critical else "WARNING"
            print(f"  [{marker}] player={d.player_id} ledger={d.ledger_balance} wallet={d.wallet_balance} delta={d.delta}")
        sys.exit(1)


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------------------
# Pytest tests
# ---------------------------------------------------------------------------

import pytest


@pytest.mark.asyncio
async def test_all_match_passes():
    ledger = Ledger()
    await ledger.create_posting([
        EntryRequest(account_id="PSP_CLEARING:x", amount=5_000, direction=Direction.DEBIT),
        EntryRequest(account_id="PLAYER_WALLET:p1", amount=5_000, direction=Direction.CREDIT),
    ])
    result = await check_divergence(ledger, {"p1": 5_000})
    assert result.overall_pass is True
    assert result.total_matched == 1


@pytest.mark.asyncio
async def test_divergence_detected():
    ledger = Ledger()
    await ledger.create_posting([
        EntryRequest(account_id="PSP_CLEARING:x", amount=10_000, direction=Direction.DEBIT),
        EntryRequest(account_id="PLAYER_WALLET:p1", amount=10_000, direction=Direction.CREDIT),
    ])
    result = await check_divergence(ledger, {"p1": 9_000})
    assert result.overall_pass is False
    assert result.total_diverged == 1
    assert result.divergences[0].delta == 1_000


@pytest.mark.asyncio
async def test_tolerance_allows_small_difference():
    ledger = Ledger()
    await ledger.create_posting([
        EntryRequest(account_id="PSP_CLEARING:x", amount=10_000, direction=Direction.DEBIT),
        EntryRequest(account_id="PLAYER_WALLET:p1", amount=10_000, direction=Direction.CREDIT),
    ])
    # 50 cent difference with 100 cent tolerance
    result = await check_divergence(ledger, {"p1": 9_950}, tolerance=100)
    assert result.overall_pass is True


@pytest.mark.asyncio
async def test_critical_threshold():
    ledger = Ledger()
    await ledger.create_posting([
        EntryRequest(account_id="PSP_CLEARING:x", amount=100_000, direction=Direction.DEBIT),
        EntryRequest(account_id="PLAYER_WALLET:p1", amount=100_000, direction=Direction.CREDIT),
    ])
    # 15000 cents = 150 EUR -> critical
    result = await check_divergence(ledger, {"p1": 85_000})
    assert result.divergences[0].is_critical is True
    assert result.total_critical == 1


@pytest.mark.asyncio
async def test_demo_check_finds_divergence():
    result = await demo_check()
    assert result.overall_pass is False
    assert result.total_diverged == 1
    assert result.divergences[0].player_id == "p2"
