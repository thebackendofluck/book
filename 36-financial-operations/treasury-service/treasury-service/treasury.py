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
Treasury Service — Business Logic

Provides the core treasury operations:
  - PSP clearing position tracking
  - Settlement recording and lifecycle
  - Operator aggregate cash position
  - Stuck settlement detection

Design decisions
----------------
* All state is stored in-memory dicts (keyed by ID/name) to keep the
  example self-contained. In production, replace with an asyncpg/SQLAlchemy
  store backed by a Postgres schema.
* Settlement references are treated as idempotency keys: recording the same
  reference twice is a no-op that returns the existing Settlement.
* "Stuck" settlements are those that remain PENDING or IN_TRANSIT for longer
  than the configured `hours` threshold.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from models import (
    AccountType,
    CashPosition,
    ClearingPosition,
    Settlement,
    SettlementDirection,
    SettlementStatus,
    TreasuryAccount,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------


class TreasuryStore:
    """
    Thread-safe-ish in-memory store for treasury accounts and settlements.

    Replace with a proper database adapter in production.
    """

    def __init__(self) -> None:
        self._accounts: dict[str, TreasuryAccount] = {}
        self._settlements: dict[str, Settlement] = {}       # keyed by settlement_id
        self._settlement_by_ref: dict[str, str] = {}        # reference → settlement_id

        # Seed default PSP clearing accounts
        self._seed_defaults()

    def _seed_defaults(self) -> None:
        """Seed commonly used PSP clearing accounts for demo purposes."""
        default_psps = [
            ("adyen", "Adyen EUR Clearing"),
            ("paypal", "PayPal EUR Clearing"),
            ("trustly", "Trustly EUR Clearing"),
            ("neteller", "Neteller EUR Clearing"),
            ("braintree", "Braintree EUR Clearing"),
            ("pix", "Pix BRL Clearing"),
        ]
        currency_map = {"pix": "BRL"}

        for psp_name, label in default_psps:
            currency = currency_map.get(psp_name, "EUR")
            account = TreasuryAccount(
                account_type=AccountType.PSP_CLEARING,
                label=label,
                psp_name=psp_name,
                currency=currency,
                balance=0,
            )
            self._accounts[account.account_id] = account

        # Bank settlement accounts
        for currency in ("EUR", "GBP", "USD"):
            account = TreasuryAccount(
                account_type=AccountType.BANK_SETTLEMENT,
                label=f"Main Bank {currency}",
                currency=currency,
                balance=0,
            )
            self._accounts[account.account_id] = account

        # Tax reserve
        for jurisdiction in ("MT", "GB", "DE"):
            account = TreasuryAccount(
                account_type=AccountType.TAX_RESERVE,
                label=f"Tax Reserve {jurisdiction}",
                currency="EUR",
                balance=0,
            )
            self._accounts[account.account_id] = account

    # ------------------------------------------------------------------
    # Account helpers
    # ------------------------------------------------------------------

    def get_accounts(self) -> list[TreasuryAccount]:
        return list(self._accounts.values())

    def get_account_by_id(self, account_id: str) -> Optional[TreasuryAccount]:
        return self._accounts.get(account_id)

    def get_psp_clearing_account(self, psp_name: str) -> Optional[TreasuryAccount]:
        for acc in self._accounts.values():
            if acc.account_type == AccountType.PSP_CLEARING and acc.psp_name == psp_name:
                return acc
        return None

    def update_account_balance(self, account_id: str, delta: int) -> TreasuryAccount:
        """Atomically apply a balance delta (positive = increase, negative = decrease)."""
        acc = self._accounts.get(account_id)
        if acc is None:
            raise KeyError(f"Account not found: {account_id}")
        updated = acc.model_copy(
            update={"balance": acc.balance + delta, "updated_at": datetime.now(timezone.utc)}
        )
        self._accounts[account_id] = updated
        return updated

    # ------------------------------------------------------------------
    # Settlement helpers
    # ------------------------------------------------------------------

    def get_settlement(self, settlement_id: str) -> Optional[Settlement]:
        return self._settlements.get(settlement_id)

    def get_settlement_by_reference(self, reference: str) -> Optional[Settlement]:
        sid = self._settlement_by_ref.get(reference)
        return self._settlements.get(sid) if sid else None

    def save_settlement(self, settlement: Settlement) -> Settlement:
        self._settlements[settlement.settlement_id] = settlement
        self._settlement_by_ref[settlement.reference] = settlement.settlement_id
        return settlement

    def all_settlements(self) -> list[Settlement]:
        return list(self._settlements.values())

    def settlements_for_psp(self, psp_name: str) -> list[Settlement]:
        return [s for s in self._settlements.values() if s.psp_name == psp_name]


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class TreasuryService:
    """
    Core treasury operations service.

    Inject a TreasuryStore (or a mock of it) via the constructor.
    """

    def __init__(self, store: TreasuryStore | None = None) -> None:
        self._store = store or TreasuryStore()

    # ------------------------------------------------------------------
    # Clearing position
    # ------------------------------------------------------------------

    def get_clearing_position(self, psp_name: str) -> ClearingPosition:
        """
        Return the current clearing position for a given PSP.

        The position is derived from:
          1. The live balance on the PSP_CLEARING account.
          2. All non-terminal settlements to compute pending in-flight amounts.
        """
        clearing_acc = self._store.get_psp_clearing_account(psp_name)
        if clearing_acc is None:
            logger.warning("No clearing account found for PSP %s — returning zero position", psp_name)
            return ClearingPosition(
                psp_name=psp_name,
                currency="EUR",
                net_position=0,
            )

        settlements = self._store.settlements_for_psp(psp_name)

        gross_deposits = sum(
            s.amount for s in settlements if s.direction == SettlementDirection.INBOUND
        )
        gross_withdrawals = sum(
            s.amount for s in settlements if s.direction == SettlementDirection.OUTBOUND
        )
        pending_amount = sum(
            s.amount
            for s in settlements
            if s.status in {SettlementStatus.PENDING, SettlementStatus.IN_TRANSIT}
        )
        last_settled = next(
            (s.amount for s in sorted(
                settlements, key=lambda x: x.initiated_at, reverse=True
            ) if s.status == SettlementStatus.SETTLED),
            0,
        )

        return ClearingPosition(
            psp_name=psp_name,
            currency=clearing_acc.currency,
            gross_deposits=gross_deposits,
            gross_withdrawals=gross_withdrawals,
            last_settled_amount=last_settled,
            net_position=clearing_acc.balance,
            pending_settlement_amount=pending_amount,
        )

    def get_all_clearing_positions(self) -> list[ClearingPosition]:
        """Return clearing positions for every registered PSP."""
        psps = {
            acc.psp_name
            for acc in self._store.get_accounts()
            if acc.account_type == AccountType.PSP_CLEARING and acc.psp_name
        }
        return [self.get_clearing_position(psp) for psp in sorted(psps)]

    # ------------------------------------------------------------------
    # Settlement recording
    # ------------------------------------------------------------------

    def record_settlement(
        self,
        psp_name: str,
        amount: int,
        reference: str,
        direction: SettlementDirection = SettlementDirection.INBOUND,
        currency: str = "EUR",
        notes: str = "",
    ) -> Settlement:
        """
        Record a settlement instruction. Idempotent on `reference`.

        If a settlement with the same `reference` already exists, the
        existing record is returned unchanged — no double-booking.
        """
        existing = self._store.get_settlement_by_reference(reference)
        if existing is not None:
            logger.info(
                "Settlement reference %s already recorded (id=%s) — skipping",
                reference,
                existing.settlement_id,
            )
            return existing

        settlement = Settlement(
            psp_name=psp_name,
            amount=amount,
            currency=currency,
            direction=direction,
            status=SettlementStatus.PENDING,
            reference=reference,
            notes=notes,
        )

        # Update the PSP clearing balance
        clearing_acc = self._store.get_psp_clearing_account(psp_name)
        if clearing_acc:
            delta = amount if direction == SettlementDirection.INBOUND else -amount
            self._store.update_account_balance(clearing_acc.account_id, delta)

        saved = self._store.save_settlement(settlement)
        logger.info(
            "Settlement recorded psp=%s ref=%s amount=%d direction=%s",
            psp_name, reference, amount, direction.value,
        )
        return saved

    def mark_settlement_settled(self, settlement_id: str) -> Settlement:
        """Transition a settlement to SETTLED and credit the bank account."""
        s = self._store.get_settlement(settlement_id)
        if s is None:
            raise ValueError(f"Settlement not found: {settlement_id}")
        if s.is_terminal:
            raise ValueError(
                f"Settlement {settlement_id} is already in terminal status {s.status.value}"
            )

        updated = s.model_copy(
            update={
                "status": SettlementStatus.SETTLED,
                "settled_at": datetime.now(timezone.utc),
            }
        )
        self._store.save_settlement(updated)

        # Credit the corresponding bank settlement account
        bank_accounts = [
            acc for acc in self._store.get_accounts()
            if acc.account_type == AccountType.BANK_SETTLEMENT
            and acc.currency == s.currency
        ]
        if bank_accounts:
            delta = s.amount if s.direction == SettlementDirection.INBOUND else -s.amount
            self._store.update_account_balance(bank_accounts[0].account_id, delta)

        return updated

    def mark_settlement_failed(self, settlement_id: str, reason: str = "") -> Settlement:
        """Mark a settlement as failed."""
        s = self._store.get_settlement(settlement_id)
        if s is None:
            raise ValueError(f"Settlement not found: {settlement_id}")
        if s.is_terminal:
            raise ValueError(
                f"Settlement {settlement_id} is already in terminal status {s.status.value}"
            )
        updated = s.model_copy(
            update={
                "status": SettlementStatus.FAILED,
                "failed_at": datetime.now(timezone.utc),
                "failure_reason": reason,
            }
        )
        return self._store.save_settlement(updated)

    # ------------------------------------------------------------------
    # Operator cash position
    # ------------------------------------------------------------------

    def get_operator_cash_position(self, reporting_currency: str = "EUR") -> CashPosition:
        """
        Return the operator's aggregate cash position across all accounts.

        Note: in production, cross-currency balances would be converted using
        live FX rates. Here we sum naively (suitable when all PSPs are EUR).
        """
        accounts = self._store.get_accounts()

        total_psp = sum(
            acc.balance
            for acc in accounts
            if acc.account_type == AccountType.PSP_CLEARING
        )
        total_bank = sum(
            acc.balance
            for acc in accounts
            if acc.account_type == AccountType.BANK_SETTLEMENT
        )
        total_tax = sum(
            acc.balance
            for acc in accounts
            if acc.account_type == AccountType.TAX_RESERVE
        )

        positions_by_psp = {
            acc.psp_name: acc.balance
            for acc in accounts
            if acc.account_type == AccountType.PSP_CLEARING and acc.psp_name
        }

        return CashPosition(
            total_psp_clearing=total_psp,
            total_bank_settlement=total_bank,
            total_tax_reserve=total_tax,
            currency=reporting_currency,
            positions_by_psp=positions_by_psp,
        )

    # ------------------------------------------------------------------
    # Stuck settlement detection
    # ------------------------------------------------------------------

    def detect_stuck_settlements(self, hours: float = 24.0) -> list[Settlement]:
        """
        Return all non-terminal settlements that have been pending for longer
        than `hours` hours without progressing to SETTLED or FAILED.

        These require manual investigation — bank wire delays, PSP issues, etc.
        """
        stuck = [
            s for s in self._store.all_settlements()
            if not s.is_terminal and s.age_hours >= hours
        ]
        if stuck:
            logger.warning(
                "Detected %d stuck settlement(s) older than %.1f hours",
                len(stuck), hours,
            )
        return sorted(stuck, key=lambda s: s.initiated_at)
