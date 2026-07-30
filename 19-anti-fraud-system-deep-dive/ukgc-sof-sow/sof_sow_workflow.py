# Companion code for "The Backend of Luck" - Chapter 19, Anti-Fraud System Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
sof_sow_workflow.py — UKGC Source of Funds / Source of Wealth workflow.

Jurisdiction:       United Kingdom
Regulator:          UK Gambling Commission (UKGC)
Regulation refs:
  - LCCP Social Responsibility Code Provision 3.4.1 (Customer Interaction)
    https://www.gamblingcommission.gov.uk/licensees-and-businesses/lccp/online/
    social-responsibility-code-provisions/3-4-1-customer-interaction
  - LCCP Ordinary Code Provision 2.1.1 (Financial Crime)
    https://www.gamblingcommission.gov.uk/licensees-and-businesses/lccp
  - UKGC Guidance: Customer interaction — financial vulnerability checks (2024)
    https://www.gamblingcommission.gov.uk/licensees-and-businesses/guide/
    customer-interaction/financial-vulnerability
  - UKGC Guidance: Source of funds and source of wealth checks (2025)
    https://www.gamblingcommission.gov.uk/licensees-and-businesses/guide/
    page/source-of-funds
Penalties:
  - Licence revocation or suspension
  - Financial penalties (no statutory cap; recent cases: £3m–£19m)
  - Public statements of non-compliance
  - Potential personal liability for senior executives

Thresholds (effective from February 2026 LCCP phase-in):
  - SoF trigger:  net loss ≥ £125 in a single session
  - SoW trigger:  net loss ≥ £500 rolling 30 days
  - Both thresholds may be set lower by the operator's own risk appetite

Decision deadlines:
  - Low-risk accounts:     24 hours to complete SoF review
  - Enhanced-risk accounts: 72 hours to complete SoW review
  - Account restricted if deadline not met

Book chapter:  Chapter 19 — Anti-Fraud & Compliance Systems
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

import structlog

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Thresholds (LCCP SR Code 3.4.1 — February 2026 phase-in)
# ---------------------------------------------------------------------------

SOF_SESSION_LOSS_TRIGGER_GBP: Decimal = Decimal("125.00")
SOW_ROLLING_30D_LOSS_TRIGGER_GBP: Decimal = Decimal("500.00")

SOF_DECISION_HOURS: int = 24
SOW_DECISION_HOURS: int = 72

# Document categories accepted for SoF / SoW verification
_SOF_DOCUMENTS = [
    "bank_statement_3_months",
    "payslip_last_3",
    "p60_or_p45",
    "hmrc_self_assessment",
    "benefits_award_letter",
    "pension_statement",
    "investment_account_statement",
]

_SOW_DOCUMENTS = [
    "bank_statement_6_months",
    "p60_last_2_years",
    "hmrc_self_assessment_last_2",
    "company_accounts",
    "inheritance_documentation",
    "property_sale_proceeds",
    "redundancy_settlement",
    "cryptocurrency_wallet_history",
]


# ---------------------------------------------------------------------------
# Domain enumerations
# ---------------------------------------------------------------------------

class CheckLevel(str, Enum):
    SOF = "source_of_funds"        # single-session £125 trigger
    SOW = "source_of_wealth"       # rolling 30-day £500 trigger


class CheckStatus(str, Enum):
    PENDING = "pending"            # check triggered, awaiting documents
    DOCUMENTS_REQUESTED = "documents_requested"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    DECLINED = "declined"
    TIMED_OUT = "timed_out"        # player did not respond in time
    RESTRICTED = "restricted"      # account locked pending resolution


class DocumentStatus(str, Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    OCR_PASS = "ocr_pass"
    OCR_FAIL = "ocr_fail"
    MANUALLY_APPROVED = "manually_approved"
    REJECTED = "rejected"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class PlayerSession:
    """Represents a completed gambling session for threshold evaluation."""
    session_id: str
    player_id: str
    started_at: datetime
    ended_at: datetime
    gross_deposits_gbp: Decimal
    gross_withdrawals_gbp: Decimal

    @property
    def net_loss_gbp(self) -> Decimal:
        """Positive value = player lost money."""
        return self.gross_deposits_gbp - self.gross_withdrawals_gbp


@dataclass
class SoFSoWCheck:
    """State machine for a single Source of Funds / Source of Wealth check."""
    check_id: str
    player_id: str
    level: CheckLevel
    triggered_by_session_id: Optional[str]
    triggered_at: datetime
    deadline_at: datetime
    status: CheckStatus = CheckStatus.PENDING
    documents_requested: list[str] = field(default_factory=list)
    documents_received: list[str] = field(default_factory=list)
    reviewer_notes: Optional[str] = None
    resolved_at: Optional[datetime] = None
    audit_trail: list[dict[str, Any]] = field(default_factory=list)

    def record_event(self, event: str, detail: dict[str, Any] | None = None) -> None:
        """Append an immutable event to the audit trail for UKGC inspection."""
        self.audit_trail.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "detail": detail or {},
        })


@dataclass
class DocumentRequest:
    """A specific document submitted by a player."""
    request_id: str
    check_id: str
    player_id: str
    document_type: str
    submitted_at: Optional[datetime] = None
    ocr_confidence: Optional[float] = None    # 0.0–1.0
    extracted_income_gbp: Optional[Decimal] = None
    status: DocumentStatus = DocumentStatus.PENDING


@dataclass
class ThresholdEvaluationResult:
    """Result of evaluating session / rolling-period thresholds."""
    player_id: str
    session_id: Optional[str]
    sof_triggered: bool
    sow_triggered: bool
    session_loss_gbp: Decimal
    rolling_30d_loss_gbp: Decimal
    evaluated_at: datetime


# ---------------------------------------------------------------------------
# Threshold evaluator
# ---------------------------------------------------------------------------

class ThresholdEvaluator:
    """
    Evaluates whether a completed session (or rolling 30-day period) crosses
    the UKGC-mandated Source of Funds or Source of Wealth trigger thresholds.

    Production implementations must query the operator's data warehouse for
    rolling 30-day aggregates.  The `_fetch_rolling_loss` method is a stub.
    """

    def evaluate_session(
        self,
        session: PlayerSession,
        rolling_30d_loss_gbp: Decimal,
    ) -> ThresholdEvaluationResult:
        sof_triggered = session.net_loss_gbp >= SOF_SESSION_LOSS_TRIGGER_GBP
        sow_triggered = rolling_30d_loss_gbp >= SOW_ROLLING_30D_LOSS_TRIGGER_GBP

        result = ThresholdEvaluationResult(
            player_id=session.player_id,
            session_id=session.session_id,
            sof_triggered=sof_triggered,
            sow_triggered=sow_triggered,
            session_loss_gbp=session.net_loss_gbp,
            rolling_30d_loss_gbp=rolling_30d_loss_gbp,
            evaluated_at=datetime.now(timezone.utc),
        )

        if sof_triggered or sow_triggered:
            log.info(
                "ukgc_sof_sow: threshold breached",
                player_id=session.player_id,
                session_id=session.session_id,
                session_loss=str(session.net_loss_gbp),
                rolling_30d_loss=str(rolling_30d_loss_gbp),
                sof_triggered=sof_triggered,
                sow_triggered=sow_triggered,
            )

        return result


# ---------------------------------------------------------------------------
# Check factory
# ---------------------------------------------------------------------------

class SoFSoWWorkflow:
    """
    Orchestrates the full Source of Funds / Source of Wealth check lifecycle.

    Responsibilities:
      1. Determine check level (SoF vs SoW) and deadline
      2. Select required documents based on risk level
      3. Restrict account if deadline not met
      4. Record every state transition for UKGC audit trail
    """

    def initiate_check(
        self,
        player_id: str,
        level: CheckLevel,
        session_id: Optional[str] = None,
    ) -> SoFSoWCheck:
        """
        Open a new SoF or SoW check and return the check record.

        The calling system must persist this record and honour the deadline.
        """
        check_id = f"SOF-{uuid.uuid4().hex[:12].upper()}"
        now = datetime.now(timezone.utc)
        hours = SOF_DECISION_HOURS if level == CheckLevel.SOF else SOW_DECISION_HOURS

        docs = _SOF_DOCUMENTS if level == CheckLevel.SOF else _SOW_DOCUMENTS

        check = SoFSoWCheck(
            check_id=check_id,
            player_id=player_id,
            level=level,
            triggered_by_session_id=session_id,
            triggered_at=now,
            deadline_at=now + timedelta(hours=hours),
            status=CheckStatus.PENDING,
            documents_requested=docs,
        )
        check.record_event("check_initiated", {
            "level": level.value,
            "deadline_hours": hours,
            "documents_requested": docs,
        })
        log.info(
            "ukgc_sof_sow: check initiated",
            check_id=check_id,
            player_id=player_id,
            level=level.value,
            deadline_at=check.deadline_at.isoformat(),
        )
        return check

    def request_documents(self, check: SoFSoWCheck) -> list[DocumentRequest]:
        """
        Generate document-request records and transition check to
        DOCUMENTS_REQUESTED.  In production these are dispatched via
        the player-messaging service.
        """
        check.status = CheckStatus.DOCUMENTS_REQUESTED
        check.record_event("documents_requested", {
            "types": check.documents_requested,
        })

        requests = []
        for doc_type in check.documents_requested:
            requests.append(DocumentRequest(
                request_id=f"DOC-{uuid.uuid4().hex[:10].upper()}",
                check_id=check.check_id,
                player_id=check.player_id,
                document_type=doc_type,
            ))

        log.info(
            "ukgc_sof_sow: documents requested",
            check_id=check.check_id,
            count=len(requests),
        )
        return requests

    def receive_document(
        self,
        check: SoFSoWCheck,
        doc_request: DocumentRequest,
        raw_bytes: bytes,
    ) -> DocumentRequest:
        """
        Accept a submitted document, run OCR hint analysis, and update
        the document request record.

        OCR is stub-implemented here; production systems integrate with
        providers such as Onfido, Jumio, or AWS Textract.
        """
        doc_request.submitted_at = datetime.now(timezone.utc)
        doc_request.status = DocumentStatus.SUBMITTED

        # --- OCR stub -------------------------------------------------------
        ocr_result = self._run_ocr_hints(raw_bytes, doc_request.document_type)
        doc_request.ocr_confidence = ocr_result["confidence"]
        doc_request.extracted_income_gbp = ocr_result.get("extracted_income_gbp")

        if ocr_result["confidence"] >= 0.80:
            doc_request.status = DocumentStatus.OCR_PASS
        else:
            doc_request.status = DocumentStatus.OCR_FAIL
            log.warning(
                "ukgc_sof_sow: OCR confidence below threshold",
                check_id=check.check_id,
                doc_type=doc_request.document_type,
                confidence=ocr_result["confidence"],
            )
        # --------------------------------------------------------------------

        check.documents_received.append(doc_request.document_type)
        check.record_event("document_received", {
            "document_type": doc_request.document_type,
            "ocr_confidence": doc_request.ocr_confidence,
            "status": doc_request.status.value,
        })

        # Move to under_review once at least one document is received
        if check.status == CheckStatus.DOCUMENTS_REQUESTED:
            check.status = CheckStatus.UNDER_REVIEW

        return doc_request

    def enforce_deadline(self, check: SoFSoWCheck) -> bool:
        """
        Call periodically (e.g., every 15 minutes via a scheduler).

        Returns True if the account has been restricted due to non-compliance.
        """
        if check.status in (
            CheckStatus.APPROVED, CheckStatus.DECLINED, CheckStatus.RESTRICTED
        ):
            return False

        if datetime.now(timezone.utc) >= check.deadline_at:
            self._restrict_account(check)
            return True

        return False

    def approve_check(
        self,
        check: SoFSoWCheck,
        reviewer_id: str,
        notes: str,
    ) -> SoFSoWCheck:
        """Mark a check as approved after satisfactory document review."""
        check.status = CheckStatus.APPROVED
        check.reviewer_notes = notes
        check.resolved_at = datetime.now(timezone.utc)
        check.record_event("check_approved", {
            "reviewer_id": reviewer_id,
            "notes": notes,
        })
        log.info("ukgc_sof_sow: check approved",
                 check_id=check.check_id, player_id=check.player_id)
        return check

    def decline_check(
        self,
        check: SoFSoWCheck,
        reviewer_id: str,
        notes: str,
    ) -> SoFSoWCheck:
        """
        Mark a check as declined.  The operator must close the account or
        prevent further deposits as mandated by LCCP SR Code 3.4.1.
        """
        check.status = CheckStatus.DECLINED
        check.reviewer_notes = notes
        check.resolved_at = datetime.now(timezone.utc)
        check.record_event("check_declined", {
            "reviewer_id": reviewer_id,
            "notes": notes,
            "action": "account_restricted",
        })
        log.warning("ukgc_sof_sow: check declined — restricting account",
                    check_id=check.check_id, player_id=check.player_id)
        return check

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _restrict_account(self, check: SoFSoWCheck) -> None:
        """
        Apply account restriction.  Production systems call the account
        service to block deposits and active sessions.
        """
        check.status = CheckStatus.RESTRICTED
        check.record_event("account_restricted", {
            "reason": "sof_sow_deadline_not_met",
            "deadline_at": check.deadline_at.isoformat(),
        })
        log.warning(
            "ukgc_sof_sow: account restricted — deadline not met",
            check_id=check.check_id,
            player_id=check.player_id,
            deadline_at=check.deadline_at.isoformat(),
        )
        # TODO: call AccountService.restrict(check.player_id, reason="sof_sow")

    @staticmethod
    def _run_ocr_hints(raw_bytes: bytes, document_type: str) -> dict[str, Any]:
        """
        Stub OCR analysis.  Replace with real provider (Onfido / Jumio /
        AWS Textract).  Returns a dict with at minimum 'confidence' (0–1).
        """
        _ = hashlib.sha256(raw_bytes).hexdigest()  # ensure bytes are consumed
        # In production: call OCR provider API, parse fields, cross-validate
        return {
            "confidence": 0.92,          # stub: always passes
            "extracted_income_gbp": None,
            "document_type_detected": document_type,
        }


# ---------------------------------------------------------------------------
# End-to-end demonstration
# ---------------------------------------------------------------------------

def _demo() -> None:
    """Illustrate a complete SoW check triggered by a session loss."""
    evaluator = ThresholdEvaluator()
    workflow = SoFSoWWorkflow()

    # Simulate a session where the player lost £600 over 30 days
    session = PlayerSession(
        session_id="sess-abc123",
        player_id="player-uk-4567",
        started_at=datetime(2026, 4, 1, 20, 0, tzinfo=timezone.utc),
        ended_at=datetime(2026, 4, 1, 23, 0, tzinfo=timezone.utc),
        gross_deposits_gbp=Decimal("700.00"),
        gross_withdrawals_gbp=Decimal("100.00"),
    )

    rolling_30d = Decimal("600.00")
    result = evaluator.evaluate_session(session, rolling_30d)

    if result.sow_triggered:
        check = workflow.initiate_check(
            player_id=session.player_id,
            level=CheckLevel.SOW,
            session_id=session.session_id,
        )
        doc_requests = workflow.request_documents(check)
        print(f"Check {check.check_id} opened — {len(doc_requests)} documents requested")
        print(f"Deadline: {check.deadline_at.isoformat()}")
        print(f"Audit trail entries: {len(check.audit_trail)}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    _demo()
