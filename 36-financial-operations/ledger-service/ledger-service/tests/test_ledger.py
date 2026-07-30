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
Ledger Service — Test Suite

30+ tests covering the double-entry accounting invariants,
event translation, reconciliation, and edge cases.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import uuid
from importlib.machinery import ModuleSpec
from pathlib import Path
from typing import cast

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Local module loading
# ---------------------------------------------------------------------------
# Each chapter-36 microservice ships its own `models.py` and
# `reconciliation.py`, so full-repo pytest collection loses track of
# which one wins `sys.modules`. We explicitly install this service's
# copies first -- see the matching preamble in payments-platform's
# test_payments.py for the full rationale.
SERVICE_DIR = Path(__file__).resolve().parent.parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))


def _load_local_module(module_name: str, file_name: str):
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(
        module_name, SERVICE_DIR / file_name,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(cast(ModuleSpec, spec))
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_load_local_module("models", "models.py")
_load_local_module("ledger", "ledger.py")
_load_local_module("event_translator", "event_translator.py")
_load_local_module("reconciliation", "reconciliation.py")


@pytest.fixture(autouse=True, scope="module")
def _pin_local_modules():
    """Re-pin this service's modules so sibling files can't affect us."""
    _load_local_module("models", "models.py")
    _load_local_module("ledger", "ledger.py")
    _load_local_module("event_translator", "event_translator.py")
    _load_local_module("reconciliation", "reconciliation.py")
    yield


from event_translator import EventTranslator
from ledger import Ledger, UnbalancedPostingError
from models import (
    AccountType,
    Balance,
    Direction,
    EntryRequest,
    InvariantResult,
    LedgerEntry,
    PostingRequest,
)
from reconciliation import ReconciliationEngine, WalletService, PSPService


# --- Fixtures ---


@pytest_asyncio.fixture
async def ledger():
    return Ledger()


@pytest_asyncio.fixture
async def translator(ledger):
    return EventTranslator(ledger)


@pytest_asyncio.fixture
async def wallet_service():
    return WalletService()


@pytest_asyncio.fixture
async def psp_service():
    return PSPService()


@pytest_asyncio.fixture
async def reconciler(ledger, wallet_service, psp_service):
    return ReconciliationEngine(ledger, wallet_service, psp_service)


# --- 1. Core double-entry invariants ---


@pytest.mark.asyncio
async def test_posting_must_balance(ledger):
    """A balanced posting (debits == credits) succeeds."""
    entries = [
        EntryRequest(account_id="A", amount=1000, direction=Direction.DEBIT),
        EntryRequest(account_id="B", amount=1000, direction=Direction.CREDIT),
    ]
    posting = await ledger.create_posting(entries)
    assert len(posting.entries) == 2
    debits = sum(e.amount for e in posting.entries if e.direction == Direction.DEBIT)
    credits = sum(e.amount for e in posting.entries if e.direction == Direction.CREDIT)
    assert debits == credits == 1000


@pytest.mark.asyncio
async def test_unbalanced_posting_rejected(ledger):
    """An unbalanced posting (off by 1 cent) is rejected."""
    entries = [
        EntryRequest(account_id="A", amount=1000, direction=Direction.DEBIT),
        EntryRequest(account_id="B", amount=999, direction=Direction.CREDIT),
    ]
    with pytest.raises(UnbalancedPostingError):
        await ledger.create_posting(entries)


@pytest.mark.asyncio
async def test_posting_requires_both_sides(ledger):
    """A posting with only debits (no credits) is rejected."""
    entries = [
        EntryRequest(account_id="A", amount=1000, direction=Direction.DEBIT),
        EntryRequest(account_id="B", amount=1000, direction=Direction.DEBIT),
    ]
    with pytest.raises(UnbalancedPostingError):
        await ledger.create_posting(entries)


@pytest.mark.asyncio
async def test_posting_model_validation_rejects_unbalanced():
    """Pydantic model itself rejects unbalanced postings."""
    with pytest.raises(ValueError, match="unbalanced"):
        PostingRequest(
            entries=[
                EntryRequest(account_id="A", amount=100, direction=Direction.DEBIT),
                EntryRequest(account_id="B", amount=50, direction=Direction.CREDIT),
            ]
        )


# --- 2. Event translation ---


@pytest.mark.asyncio
async def test_deposit_creates_correct_entries(translator):
    """Deposit: DEBIT PSP_CLEARING, CREDIT PLAYER_WALLET."""
    posting = await translator.deposit("player1", 5000, "stripe")
    entries = posting.entries
    assert len(entries) == 2

    debit = next(e for e in entries if e.direction == Direction.DEBIT)
    credit = next(e for e in entries if e.direction == Direction.CREDIT)

    assert "PSP_CLEARING" in debit.account_id
    assert "PLAYER_WALLET" in credit.account_id
    assert debit.amount == credit.amount == 5000


@pytest.mark.asyncio
async def test_withdrawal_creates_correct_entries(translator):
    """Withdrawal: DEBIT PLAYER_WALLET, CREDIT PSP_CLEARING."""
    posting = await translator.withdrawal("player1", 3000, "stripe")
    entries = posting.entries

    debit = next(e for e in entries if e.direction == Direction.DEBIT)
    credit = next(e for e in entries if e.direction == Direction.CREDIT)

    assert "PLAYER_WALLET" in debit.account_id
    assert "PSP_CLEARING" in credit.account_id
    assert debit.amount == credit.amount == 3000


@pytest.mark.asyncio
async def test_bet_creates_correct_entries(translator):
    """Bet: DEBIT PLAYER_WALLET, CREDIT OPERATOR_REVENUE."""
    posting = await translator.bet("player1", 2000, "blackjack")
    entries = posting.entries

    debit = next(e for e in entries if e.direction == Direction.DEBIT)
    credit = next(e for e in entries if e.direction == Direction.CREDIT)

    assert "PLAYER_WALLET" in debit.account_id
    assert "OPERATOR_REVENUE" in credit.account_id


@pytest.mark.asyncio
async def test_win_creates_correct_entries(translator):
    """Win: DEBIT OPERATOR_REVENUE, CREDIT PLAYER_WALLET."""
    posting = await translator.win("player1", 8000, "blackjack")
    entries = posting.entries

    debit = next(e for e in entries if e.direction == Direction.DEBIT)
    credit = next(e for e in entries if e.direction == Direction.CREDIT)

    assert "OPERATOR_REVENUE" in debit.account_id
    assert "PLAYER_WALLET" in credit.account_id


@pytest.mark.asyncio
async def test_bonus_grant_creates_correct_entries(translator):
    """Bonus: DEBIT BONUS_LIABILITY, CREDIT PLAYER_WALLET."""
    posting = await translator.bonus_grant("player1", 1000)
    entries = posting.entries

    debit = next(e for e in entries if e.direction == Direction.DEBIT)
    credit = next(e for e in entries if e.direction == Direction.CREDIT)

    assert "BONUS_LIABILITY" in debit.account_id
    assert "PLAYER_WALLET" in credit.account_id


@pytest.mark.asyncio
async def test_tax_withhold_creates_correct_entries(translator):
    """Tax: DEBIT PLAYER_WALLET, CREDIT TAX_LIABILITY."""
    posting = await translator.tax_withhold("player1", 500)
    entries = posting.entries

    debit = next(e for e in entries if e.direction == Direction.DEBIT)
    credit = next(e for e in entries if e.direction == Direction.CREDIT)

    assert "PLAYER_WALLET" in debit.account_id
    assert "TAX_LIABILITY" in credit.account_id


@pytest.mark.asyncio
async def test_psp_settlement_creates_correct_entries(translator):
    """PSP settlement: DEBIT BANK_SETTLEMENT, CREDIT PSP_CLEARING."""
    posting = await translator.psp_settlement("stripe", 50000)
    entries = posting.entries

    debit = next(e for e in entries if e.direction == Direction.DEBIT)
    credit = next(e for e in entries if e.direction == Direction.CREDIT)

    assert "BANK_SETTLEMENT" in debit.account_id
    assert "PSP_CLEARING" in credit.account_id


# --- 3. Balance calculations ---


@pytest.mark.asyncio
async def test_account_balance_is_sum_of_entries(ledger):
    """Balance = sum(debits) - sum(credits) for the account."""
    entries = [
        EntryRequest(account_id="wallet:p1", amount=5000, direction=Direction.CREDIT),
        EntryRequest(account_id="psp:stripe", amount=5000, direction=Direction.DEBIT),
    ]
    await ledger.create_posting(entries)

    balance = await ledger.get_account_balance("wallet:p1")
    assert balance.total_credits == 5000
    assert balance.total_debits == 0
    # For a wallet, net = credits - debits
    assert balance.balance == -5000  # debit-oriented: debits - credits


@pytest.mark.asyncio
async def test_balance_after_deposit_bet_win_withdrawal(translator, ledger):
    """Full lifecycle: deposit -> bet -> win -> withdrawal."""
    await translator.deposit("p1", 10000, "stripe")     # +10000
    await translator.bet("p1", 3000, "slots")           # -3000
    await translator.win("p1", 7000, "slots")           # +7000
    await translator.withdrawal("p1", 5000, "stripe")   # -5000

    balance = await ledger.get_account_balance("PLAYER_WALLET:p1")
    # Net = credits - debits = (10000+7000) - (3000+5000) = 17000 - 8000 = 9000
    wallet_net = balance.total_credits - balance.total_debits
    assert wallet_net == 9000


@pytest.mark.asyncio
async def test_empty_account_balance_is_zero(ledger):
    """An account with no entries has zero balance."""
    balance = await ledger.get_account_balance("nonexistent:account")
    assert balance.balance == 0
    assert balance.total_debits == 0
    assert balance.total_credits == 0
    assert balance.entry_count == 0


# --- 4. Invariant checking ---


@pytest.mark.asyncio
async def test_invariant_check_passes_on_clean_data(translator, ledger):
    """All postings created through normal flow must pass invariant check."""
    await translator.deposit("p1", 5000, "stripe")
    await translator.bet("p1", 2000, "roulette")
    await translator.win("p1", 3000, "roulette")

    result = await ledger.verify_invariant()
    assert result.is_valid is True
    assert result.total_postings == 3
    assert len(result.unbalanced_postings) == 0


@pytest.mark.asyncio
async def test_invariant_check_detects_corruption(ledger):
    """Injecting a raw orphaned entry should be detected by invariant check."""
    # Create a valid posting first
    entries = [
        EntryRequest(account_id="A", amount=1000, direction=Direction.DEBIT),
        EntryRequest(account_id="B", amount=1000, direction=Direction.CREDIT),
    ]
    await ledger.create_posting(entries)

    # Inject a corrupt orphaned entry directly into the store
    orphan = LedgerEntry(
        entry_group_id=uuid.uuid4(),
        account_id="X",
        amount=666,
        direction=Direction.DEBIT,
    )
    await ledger.store.inject_raw_entry(orphan)

    result = await ledger.verify_invariant()
    assert result.is_valid is False
    assert len(result.unbalanced_postings) > 0


# --- 5. Idempotency ---


@pytest.mark.asyncio
async def test_duplicate_posting_idempotent(ledger):
    """Submitting the same entry_group_id twice returns the original posting."""
    group_id = uuid.uuid4()
    entries = [
        EntryRequest(account_id="A", amount=1000, direction=Direction.DEBIT),
        EntryRequest(account_id="B", amount=1000, direction=Direction.CREDIT),
    ]

    posting1 = await ledger.create_posting(entries, entry_group_id=group_id)
    posting2 = await ledger.create_posting(entries, entry_group_id=group_id)

    assert posting1.entry_group_id == posting2.entry_group_id
    assert len(posting1.entries) == len(posting2.entries)

    # Verify only one posting exists
    all_postings = await ledger.store.get_all_postings()
    assert len(all_postings) == 1


@pytest.mark.asyncio
async def test_idempotent_deposit(translator, ledger):
    """Duplicate deposit with same key creates only one posting."""
    key = uuid.uuid4()
    await translator.deposit("p1", 5000, "stripe", idempotency_key=key)
    await translator.deposit("p1", 5000, "stripe", idempotency_key=key)

    balance = await ledger.get_account_balance("PLAYER_WALLET:p1")
    assert balance.total_credits == 5000  # not 10000


# --- 6. Reconciliation ---


@pytest.mark.asyncio
async def test_reconcile_wallet_matches_ledger(
    translator, ledger, wallet_service, reconciler
):
    """When wallet service matches ledger, reconciliation passes."""
    await translator.deposit("p1", 10000, "stripe")
    wallet_service.set_balance("p1", 10000)  # matches ledger

    result = await reconciler.reconcile_wallet_vs_ledger("p1")
    assert result.is_matched is True
    assert result.discrepancy == 0


@pytest.mark.asyncio
async def test_reconcile_detects_divergence(
    translator, ledger, wallet_service, reconciler
):
    """When wallet service diverges from ledger, reconciliation detects it."""
    await translator.deposit("p1", 10000, "stripe")
    wallet_service.set_balance("p1", 9500)  # 500 off

    result = await reconciler.reconcile_wallet_vs_ledger("p1")
    assert result.is_matched is False
    assert result.discrepancy == 500


@pytest.mark.asyncio
async def test_reconcile_psp_matches(translator, ledger, psp_service, reconciler):
    """PSP reconciliation passes when settlement matches clearing balance."""
    await translator.deposit("p1", 20000, "adyen")
    # PSP clearing has 20000 debit (owes us). Report 20000 as outstanding.
    psp_service.set_settlement("adyen", "2026-03-22", 20000)

    result = await reconciler.reconcile_psp_vs_ledger("adyen", "2026-03-22")
    assert result.is_matched is True


@pytest.mark.asyncio
async def test_reconcile_psp_detects_mismatch(
    translator, ledger, psp_service, reconciler
):
    """PSP reconciliation detects mismatch."""
    await translator.deposit("p1", 20000, "adyen")
    psp_service.set_settlement("adyen", "2026-03-22", 15000)  # off by 5000

    result = await reconciler.reconcile_psp_vs_ledger("adyen", "2026-03-22")
    assert result.is_matched is False
    assert result.discrepancy == 5000


@pytest.mark.asyncio
async def test_detect_orphaned_entries(ledger, reconciler):
    """Orphaned entries (not in any posting) are detected."""
    # Normal posting
    entries = [
        EntryRequest(account_id="A", amount=1000, direction=Direction.DEBIT),
        EntryRequest(account_id="B", amount=1000, direction=Direction.CREDIT),
    ]
    await ledger.create_posting(entries)

    # Inject orphan
    orphan = LedgerEntry(
        entry_group_id=uuid.uuid4(),
        account_id="ORPHAN",
        amount=999,
        direction=Direction.DEBIT,
    )
    await ledger.store.inject_raw_entry(orphan)

    orphaned = await reconciler.detect_orphaned_entries()
    assert len(orphaned) == 1
    assert orphaned[0]["account_id"] == "ORPHAN"


@pytest.mark.asyncio
async def test_daily_reconciliation_run(
    translator, ledger, wallet_service, psp_service, reconciler
):
    """Daily run checks all known wallets and PSPs."""
    await translator.deposit("p1", 5000, "stripe")
    await translator.deposit("p2", 8000, "stripe")

    wallet_service.set_balance("p1", 5000)
    wallet_service.set_balance("p2", 8000)

    result = await reconciler.daily_reconciliation_run()
    assert result.total_checked >= 2
    assert result.mismatched == 0


# --- 7. Rebuild balance ---


@pytest.mark.asyncio
async def test_rebuild_balance_matches_current(translator, ledger):
    """Rebuilt balance must equal current balance."""
    await translator.deposit("p1", 10000, "stripe")
    await translator.bet("p1", 3000, "poker")

    current = await ledger.get_account_balance("PLAYER_WALLET:p1")
    rebuilt = await ledger.rebuild_balance("PLAYER_WALLET:p1")

    assert current.balance == rebuilt.balance
    assert current.total_debits == rebuilt.total_debits
    assert current.total_credits == rebuilt.total_credits


# --- 8. Statement ---


@pytest.mark.asyncio
async def test_account_statement_returns_entries(translator, ledger):
    """Statement returns all entries for an account."""
    await translator.deposit("p1", 5000, "stripe")
    await translator.bet("p1", 2000, "slots")

    statement = await ledger.get_account_statement("PLAYER_WALLET:p1")
    assert len(statement) == 2

    # One credit (deposit) and one debit (bet)
    directions = {e.direction for e in statement}
    assert Direction.DEBIT in directions
    assert Direction.CREDIT in directions


@pytest.mark.asyncio
async def test_statement_date_filter(translator, ledger):
    """Statement respects date filters."""
    from datetime import datetime, timezone, timedelta

    await translator.deposit("p1", 5000, "stripe")

    far_future = datetime(2099, 1, 1, tzinfo=timezone.utc)
    statement = await ledger.get_account_statement(
        "PLAYER_WALLET:p1", from_date=far_future
    )
    assert len(statement) == 0


# --- 9. Concurrent operations ---


@pytest.mark.asyncio
async def test_concurrent_postings_dont_corrupt(ledger):
    """Multiple concurrent postings should not corrupt the ledger."""
    async def make_posting(i: int):
        entries = [
            EntryRequest(account_id=f"A:{i}", amount=100, direction=Direction.DEBIT),
            EntryRequest(account_id=f"B:{i}", amount=100, direction=Direction.CREDIT),
        ]
        await ledger.create_posting(entries)

    await asyncio.gather(*[make_posting(i) for i in range(50)])

    result = await ledger.verify_invariant()
    assert result.is_valid is True
    assert result.total_postings == 50


@pytest.mark.asyncio
async def test_concurrent_deposits_to_same_account(translator, ledger):
    """Concurrent deposits to the same wallet must all be recorded."""
    tasks = [translator.deposit("p1", 1000, "stripe") for _ in range(20)]
    await asyncio.gather(*tasks)

    balance = await ledger.get_account_balance("PLAYER_WALLET:p1")
    assert balance.total_credits == 20000


# --- 10. Edge cases ---


@pytest.mark.asyncio
async def test_multi_leg_posting(ledger):
    """A posting with more than 2 legs that still balances."""
    entries = [
        EntryRequest(account_id="A", amount=5000, direction=Direction.DEBIT),
        EntryRequest(account_id="B", amount=3000, direction=Direction.CREDIT),
        EntryRequest(account_id="C", amount=2000, direction=Direction.CREDIT),
    ]
    posting = await ledger.create_posting(entries)
    assert len(posting.entries) == 3

    result = await ledger.verify_invariant()
    assert result.is_valid is True


@pytest.mark.asyncio
async def test_zero_amount_rejected():
    """Zero-amount entries are rejected by Pydantic validation."""
    with pytest.raises(ValueError):
        EntryRequest(account_id="A", amount=0, direction=Direction.DEBIT)


@pytest.mark.asyncio
async def test_negative_amount_rejected():
    """Negative amounts are rejected by Pydantic validation."""
    with pytest.raises(ValueError):
        EntryRequest(account_id="A", amount=-100, direction=Direction.DEBIT)


@pytest.mark.asyncio
async def test_empty_account_id_rejected():
    """Empty account_id is rejected by Pydantic validation."""
    with pytest.raises(ValueError):
        EntryRequest(account_id="", amount=100, direction=Direction.DEBIT)


@pytest.mark.asyncio
async def test_large_amount_posting(ledger):
    """Very large amounts (billions in cents) work correctly."""
    big = 999_999_999_999
    entries = [
        EntryRequest(account_id="A", amount=big, direction=Direction.DEBIT),
        EntryRequest(account_id="B", amount=big, direction=Direction.CREDIT),
    ]
    posting = await ledger.create_posting(entries)
    assert posting.entries[0].amount == big


@pytest.mark.asyncio
async def test_metadata_preserved(translator, ledger):
    """Event metadata is preserved in the posting."""
    posting = await translator.deposit("p1", 5000, "stripe")
    assert posting.metadata["event"] == "deposit"
    assert posting.metadata["player_id"] == "p1"
    assert posting.metadata["psp"] == "stripe"


@pytest.mark.asyncio
async def test_entry_group_id_links_entries(ledger):
    """All entries in a posting share the same entry_group_id."""
    entries = [
        EntryRequest(account_id="A", amount=1000, direction=Direction.DEBIT),
        EntryRequest(account_id="B", amount=1000, direction=Direction.CREDIT),
    ]
    posting = await ledger.create_posting(entries)

    group_ids = {e.entry_group_id for e in posting.entries}
    assert len(group_ids) == 1
    assert posting.entry_group_id in group_ids


@pytest.mark.asyncio
async def test_full_player_lifecycle(translator, ledger, wallet_service, reconciler):
    """
    Full lifecycle: deposit, bonus, bets, wins, tax, withdrawal.
    Verify invariant and reconciliation at the end.
    """
    await translator.deposit("whale", 100_000, "adyen")
    await translator.bonus_grant("whale", 10_000)
    await translator.bet("whale", 50_000, "baccarat")
    await translator.win("whale", 120_000, "baccarat")
    await translator.bet("whale", 30_000, "baccarat")
    await translator.tax_withhold("whale", 15_000)
    await translator.withdrawal("whale", 80_000, "adyen")

    # Wallet net = credits - debits
    # Credits: 100000 + 10000 + 120000 = 230000
    # Debits: 50000 + 30000 + 15000 + 80000 = 175000
    # Net: 55000
    balance = await ledger.get_account_balance("PLAYER_WALLET:whale")
    wallet_net = balance.total_credits - balance.total_debits
    assert wallet_net == 55_000

    # Invariant must hold
    invariant = await ledger.verify_invariant()
    assert invariant.is_valid is True
    assert invariant.total_postings == 7

    # Reconciliation
    wallet_service.set_balance("whale", 55_000)
    recon = await reconciler.reconcile_wallet_vs_ledger("whale")
    assert recon.is_matched is True
