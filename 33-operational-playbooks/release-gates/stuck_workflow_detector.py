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
Release gate: detect workflows stuck beyond their SLA.

Scans all active workflows and flags any step that has exceeded its
SLA deadline.  Returns exit code 1 if any breach is found, making it
suitable for CI/CD gates and cron-based monitoring.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

# ---------------------------------------------------------------------------
# In production this queries the workflow database.  Here we simulate
# with in-memory data suitable for testing and book illustration.
# ---------------------------------------------------------------------------

_SAMPLE_WORKFLOWS: list[dict[str, Any]] = [
    {
        "workflow_id": "wf-001",
        "workflow_type": "AML_REVIEW",
        "subject_id": "player_100",
        "state": "IN_PROGRESS",
        "current_step": "transaction_analysis",
        "step_sla_minutes": 120,
        "step_started_at": (datetime.now(timezone.utc) - timedelta(minutes=180)).isoformat(),
        "priority": "HIGH",
        "assigned_to": "analyst_01",
    },
    {
        "workflow_id": "wf-002",
        "workflow_type": "PLAYER_COMPLAINT",
        "subject_id": "player_200",
        "state": "AWAITING_APPROVAL",
        "current_step": "manager_review",
        "step_sla_minutes": 60,
        "step_started_at": (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(),
        "priority": "NORMAL",
        "assigned_to": "manager_01",
    },
    {
        "workflow_id": "wf-003",
        "workflow_type": "RG_INTERVENTION",
        "subject_id": "player_300",
        "state": "IN_PROGRESS",
        "current_step": "risk_assessment",
        "step_sla_minutes": 30,
        "step_started_at": (datetime.now(timezone.utc) - timedelta(minutes=90)).isoformat(),
        "priority": "URGENT",
        "assigned_to": "rg_specialist_01",
    },
    {
        "workflow_id": "wf-004",
        "workflow_type": "AML_REVIEW",
        "subject_id": "player_400",
        "state": "COMPLETED",
        "current_step": "sar_decision",
        "step_sla_minutes": 60,
        "step_started_at": (datetime.now(timezone.utc) - timedelta(minutes=200)).isoformat(),
        "priority": "NORMAL",
        "assigned_to": "mlro_01",
    },
]


def detect_stuck_workflows(workflows: list[dict[str, Any]] | None = None,
                           now: datetime | None = None) -> list[dict[str, Any]]:
    """
    Scan workflows and return those where the current step exceeds SLA.

    Only considers workflows in active states (IN_PROGRESS, AWAITING_APPROVAL).
    """
    if workflows is None:
        workflows = _SAMPLE_WORKFLOWS
    if now is None:
        now = datetime.now(timezone.utc)

    active_states = {"IN_PROGRESS", "AWAITING_APPROVAL", "ESCALATED"}
    stuck: list[dict[str, Any]] = []

    for wf in workflows:
        if wf.get("state") not in active_states:
            continue

        started_at_str = wf.get("step_started_at")
        sla_minutes = wf.get("step_sla_minutes", 0)
        if not started_at_str or not sla_minutes:
            continue

        started_at = datetime.fromisoformat(started_at_str)
        deadline = started_at + timedelta(minutes=sla_minutes)

        if now > deadline:
            overdue_minutes = int((now - deadline).total_seconds() / 60)
            stuck.append({
                "workflow_id": wf["workflow_id"],
                "workflow_type": wf.get("workflow_type", ""),
                "subject_id": wf.get("subject_id", ""),
                "current_step": wf.get("current_step", ""),
                "sla_minutes": sla_minutes,
                "overdue_minutes": overdue_minutes,
                "priority": wf.get("priority", "NORMAL"),
                "assigned_to": wf.get("assigned_to", "unassigned"),
                "severity": _classify_severity(overdue_minutes, sla_minutes, wf.get("priority", "NORMAL")),
            })

    return sorted(stuck, key=lambda x: (-_severity_rank(x["severity"]), -x["overdue_minutes"]))


def _classify_severity(overdue_minutes: int, sla_minutes: int, priority: str) -> str:
    """Classify the severity of a stuck workflow."""
    ratio = overdue_minutes / max(sla_minutes, 1)
    if priority == "URGENT" or ratio > 2.0:
        return "CRITICAL"
    if priority == "HIGH" or ratio > 1.0:
        return "HIGH"
    if ratio > 0.5:
        return "MEDIUM"
    return "LOW"


def _severity_rank(severity: str) -> int:
    return {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(severity, 0)


def main() -> int:
    """Entry point for CI/CD and cron execution."""
    stuck = detect_stuck_workflows()

    if not stuck:
        print("OK: No stuck workflows detected.")
        return 0

    print(f"ALERT: {len(stuck)} workflow(s) stuck beyond SLA\n")
    for item in stuck:
        print(f"  [{item['severity']}] {item['workflow_id']} "
              f"({item['workflow_type']}) step={item['current_step']} "
              f"overdue={item['overdue_minutes']}m assigned={item['assigned_to']}")

    print(f"\nJSON output:\n{json.dumps(stuck, indent=2)}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
