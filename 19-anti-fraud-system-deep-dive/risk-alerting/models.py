# Companion code for "The Backend of Luck" - Chapter 19, Anti-Fraud System Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
models.py – Data models for the risk-alerting service.

Covers alert types, severity/priority levels, payment events, and the
canonical alert payload that is forwarded to the notification layer.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class AlertPriority(str, Enum):
    P1 = "P1"  # Critical
    P2 = "P2"  # High
    P3 = "P3"  # Medium
    P4 = "P4"  # Low
    P5 = "P5"  # Informational (default)


class AlertStatus(str, Enum):
    NEW = "NEW"
    PENDING = "PENDING"
    RESOLVED = "RESOLVED"


class PaymentStatus(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    PENDING = "PENDING"
    DECLINED = "DECLINED"


class AlertName(str, Enum):
    """Canonical names that match the Kafka stream keys used in the engine."""

    HIGH_DEPOSITOR = "HighDepositor"
    DEPOSIT_METHODS_ABUSE_1H = "DepositMethodsAbuseWithin1Hour"
    FIVE_UNIQUE_INSTRUMENTS_20MIN = "5UniqueInstrumentsIn20MinutesDeclined"
    MULTIPLE_UNIQUE_CARDS = "MultipleUniqueCardsUsed"
    THREE_OR_MORE_UNIQUE_CARDS = "ThreeOrMoreUniqueCards"
    TOTAL_AMOUNT_DEPOSITS_24H = "TotalAmountOfDepositsIn24Hours"
    TOTAL_DEPOSITS_3_DAYS = "StructuringDDepositLimitIn3Days"
    TOTAL_WITHDRAWAL_9000_72H = "TotalWithdrawalExceeded9000In72HoursAlert"
    DECLINED_20_DEPOSITS_24H = "Last20DepositsIn24hoursDeclined"
    DECLINED_20_DEPOSITS_7D = "Last20DepositsIn7daysDeclined"
    LAST_5_DEPOSITS_DECLINED = "Last5DepositsDeclined"
    LAST_3_CARD_DEPOSITS_DECLINED = "Last3CardDepositsDeclined"
    SHARED_PAYMENT_METHODS = "SharedPaymentMethodsByTwoUsers"
    SUCCESSFUL_5_DEPOSITS_ONE_DAY = "Successful5DepositsOneGamingDay"
    TWO_SUCCESSFUL_DEPOSITS_10MIN = "TwoSuccessfulDepositsIn10Minutes"
    PAYMENT_PROVIDER_ERROR = "PaymentProviderError"
    DELETING_PAYMENT_ACCOUNTS_WEEK = "DeletingPaymentAccountsPerWeek"
    MULTIPLE_BANK_ACCOUNTS = "MultipleBankAccountsAdded"
    SHARED_INSTRUMENT_WITHDRAWAL = "SharedPaymentInstrumentWithdrawal"

    # SIGAP (Brazil regulatory) alert
    SIGAP_HIGH_RISK = "SIGAPHighRisk"


# ---------------------------------------------------------------------------
# Payment event models (consumed from Kafka)
# ---------------------------------------------------------------------------


class PaymentStatusChangeEvent(BaseModel):
    """Mirrors the Scala PaymentStatusChangeMessage consumed from Kafka."""

    message_type: str = "PAYMENT_STATUS_CHANGE"
    user_id: int
    payment_id: int
    amount: int  # amount in cents
    currency: str
    status: PaymentStatus
    payment_method: Optional[str] = None
    recurring_reference: Optional[str] = None
    payment_instrument_id: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class DepositEvent(BaseModel):
    """Envelope for a deposit message, matching the Scala DepositMessage wrapper."""

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    content: PaymentStatusChangeEvent


class WithdrawalEvent(BaseModel):
    """Mirrors the Scala WithdrawalMessage."""

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: int
    amount: int  # amount in cents
    currency: str
    status: str  # "ACCEPTED" | "PENDING" | "DECLINED"
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Alert description (configurable per alert name)
# ---------------------------------------------------------------------------


class AlertDescription(BaseModel):
    """Stores configurable metadata for each alert rule."""

    alert_name: str
    title: str
    description: str
    priority: AlertPriority = AlertPriority.P3
    enabled: bool = True
    threshold: Optional[int] = None  # e.g. deposit count
    window_minutes: Optional[int] = None  # sliding window size


# ---------------------------------------------------------------------------
# Core alert payload (forwarded to notification layer)
# ---------------------------------------------------------------------------


class RiskAlert(BaseModel):
    """
    Canonical alert payload produced by the alert engine.

    Mirrors the OpsgenieAlert structure from the original Scala service but
    decoupled from any specific notification provider.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    message: str
    alert_name: str
    alias: Optional[str] = None
    description: Optional[str] = None
    details: Dict[str, str] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)
    priority: Optional[AlertPriority] = None
    user_ids: List[str] = Field(default_factory=list)
    source: Optional[str] = "risk-alerting"
    status: AlertStatus = AlertStatus.NEW
    agent_id: Optional[str] = None
    comment: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Stored alert record (persisted to DB)
# ---------------------------------------------------------------------------


class StoredAlert(BaseModel):
    """Alert record as stored in the database (includes DB-assigned id)."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    alert_name: str
    message: str
    details: Dict[str, Any] = Field(default_factory=dict)
    user_id: str
    priority: str = AlertPriority.P5
    status: AlertStatus = AlertStatus.NEW
    agent_id: Optional[str] = None
    comment: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Request / response models for the HTTP API
# ---------------------------------------------------------------------------


class UpdateAlertRequest(BaseModel):
    status: Optional[AlertStatus] = None
    agent_id: Optional[str] = None
    comment: Optional[str] = None


class UpdateAlertDescriptionRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[AlertPriority] = None
    enabled: Optional[bool] = None
    threshold: Optional[int] = None
    window_minutes: Optional[int] = None


class AlertListResponse(BaseModel):
    alerts: List[StoredAlert]
    total: int


class SIGAPReport(BaseModel):
    """Brazil-specific SIGAP regulatory report payload."""

    report_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    alert_name: str
    amount_cents: int
    currency: str
    jurisdiction: str = "BR"
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    details: Dict[str, str] = Field(default_factory=dict)
