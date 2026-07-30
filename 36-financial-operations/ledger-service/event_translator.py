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
Ledger Service — Event Translator

Translates business-domain events into double-entry ledger postings.
Each function maps a single business event to a balanced set of entries.
"""

from __future__ import annotations

import uuid

import structlog

from ledger import Ledger
from models import AccountType, Direction, EntryRequest, Posting

logger = structlog.get_logger()


def _account_id(account_type: AccountType, entity_id: str) -> str:
    """Build a deterministic account ID from type and entity."""
    return f"{account_type.value}:{entity_id}"


class EventTranslator:
    """Translates business events into balanced double-entry postings."""

    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger

    async def deposit(
        self,
        player_id: str,
        amount: int,
        psp: str,
        idempotency_key: uuid.UUID | None = None,
    ) -> Posting:
        """
        Player deposits money via PSP.
        DEBIT PSP_CLEARING (PSP owes us money)
        CREDIT PLAYER_WALLET (player balance increases)
        """
        entries = [
            EntryRequest(
                account_id=_account_id(AccountType.PSP_CLEARING, psp),
                amount=amount,
                direction=Direction.DEBIT,
            ),
            EntryRequest(
                account_id=_account_id(AccountType.PLAYER_WALLET, player_id),
                amount=amount,
                direction=Direction.CREDIT,
            ),
        ]
        return await self.ledger.create_posting(
            entries,
            entry_group_id=idempotency_key,
            metadata={"event": "deposit", "player_id": player_id, "psp": psp},
        )

    async def withdrawal(
        self,
        player_id: str,
        amount: int,
        psp: str,
        idempotency_key: uuid.UUID | None = None,
    ) -> Posting:
        """
        Player withdraws money via PSP.
        DEBIT PLAYER_WALLET (player balance decreases)
        CREDIT PSP_CLEARING (we owe PSP money)
        """
        entries = [
            EntryRequest(
                account_id=_account_id(AccountType.PLAYER_WALLET, player_id),
                amount=amount,
                direction=Direction.DEBIT,
            ),
            EntryRequest(
                account_id=_account_id(AccountType.PSP_CLEARING, psp),
                amount=amount,
                direction=Direction.CREDIT,
            ),
        ]
        return await self.ledger.create_posting(
            entries,
            entry_group_id=idempotency_key,
            metadata={"event": "withdrawal", "player_id": player_id, "psp": psp},
        )

    async def bet(
        self,
        player_id: str,
        amount: int,
        game: str,
        idempotency_key: uuid.UUID | None = None,
    ) -> Posting:
        """
        Player places a bet.
        DEBIT PLAYER_WALLET (player balance decreases)
        CREDIT OPERATOR_REVENUE (operator earns)
        """
        entries = [
            EntryRequest(
                account_id=_account_id(AccountType.PLAYER_WALLET, player_id),
                amount=amount,
                direction=Direction.DEBIT,
            ),
            EntryRequest(
                account_id=_account_id(AccountType.OPERATOR_REVENUE, game),
                amount=amount,
                direction=Direction.CREDIT,
            ),
        ]
        return await self.ledger.create_posting(
            entries,
            entry_group_id=idempotency_key,
            metadata={"event": "bet", "player_id": player_id, "game": game},
        )

    async def win(
        self,
        player_id: str,
        amount: int,
        game: str,
        idempotency_key: uuid.UUID | None = None,
    ) -> Posting:
        """
        Player wins.
        DEBIT OPERATOR_REVENUE (operator pays out)
        CREDIT PLAYER_WALLET (player balance increases)
        """
        entries = [
            EntryRequest(
                account_id=_account_id(AccountType.OPERATOR_REVENUE, game),
                amount=amount,
                direction=Direction.DEBIT,
            ),
            EntryRequest(
                account_id=_account_id(AccountType.PLAYER_WALLET, player_id),
                amount=amount,
                direction=Direction.CREDIT,
            ),
        ]
        return await self.ledger.create_posting(
            entries,
            entry_group_id=idempotency_key,
            metadata={"event": "win", "player_id": player_id, "game": game},
        )

    async def bonus_grant(
        self,
        player_id: str,
        amount: int,
        idempotency_key: uuid.UUID | None = None,
    ) -> Posting:
        """
        Bonus granted to player.
        DEBIT BONUS_LIABILITY (company takes on liability)
        CREDIT PLAYER_WALLET (player balance increases)
        """
        entries = [
            EntryRequest(
                account_id=_account_id(AccountType.BONUS_LIABILITY, "company"),
                amount=amount,
                direction=Direction.DEBIT,
            ),
            EntryRequest(
                account_id=_account_id(AccountType.PLAYER_WALLET, player_id),
                amount=amount,
                direction=Direction.CREDIT,
            ),
        ]
        return await self.ledger.create_posting(
            entries,
            entry_group_id=idempotency_key,
            metadata={"event": "bonus_grant", "player_id": player_id},
        )

    async def tax_withhold(
        self,
        player_id: str,
        amount: int,
        idempotency_key: uuid.UUID | None = None,
    ) -> Posting:
        """
        Tax withheld from player.
        DEBIT PLAYER_WALLET (player balance decreases)
        CREDIT TAX_LIABILITY (tax obligation recorded)
        """
        entries = [
            EntryRequest(
                account_id=_account_id(AccountType.PLAYER_WALLET, player_id),
                amount=amount,
                direction=Direction.DEBIT,
            ),
            EntryRequest(
                account_id=_account_id(AccountType.TAX_LIABILITY, "authority"),
                amount=amount,
                direction=Direction.CREDIT,
            ),
        ]
        return await self.ledger.create_posting(
            entries,
            entry_group_id=idempotency_key,
            metadata={"event": "tax_withhold", "player_id": player_id},
        )

    async def psp_settlement(
        self,
        psp: str,
        amount: int,
        idempotency_key: uuid.UUID | None = None,
    ) -> Posting:
        """
        PSP settles with operator (money lands in bank).
        DEBIT BANK_SETTLEMENT (bank balance increases)
        CREDIT PSP_CLEARING (PSP clears its obligation)
        """
        entries = [
            EntryRequest(
                account_id=_account_id(AccountType.BANK_SETTLEMENT, "main"),
                amount=amount,
                direction=Direction.DEBIT,
            ),
            EntryRequest(
                account_id=_account_id(AccountType.PSP_CLEARING, psp),
                amount=amount,
                direction=Direction.CREDIT,
            ),
        ]
        return await self.ledger.create_posting(
            entries,
            entry_group_id=idempotency_key,
            metadata={"event": "psp_settlement", "psp": psp},
        )

    async def psp_fee(
        self,
        psp: str,
        amount: int,
        fee_type: str = "processing",
        idempotency_key: uuid.UUID | None = None,
    ) -> Posting:
        """
        PSP processing fee charged on a transaction.
        DEBIT FEE_EXPENSE (cost increases)
        CREDIT PSP_CLEARING (PSP retains fee from clearing balance)
        """
        entries = [
            EntryRequest(
                account_id=_account_id(AccountType.FEE_EXPENSE, f"{psp}:{fee_type}"),
                amount=amount,
                direction=Direction.DEBIT,
            ),
            EntryRequest(
                account_id=_account_id(AccountType.PSP_CLEARING, psp),
                amount=amount,
                direction=Direction.CREDIT,
            ),
        ]
        return await self.ledger.create_posting(
            entries,
            entry_group_id=idempotency_key,
            metadata={"event": "psp_fee", "psp": psp, "fee_type": fee_type},
        )

    async def suspense_park(
        self,
        source_account_id: str,
        amount: int,
        reason: str = "under_investigation",
        idempotency_key: uuid.UUID | None = None,
    ) -> Posting:
        """
        Park money in suspense while under investigation.
        DEBIT SUSPENSE (money held pending investigation)
        CREDIT source_account (removed from source until resolved)

        Used when a transaction is flagged by fraud, AML, or reconciliation
        mismatch. Money is not lost, just frozen until manually resolved.
        """
        entries = [
            EntryRequest(
                account_id=_account_id(AccountType.SUSPENSE, reason),
                amount=amount,
                direction=Direction.DEBIT,
            ),
            EntryRequest(
                account_id=source_account_id,
                amount=amount,
                direction=Direction.CREDIT,
            ),
        ]
        return await self.ledger.create_posting(
            entries,
            entry_group_id=idempotency_key,
            metadata={"event": "suspense_park", "source": source_account_id, "reason": reason},
        )

    async def suspense_release(
        self,
        target_account_id: str,
        amount: int,
        reason: str = "investigation_cleared",
        idempotency_key: uuid.UUID | None = None,
    ) -> Posting:
        """
        Release money from suspense back to the target account.
        DEBIT target_account (money returned)
        CREDIT SUSPENSE (suspense reduced)
        """
        entries = [
            EntryRequest(
                account_id=target_account_id,
                amount=amount,
                direction=Direction.DEBIT,
            ),
            EntryRequest(
                account_id=_account_id(AccountType.SUSPENSE, "under_investigation"),
                amount=amount,
                direction=Direction.CREDIT,
            ),
        ]
        return await self.ledger.create_posting(
            entries,
            entry_group_id=idempotency_key,
            metadata={"event": "suspense_release", "target": target_account_id, "reason": reason},
        )
