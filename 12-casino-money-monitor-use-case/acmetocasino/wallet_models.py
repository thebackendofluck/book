# Companion code for "The Backend of Luck" - Chapter 12, Real-Time Cash Flow Management for Online Casinos.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Pydantic models for the event-sourced Wallet module.
"""

import datetime
import uuid
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field


class WalletEventType(str, Enum):
    BET = "BET"
    WIN = "WIN"
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    BONUS_CREDIT = "BONUS_CREDIT"
    BONUS_DEBIT = "BONUS_DEBIT"


class TransactionRequest(BaseModel):
    event_type: WalletEventType
    amount: Decimal = Field(gt=0, max_digits=15, decimal_places=2)
    currency: str = Field(default="USD", max_length=3)
    reference_id: uuid.UUID | None = None
    metadata: dict | None = None


class WalletEvent(BaseModel):
    id: int
    player_id: uuid.UUID
    event_type: WalletEventType
    amount: Decimal
    currency: str
    reference_id: uuid.UUID | None
    metadata: dict | None
    created_at: datetime.datetime


class Balance(BaseModel):
    player_id: uuid.UUID
    balance: Decimal
    currency: str = "USD"
    event_count: int
