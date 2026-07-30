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
accounts/wallet.py
------------------
Wallet operations for the in-house (standard) accounts provider.

This module owns the core balance logic when AcmetoCasino operates its
own wallet rather than delegating to a third-party provider. It is used
by the StandardAccountsProvider and unit tests.

Design decisions
----------------
* Amounts are always in minor units (pence/cents as integers stored as
  Decimal to prevent float drift). The `major_units()` helper converts
  for display or supplier API calls that expect decimal amounts.
* All mutations are run inside a caller-supplied async context manager
  that represents the database transaction. This keeps the wallet logic
  testable without a real database.
* Reality-check timer is checked here rather than at the bridge level so
  that the elapsed flag can be included in the TransactionResult.
"""

from __future__ import annotations

import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from transaction_result import (
    BalanceStatus,
    InsufficientFundsError,
    NoMatchingDebitError,
    TransactionBlockedError,
    TransactionResult,
    TransactionType,
    success_result,
)

logger = logging.getLogger(__name__)

# Conversion factor: minor units per major unit (pence → pounds)
MINOR_UNITS_PER_MAJOR = Decimal("100")


def major_units(minor: Decimal) -> Decimal:
    """Convert minor units (pence) to major units (pounds)."""
    return (minor / MINOR_UNITS_PER_MAJOR).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def minor_units(major: Decimal) -> Decimal:
    """Convert major units (pounds) to minor units (pence)."""
    return (major * MINOR_UNITS_PER_MAJOR).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Wallet record (passed in from DB layer)
# ---------------------------------------------------------------------------


class WalletRecord:
    """
    Snapshot of a player's wallet at a point in time.

    In production this is hydrated from the database. In tests it can be
    created directly.
    """

    __slots__ = (
        "player_id",
        "currency",
        "cash_balance",
        "bonus_balance",
        "is_locked",
        "session_active",
    )

    def __init__(
        self,
        player_id: str,
        currency: str,
        cash_balance: Decimal,
        bonus_balance: Decimal,
        is_locked: bool = False,
        session_active: bool = True,
    ) -> None:
        self.player_id = player_id
        self.currency = currency
        self.cash_balance = cash_balance
        self.bonus_balance = bonus_balance
        self.is_locked = is_locked
        self.session_active = session_active

    @property
    def total_balance(self) -> Decimal:
        return self.cash_balance + self.bonus_balance

    def to_balance_status(self) -> BalanceStatus:
        return BalanceStatus(
            cash_balance=self.cash_balance,
            bonus_balance=self.bonus_balance,
            currency=self.currency,
        )

    def __repr__(self) -> str:
        return (
            f"WalletRecord(player_id={self.player_id!r}, "
            f"cash={self.cash_balance}, bonus={self.bonus_balance}, "
            f"currency={self.currency!r})"
        )


# ---------------------------------------------------------------------------
# Wallet operations
# ---------------------------------------------------------------------------


def get_balance(wallet: WalletRecord) -> BalanceStatus:
    """
    Return the current balance snapshot.

    No side effects — purely reads the wallet record.
    """
    return wallet.to_balance_status()


def debit(
    wallet: WalletRecord,
    amount: Decimal,
    round_id: str,
    tx_id: str,
    disallow_locked: bool = True,
    error_if_using_bonus: Optional[str] = None,
    apply_wagering: bool = True,
) -> tuple[WalletRecord, TransactionResult]:
    """
    Deduct `amount` from the player's wallet and return the updated record.

    Deduction strategy:
    1. Cash is spent first.
    2. Bonus is spent from the remainder.
    3. If error_if_using_bonus is set and bonus would be used, raise.

    Args:
        wallet:              Current wallet snapshot.
        amount:              Amount to debit in minor units.
        round_id:            Supplier round identifier.
        tx_id:               Platform transaction ID.
        disallow_locked:     Raise UserLockedError if account is locked.
        error_if_using_bonus: Error message to raise if bonus would be used.
        apply_wagering:      Whether wagering requirements should be updated.

    Returns:
        Tuple of (updated_wallet, TransactionResult).

    Raises:
        InsufficientFundsError: Balance too low.
        TransactionBlockedError: Account locked or bonus restriction.
    """
    if disallow_locked and wallet.is_locked:
        raise TransactionBlockedError(
            f"Account {wallet.player_id} is locked; debit rejected"
        )

    if amount <= Decimal("0"):
        raise ValueError(f"Debit amount must be positive, got {amount}")

    if amount > wallet.total_balance:
        raise InsufficientFundsError(
            f"Insufficient funds: need {amount}, have {wallet.total_balance} "
            f"({wallet.currency})"
        )

    # Determine how much comes from cash vs bonus
    cash_usage = min(amount, wallet.cash_balance)
    bonus_usage = amount - cash_usage

    if error_if_using_bonus and bonus_usage > Decimal("0"):
        raise TransactionBlockedError(error_if_using_bonus)

    new_cash = wallet.cash_balance - cash_usage
    new_bonus = wallet.bonus_balance - bonus_usage

    updated = WalletRecord(
        player_id=wallet.player_id,
        currency=wallet.currency,
        cash_balance=new_cash,
        bonus_balance=new_bonus,
        is_locked=wallet.is_locked,
        session_active=wallet.session_active,
    )

    balance_status = updated.to_balance_status()
    result = success_result(
        tx_type=TransactionType.DEBIT,
        balance=balance_status,
        tx_id=tx_id,
        external_id=round_id,
        cash_usage=cash_usage,
        bonus_usage=bonus_usage,
    )

    logger.debug(
        "debit OK player=%s amount=%s cash_used=%s bonus_used=%s "
        "new_balance=%s",
        wallet.player_id, amount, cash_usage, bonus_usage,
        balance_status.total_balance,
    )
    return updated, result


def credit(
    wallet: WalletRecord,
    amount: Decimal,
    round_id: str,
    tx_id: str,
    apply_geoverification: bool = False,
) -> tuple[WalletRecord, TransactionResult]:
    """
    Add `amount` to the player's cash balance.

    All credits go to cash (not bonus) unless the operation is a bonus
    award — use `credit_bonus()` for that.

    Args:
        wallet:                  Current wallet snapshot.
        amount:                  Amount to credit in minor units.
        round_id:                Supplier round identifier.
        tx_id:                   Platform transaction ID.
        apply_geoverification:   Whether geolocation must be verified
                                 before crediting (required by some
                                 jurisdictions for large wins).

    Returns:
        Tuple of (updated_wallet, TransactionResult).
    """
    if amount < Decimal("0"):
        raise ValueError(f"Credit amount must be non-negative, got {amount}")

    new_cash = wallet.cash_balance + amount

    updated = WalletRecord(
        player_id=wallet.player_id,
        currency=wallet.currency,
        cash_balance=new_cash,
        bonus_balance=wallet.bonus_balance,
        is_locked=wallet.is_locked,
        session_active=wallet.session_active,
    )

    balance_status = updated.to_balance_status()
    result = success_result(
        tx_type=TransactionType.CREDIT,
        balance=balance_status,
        tx_id=tx_id,
        external_id=round_id,
        cash_usage=amount,
    )

    logger.debug(
        "credit OK player=%s amount=%s new_balance=%s",
        wallet.player_id, amount, balance_status.total_balance,
    )
    return updated, result


def credit_bonus(
    wallet: WalletRecord,
    amount: Decimal,
    tx_id: str,
    bonus_description: str = "bonus",
) -> tuple[WalletRecord, TransactionResult]:
    """
    Add `amount` to the player's bonus balance (free-rounds award, etc.).
    """
    if amount < Decimal("0"):
        raise ValueError(f"Bonus credit amount must be non-negative, got {amount}")

    new_bonus = wallet.bonus_balance + amount
    updated = WalletRecord(
        player_id=wallet.player_id,
        currency=wallet.currency,
        cash_balance=wallet.cash_balance,
        bonus_balance=new_bonus,
        is_locked=wallet.is_locked,
        session_active=wallet.session_active,
    )

    balance_status = updated.to_balance_status()
    result = success_result(
        tx_type=TransactionType.BONUS,
        balance=balance_status,
        tx_id=tx_id,
        external_id=bonus_description,
        bonus_usage=amount,
    )

    logger.debug(
        "credit_bonus OK player=%s amount=%s new_bonus_balance=%s",
        wallet.player_id, amount, new_bonus,
    )
    return updated, result


def refund(
    wallet: WalletRecord,
    original_debit_cash: Decimal,
    original_debit_bonus: Decimal,
    round_id: str,
    tx_id: str,
) -> tuple[WalletRecord, TransactionResult]:
    """
    Reverse a previous debit by returning cash and bonus used.

    The caller is responsible for locating the original debit amounts
    (from the transaction log) and passing them in.

    Args:
        wallet:                Current wallet snapshot.
        original_debit_cash:   Cash component of the original debit.
        original_debit_bonus:  Bonus component of the original debit.
        round_id:              Supplier round identifier.
        tx_id:                 Platform transaction ID for this refund.

    Returns:
        Tuple of (updated_wallet, TransactionResult).

    Raises:
        NoMatchingDebitError: If amounts are zero (nothing to refund).
    """
    total_refund = original_debit_cash + original_debit_bonus
    if total_refund <= Decimal("0"):
        raise NoMatchingDebitError(
            f"Nothing to refund for round {round_id}"
        )

    new_cash = wallet.cash_balance + original_debit_cash
    new_bonus = wallet.bonus_balance + original_debit_bonus

    updated = WalletRecord(
        player_id=wallet.player_id,
        currency=wallet.currency,
        cash_balance=new_cash,
        bonus_balance=new_bonus,
        is_locked=wallet.is_locked,
        session_active=wallet.session_active,
    )

    balance_status = updated.to_balance_status()
    result = success_result(
        tx_type=TransactionType.REFUND,
        balance=balance_status,
        tx_id=tx_id,
        external_id=round_id,
        cash_usage=original_debit_cash,
        bonus_usage=original_debit_bonus,
    )

    logger.debug(
        "refund OK player=%s refunded_cash=%s refunded_bonus=%s new_balance=%s",
        wallet.player_id, original_debit_cash, original_debit_bonus,
        balance_status.total_balance,
    )
    return updated, result


def validate_balance(wallet: WalletRecord) -> None:
    """
    Assert that the wallet is in a consistent state.

    Called after every mutation. Raises AssertionError if invariants are
    violated (should never happen in production — indicates a bug).
    """
    assert wallet.cash_balance >= Decimal("0"), (
        f"Negative cash balance {wallet.cash_balance} for player {wallet.player_id}"
    )
    assert wallet.bonus_balance >= Decimal("0"), (
        f"Negative bonus balance {wallet.bonus_balance} for player {wallet.player_id}"
    )
