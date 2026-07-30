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
Ledger Service — Core Double-Entry Engine

The ledger is IMMUTABLE: no updates, no deletes, only appends.
Every posting must balance: sum(debits) == sum(credits).
"""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from datetime import datetime, timezone

import structlog

from models import (
    Balance,
    Direction,
    EntryRequest,
    InvariantResult,
    LedgerAccount,
    LedgerEntry,
    Posting,
    PostingRequest,
)

logger = structlog.get_logger()


class LedgerError(Exception):
    pass


class UnbalancedPostingError(LedgerError):
    pass


class DuplicatePostingError(LedgerError):
    pass


class AccountNotFoundError(LedgerError):
    pass


class InMemoryLedgerStore:
    """
    Thread-safe in-memory ledger storage.

    In production this would be backed by PostgreSQL with serializable
    isolation for atomic posting writes.
    """

    def __init__(self) -> None:
        self._entries: list[LedgerEntry] = []
        self._postings: dict[uuid.UUID, Posting] = {}
        self._accounts: dict[str, LedgerAccount] = {}
        self._lock = asyncio.Lock()

    @property
    def entries(self) -> list[LedgerEntry]:
        return list(self._entries)

    @property
    def postings(self) -> dict[uuid.UUID, Posting]:
        return dict(self._postings)

    @property
    def accounts(self) -> dict[str, LedgerAccount]:
        return dict(self._accounts)

    async def register_account(self, account: LedgerAccount) -> LedgerAccount:
        async with self._lock:
            self._accounts[account.account_id] = account
        return account

    async def get_account(self, account_id: str) -> LedgerAccount | None:
        return self._accounts.get(account_id)

    async def append_posting(self, posting: Posting) -> Posting:
        async with self._lock:
            if posting.entry_group_id in self._postings:
                return self._postings[posting.entry_group_id]
            self._postings[posting.entry_group_id] = posting
            self._entries.extend(posting.entries)
        return posting

    async def has_posting(self, entry_group_id: uuid.UUID) -> bool:
        return entry_group_id in self._postings

    async def get_entries_for_account(
        self,
        account_id: str,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
    ) -> list[LedgerEntry]:
        results = []
        for entry in self._entries:
            if entry.account_id != account_id:
                continue
            if from_date and entry.created_at < from_date:
                continue
            if to_date and entry.created_at > to_date:
                continue
            results.append(entry)
        return results

    async def get_all_entries(self) -> list[LedgerEntry]:
        return list(self._entries)

    async def get_all_postings(self) -> list[Posting]:
        return list(self._postings.values())

    async def inject_raw_entry(self, entry: LedgerEntry) -> None:
        """Bypass normal flow — used ONLY for corruption testing."""
        async with self._lock:
            self._entries.append(entry)


class Ledger:
    """Core double-entry accounting engine."""

    def __init__(self, store: InMemoryLedgerStore | None = None) -> None:
        self.store = store or InMemoryLedgerStore()

    async def create_posting(
        self,
        entries: list[EntryRequest],
        entry_group_id: uuid.UUID | None = None,
        metadata: dict[str, str] | None = None,
    ) -> Posting:
        """
        Create a balanced posting atomically.
        Validates that sum(debits) == sum(credits) before writing.
        Idempotent on entry_group_id.
        """
        group_id = entry_group_id or uuid.uuid4()
        meta = metadata or {}

        # Idempotency check
        if await self.store.has_posting(group_id):
            logger.info("duplicate_posting_skipped", entry_group_id=str(group_id))
            return self.store.postings[group_id]

        # Validate balance
        total_debits = sum(e.amount for e in entries if e.direction == Direction.DEBIT)
        total_credits = sum(e.amount for e in entries if e.direction == Direction.CREDIT)

        if total_debits != total_credits:
            raise UnbalancedPostingError(
                f"Unbalanced posting: debits={total_debits}, credits={total_credits}"
            )

        if not any(e.direction == Direction.DEBIT for e in entries):
            raise UnbalancedPostingError("Posting must contain at least one DEBIT entry")
        if not any(e.direction == Direction.CREDIT for e in entries):
            raise UnbalancedPostingError("Posting must contain at least one CREDIT entry")

        now = datetime.now(timezone.utc)
        ledger_entries = [
            LedgerEntry(
                entry_group_id=group_id,
                account_id=e.account_id,
                amount=e.amount,
                direction=e.direction,
                created_at=now,
                metadata=meta,
            )
            for e in entries
        ]

        posting = Posting(
            entry_group_id=group_id,
            entries=ledger_entries,
            created_at=now,
            metadata=meta,
        )

        await self.store.append_posting(posting)

        logger.info(
            "posting_created",
            entry_group_id=str(group_id),
            num_entries=len(ledger_entries),
            amount=total_debits,
        )
        return posting

    async def get_account_balance(self, account_id: str) -> Balance:
        """Calculate account balance from all entries (source of truth)."""
        entries = await self.store.get_entries_for_account(account_id)

        total_debits = sum(e.amount for e in entries if e.direction == Direction.DEBIT)
        total_credits = sum(e.amount for e in entries if e.direction == Direction.CREDIT)

        return Balance(
            account_id=account_id,
            balance=total_debits - total_credits,
            total_debits=total_debits,
            total_credits=total_credits,
            entry_count=len(entries),
        )

    async def get_account_statement(
        self,
        account_id: str,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
    ) -> list[LedgerEntry]:
        """Return all entries for an account within a date range."""
        return await self.store.get_entries_for_account(account_id, from_date, to_date)

    async def verify_invariant(self) -> InvariantResult:
        """
        Verify the fundamental invariant: every posting must balance.
        This is the auditor's best friend.
        """
        postings = await self.store.get_all_postings()
        unbalanced: list[uuid.UUID] = []

        for posting in postings:
            total_debits = sum(
                e.amount for e in posting.entries if e.direction == Direction.DEBIT
            )
            total_credits = sum(
                e.amount for e in posting.entries if e.direction == Direction.CREDIT
            )
            if total_debits != total_credits:
                unbalanced.append(posting.entry_group_id)

        # Also check for orphaned entries (entries not in any posting)
        all_entries = await self.store.get_all_entries()
        all_posting_entry_ids = set()
        for posting in postings:
            for entry in posting.entries:
                all_posting_entry_ids.add(entry.entry_id)

        orphaned_groups: set[uuid.UUID] = set()
        for entry in all_entries:
            if entry.entry_id not in all_posting_entry_ids:
                orphaned_groups.add(entry.entry_group_id)

        all_unbalanced = list(set(unbalanced) | orphaned_groups)

        return InvariantResult(
            is_valid=len(all_unbalanced) == 0,
            total_postings=len(postings),
            unbalanced_postings=all_unbalanced,
        )

    async def rebuild_balance(self, account_id: str) -> Balance:
        """
        Recalculate balance from scratch by replaying all entries.
        Identical to get_account_balance (entries are the source of truth),
        but semantically signals an intentional rebuild.
        """
        logger.info("rebuilding_balance", account_id=account_id)
        return await self.get_account_balance(account_id)
