#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 09, Legal Framework and Contracts.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Contract Lifecycle Management System for iGaming Operators.

Manages the full lifecycle of vendor, provider, and affiliate contracts:
  - Draft -> Review -> Approved -> Active -> Renewal/Expiry/Terminated
  - Version tracking, amendment history, approval workflows
  - Automated renewal reminders and expiry alerting
  - Audit trail for regulatory compliance (MGA, UKGC, Curacao)

Usage:
    python contract_lifecycle.py --action create --type game_provider --vendor "SlotCo"
    python contract_lifecycle.py --action list --status active
    python contract_lifecycle.py --action renew --contract-id CON-2026-0042
    python contract_lifecycle.py --action report --format json
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional
from pathlib import Path


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------

class ContractStatus(str, Enum):
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    UNDER_LEGAL_REVIEW = "under_legal_review"
    APPROVED = "approved"
    ACTIVE = "active"
    AMENDMENT_PENDING = "amendment_pending"
    RENEWAL_PENDING = "renewal_pending"
    EXPIRED = "expired"
    TERMINATED = "terminated"
    SUSPENDED = "suspended"

    # Valid transitions
    @classmethod
    def valid_transitions(cls):
        return {
            cls.DRAFT: [cls.PENDING_REVIEW, cls.TERMINATED],
            cls.PENDING_REVIEW: [cls.UNDER_LEGAL_REVIEW, cls.DRAFT, cls.TERMINATED],
            cls.UNDER_LEGAL_REVIEW: [cls.APPROVED, cls.DRAFT, cls.TERMINATED],
            cls.APPROVED: [cls.ACTIVE, cls.TERMINATED],
            cls.ACTIVE: [
                cls.AMENDMENT_PENDING, cls.RENEWAL_PENDING,
                cls.SUSPENDED, cls.TERMINATED, cls.EXPIRED
            ],
            cls.AMENDMENT_PENDING: [cls.ACTIVE, cls.TERMINATED],
            cls.RENEWAL_PENDING: [cls.ACTIVE, cls.EXPIRED, cls.TERMINATED],
            cls.SUSPENDED: [cls.ACTIVE, cls.TERMINATED],
            cls.EXPIRED: [cls.RENEWAL_PENDING],
            cls.TERMINATED: [],
        }


class ContractType(str, Enum):
    GAME_PROVIDER = "game_provider"
    PAYMENT_PROCESSOR = "payment_processor"
    AFFILIATE = "affiliate"
    PLATFORM_LICENSE = "platform_license"
    DATA_PROCESSING = "data_processing"
    KYC_PROVIDER = "kyc_provider"
    RESPONSIBLE_GAMBLING = "responsible_gambling"
    MARKETING = "marketing"
    HOSTING_INFRASTRUCTURE = "hosting_infrastructure"
    ODDS_FEED = "odds_feed"


class Jurisdiction(str, Enum):
    MGA = "mga"          # Malta Gaming Authority
    UKGC = "ukgc"        # UK Gambling Commission
    CURACAO = "curacao"   # Curacao eGaming
    GIBRALTAR = "gibraltar"
    ISLE_OF_MAN = "isle_of_man"
    KAHNAWAKE = "kahnawake"
    PAGCOR = "pagcor"     # Philippines
    SGA = "sga"           # Sweden Spelinspektionen
    ONJN = "onjn"         # Romania
    AGCO = "agco"         # Ontario, Canada
    BRAZIL_SPA = "brazil_spa"


@dataclass
class ContractParty:
    legal_name: str
    registration_number: str
    jurisdiction: str
    address: str
    contact_name: str
    contact_email: str


@dataclass
class FinancialTerms:
    model: str                    # "revenue_share", "fixed_fee", "hybrid", "cpa"
    revenue_share_pct: float = 0.0
    fixed_monthly_fee: float = 0.0
    minimum_guarantee: float = 0.0
    currency: str = "EUR"
    payment_terms_days: int = 30
    settlement_frequency: str = "monthly"  # weekly, biweekly, monthly


@dataclass
class Contract:
    contract_id: str
    contract_type: ContractType
    status: ContractStatus
    vendor: ContractParty
    operator: ContractParty
    financial_terms: FinancialTerms
    jurisdictions: list
    effective_date: Optional[str] = None
    expiry_date: Optional[str] = None
    auto_renewal: bool = True
    renewal_notice_days: int = 90
    termination_notice_days: int = 60
    version: int = 1
    created_at: str = ""
    updated_at: str = ""
    created_by: str = ""
    tags: list = field(default_factory=list)
    notes: str = ""


# ---------------------------------------------------------------------------
# Database layer
# ---------------------------------------------------------------------------

DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS contracts (
    contract_id TEXT PRIMARY KEY,
    contract_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    vendor_json TEXT NOT NULL,
    operator_json TEXT NOT NULL,
    financial_terms_json TEXT NOT NULL,
    jurisdictions_json TEXT NOT NULL,
    effective_date TEXT,
    expiry_date TEXT,
    auto_renewal INTEGER DEFAULT 1,
    renewal_notice_days INTEGER DEFAULT 90,
    termination_notice_days INTEGER DEFAULT 60,
    version INTEGER DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    tags_json TEXT DEFAULT '[]',
    notes TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS contract_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id TEXT NOT NULL,
    action TEXT NOT NULL,
    old_status TEXT,
    new_status TEXT,
    changed_by TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    details TEXT,
    document_hash TEXT,
    FOREIGN KEY (contract_id) REFERENCES contracts(contract_id)
);

CREATE TABLE IF NOT EXISTS contract_amendments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id TEXT NOT NULL,
    amendment_number INTEGER NOT NULL,
    description TEXT NOT NULL,
    old_terms_json TEXT,
    new_terms_json TEXT,
    effective_date TEXT,
    approved_by TEXT,
    approved_at TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (contract_id) REFERENCES contracts(contract_id)
);

CREATE TABLE IF NOT EXISTS approval_workflow (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id TEXT NOT NULL,
    step_order INTEGER NOT NULL,
    approver_role TEXT NOT NULL,
    approver_name TEXT,
    status TEXT DEFAULT 'pending',
    approved_at TEXT,
    comments TEXT,
    FOREIGN KEY (contract_id) REFERENCES contracts(contract_id)
);

CREATE INDEX IF NOT EXISTS idx_contracts_status ON contracts(status);
CREATE INDEX IF NOT EXISTS idx_contracts_type ON contracts(contract_type);
CREATE INDEX IF NOT EXISTS idx_contracts_expiry ON contracts(expiry_date);
CREATE INDEX IF NOT EXISTS idx_history_contract ON contract_history(contract_id);
"""


class ContractDB:
    def __init__(self, db_path: str = "contracts.db"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(DB_SCHEMA)

    def close(self):
        self.conn.close()

    def generate_id(self, contract_type: str) -> str:
        year = datetime.now(timezone.utc).strftime("%Y")
        cursor = self.conn.execute(
            "SELECT COUNT(*) FROM contracts WHERE contract_id LIKE ?",
            (f"CON-{year}-%",)
        )
        seq = cursor.fetchone()[0] + 1
        return f"CON-{year}-{seq:04d}"

    def create_contract(self, contract: Contract) -> str:
        now = datetime.now(timezone.utc).isoformat()
        contract.created_at = now
        contract.updated_at = now
        if not contract.contract_id:
            contract.contract_id = self.generate_id(contract.contract_type)

        self.conn.execute(
            """INSERT INTO contracts (
                contract_id, contract_type, status, vendor_json, operator_json,
                financial_terms_json, jurisdictions_json, effective_date, expiry_date,
                auto_renewal, renewal_notice_days, termination_notice_days, version,
                created_at, updated_at, created_by, tags_json, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                contract.contract_id, contract.contract_type, contract.status,
                json.dumps(asdict(contract.vendor)),
                json.dumps(asdict(contract.operator)),
                json.dumps(asdict(contract.financial_terms)),
                json.dumps(contract.jurisdictions),
                contract.effective_date, contract.expiry_date,
                int(contract.auto_renewal), contract.renewal_notice_days,
                contract.termination_notice_days, contract.version,
                contract.created_at, contract.updated_at,
                contract.created_by, json.dumps(contract.tags), contract.notes,
            )
        )
        self._log_history(contract.contract_id, "created", None, contract.status,
                          contract.created_by, "Contract created")
        self._create_approval_workflow(contract.contract_id, contract.contract_type)
        self.conn.commit()
        return contract.contract_id

    def transition_status(self, contract_id: str, new_status: ContractStatus,
                          changed_by: str, details: str = "") -> bool:
        row = self.conn.execute(
            "SELECT status FROM contracts WHERE contract_id = ?", (contract_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Contract {contract_id} not found")

        current = ContractStatus(row["status"])
        valid = ContractStatus.valid_transitions()
        if new_status not in valid.get(current, []):
            raise ValueError(
                f"Invalid transition: {current.value} -> {new_status.value}. "
                f"Allowed: {[s.value for s in valid.get(current, [])]}"
            )

        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "UPDATE contracts SET status = ?, updated_at = ?, version = version + 1 WHERE contract_id = ?",
            (new_status.value, now, contract_id)
        )
        self._log_history(contract_id, "status_change", current.value,
                          new_status.value, changed_by, details)
        self.conn.commit()
        return True

    def renew_contract(self, contract_id: str, new_expiry: str,
                       changed_by: str) -> bool:
        self.transition_status(contract_id, ContractStatus.RENEWAL_PENDING,
                               changed_by, f"Renewal requested, new expiry: {new_expiry}")
        self.conn.execute(
            "UPDATE contracts SET expiry_date = ?, updated_at = ? WHERE contract_id = ?",
            (new_expiry, datetime.now(timezone.utc).isoformat(), contract_id)
        )
        self.transition_status(contract_id, ContractStatus.ACTIVE,
                               changed_by, "Renewal approved")
        return True

    def add_amendment(self, contract_id: str, description: str,
                      old_terms: dict, new_terms: dict, changed_by: str) -> int:
        cursor = self.conn.execute(
            "SELECT MAX(amendment_number) FROM contract_amendments WHERE contract_id = ?",
            (contract_id,)
        )
        max_num = cursor.fetchone()[0] or 0
        amendment_number = max_num + 1
        now = datetime.now(timezone.utc).isoformat()

        self.conn.execute(
            """INSERT INTO contract_amendments
               (contract_id, amendment_number, description, old_terms_json,
                new_terms_json, effective_date, approved_by, approved_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (contract_id, amendment_number, description,
             json.dumps(old_terms), json.dumps(new_terms),
             now, changed_by, now, now)
        )
        self._log_history(contract_id, "amendment", None, None,
                          changed_by, f"Amendment #{amendment_number}: {description}")
        self.conn.commit()
        return amendment_number

    def list_contracts(self, status: Optional[str] = None,
                       contract_type: Optional[str] = None) -> list:
        query = "SELECT * FROM contracts WHERE 1=1"
        params = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if contract_type:
            query += " AND contract_type = ?"
            params.append(contract_type)
        query += " ORDER BY updated_at DESC"
        return [dict(r) for r in self.conn.execute(query, params).fetchall()]

    def get_contract(self, contract_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM contracts WHERE contract_id = ?", (contract_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_expiring_contracts(self, within_days: int = 90) -> list:
        cutoff = (datetime.now(timezone.utc) + timedelta(days=within_days)).strftime("%Y-%m-%d")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        rows = self.conn.execute(
            """SELECT * FROM contracts
               WHERE status = 'active' AND expiry_date IS NOT NULL
               AND expiry_date BETWEEN ? AND ?
               ORDER BY expiry_date ASC""",
            (today, cutoff)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_audit_trail(self, contract_id: str) -> list:
        rows = self.conn.execute(
            "SELECT * FROM contract_history WHERE contract_id = ? ORDER BY changed_at",
            (contract_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def _log_history(self, contract_id, action, old_status, new_status,
                     changed_by, details):
        self.conn.execute(
            """INSERT INTO contract_history
               (contract_id, action, old_status, new_status, changed_by,
                changed_at, details) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (contract_id, action, old_status, new_status, changed_by,
             datetime.now(timezone.utc).isoformat(), details)
        )

    def _create_approval_workflow(self, contract_id: str, contract_type: str):
        """Create default approval workflow based on contract type."""
        workflows = {
            "game_provider": [
                (1, "legal_counsel", "Legal review of game provider terms"),
                (2, "compliance_officer", "Regulatory compliance check"),
                (3, "cto", "Technical integration approval"),
                (4, "cfo", "Financial terms approval"),
                (5, "ceo", "Final executive sign-off"),
            ],
            "payment_processor": [
                (1, "legal_counsel", "Legal review"),
                (2, "compliance_officer", "AML/KYC compliance review"),
                (3, "head_of_payments", "Payment integration approval"),
                (4, "cfo", "Financial terms approval"),
                (5, "dpo", "Data processing review"),
            ],
            "affiliate": [
                (1, "legal_counsel", "Legal review"),
                (2, "compliance_officer", "Advertising compliance check"),
                (3, "head_of_marketing", "Marketing terms approval"),
                (4, "cfo", "Commission structure approval"),
            ],
        }
        steps = workflows.get(contract_type, [
            (1, "legal_counsel", "Legal review"),
            (2, "compliance_officer", "Compliance check"),
            (3, "department_head", "Department approval"),
        ])
        for order, role, comment in steps:
            self.conn.execute(
                """INSERT INTO approval_workflow
                   (contract_id, step_order, approver_role, status, comments)
                   VALUES (?, ?, ?, 'pending', ?)""",
                (contract_id, order, role, comment)
            )


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

class ContractReporter:
    def __init__(self, db: ContractDB):
        self.db = db

    def summary_report(self) -> dict:
        cursor = self.db.conn.execute(
            "SELECT status, COUNT(*) as cnt FROM contracts GROUP BY status"
        )
        status_counts = {row["status"]: row["cnt"] for row in cursor.fetchall()}

        cursor = self.db.conn.execute(
            "SELECT contract_type, COUNT(*) as cnt FROM contracts GROUP BY contract_type"
        )
        type_counts = {row["contract_type"]: row["cnt"] for row in cursor.fetchall()}

        expiring_30 = len(self.db.get_expiring_contracts(30))
        expiring_90 = len(self.db.get_expiring_contracts(90))

        cursor = self.db.conn.execute("""
            SELECT SUM(json_extract(financial_terms_json, '$.fixed_monthly_fee')) as total_fixed,
                   AVG(json_extract(financial_terms_json, '$.revenue_share_pct')) as avg_rev_share
            FROM contracts WHERE status = 'active'
        """)
        fin = cursor.fetchone()

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status_breakdown": status_counts,
            "type_breakdown": type_counts,
            "expiring_30_days": expiring_30,
            "expiring_90_days": expiring_90,
            "total_monthly_fixed_fees": fin["total_fixed"] or 0,
            "avg_revenue_share_pct": round(fin["avg_rev_share"] or 0, 2),
        }

    def compliance_report(self, jurisdiction: str) -> list:
        rows = self.db.conn.execute(
            """SELECT * FROM contracts
               WHERE status = 'active'
               AND jurisdictions_json LIKE ?""",
            (f'%"{jurisdiction}"%',)
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def demo():
    """Run a full demo lifecycle."""
    db = ContractDB(":memory:")

    operator = ContractParty(
        legal_name="AcmetoCasino Ltd",
        registration_number="C-87654",
        jurisdiction="mga",
        address="Level 3, Spinola Park, St Julians, Malta",
        contact_name="Maria Borg",
        contact_email="legal@acmetocasino.com",
    )

    # 1. Create a game provider contract
    vendor = ContractParty(
        legal_name="MegaSlots International",
        registration_number="NL-12345678",
        jurisdiction="mga",
        address="Keizersgracht 100, Amsterdam",
        contact_name="Jan de Vries",
        contact_email="partnerships@megaslots.com",
    )
    terms = FinancialTerms(
        model="revenue_share",
        revenue_share_pct=12.0,
        minimum_guarantee=5000.0,
        currency="EUR",
        payment_terms_days=30,
        settlement_frequency="monthly",
    )
    contract = Contract(
        contract_id="",
        contract_type=ContractType.GAME_PROVIDER,
        status=ContractStatus.DRAFT,
        vendor=vendor,
        operator=operator,
        financial_terms=terms,
        jurisdictions=["mga", "ukgc"],
        effective_date="2026-04-01",
        expiry_date="2028-03-31",
        created_by="legal_team",
        tags=["slots", "tier1"],
    )
    cid = db.create_contract(contract)
    print(f"[+] Created contract: {cid}")

    # 2. Move through approval workflow
    db.transition_status(cid, ContractStatus.PENDING_REVIEW, "legal_team",
                         "Submitted for review")
    db.transition_status(cid, ContractStatus.UNDER_LEGAL_REVIEW, "john_lawyer",
                         "Assigned to legal counsel")
    db.transition_status(cid, ContractStatus.APPROVED, "john_lawyer",
                         "All terms reviewed and approved")
    db.transition_status(cid, ContractStatus.ACTIVE, "maria_borg",
                         "Contract signed by both parties")
    print(f"[+] Contract {cid} is now ACTIVE")

    # 3. Add an amendment
    amendment_num = db.add_amendment(
        cid,
        "Increase revenue share from 12% to 14% due to exclusive title launch",
        {"revenue_share_pct": 12.0},
        {"revenue_share_pct": 14.0},
        "maria_borg"
    )
    print(f"[+] Amendment #{amendment_num} added")

    # 4. Create a payment processor contract
    psp = ContractParty(
        legal_name="PaySecure Solutions Ltd",
        registration_number="UK-99887766",
        jurisdiction="ukgc",
        address="10 Finsbury Square, London EC2A",
        contact_name="Sarah Collins",
        contact_email="partnerships@paysecure.com",
    )
    psp_terms = FinancialTerms(
        model="hybrid",
        revenue_share_pct=0.5,
        fixed_monthly_fee=2500.0,
        currency="GBP",
        payment_terms_days=14,
        settlement_frequency="weekly",
    )
    psp_contract = Contract(
        contract_id="",
        contract_type=ContractType.PAYMENT_PROCESSOR,
        status=ContractStatus.DRAFT,
        vendor=psp,
        operator=operator,
        financial_terms=psp_terms,
        jurisdictions=["ukgc", "mga"],
        effective_date="2026-05-01",
        expiry_date="2027-04-30",
        created_by="payments_team",
        tags=["psp", "cards", "ewallets"],
    )
    psp_id = db.create_contract(psp_contract)
    print(f"[+] Created payment contract: {psp_id}")

    # 5. Check expiring contracts
    expiring = db.get_expiring_contracts(within_days=900)
    print(f"\n[i] Contracts expiring within 900 days: {len(expiring)}")
    for c in expiring:
        print(f"    {c['contract_id']} - {c['contract_type']} - expires {c['expiry_date']}")

    # 6. Generate reports
    reporter = ContractReporter(db)
    summary = reporter.summary_report()
    print(f"\n{'='*60}")
    print("CONTRACT PORTFOLIO SUMMARY")
    print(f"{'='*60}")
    print(json.dumps(summary, indent=2))

    # 7. Audit trail
    trail = db.get_audit_trail(cid)
    print(f"\n{'='*60}")
    print(f"AUDIT TRAIL FOR {cid}")
    print(f"{'='*60}")
    for entry in trail:
        print(f"  [{entry['changed_at'][:19]}] {entry['action']:20s} "
              f"{entry['old_status'] or '':20s} -> {entry['new_status'] or '':20s} "
              f"by {entry['changed_by']}")
        if entry['details']:
            print(f"    Detail: {entry['details']}")

    db.close()
    print("\n[OK] Contract lifecycle demo complete.")


def main():
    parser = argparse.ArgumentParser(
        description="Contract Lifecycle Management for iGaming Operators"
    )
    parser.add_argument("--action", choices=["create", "list", "renew", "report", "demo"],
                        default="demo")
    parser.add_argument("--type", choices=[t.value for t in ContractType], default=None)
    parser.add_argument("--status", choices=[s.value for s in ContractStatus], default=None)
    parser.add_argument("--contract-id", default=None)
    parser.add_argument("--vendor", default=None)
    parser.add_argument("--db", default="contracts.db")
    parser.add_argument("--format", choices=["json", "text"], default="text")

    args = parser.parse_args()

    if args.action == "demo":
        demo()
        return

    db = ContractDB(args.db)
    try:
        if args.action == "list":
            contracts = db.list_contracts(status=args.status, contract_type=args.type)
            if args.format == "json":
                print(json.dumps(contracts, indent=2))
            else:
                print(f"{'ID':<18} {'Type':<22} {'Status':<18} {'Vendor':<30} {'Expiry'}")
                print("-" * 105)
                for c in contracts:
                    vendor = json.loads(c["vendor_json"])
                    print(f"{c['contract_id']:<18} {c['contract_type']:<22} "
                          f"{c['status']:<18} {vendor['legal_name']:<30} "
                          f"{c['expiry_date'] or 'N/A'}")
        elif args.action == "report":
            reporter = ContractReporter(db)
            print(json.dumps(reporter.summary_report(), indent=2))
        elif args.action == "renew" and args.contract_id:
            new_expiry = (datetime.now(timezone.utc) + timedelta(days=365)).strftime("%Y-%m-%d")
            db.renew_contract(args.contract_id, new_expiry, "cli_user")
            print(f"[+] Contract {args.contract_id} renewed until {new_expiry}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
