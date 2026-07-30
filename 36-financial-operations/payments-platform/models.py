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
Domain models for the AcmetoCasino payments platform.

All monetary amounts are stored as integers representing the smallest currency
unit (cents / pence / øre). A EUR deposit of 50.00 is stored as 5000.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class PaymentStatus(str, Enum):
    """Full lifecycle of a payment transaction."""

    STARTED = "STARTED"
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    VERIFY = "VERIFY"          # 3-D Secure / additional auth required
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    ABANDONED = "ABANDONED"    # Timed out with unknown outcome
    VOIDING = "VOIDING"
    VOIDED = "VOIDED"
    VOID_FAILED = "VOID_FAILED"
    REFUNDED = "REFUNDED"

    @property
    def is_terminal(self) -> bool:
        return self in {
            PaymentStatus.SUCCEEDED,
            PaymentStatus.FAILED,
            PaymentStatus.ABANDONED,
            PaymentStatus.VOIDED,
            PaymentStatus.VOID_FAILED,
            PaymentStatus.REFUNDED,
        }

    @property
    def is_locking(self) -> bool:
        """Locking states prevent concurrent operations on the same user/payment."""
        return self in {
            PaymentStatus.PROCESSING,
            PaymentStatus.SUCCEEDED,
        }


class WithdrawalStatus(str, Enum):
    PENDING = "PENDING"
    REVIEW = "REVIEW"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    PROCESSING = "PROCESSING"
    FAILED = "FAILED"
    REVERSED = "REVERSED"
    TIMED_OUT = "TIMED_OUT"
    STARTED = "STARTED"
    INVALIDATED = "INVALIDATED"
    ACCEPTING_ON_ACCOUNT = "ACCEPTING_ON_ACCOUNT"

    @property
    def cannot_process(self) -> bool:
        return self in {
            WithdrawalStatus.ACCEPTED,
            WithdrawalStatus.REJECTED,
            WithdrawalStatus.REVERSED,
            WithdrawalStatus.PROCESSING,
            WithdrawalStatus.TIMED_OUT,
            WithdrawalStatus.ACCEPTING_ON_ACCOUNT,
            WithdrawalStatus.INVALIDATED,
        }


class TransactionType(str, Enum):
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"


class PaymentMethod(str, Enum):
    CARD = "card"
    PAYPAL = "paypal"
    APPLE_PAY = "apple_pay"
    GOOGLE_PAY = "google_pay"
    BANK_TRANSFER = "bank_transfer"
    PIX = "pix"
    BOLETO = "boleto"
    NETELLER = "neteller"
    SKRILL = "skrill"
    TRUSTLY = "trustly"
    OFFLINE = "offline"


class KycStatus(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class FraudDecision(str, Enum):
    ALLOW = "ALLOW"
    REVIEW = "REVIEW"
    BLOCK = "BLOCK"


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


class CurrencyConversionResult(BaseModel):
    source_currency: str
    source_amount: int
    target_currency: str
    exchanged_amount: int
    exchange_rate: float
    converted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class FailureInfo(BaseModel):
    failure_type: Optional[str] = None
    failure_reason: Optional[str] = None


class PaymentProviderInfo(BaseModel):
    provider_name: str
    external_transaction_id: Optional[str] = None
    extra: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Core domain models
# ---------------------------------------------------------------------------


class Transaction(BaseModel):
    """Immutable record of a single financial event."""

    transaction_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    payment_id: str
    transaction_type: TransactionType
    amount: int                         # smallest currency unit
    currency: str = Field(min_length=3, max_length=3)
    status: PaymentStatus
    provider_name: str
    external_id: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("currency")
    @classmethod
    def currency_uppercase(cls, v: str) -> str:
        return v.upper()


class Payment(BaseModel):
    """A deposit or withdrawal attempt."""

    payment_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    brand_id: int
    user_id: int
    amount: int
    currency: str = Field(min_length=3, max_length=3)
    user_ip: str
    method: PaymentMethod
    status: PaymentStatus = PaymentStatus.STARTED
    bonus_group_id: Optional[int] = None
    mobile: bool = False
    country_code: str = Field(min_length=2, max_length=2)
    language: str = "en"
    provider_info: PaymentProviderInfo = Field(
        default_factory=lambda: PaymentProviderInfo(provider_name="unknown")
    )
    failure_info: FailureInfo = Field(default_factory=FailureInfo)
    converted_amount: Optional[CurrencyConversionResult] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("currency")
    @classmethod
    def currency_uppercase(cls, v: str) -> str:
        return v.upper()

    @property
    def psp_amount(self) -> int:
        """Amount to send to the PSP (may be converted)."""
        if self.converted_amount:
            return self.converted_amount.exchanged_amount
        return self.amount

    @property
    def psp_currency(self) -> str:
        if self.converted_amount:
            return self.converted_amount.target_currency
        return self.currency


class DepositRequest(BaseModel):
    brand_id: int
    user_id: int
    amount: int
    currency: str
    user_ip: str
    method: PaymentMethod
    country_code: str
    language: str = "en"
    mobile: bool = False
    bonus_group_id: Optional[int] = None
    recurring_detail_reference: Optional[str] = None
    params: dict[str, Any] = Field(default_factory=dict)


class Deposit(Payment):
    """Specialised payment for incoming funds."""

    transaction_type: TransactionType = TransactionType.DEPOSIT
    instrument_id: Optional[str] = None     # stored card / e-wallet token


class Withdrawal(BaseModel):
    """Outbound payment request."""

    withdrawal_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    brand_id: int
    user_id: int
    amount: int
    currency: str = Field(min_length=3, max_length=3)
    method: PaymentMethod
    status: WithdrawalStatus = WithdrawalStatus.STARTED
    external_id: Optional[str] = None
    error_message: Optional[str] = None
    actioned_by: Optional[int] = None        # admin user id
    processor: Optional[str] = None
    details: dict[str, Any] = Field(default_factory=dict)
    auto_review_at: Optional[datetime] = None
    confirm_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("currency")
    @classmethod
    def currency_uppercase(cls, v: str) -> str:
        return v.upper()


class PSPResponse(BaseModel):
    """Normalised response returned by any PSP adapter."""

    success: bool
    external_transaction_id: Optional[str] = None
    redirect_url: Optional[str] = None
    status: PaymentStatus
    raw_response: dict[str, Any] = Field(default_factory=dict)
    error_code: Optional[str] = None
    error_message: Optional[str] = None


class ReconciliationRecord(BaseModel):
    """One row in a daily reconciliation report."""

    record_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    date: str                               # YYYY-MM-DD
    provider_name: str
    currency: str
    total_deposits: int
    total_withdrawals: int
    total_refunds: int
    transaction_count: int
    discrepancy_amount: int = 0
    notes: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class FraudScore(BaseModel):
    payment_id: str
    user_id: int
    score: float = Field(ge=0.0, le=1.0)
    decision: FraudDecision
    signals: list[str] = Field(default_factory=list)
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
