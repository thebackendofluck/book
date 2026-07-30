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
Reconciliation Service -- Domain Models

Operator-facing reconciliation with jobs, mismatches, investigation
workflow, and closure approval for audit compliance.

All monetary values are in minor units (cents).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ReconciliationType(str, Enum):
    """What two sources are being compared."""

    LEDGER_VS_WALLET = "LEDGER_VS_WALLET"
    LEDGER_VS_PSP = "LEDGER_VS_PSP"
    LEDGER_VS_BANK = "LEDGER_VS_BANK"
    LEDGER_VS_TAX = "LEDGER_VS_TAX"


class JobStatus(str, Enum):
    """Lifecycle of a reconciliation job."""

    SCHEDULED = "SCHEDULED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class MismatchSeverity(str, Enum):
    """How bad the discrepancy is."""

    INFO = "INFO"            # < 100 minor units -- likely rounding
    WARNING = "WARNING"      # 100-10000 minor units -- investigate
    CRITICAL = "CRITICAL"    # > 10000 minor units or percentage > 1%


class MismatchStatus(str, Enum):
    """Investigation lifecycle."""

    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    RESOLVED = "RESOLVED"
    WAIVED = "WAIVED"        # Approved write-off (requires sign-off)


class ClosureStatus(str, Enum):
    """Reconciliation closure for the period."""

    PENDING = "PENDING"      # Awaiting all jobs to complete
    READY = "READY"          # All jobs done, awaiting sign-off
    APPROVED = "APPROVED"    # Signed off by finance controller
    REJECTED = "REJECTED"    # Sent back for re-investigation


# ---------------------------------------------------------------------------
# Core models
# ---------------------------------------------------------------------------


class ReconciliationJob(BaseModel):
    """A single reconciliation run comparing two data sources."""

    job_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    recon_type: ReconciliationType
    entity_id: str = Field(
        ...,
        description="Player ID, PSP name, bank account, or tax jurisdiction being reconciled",
    )
    status: JobStatus = JobStatus.SCHEDULED
    source_a_label: str = ""
    source_a_balance: int = 0
    source_b_label: str = ""
    source_b_balance: int = 0
    discrepancy: int = 0
    is_matched: bool = False
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Mismatch(BaseModel):
    """
    A detected discrepancy that requires investigation.

    Every mismatch is tracked through an investigation workflow:
    OPEN -> INVESTIGATING -> RESOLVED/WAIVED.
    """

    mismatch_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    job_id: str
    recon_type: ReconciliationType
    entity_id: str
    source_a_label: str
    source_a_balance: int
    source_b_label: str
    source_b_balance: int
    discrepancy: int
    severity: MismatchSeverity
    status: MismatchStatus = MismatchStatus.OPEN
    assigned_to: Optional[str] = None
    investigation_notes: list[str] = Field(default_factory=list)
    resolution: Optional[str] = None
    resolved_by: Optional[str] = None
    resolved_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ReconciliationClosure(BaseModel):
    """
    Period closure record for audit.

    A closure groups all reconciliation jobs for a period (day/week)
    and requires manual sign-off from a finance controller.
    This is a hard requirement for NJ DGE and MGA compliance.
    """

    closure_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    period_label: str = Field(
        ..., description="e.g. '2026-03-23' for daily, '2026-W12' for weekly"
    )
    status: ClosureStatus = ClosureStatus.PENDING
    job_ids: list[str] = Field(default_factory=list)
    total_jobs: int = 0
    total_matched: int = 0
    total_mismatched: int = 0
    open_mismatches: int = 0
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def can_approve(self) -> bool:
        """Closure can only be approved when all mismatches are resolved."""
        return self.open_mismatches == 0 and self.status in {
            ClosureStatus.PENDING,
            ClosureStatus.READY,
        }


class ScheduleConfig(BaseModel):
    """Configuration for the reconciliation scheduler."""

    schedule_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    recon_type: ReconciliationType
    frequency: str = "daily"  # "daily", "hourly", "weekly"
    enabled: bool = True
    last_run_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None
