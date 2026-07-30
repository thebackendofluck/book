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
E2E test: full game round lifecycle — launch → bet → win → balance → ledger.

This test exercises the entire stack in-process using in-memory fakes:
  1. Player logs in
  2. Balance is verified
  3. Player places a bet (debit)
  4. Player wins (credit)
  5. Final balance is asserted
  6. A ledger entry is recorded and reconciles correctly
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from acmetocasino.gameservice.accounts.ledger_adapter import InMemoryLedgerAdapter, LedgerEntry
from acmetocasino.gameservice.accounts.wallet_service import InMemoryWalletStore, WalletService
from acmetocasino.gameservice.accounts_bridge import AccountsBridge
from acmetocasino.gameservice.models.enums import CommandType, GameMode
from acmetocasino.gameservice.models.launch_request import LaunchRequest
from acmetocasino.gameservice.models.player_context import PlayerContext
from acmetocasino.gameservice.models.round_command import RoundCommand
from acmetocasino.gameservice.suppliers.registry import SupplierRegistry
from acmetocasino.gameservice.transaction_result import TransactionResult

from tests.conftest import InMemoryAccountsProvider


def test_full_round_lifecycle_slots_win(
    accounts_bridge: AccountsBridge,
    provider: InMemoryAccountsProvider,
    ledger: InMemoryLedgerAdapter,
    supplier_registry: SupplierRegistry,
    player_context: PlayerContext,
) -> None:
    """Player places a £5 bet and wins £12.50 — full lifecycle."""
    # 1. Launch game session
    adapter = supplier_registry.resolve("netent", "acme_uk", "MGA")
    request = LaunchRequest(
        player=player_context,
        game_id="starburst",
        supplier_id="netent",
        mode=GameMode.REAL_MONEY,
    )
    launch = adapter.launch_session(request)
    assert launch.session_id

    # 2. Verify opening balance
    opening = accounts_bridge.get_balance("player-001", "acme_uk")
    assert opening.cash_balance == Decimal("100.00")

    # 3. Debit (bet placement)
    round_id = f"round-{uuid.uuid4()}"
    debit_cmd = RoundCommand(
        command_type=CommandType.DEBIT,
        round_id=round_id,
        amount=Decimal("5.00"),
        supplier_ref=f"ref-debit-{round_id}",
    )
    debit_result = accounts_bridge.debit(
        "player-001", "acme_uk", "starburst", round_id, [debit_cmd]
    )
    assert debit_result.succeeded
    assert debit_result.balance.cash_balance == Decimal("95.00")

    # 4. Record wager in ledger
    wager_entry = LedgerEntry.for_wager(
        player_id="player-001",
        amount=Decimal("5.00"),
        currency="EUR",
        round_id=round_id,
    )
    ledger.record_entry(wager_entry)

    # 5. Credit (win payout)
    credit_cmd = RoundCommand(
        command_type=CommandType.CREDIT,
        round_id=round_id,
        amount=Decimal("12.50"),
        supplier_ref=f"ref-credit-{round_id}",
    )
    credit_result = accounts_bridge.credit(
        "player-001", "acme_uk", "starburst", round_id, [credit_cmd]
    )
    assert credit_result.succeeded
    assert credit_result.balance.cash_balance == Decimal("107.50")

    # 6. Record win in ledger
    win_entry = LedgerEntry.for_win(
        player_id="player-001",
        amount=Decimal("12.50"),
        currency="EUR",
        round_id=round_id,
    )
    ledger.record_entry(win_entry)

    # 7. Assert ledger reconciliation
    # The ledger only recorded the round's wager (-5) and win (+12.5) = net +7.50.
    # The opening 100 was not recorded, so we reconcile against the round's net.
    round_net = Decimal("12.50") - Decimal("5.00")  # 7.50
    reconciliation = ledger.reconcile("player-001", round_net)
    assert reconciliation.is_balanced
    assert reconciliation.entry_count == 2


def test_full_round_lifecycle_losing_spin(
    accounts_bridge: AccountsBridge,
    ledger: InMemoryLedgerAdapter,
) -> None:
    """Player places a £10 bet and loses — balance decreases, no credit."""
    opening = accounts_bridge.get_balance("player-001", "acme_uk")
    initial_cash = opening.cash_balance

    round_id = f"round-loss-{uuid.uuid4()}"
    debit_cmd = RoundCommand(
        command_type=CommandType.DEBIT,
        round_id=round_id,
        amount=Decimal("10.00"),
    )
    result = accounts_bridge.debit("player-001", "acme_uk", "gonzo", round_id, [debit_cmd])
    assert result.balance.cash_balance == initial_cash - Decimal("10.00")

    wager_entry = LedgerEntry.for_wager(
        player_id="player-001",
        amount=Decimal("10.00"),
        currency="EUR",
        round_id=round_id,
    )
    ledger.record_entry(wager_entry)

    # The ledger recorded a wager of -10 (net = -10).
    # Reconcile against the round's net impact on the ledger.
    recon = ledger.reconcile("player-001", Decimal("-10.00"))
    assert recon.is_balanced
    assert recon.entry_count == 1


def test_round_rollback_restores_balance(
    accounts_bridge: AccountsBridge,
) -> None:
    """A rollback after bet restores the player's balance."""
    opening = accounts_bridge.get_balance("player-001", "acme_uk")
    initial = opening.cash_balance

    round_id = f"round-rollback-{uuid.uuid4()}"
    debit_cmd = RoundCommand(
        command_type=CommandType.DEBIT,
        round_id=round_id,
        amount=Decimal("8.00"),
    )
    accounts_bridge.debit("player-001", "acme_uk", "starburst", round_id, [debit_cmd])

    # Rollback (refund)
    rollback_cmd = RoundCommand(
        command_type=CommandType.ROLLBACK,
        round_id=round_id,
        amount=Decimal("8.00"),
    )
    accounts_bridge.refund("player-001", "acme_uk", round_id, [rollback_cmd])

    after = accounts_bridge.get_balance("player-001", "acme_uk")
    assert after.cash_balance == initial


def test_bonus_credited_and_tracked(
    accounts_bridge: AccountsBridge,
) -> None:
    """Adding a bonus increases bonus balance, not cash balance."""
    before = accounts_bridge.get_balance("player-001", "acme_uk")
    cash_before = before.cash_balance

    accounts_bridge.add_bonus("player-001", "acme_uk", Decimal("25.00"), "welcome")

    after = accounts_bridge.get_balance("player-001", "acme_uk")
    assert after.bonus_balance == Decimal("25.00")
    assert after.cash_balance == cash_before  # cash unchanged


def test_multiple_consecutive_rounds(
    accounts_bridge: AccountsBridge,
) -> None:
    """Three consecutive rounds; balance tracked correctly throughout."""
    rounds = [
        (Decimal("5.00"), Decimal("0")),    # lose
        (Decimal("10.00"), Decimal("15.00")),  # win 5
        (Decimal("2.00"), Decimal("10.00")),   # win 8
    ]
    balance = accounts_bridge.get_balance("player-001", "acme_uk").cash_balance
    for i, (bet, win) in enumerate(rounds):
        rid = f"multi-round-{i}"
        accounts_bridge.debit(
            "player-001", "acme_uk", "starburst", rid,
            [RoundCommand(command_type=CommandType.DEBIT, round_id=rid, amount=bet)]
        )
        balance -= bet
        if win > Decimal("0"):
            accounts_bridge.credit(
                "player-001", "acme_uk", "starburst", rid,
                [RoundCommand(command_type=CommandType.CREDIT, round_id=rid, amount=win)]
            )
            balance += win

    final = accounts_bridge.get_balance("player-001", "acme_uk").cash_balance
    assert final == balance
