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
Disputes service: chargebacks, player complaints, and error resolution.

Handles:
  - Dispute types: chargeback, complaint, self-exclusion failure, technical error
  - Evidence collection from game logs, transactions, and session data
  - Resolution workflow: investigate -> respond -> escalate -> resolve
  - Chargeback response to PSP
  - Financial impact tracking per dispute
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

class DisputeType(str, Enum):
    CHARGEBACK = "CHARGEBACK"
    PLAYER_COMPLAINT = "PLAYER_COMPLAINT"
    SELF_EXCLUSION_FAILURE = "SELF_EXCLUSION_FAILURE"
    TECHNICAL_ERROR = "TECHNICAL_ERROR"
    BONUS_DISPUTE = "BONUS_DISPUTE"
    UNFAIR_TERMS = "UNFAIR_TERMS"
    DELAYED_WITHDRAWAL = "DELAYED_WITHDRAWAL"


class DisputeStatus(str, Enum):
    RECEIVED = "RECEIVED"
    UNDER_INVESTIGATION = "UNDER_INVESTIGATION"
    PENDING_EVIDENCE = "PENDING_EVIDENCE"
    PENDING_RESPONSE = "PENDING_RESPONSE"
    ESCALATED = "ESCALATED"
    RESOLVED = "RESOLVED"
    REFERRED_ADR = "REFERRED_ADR"
    CLOSED = "CLOSED"


class DisputeResolution(str, Enum):
    UPHELD = "UPHELD"                    # player wins
    PARTIALLY_UPHELD = "PARTIALLY_UPHELD"
    DISMISSED = "DISMISSED"              # operator wins
    REFUNDED = "REFUNDED"
    CREDITED = "CREDITED"
    REFERRED_ADR = "REFERRED_ADR"
    REFERRED_REGULATOR = "REFERRED_REGULATOR"
    VOIDED = "VOIDED"


class ChargebackOutcome(str, Enum):
    WON = "WON"          # operator won the chargeback
    LOST = "LOST"        # player/bank won
    ACCEPTED = "ACCEPTED"  # operator accepted without fighting
    PENDING = "PENDING"


class EvidenceType(str, Enum):
    GAME_LOG = "GAME_LOG"
    TRANSACTION_HISTORY = "TRANSACTION_HISTORY"
    SESSION_DATA = "SESSION_DATA"
    CHAT_TRANSCRIPT = "CHAT_TRANSCRIPT"
    SCREENSHOT = "SCREENSHOT"
    RNG_CERTIFICATE = "RNG_CERTIFICATE"
    KYC_DOCUMENT = "KYC_DOCUMENT"
    TERMS_ACCEPTANCE = "TERMS_ACCEPTANCE"
    IP_LOG = "IP_LOG"
    RESPONSIBLE_GAMING_LOG = "RESPONSIBLE_GAMING_LOG"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class DisputeEvidence(BaseModel):
    evidence_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    evidence_type: EvidenceType
    reference: str       # URL, file ID, or system reference
    description: str = ""
    collected_by: str = "system"
    collected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"use_enum_values": True}


class FinancialImpact(BaseModel):
    """Track the financial impact of a dispute."""
    disputed_amount: float = 0.0
    currency: str = "USD"
    refunded_amount: float = 0.0
    chargeback_fee: float = 0.0       # PSP chargeback fee
    goodwill_credit: float = 0.0
    net_impact: float = 0.0           # total cost to operator

    def calculate_net_impact(self) -> float:
        self.net_impact = self.refunded_amount + self.chargeback_fee + self.goodwill_credit
        return self.net_impact


class ChargebackDetail(BaseModel):
    """PSP chargeback-specific data."""
    psp_reference: str = ""
    psp_name: str = ""
    reason_code: str = ""             # Visa/MC reason code
    reason_description: str = ""
    original_transaction_id: str = ""
    original_transaction_date: datetime | None = None
    response_deadline: datetime | None = None
    outcome: ChargebackOutcome = ChargebackOutcome.PENDING
    response_sent: bool = False
    response_sent_at: datetime | None = None

    model_config = {"use_enum_values": True}


class Dispute(BaseModel):
    dispute_id: str = Field(default_factory=lambda: f"DSP-{uuid.uuid4().hex[:8].upper()}")
    dispute_type: DisputeType
    status: DisputeStatus = DisputeStatus.RECEIVED
    player_id: str
    player_email: str = ""
    subject: str
    description: str = ""
    evidence: list[DisputeEvidence] = Field(default_factory=list)
    financial_impact: FinancialImpact = Field(default_factory=FinancialImpact)
    chargeback: ChargebackDetail | None = None
    resolution: DisputeResolution | None = None
    resolution_notes: str = ""
    assigned_to: str | None = None
    assigned_team: str = "disputes"
    sla_hours: int = 48
    sla_deadline: datetime | None = None
    jurisdiction: str = ""
    adr_provider: str = ""           # ADR body for escalation
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: datetime | None = None
    closed_at: datetime | None = None
    created_by: str = "system"
    audit_trail: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"use_enum_values": True}


# ---------------------------------------------------------------------------
# SLA configuration by dispute type
# ---------------------------------------------------------------------------

_SLA_HOURS: dict[str, int] = {
    "CHARGEBACK": 24,                  # PSP deadlines are tight
    "PLAYER_COMPLAINT": 48,
    "SELF_EXCLUSION_FAILURE": 4,       # regulatory urgency
    "TECHNICAL_ERROR": 24,
    "BONUS_DISPUTE": 72,
    "UNFAIR_TERMS": 48,
    "DELAYED_WITHDRAWAL": 24,
}

# ADR providers per jurisdiction
_ADR_PROVIDERS: dict[str, str] = {
    "UKGC": "eCOGRA / IBAS",
    "MGA": "eCOGRA",
    "CURACAO": "N/A",
    "BRAZIL": "SENACON / Procon",
    "SWEDEN": "ARN (Allmanna reklamationsnamnden)",
    "ONTARIO": "iGaming Ontario",
}


# ---------------------------------------------------------------------------
# Audit helper
# ---------------------------------------------------------------------------

def _audit(dispute: Dispute, event: str, *, actor: str = "system",
           notes: str = "") -> None:
    dispute.audit_trail.append({
        "event": event,
        "actor": actor,
        "notes": notes,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    dispute.updated_at = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Dispute lifecycle
# ---------------------------------------------------------------------------

def create_dispute(dispute_type: DisputeType, player_id: str, subject: str, *,
                   description: str = "", player_email: str = "",
                   disputed_amount: float = 0.0, currency: str = "USD",
                   jurisdiction: str = "", created_by: str = "system",
                   metadata: dict[str, Any] | None = None) -> Dispute:
    """Create a new dispute with SLA computed from type."""
    sla_hours = _SLA_HOURS.get(dispute_type.value, 48)
    adr_provider = _ADR_PROVIDERS.get(jurisdiction, "")

    dispute = Dispute(
        dispute_type=dispute_type,
        player_id=player_id,
        player_email=player_email,
        subject=subject,
        description=description,
        jurisdiction=jurisdiction,
        adr_provider=adr_provider,
        sla_hours=sla_hours,
        sla_deadline=datetime.now(timezone.utc) + timedelta(hours=sla_hours),
        created_by=created_by,
        metadata=metadata or {},
        financial_impact=FinancialImpact(
            disputed_amount=disputed_amount,
            currency=currency,
        ),
    )
    _audit(dispute, "dispute_created", actor=created_by)
    logger.info("Dispute %s created: %s [%s]", dispute.dispute_id, subject, dispute_type.value)
    return dispute


def create_chargeback(player_id: str, *, psp_reference: str, psp_name: str,
                      reason_code: str, reason_description: str = "",
                      original_txn_id: str = "", original_txn_date: datetime | None = None,
                      disputed_amount: float = 0.0, currency: str = "USD",
                      chargeback_fee: float = 0.0, response_deadline: datetime | None = None,
                      jurisdiction: str = "", player_email: str = "",
                      metadata: dict[str, Any] | None = None) -> Dispute:
    """Create a chargeback dispute with PSP-specific detail."""
    dispute = create_dispute(
        DisputeType.CHARGEBACK,
        player_id=player_id,
        subject=f"Chargeback: {reason_code} via {psp_name}",
        description=reason_description,
        player_email=player_email,
        disputed_amount=disputed_amount,
        currency=currency,
        jurisdiction=jurisdiction,
        metadata=metadata,
    )
    dispute.chargeback = ChargebackDetail(
        psp_reference=psp_reference,
        psp_name=psp_name,
        reason_code=reason_code,
        reason_description=reason_description,
        original_transaction_id=original_txn_id,
        original_transaction_date=original_txn_date,
        response_deadline=response_deadline,
    )
    dispute.financial_impact.chargeback_fee = chargeback_fee
    _audit(dispute, "chargeback_detail_added", notes=f"PSP: {psp_name}, code: {reason_code}")
    return dispute


def assign_dispute(dispute: Dispute, assignee: str, *,
                   team: str = "", actor: str = "system") -> Dispute:
    """Assign a dispute to an agent."""
    if dispute.status in (DisputeStatus.RESOLVED, DisputeStatus.CLOSED):
        raise ValueError(f"Cannot assign dispute in status '{dispute.status}'")
    dispute.assigned_to = assignee
    if team:
        dispute.assigned_team = team
    if dispute.status == DisputeStatus.RECEIVED:
        dispute.status = DisputeStatus.UNDER_INVESTIGATION
    _audit(dispute, "dispute_assigned", actor=actor, notes=f"Assigned to {assignee}")
    return dispute


def add_evidence(dispute: Dispute, evidence_type: EvidenceType, reference: str, *,
                 description: str = "", collected_by: str = "system",
                 metadata: dict[str, Any] | None = None) -> Dispute:
    """Attach evidence to a dispute."""
    ev = DisputeEvidence(
        evidence_type=evidence_type,
        reference=reference,
        description=description,
        collected_by=collected_by,
        metadata=metadata or {},
    )
    dispute.evidence.append(ev)
    _audit(dispute, "evidence_added", actor=collected_by,
           notes=f"{evidence_type}: {reference}")
    return dispute


def collect_standard_evidence(dispute: Dispute) -> Dispute:
    """Auto-collect standard evidence based on dispute type."""
    player = dispute.player_id
    # Transaction history is always relevant
    add_evidence(dispute, EvidenceType.TRANSACTION_HISTORY,
                 f"txn_export/{player}", description="Full transaction history export")
    add_evidence(dispute, EvidenceType.SESSION_DATA,
                 f"session_log/{player}", description="Session activity log")
    add_evidence(dispute, EvidenceType.IP_LOG,
                 f"ip_log/{player}", description="IP address history")

    if dispute.dispute_type == DisputeType.CHARGEBACK:
        if dispute.chargeback and dispute.chargeback.original_transaction_id:
            add_evidence(dispute, EvidenceType.TRANSACTION_HISTORY,
                         dispute.chargeback.original_transaction_id,
                         description="Original transaction detail")

    if dispute.dispute_type == DisputeType.TECHNICAL_ERROR:
        add_evidence(dispute, EvidenceType.GAME_LOG,
                     f"game_log/{player}", description="Game round logs")
        add_evidence(dispute, EvidenceType.RNG_CERTIFICATE,
                     "rng_cert/current", description="Current RNG certificate")

    if dispute.dispute_type == DisputeType.SELF_EXCLUSION_FAILURE:
        add_evidence(dispute, EvidenceType.RESPONSIBLE_GAMING_LOG,
                     f"rg_log/{player}", description="Responsible gaming activity")

    _audit(dispute, "standard_evidence_collected")
    return dispute


def escalate_dispute(dispute: Dispute, *, reason: str = "",
                     actor: str = "system") -> Dispute:
    """Escalate to management or ADR."""
    if dispute.status in (DisputeStatus.RESOLVED, DisputeStatus.CLOSED):
        raise ValueError(f"Cannot escalate dispute in status '{dispute.status}'")
    dispute.status = DisputeStatus.ESCALATED
    _audit(dispute, "dispute_escalated", actor=actor, notes=reason)
    return dispute


def refer_to_adr(dispute: Dispute, *, actor: str = "system") -> Dispute:
    """Refer dispute to the jurisdiction's ADR body."""
    if not dispute.adr_provider:
        raise ValueError("No ADR provider configured for this jurisdiction")
    dispute.status = DisputeStatus.REFERRED_ADR
    _audit(dispute, "referred_to_adr", actor=actor,
           notes=f"Referred to {dispute.adr_provider}")
    return dispute


def respond_to_chargeback(dispute: Dispute, *, evidence_summary: str,
                          accept: bool = False,
                          actor: str = "system") -> Dispute:
    """Send chargeback response to PSP."""
    if dispute.dispute_type != DisputeType.CHARGEBACK:
        raise ValueError("Not a chargeback dispute")
    if dispute.chargeback is None:
        raise ValueError("No chargeback detail attached")

    dispute.chargeback.response_sent = True
    dispute.chargeback.response_sent_at = datetime.now(timezone.utc)

    if accept:
        dispute.chargeback.outcome = ChargebackOutcome.ACCEPTED
        _audit(dispute, "chargeback_accepted", actor=actor, notes=evidence_summary)
    else:
        _audit(dispute, "chargeback_contested", actor=actor, notes=evidence_summary)

    return dispute


def resolve_dispute(dispute: Dispute, resolution: DisputeResolution, *,
                    notes: str = "", refunded_amount: float = 0.0,
                    goodwill_credit: float = 0.0,
                    resolved_by: str = "system") -> Dispute:
    """Resolve a dispute with outcome and financial settlement."""
    if dispute.status in (DisputeStatus.RESOLVED, DisputeStatus.CLOSED):
        raise ValueError(f"Cannot resolve dispute in status '{dispute.status}'")

    dispute.status = DisputeStatus.RESOLVED
    dispute.resolution = resolution
    dispute.resolution_notes = notes
    dispute.resolved_at = datetime.now(timezone.utc)

    # Update financial impact
    dispute.financial_impact.refunded_amount = refunded_amount
    dispute.financial_impact.goodwill_credit = goodwill_credit
    dispute.financial_impact.calculate_net_impact()

    _audit(dispute, "dispute_resolved", actor=resolved_by,
           notes=f"{resolution}: {notes}")
    return dispute


def close_dispute(dispute: Dispute, *, actor: str = "system") -> Dispute:
    """Close a resolved dispute (final state)."""
    if dispute.status != DisputeStatus.RESOLVED:
        raise ValueError("Only resolved disputes can be closed")
    dispute.status = DisputeStatus.CLOSED
    dispute.closed_at = datetime.now(timezone.utc)
    _audit(dispute, "dispute_closed", actor=actor)
    return dispute


def set_chargeback_outcome(dispute: Dispute, outcome: ChargebackOutcome, *,
                           actor: str = "system") -> Dispute:
    """Record the final chargeback outcome from PSP."""
    if dispute.chargeback is None:
        raise ValueError("Not a chargeback dispute")
    dispute.chargeback.outcome = outcome
    if outcome == ChargebackOutcome.LOST:
        dispute.financial_impact.refunded_amount = dispute.financial_impact.disputed_amount
    dispute.financial_impact.calculate_net_impact()
    _audit(dispute, "chargeback_outcome_recorded", actor=actor, notes=outcome)
    return dispute


# ---------------------------------------------------------------------------
# Reporting / queries
# ---------------------------------------------------------------------------

def check_sla_status(dispute: Dispute) -> dict[str, Any]:
    """Check SLA compliance for a dispute."""
    now = datetime.now(timezone.utc)
    result: dict[str, Any] = {
        "dispute_id": dispute.dispute_id,
        "sla_hours": dispute.sla_hours,
        "status": dispute.status,
    }
    if dispute.status in (DisputeStatus.RESOLVED, DisputeStatus.CLOSED):
        met = dispute.resolved_at and dispute.sla_deadline and dispute.resolved_at <= dispute.sla_deadline
        result["sla_met"] = bool(met)
    elif dispute.sla_deadline:
        result["sla_met"] = now <= dispute.sla_deadline
        if not result["sla_met"]:
            overdue = now - dispute.sla_deadline
            result["overdue_hours"] = round(overdue.total_seconds() / 3600, 1)
    else:
        result["sla_met"] = True
    return result


def calculate_dispute_metrics(disputes: list[Dispute]) -> dict[str, Any]:
    """Calculate aggregate metrics over a list of disputes."""
    if not disputes:
        return {"total": 0}
    total = len(disputes)
    by_type: dict[str, int] = {}
    by_status: dict[str, int] = {}
    by_resolution: dict[str, int] = {}
    total_disputed = 0.0
    total_refunded = 0.0
    total_fees = 0.0
    sla_met = 0
    sla_missed = 0

    for d in disputes:
        by_type[d.dispute_type] = by_type.get(d.dispute_type, 0) + 1
        by_status[d.status] = by_status.get(d.status, 0) + 1
        if d.resolution:
            by_resolution[d.resolution] = by_resolution.get(d.resolution, 0) + 1
        total_disputed += d.financial_impact.disputed_amount
        total_refunded += d.financial_impact.refunded_amount
        total_fees += d.financial_impact.chargeback_fee
        sla = check_sla_status(d)
        if sla.get("sla_met"):
            sla_met += 1
        else:
            sla_missed += 1

    return {
        "total": total,
        "by_type": by_type,
        "by_status": by_status,
        "by_resolution": by_resolution,
        "total_disputed_amount": round(total_disputed, 2),
        "total_refunded_amount": round(total_refunded, 2),
        "total_chargeback_fees": round(total_fees, 2),
        "sla_met": sla_met,
        "sla_missed": sla_missed,
        "sla_compliance_rate": round(sla_met / total * 100, 1) if total > 0 else 0.0,
    }
