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
Tests for the case management service.
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from datetime import datetime, timedelta, timezone

from case_manager import (
    Case,
    CaseStatus,
    CasePriority,
    CaseType,
    Resolution,
    create_case,
    assign_case,
    add_note,
    add_evidence,
    escalate_case,
    resolve_case,
    close_case,
    check_sla_status,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_case(**kwargs) -> Case:
    defaults = {
        "case_type": CaseType.AML_ALERT,
        "subject_id": "player_001",
        "title": "Suspicious deposit pattern",
    }
    defaults.update(kwargs)
    return create_case(**defaults)


# ---------------------------------------------------------------------------
# Creation tests
# ---------------------------------------------------------------------------

class TestCaseCreation:
    def test_create_basic_case(self):
        case = _make_case()
        assert case.status == CaseStatus.OPEN
        assert case.case_type == CaseType.AML_ALERT
        assert case.case_id.startswith("CASE-")

    def test_sla_computed_from_type_and_priority(self):
        case = _make_case(priority=CasePriority.CRITICAL)
        assert case.sla_hours == 4  # AML_ALERT + CRITICAL = 4h

    def test_sla_default_for_unknown_combo(self):
        case = _make_case(case_type=CaseType.TECHNICAL_ERROR)
        assert case.sla_hours == 24  # default

    def test_sla_deadline_set(self):
        case = _make_case()
        assert case.sla_deadline is not None
        assert case.sla_deadline > datetime.now(timezone.utc)

    def test_audit_trail_on_create(self):
        case = _make_case()
        assert len(case.audit_trail) == 1
        assert case.audit_trail[0]["event"] == "case_created"

    def test_rg_critical_sla_is_1_hour(self):
        case = _make_case(
            case_type=CaseType.RG_FLAG,
            priority=CasePriority.CRITICAL,
            title="Problem gambling markers detected",
        )
        assert case.sla_hours == 1

    def test_self_exclusion_breach_critical_sla(self):
        case = _make_case(
            case_type=CaseType.SELF_EXCLUSION_BREACH,
            priority=CasePriority.CRITICAL,
            title="Self-excluded player logged in",
        )
        assert case.sla_hours == 1


# ---------------------------------------------------------------------------
# Assignment tests
# ---------------------------------------------------------------------------

class TestAssignment:
    def test_assign_transitions_to_in_progress(self):
        case = _make_case()
        assign_case(case, "analyst_01", team="AML")
        assert case.status == CaseStatus.IN_PROGRESS
        assert case.assigned_to == "analyst_01"
        assert case.assigned_team == "AML"

    def test_reassign(self):
        case = _make_case()
        assign_case(case, "analyst_01")
        assign_case(case, "analyst_02")
        assert case.assigned_to == "analyst_02"

    def test_assign_closed_case_raises(self):
        case = _make_case()
        assign_case(case, "analyst_01")
        resolve_case(case, Resolution.DISMISSED, resolved_by="analyst_01")
        close_case(case)
        with pytest.raises(ValueError, match="Cannot assign"):
            assign_case(case, "analyst_03")


# ---------------------------------------------------------------------------
# Notes and evidence
# ---------------------------------------------------------------------------

class TestNotesAndEvidence:
    def test_add_internal_note(self):
        case = _make_case()
        add_note(case, "analyst_01", "Deposits from 3 different cards in 1 hour")
        assert len(case.notes) == 1
        assert case.notes[0].is_internal is True

    def test_add_external_note(self):
        case = _make_case()
        add_note(case, "analyst_01", "We are reviewing your account", is_internal=False)
        assert case.notes[0].is_internal is False

    def test_add_evidence(self):
        case = _make_case()
        add_evidence(case, "transaction", "TXN-12345", description="Suspicious deposit")
        assert len(case.evidence) == 1
        assert case.evidence[0].evidence_type == "transaction"
        assert case.evidence[0].reference == "TXN-12345"

    def test_multiple_evidence_items(self):
        case = _make_case()
        add_evidence(case, "game_log", "GL-001")
        add_evidence(case, "session_data", "SESS-002")
        add_evidence(case, "document", "DOC-003")
        assert len(case.evidence) == 3


# ---------------------------------------------------------------------------
# Escalation
# ---------------------------------------------------------------------------

class TestEscalation:
    def test_escalate_case(self):
        case = _make_case()
        assign_case(case, "analyst_01")
        escalate_case(case, reason="Needs MLRO review", actor="analyst_01")
        assert case.status == CaseStatus.ESCALATED

    def test_escalate_closed_raises(self):
        case = _make_case()
        assign_case(case, "analyst_01")
        resolve_case(case, Resolution.DISMISSED, resolved_by="analyst_01")
        close_case(case)
        with pytest.raises(ValueError, match="Cannot escalate"):
            escalate_case(case)


# ---------------------------------------------------------------------------
# Resolution and closure
# ---------------------------------------------------------------------------

class TestResolution:
    def test_resolve_case(self):
        case = _make_case()
        assign_case(case, "analyst_01")
        resolve_case(case, Resolution.SAR_FILED, notes="SAR submitted ref SAR-2024-0001", resolved_by="mlro_01")
        assert case.status == CaseStatus.RESOLVED
        assert case.resolution == Resolution.SAR_FILED
        assert case.resolved_at is not None

    def test_close_resolved_case(self):
        case = _make_case()
        assign_case(case, "analyst_01")
        resolve_case(case, Resolution.DISMISSED, resolved_by="analyst_01")
        close_case(case)
        assert case.status == CaseStatus.CLOSED
        assert case.closed_at is not None

    def test_close_non_resolved_raises(self):
        case = _make_case()
        with pytest.raises(ValueError, match="Only resolved"):
            close_case(case)

    def test_resolve_already_resolved_raises(self):
        case = _make_case()
        assign_case(case, "analyst_01")
        resolve_case(case, Resolution.DISMISSED, resolved_by="analyst_01")
        with pytest.raises(ValueError, match="Cannot resolve"):
            resolve_case(case, Resolution.UPHELD, resolved_by="analyst_02")


# ---------------------------------------------------------------------------
# SLA checks
# ---------------------------------------------------------------------------

class TestSLAStatus:
    def test_sla_within_deadline(self):
        case = _make_case()
        result = check_sla_status(case)
        assert result["sla_met"] is True
        assert "remaining_hours" in result

    def test_sla_breached(self):
        case = _make_case()
        case.sla_deadline = datetime.now(timezone.utc) - timedelta(hours=2)
        result = check_sla_status(case)
        assert result["sla_met"] is False
        assert result["overdue_hours"] >= 2.0

    def test_sla_met_on_resolved_case(self):
        case = _make_case()
        assign_case(case, "analyst_01")
        resolve_case(case, Resolution.DISMISSED, resolved_by="analyst_01")
        result = check_sla_status(case)
        assert result["sla_met"] is True

    def test_sla_missed_on_late_resolution(self):
        case = _make_case()
        assign_case(case, "analyst_01")
        case.sla_deadline = datetime.now(timezone.utc) - timedelta(hours=5)
        resolve_case(case, Resolution.DISMISSED, resolved_by="analyst_01")
        result = check_sla_status(case)
        assert result["sla_met"] is False
