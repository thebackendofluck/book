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
License Renewal Tracking with Deadline Alerts
================================================

Tracks iGaming license renewals across multiple jurisdictions with
configurable alert thresholds, automated status monitoring, and
renewal task generation.

Usage:
    python license_renewal_tracker.py --status
    python license_renewal_tracker.py --alerts
    python license_renewal_tracker.py --add --jurisdiction MGA --expiry 2027-06-15
    python license_renewal_tracker.py --export licenses.json
"""

import json
import logging
import argparse
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


class LicenseStatus(Enum):
    ACTIVE = "active"
    RENEWAL_PENDING = "renewal_pending"
    RENEWAL_SUBMITTED = "renewal_submitted"
    UNDER_REVIEW = "under_review"
    CONDITIONALLY_APPROVED = "conditionally_approved"
    SUSPENDED = "suspended"
    EXPIRED = "expired"
    REVOKED = "revoked"


class AlertLevel(Enum):
    INFO = "info"         # >12 months to expiry
    NOTICE = "notice"     # 6-12 months
    WARNING = "warning"   # 3-6 months
    URGENT = "urgent"     # 1-3 months
    CRITICAL = "critical" # <1 month or expired


@dataclass
class RenewalTask:
    """A task required for license renewal."""
    id: str
    name: str
    description: str
    deadline: str
    responsible: str
    status: str = "pending"  # pending, in_progress, completed, blocked
    dependencies: list = field(default_factory=list)
    notes: str = ""


@dataclass
class LicenseRecord:
    """A single license tracking record."""
    id: str
    jurisdiction: str
    jurisdiction_code: str
    regulator: str
    license_type: str
    license_number: str
    entity_name: str

    # Dates
    issue_date: str
    expiry_date: str
    last_renewal_date: Optional[str] = None
    next_renewal_deadline: Optional[str] = None

    # Renewal configuration
    renewal_lead_time_months: int = 6    # how early to start renewal
    renewal_timeline_months: int = 4     # expected processing time
    auto_renewal: bool = False

    # Costs
    renewal_fee_usd: float = 0
    estimated_renewal_cost_usd: float = 0  # total including legal, testing

    # Status
    status: LicenseStatus = LicenseStatus.ACTIVE
    conditions: list = field(default_factory=list)
    compliance_issues: list = field(default_factory=list)

    # Key contacts
    regulatory_contact: str = ""
    legal_counsel: str = ""
    internal_owner: str = ""

    # Renewal tasks
    renewal_tasks: list = field(default_factory=list)

    # History
    renewal_history: list = field(default_factory=list)
    notes: str = ""

    def days_until_expiry(self) -> int:
        expiry = datetime.strptime(self.expiry_date, "%Y-%m-%d")
        return (expiry - datetime.now()).days

    def alert_level(self) -> AlertLevel:
        days = self.days_until_expiry()
        if days < 0:
            return AlertLevel.CRITICAL
        elif days < 30:
            return AlertLevel.CRITICAL
        elif days < 90:
            return AlertLevel.URGENT
        elif days < 180:
            return AlertLevel.WARNING
        elif days < 365:
            return AlertLevel.NOTICE
        else:
            return AlertLevel.INFO

    def renewal_start_date(self) -> str:
        expiry = datetime.strptime(self.expiry_date, "%Y-%m-%d")
        start = expiry - timedelta(days=self.renewal_lead_time_months * 30)
        return start.strftime("%Y-%m-%d")

    def should_start_renewal(self) -> bool:
        start = datetime.strptime(self.renewal_start_date(), "%Y-%m-%d")
        return datetime.now() >= start


# ---------------------------------------------------------------------------
# Sample license portfolio
# ---------------------------------------------------------------------------

SAMPLE_LICENSES = [
    LicenseRecord(
        id="LIC-001", jurisdiction="Malta", jurisdiction_code="MGA",
        regulator="Malta Gaming Authority",
        license_type="B2C Type 1 (Casino)", license_number="MGA/B2C/123/2022",
        entity_name="AcmetoCasino Malta Ltd",
        issue_date="2022-06-15", expiry_date="2027-06-15",
        renewal_lead_time_months=8, renewal_timeline_months=4,
        renewal_fee_usd=28000, estimated_renewal_cost_usd=85000,
        status=LicenseStatus.ACTIVE,
        regulatory_contact="licensing@mga.org.mt",
        legal_counsel="WH Partners (Malta)",
        internal_owner="VP Regulatory Affairs",
        renewal_tasks=[
            RenewalTask("MGA-R01", "Updated AML Risk Assessment", "Refresh business-wide risk assessment",
                        "2026-10-15", "Compliance Director"),
            RenewalTask("MGA-R02", "Financial Audit", "Commission renewal financial audit",
                        "2026-08-15", "CFO"),
            RenewalTask("MGA-R03", "Penetration Test", "Annual security assessment",
                        "2026-09-01", "CISO"),
            RenewalTask("MGA-R04", "Key Person Updates", "Update personal declarations for any changes",
                        "2026-11-01", "Legal Counsel"),
            RenewalTask("MGA-R05", "Technical Compliance Review", "Verify ongoing technical standard compliance",
                        "2026-09-15", "CTO"),
        ],
    ),
    LicenseRecord(
        id="LIC-002", jurisdiction="United Kingdom", jurisdiction_code="UKGC",
        regulator="UK Gambling Commission",
        license_type="Remote Casino Operating Licence", license_number="OL-001234-R-567890",
        entity_name="AcmetoCasino UK Ltd",
        issue_date="2023-03-01", expiry_date="2026-09-01",
        renewal_lead_time_months=6, renewal_timeline_months=6,
        renewal_fee_usd=120000, estimated_renewal_cost_usd=200000,
        status=LicenseStatus.ACTIVE,
        regulatory_contact="info@gamblingcommission.gov.uk",
        legal_counsel="Harris Hagan LLP",
        internal_owner="UK Regulatory Manager",
        renewal_tasks=[
            RenewalTask("UKGC-R01", "Annual Return Submission", "File latest annual return",
                        "2026-03-01", "Finance Director"),
            RenewalTask("UKGC-R02", "PML Renewals", "Renew personal management licences",
                        "2026-04-01", "HR Director"),
            RenewalTask("UKGC-R03", "RTS Self-Assessment", "Remote technical standards assessment update",
                        "2026-05-01", "CTO"),
            RenewalTask("UKGC-R04", "Affordability Review", "Verify financial vulnerability procedures",
                        "2026-04-15", "Compliance Director"),
        ],
    ),
    LicenseRecord(
        id="LIC-003", jurisdiction="Sweden", jurisdiction_code="SWE",
        regulator="Spelinspektionen",
        license_type="Online Gambling Licence", license_number="18Li12345",
        entity_name="AcmetoCasino Sweden AB",
        issue_date="2024-01-01", expiry_date="2029-01-01",
        renewal_lead_time_months=6, renewal_timeline_months=3,
        renewal_fee_usd=35000, estimated_renewal_cost_usd=60000,
        status=LicenseStatus.ACTIVE,
        regulatory_contact="registrator@spelinspektionen.se",
        internal_owner="Nordic Regulatory Manager",
    ),
    LicenseRecord(
        id="LIC-004", jurisdiction="Ontario, Canada", jurisdiction_code="ONT",
        regulator="iGaming Ontario / AGCO",
        license_type="iGaming Operator Registration", license_number="iGO-OP-2024-0567",
        entity_name="AcmetoCasino Canada Inc",
        issue_date="2024-06-01", expiry_date="2026-06-01",
        renewal_lead_time_months=4, renewal_timeline_months=3,
        renewal_fee_usd=100000, estimated_renewal_cost_usd=150000,
        status=LicenseStatus.ACTIVE,
        internal_owner="North America Regulatory Manager",
    ),
]


# ---------------------------------------------------------------------------
# License renewal tracker
# ---------------------------------------------------------------------------

class LicenseRenewalTracker:
    """Track and manage iGaming license renewals across jurisdictions."""

    ALERT_THRESHOLDS_DAYS = {
        AlertLevel.CRITICAL: 30,
        AlertLevel.URGENT: 90,
        AlertLevel.WARNING: 180,
        AlertLevel.NOTICE: 365,
    }

    def __init__(self):
        self.licenses: dict[str, LicenseRecord] = {}
        self._load_sample_data()

    def _load_sample_data(self):
        for lic in SAMPLE_LICENSES:
            self.licenses[lic.id] = lic

    def add_license(self, license_record: LicenseRecord):
        self.licenses[license_record.id] = license_record
        logger.info("Added license: %s (%s)", license_record.id, license_record.jurisdiction)

    def get_portfolio_status(self) -> dict:
        """Get status overview of all tracked licenses."""
        records = []
        for lic in sorted(self.licenses.values(), key=lambda x: x.days_until_expiry()):
            records.append({
                "id": lic.id,
                "jurisdiction": lic.jurisdiction,
                "entity": lic.entity_name,
                "license_number": lic.license_number,
                "expiry_date": lic.expiry_date,
                "days_remaining": lic.days_until_expiry(),
                "alert_level": lic.alert_level().value,
                "status": lic.status.value,
                "renewal_start_date": lic.renewal_start_date(),
                "should_start_renewal": lic.should_start_renewal(),
                "estimated_cost_usd": lic.estimated_renewal_cost_usd,
            })

        total_renewal_cost = sum(r["estimated_cost_usd"] for r in records
                                  if r["days_remaining"] < 365)

        return {
            "report_date": datetime.now().strftime("%Y-%m-%d"),
            "total_licenses": len(records),
            "active": sum(1 for r in records if r["status"] == "active"),
            "requiring_action": sum(1 for r in records if r["should_start_renewal"]),
            "critical_alerts": sum(1 for r in records if r["alert_level"] == "critical"),
            "urgent_alerts": sum(1 for r in records if r["alert_level"] == "urgent"),
            "renewal_budget_next_12m_usd": total_renewal_cost,
            "licenses": records,
        }

    def get_alerts(self, min_level: AlertLevel = AlertLevel.NOTICE) -> list[dict]:
        """Get all active alerts at or above minimum level."""
        level_order = [AlertLevel.INFO, AlertLevel.NOTICE, AlertLevel.WARNING,
                       AlertLevel.URGENT, AlertLevel.CRITICAL]
        min_idx = level_order.index(min_level)

        alerts = []
        for lic in self.licenses.values():
            alert = lic.alert_level()
            if level_order.index(alert) >= min_idx:
                days = lic.days_until_expiry()
                message = self._generate_alert_message(lic, alert, days)
                alerts.append({
                    "license_id": lic.id,
                    "jurisdiction": lic.jurisdiction,
                    "alert_level": alert.value,
                    "days_remaining": days,
                    "expiry_date": lic.expiry_date,
                    "message": message,
                    "action_required": self._get_required_actions(lic, alert),
                })

        alerts.sort(key=lambda x: x["days_remaining"])
        return alerts

    def _generate_alert_message(self, lic: LicenseRecord, level: AlertLevel, days: int) -> str:
        if days < 0:
            return f"EXPIRED: {lic.jurisdiction} license {lic.license_number} expired {abs(days)} days ago!"
        elif level == AlertLevel.CRITICAL:
            return f"CRITICAL: {lic.jurisdiction} license expires in {days} days ({lic.expiry_date})"
        elif level == AlertLevel.URGENT:
            return f"URGENT: {lic.jurisdiction} license renewal required — {days} days remaining"
        elif level == AlertLevel.WARNING:
            return f"WARNING: Begin {lic.jurisdiction} renewal process — {days} days to expiry"
        else:
            return f"NOTICE: {lic.jurisdiction} license renewal upcoming — {days} days to expiry"

    def _get_required_actions(self, lic: LicenseRecord, level: AlertLevel) -> list[str]:
        actions = []
        if level in (AlertLevel.CRITICAL, AlertLevel.URGENT):
            actions.extend([
                f"Contact {lic.regulator} immediately regarding renewal status",
                f"Engage legal counsel: {lic.legal_counsel or 'TBD'}",
                "Prepare emergency renewal application",
                f"Allocate budget: ${lic.estimated_renewal_cost_usd:,.0f}",
            ])
        elif level == AlertLevel.WARNING:
            actions.extend([
                "Begin document gathering for renewal",
                "Schedule internal compliance review",
                "Commission required audits and testing",
                f"Budget allocation: ${lic.estimated_renewal_cost_usd:,.0f}",
            ])
        else:
            actions.extend([
                "Add renewal to upcoming planning cycle",
                "Review any regulatory changes since last renewal",
            ])
        return actions

    def get_renewal_timeline(self, license_id: str) -> dict:
        """Get detailed renewal timeline for a specific license."""
        lic = self.licenses.get(license_id)
        if not lic:
            return {"error": f"License '{license_id}' not found"}

        tasks = lic.renewal_tasks
        expiry = datetime.strptime(lic.expiry_date, "%Y-%m-%d")
        start = datetime.strptime(lic.renewal_start_date(), "%Y-%m-%d")

        return {
            "license_id": lic.id,
            "jurisdiction": lic.jurisdiction,
            "expiry_date": lic.expiry_date,
            "renewal_start_date": lic.renewal_start_date(),
            "days_until_expiry": lic.days_until_expiry(),
            "renewal_window_days": (expiry - start).days,
            "estimated_cost_usd": lic.estimated_renewal_cost_usd,
            "tasks": [asdict(t) for t in tasks],
            "tasks_completed": sum(1 for t in tasks if t.status == "completed"),
            "tasks_total": len(tasks),
            "completion_pct": round(sum(1 for t in tasks if t.status == "completed") / max(len(tasks), 1) * 100),
        }

    def get_annual_calendar(self) -> dict:
        """Generate annual renewal calendar."""
        calendar = {}
        for lic in self.licenses.values():
            renewal_start = lic.renewal_start_date()
            month = renewal_start[:7]  # YYYY-MM
            if month not in calendar:
                calendar[month] = []
            calendar[month].append({
                "license_id": lic.id,
                "jurisdiction": lic.jurisdiction,
                "expiry": lic.expiry_date,
                "action": "Begin renewal process",
                "cost_usd": lic.estimated_renewal_cost_usd,
            })

        return {
            "calendar": dict(sorted(calendar.items())),
            "total_annual_cost_usd": sum(lic.estimated_renewal_cost_usd for lic in self.licenses.values()),
        }

    def export_portfolio(self, output_path: str):
        """Export full portfolio to JSON."""
        data = {
            "exported": datetime.now().isoformat(),
            "licenses": [asdict(lic) for lic in self.licenses.values()],
        }
        path = Path(output_path)
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)
        logger.info("Exported %d licenses to %s", len(self.licenses), path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="iGaming License Renewal Tracker")
    parser.add_argument("--status", action="store_true", help="Portfolio status overview")
    parser.add_argument("--alerts", action="store_true", help="Show active alerts")
    parser.add_argument("--timeline", type=str, help="Renewal timeline for license ID")
    parser.add_argument("--calendar", action="store_true", help="Annual renewal calendar")
    parser.add_argument("--export", type=str, help="Export portfolio to JSON file")
    parser.add_argument("--format", choices=["json", "text"], default="text")
    args = parser.parse_args()

    tracker = LicenseRenewalTracker()

    if args.export:
        tracker.export_portfolio(args.export)
        return

    if args.timeline:
        result = tracker.get_renewal_timeline(args.timeline)
        print(json.dumps(result, indent=2, default=str))
        return

    if args.calendar:
        result = tracker.get_annual_calendar()
        print(json.dumps(result, indent=2, default=str))
        return

    if args.alerts:
        alerts = tracker.get_alerts()
        if args.format == "json":
            print(json.dumps(alerts, indent=2, default=str))
        else:
            print(f"\n=== License Renewal Alerts ===\n")
            for a in alerts:
                level = a["alert_level"].upper()
                print(f"  [{level:8s}] {a['message']}")
                for action in a["action_required"]:
                    print(f"             -> {action}")
                print()
        return

    # Default: status
    status = tracker.get_portfolio_status()
    if args.format == "json":
        print(json.dumps(status, indent=2, default=str))
    else:
        print(f"\n{'='*70}")
        print(f"  License Portfolio Status — {status['report_date']}")
        print(f"{'='*70}\n")
        print(f"  Total licenses:      {status['total_licenses']}")
        print(f"  Active:              {status['active']}")
        print(f"  Requiring action:    {status['requiring_action']}")
        print(f"  Critical alerts:     {status['critical_alerts']}")
        print(f"  Urgent alerts:       {status['urgent_alerts']}")
        print(f"  Renewal budget (12m): ${status['renewal_budget_next_12m_usd']:,.0f}\n")

        print(f"  {'ID':<10} {'Jurisdiction':<25} {'Expiry':<12} {'Days':<8} {'Alert':<10} {'Status':<15}")
        print(f"  {'-'*80}")
        for lic in status["licenses"]:
            print(f"  {lic['id']:<10} {lic['jurisdiction']:<25} {lic['expiry_date']:<12} "
                  f"{lic['days_remaining']:<8} {lic['alert_level']:<10} {lic['status']:<15}")


if __name__ == "__main__":
    main()
