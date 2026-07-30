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
Unit tests for InMemoryLedgerAdapter.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from acmetocasino.gameservice.accounts.ledger_adapter import (
    InMemoryLedgerAdapter,
    LedgerEntry,
)


def _wager_entry(player_id: str = "p-1", amount: str = "10.00") -> LedgerEntry:
    return LedgerEntry.for_wager(
        player_id=player_id,
        amount=Decimal(amount),
        currency="EUR",
        round_id="r-1",
        supplier_ref="ref-1",
    )


def _win_entry(player_id: str = "p-1", amount: str = "20.00") -> LedgerEntry:
    return LedgerEntry.for_win(
        player_id=player_id,
        amount=Decimal(amount),
        currency="EUR",
        round_id="r-1",
        supplier_ref="ref-2",
    )


def test_record_entry_returns_entry_id() -> None:
    adapter = InMemoryLedgerAdapter()
    entry = _wager_entry()
    entry_id = adapter.record_entry(entry)
    assert entry_id == entry.entry_id


def test_len_increases_with_entries() -> None:
    adapter = InMemoryLedgerAdapter()
    assert len(adapter) == 0
    adapter.record_entry(_wager_entry())
    assert len(adapter) == 1
    adapter.record_entry(_win_entry())
    assert len(adapter) == 2


def test_all_entries_for_returns_player_entries() -> None:
    adapter = InMemoryLedgerAdapter()
    adapter.record_entry(_wager_entry("p-1"))
    adapter.record_entry(_wager_entry("p-2"))
    entries = adapter.all_entries_for("p-1")
    assert len(entries) == 1
    assert entries[0].player_id == "p-1"


def test_reconcile_balanced_after_wager_and_win() -> None:
    adapter = InMemoryLedgerAdapter()
    # wager: player loses 10 (dr player, cr house)
    adapter.record_entry(_wager_entry("p-1", "10.00"))
    # win: player gains 20 (dr house, cr player)
    adapter.record_entry(_win_entry("p-1", "20.00"))
    # Net: +20 - 10 = 10 stored balance
    result = adapter.reconcile("p-1", Decimal("10.00"))
    assert result.is_balanced is True
    assert result.discrepancy == Decimal("0")
    assert result.entry_count == 2


def test_reconcile_detects_discrepancy() -> None:
    adapter = InMemoryLedgerAdapter()
    adapter.record_entry(_wager_entry("p-1", "10.00"))
    # stored_balance says 95, but ledger says 90
    result = adapter.reconcile("p-1", Decimal("95.00"))
    assert result.is_balanced is False
    assert result.discrepancy != Decimal("0")


def test_ledger_entry_for_wager_has_correct_accounts() -> None:
    entry = _wager_entry("p-1")
    assert entry.account_dr == "player:p-1"
    assert entry.account_cr == "house:game_revenue"
    assert entry.entry_type == "wager"


def test_ledger_entry_for_win_has_correct_accounts() -> None:
    entry = _win_entry("p-1")
    assert entry.account_dr == "house:game_revenue"
    assert entry.account_cr == "player:p-1"
    assert entry.entry_type == "win"


def test_ledger_entry_for_rollback() -> None:
    entry = LedgerEntry.for_rollback(
        player_id="p-1",
        amount=Decimal("5.00"),
        currency="EUR",
        round_id="r-1",
        original_entry_id="orig-eid",
    )
    assert entry.entry_type == "rollback"
    assert entry.metadata["reverses"] == "orig-eid"


def test_get_entries_by_date_range() -> None:
    from datetime import timedelta
    adapter = InMemoryLedgerAdapter()
    entry = _wager_entry()
    adapter.record_entry(entry)

    now = datetime.now(timezone.utc)
    past = now - timedelta(hours=1)
    future = now + timedelta(hours=1)

    entries = adapter.get_entries("p-1", past, future)
    assert len(entries) == 1


def test_reconcile_no_entries_gives_zero_calculated() -> None:
    adapter = InMemoryLedgerAdapter()
    result = adapter.reconcile("p-new", Decimal("0"))
    assert result.calculated_balance == Decimal("0")
    assert result.is_balanced is True
    assert result.entry_count == 0
