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
Data Subject Request (DSR) Processor for iGaming Platforms.

Handles GDPR Article 17 (right to erasure), CCPA right to delete,
LGPD direito à eliminação, and equivalent rights under PDPA, PIPA,
and PIPEDA for the AcmetoCasino platform.

The core challenge in iGaming DSR processing is the tension between a
player's right to erasure and mandatory AML/tax retention obligations.
This processor implements selective erasure: PII is anonymised or deleted
while transaction records required for regulatory compliance are retained
under documented legal basis.

Usage:
    python dsr-processor.py --player-id P123456 --type deletion --jurisdiction EU
    python dsr-processor.py --player-id P123456 --type export --jurisdiction BR
    python dsr-processor.py --player-id P123456 --type rectification --jurisdiction EU
"""

import argparse
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("dsr_processor")


# ---------------------------------------------------------------------------
# Enums and constants
# ---------------------------------------------------------------------------


class DSRType(Enum):
    """Types of Data Subject Requests."""

    DELETION = "deletion"
    EXPORT = "export"
    RECTIFICATION = "rectification"
    RESTRICTION = "restriction"
    OBJECTION = "objection"


class DSRStatus(Enum):
    """Processing states of a DSR."""

    RECEIVED = "received"
    IDENTITY_VERIFIED = "identity_verified"
    LEGAL_ASSESSMENT = "legal_assessment"
    IN_PROGRESS = "in_progress"
    PARTIALLY_FULFILLED = "partially_fulfilled"
    FULFILLED = "fulfilled"
    REJECTED = "rejected"
    EXTENDED = "extended"  # GDPR allows 3-month extension for complex requests


class Jurisdiction(Enum):
    """Regulatory jurisdictions that govern the request."""

    EU = "EU"          # GDPR
    UK = "UK"          # UK GDPR + Data Protection Act 2018
    CA_US = "CA_US"    # CCPA / CPRA (California)
    BR = "BR"          # LGPD
    KR = "KR"          # PIPA (South Korea)
    SG = "SG"          # PDPA (Singapore)
    TH = "TH"          # PDPA (Thailand)
    CA = "CA"          # PIPEDA (Canada)
    OTHER = "OTHER"    # Best-effort erasure without specific statutory deadline


class DataCategory(Enum):
    """Classification of player data by regulatory treatment."""

    PII = "pii"                             # Name, email, DOB, address — erasable
    FINANCIAL = "financial"                 # Transactions — AML retention applies
    SPECIAL_CATEGORY = "special_category"   # Addiction indicators — GDPR Art. 9
    MARKETING_CONSENT = "marketing_consent" # Consent records — audit trail retained
    GAME_HISTORY = "game_history"           # Round results — anonymisable
    KYC_DOCUMENTS = "kyc_documents"         # ID docs — AML retention applies
    SESSION_LOGS = "session_logs"           # Login/activity — shorter retention
    SUPPORT_TICKETS = "support_tickets"     # CS history — anonymisable after hold


class RetentionBasis(Enum):
    """Legal basis for retaining data despite an erasure request."""

    AML = "aml"                         # Anti-Money Laundering directives
    TAX = "tax"                         # Tax authority requirements
    RESPONSIBLE_GAMBLING = "rg"         # Regulator-mandated self-exclusion records
    LEGAL_PROCEEDINGS = "legal"         # Ongoing dispute or investigation
    FRAUD_PREVENTION = "fraud"          # Legitimate interest, time-limited
    NONE = "none"                       # No retention basis — must be erased


# ---------------------------------------------------------------------------
# Retention schedule by jurisdiction
# ---------------------------------------------------------------------------

# Maps (jurisdiction, data_category) → (retention_years, basis)
RETENTION_SCHEDULE: dict[tuple[str, str], tuple[int, RetentionBasis]] = {
    # EU — GDPR + 4AMLD/5AMLD + national tax law
    ("EU", DataCategory.FINANCIAL.value): (5, RetentionBasis.AML),
    ("EU", DataCategory.KYC_DOCUMENTS.value): (5, RetentionBasis.AML),
    ("EU", DataCategory.GAME_HISTORY.value): (5, RetentionBasis.AML),
    ("EU", DataCategory.PII.value): (0, RetentionBasis.NONE),
    ("EU", DataCategory.SPECIAL_CATEGORY.value): (99, RetentionBasis.RESPONSIBLE_GAMBLING),
    ("EU", DataCategory.MARKETING_CONSENT.value): (99, RetentionBasis.AML),
    ("EU", DataCategory.SESSION_LOGS.value): (1, RetentionBasis.FRAUD_PREVENTION),
    ("EU", DataCategory.SUPPORT_TICKETS.value): (2, RetentionBasis.FRAUD_PREVENTION),
    # UK — same as EU post-Brexit
    ("UK", DataCategory.FINANCIAL.value): (5, RetentionBasis.AML),
    ("UK", DataCategory.KYC_DOCUMENTS.value): (5, RetentionBasis.AML),
    ("UK", DataCategory.GAME_HISTORY.value): (5, RetentionBasis.AML),
    ("UK", DataCategory.PII.value): (0, RetentionBasis.NONE),
    ("UK", DataCategory.SPECIAL_CATEGORY.value): (99, RetentionBasis.RESPONSIBLE_GAMBLING),
    ("UK", DataCategory.MARKETING_CONSENT.value): (99, RetentionBasis.AML),
    ("UK", DataCategory.SESSION_LOGS.value): (1, RetentionBasis.FRAUD_PREVENTION),
    ("UK", DataCategory.SUPPORT_TICKETS.value): (2, RetentionBasis.FRAUD_PREVENTION),
    # Brazil — LGPD + Bacen AML 3 years + CARF tax 5 years
    ("BR", DataCategory.FINANCIAL.value): (5, RetentionBasis.TAX),
    ("BR", DataCategory.KYC_DOCUMENTS.value): (5, RetentionBasis.AML),
    ("BR", DataCategory.GAME_HISTORY.value): (5, RetentionBasis.AML),
    ("BR", DataCategory.PII.value): (0, RetentionBasis.NONE),
    ("BR", DataCategory.SPECIAL_CATEGORY.value): (5, RetentionBasis.RESPONSIBLE_GAMBLING),
    ("BR", DataCategory.MARKETING_CONSENT.value): (5, RetentionBasis.AML),
    ("BR", DataCategory.SESSION_LOGS.value): (1, RetentionBasis.FRAUD_PREVENTION),
    ("BR", DataCategory.SUPPORT_TICKETS.value): (2, RetentionBasis.FRAUD_PREVENTION),
    # California — CCPA/CPRA + FinCEN AML 5 years
    ("CA_US", DataCategory.FINANCIAL.value): (5, RetentionBasis.AML),
    ("CA_US", DataCategory.KYC_DOCUMENTS.value): (5, RetentionBasis.AML),
    ("CA_US", DataCategory.GAME_HISTORY.value): (5, RetentionBasis.AML),
    ("CA_US", DataCategory.PII.value): (0, RetentionBasis.NONE),
    ("CA_US", DataCategory.SPECIAL_CATEGORY.value): (5, RetentionBasis.RESPONSIBLE_GAMBLING),
    ("CA_US", DataCategory.MARKETING_CONSENT.value): (99, RetentionBasis.AML),
    ("CA_US", DataCategory.SESSION_LOGS.value): (1, RetentionBasis.FRAUD_PREVENTION),
    ("CA_US", DataCategory.SUPPORT_TICKETS.value): (2, RetentionBasis.FRAUD_PREVENTION),
    # Canada — PIPEDA + FINTRAC 5 years
    ("CA", DataCategory.FINANCIAL.value): (5, RetentionBasis.AML),
    ("CA", DataCategory.KYC_DOCUMENTS.value): (5, RetentionBasis.AML),
    ("CA", DataCategory.GAME_HISTORY.value): (5, RetentionBasis.AML),
    ("CA", DataCategory.PII.value): (0, RetentionBasis.NONE),
    ("CA", DataCategory.SPECIAL_CATEGORY.value): (5, RetentionBasis.RESPONSIBLE_GAMBLING),
    ("CA", DataCategory.MARKETING_CONSENT.value): (5, RetentionBasis.AML),
    ("CA", DataCategory.SESSION_LOGS.value): (1, RetentionBasis.FRAUD_PREVENTION),
    ("CA", DataCategory.SUPPORT_TICKETS.value): (2, RetentionBasis.FRAUD_PREVENTION),
    # South Korea — PIPA + Act on Reporting and Using Specified Financial Information
    ("KR", DataCategory.FINANCIAL.value): (5, RetentionBasis.AML),
    ("KR", DataCategory.KYC_DOCUMENTS.value): (5, RetentionBasis.AML),
    ("KR", DataCategory.GAME_HISTORY.value): (5, RetentionBasis.AML),
    ("KR", DataCategory.PII.value): (0, RetentionBasis.NONE),
    ("KR", DataCategory.SPECIAL_CATEGORY.value): (3, RetentionBasis.RESPONSIBLE_GAMBLING),
    ("KR", DataCategory.MARKETING_CONSENT.value): (3, RetentionBasis.AML),
    ("KR", DataCategory.SESSION_LOGS.value): (1, RetentionBasis.FRAUD_PREVENTION),
    ("KR", DataCategory.SUPPORT_TICKETS.value): (3, RetentionBasis.FRAUD_PREVENTION),
    # Singapore — PDPA + MAS AML Notice
    ("SG", DataCategory.FINANCIAL.value): (5, RetentionBasis.AML),
    ("SG", DataCategory.KYC_DOCUMENTS.value): (5, RetentionBasis.AML),
    ("SG", DataCategory.GAME_HISTORY.value): (5, RetentionBasis.AML),
    ("SG", DataCategory.PII.value): (0, RetentionBasis.NONE),
    ("SG", DataCategory.SPECIAL_CATEGORY.value): (5, RetentionBasis.RESPONSIBLE_GAMBLING),
    ("SG", DataCategory.MARKETING_CONSENT.value): (5, RetentionBasis.AML),
    ("SG", DataCategory.SESSION_LOGS.value): (1, RetentionBasis.FRAUD_PREVENTION),
    ("SG", DataCategory.SUPPORT_TICKETS.value): (2, RetentionBasis.FRAUD_PREVENTION),
    # Thailand — PDPA 2019 + AML Act
    ("TH", DataCategory.FINANCIAL.value): (5, RetentionBasis.AML),
    ("TH", DataCategory.KYC_DOCUMENTS.value): (5, RetentionBasis.AML),
    ("TH", DataCategory.GAME_HISTORY.value): (5, RetentionBasis.AML),
    ("TH", DataCategory.PII.value): (0, RetentionBasis.NONE),
    ("TH", DataCategory.SPECIAL_CATEGORY.value): (3, RetentionBasis.RESPONSIBLE_GAMBLING),
    ("TH", DataCategory.MARKETING_CONSENT.value): (3, RetentionBasis.AML),
    ("TH", DataCategory.SESSION_LOGS.value): (1, RetentionBasis.FRAUD_PREVENTION),
    ("TH", DataCategory.SUPPORT_TICKETS.value): (2, RetentionBasis.FRAUD_PREVENTION),
}

# Deadline in calendar days from receipt of verified request
RESPONSE_DEADLINE_DAYS: dict[str, int] = {
    "EU": 30,       # GDPR Art. 12(3) — extendable to 90 days for complexity
    "UK": 30,       # UK GDPR
    "CA_US": 45,    # CCPA/CPRA — extendable to 90 days with notice
    "BR": 15,       # LGPD Art. 18 — 15 business days
    "KR": 10,       # PIPA — 10 days
    "SG": 30,       # PDPA — 10 business days (approximated as 14 calendar + buffer)
    "TH": 30,       # PDPA Thailand
    "CA": 30,       # PIPEDA — 30 calendar days
    "OTHER": 30,    # Best effort
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class DataRecord:
    """Represents a single data record associated with a player."""

    record_id: str
    category: DataCategory
    system: str             # e.g. "postgres:players", "elasticsearch:events"
    created_at: datetime
    fields: dict[str, Any]
    pii_fields: list[str]   # Field names that contain PII
    can_anonymise: bool = True


@dataclass
class RetentionDecision:
    """Result of evaluating whether a record must be retained."""

    record_id: str
    category: DataCategory
    action: str             # "delete", "anonymise", "retain"
    retention_basis: RetentionBasis
    retain_until: Optional[datetime]
    fields_to_remove: list[str]
    fields_to_retain: list[str]
    legal_note: str


@dataclass
class DSRRequest:
    """A submitted Data Subject Request."""

    request_id: str
    player_id: str
    request_type: DSRType
    jurisdiction: Jurisdiction
    submitted_at: datetime
    deadline: datetime
    status: DSRStatus = DSRStatus.RECEIVED
    identity_verified: bool = False
    records_assessed: list[RetentionDecision] = field(default_factory=list)
    audit_trail: list[dict[str, Any]] = field(default_factory=list)
    compliance_report: Optional[dict[str, Any]] = None

    def log_event(self, event: str, detail: dict[str, Any] | None = None) -> None:
        """Append an immutable audit entry."""
        entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
        }
        if detail:
            entry["detail"] = detail
        self.audit_trail.append(entry)


# ---------------------------------------------------------------------------
# DSR Processor
# ---------------------------------------------------------------------------


class DSRProcessor:
    """
    Processes Data Subject Requests for the AcmetoCasino platform.

    Implements the full lifecycle:
    1. Request intake and identity verification
    2. Data discovery across all platform systems
    3. Retention assessment per jurisdiction rules
    4. Selective erasure / anonymisation / export
    5. Compliance report generation
    6. Audit trail preservation

    In production this class would inject database connections, object storage
    clients, and a message broker.  The methods marked "# DB CALL" represent
    the integration points.
    """

    def __init__(self, player_id: str, jurisdiction: Jurisdiction) -> None:
        self.player_id = player_id
        self.jurisdiction = jurisdiction

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def create_request(self, request_type: DSRType) -> DSRRequest:
        """Intake a new DSR and calculate the response deadline."""
        deadline_days = RESPONSE_DEADLINE_DAYS.get(self.jurisdiction.value, 30)
        now = datetime.now(timezone.utc)

        request = DSRRequest(
            request_id=f"DSR-{uuid.uuid4().hex[:12].upper()}",
            player_id=self.player_id,
            request_type=request_type,
            jurisdiction=self.jurisdiction,
            submitted_at=now,
            deadline=now + timedelta(days=deadline_days),
        )
        request.log_event(
            "request_created",
            {
                "type": request_type.value,
                "jurisdiction": self.jurisdiction.value,
                "deadline": request.deadline.isoformat(),
            },
        )
        logger.info(
            "DSR created: %s | player=%s | type=%s | deadline=%s",
            request.request_id,
            self.player_id,
            request_type.value,
            request.deadline.date(),
        )
        return request

    def verify_identity(self, request: DSRRequest, verification_token: str) -> bool:
        """
        Verify the requester's identity before processing.

        In production: compare against KYC-verified identity record,
        validate government ID, or check authenticated session.
        """
        # Stub: accept any non-empty token in demo mode
        if not verification_token:
            request.log_event("identity_verification_failed", {"reason": "empty_token"})
            return False

        request.identity_verified = True
        request.status = DSRStatus.IDENTITY_VERIFIED
        request.log_event("identity_verified", {"method": "kyc_token"})
        return True

    def discover_player_data(self, request: DSRRequest) -> list[DataRecord]:
        """
        Discover all data records associated with the player across platform systems.

        Integration points (marked # DB CALL) represent queries to real systems.
        Returns synthetic data matching the AcmetoCasino schema for demonstration.
        """
        records: list[DataRecord] = []
        now = datetime.now(timezone.utc)
        account_age_days = 730  # 2-year-old account for demo

        # --- Player profile table (PostgreSQL: players)  # DB CALL
        records.append(DataRecord(
            record_id=f"PG-PLAYER-{self.player_id}",
            category=DataCategory.PII,
            system="postgres:players",
            created_at=now - timedelta(days=account_age_days),
            fields={
                "first_name": "Jane",
                "last_name": "Doe",
                "email": "jane.doe@example.com",
                "date_of_birth": "1985-06-15",
                "address": "123 Example St, Dublin, IE",
                "phone": "+353 1 234 5678",
                "nationality": "IE",
            },
            pii_fields=["first_name", "last_name", "email", "date_of_birth", "address", "phone"],
        ))

        # --- KYC documents (Object storage + PostgreSQL index)  # DB CALL
        records.append(DataRecord(
            record_id=f"KYC-{self.player_id}-001",
            category=DataCategory.KYC_DOCUMENTS,
            system="s3:kyc-documents",
            created_at=now - timedelta(days=account_age_days),
            fields={
                "document_type": "passport",
                "document_number": "P12345678",
                "issuing_country": "IE",
                "expiry_date": "2030-06-15",
                "verification_status": "verified",
            },
            pii_fields=["document_number"],
            can_anonymise=True,
        ))

        # --- Financial transactions (PostgreSQL: transactions)  # DB CALL
        for i in range(5):
            records.append(DataRecord(
                record_id=f"TXN-{self.player_id}-{i:04d}",
                category=DataCategory.FINANCIAL,
                system="postgres:transactions",
                created_at=now - timedelta(days=account_age_days - (i * 30)),
                fields={
                    "amount": 100.00 + (i * 25),
                    "currency": "EUR",
                    "type": "deposit" if i % 2 == 0 else "withdrawal",
                    "payment_method": "visa_****1234",
                    "reference": f"TXN{uuid.uuid4().hex[:8].upper()}",
                    "player_name": "Jane Doe",   # PII embedded in transaction
                    "player_email": "jane.doe@example.com",
                },
                pii_fields=["player_name", "player_email", "payment_method"],
            ))

        # --- Game history (ClickHouse: game_rounds)  # DB CALL
        for i in range(10):
            records.append(DataRecord(
                record_id=f"ROUND-{self.player_id}-{i:06d}",
                category=DataCategory.GAME_HISTORY,
                system="clickhouse:game_rounds",
                created_at=now - timedelta(days=account_age_days - i),
                fields={
                    "game_id": f"SLOT-{i % 3 + 1}",
                    "bet_amount": 1.00,
                    "win_amount": 0.00 if i % 3 != 0 else 5.00,
                    "rng_seed_hash": hashlib.sha256(f"seed-{i}".encode()).hexdigest(),
                    "player_id": self.player_id,
                },
                pii_fields=["player_id"],
            ))

        # --- Session logs (Elasticsearch: sessions)  # DB CALL
        for i in range(3):
            records.append(DataRecord(
                record_id=f"SESSION-{self.player_id}-{i}",
                category=DataCategory.SESSION_LOGS,
                system="elasticsearch:sessions",
                created_at=now - timedelta(days=i * 10),
                fields={
                    "ip_address": "203.0.113.42",
                    "user_agent": "Mozilla/5.0 ...",
                    "login_at": (now - timedelta(days=i * 10)).isoformat(),
                    "player_id": self.player_id,
                },
                pii_fields=["ip_address", "player_id"],
            ))

        # --- Responsible gambling flags  # DB CALL
        records.append(DataRecord(
            record_id=f"RG-{self.player_id}",
            category=DataCategory.SPECIAL_CATEGORY,
            system="postgres:rg_flags",
            created_at=now - timedelta(days=180),
            fields={
                "self_exclusion_active": False,
                "cool_off_active": False,
                "risk_score": "low",
                "intervention_history": [],
            },
            pii_fields=[],
            can_anonymise=True,
        ))

        # --- Marketing consent (PostgreSQL: consent_audit)  # DB CALL
        records.append(DataRecord(
            record_id=f"CONSENT-{self.player_id}",
            category=DataCategory.MARKETING_CONSENT,
            system="postgres:consent_audit",
            created_at=now - timedelta(days=account_age_days),
            fields={
                "email_marketing": True,
                "sms_marketing": False,
                "player_id": self.player_id,
                "source": "registration",
            },
            pii_fields=["player_id"],
        ))

        # --- Support tickets (Zendesk / PostgreSQL)  # DB CALL
        records.append(DataRecord(
            record_id=f"TICKET-{self.player_id}-001",
            category=DataCategory.SUPPORT_TICKETS,
            system="postgres:support_tickets",
            created_at=now - timedelta(days=60),
            fields={
                "subject": "Withdrawal delay",
                "body": "Hello, my withdrawal ...",
                "player_name": "Jane Doe",
                "player_email": "jane.doe@example.com",
                "status": "resolved",
            },
            pii_fields=["player_name", "player_email", "body"],
        ))

        request.log_event("data_discovery_complete", {"record_count": len(records)})
        return records

    def assess_retention(
        self, request: DSRRequest, records: list[DataRecord]
    ) -> list[RetentionDecision]:
        """
        Evaluate each data record against jurisdiction retention rules.

        Returns a list of decisions: delete, anonymise, or retain with legal basis.
        """
        request.status = DSRStatus.LEGAL_ASSESSMENT
        decisions: list[RetentionDecision] = []
        now = datetime.now(timezone.utc)

        for record in records:
            jur = self.jurisdiction.value
            cat = record.category.value
            key = (jur, cat)

            # Fall back to EU rules if jurisdiction not explicitly mapped
            retention_years, basis = RETENTION_SCHEDULE.get(
                key,
                RETENTION_SCHEDULE.get(("EU", cat), (0, RetentionBasis.NONE)),
            )

            if basis == RetentionBasis.NONE or retention_years == 0:
                # No retention requirement — full deletion
                decision = RetentionDecision(
                    record_id=record.record_id,
                    category=record.category,
                    action="delete",
                    retention_basis=RetentionBasis.NONE,
                    retain_until=None,
                    fields_to_remove=list(record.fields.keys()),
                    fields_to_retain=[],
                    legal_note="No statutory retention obligation. Full erasure applied.",
                )
            elif basis == RetentionBasis.RESPONSIBLE_GAMBLING and retention_years == 99:
                # Self-exclusion records — must be retained indefinitely (regulator requirement)
                decision = RetentionDecision(
                    record_id=record.record_id,
                    category=record.category,
                    action="retain",
                    retention_basis=basis,
                    retain_until=None,  # Indefinite
                    fields_to_remove=[],
                    fields_to_retain=list(record.fields.keys()),
                    legal_note=(
                        "Responsible gambling records retained indefinitely per "
                        "regulatory requirement. Cannot be erased."
                    ),
                )
            elif basis == RetentionBasis.AML and retention_years == 99:
                # Consent audit trail — retain forever as evidence
                decision = RetentionDecision(
                    record_id=record.record_id,
                    category=record.category,
                    action="retain",
                    retention_basis=basis,
                    retain_until=None,
                    fields_to_remove=[],
                    fields_to_retain=list(record.fields.keys()),
                    legal_note="Consent audit trail retained as permanent compliance evidence.",
                )
            elif record.can_anonymise and record.pii_fields:
                # Has PII but underlying record must be kept for AML/tax
                # Solution: anonymise PII fields, retain the financial skeleton
                retain_until = record.created_at + timedelta(days=retention_years * 365)
                decision = RetentionDecision(
                    record_id=record.record_id,
                    category=record.category,
                    action="anonymise",
                    retention_basis=basis,
                    retain_until=retain_until,
                    fields_to_remove=record.pii_fields,
                    fields_to_retain=[
                        f for f in record.fields if f not in record.pii_fields
                    ],
                    legal_note=(
                        f"Record retained under {basis.value} obligation until "
                        f"{retain_until.date()}. PII fields anonymised."
                    ),
                )
            else:
                # No PII to strip and record is under retention — retain as-is
                retain_until = record.created_at + timedelta(days=retention_years * 365)
                decision = RetentionDecision(
                    record_id=record.record_id,
                    category=record.category,
                    action="retain",
                    retention_basis=basis,
                    retain_until=retain_until,
                    fields_to_remove=[],
                    fields_to_retain=list(record.fields.keys()),
                    legal_note=(
                        f"Full record retained under {basis.value} until "
                        f"{retain_until.date()}."
                    ),
                )

            decisions.append(decision)

        request.records_assessed = decisions
        request.log_event(
            "retention_assessment_complete",
            {
                "total": len(decisions),
                "delete": sum(1 for d in decisions if d.action == "delete"),
                "anonymise": sum(1 for d in decisions if d.action == "anonymise"),
                "retain": sum(1 for d in decisions if d.action == "retain"),
            },
        )
        return decisions

    def execute_deletion(
        self, request: DSRRequest, decisions: list[RetentionDecision]
    ) -> dict[str, int]:
        """
        Execute deletion and anonymisation decisions.

        In production: executes SQL DELETE / UPDATE statements, removes
        S3 objects, purges Elasticsearch documents, invalidates caches.
        Each operation is wrapped in a database transaction with rollback.
        """
        if request.request_type not in (DSRType.DELETION, DSRType.RESTRICTION):
            raise ValueError("execute_deletion called on non-deletion request")

        request.status = DSRStatus.IN_PROGRESS
        counters: dict[str, int] = {"deleted": 0, "anonymised": 0, "retained": 0, "errors": 0}

        for decision in decisions:
            try:
                if decision.action == "delete":
                    # In production: DELETE FROM <table> WHERE player_id = %s
                    logger.info(
                        "[WOULD DELETE] system=%s record=%s",
                        decision.category.value,
                        decision.record_id,
                    )
                    counters["deleted"] += 1

                elif decision.action == "anonymise":
                    # In production: UPDATE <table> SET field = pseudonym WHERE id = %s
                    # Pseudonym is a deterministic hash of (record_id + anonymisation_key)
                    anonymised_values: dict[str, str] = {}
                    for pii_field in decision.fields_to_remove:
                        pseudonym = hashlib.sha256(
                            f"{decision.record_id}:{pii_field}:anon-salt".encode()
                        ).hexdigest()[:16]
                        anonymised_values[pii_field] = f"[ANONYMISED-{pseudonym}]"

                    logger.info(
                        "[WOULD ANONYMISE] record=%s fields=%s",
                        decision.record_id,
                        decision.fields_to_remove,
                    )
                    counters["anonymised"] += 1

                else:
                    logger.info(
                        "[RETAINED] record=%s basis=%s until=%s",
                        decision.record_id,
                        decision.retention_basis.value,
                        decision.retain_until.date() if decision.retain_until else "indefinite",
                    )
                    counters["retained"] += 1

            except Exception as exc:
                logger.error("Error processing record %s: %s", decision.record_id, exc)
                counters["errors"] += 1
                request.log_event(
                    "record_processing_error",
                    {"record_id": decision.record_id, "error": str(exc)},
                )

        if counters["errors"] == 0:
            request.status = (
                DSRStatus.FULFILLED
                if counters["retained"] == 0
                else DSRStatus.PARTIALLY_FULFILLED
            )
        request.log_event("deletion_execution_complete", counters)
        return counters

    def export_player_data(self, request: DSRRequest, records: list[DataRecord]) -> dict[str, Any]:
        """
        Build a GDPR-portable data export (JSON format, machine-readable).

        Satisfies GDPR Article 20 (right to data portability), CCPA right to know,
        and LGPD equivalent portability right.
        """
        if request.request_type != DSRType.EXPORT:
            raise ValueError("export_player_data called on non-export request")

        request.status = DSRStatus.IN_PROGRESS
        export_payload: dict[str, Any] = {
            "meta": {
                "request_id": request.request_id,
                "player_id": request.player_id,
                "export_generated_at": datetime.now(timezone.utc).isoformat(),
                "jurisdiction": request.jurisdiction.value,
                "format_version": "1.0",
                "format": "GDPR-portable-JSON",
            },
            "data": {},
        }

        for record in records:
            category = record.category.value
            if category not in export_payload["data"]:
                export_payload["data"][category] = []

            export_payload["data"][category].append({
                "record_id": record.record_id,
                "system": record.system,
                "created_at": record.created_at.isoformat(),
                "fields": record.fields,
            })

        request.status = DSRStatus.FULFILLED
        request.log_event(
            "export_generated",
            {
                "categories": list(export_payload["data"].keys()),
                "total_records": sum(
                    len(v) for v in export_payload["data"].values()
                ),
            },
        )
        return export_payload

    def generate_compliance_report(self, request: DSRRequest) -> dict[str, Any]:
        """
        Generate the compliance report that accompanies the DSR response to the player.

        This document:
        - Confirms receipt and deadline
        - Lists what was deleted / anonymised / retained
        - Provides legal basis for any retained data
        - Forms part of the operator's audit record
        """
        decisions = request.records_assessed
        report: dict[str, Any] = {
            "report_id": f"DSR-REPORT-{uuid.uuid4().hex[:8].upper()}",
            "request_id": request.request_id,
            "player_id": request.player_id,
            "request_type": request.request_type.value,
            "jurisdiction": request.jurisdiction.value,
            "applicable_law": _applicable_law(request.jurisdiction),
            "submitted_at": request.submitted_at.isoformat(),
            "deadline": request.deadline.isoformat(),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "status": request.status.value,
            "summary": {
                "total_records_assessed": len(decisions),
                "deleted": sum(1 for d in decisions if d.action == "delete"),
                "anonymised": sum(1 for d in decisions if d.action == "anonymise"),
                "retained": sum(1 for d in decisions if d.action == "retain"),
            },
            "retained_records": [
                {
                    "record_id": d.record_id,
                    "category": d.category.value,
                    "retention_basis": d.retention_basis.value,
                    "retain_until": d.retain_until.isoformat() if d.retain_until else "indefinite",
                    "legal_note": d.legal_note,
                }
                for d in decisions
                if d.action in ("retain", "anonymise")
            ],
            "audit_trail": request.audit_trail,
        }

        request.compliance_report = report
        request.log_event("compliance_report_generated", {"report_id": report["report_id"]})
        return report


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _applicable_law(jurisdiction: Jurisdiction) -> str:
    """Return the primary data protection law for a jurisdiction."""
    laws: dict[str, str] = {
        "EU": "GDPR (Regulation (EU) 2016/679), Articles 17 and 20",
        "UK": "UK GDPR and Data Protection Act 2018, Articles 17 and 20",
        "CA_US": "CCPA/CPRA (Cal. Civ. Code §§ 1798.100–1798.199)",
        "BR": "LGPD (Lei No. 13.709/2018), Art. 18",
        "KR": "PIPA (Personal Information Protection Act), Art. 36",
        "SG": "PDPA 2012 (Singapore), Section 22",
        "TH": "PDPA B.E. 2562 (2019) (Thailand), Section 33",
        "CA": "PIPEDA (S.C. 2000, c. 5), Principle 4.5",
        "OTHER": "Best-effort erasure (no specific statute)",
    }
    return laws.get(jurisdiction.value, "Unknown")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Process a Data Subject Request for an AcmetoCasino player."
    )
    parser.add_argument("--player-id", required=True, help="Platform player identifier")
    parser.add_argument(
        "--type",
        required=True,
        choices=[t.value for t in DSRType],
        help="Type of request",
    )
    parser.add_argument(
        "--jurisdiction",
        required=True,
        choices=[j.value for j in Jurisdiction],
        help="Regulatory jurisdiction governing this request",
    )
    parser.add_argument(
        "--verify-token",
        default="demo-token",
        help="Identity verification token (or KYC reference)",
    )
    parser.add_argument(
        "--output",
        default="report.json",
        help="Path to write the compliance report JSON",
    )
    args = parser.parse_args()

    processor = DSRProcessor(
        player_id=args.player_id,
        jurisdiction=Jurisdiction(args.jurisdiction),
    )

    # Step 1: Create the request
    dsr = processor.create_request(DSRType(args.type))

    # Step 2: Verify identity
    if not processor.verify_identity(dsr, args.verify_token):
        print(f"Identity verification failed. Request {dsr.request_id} rejected.")
        return

    # Step 3: Discover all data
    records = processor.discover_player_data(dsr)
    print(f"Discovered {len(records)} data records across {len({r.system for r in records})} systems.")

    if args.type == DSRType.EXPORT.value:
        # Export path
        export_data = processor.export_player_data(dsr, records)
        with open(args.output.replace(".json", "-export.json"), "w") as f:
            json.dump(export_data, f, indent=2, default=str)
        print(f"Data export written to {args.output.replace('.json', '-export.json')}")
    else:
        # Deletion / anonymisation path
        decisions = processor.assess_retention(dsr, records)

        delete_count = sum(1 for d in decisions if d.action == "delete")
        anonymise_count = sum(1 for d in decisions if d.action == "anonymise")
        retain_count = sum(1 for d in decisions if d.action == "retain")
        print(
            f"Retention assessment: {delete_count} to delete, "
            f"{anonymise_count} to anonymise, {retain_count} to retain."
        )

        if args.type == DSRType.DELETION.value:
            counters = processor.execute_deletion(dsr, decisions)
            print(
                f"Execution complete: deleted={counters['deleted']} "
                f"anonymised={counters['anonymised']} "
                f"retained={counters['retained']} "
                f"errors={counters['errors']}"
            )

    # Step 5: Generate and write compliance report
    report = processor.generate_compliance_report(dsr)
    with open(args.output, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nCompliance report written to {args.output}")
    print(f"Request ID: {dsr.request_id} | Status: {dsr.status.value}")


if __name__ == "__main__":
    main()
