#!/usr/bin/env python3
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
Incident Response Orchestration System for iGaming Platforms.

Manages the full incident lifecycle: detection, triage, escalation,
response coordination, communication, and post-mortem.

Follows ITIL incident management and gaming-specific regulatory
requirements (MGA, UKGC, Curacao).

Usage:
    python incident_commander.py declare --severity P1 --title "Payment gateway timeout"
    python incident_commander.py escalate --incident-id INC-2026-0042
    python incident_commander.py resolve --incident-id INC-2026-0042 --root-cause "DB connection pool exhaustion"
    python incident_commander.py report --incident-id INC-2026-0042 --format regulatory
"""

import argparse
import json
import logging
import os
import sys
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("incident_commander")


# ---------------------------------------------------------------------------
# Domain Models
# ---------------------------------------------------------------------------

class Severity(Enum):
    P1 = "P1"  # Critical: platform down, payments blocked, data breach
    P2 = "P2"  # High: degraded service, single game provider down
    P3 = "P3"  # Medium: non-critical feature outage
    P4 = "P4"  # Low: cosmetic issues, non-urgent bugs


class IncidentState(Enum):
    DECLARED = "declared"
    INVESTIGATING = "investigating"
    IDENTIFIED = "identified"
    MITIGATING = "mitigating"
    RESOLVED = "resolved"
    POST_MORTEM = "post_mortem"
    CLOSED = "closed"


class IncidentCategory(Enum):
    PLATFORM_OUTAGE = "platform_outage"
    PAYMENT_FAILURE = "payment_failure"
    GAME_PROVIDER = "game_provider"
    DATA_BREACH = "data_breach"
    REGULATORY_COMPLIANCE = "regulatory_compliance"
    SECURITY_INCIDENT = "security_incident"
    PERFORMANCE_DEGRADATION = "performance_degradation"
    THIRD_PARTY_DEPENDENCY = "third_party_dependency"


# Regulatory notification windows by jurisdiction (hours)
REGULATORY_NOTIFICATION = {
    "UKGC": {"data_breach": 72, "platform_outage": 24, "payment_failure": 24},
    "MGA": {"data_breach": 72, "platform_outage": 48, "payment_failure": 48},
    "Curacao": {"data_breach": 72, "platform_outage": 72, "payment_failure": 72},
    "ONJN_Romania": {"data_breach": 24, "platform_outage": 24, "payment_failure": 24},
    "SGA_Sweden": {"data_breach": 72, "platform_outage": 24, "payment_failure": 24},
    "AGCO_Ontario": {"data_breach": 48, "platform_outage": 24, "payment_failure": 24},
}


@dataclass
class TimelineEntry:
    timestamp: str
    action: str
    actor: str
    details: str


@dataclass
class Incident:
    id: str
    title: str
    severity: str
    category: str
    state: str
    declared_at: str
    declared_by: str
    description: str = ""
    affected_services: list = field(default_factory=list)
    affected_jurisdictions: list = field(default_factory=list)
    incident_commander: str = ""
    communications_lead: str = ""
    technical_lead: str = ""
    root_cause: str = ""
    resolution: str = ""
    resolved_at: str = ""
    player_impact: dict = field(default_factory=dict)
    financial_impact: dict = field(default_factory=dict)
    timeline: list = field(default_factory=list)
    action_items: list = field(default_factory=list)
    regulatory_notifications: list = field(default_factory=list)
    post_mortem_url: str = ""


# ---------------------------------------------------------------------------
# Escalation Matrix
# ---------------------------------------------------------------------------

ESCALATION_MATRIX = {
    "P1": {
        "initial_responders": ["on-call-engineer", "on-call-sre"],
        "5_min": ["engineering-manager", "vp-engineering"],
        "15_min": ["cto", "coo"],
        "30_min": ["ceo", "legal-counsel"],
        "auto_notify": ["compliance-team", "customer-support-lead"],
        "war_room": True,
        "status_page_update": True,
        "player_communication": True,
        "regulatory_check": True,
    },
    "P2": {
        "initial_responders": ["on-call-engineer"],
        "15_min": ["engineering-manager"],
        "30_min": ["vp-engineering"],
        "60_min": ["cto"],
        "auto_notify": ["customer-support-lead"],
        "war_room": False,
        "status_page_update": True,
        "player_communication": False,
        "regulatory_check": True,
    },
    "P3": {
        "initial_responders": ["on-call-engineer"],
        "60_min": ["engineering-manager"],
        "auto_notify": [],
        "war_room": False,
        "status_page_update": False,
        "player_communication": False,
        "regulatory_check": False,
    },
    "P4": {
        "initial_responders": ["on-call-engineer"],
        "auto_notify": [],
        "war_room": False,
        "status_page_update": False,
        "player_communication": False,
        "regulatory_check": False,
    },
}


# ---------------------------------------------------------------------------
# Runbooks
# ---------------------------------------------------------------------------

RUNBOOKS = {
    "platform_outage": {
        "title": "Platform Outage Response",
        "steps": [
            "1. Check infrastructure health: kubectl get nodes && kubectl get pods -A | grep -v Running",
            "2. Check CDN/Load Balancer status: curl -s https://status.cloudflare.com/api/v2/summary.json",
            "3. Check database connectivity: psql -h $DB_HOST -U $DB_USER -c 'SELECT 1'",
            "4. Check Redis cluster: redis-cli -h $REDIS_HOST cluster info",
            "5. Review error rates in Grafana: open https://grafana.casino.internal/d/platform-overview",
            "6. Check recent deployments: kubectl rollout history deployment -n production",
            "7. If deployment-related: kubectl rollout undo deployment/<service> -n production",
            "8. Enable circuit breakers for non-critical services",
            "9. Scale up healthy pods: kubectl scale deployment/<service> --replicas=<N>",
            "10. Verify recovery via synthetic monitoring",
        ],
    },
    "payment_failure": {
        "title": "Payment Gateway Failure Response",
        "steps": [
            "1. Identify affected payment provider(s) via logs",
            "2. Check provider status pages (Stripe, Nuvei, PaySafe, etc.)",
            "3. Verify SSL certificate validity for payment endpoints",
            "4. Check payment service pod health: kubectl get pods -n payments",
            "5. Review payment queue depth: rabbitmq-diagnostics queue_lengths",
            "6. If provider-side: activate backup payment route",
            "7. Pause deposit promotions to reduce load",
            "8. Monitor player balance discrepancies",
            "9. Verify reconciliation jobs are queued for retry",
            "10. Notify finance team of potential settlement delays",
        ],
    },
    "data_breach": {
        "title": "Data Breach Response (GDPR/Regulatory)",
        "steps": [
            "1. IMMEDIATELY isolate affected systems (do NOT shut down - preserve evidence)",
            "2. Engage forensics team and legal counsel",
            "3. Determine scope: what data, how many players, which jurisdictions",
            "4. Preserve all logs and evidence (copy, do not modify)",
            "5. Assess if personal data (PII/KYC) is compromised",
            "6. Start GDPR 72-hour notification clock",
            "7. Prepare regulatory notifications per jurisdiction",
            "8. Prepare player notification if required",
            "9. Engage external forensics firm if breach is significant",
            "10. Document everything for regulatory reporting",
        ],
    },
    "game_provider": {
        "title": "Game Provider Outage Response",
        "steps": [
            "1. Identify affected provider and games",
            "2. Check provider status API/page",
            "3. Disable affected games in game lobby",
            "4. Redirect players to alternative games",
            "5. Calculate open round liability",
            "6. Monitor stuck/pending game rounds",
            "7. Contact provider technical support",
            "8. Track SLA violations for commercial discussions",
            "9. Re-enable games once provider confirms recovery",
            "10. Reconcile any interrupted game rounds",
        ],
    },
    "security_incident": {
        "title": "Security Incident Response",
        "steps": [
            "1. Classify threat type (DDoS, intrusion, credential stuffing, etc.)",
            "2. Activate DDoS mitigation if applicable (Cloudflare/AWS Shield)",
            "3. Block suspicious IPs/ranges at WAF level",
            "4. Check for unauthorized access in audit logs",
            "5. Rotate compromised credentials immediately",
            "6. Enable enhanced logging on affected systems",
            "7. Scan for indicators of compromise (IoC)",
            "8. Check for lateral movement in network logs",
            "9. Assess if player data is at risk",
            "10. Engage CERT/law enforcement if criminal activity detected",
        ],
    },
}


# ---------------------------------------------------------------------------
# IncidentManager
# ---------------------------------------------------------------------------

class IncidentManager:
    """Core incident management engine."""

    def __init__(self, storage_dir: str = "./incidents"):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def _incident_path(self, incident_id: str) -> Path:
        return self.storage_dir / f"{incident_id}.json"

    def _save(self, incident: Incident):
        with open(self._incident_path(incident.id), "w") as f:
            json.dump(asdict(incident), f, indent=2, default=str)

    def _load(self, incident_id: str) -> Incident:
        path = self._incident_path(incident_id)
        if not path.exists():
            raise FileNotFoundError(f"Incident {incident_id} not found")
        with open(path) as f:
            data = json.load(f)
        return Incident(**data)

    def declare(
        self,
        title: str,
        severity: str,
        category: str = "platform_outage",
        description: str = "",
        declared_by: str = "on-call-engineer",
        affected_services: Optional[list] = None,
        affected_jurisdictions: Optional[list] = None,
    ) -> Incident:
        """Declare a new incident."""
        now = datetime.now(timezone.utc)
        incident_id = f"INC-{now.strftime('%Y')}-{uuid.uuid4().hex[:6].upper()}"

        incident = Incident(
            id=incident_id,
            title=title,
            severity=severity,
            category=category,
            state=IncidentState.DECLARED.value,
            declared_at=now.isoformat() + "Z",
            declared_by=declared_by,
            description=description,
            affected_services=affected_services or [],
            affected_jurisdictions=affected_jurisdictions or [],
        )

        incident.timeline.append(asdict(TimelineEntry(
            timestamp=now.isoformat() + "Z",
            action="incident_declared",
            actor=declared_by,
            details=f"Incident declared: {title} (Severity: {severity})",
        )))

        # Auto-assign roles based on escalation matrix
        escalation = ESCALATION_MATRIX.get(severity, ESCALATION_MATRIX["P4"])
        incident.incident_commander = escalation["initial_responders"][0]  # ty:ignore[not-subscriptable]
        if len(escalation["initial_responders"]) > 1:  # ty:ignore[invalid-argument-type]
            incident.technical_lead = escalation["initial_responders"][1]  # ty:ignore[not-subscriptable]

        # Check regulatory notification requirements
        if escalation.get("regulatory_check") and affected_jurisdictions:
            self._check_regulatory_deadlines(incident)

        self._save(incident)
        logger.info(f"Incident declared: {incident_id} - {title} [{severity}]")

        # Print escalation info
        self._print_escalation_plan(incident, escalation)

        # Print relevant runbook
        if category in RUNBOOKS:
            self._print_runbook(category)

        return incident

    def escalate(self, incident_id: str, reason: str = "", escalated_by: str = "system"):
        """Escalate an incident to the next tier."""
        incident = self._load(incident_id)
        severity = incident.severity
        escalation = ESCALATION_MATRIX.get(severity, ESCALATION_MATRIX["P4"])

        now = datetime.now(timezone.utc)
        declared_at = datetime.fromisoformat(incident.declared_at.rstrip("Z"))
        elapsed_min = (now - declared_at).total_seconds() / 60

        # Determine next escalation tier
        tiers = ["5_min", "15_min", "30_min", "60_min"]
        next_contacts = []
        for tier in tiers:
            tier_min = int(tier.split("_")[0])
            if elapsed_min >= tier_min and tier in escalation:
                next_contacts = escalation[tier]

        incident.timeline.append(asdict(TimelineEntry(
            timestamp=now.isoformat() + "Z",
            action="escalated",
            actor=escalated_by,
            details=f"Escalated after {int(elapsed_min)}m. Reason: {reason}. "
                    f"Notifying: {', '.join(next_contacts) if next_contacts else 'no additional contacts'}",  # ty:ignore[no-matching-overload]
        )))

        if incident.state == IncidentState.DECLARED.value:
            incident.state = IncidentState.INVESTIGATING.value

        self._save(incident)
        logger.info(f"Incident {incident_id} escalated. Contacts: {next_contacts}")

        print(f"\n{'='*60}")
        print(f"ESCALATION - {incident_id}")
        print(f"{'='*60}")
        print(f"Elapsed time: {int(elapsed_min)} minutes")
        print(f"Next contacts: {', '.join(next_contacts) if next_contacts else 'No additional tier'}")  # ty:ignore[no-matching-overload]
        if escalation.get("war_room"):
            print(f"WAR ROOM: Activate immediately - https://meet.casino.internal/war-room")
        print(f"{'='*60}\n")

        return incident

    def update_state(self, incident_id: str, new_state: str, details: str = "",
                     actor: str = "incident-commander"):
        """Update incident state."""
        incident = self._load(incident_id)
        old_state = incident.state
        incident.state = new_state

        incident.timeline.append(asdict(TimelineEntry(
            timestamp=datetime.now(timezone.utc).isoformat() + "Z",
            action="state_change",
            actor=actor,
            details=f"State changed: {old_state} -> {new_state}. {details}",
        )))

        self._save(incident)
        logger.info(f"Incident {incident_id}: {old_state} -> {new_state}")
        return incident

    def resolve(self, incident_id: str, root_cause: str, resolution: str,
                resolved_by: str = "incident-commander"):
        """Resolve an incident."""
        incident = self._load(incident_id)
        now = datetime.now(timezone.utc)

        incident.state = IncidentState.RESOLVED.value
        incident.root_cause = root_cause
        incident.resolution = resolution
        incident.resolved_at = now.isoformat() + "Z"

        declared_at = datetime.fromisoformat(incident.declared_at.rstrip("Z"))
        ttm = now - declared_at

        incident.timeline.append(asdict(TimelineEntry(
            timestamp=now.isoformat() + "Z",
            action="resolved",
            actor=resolved_by,
            details=f"Resolved. TTM: {ttm}. Root cause: {root_cause}",
        )))

        # Calculate impact
        incident.player_impact = self._estimate_player_impact(incident, ttm)
        incident.financial_impact = self._estimate_financial_impact(incident, ttm)

        self._save(incident)

        print(f"\n{'='*60}")
        print(f"INCIDENT RESOLVED - {incident_id}")
        print(f"{'='*60}")
        print(f"Title: {incident.title}")
        print(f"Severity: {incident.severity}")
        print(f"Time to Mitigate: {ttm}")
        print(f"Root Cause: {root_cause}")
        print(f"Resolution: {resolution}")
        print(f"\nEstimated Player Impact:")
        for k, v in incident.player_impact.items():
            print(f"  {k}: {v}")
        print(f"\nEstimated Financial Impact:")
        for k, v in incident.financial_impact.items():
            print(f"  {k}: {v}")
        print(f"{'='*60}\n")

        return incident

    def generate_report(self, incident_id: str, report_format: str = "internal") -> str:
        """Generate incident report (internal or regulatory)."""
        incident = self._load(incident_id)

        if report_format == "regulatory":
            return self._generate_regulatory_report(incident)
        elif report_format == "post_mortem":
            return self._generate_post_mortem(incident)
        else:
            return self._generate_internal_report(incident)

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _check_regulatory_deadlines(self, incident: Incident):
        """Check regulatory notification deadlines for affected jurisdictions."""
        category_map = {
            "data_breach": "data_breach",
            "platform_outage": "platform_outage",
            "payment_failure": "payment_failure",
        }
        cat_key = category_map.get(incident.category)
        if not cat_key:
            return

        for jurisdiction in incident.affected_jurisdictions:
            if jurisdiction in REGULATORY_NOTIFICATION:
                hours = REGULATORY_NOTIFICATION[jurisdiction].get(cat_key)
                if hours:
                    deadline = datetime.fromisoformat(
                        incident.declared_at.rstrip("Z")
                    ) + timedelta(hours=hours)
                    incident.regulatory_notifications.append({
                        "jurisdiction": jurisdiction,
                        "deadline": deadline.isoformat() + "Z",
                        "hours": hours,
                        "notified": False,
                    })
                    logger.warning(
                        f"REGULATORY: {jurisdiction} requires notification within "
                        f"{hours}h (by {deadline.isoformat()}Z)"
                    )

    def _print_escalation_plan(self, incident: Incident, escalation: dict):
        print(f"\n{'='*60}")
        print(f"INCIDENT DECLARED: {incident.id}")
        print(f"{'='*60}")
        print(f"Title:    {incident.title}")
        print(f"Severity: {incident.severity}")
        print(f"Category: {incident.category}")
        print(f"Time:     {incident.declared_at}")
        print(f"\nINCIDENT COMMANDER: {incident.incident_commander}")
        print(f"TECHNICAL LEAD:     {incident.technical_lead or 'TBD'}")
        print(f"\nESCALATION PLAN:")
        for key in ["5_min", "15_min", "30_min", "60_min"]:
            if key in escalation:
                print(f"  +{key.replace('_', ' ')}: {', '.join(escalation[key])}")
        if escalation.get("war_room"):
            print(f"\n  ** WAR ROOM ACTIVATED ** -> https://meet.casino.internal/war-room")
        if escalation.get("status_page_update"):
            print(f"  ** Status page update required **")
        if incident.regulatory_notifications:
            print(f"\n  REGULATORY DEADLINES:")
            for n in incident.regulatory_notifications:
                print(f"    {n['jurisdiction']}: {n['hours']}h deadline -> {n['deadline']}")
        print(f"{'='*60}\n")

    def _print_runbook(self, category: str):
        runbook = RUNBOOKS[category]
        print(f"\n{'='*60}")
        print(f"RUNBOOK: {runbook['title']}")
        print(f"{'='*60}")
        for step in runbook["steps"]:
            print(f"  {step}")
        print(f"{'='*60}\n")

    def _estimate_player_impact(self, incident: Incident, ttm: timedelta) -> dict:
        """Estimate player impact based on severity and duration."""
        hours = ttm.total_seconds() / 3600
        base_players = {
            "P1": 50000, "P2": 15000, "P3": 3000, "P4": 500,
        }
        affected = int(base_players.get(incident.severity, 500) * max(1, hours))
        return {
            "estimated_affected_players": affected,
            "duration_hours": round(hours, 2),
            "sessions_disrupted": int(affected * 1.5),
            "support_tickets_expected": int(affected * 0.05),
        }

    def _estimate_financial_impact(self, incident: Incident, ttm: timedelta) -> dict:
        """Estimate financial impact."""
        hours = ttm.total_seconds() / 3600
        hourly_revenue = {
            "P1": 25000, "P2": 10000, "P3": 3000, "P4": 500,
        }
        lost = hourly_revenue.get(incident.severity, 500) * hours
        return {
            "estimated_lost_revenue_eur": round(lost, 2),
            "estimated_compensation_eur": round(lost * 0.1, 2),
            "estimated_regulatory_fine_risk_eur": round(lost * 0.5, 2) if incident.severity == "P1" else 0,
            "total_estimated_cost_eur": round(lost * 1.6, 2) if incident.severity == "P1" else round(lost * 1.1, 2),
        }

    def _generate_internal_report(self, incident: Incident) -> str:
        lines = [
            f"# Incident Report: {incident.id}",
            f"",
            f"## Summary",
            f"- **Title:** {incident.title}",
            f"- **Severity:** {incident.severity}",
            f"- **Category:** {incident.category}",
            f"- **State:** {incident.state}",
            f"- **Declared:** {incident.declared_at}",
            f"- **Resolved:** {incident.resolved_at or 'Ongoing'}",
            f"- **Commander:** {incident.incident_commander}",
            f"",
            f"## Root Cause",
            f"{incident.root_cause or 'Under investigation'}",
            f"",
            f"## Resolution",
            f"{incident.resolution or 'Pending'}",
            f"",
            f"## Timeline",
        ]
        for entry in incident.timeline:
            lines.append(f"- [{entry['timestamp']}] {entry['action']}: {entry['details']} ({entry['actor']})")
        lines.extend([
            f"",
            f"## Impact",
            f"### Player Impact",
        ])
        for k, v in incident.player_impact.items():
            lines.append(f"- {k}: {v}")
        lines.extend([f"", f"### Financial Impact"])
        for k, v in incident.financial_impact.items():
            lines.append(f"- {k}: {v}")
        report = "\n".join(lines)
        print(report)
        return report

    def _generate_regulatory_report(self, incident: Incident) -> str:
        lines = [
            f"REGULATORY INCIDENT NOTIFICATION",
            f"{'='*50}",
            f"",
            f"Incident Reference: {incident.id}",
            f"Reporting Entity: [OPERATOR_NAME]",
            f"License Number(s): [LICENSE_NUMBERS]",
            f"Date of Incident: {incident.declared_at}",
            f"Date of Report: {datetime.now(timezone.utc).isoformat()}Z",
            f"",
            f"1. INCIDENT DESCRIPTION",
            f"   {incident.title}",
            f"   {incident.description}",
            f"",
            f"2. SEVERITY AND CLASSIFICATION",
            f"   Severity: {incident.severity}",
            f"   Category: {incident.category}",
            f"",
            f"3. AFFECTED SERVICES",
            f"   {', '.join(incident.affected_services) if incident.affected_services else 'N/A'}",
            f"",
            f"4. AFFECTED JURISDICTIONS",
            f"   {', '.join(incident.affected_jurisdictions) if incident.affected_jurisdictions else 'N/A'}",
            f"",
            f"5. PLAYER IMPACT",
        ]
        for k, v in incident.player_impact.items():
            lines.append(f"   - {k}: {v}")
        lines.extend([
            f"",
            f"6. ROOT CAUSE ANALYSIS",
            f"   {incident.root_cause or 'Under investigation'}",
            f"",
            f"7. CORRECTIVE ACTIONS TAKEN",
            f"   {incident.resolution or 'Pending'}",
            f"",
            f"8. PREVENTIVE MEASURES",
            f"   [To be completed during post-mortem]",
            f"",
            f"9. TIMELINE OF EVENTS",
        ])
        for entry in incident.timeline:
            lines.append(f"   [{entry['timestamp']}] {entry['details']}")
        lines.extend([
            f"",
            f"10. CONTACT INFORMATION",
            f"    Incident Commander: {incident.incident_commander}",
            f"    Compliance Officer: [COMPLIANCE_OFFICER]",
            f"    Email: [COMPLIANCE_EMAIL]",
            f"    Phone: [COMPLIANCE_PHONE]",
            f"",
            f"Authorized Signatory: ________________________",
            f"Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
        ])
        report = "\n".join(lines)
        print(report)
        return report

    def _generate_post_mortem(self, incident: Incident) -> str:
        declared = datetime.fromisoformat(incident.declared_at.rstrip("Z"))
        resolved = datetime.fromisoformat(incident.resolved_at.rstrip("Z")) if incident.resolved_at else None
        ttm = (resolved - declared) if resolved else "Ongoing"

        lines = [
            f"# Post-Mortem: {incident.id}",
            f"",
            f"**Date:** {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
            f"**Incident Commander:** {incident.incident_commander}",
            f"**Severity:** {incident.severity}",
            f"**Duration:** {ttm}",
            f"",
            f"## What Happened",
            f"{incident.description or incident.title}",
            f"",
            f"## Root Cause",
            f"{incident.root_cause}",
            f"",
            f"## Impact",
            f"### Players",
        ]
        for k, v in incident.player_impact.items():
            lines.append(f"- {k}: {v}")
        lines.extend([
            f"",
            f"### Financial",
        ])
        for k, v in incident.financial_impact.items():
            lines.append(f"- {k}: {v}")
        lines.extend([
            f"",
            f"## Timeline",
        ])
        for entry in incident.timeline:
            lines.append(f"| {entry['timestamp']} | {entry['action']} | {entry['details']} |")
        lines.extend([
            f"",
            f"## What Went Well",
            f"- [ ] Detection was timely",
            f"- [ ] Escalation followed procedure",
            f"- [ ] Communication was clear",
            f"- [ ] Runbook was helpful",
            f"",
            f"## What Could Be Improved",
            f"- [ ] Monitoring gaps",
            f"- [ ] Documentation gaps",
            f"- [ ] Process improvements",
            f"- [ ] Tooling improvements",
            f"",
            f"## Action Items",
            f"| # | Action | Owner | Deadline | Status |",
            f"|---|--------|-------|----------|--------|",
            f"| 1 | [TODO] | [OWNER] | [DATE] | Open |",
            f"| 2 | [TODO] | [OWNER] | [DATE] | Open |",
            f"",
            f"## Lessons Learned",
            f"[To be filled during post-mortem meeting]",
        ])
        report = "\n".join(lines)
        print(report)
        return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Incident Commander - iGaming Incident Response System")
    subparsers = parser.add_subparsers(dest="command")

    # Declare
    declare_parser = subparsers.add_parser("declare", help="Declare a new incident")
    declare_parser.add_argument("--title", required=True, help="Incident title")
    declare_parser.add_argument("--severity", required=True, choices=["P1", "P2", "P3", "P4"])
    declare_parser.add_argument("--category", default="platform_outage",
                                choices=[c.value for c in IncidentCategory])
    declare_parser.add_argument("--description", default="")
    declare_parser.add_argument("--services", nargs="*", default=[])
    declare_parser.add_argument("--jurisdictions", nargs="*", default=[])
    declare_parser.add_argument("--declared-by", default="on-call-engineer")

    # Escalate
    esc_parser = subparsers.add_parser("escalate", help="Escalate an incident")
    esc_parser.add_argument("--incident-id", required=True)
    esc_parser.add_argument("--reason", default="No improvement observed")

    # Update state
    state_parser = subparsers.add_parser("update", help="Update incident state")
    state_parser.add_argument("--incident-id", required=True)
    state_parser.add_argument("--state", required=True,
                              choices=[s.value for s in IncidentState])
    state_parser.add_argument("--details", default="")

    # Resolve
    res_parser = subparsers.add_parser("resolve", help="Resolve an incident")
    res_parser.add_argument("--incident-id", required=True)
    res_parser.add_argument("--root-cause", required=True)
    res_parser.add_argument("--resolution", default="")

    # Report
    rep_parser = subparsers.add_parser("report", help="Generate incident report")
    rep_parser.add_argument("--incident-id", required=True)
    rep_parser.add_argument("--format", default="internal",
                            choices=["internal", "regulatory", "post_mortem"])

    # Runbook
    rb_parser = subparsers.add_parser("runbook", help="Display a runbook")
    rb_parser.add_argument("--category", required=True, choices=list(RUNBOOKS.keys()))

    # List
    subparsers.add_parser("list", help="List all incidents")

    args = parser.parse_args()
    mgr = IncidentManager()

    if args.command == "declare":
        mgr.declare(
            title=args.title,
            severity=args.severity,
            category=args.category,
            description=args.description,
            declared_by=args.declared_by,
            affected_services=args.services,
            affected_jurisdictions=args.jurisdictions,
        )
    elif args.command == "escalate":
        mgr.escalate(args.incident_id, reason=args.reason)
    elif args.command == "update":
        mgr.update_state(args.incident_id, args.state, details=args.details)
    elif args.command == "resolve":
        mgr.resolve(args.incident_id, root_cause=args.root_cause, resolution=args.resolution)
    elif args.command == "report":
        mgr.generate_report(args.incident_id, report_format=args.format)
    elif args.command == "runbook":
        runbook = RUNBOOKS[args.category]
        print(f"\n{'='*60}")
        print(f"RUNBOOK: {runbook['title']}")
        print(f"{'='*60}")
        for step in runbook["steps"]:
            print(f"  {step}")
        print(f"{'='*60}\n")
    elif args.command == "list":
        incidents_dir = Path("./incidents")
        if not incidents_dir.exists():
            print("No incidents found.")
            return
        for f in sorted(incidents_dir.glob("*.json")):
            with open(f) as fh:
                data = json.load(fh)
            print(f"  {data['id']}  [{data['severity']}] [{data['state']}]  {data['title']}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
