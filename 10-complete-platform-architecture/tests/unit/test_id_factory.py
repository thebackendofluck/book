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
Unit tests for UUID / ID generation helpers in BaseSupplierAdapter and LedgerEntry.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from acmetocasino.gameservice.accounts.ledger_adapter import LedgerEntry
from acmetocasino.gameservice.suppliers.base import BaseSupplierAdapter


def test_new_transaction_id_is_valid_uuid() -> None:
    tid = BaseSupplierAdapter._new_transaction_id()
    parsed = uuid.UUID(tid)
    assert str(parsed) == tid


def test_new_transaction_id_is_unique_each_call() -> None:
    ids = {BaseSupplierAdapter._new_transaction_id() for _ in range(100)}
    assert len(ids) == 100


def test_utcnow_is_timezone_aware() -> None:
    from datetime import timezone
    dt = BaseSupplierAdapter._utcnow()
    assert dt.tzinfo is not None
    assert dt.tzinfo == timezone.utc


def test_ledger_entry_id_is_valid_uuid() -> None:
    entry = LedgerEntry.for_wager(
        player_id="p-1",
        amount=Decimal("1"),
        currency="EUR",
        round_id="r-1",
    )
    parsed = uuid.UUID(entry.entry_id)
    assert str(parsed) == entry.entry_id


def test_ledger_entry_ids_are_unique() -> None:
    ids = set()
    for _ in range(50):
        entry = LedgerEntry.for_wager(
            player_id="p-1", amount=Decimal("1"), currency="EUR", round_id="r-1"
        )
        ids.add(entry.entry_id)
    assert len(ids) == 50


def test_ledger_entry_created_at_is_iso8601() -> None:
    from datetime import datetime, timezone
    entry = LedgerEntry.for_wager(
        player_id="p-1", amount=Decimal("1"), currency="EUR", round_id="r-1"
    )
    # Should parse without error
    dt = datetime.fromisoformat(entry.created_at)
    assert dt.tzinfo is not None


def test_ledger_entry_is_frozen() -> None:
    import pytest
    entry = LedgerEntry.for_wager(
        player_id="p-1", amount=Decimal("1"), currency="EUR", round_id="r-1"
    )
    with pytest.raises(Exception):
        entry.amount = Decimal("999")  # type: ignore[misc]
