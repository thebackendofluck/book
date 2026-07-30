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
Release Gate: Treasury Balance Verification

Verifies the operator's treasury is in a healthy state before deployment:
  1. Float adequacy: enough liquidity to cover all player liabilities
  2. PSP settlement positions: no anomalous clearing balances
  3. Bank reconciliation: ledger bank balance matches actual bank balance
  4. No stuck settlements older than threshold

Exit codes:
  0 = PASS (treasury healthy)
  1 = FAIL (financial risk detected -- block deployment)

Compliance: NJ DGE 13:69O-1.9 (patron fund segregation),
            MGA Player Protection Directive 2018,
            UKGC LCCP 4.2.1 (protection of customer funds)
"""

from __future__ import annotations

import json
import sys
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "treasury-service"))

from models import SettlementDirection, SettlementStatus  # ty: ignore[unresolved-import]
from treasury import TreasuryService, TreasuryStore  # ty: ignore[unresolved-import]


@dataclass
class TreasuryGateResult:
    """Output of the treasury balance release gate."""

    # Float check
    float_adequate: bool = False
    float_ratio: float = 0.0
    total_player_liabilities: int = 0
    available_liquid: int = 0
    surplus_or_deficit: int = 0

    # PSP positions
    psp_positions_checked: int = 0
    psp_anomalies: list[str] = field(default_factory=list)

    # Bank reconciliation
    bank_balance_verified: bool = False
    bank_balance: int = 0
    ledger_bank_balance: int = 0
    bank_discrepancy: int = 0

    # Stuck settlements
    stuck_settlement_count: int = 0
    stuck_settlement_threshold_hours: float = 24.0

    # Overall
    overall_pass: bool = False
    checked_at: str = ""
    errors: list[str] = field(default_factory=list)


def check_treasury(
    service: TreasuryService,
    total_player_liabilities: int,
    avg_daily_withdrawals: int = 0,
    actual_bank_balance: int | None = None,
    stuck_threshold_hours: float = 24.0,
    max_psp_clearing_amount: int = 1_000_000_00,  # 1M EUR in cents
) -> TreasuryGateResult:
    """
    Execute all treasury verification checks.

    Args:
        service: Treasury service instance
        total_player_liabilities: Sum of all player balances (from ledger)
        avg_daily_withdrawals: Average daily withdrawal volume
        actual_bank_balance: Real bank balance for reconciliation (None to skip)
        stuck_threshold_hours: Hours before a settlement is considered stuck
        max_psp_clearing_amount: Alert threshold for PSP clearing balance
    """
    result = TreasuryGateResult(
        checked_at=datetime.now(timezone.utc).isoformat(),
        stuck_settlement_threshold_hours=stuck_threshold_hours,
    )

    # Check 1: Float adequacy
    float_req = service.check_float(total_player_liabilities, avg_daily_withdrawals)
    result.float_adequate = float_req.is_adequate
    result.float_ratio = float_req.float_ratio
    result.total_player_liabilities = float_req.total_player_liabilities
    result.available_liquid = float_req.available_liquid
    result.surplus_or_deficit = float_req.surplus_or_deficit

    if not float_req.is_adequate:
        result.errors.append(
            f"FLOAT DEFICIT: liabilities={total_player_liabilities} "
            f"available={float_req.available_liquid} "
            f"deficit={abs(float_req.surplus_or_deficit)}"
        )

    # Check 2: PSP clearing positions
    positions = service.get_all_clearing_positions()
    result.psp_positions_checked = len(positions)

    for pos in positions:
        if abs(pos.net_position) > max_psp_clearing_amount:
            anomaly = (
                f"PSP {pos.psp_name}: clearing balance {pos.net_position} "
                f"exceeds threshold {max_psp_clearing_amount}"
            )
            result.psp_anomalies.append(anomaly)
            result.errors.append(f"PSP ANOMALY: {anomaly}")

    # Check 3: Bank reconciliation (if actual balance provided)
    if actual_bank_balance is not None:
        cash = service.get_operator_cash_position()
        result.ledger_bank_balance = cash.total_bank_settlement
        result.bank_balance = actual_bank_balance
        result.bank_discrepancy = abs(cash.total_bank_settlement - actual_bank_balance)
        result.bank_balance_verified = result.bank_discrepancy == 0

        if not result.bank_balance_verified:
            result.errors.append(
                f"BANK MISMATCH: ledger={cash.total_bank_settlement} "
                f"actual={actual_bank_balance} "
                f"discrepancy={result.bank_discrepancy}"
            )
    else:
        result.bank_balance_verified = True  # Skip if not provided

    # Check 4: Stuck settlements
    stuck = service.detect_stuck_settlements(hours=stuck_threshold_hours)
    result.stuck_settlement_count = len(stuck)

    if stuck:
        result.errors.append(
            f"STUCK SETTLEMENTS: {len(stuck)} settlement(s) older than "
            f"{stuck_threshold_hours}h require investigation"
        )

    # Overall: pass if float is adequate and no bank mismatch
    # (stuck settlements and PSP anomalies are warnings, not blockers)
    result.overall_pass = result.float_adequate and result.bank_balance_verified

    return result


def demo_check() -> TreasuryGateResult:
    """Run against demo data: healthy treasury."""
    svc = TreasuryService(store=TreasuryStore())

    # Record some settlements
    svc.record_settlement("adyen", 100_000, "ADY-001", SettlementDirection.INBOUND)
    svc.record_settlement("paypal", 50_000, "PP-001", SettlementDirection.INBOUND)

    # Settle one (moves to bank)
    s = svc.record_settlement("adyen", 80_000, "ADY-SETTLE-001", SettlementDirection.INBOUND)
    svc.mark_settlement_settled(s.settlement_id)

    return check_treasury(
        service=svc,
        total_player_liabilities=150_000,
        avg_daily_withdrawals=30_000,
        actual_bank_balance=80_000,
    )


def main() -> None:
    result = demo_check()
    print(json.dumps(asdict(result), indent=2))

    if result.overall_pass:
        print(f"\nRESULT: PASS -- float ratio={result.float_ratio:.2f}, treasury healthy")
        sys.exit(0)
    else:
        print("\nRESULT: FAIL -- treasury risk detected")
        for error in result.errors:
            print(f"  ERROR: {error}")
        sys.exit(1)


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------------------
# Pytest tests
# ---------------------------------------------------------------------------

import pytest


def test_healthy_treasury_passes():
    result = demo_check()
    assert result.overall_pass is True
    assert result.float_adequate is True
    assert result.bank_balance_verified is True


def test_float_deficit_fails():
    svc = TreasuryService(store=TreasuryStore())
    # No settlements -> zero available liquidity
    result = check_treasury(svc, total_player_liabilities=500_000)
    assert result.overall_pass is False
    assert result.float_adequate is False
    assert result.surplus_or_deficit < 0


def test_bank_mismatch_fails():
    svc = TreasuryService(store=TreasuryStore())
    svc.record_settlement("adyen", 100_000, "REF-1", SettlementDirection.INBOUND)

    result = check_treasury(
        svc,
        total_player_liabilities=50_000,
        actual_bank_balance=999_999,  # doesn't match
    )
    assert result.bank_balance_verified is False
    assert result.bank_discrepancy > 0


def test_stuck_settlements_detected():
    svc = TreasuryService(store=TreasuryStore())
    s = svc.record_settlement("adyen", 10_000, "REF-STUCK")

    # Backdate to 48 hours ago
    import uuid
    store = svc._store
    aged = s.model_copy(update={
        "settlement_id": str(uuid.uuid4()),
        "reference": "REF-AGED",
        "initiated_at": datetime.now(timezone.utc) - timedelta(hours=48),
    })
    store.save_settlement(aged)

    result = check_treasury(svc, total_player_liabilities=0, stuck_threshold_hours=24.0)
    assert result.stuck_settlement_count >= 1


def test_psp_anomaly_detected():
    svc = TreasuryService(store=TreasuryStore())
    # Huge settlement -> clearing balance exceeds threshold
    svc.record_settlement("adyen", 200_000_00, "BIG-001", SettlementDirection.INBOUND)

    result = check_treasury(
        svc,
        total_player_liabilities=0,
        max_psp_clearing_amount=100_000_00,
    )
    assert len(result.psp_anomalies) >= 1


def test_zero_liabilities_passes():
    svc = TreasuryService(store=TreasuryStore())
    result = check_treasury(svc, total_player_liabilities=0)
    assert result.overall_pass is True
    assert result.float_adequate is True


def test_float_ratio_calculated():
    svc = TreasuryService(store=TreasuryStore())
    svc.record_settlement("adyen", 200_000, "REF-1", SettlementDirection.INBOUND)

    result = check_treasury(svc, total_player_liabilities=100_000)
    assert result.float_ratio >= 2.0
    assert result.surplus_or_deficit == 100_000
