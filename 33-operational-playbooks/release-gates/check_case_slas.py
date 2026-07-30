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
Release gate: verify all open cases are resolved within SLA.

Scans cases and reports:
  - Cases approaching SLA deadline (warning zone: <25% time remaining)
  - Cases that have breached SLA
  - SLA compliance rate

Returns exit code 1 if any SLA breach is found.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Sample data (in production this queries the case database)
# ---------------------------------------------------------------------------

_SAMPLE_CASES: list[dict[str, Any]] = [
    {
        "case_id": "CASE-A001",
        "case_type": "AML_ALERT",
        "priority": "HIGH",
        "status": "IN_PROGRESS",
        "sla_hours": 8,
        "created_at": (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat(),
        "sla_deadline": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
        "assigned_to": "analyst_01",
        "subject_id": "player_100",
    },
    {
        "case_id": "CASE-B002",
        "case_type": "PLAYER_COMPLAINT",
        "priority": "NORMAL",
        "status": "IN_PROGRESS",
        "sla_hours": 24,
        "created_at": (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat(),
        "sla_deadline": (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat(),
        "assigned_to": "agent_03",
        "subject_id": "player_200",
    },
    {
        "case_id": "CASE-C003",
        "case_type": "RG_FLAG",
        "priority": "CRITICAL",
        "status": "OPEN",
        "sla_hours": 1,
        "created_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
        "sla_deadline": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        "assigned_to": None,
        "subject_id": "player_300",
    },
    {
        "case_id": "CASE-D004",
        "case_type": "CHARGEBACK",
        "priority": "NORMAL",
        "status": "RESOLVED",
        "sla_hours": 24,
        "created_at": (datetime.now(timezone.utc) - timedelta(hours=20)).isoformat(),
        "sla_deadline": (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat(),
        "resolved_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
        "assigned_to": "agent_05",
        "subject_id": "player_400",
    },
]


def check_case_slas(cases: list[dict[str, Any]] | None = None,
                    now: datetime | None = None) -> dict[str, Any]:
    """
    Analyse all cases for SLA compliance.

    Returns a report with breached, at-risk, and compliant counts.
    """
    if cases is None:
        cases = _SAMPLE_CASES
    if now is None:
        now = datetime.now(timezone.utc)

    active_statuses = {"OPEN", "IN_PROGRESS", "PENDING_INFO", "ESCALATED"}
    breached: list[dict[str, Any]] = []
    at_risk: list[dict[str, Any]] = []
    compliant = 0
    resolved_within_sla = 0
    resolved_outside_sla = 0

    for case in cases:
        status = case.get("status", "")
        deadline_str = case.get("sla_deadline")
        if not deadline_str:
            continue

        deadline = datetime.fromisoformat(deadline_str)

        if status in active_statuses:
            if now > deadline:
                overdue_hours = round((now - deadline).total_seconds() / 3600, 1)
                breached.append({
                    "case_id": case["case_id"],
                    "case_type": case.get("case_type", ""),
                    "priority": case.get("priority", ""),
                    "status": status,
                    "sla_hours": case.get("sla_hours", 0),
                    "overdue_hours": overdue_hours,
                    "assigned_to": case.get("assigned_to") or "UNASSIGNED",
                    "subject_id": case.get("subject_id", ""),
                })
            else:
                remaining = deadline - now
                total_sla_seconds = case.get("sla_hours", 24) * 3600
                pct_remaining = remaining.total_seconds() / max(total_sla_seconds, 1)
                if pct_remaining < 0.25:
                    at_risk.append({
                        "case_id": case["case_id"],
                        "case_type": case.get("case_type", ""),
                        "priority": case.get("priority", ""),
                        "remaining_hours": round(remaining.total_seconds() / 3600, 1),
                        "assigned_to": case.get("assigned_to") or "UNASSIGNED",
                    })
                else:
                    compliant += 1

        elif status in ("RESOLVED", "CLOSED"):
            resolved_str = case.get("resolved_at")
            if resolved_str:
                resolved_at = datetime.fromisoformat(resolved_str)
                if resolved_at <= deadline:
                    resolved_within_sla += 1
                else:
                    resolved_outside_sla += 1
            else:
                resolved_within_sla += 1  # no resolved_at means assume ok

    total_evaluated = len(breached) + len(at_risk) + compliant + resolved_within_sla + resolved_outside_sla
    sla_pass = compliant + resolved_within_sla
    sla_fail = len(breached) + resolved_outside_sla

    return {
        "timestamp": now.isoformat(),
        "total_cases_evaluated": total_evaluated,
        "breached": breached,
        "at_risk": at_risk,
        "compliant_active": compliant,
        "resolved_within_sla": resolved_within_sla,
        "resolved_outside_sla": resolved_outside_sla,
        "sla_compliance_rate": round(sla_pass / max(total_evaluated, 1) * 100, 1),
        "pass": len(breached) == 0,
    }


def main() -> int:
    """Entry point for CI/CD and cron execution."""
    report = check_case_slas()

    if report["pass"]:
        print(f"OK: All cases within SLA. Compliance rate: {report['sla_compliance_rate']}%")
        if report["at_risk"]:
            print(f"\n  WARNING: {len(report['at_risk'])} case(s) approaching SLA deadline:")
            for item in report["at_risk"]:
                print(f"    {item['case_id']} ({item['case_type']}) "
                      f"remaining={item['remaining_hours']}h assigned={item['assigned_to']}")
        return 0

    print(f"FAIL: {len(report['breached'])} case(s) breached SLA "
          f"(compliance: {report['sla_compliance_rate']}%)\n")
    for item in report["breached"]:
        print(f"  BREACH: {item['case_id']} ({item['case_type']}/{item['priority']}) "
              f"overdue={item['overdue_hours']}h assigned={item['assigned_to']}")

    if report["at_risk"]:
        print(f"\n  At-risk ({len(report['at_risk'])}):")
        for item in report["at_risk"]:
            print(f"    {item['case_id']} remaining={item['remaining_hours']}h")

    print(f"\n{json.dumps(report, indent=2, default=str)}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
