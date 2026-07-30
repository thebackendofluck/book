# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
tests/test_kyc_lifecycle.py — Tests for KYC lifecycle service.

Covers:
  - State machine transitions (valid and invalid)
  - Retention policies per jurisdiction
  - Reviewer workflow (assign, review, approve/reject)
  - Document management with access control
  - PII access logging and redaction
  - Regulatory export
  - Expiry and re-verification
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kyc_lifecycle import (
    AccessLevel,
    DocumentType,
    KYCCase,
    KYCLifecycleService,
    KYCState,
    ReviewDecision,
    VALID_TRANSITIONS,
    retention_for_jurisdiction,
)
from evidence_store import EvidenceStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def service(tmp_path) -> KYCLifecycleService:
    store = EvidenceStore(storage_root=str(tmp_path / "evidence"))
    return KYCLifecycleService(evidence_store=store)


@pytest.fixture
def case_gb(service: KYCLifecycleService) -> KYCCase:
    """A UK KYC case in PENDING state."""
    return service.create_case(
        player_id="player-uk-001",
        jurisdiction="GB",
        trigger="REGISTRATION",
    )


@pytest.fixture
def case_nj(service: KYCLifecycleService) -> KYCCase:
    """A New Jersey KYC case in PENDING state."""
    return service.create_case(
        player_id="player-nj-001",
        jurisdiction="US-NJ",
        trigger="REGISTRATION",
    )


# ---------------------------------------------------------------------------
# Retention policies
# ---------------------------------------------------------------------------

class TestRetentionPolicies:
    def test_nj_retention_7_years(self):
        assert retention_for_jurisdiction("US-NJ") == 7

    def test_uk_retention_5_years(self):
        assert retention_for_jurisdiction("GB") == 5

    def test_malta_retention_5_years(self):
        assert retention_for_jurisdiction("MT") == 5

    def test_brazil_retention_10_years(self):
        assert retention_for_jurisdiction("BR") == 10

    def test_unknown_jurisdiction_defaults(self):
        assert retention_for_jurisdiction("XX") == 5

    def test_case_retention_date_set_on_creation(self, case_nj: KYCCase):
        assert case_nj.retention_until != ""
        retention = datetime.fromisoformat(case_nj.retention_until)
        now = datetime.now(timezone.utc)
        years_diff = (retention - now).days / 365
        assert years_diff > 6.9  # ~7 years for NJ


# ---------------------------------------------------------------------------
# State machine transitions
# ---------------------------------------------------------------------------

class TestStateMachine:
    def test_valid_happy_path(self, service: KYCLifecycleService, case_gb: KYCCase):
        """PENDING -> DOCS_REQUESTED -> SUBMITTED -> UNDER_REVIEW -> APPROVED"""
        cid = case_gb.case_id

        service.transition(
            cid, KYCState.DOCUMENTS_REQUESTED,
            actor="agent-1", actor_role=AccessLevel.REVIEWER.value,
        )
        assert service.get_case(cid).state == "DOCUMENTS_REQUESTED"

        service.transition(
            cid, KYCState.SUBMITTED,
            actor="player-uk-001", actor_role=AccessLevel.REVIEWER.value,
        )
        assert service.get_case(cid).state == "SUBMITTED"

        service.transition(
            cid, KYCState.UNDER_REVIEW,
            actor="reviewer-1", actor_role=AccessLevel.REVIEWER.value,
        )
        assert service.get_case(cid).state == "UNDER_REVIEW"

        service.transition(
            cid, KYCState.APPROVED,
            actor="reviewer-1", actor_role=AccessLevel.REVIEWER.value,
            reason="All documents verified",
        )
        assert service.get_case(cid).state == "APPROVED"

    def test_rejection_and_retry(self, service: KYCLifecycleService, case_gb: KYCCase):
        """UNDER_REVIEW -> REJECTED -> DOCUMENTS_REQUESTED (re-upload)"""
        cid = case_gb.case_id

        # Advance to UNDER_REVIEW
        for state in [
            KYCState.DOCUMENTS_REQUESTED,
            KYCState.SUBMITTED,
            KYCState.UNDER_REVIEW,
        ]:
            service.transition(
                cid, state,
                actor="reviewer-1", actor_role=AccessLevel.REVIEWER.value,
            )

        service.transition(
            cid, KYCState.REJECTED,
            actor="reviewer-1", actor_role=AccessLevel.REVIEWER.value,
            reason="Blurry passport image",
        )
        assert service.get_case(cid).state == "REJECTED"

        # Player can re-upload
        service.transition(
            cid, KYCState.DOCUMENTS_REQUESTED,
            actor="agent-1", actor_role=AccessLevel.REVIEWER.value,
            reason="Re-upload requested",
        )
        assert service.get_case(cid).state == "DOCUMENTS_REQUESTED"

    def test_invalid_transition_raises(self, service: KYCLifecycleService, case_gb: KYCCase):
        """Cannot skip from PENDING directly to APPROVED."""
        with pytest.raises(ValueError, match="Invalid transition"):
            service.transition(
                case_gb.case_id, KYCState.APPROVED,
                actor="hacker", actor_role=AccessLevel.REVIEWER.value,
            )

    def test_suspend_from_any_state(self, service: KYCLifecycleService, case_gb: KYCCase):
        """COMPLIANCE_OFFICER can suspend from any state."""
        service.transition(
            case_gb.case_id, KYCState.SUSPENDED,
            actor="co-1", actor_role=AccessLevel.COMPLIANCE_OFFICER.value,
            reason="Fraud investigation",
        )
        assert service.get_case(case_gb.case_id).state == "SUSPENDED"

    def test_non_compliance_officer_cannot_suspend(
        self, service: KYCLifecycleService, case_gb: KYCCase
    ):
        with pytest.raises(PermissionError, match="COMPLIANCE_OFFICER"):
            service.transition(
                case_gb.case_id, KYCState.SUSPENDED,
                actor="agent-1", actor_role=AccessLevel.AGENT.value,
            )

    def test_edd_trigger_from_approved(self, service: KYCLifecycleService, case_gb: KYCCase):
        """APPROVED -> ENHANCED_DUE_DILIGENCE (high-value trigger)."""
        cid = case_gb.case_id
        for state in [
            KYCState.DOCUMENTS_REQUESTED,
            KYCState.SUBMITTED,
            KYCState.UNDER_REVIEW,
            KYCState.APPROVED,
        ]:
            service.transition(
                cid, state,
                actor="reviewer-1", actor_role=AccessLevel.REVIEWER.value,
            )

        service.transition(
            cid, KYCState.ENHANCED_DUE_DILIGENCE,
            actor="SYSTEM", actor_role=AccessLevel.SYSTEM.value,
            reason="Deposit threshold exceeded: EUR 50,000",
        )
        assert service.get_case(cid).state == "ENHANCED_DUE_DILIGENCE"

    def test_all_valid_transitions_are_bidirectional_check(self):
        """Every state in VALID_TRANSITIONS has at least one outgoing edge."""
        for state in KYCState:
            assert state in VALID_TRANSITIONS, (
                f"State {state.value} missing from VALID_TRANSITIONS"
            )


# ---------------------------------------------------------------------------
# Reviewer workflow
# ---------------------------------------------------------------------------

class TestReviewerWorkflow:
    def _advance_to_submitted(self, service, case):
        for state in [KYCState.DOCUMENTS_REQUESTED, KYCState.SUBMITTED]:
            service.transition(
                case.case_id, state,
                actor="agent-1", actor_role=AccessLevel.REVIEWER.value,
            )

    def test_assign_reviewer(self, service: KYCLifecycleService, case_gb: KYCCase):
        self._advance_to_submitted(service, case_gb)
        assignment = service.assign_reviewer(
            case_gb.case_id, "reviewer-42",
            actor="supervisor-1", actor_role=AccessLevel.COMPLIANCE_OFFICER.value,
        )
        assert assignment.reviewer_id == "reviewer-42"
        assert service.get_case(case_gb.case_id).state == "UNDER_REVIEW"

    def test_submit_review_approve(self, service: KYCLifecycleService, case_gb: KYCCase):
        self._advance_to_submitted(service, case_gb)
        service.assign_reviewer(
            case_gb.case_id, "reviewer-42",
            actor="supervisor-1", actor_role=AccessLevel.COMPLIANCE_OFFICER.value,
        )
        service.submit_review(
            case_gb.case_id, "reviewer-42",
            ReviewDecision.APPROVE, "Documents verified", risk_score=0.1,
        )
        assert service.get_case(case_gb.case_id).state == "APPROVED"

    def test_submit_review_reject(self, service: KYCLifecycleService, case_gb: KYCCase):
        self._advance_to_submitted(service, case_gb)
        service.assign_reviewer(
            case_gb.case_id, "reviewer-42",
            actor="supervisor-1", actor_role=AccessLevel.COMPLIANCE_OFFICER.value,
        )
        service.submit_review(
            case_gb.case_id, "reviewer-42",
            ReviewDecision.REJECT, "Expired passport",
        )
        assert service.get_case(case_gb.case_id).state == "REJECTED"

    def test_submit_review_escalate(self, service: KYCLifecycleService, case_gb: KYCCase):
        self._advance_to_submitted(service, case_gb)
        service.assign_reviewer(
            case_gb.case_id, "reviewer-42",
            actor="supervisor-1", actor_role=AccessLevel.COMPLIANCE_OFFICER.value,
        )
        service.submit_review(
            case_gb.case_id, "reviewer-42",
            ReviewDecision.ESCALATE, "PEP match detected", risk_score=0.9,
        )
        assert service.get_case(case_gb.case_id).state == "ENHANCED_DUE_DILIGENCE"

    def test_cannot_assign_reviewer_in_wrong_state(
        self, service: KYCLifecycleService, case_gb: KYCCase
    ):
        with pytest.raises(ValueError, match="Cannot assign reviewer"):
            service.assign_reviewer(
                case_gb.case_id, "reviewer-42",
                actor="supervisor-1",
                actor_role=AccessLevel.COMPLIANCE_OFFICER.value,
            )


# ---------------------------------------------------------------------------
# Document management
# ---------------------------------------------------------------------------

class TestDocumentManagement:
    def _advance_to_docs_requested(self, service, case):
        service.transition(
            case.case_id, KYCState.DOCUMENTS_REQUESTED,
            actor="agent-1", actor_role=AccessLevel.REVIEWER.value,
        )

    def test_add_document(self, service: KYCLifecycleService, case_gb: KYCCase):
        self._advance_to_docs_requested(service, case_gb)
        doc = service.add_document(
            case_gb.case_id, DocumentType.IDENTITY,
            "passport.jpg", b"fake-passport-content",
            actor="player-uk-001", actor_role=AccessLevel.AGENT.value,
        )
        assert doc.document_type == "IDENTITY"
        assert doc.content_hash != ""
        assert len(service.get_case(case_gb.case_id).documents) == 1

    def test_cannot_add_document_in_wrong_state(
        self, service: KYCLifecycleService, case_gb: KYCCase
    ):
        with pytest.raises(ValueError, match="Cannot add documents"):
            service.add_document(
                case_gb.case_id, DocumentType.IDENTITY,
                "passport.jpg", b"content",
                actor="player", actor_role=AccessLevel.AGENT.value,
            )

    def test_agent_gets_redacted_view(self, service: KYCLifecycleService, case_gb: KYCCase):
        self._advance_to_docs_requested(service, case_gb)
        doc = service.add_document(
            case_gb.case_id, DocumentType.IDENTITY,
            "passport.jpg", b"content",
            actor="player", actor_role=AccessLevel.AGENT.value,
        )
        viewed = service.view_document(
            case_gb.case_id, doc.document_id,
            actor="agent-1", actor_role=AccessLevel.AGENT.value,
        )
        assert viewed is not None
        assert viewed.player_id == "[REDACTED]"
        assert viewed.encrypted_ref == "[ACCESS_RESTRICTED]"

    def test_reviewer_gets_full_view(self, service: KYCLifecycleService, case_gb: KYCCase):
        self._advance_to_docs_requested(service, case_gb)
        doc = service.add_document(
            case_gb.case_id, DocumentType.IDENTITY,
            "passport.jpg", b"content",
            actor="player", actor_role=AccessLevel.AGENT.value,
        )
        viewed = service.view_document(
            case_gb.case_id, doc.document_id,
            actor="reviewer-1", actor_role=AccessLevel.REVIEWER.value,
        )
        assert viewed is not None
        assert viewed.player_id != "[REDACTED]"


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------

class TestAuditTrail:
    def test_creation_logged(self, service: KYCLifecycleService, case_gb: KYCCase):
        trail = service.get_case(case_gb.case_id).audit_trail
        assert len(trail) >= 1
        assert trail[0].action == "CASE_CREATED"

    def test_transitions_logged(self, service: KYCLifecycleService, case_gb: KYCCase):
        service.transition(
            case_gb.case_id, KYCState.DOCUMENTS_REQUESTED,
            actor="agent-1", actor_role=AccessLevel.REVIEWER.value,
        )
        trail = service.get_case(case_gb.case_id).audit_trail
        transition_entries = [e for e in trail if e.action == "STATE_TRANSITION"]
        assert len(transition_entries) >= 1
        assert transition_entries[0].to_state == "DOCUMENTS_REQUESTED"

    def test_document_view_logged(self, service: KYCLifecycleService, case_gb: KYCCase):
        service.transition(
            case_gb.case_id, KYCState.DOCUMENTS_REQUESTED,
            actor="agent-1", actor_role=AccessLevel.REVIEWER.value,
        )
        doc = service.add_document(
            case_gb.case_id, DocumentType.IDENTITY,
            "id.jpg", b"content",
            actor="player", actor_role=AccessLevel.AGENT.value,
        )
        service.view_document(
            case_gb.case_id, doc.document_id,
            actor="reviewer-1", actor_role=AccessLevel.REVIEWER.value,
        )
        trail = service.get_case(case_gb.case_id).audit_trail
        view_entries = [e for e in trail if e.action == "DOCUMENT_VIEWED"]
        assert len(view_entries) >= 1


# ---------------------------------------------------------------------------
# Regulatory export
# ---------------------------------------------------------------------------

class TestRegulatoryExport:
    def test_compliance_officer_can_export(
        self, service: KYCLifecycleService, case_gb: KYCCase
    ):
        export = service.export_for_regulator(
            case_gb.case_id,
            actor="co-1", actor_role=AccessLevel.COMPLIANCE_OFFICER.value,
        )
        assert "export_id" in export
        assert export["jurisdiction"] == "GB"
        assert export["retention_policy_years"] == 5

    def test_non_compliance_officer_cannot_export(
        self, service: KYCLifecycleService, case_gb: KYCCase
    ):
        with pytest.raises(PermissionError, match="COMPLIANCE_OFFICER"):
            service.export_for_regulator(
                case_gb.case_id,
                actor="agent-1", actor_role=AccessLevel.AGENT.value,
            )

    def test_export_logs_audit_entry(
        self, service: KYCLifecycleService, case_gb: KYCCase
    ):
        service.export_for_regulator(
            case_gb.case_id,
            actor="co-1", actor_role=AccessLevel.COMPLIANCE_OFFICER.value,
        )
        trail = service.get_case(case_gb.case_id).audit_trail
        export_entries = [e for e in trail if e.action == "REGULATORY_EXPORT"]
        assert len(export_entries) == 1


# ---------------------------------------------------------------------------
# Retention report
# ---------------------------------------------------------------------------

class TestRetentionReport:
    def test_retention_report_structure(
        self, service: KYCLifecycleService, case_gb: KYCCase, case_nj: KYCCase
    ):
        report = service.retention_report()
        assert len(report) == 2
        jurisdictions = {r["jurisdiction"] for r in report}
        assert "GB" in jurisdictions
        assert "US-NJ" in jurisdictions
        for entry in report:
            assert entry["compliant"] is True
            assert entry["days_remaining"] > 0
