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
Tests for the disputes service: chargebacks, complaints, and error resolution.
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from datetime import datetime, timedelta, timezone

from disputes import (
    ChargebackOutcome,
    Dispute,
    DisputeResolution,
    DisputeStatus,
    DisputeType,
    EvidenceType,
    add_evidence,
    assign_dispute,
    calculate_dispute_metrics,
    check_sla_status,
    close_dispute,
    collect_standard_evidence,
    create_chargeback,
    create_dispute,
    escalate_dispute,
    refer_to_adr,
    resolve_dispute,
    respond_to_chargeback,
    set_chargeback_outcome,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_complaint(**kwargs) -> Dispute:
    defaults = {
        "dispute_type": DisputeType.PLAYER_COMPLAINT,
        "player_id": "player_001",
        "subject": "Withdrawal delayed 5 days",
        "disputed_amount": 500.0,
    }
    defaults.update(kwargs)
    return create_dispute(**defaults)


def _make_chargeback(**kwargs) -> Dispute:
    defaults = {
        "player_id": "player_002",
        "psp_reference": "PSP-REF-001",
        "psp_name": "Adyen",
        "reason_code": "10.4",
        "reason_description": "Other fraud - Card absent",
        "original_txn_id": "TXN-99001",
        "disputed_amount": 200.0,
        "currency": "GBP",
        "chargeback_fee": 25.0,
        "jurisdiction": "UKGC",
    }
    defaults.update(kwargs)
    return create_chargeback(**defaults)


# ---------------------------------------------------------------------------
# Dispute creation tests
# ---------------------------------------------------------------------------

class TestDisputeCreation:
    def test_create_complaint(self):
        d = _make_complaint()
        assert d.status == DisputeStatus.RECEIVED
        assert d.dispute_type == DisputeType.PLAYER_COMPLAINT
        assert d.dispute_id.startswith("DSP-")
        assert d.sla_hours == 48

    def test_create_self_exclusion_failure(self):
        d = create_dispute(
            DisputeType.SELF_EXCLUSION_FAILURE,
            player_id="player_003",
            subject="Self-excluded player placed bets",
        )
        assert d.sla_hours == 4  # regulatory urgency

    def test_sla_deadline_set(self):
        d = _make_complaint()
        assert d.sla_deadline is not None
        assert d.sla_deadline > datetime.now(timezone.utc)

    def test_financial_impact_set(self):
        d = _make_complaint(disputed_amount=500.0, currency="EUR")
        assert d.financial_impact.disputed_amount == 500.0
        assert d.financial_impact.currency == "EUR"

    def test_audit_trail_on_create(self):
        d = _make_complaint()
        assert len(d.audit_trail) >= 1
        assert d.audit_trail[0]["event"] == "dispute_created"


# ---------------------------------------------------------------------------
# Chargeback creation tests
# ---------------------------------------------------------------------------

class TestChargebackCreation:
    def test_create_chargeback(self):
        d = _make_chargeback()
        assert d.dispute_type == DisputeType.CHARGEBACK
        assert d.chargeback is not None
        assert d.chargeback.psp_reference == "PSP-REF-001"
        assert d.chargeback.reason_code == "10.4"
        assert d.financial_impact.chargeback_fee == 25.0

    def test_chargeback_sla_is_24h(self):
        d = _make_chargeback()
        assert d.sla_hours == 24

    def test_chargeback_adr_provider_set(self):
        d = _make_chargeback(jurisdiction="UKGC")
        assert "eCOGRA" in d.adr_provider or "IBAS" in d.adr_provider


# ---------------------------------------------------------------------------
# Assignment tests
# ---------------------------------------------------------------------------

class TestAssignment:
    def test_assign_moves_to_under_investigation(self):
        d = _make_complaint()
        assign_dispute(d, "agent_01", team="complaints")
        assert d.status == DisputeStatus.UNDER_INVESTIGATION
        assert d.assigned_to == "agent_01"

    def test_assign_closed_raises(self):
        d = _make_complaint()
        assign_dispute(d, "agent_01")
        resolve_dispute(d, DisputeResolution.DISMISSED, resolved_by="agent_01")
        close_dispute(d)
        with pytest.raises(ValueError, match="Cannot assign"):
            assign_dispute(d, "agent_02")


# ---------------------------------------------------------------------------
# Evidence tests
# ---------------------------------------------------------------------------

class TestEvidence:
    def test_add_evidence(self):
        d = _make_complaint()
        add_evidence(d, EvidenceType.TRANSACTION_HISTORY, "TXN-EXPORT-001")
        assert len(d.evidence) == 1
        assert d.evidence[0].evidence_type == EvidenceType.TRANSACTION_HISTORY

    def test_collect_standard_evidence(self):
        d = _make_complaint()
        collect_standard_evidence(d)
        types = [e.evidence_type for e in d.evidence]
        assert EvidenceType.TRANSACTION_HISTORY in types
        assert EvidenceType.SESSION_DATA in types
        assert EvidenceType.IP_LOG in types

    def test_standard_evidence_for_technical_error(self):
        d = create_dispute(DisputeType.TECHNICAL_ERROR, "player_x", subject="Game froze")
        collect_standard_evidence(d)
        types = [e.evidence_type for e in d.evidence]
        assert EvidenceType.GAME_LOG in types
        assert EvidenceType.RNG_CERTIFICATE in types

    def test_standard_evidence_for_self_exclusion(self):
        d = create_dispute(DisputeType.SELF_EXCLUSION_FAILURE, "player_x", subject="Breach")
        collect_standard_evidence(d)
        types = [e.evidence_type for e in d.evidence]
        assert EvidenceType.RESPONSIBLE_GAMING_LOG in types


# ---------------------------------------------------------------------------
# Chargeback response tests
# ---------------------------------------------------------------------------

class TestChargebackResponse:
    def test_contest_chargeback(self):
        d = _make_chargeback()
        respond_to_chargeback(d, evidence_summary="Player completed KYC, 3DS verified")
        assert d.chargeback.response_sent is True
        assert d.chargeback.response_sent_at is not None

    def test_accept_chargeback(self):
        d = _make_chargeback()
        respond_to_chargeback(d, evidence_summary="Accepting", accept=True)
        assert d.chargeback.outcome == ChargebackOutcome.ACCEPTED

    def test_respond_non_chargeback_raises(self):
        d = _make_complaint()
        with pytest.raises(ValueError, match="Not a chargeback"):
            respond_to_chargeback(d, evidence_summary="x")


# ---------------------------------------------------------------------------
# Escalation and ADR
# ---------------------------------------------------------------------------

class TestEscalation:
    def test_escalate(self):
        d = _make_complaint()
        assign_dispute(d, "agent_01")
        escalate_dispute(d, reason="Needs legal review")
        assert d.status == DisputeStatus.ESCALATED

    def test_escalate_closed_raises(self):
        d = _make_complaint()
        assign_dispute(d, "agent_01")
        resolve_dispute(d, DisputeResolution.DISMISSED, resolved_by="agent_01")
        close_dispute(d)
        with pytest.raises(ValueError):
            escalate_dispute(d)

    def test_refer_to_adr(self):
        d = _make_complaint(jurisdiction="UKGC")
        refer_to_adr(d)
        assert d.status == DisputeStatus.REFERRED_ADR

    def test_refer_adr_no_provider_raises(self):
        d = _make_complaint(jurisdiction="UNKNOWN")
        with pytest.raises(ValueError, match="No ADR provider"):
            refer_to_adr(d)


# ---------------------------------------------------------------------------
# Resolution tests
# ---------------------------------------------------------------------------

class TestResolution:
    def test_resolve_upheld(self):
        d = _make_complaint()
        assign_dispute(d, "agent_01")
        resolve_dispute(d, DisputeResolution.UPHELD,
                        notes="Player was correct, withdrawal delayed",
                        refunded_amount=500.0, resolved_by="manager_01")
        assert d.status == DisputeStatus.RESOLVED
        assert d.resolution == DisputeResolution.UPHELD
        assert d.financial_impact.refunded_amount == 500.0
        assert d.financial_impact.net_impact == 500.0

    def test_resolve_dismissed(self):
        d = _make_complaint()
        assign_dispute(d, "agent_01")
        resolve_dispute(d, DisputeResolution.DISMISSED, resolved_by="agent_01")
        assert d.financial_impact.net_impact == 0.0

    def test_resolve_with_goodwill(self):
        d = _make_complaint()
        assign_dispute(d, "agent_01")
        resolve_dispute(d, DisputeResolution.PARTIALLY_UPHELD,
                        refunded_amount=100.0, goodwill_credit=50.0,
                        resolved_by="manager_01")
        assert d.financial_impact.net_impact == 150.0

    def test_close_dispute(self):
        d = _make_complaint()
        assign_dispute(d, "agent_01")
        resolve_dispute(d, DisputeResolution.DISMISSED, resolved_by="agent_01")
        close_dispute(d)
        assert d.status == DisputeStatus.CLOSED
        assert d.closed_at is not None

    def test_close_non_resolved_raises(self):
        d = _make_complaint()
        with pytest.raises(ValueError, match="Only resolved"):
            close_dispute(d)


# ---------------------------------------------------------------------------
# Chargeback outcome tests
# ---------------------------------------------------------------------------

class TestChargebackOutcome:
    def test_chargeback_won(self):
        d = _make_chargeback()
        set_chargeback_outcome(d, ChargebackOutcome.WON)
        assert d.chargeback.outcome == ChargebackOutcome.WON
        # Operator won, no refund
        assert d.financial_impact.refunded_amount == 0.0

    def test_chargeback_lost(self):
        d = _make_chargeback()
        set_chargeback_outcome(d, ChargebackOutcome.LOST)
        assert d.financial_impact.refunded_amount == 200.0  # full amount
        assert d.financial_impact.net_impact == 225.0  # refund + fee


# ---------------------------------------------------------------------------
# SLA tests
# ---------------------------------------------------------------------------

class TestSLA:
    def test_sla_within_deadline(self):
        d = _make_complaint()
        result = check_sla_status(d)
        assert result["sla_met"] is True

    def test_sla_breached(self):
        d = _make_complaint()
        d.sla_deadline = datetime.now(timezone.utc) - timedelta(hours=10)
        result = check_sla_status(d)
        assert result["sla_met"] is False
        assert result["overdue_hours"] >= 10.0


# ---------------------------------------------------------------------------
# Metrics tests
# ---------------------------------------------------------------------------

class TestMetrics:
    def test_empty_metrics(self):
        result = calculate_dispute_metrics([])
        assert result["total"] == 0

    def test_aggregate_metrics(self):
        disputes = []
        d1 = _make_complaint(disputed_amount=500.0)
        assign_dispute(d1, "a1")
        resolve_dispute(d1, DisputeResolution.UPHELD, refunded_amount=500.0, resolved_by="m1")
        disputes.append(d1)

        d2 = _make_complaint(disputed_amount=300.0)
        assign_dispute(d2, "a1")
        resolve_dispute(d2, DisputeResolution.DISMISSED, resolved_by="a1")
        disputes.append(d2)

        d3 = _make_chargeback(disputed_amount=200.0, chargeback_fee=25.0)
        disputes.append(d3)

        metrics = calculate_dispute_metrics(disputes)
        assert metrics["total"] == 3
        assert metrics["total_disputed_amount"] == 1000.0
        assert metrics["total_refunded_amount"] == 500.0
        assert metrics["total_chargeback_fees"] == 25.0
