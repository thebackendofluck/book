# Companion code for "The Backend of Luck" - Chapter 33, Operational Playbooks.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Workflow Orchestration Service data models.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class WorkflowStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class WorkflowType(str, Enum):
    KYC_REVIEW = "KYC_REVIEW"
    WITHDRAWAL_APPROVAL = "WITHDRAWAL_APPROVAL"
    DISPUTE_RESOLUTION = "DISPUTE_RESOLUTION"
    BONUS_REVIEW = "BONUS_REVIEW"
    ACCOUNT_SUSPENSION = "ACCOUNT_SUSPENSION"


class StepStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"


class WorkflowStep(BaseModel):
    step_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str = ""
    order: int
    status: StepStatus = StepStatus.PENDING
    assignee: str | None = None
    completed_by: str | None = None
    completed_at: datetime | None = None
    notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"use_enum_values": True}


class WorkflowCreateRequest(BaseModel):
    workflow_type: WorkflowType
    subject_id: str                   # e.g., player_id, transaction_id
    subject_type: str                 # e.g., "player", "transaction", "ticket"
    priority: str = "NORMAL"          # LOW, NORMAL, HIGH, URGENT
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_by: str = "system"


class ApprovalRequest(BaseModel):
    approved: bool
    approver: str
    notes: str = ""


class Workflow(BaseModel):
    workflow_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    workflow_type: WorkflowType
    subject_id: str
    subject_type: str
    priority: str = "NORMAL"
    status: WorkflowStatus = WorkflowStatus.PENDING
    steps: list[WorkflowStep] = Field(default_factory=list)
    current_step_index: int = 0
    created_by: str = "system"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    audit_log: list[dict[str, Any]] = Field(default_factory=list)

    model_config = {"use_enum_values": True}
