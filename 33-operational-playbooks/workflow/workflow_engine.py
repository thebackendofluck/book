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
Generic workflow orchestration engine with YAML-configurable state machines.

Supports three step types:
  - auto:     executed by code, transitions immediately on success
  - manual:   requires human review before advancing
  - approval: requires sign-off from an authorised officer

Each step has an SLA.  When a step exceeds its SLA, the engine flags it
for escalation.  Every state transition is recorded in an immutable audit
trail suitable for regulatory inspection.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class StepType(str, Enum):
    AUTO = "auto"
    MANUAL = "manual"
    APPROVAL = "approval"


class StepStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"
    ESCALATED = "ESCALATED"


class WorkflowState(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    ESCALATED = "ESCALATED"


class Priority(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class StepDefinition(BaseModel):
    """YAML-friendly step template."""
    name: str
    description: str = ""
    step_type: StepType = StepType.MANUAL
    sla_minutes: int = 60
    assignee_role: str | None = None


class WorkflowDefinition(BaseModel):
    """A workflow template loaded from YAML config."""
    workflow_type: str
    description: str = ""
    steps: list[StepDefinition]
    default_priority: Priority = Priority.NORMAL


class AuditEntry(BaseModel):
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    event: str
    actor: str = "system"
    step_name: str = ""
    notes: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowStep(BaseModel):
    step_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str = ""
    step_type: StepType = StepType.MANUAL
    order: int = 0
    status: StepStatus = StepStatus.PENDING
    sla_minutes: int = 60
    sla_deadline: datetime | None = None
    assignee: str | None = None
    assignee_role: str | None = None
    completed_by: str | None = None
    completed_at: datetime | None = None
    started_at: datetime | None = None
    notes: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"use_enum_values": True}


class WorkflowInstance(BaseModel):
    workflow_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    workflow_type: str
    subject_id: str
    subject_type: str = ""
    priority: Priority = Priority.NORMAL
    state: WorkflowState = WorkflowState.PENDING
    steps: list[WorkflowStep] = Field(default_factory=list)
    current_step_index: int = 0
    created_by: str = "system"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    audit_trail: list[dict[str, Any]] = Field(default_factory=list)

    model_config = {"use_enum_values": True}


# ---------------------------------------------------------------------------
# Workflow registry: type -> definition
# ---------------------------------------------------------------------------

_DEFINITIONS: dict[str, WorkflowDefinition] = {}


def register_workflow(definition: WorkflowDefinition) -> None:
    """Register a workflow definition (normally loaded from YAML)."""
    _DEFINITIONS[definition.workflow_type] = definition
    logger.info("Registered workflow definition: %s", definition.workflow_type)


def get_definition(workflow_type: str) -> WorkflowDefinition:
    defn = _DEFINITIONS.get(workflow_type)
    if defn is None:
        raise ValueError(f"Unknown workflow type: {workflow_type}")
    return defn


# ---------------------------------------------------------------------------
# Built-in iGaming workflow definitions
# ---------------------------------------------------------------------------

_BUILTIN_DEFINITIONS = [
    WorkflowDefinition(
        workflow_type="AML_REVIEW",
        description="Anti-money-laundering investigation triggered by alert",
        steps=[
            StepDefinition(name="alert_triage", description="Classify and prioritise the alert", step_type=StepType.AUTO, sla_minutes=5),
            StepDefinition(name="transaction_analysis", description="Review transaction patterns", step_type=StepType.MANUAL, sla_minutes=120, assignee_role="aml_analyst"),
            StepDefinition(name="source_of_funds", description="Verify source of funds documentation", step_type=StepType.MANUAL, sla_minutes=240, assignee_role="aml_analyst"),
            StepDefinition(name="sar_decision", description="File SAR or dismiss", step_type=StepType.APPROVAL, sla_minutes=60, assignee_role="mlro"),
        ],
    ),
    WorkflowDefinition(
        workflow_type="PLAYER_COMPLAINT",
        description="Player complaint handling per ADR requirements",
        steps=[
            StepDefinition(name="intake", description="Log complaint and acknowledge player", step_type=StepType.AUTO, sla_minutes=15),
            StepDefinition(name="investigation", description="Investigate root cause", step_type=StepType.MANUAL, sla_minutes=480, assignee_role="support_senior"),
            StepDefinition(name="response_draft", description="Draft response for player", step_type=StepType.MANUAL, sla_minutes=120, assignee_role="support_senior"),
            StepDefinition(name="manager_review", description="Manager signs off on response", step_type=StepType.APPROVAL, sla_minutes=60, assignee_role="support_manager"),
            StepDefinition(name="player_notification", description="Send resolution to player", step_type=StepType.AUTO, sla_minutes=10),
        ],
    ),
    WorkflowDefinition(
        workflow_type="RG_INTERVENTION",
        description="Responsible-gaming intervention workflow",
        steps=[
            StepDefinition(name="marker_detection", description="RG system flags markers", step_type=StepType.AUTO, sla_minutes=1),
            StepDefinition(name="risk_assessment", description="Assess player risk level", step_type=StepType.MANUAL, sla_minutes=30, assignee_role="rg_specialist"),
            StepDefinition(name="intervention_decision", description="Decide on limits, cool-off, or exclusion", step_type=StepType.APPROVAL, sla_minutes=60, assignee_role="rg_manager"),
            StepDefinition(name="player_contact", description="Contact player with support info", step_type=StepType.MANUAL, sla_minutes=120, assignee_role="rg_specialist"),
        ],
    ),
]

for _d in _BUILTIN_DEFINITIONS:
    register_workflow(_d)


# ---------------------------------------------------------------------------
# Audit helper
# ---------------------------------------------------------------------------

def _audit(wf: WorkflowInstance, event: str, *, actor: str = "system",
           step_name: str = "", notes: str = "", **extra: Any) -> None:
    entry = AuditEntry(event=event, actor=actor, step_name=step_name, notes=notes, metadata=extra)
    wf.audit_trail.append(entry.model_dump())
    wf.updated_at = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Engine operations
# ---------------------------------------------------------------------------

def create_workflow(workflow_type: str, subject_id: str, *,
                    subject_type: str = "", priority: Priority = Priority.NORMAL,
                    created_by: str = "system",
                    metadata: dict[str, Any] | None = None) -> WorkflowInstance:
    """Instantiate a workflow from a registered definition."""
    defn = get_definition(workflow_type)
    steps = [
        WorkflowStep(
            name=sd.name,
            description=sd.description,
            step_type=sd.step_type,
            sla_minutes=sd.sla_minutes,
            assignee_role=sd.assignee_role,
            order=i,
        )
        for i, sd in enumerate(defn.steps)
    ]
    wf = WorkflowInstance(
        workflow_type=workflow_type,
        subject_id=subject_id,
        subject_type=subject_type,
        priority=priority,
        steps=steps,
        created_by=created_by,
        metadata=metadata or {},
    )
    _audit(wf, "workflow_created", actor=created_by)
    return wf


def start_workflow(wf: WorkflowInstance) -> WorkflowInstance:
    """Transition PENDING -> IN_PROGRESS and activate the first step."""
    if wf.state != WorkflowState.PENDING:
        raise ValueError(f"Cannot start workflow in state '{wf.state}'")
    wf.state = WorkflowState.IN_PROGRESS
    if wf.steps:
        step = wf.steps[0]
        step.status = StepStatus.IN_PROGRESS
        step.started_at = datetime.now(timezone.utc)
        step.sla_deadline = datetime.now(timezone.utc) + timedelta(minutes=step.sla_minutes)
    _audit(wf, "workflow_started")
    return wf


def advance_step(wf: WorkflowInstance, *, actor: str = "system", notes: str = "") -> WorkflowInstance:
    """Complete the current step and move to the next."""
    if wf.state != WorkflowState.IN_PROGRESS:
        raise ValueError(f"Cannot advance step in state '{wf.state}'")
    idx = wf.current_step_index
    if idx >= len(wf.steps):
        raise ValueError("No current step to advance")

    step = wf.steps[idx]
    step.status = StepStatus.COMPLETED
    step.completed_by = actor
    step.completed_at = datetime.now(timezone.utc)
    step.notes = notes
    _audit(wf, "step_completed", actor=actor, step_name=step.name, notes=notes)

    next_idx = idx + 1
    if next_idx < len(wf.steps):
        wf.current_step_index = next_idx
        next_step = wf.steps[next_idx]
        next_step.status = StepStatus.IN_PROGRESS
        next_step.started_at = datetime.now(timezone.utc)
        next_step.sla_deadline = datetime.now(timezone.utc) + timedelta(minutes=next_step.sla_minutes)
        _audit(wf, "step_started", step_name=next_step.name)
        if next_step.step_type == StepType.APPROVAL:
            wf.state = WorkflowState.AWAITING_APPROVAL
            _audit(wf, "awaiting_approval", step_name=next_step.name)
    else:
        wf.state = WorkflowState.COMPLETED
        wf.completed_at = datetime.now(timezone.utc)
        _audit(wf, "workflow_completed")

    return wf


def approve_step(wf: WorkflowInstance, *, approved: bool, approver: str,
                 notes: str = "") -> WorkflowInstance:
    """Approve or reject an approval step."""
    if wf.state != WorkflowState.AWAITING_APPROVAL:
        raise ValueError(f"Cannot approve/reject in state '{wf.state}'")
    idx = wf.current_step_index
    step = wf.steps[idx]
    if step.step_type != StepType.APPROVAL:
        raise ValueError(f"Step '{step.name}' is not an approval step")

    if approved:
        step.status = StepStatus.COMPLETED
        step.completed_by = approver
        step.completed_at = datetime.now(timezone.utc)
        step.notes = notes
        _audit(wf, "step_approved", actor=approver, step_name=step.name, notes=notes)
        # Move to next step or complete
        next_idx = idx + 1
        if next_idx < len(wf.steps):
            wf.current_step_index = next_idx
            wf.state = WorkflowState.IN_PROGRESS
            next_step = wf.steps[next_idx]
            next_step.status = StepStatus.IN_PROGRESS
            next_step.started_at = datetime.now(timezone.utc)
            next_step.sla_deadline = datetime.now(timezone.utc) + timedelta(minutes=next_step.sla_minutes)
            _audit(wf, "step_started", step_name=next_step.name)
        else:
            wf.state = WorkflowState.COMPLETED
            wf.completed_at = datetime.now(timezone.utc)
            _audit(wf, "workflow_completed")
    else:
        step.status = StepStatus.FAILED
        step.completed_by = approver
        step.completed_at = datetime.now(timezone.utc)
        step.notes = notes
        wf.state = WorkflowState.REJECTED
        wf.completed_at = datetime.now(timezone.utc)
        _audit(wf, "step_rejected", actor=approver, step_name=step.name, notes=notes)

    return wf


def escalate_step(wf: WorkflowInstance, *, reason: str = "SLA breach",
                  actor: str = "system") -> WorkflowInstance:
    """Escalate the current step -- used when SLA is breached."""
    if wf.state not in (WorkflowState.IN_PROGRESS, WorkflowState.AWAITING_APPROVAL):
        raise ValueError(f"Cannot escalate in state '{wf.state}'")
    idx = wf.current_step_index
    step = wf.steps[idx]
    step.status = StepStatus.ESCALATED
    wf.state = WorkflowState.ESCALATED
    _audit(wf, "step_escalated", actor=actor, step_name=step.name, notes=reason)
    return wf


def cancel_workflow(wf: WorkflowInstance, *, actor: str = "system",
                    reason: str = "") -> WorkflowInstance:
    """Cancel a non-terminal workflow."""
    terminal = {WorkflowState.COMPLETED, WorkflowState.REJECTED, WorkflowState.CANCELLED}
    if wf.state in terminal:
        raise ValueError(f"Cannot cancel workflow in terminal state '{wf.state}'")
    wf.state = WorkflowState.CANCELLED
    wf.completed_at = datetime.now(timezone.utc)
    _audit(wf, "workflow_cancelled", actor=actor, notes=reason)
    return wf


def check_sla_breaches(wf: WorkflowInstance) -> list[dict[str, Any]]:
    """Return list of steps that have breached their SLA."""
    breaches: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    for step in wf.steps:
        if step.status in (StepStatus.IN_PROGRESS, StepStatus.ESCALATED):
            if step.sla_deadline and now > step.sla_deadline:
                overdue = now - step.sla_deadline
                breaches.append({
                    "step_name": step.name,
                    "sla_minutes": step.sla_minutes,
                    "overdue_minutes": int(overdue.total_seconds() / 60),
                    "deadline": step.sla_deadline.isoformat(),
                })
    return breaches
