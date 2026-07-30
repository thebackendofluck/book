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
Workflow Orchestration Service — FastAPI application.

Endpoints:
  POST /workflows                   Create and start a workflow
  GET  /workflows/{id}              Get workflow details
  POST /workflows/{id}/advance      Advance to the next step
  POST /workflows/{id}/approve      Issue approval or rejection
  POST /workflows/{id}/cancel       Cancel an in-progress workflow
  GET  /workflows                   List all workflows (with optional filters)
"""
from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, status

from engine import advance_step, apply_approval, cancel_workflow, start_workflow, build_steps
from models import (
    ApprovalRequest,
    Workflow,
    WorkflowCreateRequest,
    WorkflowStatus,
    WorkflowType,
)
from security import Principal, ServiceRole, require_roles

app = FastAPI(
    title="Workflow Orchestration Service",
    description="State-machine engine for long-running iGaming reviews: KYC, withdrawals, disputes.",
    version="1.0.0",
)

# ---------------------------------------------------------------------------
# In-memory store (replace with PostgreSQL in production)
# ---------------------------------------------------------------------------
_workflows: dict[str, Workflow] = {}


def _get_or_404(workflow_id: str) -> Workflow:
    wf = _workflows.get(workflow_id)
    if wf is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow '{workflow_id}' not found.",
        )
    return wf


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.post("/workflows", response_model=Workflow, status_code=status.HTTP_201_CREATED)
def create_workflow(
    req: WorkflowCreateRequest,
    principal: Principal = Depends(require_roles([ServiceRole.SERVICE, ServiceRole.ADMIN])),
) -> Workflow:
    """Create a new workflow and immediately start it."""
    wf = Workflow(
        workflow_type=req.workflow_type,
        subject_id=req.subject_id,
        subject_type=req.subject_type,
        priority=req.priority,
        metadata=req.metadata,
        created_by=principal.sub,
    )
    wf.steps = build_steps(WorkflowType(wf.workflow_type))
    try:
        start_workflow(wf)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    _workflows[wf.workflow_id] = wf
    return wf


@app.get("/workflows", response_model=list[Workflow])
def list_workflows(
    workflow_type: WorkflowType | None = Query(default=None),
    wf_status: WorkflowStatus | None = Query(default=None, alias="status"),
) -> list[Workflow]:
    """List workflows with optional type and status filters."""
    result = list(_workflows.values())
    if workflow_type:
        result = [w for w in result if w.workflow_type == workflow_type]
    if wf_status:
        result = [w for w in result if w.status == wf_status]
    return result


@app.get("/workflows/{workflow_id}", response_model=Workflow)
def get_workflow(workflow_id: str) -> Workflow:
    """Retrieve a single workflow by ID."""
    return _get_or_404(workflow_id)


@app.post("/workflows/{workflow_id}/advance", response_model=Workflow)
def advance_workflow_step(
    workflow_id: str,
    notes: str = "",
    principal: Principal = Depends(require_roles([ServiceRole.SERVICE, ServiceRole.ADMIN])),
) -> Workflow:
    """Mark the current step complete and move to the next one."""
    wf = _get_or_404(workflow_id)
    try:
        advance_step(wf, notes=notes, actor=principal.sub)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return wf


@app.post("/workflows/{workflow_id}/approve", response_model=Workflow)
def approve_workflow(
    workflow_id: str,
    req: ApprovalRequest,
    principal: Principal = Depends(require_roles([ServiceRole.ADMIN])),
) -> Workflow:
    """
    Issue a terminal approval or rejection for a workflow in
    AWAITING_APPROVAL status.
    """
    wf = _get_or_404(workflow_id)
    # The approver is the authenticated principal, never the caller-supplied body field.
    req = req.model_copy(update={"approver": principal.sub})
    try:
        apply_approval(wf, req)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return wf


@app.post("/workflows/{workflow_id}/cancel", response_model=Workflow)
def cancel_workflow_endpoint(
    workflow_id: str,
    reason: str = "",
    principal: Principal = Depends(require_roles([ServiceRole.SERVICE, ServiceRole.ADMIN])),
) -> Workflow:
    """Cancel an active workflow."""
    wf = _get_or_404(workflow_id)
    try:
        cancel_workflow(wf, actor=principal.sub, reason=reason)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return wf


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict[str, Any]:
    statuses = {}
    for wf in _workflows.values():
        statuses[wf.status] = statuses.get(wf.status, 0) + 1
    return {"status": "ok", "workflow_counts": statuses}
