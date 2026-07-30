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
Tests for the Workflow Orchestration Service.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from main import app, _workflows
from models import WorkflowStatus, WorkflowType


@pytest.fixture(autouse=True)
def clear_store():
    _workflows.clear()
    yield
    _workflows.clear()


@pytest.fixture
def client():
    return TestClient(app)


def _create_kyc_workflow(client, subject_id="player_001"):
    return client.post("/workflows", json={
        "workflow_type": "KYC_REVIEW",
        "subject_id": subject_id,
        "subject_type": "player",
        "created_by": "system",
    })


# ---------------------------------------------------------------------------
# Workflow creation tests
# ---------------------------------------------------------------------------

def test_create_kyc_workflow(client):
    resp = _create_kyc_workflow(client)
    assert resp.status_code == 201
    data = resp.json()
    assert data["workflow_type"] == "KYC_REVIEW"
    assert data["status"] == WorkflowStatus.IN_PROGRESS
    assert len(data["steps"]) == 4
    assert data["steps"][0]["status"] == "IN_PROGRESS"


def test_create_withdrawal_workflow(client):
    resp = client.post("/workflows", json={
        "workflow_type": "WITHDRAWAL_APPROVAL",
        "subject_id": "txn_123",
        "subject_type": "transaction",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["workflow_type"] == "WITHDRAWAL_APPROVAL"
    assert len(data["steps"]) == 5


def test_create_dispute_workflow(client):
    resp = client.post("/workflows", json={
        "workflow_type": "DISPUTE_RESOLUTION",
        "subject_id": "ticket_456",
        "subject_type": "ticket",
    })
    assert resp.status_code == 201
    assert len(resp.json()["steps"]) == 5


# ---------------------------------------------------------------------------
# Get workflow tests
# ---------------------------------------------------------------------------

def test_get_workflow(client):
    wf_id = _create_kyc_workflow(client).json()["workflow_id"]
    resp = client.get(f"/workflows/{wf_id}")
    assert resp.status_code == 200
    assert resp.json()["workflow_id"] == wf_id


def test_get_nonexistent_workflow(client):
    resp = client.get("/workflows/does-not-exist")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Step advancement tests
# ---------------------------------------------------------------------------

def test_advance_step(client):
    wf_id = _create_kyc_workflow(client).json()["workflow_id"]
    resp = client.post(f"/workflows/{wf_id}/advance?actor=agent_01&notes=docs+collected")
    assert resp.status_code == 200
    data = resp.json()
    assert data["steps"][0]["status"] == "COMPLETED"
    assert data["steps"][1]["status"] == "IN_PROGRESS"
    assert data["current_step_index"] == 1


def test_advance_all_steps_reaches_awaiting_approval(client):
    wf_id = _create_kyc_workflow(client).json()["workflow_id"]
    # KYC_REVIEW has 4 steps — advance through all of them
    for _ in range(4):
        resp = client.post(f"/workflows/{wf_id}/advance")
        assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == WorkflowStatus.AWAITING_APPROVAL


def test_advance_rejected_workflow_returns_409(client):
    wf_id = _create_kyc_workflow(client).json()["workflow_id"]
    # Advance to awaiting_approval
    for _ in range(4):
        client.post(f"/workflows/{wf_id}/advance")
    # Reject
    client.post(f"/workflows/{wf_id}/approve", json={"approved": False, "approver": "officer"})
    # Try to advance a terminal workflow
    resp = client.post(f"/workflows/{wf_id}/advance")
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Approval tests
# ---------------------------------------------------------------------------

def test_approve_workflow(client):
    wf_id = _create_kyc_workflow(client).json()["workflow_id"]
    for _ in range(4):
        client.post(f"/workflows/{wf_id}/advance")
    resp = client.post(f"/workflows/{wf_id}/approve", json={
        "approved": True, "approver": "kyc_officer_01", "notes": "All checks passed"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == WorkflowStatus.COMPLETED
    assert data["completed_at"] is not None


def test_reject_workflow(client):
    wf_id = _create_kyc_workflow(client).json()["workflow_id"]
    for _ in range(4):
        client.post(f"/workflows/{wf_id}/advance")
    resp = client.post(f"/workflows/{wf_id}/approve", json={
        "approved": False, "approver": "kyc_officer_02", "notes": "Document mismatch"
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == WorkflowStatus.REJECTED


def test_approve_in_progress_workflow_returns_409(client):
    wf_id = _create_kyc_workflow(client).json()["workflow_id"]
    resp = client.post(f"/workflows/{wf_id}/approve", json={
        "approved": True, "approver": "officer"
    })
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Cancel tests
# ---------------------------------------------------------------------------

def test_cancel_workflow(client):
    wf_id = _create_kyc_workflow(client).json()["workflow_id"]
    resp = client.post(f"/workflows/{wf_id}/cancel?actor=admin&reason=player+request")
    assert resp.status_code == 200
    assert resp.json()["status"] == WorkflowStatus.CANCELLED


def test_cancel_completed_workflow_returns_409(client):
    wf_id = _create_kyc_workflow(client).json()["workflow_id"]
    for _ in range(4):
        client.post(f"/workflows/{wf_id}/advance")
    client.post(f"/workflows/{wf_id}/approve", json={"approved": True, "approver": "officer"})
    resp = client.post(f"/workflows/{wf_id}/cancel")
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# List and filter tests
# ---------------------------------------------------------------------------

def test_list_workflows(client):
    _create_kyc_workflow(client, "p1")
    _create_kyc_workflow(client, "p2")
    resp = client.get("/workflows")
    assert len(resp.json()) == 2


def test_list_workflows_filter_by_type(client):
    _create_kyc_workflow(client)
    client.post("/workflows", json={
        "workflow_type": "WITHDRAWAL_APPROVAL", "subject_id": "txn_1", "subject_type": "transaction"
    })
    resp = client.get("/workflows?workflow_type=KYC_REVIEW")
    assert all(w["workflow_type"] == "KYC_REVIEW" for w in resp.json())


# ---------------------------------------------------------------------------
# Audit log tests
# ---------------------------------------------------------------------------

def test_audit_log_populated(client):
    wf_id = _create_kyc_workflow(client).json()["workflow_id"]
    client.post(f"/workflows/{wf_id}/advance?actor=agent_01")
    data = client.get(f"/workflows/{wf_id}").json()
    events = [entry["event"] for entry in data["audit_log"]]
    assert "workflow_started" in events
    assert any("step_completed" in e for e in events)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
