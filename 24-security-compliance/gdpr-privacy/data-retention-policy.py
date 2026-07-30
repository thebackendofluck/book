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
Automated Data Retention Policy Enforcer for iGaming Platforms.

Implements scheduled retention enforcement across all data systems in the
AcmetoCasino platform. Runs nightly as a cron job (or Kubernetes CronJob)
to identify records that have exceeded their retention window and apply
the appropriate action: delete, anonymise, or flag for manual review.

Retention schedules are driven by regulatory obligations:
- AML: 5 years (EU 4AMLD/5AMLD, FinCEN, FINTRAC)
- Tax: 5-7 years depending on jurisdiction
- Responsible gambling: indefinite for self-exclusion records
- Session logs: 1 year (fraud investigation basis)
- Support tickets: 2 years (legitimate interest)
- PII: deleted on account closure or DSR (no independent retention basis)

Usage:
    python data-retention-policy.py --scan-only       # Report without deleting
    python data-retention-policy.py --execute         # Apply retention actions
    python data-retention-policy.py --report-only     # Generate compliance report
    python data-retention-policy.py --category financial --execute
"""

import argparse
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
logger = logging.getLogger("retention_policy")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class DataCategory(Enum):
    PII = "pii"
    FINANCIAL = "financial"
    SPECIAL_CATEGORY = "special_category"
    MARKETING_CONSENT = "marketing_consent"
    GAME_HISTORY = "game_history"
    KYC_DOCUMENTS = "kyc_documents"
    SESSION_LOGS = "session_logs"
    SUPPORT_TICKETS = "support_tickets"


class RetentionAction(Enum):
    DELETE = "delete"
    ANONYMISE = "anonymise"
    RETAIN = "retain"
    FLAG_REVIEW = "flag_review"


# ---------------------------------------------------------------------------
# Retention policy definitions
# ---------------------------------------------------------------------------


@dataclass
class RetentionPolicy:
    """Defines how a data category is treated after its retention window expires."""

    category: DataCategory
    retention_days: int                 # 0 = no independent retention period
    action_on_expiry: RetentionAction
    pii_fields: list[str]               # Fields to strip during anonymisation
    legal_basis: str
    regulation: str
    indefinite: bool = False            # True = never expires (self-exclusion, consent audit)
    grace_period_days: int = 30         # Buffer before actual deletion/anonymisation


# Multi-jurisdiction retention policies — most restrictive applicable rule wins
# when a player's data spans multiple jurisdictions.
RETENTION_POLICIES: list[RetentionPolicy] = [
    RetentionPolicy(
        category=DataCategory.PII,
        retention_days=0,
        action_on_expiry=RetentionAction.DELETE,
        pii_fields=[
            "first_name", "last_name", "email", "date_of_birth",
            "address", "phone", "nationality",
        ],
        legal_basis="No independent retention basis. Deleted on account closure or DSR.",
        regulation="GDPR Art. 17, LGPD Art. 18, CCPA § 1798.105",
    ),
    RetentionPolicy(
        category=DataCategory.FINANCIAL,
        retention_days=5 * 365,         # 5 years — EU 4AMLD, US FinCEN
        action_on_expiry=RetentionAction.ANONYMISE,
        pii_fields=["player_name", "player_email", "payment_method", "iban", "card_last4"],
        legal_basis="AML/CTF regulatory obligation — financial records retained for AML audit.",
        regulation="EU 4AMLD Art. 40, US Bank Secrecy Act 31 U.S.C. § 5318(g)",
        grace_period_days=30,
    ),
    RetentionPolicy(
        category=DataCategory.KYC_DOCUMENTS,
        retention_days=5 * 365,
        action_on_expiry=RetentionAction.DELETE,
        pii_fields=["document_number", "document_image_path"],
        legal_basis="AML CDD record retention requirement.",
        regulation="EU 4AMLD Art. 40(2), FATF Recommendation 11",
        grace_period_days=30,
    ),
    RetentionPolicy(
        category=DataCategory.GAME_HISTORY,
        retention_days=5 * 365,
        action_on_expiry=RetentionAction.ANONYMISE,
        pii_fields=["player_id", "player_email"],
        legal_basis="AML audit trail — game history supports transaction monitoring.",
        regulation="EU 4AMLD, MGA Directive 3 of 2018 Art. 6",
        grace_period_days=30,
    ),
    RetentionPolicy(
        category=DataCategory.SPECIAL_CATEGORY,
        retention_days=0,               # Indefinite for self-exclusion
        action_on_expiry=RetentionAction.RETAIN,
        pii_fields=[],
        legal_basis=(
            "Self-exclusion and responsible gambling records must be retained indefinitely "
            "per UKGC RTS, MGA directive, and equivalent requirements. Used to prevent "
            "re-registration by excluded players."
        ),
        regulation="UKGC RTS 14, MGA Directive 3 of 2018 Art. 13, GGL § 8a",
        indefinite=True,
    ),
    RetentionPolicy(
        category=DataCategory.MARKETING_CONSENT,
        retention_days=0,               # Indefinite audit trail
        action_on_expiry=RetentionAction.RETAIN,
        pii_fields=[],
        legal_basis=(
            "Consent records retained indefinitely as evidence of lawful basis "
            "for historical communications. Consent withdrawal date is part of the record."
        ),
        regulation="GDPR Art. 7(1), ePrivacy Directive Art. 13",
        indefinite=True,
    ),
    RetentionPolicy(
        category=DataCategory.SESSION_LOGS,
        retention_days=1 * 365,
        action_on_expiry=RetentionAction.DELETE,
        pii_fields=["ip_address", "player_id", "user_agent"],
        legal_basis="Legitimate interest in fraud investigation and AML transaction monitoring.",
        regulation="GDPR Recital 47, ePrivacy Directive",
        grace_period_days=30,
    ),
    RetentionPolicy(
        category=DataCategory.SUPPORT_TICKETS,
        retention_days=2 * 365,
        action_on_expiry=RetentionAction.ANONYMISE,
        pii_fields=["player_name", "player_email", "body"],
        legal_basis=(
            "Legitimate interest in maintaining support history for dispute resolution. "
            "Anonymised after 2 years; ticket metadata (status, resolution code) retained."
        ),
        regulation="GDPR Recital 47",
        grace_period_days=30,
    ),
]

POLICY_INDEX: dict[DataCategory, RetentionPolicy] = {p.category: p for p in RETENTION_POLICIES}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class DataRecord:
    """Represents a data record found during a retention scan."""

    record_id: str
    category: DataCategory
    system: str
    player_id: str
    created_at: datetime
    last_activity: Optional[datetime] = None
    jurisdiction: str = "EU"
    current_fields: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetentionDecision:
    """The retention action to apply to a specific record."""

    record_id: str
    category: DataCategory
    system: str
    player_id: str
    action: RetentionAction
    created_at: datetime
    expires_at: Optional[datetime]
    legal_basis: str
    fields_to_remove: list[str]
    executed: bool = False
    error: Optional[str] = None


@dataclass
class RetentionScanResult:
    """Aggregated result of a retention scan run."""

    run_id: str
    started_at: datetime
    completed_at: Optional[datetime]
    total_records_scanned: int
    decisions: list[RetentionDecision]
    errors: list[dict[str, Any]]
    dry_run: bool

    @property
    def summary(self) -> dict[str, Any]:
        """Return a summary breakdown."""
        return {
            "run_id": self.run_id,
            "dry_run": self.dry_run,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "total_scanned": self.total_records_scanned,
            "to_delete": sum(1 for d in self.decisions if d.action == RetentionAction.DELETE),
            "to_anonymise": sum(1 for d in self.decisions if d.action == RetentionAction.ANONYMISE),
            "to_retain": sum(1 for d in self.decisions if d.action == RetentionAction.RETAIN),
            "to_flag": sum(1 for d in self.decisions if d.action == RetentionAction.FLAG_REVIEW),
            "executed": sum(1 for d in self.decisions if d.executed),
            "errors": len(self.errors),
        }


# ---------------------------------------------------------------------------
# Retention enforcer
# ---------------------------------------------------------------------------


class RetentionPolicyEnforcer:
    """
    Scans all platform data systems and enforces retention policies.

    Designed to run as a nightly Kubernetes CronJob. All scan results and
    execution actions are persisted to the compliance audit log.

    In production, the _scan_* methods execute parameterised SQL queries and
    storage API calls. The mock implementations below generate representative
    data matching the AcmetoCasino schema.
    """

    def __init__(
        self,
        dry_run: bool = True,
        category_filter: Optional[DataCategory] = None,
    ) -> None:
        self.dry_run = dry_run
        self.category_filter = category_filter
        self.now = datetime.now(timezone.utc)

    def run(self) -> RetentionScanResult:
        """Execute a full retention scan and optionally enforce decisions."""
        run_id = f"RET-{uuid.uuid4().hex[:10].upper()}"
        result = RetentionScanResult(
            run_id=run_id,
            started_at=self.now,
            completed_at=None,
            total_records_scanned=0,
            decisions=[],
            errors=[],
            dry_run=self.dry_run,
        )

        logger.info(
            "Retention scan started: run_id=%s dry_run=%s category=%s",
            run_id,
            self.dry_run,
            self.category_filter.value if self.category_filter else "all",
        )

        # Scan each system for expired records
        scan_results = self._discover_expired_records()
        result.total_records_scanned = len(scan_results)

        # Assess and classify each expired record
        for record in scan_results:
            try:
                decision = self._assess_record(record)
                result.decisions.append(decision)
            except Exception as exc:
                logger.error("Error assessing record %s: %s", record.record_id, exc)
                result.errors.append({"record_id": record.record_id, "error": str(exc)})

        # Execute decisions if not dry run
        if not self.dry_run:
            self._execute_decisions(result)

        result.completed_at = datetime.now(timezone.utc)
        self._persist_audit_log(result)

        logger.info(
            "Retention scan complete: %s",
            json.dumps(result.summary, default=str),
        )
        return result

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def _discover_expired_records(self) -> list[DataRecord]:
        """
        Discover records across all systems that have exceeded their retention window.

        Production integration points (each calls a different system):
        - PostgreSQL: players, transactions, game_rounds, sessions, consent_audit
        - ClickHouse: game_rounds (long-term analytics store)
        - Elasticsearch: sessions, events
        - S3: kyc-documents, exports
        """
        records: list[DataRecord] = []

        scan_methods = [
            self._scan_pii_records,
            self._scan_financial_records,
            self._scan_kyc_documents,
            self._scan_game_history,
            self._scan_session_logs,
            self._scan_support_tickets,
        ]

        for scan_method in scan_methods:
            try:
                batch = scan_method()
                if self.category_filter is None or any(
                    r.category == self.category_filter for r in batch
                ):
                    records.extend(
                        r for r in batch
                        if self.category_filter is None or r.category == self.category_filter
                    )
            except Exception as exc:
                logger.error("Scan method %s failed: %s", scan_method.__name__, exc)

        return records

    def _scan_pii_records(self) -> list[DataRecord]:
        """
        Find player PII records for closed/inactive accounts.

        SQL (production):
            SELECT id, player_id, created_at FROM players
            WHERE account_status = 'closed'
            AND closed_at < NOW() - INTERVAL '30 days'
            AND anonymised_at IS NULL;
        """
        # Synthetic demo records
        return [
            DataRecord(
                record_id="PG-PLAYER-CLOSED-001",
                category=DataCategory.PII,
                system="postgres:players",
                player_id="P-CLOSED-001",
                created_at=self.now - timedelta(days=400),
                last_activity=self.now - timedelta(days=65),
                jurisdiction="EU",
                current_fields={
                    "first_name": "John", "last_name": "Smith",
                    "email": "john.smith@example.com",
                    "account_status": "closed",
                },
            ),
        ]

    def _scan_financial_records(self) -> list[DataRecord]:
        """
        Find financial transaction records with PII past the AML retention window.

        SQL (production):
            SELECT id, player_id, created_at FROM transactions
            WHERE created_at < NOW() - INTERVAL '5 years 30 days'
            AND player_name IS NOT NULL;
        """
        threshold = self.now - timedelta(days=(5 * 365) + 30)
        return [
            DataRecord(
                record_id=f"TXN-OLD-{i:04d}",
                category=DataCategory.FINANCIAL,
                system="postgres:transactions",
                player_id=f"P-ARCHIVE-{i:03d}",
                created_at=threshold - timedelta(days=i * 5),
                jurisdiction="EU",
                current_fields={
                    "amount": 50.00,
                    "currency": "EUR",
                    "player_name": "Old Player",
                    "player_email": "old@example.com",
                    "type": "deposit",
                },
            )
            for i in range(3)
        ]

    def _scan_kyc_documents(self) -> list[DataRecord]:
        """
        Find KYC documents past the 5-year AML retention window.

        SQL (production):
            SELECT id, player_id, created_at FROM kyc_documents
            WHERE verified_at < NOW() - INTERVAL '5 years 30 days'
            AND deleted_at IS NULL;
        """
        threshold = self.now - timedelta(days=(5 * 365) + 30)
        return [
            DataRecord(
                record_id=f"KYC-OLD-{i:04d}",
                category=DataCategory.KYC_DOCUMENTS,
                system="s3:kyc-documents",
                player_id=f"P-ARCHIVE-KYC-{i:03d}",
                created_at=threshold - timedelta(days=i * 10),
                jurisdiction="EU",
                current_fields={
                    "document_type": "passport",
                    "document_number": f"P{i:08d}",
                    "document_image_path": f"s3://kyc-docs/P-ARCHIVE-KYC-{i:03d}/passport.pdf",
                },
            )
            for i in range(2)
        ]

    def _scan_game_history(self) -> list[DataRecord]:
        """
        Find game history records past the AML retention window where player_id is still PII.

        ClickHouse (production):
            SELECT record_id, player_id, created_at FROM game_rounds
            WHERE created_at < NOW() - INTERVAL '5 years 30 days'
            AND player_id NOT LIKE 'ANON-%';
        """
        threshold = self.now - timedelta(days=(5 * 365) + 30)
        return [
            DataRecord(
                record_id=f"ROUND-OLD-{i:06d}",
                category=DataCategory.GAME_HISTORY,
                system="clickhouse:game_rounds",
                player_id=f"P-ARCHIVE-{i // 10:03d}",
                created_at=threshold - timedelta(days=i),
                jurisdiction="EU",
                current_fields={
                    "game_id": "SLOT-1",
                    "bet_amount": 1.00,
                    "win_amount": 0.00,
                    "player_id": f"P-ARCHIVE-{i // 10:03d}",
                },
            )
            for i in range(5)
        ]

    def _scan_session_logs(self) -> list[DataRecord]:
        """
        Find session logs past the 1-year retention window.

        Elasticsearch (production):
            GET sessions/_search
            { "query": { "range": { "login_at": { "lt": "now-1y" } } } }
        """
        threshold = self.now - timedelta(days=365 + 30)
        return [
            DataRecord(
                record_id=f"SESSION-OLD-{i:04d}",
                category=DataCategory.SESSION_LOGS,
                system="elasticsearch:sessions",
                player_id=f"P-{i:06d}",
                created_at=threshold - timedelta(days=i * 2),
                jurisdiction="EU",
                current_fields={
                    "ip_address": "203.0.113.1",
                    "player_id": f"P-{i:06d}",
                    "user_agent": "Mozilla/5.0",
                },
            )
            for i in range(4)
        ]

    def _scan_support_tickets(self) -> list[DataRecord]:
        """
        Find support tickets past the 2-year retention window that still contain PII.

        SQL (production):
            SELECT id, player_id, created_at FROM support_tickets
            WHERE created_at < NOW() - INTERVAL '2 years 30 days'
            AND player_name IS NOT NULL;
        """
        threshold = self.now - timedelta(days=(2 * 365) + 30)
        return [
            DataRecord(
                record_id=f"TICKET-OLD-{i:04d}",
                category=DataCategory.SUPPORT_TICKETS,
                system="postgres:support_tickets",
                player_id=f"P-{i * 100:06d}",
                created_at=threshold - timedelta(days=i * 15),
                jurisdiction="EU",
                current_fields={
                    "subject": "Account issue",
                    "body": "Dear support, my account ...",
                    "player_name": f"Player {i}",
                    "player_email": f"player{i}@example.com",
                    "status": "resolved",
                },
            )
            for i in range(3)
        ]

    # ------------------------------------------------------------------
    # Assessment
    # ------------------------------------------------------------------

    def _assess_record(self, record: DataRecord) -> RetentionDecision:
        """Determine the correct action for an expired record."""
        policy = POLICY_INDEX.get(record.category)
        if policy is None:
            return RetentionDecision(
                record_id=record.record_id,
                category=record.category,
                system=record.system,
                player_id=record.player_id,
                action=RetentionAction.FLAG_REVIEW,
                created_at=record.created_at,
                expires_at=None,
                legal_basis="No policy found — manual review required.",
                fields_to_remove=[],
            )

        if policy.indefinite:
            return RetentionDecision(
                record_id=record.record_id,
                category=record.category,
                system=record.system,
                player_id=record.player_id,
                action=RetentionAction.RETAIN,
                created_at=record.created_at,
                expires_at=None,
                legal_basis=policy.legal_basis,
                fields_to_remove=[],
            )

        expires_at = record.created_at + timedelta(days=policy.retention_days)
        return RetentionDecision(
            record_id=record.record_id,
            category=record.category,
            system=record.system,
            player_id=record.player_id,
            action=policy.action_on_expiry,
            created_at=record.created_at,
            expires_at=expires_at,
            legal_basis=policy.legal_basis,
            fields_to_remove=policy.pii_fields,
        )

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def _execute_decisions(self, result: RetentionScanResult) -> None:
        """
        Apply retention decisions to the underlying data systems.

        In production each operation is wrapped in a transaction and logged
        to the compliance audit table before the destructive action is taken.
        """
        for decision in result.decisions:
            if decision.action == RetentionAction.RETAIN:
                decision.executed = True
                continue

            try:
                if decision.action == RetentionAction.DELETE:
                    self._delete_record(decision)

                elif decision.action == RetentionAction.ANONYMISE:
                    self._anonymise_record(decision)

                elif decision.action == RetentionAction.FLAG_REVIEW:
                    self._flag_for_review(decision)

                decision.executed = True

            except Exception as exc:
                decision.error = str(exc)
                result.errors.append({
                    "record_id": decision.record_id,
                    "action": decision.action.value,
                    "error": str(exc),
                })
                logger.error(
                    "Failed to execute %s on %s: %s",
                    decision.action.value,
                    decision.record_id,
                    exc,
                )

    def _delete_record(self, decision: RetentionDecision) -> None:
        """Delete a record from its system."""
        # Production:
        # if "postgres:" in decision.system:
        #     db.execute("DELETE FROM <table> WHERE id = %s", [decision.record_id])
        # elif "s3:" in decision.system:
        #     s3.delete_object(Bucket=bucket, Key=key)
        # elif "elasticsearch:" in decision.system:
        #     es.delete(index=index, id=decision.record_id)
        logger.info(
            "[DELETE] system=%s record=%s player=%s",
            decision.system,
            decision.record_id,
            decision.player_id,
        )

    def _anonymise_record(self, decision: RetentionDecision) -> None:
        """Strip PII fields from a record, retaining the financial skeleton."""
        # Production:
        # updates = {f: f"[ANONYMISED]" for f in decision.fields_to_remove}
        # db.execute("UPDATE <table> SET ... WHERE id = %s", updates + [decision.record_id])
        logger.info(
            "[ANONYMISE] system=%s record=%s fields=%s",
            decision.system,
            decision.record_id,
            decision.fields_to_remove,
        )

    def _flag_for_review(self, decision: RetentionDecision) -> None:
        """Insert a review task for compliance team."""
        logger.warning(
            "[FLAG_REVIEW] system=%s record=%s — no policy found",
            decision.system,
            decision.record_id,
        )

    # ------------------------------------------------------------------
    # Audit logging
    # ------------------------------------------------------------------

    def _persist_audit_log(self, result: RetentionScanResult) -> None:
        """
        Write scan result to the compliance audit log.

        Production: INSERT INTO retention_audit_log (run_id, summary, decisions, ...).
        The audit log is append-only (no UPDATE/DELETE allowed on this table).
        """
        logger.info(
            "Audit log persisted for run_id=%s: %s",
            result.run_id,
            json.dumps(result.summary, default=str),
        )


# ---------------------------------------------------------------------------
# Compliance report generation
# ---------------------------------------------------------------------------


def generate_retention_report(result: RetentionScanResult) -> dict[str, Any]:
    """
    Build a compliance-ready retention report from a scan result.

    This report can be submitted to regulators as evidence of data minimisation
    and retention policy enforcement.
    """
    by_category: dict[str, dict[str, Any]] = {}
    for decision in result.decisions:
        cat = decision.category.value
        if cat not in by_category:
            by_category[cat] = {
                "total": 0,
                "deleted": 0,
                "anonymised": 0,
                "retained": 0,
                "flagged": 0,
                "errors": 0,
            }
        by_category[cat]["total"] += 1
        action_map = {
            RetentionAction.DELETE: "deleted",
            RetentionAction.ANONYMISE: "anonymised",
            RetentionAction.RETAIN: "retained",
            RetentionAction.FLAG_REVIEW: "flagged",
        }
        key = action_map.get(decision.action, "flagged")
        by_category[cat][key] += 1
        if decision.error:
            by_category[cat]["errors"] += 1

    return {
        "report_type": "retention_compliance",
        "run_id": result.run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": result.dry_run,
        "summary": result.summary,
        "by_category": by_category,
        "policies_applied": [
            {
                "category": p.category.value,
                "retention_days": p.retention_days if not p.indefinite else "indefinite",
                "action_on_expiry": p.action_on_expiry.value,
                "regulation": p.regulation,
            }
            for p in RETENTION_POLICIES
        ],
        "errors": result.errors,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Enforce data retention policies for the AcmetoCasino platform."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--scan-only",
        action="store_true",
        help="Discover expired records and print decisions without executing",
    )
    mode.add_argument(
        "--execute",
        action="store_true",
        help="Discover and execute retention actions (deletion / anonymisation)",
    )
    mode.add_argument(
        "--report-only",
        action="store_true",
        help="Generate and print a compliance report from the last scan",
    )

    parser.add_argument(
        "--category",
        choices=[c.value for c in DataCategory],
        help="Limit scan to a specific data category",
    )
    parser.add_argument(
        "--output",
        default="retention-report.json",
        help="Path to write the retention compliance report",
    )

    args = parser.parse_args()

    category_filter = DataCategory(args.category) if args.category else None

    enforcer = RetentionPolicyEnforcer(
        dry_run=not args.execute,
        category_filter=category_filter,
    )

    result = enforcer.run()
    report = generate_retention_report(result)

    # Print summary to stdout
    summary = result.summary
    print(f"\nRetention scan: {summary['run_id']}")
    print(f"  Mode:        {'DRY RUN (no changes)' if result.dry_run else 'EXECUTE'}")
    print(f"  Scanned:     {summary['total_scanned']}")
    print(f"  To delete:   {summary['to_delete']}")
    print(f"  To anonymise:{summary['to_anonymise']}")
    print(f"  Retained:    {summary['to_retain']}")
    print(f"  Flagged:     {summary['to_flag']}")
    print(f"  Errors:      {summary['errors']}")

    # Write full report
    with open(args.output, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nCompliance report written to {args.output}")


if __name__ == "__main__":
    main()
