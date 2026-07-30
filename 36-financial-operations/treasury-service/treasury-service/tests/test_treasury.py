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
Test suite for the Treasury Service.

Coverage (18 tests):
  1.  TreasuryAccount — valid PSP_CLEARING creation
  2.  TreasuryAccount — PSP_CLEARING requires psp_name
  3.  TreasuryAccount — currency normalised to uppercase
  4.  Settlement — idempotency key (same reference → no double-booking)
  5.  Settlement — is_terminal property
  6.  Settlement — age_hours approximation
  7.  ClearingPosition — effective_exposure calculation
  8.  CashPosition — net_liquid excludes tax reserves
  9.  CashPosition — total_assets sum
  10. TreasuryService — fresh store has zero clearing positions
  11. TreasuryService — record_settlement updates clearing balance
  12. TreasuryService — duplicate reference is a no-op
  13. TreasuryService — outbound settlement decreases clearing balance
  14. TreasuryService — mark_settlement_settled credits bank account
  15. TreasuryService — mark_settlement_failed records failure reason
  16. TreasuryService — mark terminal settlement raises ValueError
  17. TreasuryService — get_operator_cash_position reflects all accounts
  18. TreasuryService — detect_stuck_settlements finds aged non-terminal records
"""

from __future__ import annotations

import sys
import os
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import (
    AccountType,
    CashPosition,
    ClearingPosition,
    Settlement,
    SettlementDirection,
    SettlementStatus,
    TreasuryAccount,
)
from treasury import TreasuryService, TreasuryStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def fresh_service() -> TreasuryService:
    """Return a TreasuryService backed by a brand-new store."""
    return TreasuryService(store=TreasuryStore())


def make_settlement(**kwargs) -> Settlement:
    defaults = dict(
        psp_name="adyen",
        amount=10_000,
        currency="EUR",
        direction=SettlementDirection.INBOUND,
        reference="REF-001",
        status=SettlementStatus.PENDING,
    )
    defaults.update(kwargs)
    return Settlement(**defaults)


# ---------------------------------------------------------------------------
# 1. TreasuryAccount — valid PSP_CLEARING creation
# ---------------------------------------------------------------------------


def test_treasury_account_psp_clearing_creation():
    acc = TreasuryAccount(
        account_type=AccountType.PSP_CLEARING,
        label="Adyen EUR Clearing",
        psp_name="adyen",
        currency="EUR",
    )
    assert acc.account_type == AccountType.PSP_CLEARING
    assert acc.psp_name == "adyen"
    assert acc.balance == 0


# ---------------------------------------------------------------------------
# 2. TreasuryAccount — PSP_CLEARING requires psp_name
# ---------------------------------------------------------------------------


def test_treasury_account_psp_clearing_requires_psp_name():
    with pytest.raises(ValueError, match="psp_name"):
        TreasuryAccount(
            account_type=AccountType.PSP_CLEARING,
            label="Unnamed Clearing",
            currency="EUR",
        )


# ---------------------------------------------------------------------------
# 3. TreasuryAccount — currency normalised to uppercase
# ---------------------------------------------------------------------------


def test_treasury_account_currency_uppercase():
    acc = TreasuryAccount(
        account_type=AccountType.BANK_SETTLEMENT,
        label="Bank",
        currency="eur",
    )
    assert acc.currency == "EUR"


# ---------------------------------------------------------------------------
# 4. Settlement — model construction
# ---------------------------------------------------------------------------


def test_settlement_construction():
    s = make_settlement()
    assert s.psp_name == "adyen"
    assert s.amount == 10_000
    assert s.status == SettlementStatus.PENDING


# ---------------------------------------------------------------------------
# 5. Settlement — is_terminal property
# ---------------------------------------------------------------------------


def test_settlement_terminal_statuses():
    terminal_statuses = [SettlementStatus.SETTLED, SettlementStatus.FAILED, SettlementStatus.CANCELLED]
    for st in terminal_statuses:
        s = make_settlement(status=st)
        assert s.is_terminal, f"{st} should be terminal"

    non_terminal = [SettlementStatus.PENDING, SettlementStatus.IN_TRANSIT]
    for st in non_terminal:
        s = make_settlement(status=st)
        assert not s.is_terminal, f"{st} should not be terminal"


# ---------------------------------------------------------------------------
# 6. Settlement — age_hours approximation
# ---------------------------------------------------------------------------


def test_settlement_age_hours():
    # A settlement initiated 48 hours ago
    past = datetime.now(timezone.utc) - timedelta(hours=48)
    s = make_settlement(initiated_at=past)
    assert s.age_hours >= 47.9
    assert s.age_hours <= 48.1


# ---------------------------------------------------------------------------
# 7. ClearingPosition — effective_exposure calculation
# ---------------------------------------------------------------------------


def test_clearing_position_effective_exposure():
    pos = ClearingPosition(
        psp_name="adyen",
        currency="EUR",
        net_position=50_000,
        pending_settlement_amount=20_000,
    )
    assert pos.effective_exposure == 30_000  # 50k - 20k


def test_clearing_position_negative_effective_exposure():
    pos = ClearingPosition(
        psp_name="trustly",
        currency="EUR",
        net_position=5_000,
        pending_settlement_amount=15_000,
    )
    # In-flight exceeds current balance — over-committed
    assert pos.effective_exposure == -10_000


# ---------------------------------------------------------------------------
# 8. CashPosition — net_liquid excludes tax reserves
# ---------------------------------------------------------------------------


def test_cash_position_net_liquid():
    pos = CashPosition(
        total_psp_clearing=100_000,
        total_bank_settlement=500_000,
        total_tax_reserve=75_000,
    )
    # net_liquid = bank settlement only
    assert pos.net_liquid == 500_000


# ---------------------------------------------------------------------------
# 9. CashPosition — total_assets sum
# ---------------------------------------------------------------------------


def test_cash_position_total_assets():
    pos = CashPosition(
        total_psp_clearing=100_000,
        total_bank_settlement=500_000,
        total_tax_reserve=75_000,
    )
    assert pos.total_assets == 675_000


# ---------------------------------------------------------------------------
# 10. TreasuryService — seeded store has PSP accounts with zero balance
# ---------------------------------------------------------------------------


def test_fresh_store_zero_clearing_positions():
    svc = fresh_service()
    positions = svc.get_all_clearing_positions()
    assert len(positions) > 0
    for p in positions:
        assert p.net_position == 0


# ---------------------------------------------------------------------------
# 11. TreasuryService — record_settlement updates clearing balance
# ---------------------------------------------------------------------------


def test_record_settlement_updates_clearing_balance():
    svc = fresh_service()
    svc.record_settlement(
        psp_name="adyen",
        amount=50_000,
        reference="SETTLE-001",
        direction=SettlementDirection.INBOUND,
    )
    pos = svc.get_clearing_position("adyen")
    assert pos.net_position == 50_000


# ---------------------------------------------------------------------------
# 12. TreasuryService — duplicate reference is a no-op (idempotency)
# ---------------------------------------------------------------------------


def test_record_settlement_idempotent():
    svc = fresh_service()

    s1 = svc.record_settlement(
        psp_name="adyen",
        amount=50_000,
        reference="SETTLE-DUP",
    )
    s2 = svc.record_settlement(
        psp_name="adyen",
        amount=99_999,  # different amount — must be ignored
        reference="SETTLE-DUP",
    )

    # Same settlement returned
    assert s1.settlement_id == s2.settlement_id
    assert s2.amount == 50_000  # original amount unchanged

    # Balance only credited once
    pos = svc.get_clearing_position("adyen")
    assert pos.net_position == 50_000


# ---------------------------------------------------------------------------
# 13. TreasuryService — outbound settlement decreases clearing balance
# ---------------------------------------------------------------------------


def test_outbound_settlement_decreases_clearing():
    svc = fresh_service()
    # First put some balance in via inbound
    svc.record_settlement("paypal", 80_000, "IN-001", SettlementDirection.INBOUND)
    svc.record_settlement("paypal", 30_000, "OUT-001", SettlementDirection.OUTBOUND)

    pos = svc.get_clearing_position("paypal")
    assert pos.net_position == 50_000  # 80k - 30k


# ---------------------------------------------------------------------------
# 14. TreasuryService — mark_settlement_settled credits bank account
# ---------------------------------------------------------------------------


def test_mark_settlement_settled_credits_bank():
    svc = fresh_service()
    initial_cash = svc.get_operator_cash_position()

    s = svc.record_settlement("adyen", 100_000, "SETTLE-BANK")
    settled = svc.mark_settlement_settled(s.settlement_id)

    assert settled.status == SettlementStatus.SETTLED
    assert settled.settled_at is not None

    final_cash = svc.get_operator_cash_position()
    # Bank settlement balance should have increased by 100_000
    assert final_cash.total_bank_settlement == initial_cash.total_bank_settlement + 100_000


# ---------------------------------------------------------------------------
# 15. TreasuryService — mark_settlement_failed records failure reason
# ---------------------------------------------------------------------------


def test_mark_settlement_failed():
    svc = fresh_service()
    s = svc.record_settlement("trustly", 25_000, "SETTLE-FAIL")
    failed = svc.mark_settlement_failed(s.settlement_id, reason="Bank wire rejected")

    assert failed.status == SettlementStatus.FAILED
    assert failed.failure_reason == "Bank wire rejected"
    assert failed.failed_at is not None


# ---------------------------------------------------------------------------
# 16. TreasuryService — marking terminal settlement raises ValueError
# ---------------------------------------------------------------------------


def test_mark_terminal_settlement_raises():
    svc = fresh_service()
    s = svc.record_settlement("neteller", 10_000, "SETTLE-T1")
    svc.mark_settlement_settled(s.settlement_id)

    with pytest.raises(ValueError, match="terminal"):
        svc.mark_settlement_settled(s.settlement_id)


# ---------------------------------------------------------------------------
# 17. TreasuryService — get_operator_cash_position reflects all accounts
# ---------------------------------------------------------------------------


def test_operator_cash_position_multi_psp():
    svc = fresh_service()
    svc.record_settlement("adyen", 100_000, "A1")
    svc.record_settlement("paypal", 200_000, "P1")

    pos = svc.get_operator_cash_position()

    assert pos.total_psp_clearing == 300_000
    assert pos.positions_by_psp["adyen"] == 100_000
    assert pos.positions_by_psp["paypal"] == 200_000
    assert pos.total_assets >= 300_000  # may include tax reserves (zero)


# ---------------------------------------------------------------------------
# 18. TreasuryService — detect_stuck_settlements
# ---------------------------------------------------------------------------


def test_detect_stuck_settlements():
    svc = fresh_service()

    # Create a fresh settlement (age ~ 0) — should NOT be stuck at 24h threshold
    s_new = svc.record_settlement("adyen", 10_000, "FRESH-001")

    # Manually back-date a settlement to simulate 48-hour old pending
    store = svc._store
    aged_settlement = s_new.model_copy(
        update={
            "reference": "AGED-001",
            "initiated_at": datetime.now(timezone.utc) - timedelta(hours=48),
        }
    )
    # Give it a unique ID so it doesn't overwrite the fresh one
    import uuid
    aged_settlement = aged_settlement.model_copy(
        update={"settlement_id": str(uuid.uuid4())}
    )
    store.save_settlement(aged_settlement)

    stuck = svc.detect_stuck_settlements(hours=24.0)

    assert len(stuck) >= 1
    stuck_ids = [s.settlement_id for s in stuck]
    assert aged_settlement.settlement_id in stuck_ids
    # The freshly-created one should not appear
    assert s_new.settlement_id not in stuck_ids
