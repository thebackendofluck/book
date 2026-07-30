#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 06, Licensing Guide.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Ongoing Compliance Monitoring Checklist per Jurisdiction
==========================================================

Tracks recurring compliance obligations for each licensed jurisdiction
including periodic reporting, audits, certifications, and regulatory
submissions.

Usage:
    python ongoing_compliance.py --jurisdiction MGA --status
    python ongoing_compliance.py --all --upcoming 30
    python ongoing_compliance.py --overdue
    python ongoing_compliance.py --calendar 2026
"""

import json
import logging
import argparse
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


class ComplianceFrequency(Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    SEMI_ANNUAL = "semi_annual"
    ANNUAL = "annual"
    AD_HOC = "ad_hoc"


class ObligationCategory(Enum):
    REPORTING = "reporting"
    FINANCIAL = "financial"
    AML_CTF = "aml_ctf"
    RESPONSIBLE_GAMING = "responsible_gaming"
    TECHNICAL = "technical"
    DATA_PROTECTION = "data_protection"
    ADVERTISING = "advertising"
    TAX = "tax"
    CORPORATE = "corporate"
    AUDIT = "audit"


class ObligationStatus(Enum):
    COMPLIANT = "compliant"           # up to date
    DUE_SOON = "due_soon"             # within 14 days
    OVERDUE = "overdue"               # past deadline
    IN_PROGRESS = "in_progress"       # being worked on
    NOT_APPLICABLE = "not_applicable"


@dataclass
class ComplianceObligation:
    """A single compliance obligation."""
    id: str
    jurisdiction: str
    category: ObligationCategory
    name: str
    description: str
    frequency: ComplianceFrequency
    regulatory_reference: str = ""      # specific regulation/condition
    responsible_role: str = ""
    next_deadline: Optional[str] = None
    last_completed: Optional[str] = None
    status: ObligationStatus = ObligationStatus.COMPLIANT
    penalty_for_breach: str = ""
    automation_possible: bool = False
    automation_notes: str = ""
    evidence_required: list = field(default_factory=list)
    dependencies: list = field(default_factory=list)
    notes: str = ""


# ---------------------------------------------------------------------------
# Jurisdiction obligation databases
# ---------------------------------------------------------------------------

def _mga_obligations() -> list[ComplianceObligation]:
    """Malta Gaming Authority ongoing compliance obligations."""
    return [
        # Reporting
        ComplianceObligation(
            "MGA-OC-001", "MGA", ObligationCategory.REPORTING,
            "Monthly Player Activity Report",
            "Submit monthly gaming activity statistics to MGA including GGR, "
            "player counts, deposits, withdrawals, bonus costs, and game-level data.",
            ComplianceFrequency.MONTHLY,
            regulatory_reference="MGA Directive 2 - Player Activity Reports",
            responsible_role="BI/Analytics Director",
            next_deadline="2026-04-15",
            last_completed="2026-03-14",
            automation_possible=True,
            automation_notes="Can be automated via MGA reporting API or SFTP upload",
            evidence_required=["Submitted report confirmation", "Data reconciliation log"],
            penalty_for_breach="Administrative penalty up to EUR 50,000",
        ),
        ComplianceObligation(
            "MGA-OC-002", "MGA", ObligationCategory.REPORTING,
            "Key Event Notification",
            "Notify MGA within 72 hours of material events: data breaches, system failures, "
            "fraud incidents, changes to key persons, corporate structure changes.",
            ComplianceFrequency.AD_HOC,
            regulatory_reference="MGA Directive 11 - Key Events",
            responsible_role="Compliance Director",
            automation_possible=False,
            penalty_for_breach="License conditions or suspension",
        ),
        ComplianceObligation(
            "MGA-OC-003", "MGA", ObligationCategory.REPORTING,
            "Regulatory Return (Annual)",
            "Annual regulatory return covering financial performance, player metrics, "
            "compliance incidents, and organizational changes.",
            ComplianceFrequency.ANNUAL,
            responsible_role="Compliance Director",
            next_deadline="2026-07-31",
            penalty_for_breach="Administrative penalty; possible license conditions",
        ),

        # AML/CFT
        ComplianceObligation(
            "MGA-OC-010", "MGA", ObligationCategory.AML_CTF,
            "STR Filing (Suspicious Transaction Reports)",
            "File STRs with FIAU within prescribed timeframes when suspicious activity identified.",
            ComplianceFrequency.AD_HOC,
            regulatory_reference="PMLA Chapter 373, FIAU Implementing Procedures",
            responsible_role="MLRO",
            penalty_for_breach="Criminal liability; license revocation",
        ),
        ComplianceObligation(
            "MGA-OC-011", "MGA", ObligationCategory.AML_CTF,
            "AML Risk Assessment Review",
            "Review and update business-wide AML/CFT risk assessment.",
            ComplianceFrequency.ANNUAL,
            responsible_role="MLRO",
            next_deadline="2026-06-30",
            evidence_required=["Updated risk assessment document", "Board approval minutes"],
        ),
        ComplianceObligation(
            "MGA-OC-012", "MGA", ObligationCategory.AML_CTF,
            "MLRO Annual Report to Board",
            "MLRO presents annual compliance report to Board of Directors.",
            ComplianceFrequency.ANNUAL,
            responsible_role="MLRO",
            next_deadline="2026-03-31",
            evidence_required=["MLRO report", "Board meeting minutes"],
        ),
        ComplianceObligation(
            "MGA-OC-013", "MGA", ObligationCategory.AML_CTF,
            "AML Staff Training",
            "Conduct AML/CFT training for all relevant staff. New hires within 30 days.",
            ComplianceFrequency.ANNUAL,
            responsible_role="Compliance Training Officer",
            next_deadline="2026-12-31",
            evidence_required=["Training records", "Attendance logs", "Test results"],
            automation_possible=True,
            automation_notes="LMS-based training with automated tracking",
        ),

        # Responsible Gaming
        ComplianceObligation(
            "MGA-OC-020", "MGA", ObligationCategory.RESPONSIBLE_GAMING,
            "Player Protection Review",
            "Review effectiveness of player protection measures including limits, "
            "self-exclusion, reality checks, and intervention procedures.",
            ComplianceFrequency.QUARTERLY,
            responsible_role="Responsible Gaming Manager",
            next_deadline="2026-03-31",
            evidence_required=["Review report", "Player interaction statistics"],
        ),
        ComplianceObligation(
            "MGA-OC-021", "MGA", ObligationCategory.RESPONSIBLE_GAMING,
            "Self-Exclusion Database Sync",
            "Maintain synchronization with national/international self-exclusion databases.",
            ComplianceFrequency.DAILY,
            responsible_role="Technical Operations",
            automation_possible=True,
            automation_notes="Automated daily sync via API",
        ),

        # Technical
        ComplianceObligation(
            "MGA-OC-030", "MGA", ObligationCategory.TECHNICAL,
            "Penetration Test",
            "Commission external penetration test of all player-facing and back-office systems.",
            ComplianceFrequency.ANNUAL,
            responsible_role="CISO",
            next_deadline="2026-09-30",
            evidence_required=["Pentest report", "Remediation evidence"],
            penalty_for_breach="License conditions",
        ),
        ComplianceObligation(
            "MGA-OC-031", "MGA", ObligationCategory.TECHNICAL,
            "Business Continuity Test",
            "Test DR/BCP procedures including failover and recovery.",
            ComplianceFrequency.ANNUAL,
            responsible_role="CTO / Head of Infrastructure",
            next_deadline="2026-06-30",
            evidence_required=["DR test report", "RTO/RPO results"],
        ),
        ComplianceObligation(
            "MGA-OC-032", "MGA", ObligationCategory.TECHNICAL,
            "Game/RNG Change Certification",
            "Submit material game or RNG changes to approved testing lab.",
            ComplianceFrequency.AD_HOC,
            responsible_role="Game Development Lead",
        ),

        # Financial / Tax
        ComplianceObligation(
            "MGA-OC-040", "MGA", ObligationCategory.TAX,
            "Gaming Tax Payment",
            "Monthly gaming tax payment (5% of GGR, minimum EUR 4,500/month).",
            ComplianceFrequency.MONTHLY,
            responsible_role="Finance Director",
            next_deadline="2026-04-15",
            last_completed="2026-03-14",
            automation_possible=True,
            penalty_for_breach="Interest charges; potential license suspension",
        ),
        ComplianceObligation(
            "MGA-OC-041", "MGA", ObligationCategory.FINANCIAL,
            "Player Funds Reconciliation",
            "Monthly reconciliation of player funds in segregated accounts.",
            ComplianceFrequency.MONTHLY,
            responsible_role="Finance Director",
            next_deadline="2026-04-05",
            automation_possible=True,
            penalty_for_breach="License conditions; potential suspension",
        ),
        ComplianceObligation(
            "MGA-OC-042", "MGA", ObligationCategory.AUDIT,
            "Annual Financial Audit",
            "Submit audited financial statements to MGA.",
            ComplianceFrequency.ANNUAL,
            responsible_role="CFO",
            next_deadline="2026-06-30",
            evidence_required=["Audited accounts", "Auditor's report"],
            dependencies=["Appoint auditor by Q1"],
        ),

        # Data Protection
        ComplianceObligation(
            "MGA-OC-050", "MGA", ObligationCategory.DATA_PROTECTION,
            "GDPR Data Processing Review",
            "Review data processing activities, update ROPA, assess new processing purposes.",
            ComplianceFrequency.ANNUAL,
            responsible_role="DPO",
            next_deadline="2026-12-31",
            evidence_required=["Updated ROPA", "DPIA reviews"],
        ),
        ComplianceObligation(
            "MGA-OC-051", "MGA", ObligationCategory.DATA_PROTECTION,
            "Data Breach Notification Readiness",
            "Test data breach notification procedures (72-hour GDPR requirement).",
            ComplianceFrequency.SEMI_ANNUAL,
            responsible_role="DPO / CISO",
            next_deadline="2026-06-30",
        ),
    ]


def _ukgc_obligations() -> list[ComplianceObligation]:
    """UK Gambling Commission ongoing obligations."""
    return [
        ComplianceObligation(
            "UKGC-OC-001", "UKGC", ObligationCategory.REPORTING,
            "Regulatory Return",
            "Submit quarterly regulatory return with financial and operational data.",
            ComplianceFrequency.QUARTERLY,
            regulatory_reference="LCCP SR Code 15.1",
            responsible_role="Compliance Director",
            next_deadline="2026-04-30",
            automation_possible=True,
        ),
        ComplianceObligation(
            "UKGC-OC-002", "UKGC", ObligationCategory.REPORTING,
            "Key Event Notification",
            "Report notifiable events within 5 working days.",
            ComplianceFrequency.AD_HOC,
            regulatory_reference="LCCP SR Code 15.2",
            responsible_role="Compliance Director",
        ),
        ComplianceObligation(
            "UKGC-OC-003", "UKGC", ObligationCategory.REPORTING,
            "Annual Report Submission",
            "Annual compliance report to UKGC.",
            ComplianceFrequency.ANNUAL,
            responsible_role="Compliance Director",
            next_deadline="2026-09-30",
        ),
        ComplianceObligation(
            "UKGC-OC-010", "UKGC", ObligationCategory.AML_CTF,
            "SAR Filing",
            "File Suspicious Activity Reports to NCA UKFIU.",
            ComplianceFrequency.AD_HOC,
            regulatory_reference="POCA 2002, ML Regulations 2017",
            responsible_role="MLRO",
            penalty_for_breach="Criminal offence; unlimited fine",
        ),
        ComplianceObligation(
            "UKGC-OC-011", "UKGC", ObligationCategory.AML_CTF,
            "AML/CTF Policy Review",
            "Annual review of AML/CTF policies, procedures, and controls.",
            ComplianceFrequency.ANNUAL,
            responsible_role="MLRO",
            next_deadline="2026-06-30",
        ),
        ComplianceObligation(
            "UKGC-OC-020", "UKGC", ObligationCategory.RESPONSIBLE_GAMING,
            "GAMSTOP Self-Exclusion Sync",
            "Maintain real-time sync with GAMSTOP national self-exclusion database.",
            ComplianceFrequency.DAILY,
            regulatory_reference="LCCP SR Code 3.5",
            responsible_role="Technical Operations",
            automation_possible=True,
            penalty_for_breach="Regulatory action; fines up to GBP millions",
        ),
        ComplianceObligation(
            "UKGC-OC-021", "UKGC", ObligationCategory.RESPONSIBLE_GAMING,
            "Customer Interaction Evaluation",
            "Evaluate and enhance customer interaction procedures for at-risk players.",
            ComplianceFrequency.QUARTERLY,
            regulatory_reference="LCCP SR Code 3.4",
            responsible_role="Responsible Gaming Manager",
            next_deadline="2026-03-31",
        ),
        ComplianceObligation(
            "UKGC-OC-022", "UKGC", ObligationCategory.RESPONSIBLE_GAMING,
            "Affordability Checks Compliance",
            "Verify financial vulnerability/affordability check thresholds and procedures.",
            ComplianceFrequency.MONTHLY,
            responsible_role="Compliance Director",
            next_deadline="2026-04-01",
            automation_possible=True,
        ),
        ComplianceObligation(
            "UKGC-OC-030", "UKGC", ObligationCategory.TAX,
            "Remote Gaming Duty Payment",
            "Monthly payment of 21% Remote Gaming Duty on gross gambling yield.",
            ComplianceFrequency.MONTHLY,
            responsible_role="Finance Director",
            next_deadline="2026-04-30",
            automation_possible=True,
        ),
        ComplianceObligation(
            "UKGC-OC-040", "UKGC", ObligationCategory.TECHNICAL,
            "Testing of Game/Software Changes",
            "Submit material software changes to approved testing facility.",
            ComplianceFrequency.AD_HOC,
            regulatory_reference="LCCP SR Code 6",
            responsible_role="CTO",
        ),
        ComplianceObligation(
            "UKGC-OC-050", "UKGC", ObligationCategory.ADVERTISING,
            "Marketing Compliance Review",
            "Review all marketing materials for ASA/CAP Code and LCCP compliance.",
            ComplianceFrequency.MONTHLY,
            regulatory_reference="LCCP SR Code 5",
            responsible_role="Marketing Compliance Officer",
            next_deadline="2026-04-01",
        ),
    ]


JURISDICTION_OBLIGATIONS = {
    "MGA": ("Malta Gaming Authority", _mga_obligations),
    "UKGC": ("UK Gambling Commission", _ukgc_obligations),
}


# ---------------------------------------------------------------------------
# Compliance monitoring engine
# ---------------------------------------------------------------------------

class OngoingComplianceMonitor:
    """Monitor and track ongoing compliance obligations."""

    def __init__(self):
        self.obligations: dict[str, list[ComplianceObligation]] = {}

    def load_jurisdiction(self, code: str):
        code = code.upper()
        if code in JURISDICTION_OBLIGATIONS:
            _, fn = JURISDICTION_OBLIGATIONS[code]
            self.obligations[code] = fn()
            logger.info("Loaded %d obligations for %s", len(self.obligations[code]), code)

    def load_all(self):
        for code in JURISDICTION_OBLIGATIONS:
            self.load_jurisdiction(code)

    def get_status(self, jurisdiction: Optional[str] = None) -> dict:
        """Get compliance status overview."""
        if jurisdiction:
            self.load_jurisdiction(jurisdiction)
            all_obs = {jurisdiction.upper(): self.obligations.get(jurisdiction.upper(), [])}
        else:
            self.load_all()
            all_obs = self.obligations

        results = {}
        for code, obs_list in all_obs.items():
            items = []
            for ob in obs_list:
                status = self._calculate_status(ob)
                items.append({
                    "id": ob.id,
                    "name": ob.name,
                    "category": ob.category.value,
                    "frequency": ob.frequency.value,
                    "next_deadline": ob.next_deadline,
                    "status": status.value,
                    "responsible": ob.responsible_role,
                    "automatable": ob.automation_possible,
                })
            results[code] = {
                "jurisdiction": JURISDICTION_OBLIGATIONS[code][0],
                "total_obligations": len(items),
                "compliant": sum(1 for i in items if i["status"] == "compliant"),
                "due_soon": sum(1 for i in items if i["status"] == "due_soon"),
                "overdue": sum(1 for i in items if i["status"] == "overdue"),
                "automatable": sum(1 for i in items if i["automatable"]),
                "obligations": items,
            }

        return {"report_date": datetime.now().strftime("%Y-%m-%d"), "jurisdictions": results}

    def get_overdue(self) -> list[dict]:
        """Get all overdue obligations."""
        self.load_all()
        overdue = []
        for code, obs_list in self.obligations.items():
            for ob in obs_list:
                if ob.next_deadline:
                    deadline = datetime.strptime(ob.next_deadline, "%Y-%m-%d")
                    if deadline < datetime.now():
                        overdue.append({
                            "id": ob.id,
                            "jurisdiction": code,
                            "name": ob.name,
                            "deadline": ob.next_deadline,
                            "days_overdue": (datetime.now() - deadline).days,
                            "responsible": ob.responsible_role,
                            "penalty": ob.penalty_for_breach,
                        })
        overdue.sort(key=lambda x: x["days_overdue"], reverse=True)
        return overdue

    def get_upcoming(self, days: int = 30) -> list[dict]:
        """Get obligations due within N days."""
        self.load_all()
        upcoming = []
        cutoff = datetime.now() + timedelta(days=days)
        for code, obs_list in self.obligations.items():
            for ob in obs_list:
                if ob.next_deadline:
                    deadline = datetime.strptime(ob.next_deadline, "%Y-%m-%d")
                    if datetime.now() <= deadline <= cutoff:
                        upcoming.append({
                            "id": ob.id,
                            "jurisdiction": code,
                            "name": ob.name,
                            "deadline": ob.next_deadline,
                            "days_remaining": (deadline - datetime.now()).days,
                            "responsible": ob.responsible_role,
                            "category": ob.category.value,
                        })
        upcoming.sort(key=lambda x: x["days_remaining"])
        return upcoming

    def _calculate_status(self, ob: ComplianceObligation) -> ObligationStatus:
        if not ob.next_deadline:
            return ObligationStatus.COMPLIANT if ob.frequency == ComplianceFrequency.AD_HOC else ObligationStatus.COMPLIANT
        deadline = datetime.strptime(ob.next_deadline, "%Y-%m-%d")
        days = (deadline - datetime.now()).days
        if days < 0:
            return ObligationStatus.OVERDUE
        elif days < 14:
            return ObligationStatus.DUE_SOON
        return ObligationStatus.COMPLIANT

    def get_automation_opportunities(self) -> list[dict]:
        """List obligations that can be automated."""
        self.load_all()
        automatable = []
        for code, obs_list in self.obligations.items():
            for ob in obs_list:
                if ob.automation_possible:
                    automatable.append({
                        "id": ob.id,
                        "jurisdiction": code,
                        "name": ob.name,
                        "frequency": ob.frequency.value,
                        "notes": ob.automation_notes,
                    })
        return automatable


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="iGaming Ongoing Compliance Monitor")
    parser.add_argument("--jurisdiction", type=str, help="Jurisdiction code")
    parser.add_argument("--status", action="store_true", help="Compliance status")
    parser.add_argument("--all", action="store_true", help="All jurisdictions")
    parser.add_argument("--overdue", action="store_true", help="Show overdue obligations")
    parser.add_argument("--upcoming", type=int, help="Obligations due within N days")
    parser.add_argument("--automation", action="store_true", help="Automation opportunities")
    parser.add_argument("--format", choices=["json", "text"], default="text")
    args = parser.parse_args()

    monitor = OngoingComplianceMonitor()

    if args.overdue:
        result = monitor.get_overdue()
        if args.format == "json":
            print(json.dumps(result, indent=2))
        else:
            print(f"\n=== Overdue Compliance Obligations ===\n")
            if not result:
                print("  No overdue obligations. All compliant.")
            for item in result:
                print(f"  [{item['jurisdiction']}] {item['name']}")
                print(f"    Deadline: {item['deadline']} ({item['days_overdue']} days overdue)")
                print(f"    Responsible: {item['responsible']}")
                if item["penalty"]:
                    print(f"    Penalty: {item['penalty']}")
                print()
        return

    if args.upcoming is not None:
        result = monitor.get_upcoming(args.upcoming)
        if args.format == "json":
            print(json.dumps(result, indent=2))
        else:
            print(f"\n=== Obligations Due Within {args.upcoming} Days ===\n")
            for item in result:
                print(f"  [{item['jurisdiction']}] {item['name']}")
                print(f"    Due: {item['deadline']} ({item['days_remaining']} days)")
                print(f"    Owner: {item['responsible']}")
                print()
        return

    if args.automation:
        result = monitor.get_automation_opportunities()
        print(json.dumps(result, indent=2))
        return

    # Default: status
    jur = args.jurisdiction if args.jurisdiction else None
    result = monitor.get_status(jur)
    if args.format == "json":
        print(json.dumps(result, indent=2))
    else:
        print(f"\n{'='*70}")
        print(f"  Ongoing Compliance Status — {result['report_date']}")
        print(f"{'='*70}")
        for code, data in result["jurisdictions"].items():
            print(f"\n  {data['jurisdiction']} ({code})")
            print(f"  Total: {data['total_obligations']}  |  Compliant: {data['compliant']}  |  "
                  f"Due Soon: {data['due_soon']}  |  Overdue: {data['overdue']}  |  "
                  f"Automatable: {data['automatable']}")
            print(f"  {'ID':<15} {'Name':<45} {'Freq':<12} {'Status':<12}")
            print(f"  {'-'*84}")
            for ob in data["obligations"]:
                status_marker = {"compliant": "OK", "due_soon": "DUE!", "overdue": "LATE"}.get(ob["status"], ob["status"])
                print(f"  {ob['id']:<15} {ob['name'][:44]:<45} {ob['frequency']:<12} {status_marker:<12}")


if __name__ == "__main__":
    main()
