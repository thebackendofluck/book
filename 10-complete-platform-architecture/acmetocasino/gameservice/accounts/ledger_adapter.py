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
gameservice.accounts.ledger_adapter — LedgerAdapter Protocol + InMemoryLedgerAdapter
=====================================================================================

The ledger is the immutable audit trail of every financial event in the
platform.  Every debit, credit, bonus, rollback, and adjustment creates at
least one ledger entry.

Double-entry bookkeeping
-------------------------
The ledger follows double-entry principles:

* Every entry has a **debit account** (where value leaves) and a **credit
  account** (where value arrives).
* For a player wager: ``debit=player_wallet``, ``credit=house_account``.
* For a win payout: ``debit=house_account``, ``credit=player_wallet``.

In a full implementation the ledger table would have:
``(entry_id, player_id, account_dr, account_cr, amount, currency, created_at,
round_id, supplier_ref, entry_type)``

The :class:`LedgerAdapter` Protocol
-------------------------------------
Following the project's pattern of preferring ``Protocol`` over ``ABC``,
any object with the right method signatures satisfies this contract.  This
makes it easy to:

* Swap in a PostgreSQL, ClickHouse, or DynamoDB adapter for production.
* Use an in-memory adapter for unit and integration tests.
* Inject a spy adapter in test scenarios that assert audit-trail behaviour.

Reconciliation
--------------
:meth:`LedgerAdapter.reconcile` sums all entries for a player and asserts
that the calculated balance matches the wallet service's stored balance.
Discrepancies indicate either a bug in the transaction logic or data
corruption and should trigger an alert.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Entry types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LedgerEntry:
    """An immutable record of a single financial event.

    Attributes
    ----------
    entry_id:
        Unique platform-generated identifier.
    player_id:
        The player whose wallet was affected.
    amount:
        The absolute value of the transaction.  Always non-negative; the
        direction is expressed through ``account_dr`` / ``account_cr``.
    currency:
        ISO-4217 currency code.
    account_dr:
        The account that is *debited* (value leaves this account).
    account_cr:
        The account that is *credited* (value enters this account).
    entry_type:
        Human-readable classifier (e.g. ``"wager"``, ``"win"``,
        ``"rollback"``, ``"bonus_award"``, ``"adjustment"``).
    round_id:
        Supplier round identifier (``None`` for non-round entries such as
        deposits and bonus awards).
    supplier_ref:
        Supplier's own transaction reference for cross-system tracing.
    created_at:
        UTC timestamp when this entry was recorded.
    metadata:
        Arbitrary key-value pairs for extended audit context.
    """

    entry_id: str
    player_id: str
    amount: Decimal
    currency: str
    account_dr: str
    account_cr: str
    entry_type: str
    round_id: str | None = None
    supplier_ref: str | None = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    metadata: dict[str, str] = field(default_factory=dict)

    @classmethod
    def for_wager(
        cls,
        player_id: str,
        amount: Decimal,
        currency: str,
        round_id: str,
        supplier_ref: str | None = None,
    ) -> LedgerEntry:
        """Factory for a wager (debit) entry."""
        return cls(
            entry_id=str(uuid.uuid4()),
            player_id=player_id,
            amount=amount,
            currency=currency,
            account_dr=f"player:{player_id}",
            account_cr="house:game_revenue",
            entry_type="wager",
            round_id=round_id,
            supplier_ref=supplier_ref,
        )

    @classmethod
    def for_win(
        cls,
        player_id: str,
        amount: Decimal,
        currency: str,
        round_id: str,
        supplier_ref: str | None = None,
    ) -> LedgerEntry:
        """Factory for a win payout (credit) entry."""
        return cls(
            entry_id=str(uuid.uuid4()),
            player_id=player_id,
            amount=amount,
            currency=currency,
            account_dr="house:game_revenue",
            account_cr=f"player:{player_id}",
            entry_type="win",
            round_id=round_id,
            supplier_ref=supplier_ref,
        )

    @classmethod
    def for_rollback(
        cls,
        player_id: str,
        amount: Decimal,
        currency: str,
        round_id: str,
        original_entry_id: str,
    ) -> LedgerEntry:
        """Factory for a rollback (reversal) entry."""
        return cls(
            entry_id=str(uuid.uuid4()),
            player_id=player_id,
            amount=amount,
            currency=currency,
            account_dr="house:game_revenue",
            account_cr=f"player:{player_id}",
            entry_type="rollback",
            round_id=round_id,
            metadata={"reverses": original_entry_id},
        )


# ---------------------------------------------------------------------------
# Reconciliation result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReconciliationResult:
    """Summary of a ledger reconciliation run for a single player.

    Attributes
    ----------
    player_id:
        The player that was reconciled.
    calculated_balance:
        The balance derived by summing all ledger entries.
    stored_balance:
        The balance currently held in the wallet service.
    discrepancy:
        ``calculated_balance - stored_balance``.  Should be zero.
    is_balanced:
        ``True`` when ``discrepancy == 0``.
    entry_count:
        Number of ledger entries included in the calculation.
    """

    player_id: str
    calculated_balance: Decimal
    stored_balance: Decimal
    discrepancy: Decimal
    is_balanced: bool
    entry_count: int


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class LedgerAdapter(Protocol):
    """Structural contract for ledger persistence back-ends.

    Any object with these method signatures satisfies this protocol without
    subclassing.  This enables test doubles, alternative backends, and
    decorator adapters (e.g. a caching wrapper) to coexist cleanly.
    """

    def record_entry(self, entry: LedgerEntry) -> str:
        """Persist an immutable ledger entry.

        Parameters
        ----------
        entry:
            The :class:`LedgerEntry` to record.

        Returns
        -------
        str
            The ``entry_id`` of the persisted record.
        """
        ...

    def get_entries(
        self,
        player_id: str,
        from_date: datetime,
        to_date: datetime,
    ) -> list[LedgerEntry]:
        """Return ledger entries for *player_id* within a date range.

        Parameters
        ----------
        player_id:
            Target player.
        from_date:
            Inclusive start of the date range (UTC).
        to_date:
            Inclusive end of the date range (UTC).

        Returns
        -------
        list[LedgerEntry]
            Entries sorted by ``created_at`` ascending.
        """
        ...

    def reconcile(
        self,
        player_id: str,
        stored_balance: Decimal,
    ) -> ReconciliationResult:
        """Compare calculated vs stored balance for audit purposes.

        Parameters
        ----------
        player_id:
            Target player.
        stored_balance:
            The balance reported by the wallet service.

        Returns
        -------
        ReconciliationResult
            Includes a discrepancy field; ``is_balanced=True`` when
            calculated == stored.
        """
        ...


# ---------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------


class InMemoryLedgerAdapter:
    """Thread-safe in-memory ledger for testing and local development.

    All state is ephemeral.  Not suitable for production.

    Reconciliation logic
    --------------------
    For each player, the calculated balance is:

    ::

        calculated = sum(
            +entry.amount   for entries where account_cr starts with "player:"
            -entry.amount   for entries where account_dr starts with "player:"
        )

    This mirrors a simple player-centric ledger view.
    """

    def __init__(self) -> None:
        self._entries: list[LedgerEntry] = []
        self._lock = threading.Lock()

    def record_entry(self, entry: LedgerEntry) -> str:
        with self._lock:
            self._entries.append(entry)
        return entry.entry_id

    def get_entries(
        self,
        player_id: str,
        from_date: datetime,
        to_date: datetime,
    ) -> list[LedgerEntry]:
        from_iso = from_date.isoformat()
        to_iso = to_date.isoformat()
        with self._lock:
            result = [
                e for e in self._entries
                if e.player_id == player_id
                and from_iso <= e.created_at <= to_iso
            ]
        return sorted(result, key=lambda e: e.created_at)

    def reconcile(
        self,
        player_id: str,
        stored_balance: Decimal,
    ) -> ReconciliationResult:
        player_prefix = f"player:{player_id}"
        with self._lock:
            player_entries = [e for e in self._entries if e.player_id == player_id]

        calculated = Decimal("0")
        for entry in player_entries:
            if entry.account_cr.startswith("player:"):
                calculated += entry.amount
            if entry.account_dr.startswith("player:"):
                calculated -= entry.amount

        discrepancy = calculated - stored_balance
        return ReconciliationResult(
            player_id=player_id,
            calculated_balance=calculated,
            stored_balance=stored_balance,
            discrepancy=discrepancy,
            is_balanced=discrepancy == Decimal("0"),
            entry_count=len(player_entries),
        )

    def all_entries_for(self, player_id: str) -> list[LedgerEntry]:
        """Return all entries for *player_id* regardless of date range."""
        with self._lock:
            return [e for e in self._entries if e.player_id == player_id]

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


__all__ = [
    "InMemoryLedgerAdapter",
    "LedgerAdapter",
    "LedgerEntry",
    "ReconciliationResult",
]
