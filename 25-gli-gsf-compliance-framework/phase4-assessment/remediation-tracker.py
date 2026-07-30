#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 25, GLI-GSF Compliance Framework.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
remediation-tracker.py - Remediation Timeline Tracker with SLA Enforcement
GLI-GSF Phase 4 - Finding Remediation Management

Tracks remediation of security findings with GLI-GSF SLA enforcement:
  - Critical (CVSS 9.0-10.0): 24 hours
  - High (CVSS 7.0-8.9): 7 days
  - Medium (CVSS 4.0-6.9): 30 days
  - Low (CVSS 0.1-3.9): Next quarterly cycle

Features:
  - Import findings from scan tools (JSON/CSV)
  - Automatic SLA deadline calculation
  - Escalation notifications when SLA is at risk
  - Dashboard view of remediation status
  - Evidence attachment for completed remediations
  - Export for ISF evidence packages

GLI-GSF-4 Reference: Section 5.3 - Remediation Management

Usage:
    python3 remediation-tracker.py add --title "SQLi in login" --severity critical --owner "Dev Team"
    python3 remediation-tracker.py list
    python3 remediation-tracker.py update --id FIND-001 --status remediated
    python3 remediation-tracker.py check-sla
    python3 remediation-tracker.py export --format json --output findings.json
    python3 remediation-tracker.py demo

Requirements:
    No external dependencies (standard library only)
"""

import argparse
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

VERSION = "1.0.0"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("remediation-tracker")

DB_PATH = os.environ.get("REMEDIATION_DB", "./remediation-tracker.json")

# GLI-GSF SLA definitions
SLA_RULES = {
    "critical": {"days": 1, "label": "24 hours", "escalate_at_pct": 50},
    "high":     {"days": 7, "label": "7 days", "escalate_at_pct": 70},
    "medium":   {"days": 30, "label": "30 days", "escalate_at_pct": 80},
    "low":      {"days": 90, "label": "Quarterly", "escalate_at_pct": 90},
}


@dataclass
class Finding:
    finding_id: str
    title: str
    description: str
    severity: str
    cvss_score: float
    source: str                    # Tool that found it (ZAP, Nmap, pentest, etc.)
    ogis_domain: str
    affected_system: str
    owner: str
    status: str                    # open, in_progress, remediated, accepted, verified
    created_at: str
    deadline: str
    remediated_at: Optional[str] = None
    verified_at: Optional[str] = None
    evidence_path: Optional[str] = None
    notes: str = ""
    escalated: bool = False
    sla_breached: bool = False

    @property
    def days_remaining(self) -> int:
        now = datetime.now(timezone.utc)
        dl = datetime.fromisoformat(self.deadline.replace("Z", "+00:00"))
        return (dl - now).days

    @property
    def is_overdue(self) -> bool:
        return self.days_remaining < 0 and self.status not in ("remediated", "verified", "accepted")


class RemediationTracker:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.findings: Dict[str, Finding] = {}
        self._counter = 0
        self._load()

    def _load(self):
        p = Path(self.db_path)
        if p.exists():
            try:
                data = json.loads(p.read_text())
                self._counter = data.get("counter", 0)
                for fid, fdata in data.get("findings", {}).items():
                    self.findings[fid] = Finding(**fdata)
            except (json.JSONDecodeError, TypeError) as e:
                logger.error(f"Failed to load DB: {e}")

    def _save(self):
        data = {
            "counter": self._counter,
            "findings": {fid: asdict(f) for fid, f in self.findings.items()},
        }
        Path(self.db_path).write_text(json.dumps(data, indent=2, default=str))

    def add(self, title: str, severity: str, description: str = "",
            cvss_score: float = 0.0, source: str = "manual",
            ogis_domain: str = "", affected_system: str = "",
            owner: str = "") -> Finding:
        self._counter += 1
        fid = f"FIND-{self._counter:04d}"
        now = datetime.now(timezone.utc)
        sla = SLA_RULES.get(severity.lower(), SLA_RULES["medium"])
        deadline = (now + timedelta(days=sla["days"])).isoformat()  # ty:ignore[invalid-argument-type]

        f = Finding(
            finding_id=fid, title=title, description=description,
            severity=severity.lower(), cvss_score=cvss_score, source=source,
            ogis_domain=ogis_domain, affected_system=affected_system,
            owner=owner, status="open", created_at=now.isoformat(),
            deadline=deadline,
        )
        self.findings[fid] = f
        self._save()
        logger.info(f"Added {fid}: {title} ({severity}) - deadline: {sla['label']}")
        return f

    def update_status(self, finding_id: str, status: str,
                      notes: str = "", evidence: str = "") -> Finding:
        f = self.findings.get(finding_id)
        if not f:
            raise KeyError(f"Finding not found: {finding_id}")
        f.status = status
        if notes:
            f.notes = notes
        if evidence:
            f.evidence_path = evidence
        now = datetime.now(timezone.utc).isoformat()
        if status == "remediated":
            f.remediated_at = now
        elif status == "verified":
            f.verified_at = now
        self._save()
        return f

    def check_sla(self) -> List[Finding]:
        """Check all findings for SLA compliance. Returns findings at risk."""
        at_risk = []
        now = datetime.now(timezone.utc)
        for f in self.findings.values():
            if f.status in ("remediated", "verified", "accepted"):
                continue
            dl = datetime.fromisoformat(f.deadline.replace("Z", "+00:00"))
            total_days = SLA_RULES.get(f.severity, {}).get("days", 30)
            elapsed = (now - datetime.fromisoformat(f.created_at.replace("Z", "+00:00"))).days
            pct = (elapsed / total_days * 100) if total_days > 0 else 100  # ty:ignore[unsupported-operator]
            escalate_pct = SLA_RULES.get(f.severity, {}).get("escalate_at_pct", 80)

            if f.is_overdue:
                f.sla_breached = True
                at_risk.append(f)
            elif pct >= escalate_pct:  # ty:ignore[unsupported-operator]
                f.escalated = True
                at_risk.append(f)
        self._save()
        return at_risk

    def dashboard(self):
        NC = "\033[0m"; RED = "\033[0;31m"; GREEN = "\033[0;32m"
        YELLOW = "\033[1;33m"; CYAN = "\033[0;36m"

        findings = list(self.findings.values())
        open_f = [f for f in findings if f.status in ("open", "in_progress")]
        remediated = [f for f in findings if f.status == "remediated"]
        verified = [f for f in findings if f.status == "verified"]
        breached = [f for f in findings if f.sla_breached]

        print(f"\n{'=' * 70}")
        print(f"  GLI-GSF Remediation Tracker Dashboard")
        print(f"{'=' * 70}\n")
        print(f"  Total: {len(findings)}  Open: {len(open_f)}  "
              f"Remediated: {len(remediated)}  Verified: {len(verified)}  "
              f"SLA Breached: {RED}{len(breached)}{NC}\n")

        if not findings:
            print("  No findings tracked.\n")
            return

        sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        sorted_f = sorted(findings, key=lambda x: (sev_order.get(x.severity, 9), x.deadline))

        print(f"  {'ID':<12} {'Severity':<10} {'Status':<14} {'Days Left':<10} {'Owner':<15} {'Title'}")
        print(f"  {'-'*12} {'-'*10} {'-'*14} {'-'*10} {'-'*15} {'-'*30}")

        for f in sorted_f:
            sev_colors = {"critical": RED, "high": YELLOW, "medium": CYAN, "low": GREEN}
            sc = sev_colors.get(f.severity, NC)
            days = f.days_remaining if f.status not in ("remediated", "verified") else "-"
            status_color = RED if f.sla_breached else (GREEN if f.status in ("remediated", "verified") else NC)
            print(f"  {f.finding_id:<12} {sc}{f.severity:<10}{NC} "
                  f"{status_color}{f.status:<14}{NC} {str(days):<10} "
                  f"{f.owner:<15} {f.title[:40]}")

        print(f"\n  SLA Reference: Critical=24h, High=7d, Medium=30d, Low=Quarterly")
        print(f"{'=' * 70}\n")

    def export_json(self) -> str:
        return json.dumps({
            "document_type": "GLI-GSF Remediation Tracker Export",
            "gli_gsf_reference": "GLI-GSF-4, Section 5.3",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "total": len(self.findings),
                "open": sum(1 for f in self.findings.values() if f.status == "open"),
                "in_progress": sum(1 for f in self.findings.values() if f.status == "in_progress"),
                "remediated": sum(1 for f in self.findings.values() if f.status == "remediated"),
                "sla_breached": sum(1 for f in self.findings.values() if f.sla_breached),
            },
            "sla_rules": SLA_RULES,
            "findings": [asdict(f) for f in self.findings.values()],
        }, indent=2, default=str)


def run_demo():
    print("\n" + "=" * 70)
    print("  GLI-GSF Remediation Tracker - Demo")
    print("=" * 70)

    db = "./demo-remediation.json"
    t = RemediationTracker(db_path=db)

    demo_findings = [
        ("SQL Injection in player registration", "critical", 9.8, "OWASP ZAP", "OGIS-3", "Player API", "Dev Team"),
        ("Missing MFA on admin panel", "high", 8.1, "MFA Audit", "OGIS-2", "Back-office", "IT Ops"),
        ("TLS 1.0 enabled on game API", "high", 7.4, "testssl.sh", "OGIS-3", "Game API", "Infra Team"),
        ("Directory listing on CDN", "medium", 5.3, "Nikto", "OGIS-3", "CDN", "Infra Team"),
        ("Missing HSTS header", "medium", 4.2, "OWASP ZAP", "OGIS-3", "Web App", "Dev Team"),
        ("Verbose error messages", "low", 3.1, "Manual review", "OGIS-3", "API Gateway", "Dev Team"),
        ("Outdated jQuery version", "low", 2.5, "SCA scan", "OGIS-3", "Frontend", "Dev Team"),
    ]

    for title, sev, cvss, src, ogis, sys_, owner in demo_findings:
        t.add(title, sev, cvss_score=cvss, source=src, ogis_domain=ogis,
              affected_system=sys_, owner=owner)

    # Simulate some remediations
    t.update_status("FIND-0001", "remediated", notes="Parameterized queries deployed")
    t.update_status("FIND-0002", "in_progress", notes="MFA rollout in progress")

    t.check_sla()
    t.dashboard()

    # Cleanup
    Path(db).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="GLI-GSF Remediation Tracker")
    sub = parser.add_subparsers(dest="command")

    add = sub.add_parser("add", help="Add a finding")
    add.add_argument("--title", required=True)
    add.add_argument("--severity", required=True, choices=["critical", "high", "medium", "low"])
    add.add_argument("--cvss", type=float, default=0.0)
    add.add_argument("--source", default="manual")
    add.add_argument("--ogis", default="")
    add.add_argument("--system", default="")
    add.add_argument("--owner", default="")
    add.add_argument("--description", default="")

    upd = sub.add_parser("update", help="Update finding status")
    upd.add_argument("--id", required=True)
    upd.add_argument("--status", required=True, choices=["open", "in_progress", "remediated", "verified", "accepted"])
    upd.add_argument("--notes", default="")
    upd.add_argument("--evidence", default="")

    sub.add_parser("list", help="Show dashboard")
    sub.add_parser("check-sla", help="Check SLA compliance")

    exp = sub.add_parser("export", help="Export findings")
    exp.add_argument("--format", default="json", choices=["json"])
    exp.add_argument("--output", help="Output file")

    sub.add_parser("demo", help="Run demo")

    args = parser.parse_args()
    t = RemediationTracker()

    if args.command == "add":
        f = t.add(args.title, args.severity, args.description, args.cvss,
                  args.source, args.ogis, args.system, args.owner)
        print(f"Added: {f.finding_id}")
    elif args.command == "update":
        f = t.update_status(args.id, args.status, args.notes, args.evidence)
        print(f"Updated: {f.finding_id} -> {f.status}")
    elif args.command == "list":
        t.check_sla()
        t.dashboard()
    elif args.command == "check-sla":
        at_risk = t.check_sla()
        if at_risk:
            print(f"\n  {len(at_risk)} findings at risk:")
            for f in at_risk:
                label = "BREACHED" if f.sla_breached else "AT RISK"
                print(f"  [{label}] {f.finding_id}: {f.title} ({f.severity}, {f.days_remaining}d remaining)")
            sys.exit(1)
        else:
            print("  All findings within SLA.")
    elif args.command == "export":
        data = t.export_json()
        if hasattr(args, 'output') and args.output:
            Path(args.output).write_text(data)
            print(f"Exported to {args.output}")
        else:
            print(data)
    elif args.command == "demo":
        run_demo()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
