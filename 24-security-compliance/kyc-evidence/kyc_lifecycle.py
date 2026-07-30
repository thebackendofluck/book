#!/usr/bin/env python3
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
KYC Evidence Lifecycle Service — full state machine for KYC document management.

State machine:
    PENDING -> DOCUMENTS_REQUESTED -> SUBMITTED -> UNDER_REVIEW
    UNDER_REVIEW -> APPROVED | REJECTED
    REJECTED -> DOCUMENTS_REQUESTED  (re-upload)
    APPROVED -> EXPIRED              (periodic re-check)
    APPROVED -> ENHANCED_DUE_DILIGENCE (high-value trigger)
    Any -> SUSPENDED                 (compliance override)

Retention policies:
    - NJ (US): 7 years post-account closure  (NJAC 13:69O)
    - UK (GB): 5 years post-relationship end  (UKGC LCCP 17.1.1)
    - Malta (MT): 5 years                     (MGA Player Protection Directive)
    - Brazil (BR): 10 years                   (Lei 14.790/2023 Art. 30)
    - Default: 5 years

Document types:
    - IDENTITY: passport, driving licence, national ID
    - ADDRESS: utility bill, bank statement, council tax (< 3 months)
    - SOURCE_OF_FUNDS: payslip, tax return, bank statements (> 3 months)
    - PEP_CHECK: politically exposed person screening result
    - ENHANCED_DD: additional documentation for high-risk players

Access control levels:
    - AGENT: can view document metadata, trigger re-upload
    - REVIEWER: can view documents, approve/reject
    - COMPLIANCE_OFFICER: full access, can suspend, export
    - SYSTEM: automated transitions (expiry, EDD trigger)

Script reference for Chapter 24d.
"""

import hashlib
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class KYCState(str, Enum):
    PENDING = "PENDING"
    DOCUMENTS_REQUESTED = "DOCUMENTS_REQUESTED"
    SUBMITTED = "SUBMITTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    ENHANCED_DUE_DILIGENCE = "ENHANCED_DUE_DILIGENCE"
    SUSPENDED = "SUSPENDED"


class DocumentType(str, Enum):
    IDENTITY = "IDENTITY"
    ADDRESS = "ADDRESS"
    SOURCE_OF_FUNDS = "SOURCE_OF_FUNDS"
    PEP_CHECK = "PEP_CHECK"
    ENHANCED_DD = "ENHANCED_DD"


class AccessLevel(str, Enum):
    AGENT = "AGENT"
    REVIEWER = "REVIEWER"
    COMPLIANCE_OFFICER = "COMPLIANCE_OFFICER"
    SYSTEM = "SYSTEM"


class ReviewDecision(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    ESCALATE = "ESCALATE"
    REQUEST_MORE_INFO = "REQUEST_MORE_INFO"


# ---------------------------------------------------------------------------
# Valid transitions
# ---------------------------------------------------------------------------

VALID_TRANSITIONS: dict[KYCState, set[KYCState]] = {
    KYCState.PENDING: {KYCState.DOCUMENTS_REQUESTED, KYCState.SUSPENDED},
    KYCState.DOCUMENTS_REQUESTED: {KYCState.SUBMITTED, KYCState.SUSPENDED},
    KYCState.SUBMITTED: {KYCState.UNDER_REVIEW, KYCState.SUSPENDED},
    KYCState.UNDER_REVIEW: {
        KYCState.APPROVED,
        KYCState.REJECTED,
        KYCState.ENHANCED_DUE_DILIGENCE,
        KYCState.SUSPENDED,
    },
    KYCState.APPROVED: {
        KYCState.EXPIRED,
        KYCState.ENHANCED_DUE_DILIGENCE,
        KYCState.SUSPENDED,
    },
    KYCState.REJECTED: {KYCState.DOCUMENTS_REQUESTED, KYCState.SUSPENDED},
    KYCState.EXPIRED: {KYCState.DOCUMENTS_REQUESTED, KYCState.SUSPENDED},
    KYCState.ENHANCED_DUE_DILIGENCE: {
        KYCState.UNDER_REVIEW,
        KYCState.SUSPENDED,
    },
    KYCState.SUSPENDED: {KYCState.UNDER_REVIEW},
}


# ---------------------------------------------------------------------------
# Retention policies per jurisdiction
# ---------------------------------------------------------------------------

RETENTION_YEARS: dict[str, int] = {
    "US-NJ": 7,
    "US-PA": 7,
    "US-MI": 7,
    "GB": 5,
    "MT": 5,
    "SE": 5,
    "DK": 5,
    "BR": 10,
    "DEFAULT": 5,
}


def retention_for_jurisdiction(jurisdiction: str) -> int:
    """Return retention period in years for a given jurisdiction code."""
    return RETENTION_YEARS.get(jurisdiction, RETENTION_YEARS["DEFAULT"])


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class KYCDocument:
    document_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    case_id: str = ""
    player_id: str = ""
    document_type: str = DocumentType.IDENTITY.value
    filename: str = ""
    content_hash: str = ""
    encrypted_ref: str = ""  # reference to encrypted blob in evidence store
    uploaded_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    expires_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AuditEntry:
    entry_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    case_id: str = ""
    actor: str = ""
    actor_role: str = ""
    action: str = ""
    from_state: str = ""
    to_state: str = ""
    reason: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    ip_address: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReviewAssignment:
    assignment_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    case_id: str = ""
    reviewer_id: str = ""
    assigned_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    completed_at: str = ""
    decision: str = ""
    reason: str = ""
    risk_score: float = 0.0


@dataclass
class KYCCase:
    case_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    player_id: str = ""
    jurisdiction: str = ""
    state: str = KYCState.PENDING.value
    trigger: str = "REGISTRATION"  # REGISTRATION, PERIODIC, THRESHOLD, MANUAL
    risk_level: str = "STANDARD"   # STANDARD, HIGH, PEP
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    retention_until: str = ""
    documents: list[KYCDocument] = field(default_factory=list)
    audit_trail: list[AuditEntry] = field(default_factory=list)
    reviews: list[ReviewAssignment] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# KYC Lifecycle Service
# ---------------------------------------------------------------------------

class KYCLifecycleService:
    """
    Manages KYC case lifecycle, document collection, reviewer workflows,
    and audit trails for multi-jurisdiction online casino compliance.
    """

    def __init__(
        self,
        evidence_store: Any = None,
        notification_service: Any = None,
        redis_client: Any = None,
    ):
        self.evidence_store = evidence_store
        self.notifications = notification_service
        self.redis = redis_client
        self._cases: dict[str, KYCCase] = {}

    # ── Case creation ──────────────────────────────────────

    def create_case(
        self,
        player_id: str,
        jurisdiction: str,
        trigger: str = "REGISTRATION",
        risk_level: str = "STANDARD",
        actor: str = "SYSTEM",
        actor_role: str = AccessLevel.SYSTEM.value,
    ) -> KYCCase:
        """Create a new KYC case and log the creation event."""
        case = KYCCase(
            player_id=player_id,
            jurisdiction=jurisdiction,
            trigger=trigger,
            risk_level=risk_level,
        )

        # Compute retention date
        years = retention_for_jurisdiction(jurisdiction)
        retention_date = datetime.now(timezone.utc) + timedelta(days=years * 365)
        case.retention_until = retention_date.isoformat()

        # Audit entry
        entry = AuditEntry(
            case_id=case.case_id,
            actor=actor,
            actor_role=actor_role,
            action="CASE_CREATED",
            from_state="",
            to_state=KYCState.PENDING.value,
            reason=f"KYC triggered by {trigger}",
        )
        case.audit_trail.append(entry)

        self._cases[case.case_id] = case
        logger.info(
            "KYC case created: case_id=%s player=%s jurisdiction=%s",
            case.case_id, player_id, jurisdiction,
        )
        return case

    # ── State transitions ──────────────────────────────────

    def transition(
        self,
        case_id: str,
        new_state: KYCState,
        actor: str,
        actor_role: str,
        reason: str = "",
        details: Optional[dict[str, Any]] = None,
    ) -> KYCCase:
        """
        Attempt a state transition. Raises ValueError if the transition
        is not permitted by the state machine.
        """
        case = self._get_case(case_id)
        current = KYCState(case.state)

        if new_state not in VALID_TRANSITIONS.get(current, set()):
            raise ValueError(
                f"Invalid transition: {current.value} -> {new_state.value} "
                f"(case={case_id})"
            )

        # Access control check
        self._check_transition_permission(actor_role, current, new_state)

        old_state = case.state
        case.state = new_state.value
        case.updated_at = datetime.now(timezone.utc).isoformat()

        # Audit
        entry = AuditEntry(
            case_id=case_id,
            actor=actor,
            actor_role=actor_role,
            action="STATE_TRANSITION",
            from_state=old_state,
            to_state=new_state.value,
            reason=reason,
            details=details or {},
        )
        case.audit_trail.append(entry)

        logger.info(
            "KYC transition: case=%s %s -> %s by %s (%s)",
            case_id, old_state, new_state.value, actor, reason,
        )
        return case

    # ── Reviewer workflow ──────────────────────────────────

    def assign_reviewer(
        self,
        case_id: str,
        reviewer_id: str,
        actor: str,
        actor_role: str,
    ) -> ReviewAssignment:
        """Assign a reviewer to a case currently in SUBMITTED or UNDER_REVIEW."""
        case = self._get_case(case_id)
        if case.state not in (
            KYCState.SUBMITTED.value,
            KYCState.UNDER_REVIEW.value,
        ):
            raise ValueError(
                f"Cannot assign reviewer in state {case.state}"
            )

        assignment = ReviewAssignment(
            case_id=case_id,
            reviewer_id=reviewer_id,
        )
        case.reviews.append(assignment)

        # Auto-transition to UNDER_REVIEW if SUBMITTED
        if case.state == KYCState.SUBMITTED.value:
            self.transition(
                case_id, KYCState.UNDER_REVIEW,
                actor=actor, actor_role=actor_role,
                reason=f"Reviewer {reviewer_id} assigned",
            )

        entry = AuditEntry(
            case_id=case_id,
            actor=actor,
            actor_role=actor_role,
            action="REVIEWER_ASSIGNED",
            reason=f"Assigned to {reviewer_id}",
        )
        case.audit_trail.append(entry)

        logger.info(
            "Reviewer assigned: case=%s reviewer=%s", case_id, reviewer_id,
        )
        return assignment

    def submit_review(
        self,
        case_id: str,
        reviewer_id: str,
        decision: ReviewDecision,
        reason: str,
        risk_score: float = 0.0,
    ) -> KYCCase:
        """Submit a review decision for a case."""
        case = self._get_case(case_id)
        if case.state != KYCState.UNDER_REVIEW.value:
            raise ValueError(
                f"Cannot submit review in state {case.state}"
            )

        # Find the active assignment
        assignment = self._find_active_assignment(case, reviewer_id)
        if not assignment:
            raise ValueError(
                f"No active assignment for reviewer {reviewer_id} on case {case_id}"
            )

        assignment.completed_at = datetime.now(timezone.utc).isoformat()
        assignment.decision = decision.value
        assignment.reason = reason
        assignment.risk_score = risk_score

        # Map decision to state transition
        state_map = {
            ReviewDecision.APPROVE: KYCState.APPROVED,
            ReviewDecision.REJECT: KYCState.REJECTED,
            ReviewDecision.ESCALATE: KYCState.ENHANCED_DUE_DILIGENCE,
        }

        target_state = state_map.get(decision)
        if target_state:
            self.transition(
                case_id, target_state,
                actor=reviewer_id,
                actor_role=AccessLevel.REVIEWER.value,
                reason=reason,
                details={"risk_score": risk_score},
            )

        return case

    # ── Document management ────────────────────────────────

    def add_document(
        self,
        case_id: str,
        document_type: DocumentType,
        filename: str,
        content: bytes,
        actor: str,
        actor_role: str,
    ) -> KYCDocument:
        """
        Add a document to a KYC case. Computes hash, stores encrypted
        reference, logs access.
        """
        case = self._get_case(case_id)
        if case.state not in (
            KYCState.DOCUMENTS_REQUESTED.value,
            KYCState.ENHANCED_DUE_DILIGENCE.value,
        ):
            raise ValueError(
                f"Cannot add documents in state {case.state}"
            )

        content_hash = hashlib.sha256(content).hexdigest()

        # Store encrypted in evidence store
        encrypted_ref = ""
        if self.evidence_store:
            encrypted_ref = self.evidence_store.store(
                case_id=case_id,
                document_type=document_type.value,
                content=content,
                retention_years=retention_for_jurisdiction(case.jurisdiction),
            )

        doc = KYCDocument(
            case_id=case_id,
            player_id=case.player_id,
            document_type=document_type.value,
            filename=filename,
            content_hash=content_hash,
            encrypted_ref=encrypted_ref,
        )
        case.documents.append(doc)

        # Audit
        entry = AuditEntry(
            case_id=case_id,
            actor=actor,
            actor_role=actor_role,
            action="DOCUMENT_UPLOADED",
            reason=f"{document_type.value}: {filename}",
            details={
                "document_id": doc.document_id,
                "content_hash": content_hash,
                "size_bytes": len(content),
            },
        )
        case.audit_trail.append(entry)

        logger.info(
            "Document added: case=%s type=%s file=%s hash=%s",
            case_id, document_type.value, filename, content_hash[:12],
        )
        return doc

    # ── Expiry and re-verification ─────────────────────────

    def check_expiry(self, case_id: str) -> bool:
        """
        Check if an approved KYC case has passed its re-verification
        window. UK requires annual re-check; NJ on activity triggers.
        """
        case = self._get_case(case_id)
        if case.state != KYCState.APPROVED.value:
            return False

        approved_at = self._last_approval_time(case)
        if not approved_at:
            return False

        now = datetime.now(timezone.utc)
        age_days = (now - approved_at).days

        # Jurisdiction-specific re-verification windows
        reverify_days = {
            "GB": 365,      # UK annual re-check
            "US-NJ": 730,   # NJ every 2 years
            "US-PA": 730,
            "US-MI": 730,
            "MT": 365,      # Malta annual
            "BR": 365,      # Brazil annual
        }
        threshold = reverify_days.get(case.jurisdiction, 365)

        if age_days >= threshold:
            self.transition(
                case_id, KYCState.EXPIRED,
                actor="SYSTEM", actor_role=AccessLevel.SYSTEM.value,
                reason=f"Re-verification due ({age_days} days since approval)",
            )
            return True
        return False

    # ── Regulatory export ──────────────────────────────────

    def export_for_regulator(
        self,
        case_id: str,
        actor: str,
        actor_role: str,
        export_format: str = "json",
    ) -> dict[str, Any]:
        """
        Export a KYC case with full audit trail for regulatory request.
        Only COMPLIANCE_OFFICER role can perform this action.
        """
        if actor_role != AccessLevel.COMPLIANCE_OFFICER.value:
            raise PermissionError(
                f"Only COMPLIANCE_OFFICER can export; got {actor_role}"
            )

        case = self._get_case(case_id)

        # Log the export itself
        entry = AuditEntry(
            case_id=case_id,
            actor=actor,
            actor_role=actor_role,
            action="REGULATORY_EXPORT",
            reason="Regulator data request",
            details={"format": export_format},
        )
        case.audit_trail.append(entry)

        export = {
            "export_id": str(uuid.uuid4()),
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "case": asdict(case),
            "jurisdiction": case.jurisdiction,
            "retention_policy_years": retention_for_jurisdiction(
                case.jurisdiction
            ),
            "document_count": len(case.documents),
            "audit_entry_count": len(case.audit_trail),
            "review_count": len(case.reviews),
        }

        logger.info(
            "Regulatory export: case=%s by=%s docs=%d audit_entries=%d",
            case_id, actor, len(case.documents), len(case.audit_trail),
        )
        return export

    # ── Bulk operations ────────────────────────────────────

    def find_expired_cases(self, jurisdiction: Optional[str] = None) -> list[str]:
        """Return case IDs for approved cases past their re-verification window."""
        expired = []
        for case_id, case in self._cases.items():
            if jurisdiction and case.jurisdiction != jurisdiction:
                continue
            if case.state == KYCState.APPROVED.value:
                if self.check_expiry(case_id):
                    expired.append(case_id)
        return expired

    def retention_report(self) -> list[dict[str, Any]]:
        """Generate a retention compliance report for all cases."""
        now = datetime.now(timezone.utc)
        report = []
        for case_id, case in self._cases.items():
            if not case.retention_until:
                continue
            retention_date = datetime.fromisoformat(case.retention_until)
            days_remaining = (retention_date - now).days
            report.append({
                "case_id": case_id,
                "player_id": case.player_id,
                "jurisdiction": case.jurisdiction,
                "state": case.state,
                "retention_until": case.retention_until,
                "days_remaining": days_remaining,
                "document_count": len(case.documents),
                "compliant": days_remaining > 0,
            })
        return report

    # ── PII access logging ─────────────────────────────────

    def view_document(
        self,
        case_id: str,
        document_id: str,
        actor: str,
        actor_role: str,
    ) -> Optional[KYCDocument]:
        """
        Retrieve a document reference with access logging.
        AGENT can see metadata only; REVIEWER+ can see content ref.
        """
        case = self._get_case(case_id)

        doc = next(
            (d for d in case.documents if d.document_id == document_id),
            None,
        )
        if not doc:
            return None

        # Log access regardless of role
        entry = AuditEntry(
            case_id=case_id,
            actor=actor,
            actor_role=actor_role,
            action="DOCUMENT_VIEWED",
            details={"document_id": document_id},
        )
        case.audit_trail.append(entry)

        # AGENT gets metadata only — strip encrypted ref
        if actor_role == AccessLevel.AGENT.value:
            redacted = KYCDocument(
                document_id=doc.document_id,
                case_id=doc.case_id,
                player_id="[REDACTED]",
                document_type=doc.document_type,
                filename=doc.filename,
                content_hash=doc.content_hash,
                encrypted_ref="[ACCESS_RESTRICTED]",
                uploaded_at=doc.uploaded_at,
            )
            return redacted

        return doc

    # ── Internal helpers ───────────────────────────────────

    def _get_case(self, case_id: str) -> KYCCase:
        case = self._cases.get(case_id)
        if not case:
            raise KeyError(f"KYC case not found: {case_id}")
        return case

    def _check_transition_permission(
        self, actor_role: str, current: KYCState, target: KYCState
    ) -> None:
        """Enforce role-based transition permissions."""
        # SYSTEM can do automated transitions
        if actor_role == AccessLevel.SYSTEM.value:
            if target in (KYCState.EXPIRED, KYCState.ENHANCED_DUE_DILIGENCE):
                return
            raise PermissionError(
                f"SYSTEM cannot perform {current.value} -> {target.value}"
            )

        # Only COMPLIANCE_OFFICER can suspend
        if target == KYCState.SUSPENDED:
            if actor_role != AccessLevel.COMPLIANCE_OFFICER.value:
                raise PermissionError(
                    "Only COMPLIANCE_OFFICER can suspend a KYC case"
                )

        # REVIEWER can approve/reject
        if target in (KYCState.APPROVED, KYCState.REJECTED):
            if actor_role not in (
                AccessLevel.REVIEWER.value,
                AccessLevel.COMPLIANCE_OFFICER.value,
            ):
                raise PermissionError(
                    f"{actor_role} cannot perform {target.value}"
                )

    def _find_active_assignment(
        self, case: KYCCase, reviewer_id: str
    ) -> Optional[ReviewAssignment]:
        for a in reversed(case.reviews):
            if a.reviewer_id == reviewer_id and not a.completed_at:
                return a
        return None

    def _last_approval_time(self, case: KYCCase) -> Optional[datetime]:
        for entry in reversed(case.audit_trail):
            if entry.to_state == KYCState.APPROVED.value:
                return datetime.fromisoformat(entry.timestamp)
        return None

    def get_case(self, case_id: str) -> KYCCase:
        """Public accessor for a KYC case."""
        return self._get_case(case_id)
