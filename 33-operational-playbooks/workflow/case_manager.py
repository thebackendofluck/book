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
Case management service for compliance, support, and responsible gaming.

Cases are created from triggers (AML alerts, player complaints, RG flags)
and tracked through investigation to resolution.  Each case is tied to a
workflow instance for step-by-step orchestration.
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

class CaseStatus(str, Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    PENDING_INFO = "PENDING_INFO"
    ESCALATED = "ESCALATED"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class CasePriority(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class CaseType(str, Enum):
    AML_ALERT = "AML_ALERT"
    PLAYER_COMPLAINT = "PLAYER_COMPLAINT"
    RG_FLAG = "RG_FLAG"
    KYC_REVIEW = "KYC_REVIEW"
    CHARGEBACK = "CHARGEBACK"
    SELF_EXCLUSION_BREACH = "SELF_EXCLUSION_BREACH"
    TECHNICAL_ERROR = "TECHNICAL_ERROR"
    BONUS_ABUSE = "BONUS_ABUSE"


class Resolution(str, Enum):
    UPHELD = "UPHELD"
    DISMISSED = "DISMISSED"
    PARTIALLY_UPHELD = "PARTIALLY_UPHELD"
    REFERRED_ADR = "REFERRED_ADR"
    REFERRED_REGULATOR = "REFERRED_REGULATOR"
    SAR_FILED = "SAR_FILED"
    NO_ACTION = "NO_ACTION"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class CaseNote(BaseModel):
    note_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    author: str
    content: str
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    is_internal: bool = True  # internal notes not visible to player


class CaseEvidence(BaseModel):
    evidence_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    evidence_type: str  # "game_log", "transaction", "session_data", "screenshot", "document"
    reference: str       # URL or ID of the evidence
    description: str = ""
    collected_by: str = "system"
    collected_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class Case(BaseModel):
    case_id: str = Field(default_factory=lambda: f"CASE-{uuid.uuid4().hex[:8].upper()}")
    case_type: CaseType
    status: CaseStatus = CaseStatus.OPEN
    priority: CasePriority = CasePriority.NORMAL
    subject_id: str          # player_id or transaction_id
    subject_type: str = "player"
    title: str
    description: str = ""
    trigger_source: str = ""  # "aml_system", "player_email", "rg_detector", etc.
    trigger_reference: str = ""  # alert ID, email ID, etc.
    assigned_team: str = ""
    assigned_to: str | None = None
    workflow_id: str | None = None
    sla_hours: int = 24
    sla_deadline: datetime | None = None
    notes: list[CaseNote] = Field(default_factory=list)
    evidence: list[CaseEvidence] = Field(default_factory=list)
    resolution: Resolution | None = None
    resolution_notes: str = ""
    resolved_by: str | None = None
    created_by: str = "system"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: datetime | None = None
    closed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    audit_trail: list[dict[str, Any]] = Field(default_factory=list)
    jurisdiction: str = ""

    model_config = {"use_enum_values": True}


# ---------------------------------------------------------------------------
# SLA configuration per case type and priority
# ---------------------------------------------------------------------------

_SLA_HOURS: dict[tuple[str, str], int] = {
    # (case_type, priority) -> hours
    ("AML_ALERT", "CRITICAL"): 4,
    ("AML_ALERT", "HIGH"): 8,
    ("AML_ALERT", "NORMAL"): 24,
    ("AML_ALERT", "LOW"): 48,
    ("PLAYER_COMPLAINT", "CRITICAL"): 4,
    ("PLAYER_COMPLAINT", "HIGH"): 8,
    ("PLAYER_COMPLAINT", "NORMAL"): 24,
    ("PLAYER_COMPLAINT", "LOW"): 72,
    ("RG_FLAG", "CRITICAL"): 1,
    ("RG_FLAG", "HIGH"): 4,
    ("RG_FLAG", "NORMAL"): 8,
    ("RG_FLAG", "LOW"): 24,
    ("CHARGEBACK", "CRITICAL"): 4,
    ("CHARGEBACK", "HIGH"): 8,
    ("CHARGEBACK", "NORMAL"): 24,
    ("CHARGEBACK", "LOW"): 48,
    ("SELF_EXCLUSION_BREACH", "CRITICAL"): 1,
    ("SELF_EXCLUSION_BREACH", "HIGH"): 2,
    ("SELF_EXCLUSION_BREACH", "NORMAL"): 4,
    ("SELF_EXCLUSION_BREACH", "LOW"): 8,
}

_DEFAULT_SLA_HOURS = 24


def _get_sla_hours(case_type: str, priority: str) -> int:
    return _SLA_HOURS.get((case_type, priority), _DEFAULT_SLA_HOURS)


# ---------------------------------------------------------------------------
# Audit helper
# ---------------------------------------------------------------------------

def _audit(case: Case, event: str, *, actor: str = "system", notes: str = "") -> None:
    case.audit_trail.append({
        "event": event,
        "actor": actor,
        "notes": notes,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    case.updated_at = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Case lifecycle operations
# ---------------------------------------------------------------------------

def create_case(case_type: CaseType, subject_id: str, title: str, *,
                description: str = "", priority: CasePriority = CasePriority.NORMAL,
                trigger_source: str = "", trigger_reference: str = "",
                assigned_team: str = "", jurisdiction: str = "",
                created_by: str = "system",
                metadata: dict[str, Any] | None = None) -> Case:
    """Create a new case with SLA deadline computed from type + priority."""
    sla_hours = _get_sla_hours(case_type.value, priority.value)
    case = Case(
        case_type=case_type,
        subject_id=subject_id,
        title=title,
        description=description,
        priority=priority,
        trigger_source=trigger_source,
        trigger_reference=trigger_reference,
        assigned_team=assigned_team,
        jurisdiction=jurisdiction,
        sla_hours=sla_hours,
        sla_deadline=datetime.now(timezone.utc) + timedelta(hours=sla_hours),
        created_by=created_by,
        metadata=metadata or {},
    )
    _audit(case, "case_created", actor=created_by)
    logger.info("Case %s created: %s [%s/%s]", case.case_id, title, case_type.value, priority.value)
    return case


def assign_case(case: Case, assignee: str, *, team: str = "",
                actor: str = "system") -> Case:
    """Assign (or reassign) a case to a specific agent."""
    if case.status in (CaseStatus.RESOLVED, CaseStatus.CLOSED):
        raise ValueError(f"Cannot assign case in status '{case.status}'")
    old_assignee = case.assigned_to
    case.assigned_to = assignee
    if team:
        case.assigned_team = team
    if case.status == CaseStatus.OPEN:
        case.status = CaseStatus.IN_PROGRESS
    _audit(case, "case_assigned", actor=actor,
           notes=f"Assigned to {assignee} (was {old_assignee})")
    return case


def add_note(case: Case, author: str, content: str, *,
             is_internal: bool = True) -> Case:
    """Add a note to a case."""
    note = CaseNote(author=author, content=content, is_internal=is_internal)
    case.notes.append(note)
    _audit(case, "note_added", actor=author, notes=f"internal={is_internal}")
    return case


def add_evidence(case: Case, evidence_type: str, reference: str, *,
                 description: str = "", collected_by: str = "system") -> Case:
    """Attach evidence to a case."""
    ev = CaseEvidence(
        evidence_type=evidence_type,
        reference=reference,
        description=description,
        collected_by=collected_by,
    )
    case.evidence.append(ev)
    _audit(case, "evidence_added", actor=collected_by,
           notes=f"{evidence_type}: {reference}")
    return case


def escalate_case(case: Case, *, reason: str = "", actor: str = "system") -> Case:
    """Escalate a case to higher authority."""
    if case.status in (CaseStatus.RESOLVED, CaseStatus.CLOSED):
        raise ValueError(f"Cannot escalate case in status '{case.status}'")
    case.status = CaseStatus.ESCALATED
    _audit(case, "case_escalated", actor=actor, notes=reason)
    return case


def resolve_case(case: Case, resolution: Resolution, *, notes: str = "",
                 resolved_by: str = "system") -> Case:
    """Resolve a case with a specific resolution."""
    if case.status in (CaseStatus.RESOLVED, CaseStatus.CLOSED):
        raise ValueError(f"Cannot resolve case in status '{case.status}'")
    case.status = CaseStatus.RESOLVED
    case.resolution = resolution
    case.resolution_notes = notes
    case.resolved_by = resolved_by
    case.resolved_at = datetime.now(timezone.utc)
    _audit(case, "case_resolved", actor=resolved_by,
           notes=f"Resolution: {resolution.value} - {notes}")
    return case


def close_case(case: Case, *, actor: str = "system") -> Case:
    """Close a resolved case (final state)."""
    if case.status != CaseStatus.RESOLVED:
        raise ValueError("Only resolved cases can be closed")
    case.status = CaseStatus.CLOSED
    case.closed_at = datetime.now(timezone.utc)
    _audit(case, "case_closed", actor=actor)
    return case


def check_sla_status(case: Case) -> dict[str, Any]:
    """Check whether a case is within its SLA."""
    now = datetime.now(timezone.utc)
    result: dict[str, Any] = {
        "case_id": case.case_id,
        "sla_hours": case.sla_hours,
        "status": case.status,
    }
    if case.status in (CaseStatus.RESOLVED, CaseStatus.CLOSED):
        resolved_within = case.resolved_at and case.sla_deadline and case.resolved_at <= case.sla_deadline
        result["sla_met"] = bool(resolved_within)
        result["resolved_at"] = case.resolved_at.isoformat() if case.resolved_at else None
    elif case.sla_deadline:
        result["sla_met"] = now <= case.sla_deadline
        if not result["sla_met"]:
            overdue = now - case.sla_deadline
            result["overdue_hours"] = round(overdue.total_seconds() / 3600, 1)
        remaining = case.sla_deadline - now
        result["remaining_hours"] = max(0, round(remaining.total_seconds() / 3600, 1))
        result["deadline"] = case.sla_deadline.isoformat()
    else:
        result["sla_met"] = True  # no deadline set
    return result
