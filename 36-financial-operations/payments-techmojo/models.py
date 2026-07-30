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
Domain models for the TechMojo payments integration.

TechMojo is a payment technology provider. This service integrates with TechMojo
by consuming Kafka events and processing deposits/voids through the platform.

Key domain types:
- PaymentVO: the core payment transaction record flowing through the entire pipeline
- DepositToAccount: Kafka message sent to credit a player's account
- VoidToAccount: Kafka message sent to reverse a deposit
- DepositConsumerMessageName: enum of recognised message types on the consumer topic
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class PaymentStatus(str, Enum):
    STARTED = "STARTED"
    PENDING = "PENDING"
    PROCESSING_ON_ACCOUNT = "PROCESSING_ON_ACCOUNT"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    ABANDONED = "ABANDONED"
    VOIDED = "VOIDED"

    def is_terminal(self) -> bool:
        return self in {
            PaymentStatus.SUCCEEDED,
            PaymentStatus.FAILED,
            PaymentStatus.ABANDONED,
            PaymentStatus.VOIDED,
        }


class PaymentProvider(str, Enum):
    ADYEN = "adyen"
    PAYPAL = "paypal"
    BRAINTREE = "braintree"
    TRUSTLY = "trustly"
    TECHMOJO = "techmojo"
    UNKNOWN = "unknown"

    @classmethod
    def get_by_name(cls, name: str) -> "PaymentProvider":
        try:
            return cls(name.lower())
        except ValueError:
            return cls.UNKNOWN


class PaymentProviderInfo(BaseModel):
    provider_id: PaymentProvider = PaymentProvider.UNKNOWN
    external_id: str | None = None
    auth_code: str | None = None
    method: str | None = None
    payment_method: str | None = None
    card_issuing_bank: str | None = None
    recurring_reference: str | None = None
    payment_data: str | None = None
    custom_payment_method: str | None = None
    mapped_payment_method: str | None = None


class FailureInfo(BaseModel):
    failure_type: str | None = None
    failure_reason: str | None = None


class PaymentVO(BaseModel):
    """
    Core payment transaction record.

    - amount is stored in minor currency units (cents) to avoid float issues
    - payment_provider_info is a composed object to separate concerns cleanly
    """
    id: int = 0
    brand_id: int
    user_id: int
    amount: int
    currency: str
    user_ip: str
    status: PaymentStatus = PaymentStatus.STARTED
    bonus_group_id: int | None = None
    confirm_code: str | None = None
    message: str | None = None
    refunded: bool = False
    params: dict[str, Any] | None = None
    mobile: bool = False
    development: bool = False
    date_created: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    date_updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    failure_info: FailureInfo = Field(default_factory=FailureInfo)
    payment_provider_info: PaymentProviderInfo = Field(default_factory=PaymentProviderInfo)


class PaymentMethodVO(BaseModel):
    name: str
    label: str
    description: str = ""
    provider_id: PaymentProvider
    flow: str = "redirect_iframe"


class PaymentMethodOrderVO(BaseModel):
    brand_id: int | None = None
    country: str | None = None
    order_value: str = ""


class UserDetails(BaseModel):
    id: int
    brand_id: int
    currency: str
    country: str
    email: str | None = None


# ---------------------------------------------------------------------------
# Kafka message types
# ---------------------------------------------------------------------------

class DepositConsumerMessageName(str, Enum):
    DEPOSIT_TO_ACCOUNT_FINISHED = "DEPOSIT_TO_ACCOUNT_FINISHED"
    UPDATE_MATRIX_SCORE_FINISHED = "UPDATE_MATRIX_SCORE_FINISHED"
    RECORD_REFUSAL_FINISHED = "RECORD_REFUSAL_FINISHED"
    VOID_TO_ACCOUNT_FINISHED = "VOID_TO_ACCOUNT_FINISHED"


class DepositMessage(BaseModel):
    message_type: str
    content: str


class DepositToAccount(BaseModel):
    """
    Kafka message published to credit a player's account after a successful deposit.

    Decoupling account crediting from PSP callback processing is critical:
    PSPs enforce strict response timeouts (typically 10s); Kafka provides
    at-least-once delivery with retry for the account credit.
    """
    user_id: int
    amount: int
    ref: str
    is_mobile_payment: bool | None = None
    payment_id: int
    provider_id: str
    comment: str | None = None
    payment_bonus_group_id: int | None = None
    payment_method: str
    params: dict[str, str] = Field(default_factory=dict)
    extra_params: dict[str, str] = Field(default_factory=dict)
    auth_code: str | None = None
    message_type: str = "DEPOSIT_TO_ACCOUNT"


class VoidToAccount(BaseModel):
    user_id: int
    amount: int
    payment_id: int
    provider_id: str
    comment: str | None = None
    params: dict[str, str] = Field(default_factory=dict)
    extra_params: dict[str, str] = Field(default_factory=dict)
    message_type: str = "VOID_TO_ACCOUNT"


class DepositToAccountFinished(BaseModel):
    user_id: int
    brand_id: int
    payment_id: int
    params: dict[str, str] = Field(default_factory=dict)


class VoidToAccountFinished(BaseModel):
    user_id: int
    brand_id: int
    payment_id: int
    params: dict[str, str] = Field(default_factory=dict)


class UpdateMatrixScores(BaseModel):
    deposit_details: dict[str, Any] = Field(default_factory=dict)
    user_id: int
    brand_id: int
    message_type: str = "UPDATE_MATRIX_SCORES"


class TopicName(str, Enum):
    DEPOSIT_TO_ACCOUNT = "DEPOSIT_TO_ACCOUNT_TOPIC"
    DEPOSIT_TO_ACCOUNT_FINISHED = "DEPOSIT_TO_ACCOUNT_FINISHED_TOPIC"
    PAYMENT_STATUS_CHANGE = "PAYMENT_STATUS_CHANGE_TOPIC"
    VOID_TO_ACCOUNT = "VOID_TO_ACCOUNT_TOPIC"
