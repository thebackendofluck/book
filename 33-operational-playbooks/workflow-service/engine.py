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
Workflow state machine engine for long-running iGaming review processes.

Handles KYC verification, withdrawal approvals, and dispute resolution.
State transitions are strictly ordered; no step may be skipped without
an explicit SKIP action recorded in the audit log.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from models import (
    ApprovalRequest,
    StepStatus,
    Workflow,
    WorkflowStatus,
    WorkflowStep,
    WorkflowType,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Step templates per workflow type
# ---------------------------------------------------------------------------

_STEP_TEMPLATES: dict[WorkflowType, list[dict[str, Any]]] = {
    WorkflowType.KYC_REVIEW: [
        {"name": "document_collection", "description": "Collect ID and proof of address"},
        {"name": "automated_screening", "description": "Run AML/PEP/sanctions screening"},
        {"name": "manual_review", "description": "Agent reviews documents and screening result"},
        {"name": "decision", "description": "KYC officer approves or rejects"},
    ],
    WorkflowType.WITHDRAWAL_APPROVAL: [
        {"name": "velocity_check", "description": "Verify withdrawal is within allowed limits"},
        {"name": "kyc_status_check", "description": "Confirm player KYC is verified"},
        {"name": "fraud_score_check", "description": "Confirm fraud score is below threshold"},
        {"name": "manual_approval", "description": "Finance agent approves large withdrawal"},
        {"name": "psp_dispatch", "description": "Dispatch payment to PSP"},
    ],
    WorkflowType.DISPUTE_RESOLUTION: [
        {"name": "intake", "description": "Log dispute details and assign reference number"},
        {"name": "evidence_collection", "description": "Gather game logs, transaction records"},
        {"name": "investigation", "description": "Compliance agent investigates claim"},
        {"name": "decision", "description": "Manager reviews investigation and issues ruling"},
        {"name": "notification", "description": "Notify player of outcome"},
    ],
    WorkflowType.BONUS_REVIEW: [
        {"name": "eligibility_check", "description": "Verify player meets bonus criteria"},
        {"name": "wagering_audit", "description": "Audit wagering activity for abuse patterns"},
        {"name": "decision", "description": "CRM agent approves or voids bonus"},
    ],
    WorkflowType.ACCOUNT_SUSPENSION: [
        {"name": "trigger_review", "description": "Document the trigger event"},
        {"name": "evidence_capture", "description": "Snapshot account state and transactions"},
        {"name": "legal_review", "description": "Legal checks jurisdiction-specific obligations"},
        {"name": "suspension", "description": "Apply account restrictions"},
        {"name": "player_notification", "description": "Send regulatory notification to player"},
    ],
}


def _log_event(workflow: Workflow, event: str, actor: str = "system", notes: str = "") -> None:
    workflow.audit_log.append({
        "event": event,
        "actor": actor,
        "notes": notes,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    workflow.updated_at = datetime.now(timezone.utc)


def build_steps(workflow_type: WorkflowType) -> list[WorkflowStep]:
    """Create ordered WorkflowStep objects for a given workflow type."""
    templates = _STEP_TEMPLATES.get(workflow_type, [])
    return [
        WorkflowStep(name=t["name"], description=t["description"], order=i)
        for i, t in enumerate(templates)
    ]


def start_workflow(workflow: Workflow) -> Workflow:
    """
    Transition PENDING → IN_PROGRESS and activate the first step.
    Raises ValueError if the workflow is not in PENDING status.
    """
    if workflow.status != WorkflowStatus.PENDING:
        raise ValueError(
            f"Cannot start workflow in status '{workflow.status}'. Expected PENDING."
        )
    if not workflow.steps:
        workflow.steps = build_steps(WorkflowType(workflow.workflow_type))

    workflow.status = WorkflowStatus.IN_PROGRESS
    if workflow.steps:
        workflow.steps[0].status = StepStatus.IN_PROGRESS
    _log_event(workflow, "workflow_started")
    logger.info("Workflow %s started (%s)", workflow.workflow_id, workflow.workflow_type)
    return workflow


def advance_step(workflow: Workflow, notes: str = "", actor: str = "system") -> Workflow:
    """
    Mark the current step COMPLETED and advance to the next one.

    If the final step completes, the workflow moves to AWAITING_APPROVAL
    so that a human officer can issue the terminal decision.
    """
    if workflow.status not in (WorkflowStatus.IN_PROGRESS,):
        raise ValueError(
            f"Cannot advance step when workflow status is '{workflow.status}'."
        )

    idx = workflow.current_step_index
    if idx >= len(workflow.steps):
        raise ValueError("No current step to advance.")

    step = workflow.steps[idx]
    step.status = StepStatus.COMPLETED
    step.completed_by = actor
    step.completed_at = datetime.now(timezone.utc)
    step.notes = notes

    _log_event(workflow, f"step_completed:{step.name}", actor=actor, notes=notes)

    next_idx = idx + 1
    if next_idx < len(workflow.steps):
        workflow.current_step_index = next_idx
        workflow.steps[next_idx].status = StepStatus.IN_PROGRESS
        _log_event(workflow, f"step_started:{workflow.steps[next_idx].name}")
    else:
        # All automated steps done — await human approval
        workflow.status = WorkflowStatus.AWAITING_APPROVAL
        _log_event(workflow, "awaiting_approval")

    return workflow


def apply_approval(workflow: Workflow, req: ApprovalRequest) -> Workflow:
    """
    Apply an approval or rejection decision.
    APPROVED → COMPLETED, REJECTED stays REJECTED.
    """
    if workflow.status != WorkflowStatus.AWAITING_APPROVAL:
        raise ValueError(
            f"Cannot approve/reject workflow in status '{workflow.status}'."
        )

    if req.approved:
        workflow.status = WorkflowStatus.COMPLETED
        workflow.completed_at = datetime.now(timezone.utc)
        _log_event(workflow, "workflow_approved", actor=req.approver, notes=req.notes)
        logger.info("Workflow %s approved by %s", workflow.workflow_id, req.approver)
    else:
        workflow.status = WorkflowStatus.REJECTED
        workflow.completed_at = datetime.now(timezone.utc)
        _log_event(workflow, "workflow_rejected", actor=req.approver, notes=req.notes)
        logger.info("Workflow %s rejected by %s", workflow.workflow_id, req.approver)

    return workflow


def cancel_workflow(workflow: Workflow, actor: str = "system", reason: str = "") -> Workflow:
    """Cancel a workflow that has not yet reached a terminal state."""
    terminal = {WorkflowStatus.COMPLETED, WorkflowStatus.REJECTED, WorkflowStatus.CANCELLED}
    if WorkflowStatus(workflow.status) in terminal:
        raise ValueError(f"Cannot cancel a workflow in terminal status '{workflow.status}'.")

    workflow.status = WorkflowStatus.CANCELLED
    workflow.completed_at = datetime.now(timezone.utc)
    _log_event(workflow, "workflow_cancelled", actor=actor, notes=reason)
    return workflow
