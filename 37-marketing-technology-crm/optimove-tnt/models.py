# Companion code for "The Backend of Luck" - Chapter 37, Marketing Technology and CRM Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Domain models for the Optimove TnT integration.

Optimove TnT (Track and Trigger) is a real-time marketing automation platform.
This service consumes player lifecycle events from Kafka and forwards them to
Optimove's API to trigger personalised marketing campaigns.

Event hierarchy:
  DomainEvent
    UserEvent      -- registration, login, deposit, activation, marketing prefs
    TransactionEvent -- cash wins/losses, withdrawals, bonus events

All events carry userId and brandId to support multi-brand configurations
where each brand/licensee has its own Optimove tenant and API credentials.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Type aliases / value types
# ---------------------------------------------------------------------------

UserId = int
BrandId = int


class Currency(str, Enum):
    SEK = "SEK"
    NOK = "NOK"
    AUD = "AUD"
    INR = "INR"
    GBP = "GBP"
    ARS = "ARS"
    CHF = "CHF"
    CAD = "CAD"
    PEN = "PEN"
    BRL = "BRL"
    EUR = "EUR"
    NZD = "NZD"
    ZAR = "ZAR"
    USD = "USD"
    CLP = "CLP"


Money = int  # minor currency units
BonusPoint = int
Country = str


class MarketingPreferencesInfo(BaseModel):
    email_opt_in: bool = False
    sms_opt_in: bool = False
    push_opt_in: bool = False


# ---------------------------------------------------------------------------
# Base domain event
# ---------------------------------------------------------------------------

class DomainEvent(BaseModel):
    """Base class for all domain events forwarded to Optimove."""
    user_id: UserId
    brand_id: BrandId
    licensee_name: str | None = None
    brand_name: str
    timestamp: datetime
    country: Country
    language: str
    excluded_from_marketing: bool | None = None


# ---------------------------------------------------------------------------
# User lifecycle events
# ---------------------------------------------------------------------------

class UserRegistration(DomainEvent):
    bonus_balance: BonusPoint


class UserLogin(DomainEvent):
    bonus_balance: BonusPoint


class UserDeposited(DomainEvent):
    cash_balance: Money
    bonus_balance: BonusPoint
    amount: Money
    deposit_number: int


class DepositFailed(DomainEvent):
    cash_balance: Money
    bonus_balance: BonusPoint
    amount: Money
    payment_method: str
    reason: str


class UserActivation(DomainEvent):
    bonus_balance: BonusPoint


class MarketingPreferencesUpdated(DomainEvent):
    bonus_balance: BonusPoint
    marketing_preferences: MarketingPreferencesInfo


# ---------------------------------------------------------------------------
# Transaction events
# ---------------------------------------------------------------------------

class CashWin(DomainEvent):
    amount: Money
    cash_balance: Money


class BonusWin(DomainEvent):
    currency: Currency
    amount: BonusPoint
    bonus_balance: BonusPoint


class Withdraw(DomainEvent):
    amount: Money
    cash_balance: Money


class WithdrawAccepted(DomainEvent):
    amount: Money
    cash_balance: Money


class WithdrawReversed(DomainEvent):
    amount: Money
    cash_balance: Money


class CashDebit(DomainEvent):
    amount: Money
    cash_balance: Money


class ReleasedBonus(DomainEvent):
    currency: Currency
    amount: Money
    cash_balance: Money


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class KafkaConsumerConfig(BaseModel):
    topic: str
    error_topic: str
    commit_offset_max_batch: int = 100
    commit_offset_max_interval_seconds: int = 5
    concurrent: int = 1


class KafkaConfig(BaseModel):
    allow_lag: int = 10000
    bootstrap: str
    consumer_users: KafkaConsumerConfig
    consumer_transactions: KafkaConsumerConfig


class HttpConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080


class OpsGenieConfig(BaseModel):
    api_key: str
    enabled: bool = False


class OptimoveLicenseeSetting(BaseModel):
    licensee_name: str
    url: str
    tenant: int
    currency: Currency


class AppConfig(BaseModel):
    http: HttpConfig = Field(default_factory=HttpConfig)
    kafka: KafkaConfig
    optimove: list[OptimoveLicenseeSetting] = Field(default_factory=list)
    om_include_filter: list[UserId] = Field(default_factory=list)
    environment: str = "production"
    ops_genie: OpsGenieConfig = Field(
        default_factory=lambda: OpsGenieConfig(api_key="", enabled=False)
    )
    process_error_topics: bool = True


# ---------------------------------------------------------------------------
# Optimove API DTOs
# ---------------------------------------------------------------------------

class OptimoveEvent(BaseModel):
    """Generic event structure sent to the Optimove TnT API."""
    customer_id: str
    timestamp: str
    event_type_name: str
    params: dict[str, Any] = Field(default_factory=dict)


class EventProcessedResult(str, Enum):
    SENT = "sent"
    IGNORED = "ignored"
    LICENSEE_NOT_CONFIGURED = "licensee_not_configured"
    FAILURE = "failure"
