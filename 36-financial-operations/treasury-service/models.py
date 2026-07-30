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
Treasury Service — Domain Models

Manages the operator's financial position across PSP clearing accounts,
bank settlement accounts, and tax reserves.

All monetary values are in minor units (cents) to avoid floating-point errors.
A EUR balance of 100.00 is stored as 10000.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class AccountType(str, Enum):
    """Three categories of treasury account."""

    PSP_CLEARING = "PSP_CLEARING"       # Funds held by a PSP, awaiting settlement
    BANK_SETTLEMENT = "BANK_SETTLEMENT" # Settled funds in operator bank accounts
    TAX_RESERVE = "TAX_RESERVE"         # Ring-fenced tax liability reserves


class SettlementDirection(str, Enum):
    """Direction of a settlement move."""

    INBOUND = "INBOUND"     # PSP pays operator (e.g. net deposits due)
    OUTBOUND = "OUTBOUND"   # Operator pays PSP (e.g. net withdrawals funded)


class SettlementStatus(str, Enum):
    """Lifecycle of a settlement instruction."""

    PENDING = "PENDING"         # Instructed, not yet received/sent
    IN_TRANSIT = "IN_TRANSIT"   # Wire/SWIFT message confirmed sent
    SETTLED = "SETTLED"         # Funds confirmed received
    FAILED = "FAILED"           # Settlement did not complete
    CANCELLED = "CANCELLED"     # Voided before settlement


# ---------------------------------------------------------------------------
# Core models
# ---------------------------------------------------------------------------


class TreasuryAccount(BaseModel):
    """
    A financial account in the operator's treasury chart of accounts.

    Each PSP has one PSP_CLEARING account (the running balance of funds
    the PSP holds on the operator's behalf). The operator also holds one
    or more BANK_SETTLEMENT accounts per currency, and a TAX_RESERVE per
    jurisdiction.
    """

    account_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    account_type: AccountType
    label: str = Field(..., min_length=1, description="Human-readable name, e.g. 'Adyen EUR Clearing'")
    psp_name: Optional[str] = Field(
        default=None,
        description="PSP identifier; only set for PSP_CLEARING accounts",
    )
    currency: str = Field(..., min_length=3, max_length=3)
    balance: int = Field(
        default=0,
        description="Current balance in minor units (can be negative during settlement lag)",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("currency")
    @classmethod
    def currency_uppercase(cls, v: str) -> str:
        return v.upper()

    @model_validator(mode="after")
    def psp_required_for_clearing(self) -> TreasuryAccount:
        if self.account_type == AccountType.PSP_CLEARING and not self.psp_name:
            raise ValueError("psp_name is required for PSP_CLEARING accounts")
        return self


class Settlement(BaseModel):
    """
    A single settlement instruction between the operator and a PSP.

    Settlements collapse the running PSP clearing position into the
    operator's bank account on a scheduled cycle (daily, weekly, etc.).
    """

    settlement_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    psp_name: str = Field(..., min_length=1)
    amount: int = Field(..., gt=0, description="Settlement amount in minor units")
    currency: str = Field(..., min_length=3, max_length=3)
    direction: SettlementDirection
    status: SettlementStatus = SettlementStatus.PENDING
    reference: str = Field(
        ...,
        min_length=1,
        description="Bank reference / PSP settlement batch reference (idempotency key)",
    )
    initiated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    settled_at: Optional[datetime] = None
    failed_at: Optional[datetime] = None
    failure_reason: Optional[str] = None
    notes: str = ""

    @field_validator("currency")
    @classmethod
    def currency_uppercase(cls, v: str) -> str:
        return v.upper()

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            SettlementStatus.SETTLED,
            SettlementStatus.FAILED,
            SettlementStatus.CANCELLED,
        }

    @property
    def age_hours(self) -> float:
        """Hours elapsed since initiation."""
        delta = datetime.now(timezone.utc) - self.initiated_at
        return delta.total_seconds() / 3600


class ClearingPosition(BaseModel):
    """
    Snapshot of what a given PSP owes the operator (or vice-versa).

    A positive `net_position` means the PSP holds more operator funds than
    the last settled balance — a receivable. A negative value means the
    operator has over-drawn from the PSP clearing account.
    """

    psp_name: str
    currency: str
    gross_deposits: int = Field(
        0, description="Total deposits processed via this PSP since last settlement"
    )
    gross_withdrawals: int = Field(
        0, description="Total withdrawals processed via this PSP since last settlement"
    )
    gross_refunds: int = Field(0, description="Total refunds issued since last settlement")
    last_settled_amount: int = Field(0, description="Amount of the most recent settlement")
    net_position: int = Field(
        0,
        description="Amount currently owed to/from the PSP (positive = PSP owes operator)",
    )
    pending_settlement_amount: int = Field(
        0, description="Sum of all PENDING/IN_TRANSIT settlement instructions"
    )
    as_of: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def effective_exposure(self) -> int:
        """Net exposure after accounting for in-flight settlement instructions."""
        return self.net_position - self.pending_settlement_amount


class CashPosition(BaseModel):
    """
    Aggregate cash view across all operator accounts.

    Used by treasury ops to understand total liquid position.
    """

    total_psp_clearing: int = Field(
        0, description="Sum of all PSP clearing balances (receivables)"
    )
    total_bank_settlement: int = Field(
        0, description="Sum of confirmed bank settlement account balances"
    )
    total_tax_reserve: int = Field(
        0, description="Sum of ring-fenced tax reserve accounts"
    )
    currency: str = "EUR"   # Reporting currency (all positions converted)
    positions_by_psp: dict[str, int] = Field(
        default_factory=dict,
        description="Per-PSP clearing balance map",
    )
    as_of: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def net_liquid(self) -> int:
        """
        Liquid cash available to the operator.

        = bank settlement funds  (tax reserves are ring-fenced and excluded)
        """
        return self.total_bank_settlement

    @property
    def total_assets(self) -> int:
        return self.total_psp_clearing + self.total_bank_settlement + self.total_tax_reserve


class FloatRequirement(BaseModel):
    """
    Float calculation: how much liquidity the operator must maintain
    to cover expected withdrawal demand.

    Regulators (NJ DGE, MGA) require that player funds are segregated
    and the operator can prove solvency at any time.
    """

    total_player_liabilities: int = Field(
        0, description="Sum of all player wallet balances (what we owe players)"
    )
    available_liquid: int = Field(
        0, description="Cash available to cover withdrawals (bank + PSP clearing)"
    )
    float_ratio: float = Field(
        0.0, description="available_liquid / total_player_liabilities (must be >= 1.0)"
    )
    surplus_or_deficit: int = Field(
        0, description="Positive = surplus, negative = deficit (CRITICAL)"
    )
    withdrawal_coverage_days: float = Field(
        0.0, description="How many days of avg withdrawals the float covers"
    )
    is_adequate: bool = Field(
        True, description="True if float_ratio >= 1.0 (solvent)"
    )
    as_of: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TreasuryReport(BaseModel):
    """
    Periodic treasury report (daily or weekly).

    Summarises all financial positions, settlements, and alerts
    for the reporting period.
    """

    report_type: str = "daily"  # "daily" or "weekly"
    period_start: datetime
    period_end: datetime
    cash_position: Optional[CashPosition] = None
    clearing_positions: list[ClearingPosition] = Field(default_factory=list)
    float_requirement: Optional[FloatRequirement] = None
    settlements_initiated: int = 0
    settlements_completed: int = 0
    settlements_failed: int = 0
    stuck_settlement_count: int = 0
    total_inbound: int = 0
    total_outbound: int = 0
    net_flow: int = 0
    alerts: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
