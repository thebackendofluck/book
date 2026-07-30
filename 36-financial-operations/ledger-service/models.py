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
Ledger Service — Domain Models

Immutable double-entry accounting primitives.
All monetary values are in minor units (cents) to avoid floating-point errors.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, Field, field_validator, model_validator


class Direction(str, Enum):
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


class AccountType(str, Enum):
    PLAYER_WALLET = "PLAYER_WALLET"
    OPERATOR_REVENUE = "OPERATOR_REVENUE"
    PSP_CLEARING = "PSP_CLEARING"
    TAX_LIABILITY = "TAX_LIABILITY"
    BONUS_LIABILITY = "BONUS_LIABILITY"
    BANK_SETTLEMENT = "BANK_SETTLEMENT"
    TREASURY = "TREASURY"
    SUSPENSE = "SUSPENSE"
    FEE_EXPENSE = "FEE_EXPENSE"


class LedgerAccount(BaseModel):
    """An account in the chart of accounts."""

    account_id: str = Field(..., min_length=1)
    account_type: AccountType
    label: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = {"frozen": True}


class EntryRequest(BaseModel):
    """A single leg of a posting request (before persistence)."""

    account_id: str = Field(..., min_length=1)
    amount: Annotated[int, Field(gt=0, description="Amount in minor units (cents)")]
    direction: Direction


class LedgerEntry(BaseModel):
    """An immutable ledger entry (persisted)."""

    entry_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    entry_group_id: uuid.UUID
    account_id: str
    amount: int = Field(gt=0)
    direction: Direction
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, str] = Field(default_factory=dict)

    model_config = {"frozen": True}


class PostingRequest(BaseModel):
    """Request to create a balanced posting (group of entries)."""

    entry_group_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    entries: list[EntryRequest] = Field(..., min_length=2)
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_balance(self) -> PostingRequest:
        total_debits = sum(e.amount for e in self.entries if e.direction == Direction.DEBIT)
        total_credits = sum(e.amount for e in self.entries if e.direction == Direction.CREDIT)
        if total_debits != total_credits:
            raise ValueError(
                f"Posting is unbalanced: debits={total_debits}, credits={total_credits}"
            )
        return self

    @field_validator("entries")
    @classmethod
    def must_have_both_sides(cls, v: list[EntryRequest]) -> list[EntryRequest]:
        directions = {e.direction for e in v}
        if Direction.DEBIT not in directions or Direction.CREDIT not in directions:
            raise ValueError("Posting must have at least one DEBIT and one CREDIT entry")
        return v


class Posting(BaseModel):
    """A persisted, balanced group of ledger entries."""

    entry_group_id: uuid.UUID
    entries: list[LedgerEntry]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, str] = Field(default_factory=dict)


class Balance(BaseModel):
    """Current balance of an account."""

    account_id: str
    balance: int  # net balance in minor units (debits - credits or vice-versa)
    total_debits: int
    total_credits: int
    entry_count: int
    as_of: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class InvariantResult(BaseModel):
    """Result of verifying the double-entry invariant across all postings."""

    is_valid: bool
    total_postings: int
    unbalanced_postings: list[uuid.UUID] = Field(default_factory=list)
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ReconciliationResult(BaseModel):
    """Result of reconciling two sources of truth."""

    is_matched: bool
    source_a_label: str
    source_a_balance: int
    source_b_label: str
    source_b_balance: int
    discrepancy: int = 0
    details: str = ""
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RunResult(BaseModel):
    """Result of a batch reconciliation run."""

    total_checked: int
    matched: int
    mismatched: int
    errors: list[str] = Field(default_factory=list)
    results: list[ReconciliationResult] = Field(default_factory=list)
    run_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AuditTrailEntry(BaseModel):
    """Immutable audit record for every financial action."""

    audit_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    entry_group_id: uuid.UUID
    action: str  # "posting_created", "posting_rejected", "invariant_check", etc.
    actor: str  # "system", "operator:jane@casino.com", "api:deposit-worker"
    reason: str = ""
    metadata: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = {"frozen": True}


class TrialBalance(BaseModel):
    """
    Trial balance report: total debits and credits across all accounts.

    In a correct double-entry system, total_debits == total_credits always.
    Any imbalance indicates data corruption.
    """

    total_debits: int
    total_credits: int
    is_balanced: bool
    account_count: int
    entry_count: int
    imbalance: int = 0
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
