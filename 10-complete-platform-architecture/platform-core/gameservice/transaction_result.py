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
transaction_result.py
---------------------
Standardized transaction response models for the Game Aggregation Layer (GAL).

All supplier integrations return a TransactionResult. The wallet layer
normalizes supplier-specific responses into this canonical form before
returning to the caller. This ensures a uniform interface regardless of
which supplier processed the round.

Amounts are stored as Decimal (not float) to avoid IEEE-754 rounding issues
when dealing with fractional currency values.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class TransactionType(str, Enum):
    """Canonical transaction types understood by the GAL."""

    DEBIT = "DEBIT"       # Player places a bet / stake is taken
    CREDIT = "CREDIT"     # Player wins / stake is returned
    REFUND = "REFUND"     # Incomplete round rolled back
    BONUS = "BONUS"       # Free-rounds award / bonus credit
    ADJUST = "ADJUST"     # Post-settlement resettlement upward
    CLAWBACK = "CLAWBACK" # Post-settlement resettlement downward


class TransactionStatus(str, Enum):
    """Final disposition of a transaction request."""

    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    ALREADY_PROCESSED = "ALREADY_PROCESSED"  # Idempotency replay
    ALREADY_REFUNDED = "ALREADY_REFUNDED"    # Refund on already-refunded tx
    INVALID_OPERATION = "INVALID_OPERATION"  # e.g. refund on non-debit


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class GameServiceError(Exception):
    """Base class for all GAL exceptions."""

    def __init__(self, message: str, supplier_code: Optional[str] = None) -> None:
        super().__init__(message)
        self.supplier_code = supplier_code


class AuthenticationError(GameServiceError):
    """Token or session could not be validated."""


class InvalidSessionError(GameServiceError):
    """Session exists but is in an invalid state (expired, wrong state)."""


class UserLockedError(GameServiceError):
    """Player account is locked; debits are disallowed."""


class InsufficientFundsError(GameServiceError):
    """Player does not have enough balance to cover the debit."""


class TransactionBlockedError(GameServiceError):
    """Transaction rejected due to compliance or responsible-gambling rules."""


class AccountLimitReachedError(GameServiceError):
    """Player has hit a deposit / loss / session limit."""


class GameBlockedError(GameServiceError):
    """Game is blocked for betting (maintenance, jurisdiction, etc.)."""


class NoMatchingDebitError(GameServiceError):
    """A credit or refund references a debit that does not exist."""


class UnknownGameError(GameServiceError):
    """Game ID not recognised by the platform."""


class UnknownPlayerError(GameServiceError):
    """Player record not found."""


class DatabaseError(GameServiceError):
    """Persistence layer failure."""


class GenericSupplierError(GameServiceError):
    """Catch-all for unexpected supplier-side errors."""


# ---------------------------------------------------------------------------
# Balance model
# ---------------------------------------------------------------------------


class BalanceStatus(BaseModel):
    """
    Wallet balance snapshot returned after every transaction.

    Cash and bonus are tracked separately because bonus funds have wagering
    requirements attached. The GAL always returns both values so the game
    client can display the correct playable balance.
    """

    cash_balance: Decimal = Field(ge=Decimal("0"), description="Real-money balance in minor units (pence/cents)")
    bonus_balance: Decimal = Field(ge=Decimal("0"), description="Bonus-money balance in minor units")
    currency: str = Field(min_length=3, max_length=3, description="ISO 4217 currency code")

    @property
    def total_balance(self) -> Decimal:
        return self.cash_balance + self.bonus_balance

    @property
    def total_balance_decimal(self) -> Decimal:
        """Balance as major-unit decimal (e.g. pounds, euros)."""
        return self.total_balance / Decimal("100")

    @field_validator("currency")
    @classmethod
    def currency_uppercase(cls, v: str) -> str:
        return v.upper()

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Core result model
# ---------------------------------------------------------------------------


class TransactionResult(BaseModel):
    """
    Canonical result returned from every GAL transaction operation.

    The `tx_id` is the platform-internal identifier generated before
    calling the supplier. The `external_id` is the supplier's own reference
    (used for reconciliation).  Both are stored in the audit log.
    """

    tx_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Platform transaction ID")
    external_id: Optional[str] = Field(default=None, description="Supplier's own transaction reference")
    status: TransactionStatus
    tx_type: TransactionType
    balance: Optional[BalanceStatus] = None
    cash_usage: Decimal = Field(default=Decimal("0"), ge=Decimal("0"), description="Cash deducted/added in this tx")
    bonus_usage: Decimal = Field(default=Decimal("0"), ge=Decimal("0"), description="Bonus deducted/added in this tx")
    rc_time_elapsed: bool = Field(default=False, description="Reality-check timer exceeded during this transaction")
    error_message: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def succeeded(self) -> bool:
        return self.status == TransactionStatus.SUCCESS

    @property
    def already_processed(self) -> bool:
        return self.status in (
            TransactionStatus.ALREADY_PROCESSED,
            TransactionStatus.ALREADY_REFUNDED,
        )

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def success_result(
    tx_type: TransactionType,
    balance: BalanceStatus,
    tx_id: Optional[str] = None,
    external_id: Optional[str] = None,
    cash_usage: Decimal = Decimal("0"),
    bonus_usage: Decimal = Decimal("0"),
    rc_time_elapsed: bool = False,
) -> TransactionResult:
    """Build a successful TransactionResult."""
    return TransactionResult(
        tx_id=tx_id or str(uuid.uuid4()),
        external_id=external_id,
        status=TransactionStatus.SUCCESS,
        tx_type=tx_type,
        balance=balance,
        cash_usage=cash_usage,
        bonus_usage=bonus_usage,
        rc_time_elapsed=rc_time_elapsed,
    )


def failure_result(
    tx_type: TransactionType,
    error_message: str,
    tx_id: Optional[str] = None,
    balance: Optional[BalanceStatus] = None,
) -> TransactionResult:
    """Build a failed TransactionResult."""
    return TransactionResult(
        tx_id=tx_id or str(uuid.uuid4()),
        status=TransactionStatus.FAILED,
        tx_type=tx_type,
        balance=balance,
        error_message=error_message,
    )


def already_processed_result(
    tx_type: TransactionType,
    tx_id: str,
    balance: Optional[BalanceStatus] = None,
    refunded: bool = False,
) -> TransactionResult:
    """Build an idempotency-replay result."""
    return TransactionResult(
        tx_id=tx_id,
        status=TransactionStatus.ALREADY_REFUNDED if refunded else TransactionStatus.ALREADY_PROCESSED,
        tx_type=tx_type,
        balance=balance,
    )
