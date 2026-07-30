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
Domain models for the payments service.

Amount is stored in minor currency units (cents/pence) as int to avoid
float precision issues -- a £10 deposit is stored as 1000.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Payment status lifecycle
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Payment provider enum
# ---------------------------------------------------------------------------

class PaymentProvider(str, Enum):
    ADYEN = "adyen"
    PAYPAL = "paypal"
    BRAINTREE = "braintree"
    TRUSTLY = "trustly"
    INTERAC = "interac"
    HEXOPAY = "hexopay"
    EPG = "epg"
    PAYMENTIQ = "paymentiq"
    ONLINEIPS = "onlineips"
    PXP = "pxp"
    UPAYSAFE = "upaysafe"
    PAYSAFECARD = "paysafecard"
    OFFLINE = "offline"
    UNKNOWN = "unknown"

    @classmethod
    def get_by_name(cls, name: str) -> "PaymentProvider":
        try:
            return cls(name.lower())
        except ValueError:
            return cls.UNKNOWN


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------

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
    id: int = 0
    brand_id: int
    user_id: int
    amount: int  # minor currency units
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


# ---------------------------------------------------------------------------
# Request / response shapes
# ---------------------------------------------------------------------------

class DepositRequest(BaseModel):
    id: int
    brand_id: int
    user_id: int
    amount: int
    currency: str
    ip_address: str
    method: str
    recurring_detail_reference: str | None = None


class DepositResult(BaseModel):
    status: str
    payment_id: int
    redirect_url: str
    redirect_method: str
    redirect_type: str | None = None
    params: dict[str, str] = Field(default_factory=dict)


class UserDetails(BaseModel):
    id: int
    brand_id: int
    currency: str
    country: str
    email: str | None = None


class PaymentMethodVO(BaseModel):
    name: str
    label: str
    description: str = ""
    provider_id: PaymentProvider
    flow: str = "redirect_iframe"  # redirect_iframe | redirect_full_page | custom | direct


class Redirection(BaseModel):
    url: str
    params: dict[str, str] = Field(default_factory=dict)
    post: bool = False
    payment_id: int | None = None


# ---------------------------------------------------------------------------
# Kafka message types
# ---------------------------------------------------------------------------

class DepositToAccount(BaseModel):
    user_id: int
    amount: int
    payment_id: int
    provider_id: str
    payment_method: str
    ref: str
    is_mobile_payment: bool | None = None
    auth_code: str | None = None
    message_type: str = "DEPOSIT_TO_ACCOUNT"


class PaymentStatusChangeMessage(BaseModel):
    user_id: int
    brand_id: int
    amount: int
    status: str
    payment_id: int
    currency: str
    message_type: str = "PAYMENT_STATUS_CHANGE"


class DepositMessage(BaseModel):
    message_type: str
    content: str


class TopicName(str, Enum):
    DEPOSIT_TO_ACCOUNT = "DEPOSIT_TO_ACCOUNT_TOPIC"
    DEPOSIT_TO_ACCOUNT_FINISHED = "DEPOSIT_TO_ACCOUNT_FINISHED_TOPIC"
    PAYMENT_STATUS_CHANGE = "PAYMENT_STATUS_CHANGE_TOPIC"
    WITHDRAWAL = "WITHDRAWAL_TOPIC"
    WITHDRAWAL_FINISHED = "WITHDRAWAL_FINISHED_TOPIC"
