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
Tests for the generic workflow engine.
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from datetime import datetime, timedelta, timezone

from workflow_engine import (
    WorkflowState,
    StepStatus,
    StepType,
    Priority,
    StepDefinition,
    WorkflowDefinition,
    create_workflow,
    start_workflow,
    advance_step,
    approve_step,
    escalate_step,
    cancel_workflow,
    check_sla_breaches,
    register_workflow,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _register_test_workflow():
    """Ensure a test workflow definition is available."""
    register_workflow(WorkflowDefinition(
        workflow_type="TEST_WORKFLOW",
        description="Test workflow with auto, manual, and approval steps",
        steps=[
            StepDefinition(name="auto_step", step_type=StepType.AUTO, sla_minutes=5),
            StepDefinition(name="manual_step", step_type=StepType.MANUAL, sla_minutes=60),
            StepDefinition(name="approval_step", step_type=StepType.APPROVAL, sla_minutes=30),
        ],
    ))


def _make_workflow(**kwargs):
    return create_workflow("TEST_WORKFLOW", subject_id="player_001", **kwargs)


# ---------------------------------------------------------------------------
# Creation tests
# ---------------------------------------------------------------------------

class TestWorkflowCreation:
    def test_create_workflow(self):
        wf = _make_workflow()
        assert wf.state == WorkflowState.PENDING
        assert wf.workflow_type == "TEST_WORKFLOW"
        assert len(wf.steps) == 3
        assert wf.steps[0].name == "auto_step"
        assert wf.steps[2].step_type == StepType.APPROVAL

    def test_create_with_priority(self):
        wf = _make_workflow(priority=Priority.URGENT)
        assert wf.priority == Priority.URGENT

    def test_create_with_metadata(self):
        wf = _make_workflow(metadata={"alert_id": "ALT-001"})
        assert wf.metadata["alert_id"] == "ALT-001"

    def test_audit_trail_on_create(self):
        wf = _make_workflow()
        assert len(wf.audit_trail) == 1
        assert wf.audit_trail[0]["event"] == "workflow_created"

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown workflow type"):
            create_workflow("DOES_NOT_EXIST", subject_id="x")


# ---------------------------------------------------------------------------
# Start tests
# ---------------------------------------------------------------------------

class TestWorkflowStart:
    def test_start_activates_first_step(self):
        wf = _make_workflow()
        start_workflow(wf)
        assert wf.state == WorkflowState.IN_PROGRESS
        assert wf.steps[0].status == StepStatus.IN_PROGRESS
        assert wf.steps[0].started_at is not None
        assert wf.steps[0].sla_deadline is not None

    def test_start_non_pending_raises(self):
        wf = _make_workflow()
        start_workflow(wf)
        with pytest.raises(ValueError, match="Cannot start"):
            start_workflow(wf)


# ---------------------------------------------------------------------------
# Step advancement tests
# ---------------------------------------------------------------------------

class TestStepAdvancement:
    def test_advance_first_step(self):
        wf = _make_workflow()
        start_workflow(wf)
        advance_step(wf, actor="agent_01")
        assert wf.steps[0].status == StepStatus.COMPLETED
        assert wf.steps[1].status == StepStatus.IN_PROGRESS
        assert wf.current_step_index == 1

    def test_advance_to_approval_step(self):
        wf = _make_workflow()
        start_workflow(wf)
        advance_step(wf, actor="system")   # auto_step
        advance_step(wf, actor="agent_01")  # manual_step
        assert wf.state == WorkflowState.AWAITING_APPROVAL
        assert wf.steps[2].status == StepStatus.IN_PROGRESS

    def test_advance_non_in_progress_raises(self):
        wf = _make_workflow()
        with pytest.raises(ValueError, match="Cannot advance"):
            advance_step(wf)

    def test_advance_records_audit(self):
        wf = _make_workflow()
        start_workflow(wf)
        advance_step(wf, actor="agent_01", notes="Docs verified")
        events = [e["event"] for e in wf.audit_trail]
        assert "step_completed" in events


# ---------------------------------------------------------------------------
# Approval tests
# ---------------------------------------------------------------------------

class TestApproval:
    def _reach_approval(self):
        wf = _make_workflow()
        start_workflow(wf)
        advance_step(wf)
        advance_step(wf)
        return wf

    def test_approve_completes_workflow(self):
        wf = self._reach_approval()
        approve_step(wf, approved=True, approver="manager_01")
        assert wf.state == WorkflowState.COMPLETED
        assert wf.completed_at is not None

    def test_reject_rejects_workflow(self):
        wf = self._reach_approval()
        approve_step(wf, approved=False, approver="manager_01", notes="Insufficient evidence")
        assert wf.state == WorkflowState.REJECTED
        assert wf.steps[2].status == StepStatus.FAILED

    def test_approve_wrong_state_raises(self):
        wf = _make_workflow()
        start_workflow(wf)
        with pytest.raises(ValueError, match="Cannot approve"):
            approve_step(wf, approved=True, approver="x")


# ---------------------------------------------------------------------------
# Escalation tests
# ---------------------------------------------------------------------------

class TestEscalation:
    def test_escalate_in_progress(self):
        wf = _make_workflow()
        start_workflow(wf)
        escalate_step(wf, reason="SLA breach")
        assert wf.state == WorkflowState.ESCALATED
        assert wf.steps[0].status == StepStatus.ESCALATED

    def test_escalate_terminal_raises(self):
        wf = _make_workflow()
        start_workflow(wf)
        cancel_workflow(wf)
        with pytest.raises(ValueError):
            escalate_step(wf)


# ---------------------------------------------------------------------------
# Cancel tests
# ---------------------------------------------------------------------------

class TestCancel:
    def test_cancel_in_progress(self):
        wf = _make_workflow()
        start_workflow(wf)
        cancel_workflow(wf, actor="admin", reason="duplicate")
        assert wf.state == WorkflowState.CANCELLED
        assert wf.completed_at is not None

    def test_cancel_completed_raises(self):
        wf = _make_workflow()
        start_workflow(wf)
        advance_step(wf)
        advance_step(wf)
        approve_step(wf, approved=True, approver="mgr")
        with pytest.raises(ValueError, match="Cannot cancel"):
            cancel_workflow(wf)


# ---------------------------------------------------------------------------
# SLA breach detection
# ---------------------------------------------------------------------------

class TestSLABreaches:
    def test_no_breach_within_sla(self):
        wf = _make_workflow()
        start_workflow(wf)
        breaches = check_sla_breaches(wf)
        assert breaches == []

    def test_breach_detected(self):
        wf = _make_workflow()
        start_workflow(wf)
        # Simulate an overdue step by moving the deadline into the past
        wf.steps[0].sla_deadline = datetime.now(timezone.utc) - timedelta(minutes=10)
        breaches = check_sla_breaches(wf)
        assert len(breaches) == 1
        assert breaches[0]["step_name"] == "auto_step"
        assert breaches[0]["overdue_minutes"] >= 10


# ---------------------------------------------------------------------------
# Built-in definitions
# ---------------------------------------------------------------------------

class TestBuiltinDefinitions:
    def test_aml_review_workflow(self):
        wf = create_workflow("AML_REVIEW", subject_id="player_999")
        assert len(wf.steps) == 4
        assert wf.steps[0].name == "alert_triage"

    def test_player_complaint_workflow(self):
        wf = create_workflow("PLAYER_COMPLAINT", subject_id="player_100")
        assert len(wf.steps) == 5
        assert wf.steps[-1].name == "player_notification"

    def test_rg_intervention_workflow(self):
        wf = create_workflow("RG_INTERVENTION", subject_id="player_200")
        assert len(wf.steps) == 4
        assert wf.steps[2].step_type == StepType.APPROVAL
