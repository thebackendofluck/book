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
Maintenance Window Scheduler for iGaming Platforms.

Schedules maintenance windows with player impact analysis, jurisdiction
awareness (time zones, peak hours), and automated pre/post checks.

Features:
- Multi-jurisdiction time zone analysis
- Player activity pattern modeling
- Revenue impact estimation
- Automated pre-flight and post-deployment checks
- Maintenance notification generation
- Rollback window calculation

Usage:
    python maintenance_scheduler.py plan --service wallet-service --duration 60 --type rolling
    python maintenance_scheduler.py analyze --date 2026-03-15 --time 04:00 --timezone UTC
    python maintenance_scheduler.py optimal --service platform-core --duration 120
    python maintenance_scheduler.py checklist --maintenance-id MW-2026-001
"""

import argparse
import json
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Domain Models
# ---------------------------------------------------------------------------

class MaintenanceType(Enum):
    FULL_OUTAGE = "full_outage"        # Complete platform downtime
    ROLLING = "rolling"                 # Zero-downtime rolling update
    PARTIAL = "partial"                 # Single service/region down
    DATABASE = "database"              # DB migration requiring locks
    NETWORK = "network"                # Network infrastructure changes
    SECURITY_PATCH = "security_patch"  # Urgent security patching


class ServiceCriticality(Enum):
    CRITICAL = "critical"     # Platform core, payments, wallet
    HIGH = "high"            # Game aggregator, KYC, bonus engine
    MEDIUM = "medium"        # CMS, promotions, reporting
    LOW = "low"              # Analytics, backoffice tools


# Jurisdiction data: timezone offset from UTC, peak hours (local), weekend multiplier
JURISDICTIONS = {
    "UK": {"tz_offset": 0, "peak_start": 18, "peak_end": 23, "weekend_mult": 1.4,
           "regulator": "UKGC", "notification_hours": 48, "avg_concurrent": 12000},
    "Malta": {"tz_offset": 1, "peak_start": 19, "peak_end": 24, "weekend_mult": 1.3,
              "regulator": "MGA", "notification_hours": 24, "avg_concurrent": 3000},
    "Sweden": {"tz_offset": 1, "peak_start": 18, "peak_end": 23, "weekend_mult": 1.5,
               "regulator": "SGA", "notification_hours": 24, "avg_concurrent": 5000},
    "Romania": {"tz_offset": 2, "peak_start": 19, "peak_end": 24, "weekend_mult": 1.3,
                "regulator": "ONJN", "notification_hours": 24, "avg_concurrent": 4000},
    "Ontario": {"tz_offset": -5, "peak_start": 19, "peak_end": 24, "weekend_mult": 1.6,
                "regulator": "AGCO", "notification_hours": 48, "avg_concurrent": 8000},
    "Brazil": {"tz_offset": -3, "peak_start": 19, "peak_end": 24, "weekend_mult": 1.8,
               "regulator": "SIGAP", "notification_hours": 24, "avg_concurrent": 15000},
    "Australia": {"tz_offset": 10, "peak_start": 18, "peak_end": 23, "weekend_mult": 1.4,
                  "regulator": "ACMA", "notification_hours": 48, "avg_concurrent": 6000},
    "Japan": {"tz_offset": 9, "peak_start": 20, "peak_end": 25, "weekend_mult": 1.2,
              "regulator": "NPA", "notification_hours": 24, "avg_concurrent": 7000},
}

# Service dependency map
SERVICE_DEPENDENCIES = {
    "platform-core": {
        "criticality": "critical",
        "depends_on": ["database-primary", "redis-cluster", "message-queue"],
        "depended_by": ["wallet-service", "game-aggregator", "bonus-engine", "kyc-service"],
        "avg_rps": 5000,
        "rollback_time_min": 15,
    },
    "wallet-service": {
        "criticality": "critical",
        "depends_on": ["database-primary", "platform-core", "payment-gateway"],
        "depended_by": ["game-aggregator", "bonus-engine", "withdrawal-service"],
        "avg_rps": 3000,
        "rollback_time_min": 10,
    },
    "payment-gateway": {
        "criticality": "critical",
        "depends_on": ["database-primary", "vault"],
        "depended_by": ["wallet-service", "deposit-service", "withdrawal-service"],
        "avg_rps": 1500,
        "rollback_time_min": 5,
    },
    "game-aggregator": {
        "criticality": "high",
        "depends_on": ["platform-core", "wallet-service", "redis-cluster"],
        "depended_by": [],
        "avg_rps": 8000,
        "rollback_time_min": 10,
    },
    "bonus-engine": {
        "criticality": "high",
        "depends_on": ["platform-core", "wallet-service", "database-primary"],
        "depended_by": [],
        "avg_rps": 2000,
        "rollback_time_min": 10,
    },
    "kyc-service": {
        "criticality": "high",
        "depends_on": ["platform-core", "database-primary", "document-store"],
        "depended_by": [],
        "avg_rps": 500,
        "rollback_time_min": 5,
    },
    "cms-service": {
        "criticality": "medium",
        "depends_on": ["database-read-replica", "cdn"],
        "depended_by": [],
        "avg_rps": 1000,
        "rollback_time_min": 5,
    },
    "reporting-service": {
        "criticality": "low",
        "depends_on": ["database-read-replica", "data-warehouse"],
        "depended_by": [],
        "avg_rps": 200,
        "rollback_time_min": 5,
    },
    "database-primary": {
        "criticality": "critical",
        "depends_on": [],
        "depended_by": ["platform-core", "wallet-service", "payment-gateway", "bonus-engine"],
        "avg_rps": 15000,
        "rollback_time_min": 30,
    },
}


@dataclass
class MaintenanceWindow:
    id: str
    service: str
    maintenance_type: str
    start_utc: str
    duration_minutes: int
    end_utc: str
    impact_analysis: dict = field(default_factory=dict)
    pre_checks: list = field(default_factory=list)
    post_checks: list = field(default_factory=list)
    notifications: list = field(default_factory=list)
    rollback_plan: dict = field(default_factory=dict)
    approved_by: str = ""
    status: str = "planned"


# ---------------------------------------------------------------------------
# Maintenance Scheduler
# ---------------------------------------------------------------------------

class MaintenanceScheduler:

    def analyze_window(self, start_utc: datetime, duration_min: int,
                       service: str, maintenance_type: str = "rolling") -> dict:
        """Analyze a proposed maintenance window for player impact."""
        end_utc = start_utc + timedelta(minutes=duration_min)
        service_info = SERVICE_DEPENDENCIES.get(service, {
            "criticality": "medium", "depends_on": [], "depended_by": [],
            "avg_rps": 1000, "rollback_time_min": 10,
        })

        is_weekend = start_utc.weekday() >= 5
        jurisdiction_impact = {}
        total_affected = 0

        for jur, info in JURISDICTIONS.items():
            tz = timezone(timedelta(hours=info["tz_offset"]))  # ty:ignore[invalid-argument-type]
            local_start = start_utc.astimezone(tz)
            local_end = end_utc.astimezone(tz)

            # Check if maintenance window overlaps with peak hours
            peak_overlap_min = self._peak_overlap(
                local_start.hour + local_start.minute / 60,
                local_end.hour + local_end.minute / 60 + (24 if local_end.date() > local_start.date() else 0),
                info["peak_start"], info["peak_end"],  # ty:ignore[invalid-argument-type]
            )

            # Calculate player impact
            concurrent = info["avg_concurrent"]
            if is_weekend:
                concurrent *= info["weekend_mult"]  # ty:ignore[unsupported-operator]

            # Activity multiplier based on time of day (simplified model)
            hour = local_start.hour
            if info["peak_start"] <= hour <= info["peak_end"]:  # ty:ignore[unsupported-operator]
                activity_mult = 1.0
            elif 2 <= hour <= 6:
                activity_mult = 0.1
            elif 6 <= hour <= 12:
                activity_mult = 0.3
            else:
                activity_mult = 0.6

            affected = int(concurrent * activity_mult)  # ty:ignore[unsupported-operator]
            if maintenance_type == "rolling":
                affected = int(affected * 0.1)  # Rolling updates affect ~10% of users
            elif maintenance_type == "partial":
                affected = int(affected * 0.3)

            total_affected += affected

            jurisdiction_impact[jur] = {
                "local_start": local_start.strftime("%Y-%m-%d %H:%M %Z"),
                "local_end": local_end.strftime("%Y-%m-%d %H:%M %Z"),
                "peak_overlap_minutes": round(peak_overlap_min),
                "is_peak": peak_overlap_min > 0,
                "estimated_affected_players": affected,
                "risk_level": "HIGH" if peak_overlap_min > 30 else ("MEDIUM" if peak_overlap_min > 0 else "LOW"),
            }

        # Revenue impact estimation
        hourly_revenue_per_player = 2.5  # EUR average
        revenue_impact = total_affected * hourly_revenue_per_player * (duration_min / 60)

        # Cascade impact
        cascade_services = service_info.get("depended_by", [])

        return {
            "window": {
                "start_utc": start_utc.isoformat(),
                "end_utc": end_utc.isoformat(),
                "duration_minutes": duration_min,
                "is_weekend": is_weekend,
                "day_of_week": start_utc.strftime("%A"),
            },
            "service": {
                "name": service,
                "criticality": service_info["criticality"],
                "cascade_impact": cascade_services,
                "rollback_time_min": service_info["rollback_time_min"],
            },
            "jurisdiction_impact": jurisdiction_impact,
            "summary": {
                "total_affected_players": total_affected,
                "estimated_revenue_impact_eur": round(revenue_impact, 2),
                "highest_risk_jurisdictions": [
                    j for j, v in jurisdiction_impact.items() if v["risk_level"] == "HIGH"
                ],
                "recommendation": self._recommendation(total_affected, jurisdiction_impact, service_info),
            },
        }

    def find_optimal_window(self, service: str, duration_min: int,
                            preferred_date: Optional[datetime] = None,
                            maintenance_type: str = "rolling") -> list:
        """Find the optimal maintenance window with minimal player impact."""
        if preferred_date is None:
            preferred_date = datetime.now(tz=timezone.utc) + timedelta(days=1)

        # Scan every hour for 7 days
        candidates = []
        for day_offset in range(7):
            for hour in range(24):
                candidate = preferred_date.replace(
                    hour=hour, minute=0, second=0, microsecond=0
                ) + timedelta(days=day_offset)
                analysis = self.analyze_window(candidate, duration_min, service, maintenance_type)
                score = analysis["summary"]["total_affected_players"]
                candidates.append({
                    "start_utc": candidate.isoformat(),
                    "score": score,
                    "affected_players": score,
                    "revenue_impact": analysis["summary"]["estimated_revenue_impact_eur"],
                    "high_risk_jurisdictions": len(analysis["summary"]["highest_risk_jurisdictions"]),
                    "day": candidate.strftime("%A"),
                    "is_weekend": candidate.weekday() >= 5,
                })

        # Sort by score (fewer affected players = better)
        candidates.sort(key=lambda x: x["score"])
        return candidates[:10]  # Top 10

    def generate_checklist(self, service: str, maintenance_type: str) -> dict:
        """Generate pre-flight and post-deployment checklists."""
        service_info = SERVICE_DEPENDENCIES.get(service, {})

        pre_checks = [
            {"step": "Verify change ticket is approved", "command": None, "critical": True},
            {"step": "Notify on-call team", "command": None, "critical": True},
            {"step": "Update status page to 'Scheduled Maintenance'",
             "command": "statuspage-cli update --component {service} --status scheduled_maintenance",
             "critical": True},
            {"step": "Create database backup",
             "command": "pg_dump -h $DB_HOST -U $DB_USER casino_db | gzip > backup_$(date +%Y%m%d_%H%M).sql.gz",
             "critical": True},
            {"step": "Verify backup integrity",
             "command": "gunzip -t backup_*.sql.gz && echo 'Backup OK'",
             "critical": True},
            {"step": "Check current pod health",
             "command": f"kubectl get pods -n production -l app={service} -o wide",
             "critical": True},
            {"step": "Record current replica count",
             "command": f"kubectl get deployment {service} -n production -o jsonpath='{{.spec.replicas}}'",
             "critical": True},
            {"step": "Verify rollback image is available",
             "command": f"kubectl rollout history deployment/{service} -n production",
             "critical": True},
            {"step": "Check dependent service health",
             "command": "curl -s http://health-check.internal/api/v1/status | jq '.services'",
             "critical": True},
            {"step": "Pause non-critical cron jobs",
             "command": "kubectl patch cronjob -n production -l tier=non-critical -p '{\"spec\":{\"suspend\":true}}'",
             "critical": False},
            {"step": "Drain connections from target pods (if rolling)",
             "command": f"kubectl annotate pods -l app={service} drain=true --overwrite",
             "critical": maintenance_type == "rolling"},
            {"step": "Notify player support team",
             "command": None, "critical": True},
        ]

        post_checks = [
            {"step": "Verify all pods are Running",
             "command": f"kubectl get pods -n production -l app={service} | grep -v Running",
             "critical": True},
            {"step": "Check service endpoints are responding",
             "command": f"curl -sf http://{service}.production.svc/health | jq '.status'",
             "critical": True},
            {"step": "Verify database connectivity",
             "command": "psql -h $DB_HOST -U $DB_USER -c 'SELECT 1' casino_db",
             "critical": True},
            {"step": "Check error rate in last 5 minutes",
             "command": "promql 'rate(http_requests_total{status=~\"5..\",service=\"" + service + "\"}[5m])' | jq '.[0].value[1]'",
             "critical": True},
            {"step": "Verify latency is within SLA (p99 < 500ms)",
             "command": "promql 'histogram_quantile(0.99, rate(http_duration_seconds_bucket{service=\"" + service + "\"}[5m]))' | jq '.[0].value[1]'",
             "critical": True},
            {"step": "Run smoke tests",
             "command": f"./scripts/smoke-test.sh --service {service} --env production",
             "critical": True},
            {"step": "Check game round completion rate",
             "command": "promql 'rate(game_rounds_completed_total[5m]) / rate(game_rounds_started_total[5m])'",
             "critical": service in ["game-aggregator", "platform-core"]},
            {"step": "Verify payment processing",
             "command": "curl -sf http://payment-gateway.production.svc/health | jq '.providers'",
             "critical": service in ["payment-gateway", "wallet-service"]},
            {"step": "Resume cron jobs",
             "command": "kubectl patch cronjob -n production -l tier=non-critical -p '{\"spec\":{\"suspend\":false}}'",
             "critical": False},
            {"step": "Update status page to 'Operational'",
             "command": "statuspage-cli update --component {service} --status operational",
             "critical": True},
            {"step": "Send all-clear notification",
             "command": None, "critical": True},
            {"step": "Monitor for 30 minutes post-deployment",
             "command": f"watch -n 30 'kubectl top pods -n production -l app={service}'",
             "critical": True},
        ]

        rollback_plan = {
            "trigger_conditions": [
                "Error rate > 1% for 2 minutes",
                "p99 latency > 2s for 3 minutes",
                "Any payment processing failure",
                "Pod crash loop detected",
                "Health check failures > 3 consecutive",
            ],
            "rollback_command": f"kubectl rollout undo deployment/{service} -n production",
            "estimated_rollback_time_min": service_info.get("rollback_time_min", 10),
            "post_rollback_checks": [
                f"kubectl rollout status deployment/{service} -n production",
                f"curl -sf http://{service}.production.svc/health",
                f"./scripts/smoke-test.sh --service {service} --env production",
            ],
        }

        return {
            "service": service,
            "maintenance_type": maintenance_type,
            "pre_deployment_checks": pre_checks,
            "post_deployment_checks": post_checks,
            "rollback_plan": rollback_plan,
        }

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _peak_overlap(self, start_h: float, end_h: float,
                      peak_start: int, peak_end: int) -> float:
        """Calculate overlap in minutes between maintenance and peak hours."""
        overlap_start = max(start_h, peak_start)
        overlap_end = min(end_h, peak_end)
        overlap_hours = max(0, overlap_end - overlap_start)
        return overlap_hours * 60

    def _recommendation(self, total_affected: int, jurisdiction_impact: dict,
                        service_info: dict) -> str:
        high_risk = [j for j, v in jurisdiction_impact.items() if v["risk_level"] == "HIGH"]
        criticality = service_info.get("criticality", "medium")

        if not high_risk and total_affected < 1000:
            return "APPROVE - Low impact window. Proceed with standard change process."
        elif len(high_risk) <= 1 and total_affected < 5000:
            return (f"CONDITIONAL - Moderate impact ({total_affected} players). "
                    f"Consider alternative timing to avoid {', '.join(high_risk)} peak hours.")
        elif criticality == "critical" and high_risk:
            return (f"DEFER - High impact on critical service during peak hours in "
                    f"{', '.join(high_risk)}. Find a lower-impact window or use rolling deployment.")
        else:
            return (f"REVIEW - {total_affected} affected players across {len(high_risk)} high-risk "
                    f"jurisdictions. Requires VP-Engineering approval.")


# ---------------------------------------------------------------------------
# CLI Output Helpers
# ---------------------------------------------------------------------------

def print_analysis(analysis: dict):
    print(f"\n{'='*70}")
    print(f"MAINTENANCE WINDOW ANALYSIS")
    print(f"{'='*70}")
    w = analysis["window"]
    s = analysis["service"]
    print(f"\nWindow: {w['start_utc']} to {w['end_utc']} ({w['duration_minutes']}min)")
    print(f"Day: {w['day_of_week']} {'(WEEKEND)' if w['is_weekend'] else ''}")
    print(f"Service: {s['name']} (criticality: {s['criticality']})")
    if s["cascade_impact"]:
        print(f"Cascade impact: {', '.join(s['cascade_impact'])}")

    print(f"\n{'Jurisdiction':<15} {'Local Start':<22} {'Peak Overlap':<15} {'Affected':<10} {'Risk':<8}")
    print("-" * 70)
    for jur, impact in analysis["jurisdiction_impact"].items():
        print(f"{jur:<15} {impact['local_start']:<22} {impact['peak_overlap_minutes']}min{'':<10} "
              f"{impact['estimated_affected_players']:<10} {impact['risk_level']:<8}")

    sm = analysis["summary"]
    print(f"\n{'='*70}")
    print(f"SUMMARY")
    print(f"  Total affected players:     {sm['total_affected_players']}")
    print(f"  Estimated revenue impact:   EUR {sm['estimated_revenue_impact_eur']:,.2f}")
    print(f"  High-risk jurisdictions:    {', '.join(sm['highest_risk_jurisdictions']) or 'None'}")
    print(f"\n  RECOMMENDATION: {sm['recommendation']}")
    print(f"{'='*70}\n")


def print_optimal(candidates: list, service: str, duration: int):
    print(f"\n{'='*70}")
    print(f"TOP 10 OPTIMAL MAINTENANCE WINDOWS")
    print(f"Service: {service} | Duration: {duration}min")
    print(f"{'='*70}")
    print(f"\n{'#':<4} {'Start (UTC)':<22} {'Day':<12} {'Affected':<10} {'Revenue Impact':<16} {'Risk':<5}")
    print("-" * 70)
    for i, c in enumerate(candidates, 1):
        wknd = " (W)" if c["is_weekend"] else ""
        print(f"{i:<4} {c['start_utc'][:19]:<22} {c['day']}{wknd:<8} "
              f"{c['affected_players']:<10} EUR {c['revenue_impact']:>10,.2f}  {c['high_risk_jurisdictions']}")
    print(f"{'='*70}\n")


def print_checklist(checklist: dict):
    print(f"\n{'='*70}")
    print(f"MAINTENANCE CHECKLIST: {checklist['service']} ({checklist['maintenance_type']})")
    print(f"{'='*70}")

    print(f"\nPRE-DEPLOYMENT CHECKS:")
    for i, check in enumerate(checklist["pre_deployment_checks"], 1):
        critical = " [CRITICAL]" if check["critical"] else ""
        print(f"  [ ] {i}. {check['step']}{critical}")
        if check["command"]:
            print(f"       $ {check['command']}")

    print(f"\nPOST-DEPLOYMENT CHECKS:")
    for i, check in enumerate(checklist["post_deployment_checks"], 1):
        critical = " [CRITICAL]" if check["critical"] else ""
        print(f"  [ ] {i}. {check['step']}{critical}")
        if check["command"]:
            print(f"       $ {check['command']}")

    rb = checklist["rollback_plan"]
    print(f"\nROLLBACK PLAN:")
    print(f"  Estimated time: {rb['estimated_rollback_time_min']} minutes")
    print(f"  Command: {rb['rollback_command']}")
    print(f"  Trigger conditions:")
    for cond in rb["trigger_conditions"]:
        print(f"    - {cond}")
    print(f"{'='*70}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="iGaming Maintenance Window Scheduler")
    subparsers = parser.add_subparsers(dest="command")

    # Analyze
    analyze = subparsers.add_parser("analyze", help="Analyze a specific maintenance window")
    analyze.add_argument("--service", required=True)
    analyze.add_argument("--date", required=True, help="YYYY-MM-DD")
    analyze.add_argument("--time", required=True, help="HH:MM (UTC)")
    analyze.add_argument("--duration", type=int, required=True, help="Duration in minutes")
    analyze.add_argument("--type", default="rolling", choices=[t.value for t in MaintenanceType])

    # Optimal
    optimal = subparsers.add_parser("optimal", help="Find optimal maintenance window")
    optimal.add_argument("--service", required=True)
    optimal.add_argument("--duration", type=int, required=True, help="Duration in minutes")
    optimal.add_argument("--after", default=None, help="Earliest date YYYY-MM-DD")
    optimal.add_argument("--type", default="rolling", choices=[t.value for t in MaintenanceType])

    # Checklist
    checklist = subparsers.add_parser("checklist", help="Generate maintenance checklist")
    checklist.add_argument("--service", required=True)
    checklist.add_argument("--type", default="rolling", choices=[t.value for t in MaintenanceType])

    # Demo
    subparsers.add_parser("demo", help="Run a full demo")

    args = parser.parse_args()
    scheduler = MaintenanceScheduler()

    if args.command == "analyze":
        start = datetime.strptime(f"{args.date} {args.time}", "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        analysis = scheduler.analyze_window(start, args.duration, args.service, args.type)
        print_analysis(analysis)

    elif args.command == "optimal":
        after = None
        if args.after:
            after = datetime.strptime(args.after, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        candidates = scheduler.find_optimal_window(args.service, args.duration, after, args.type)
        print_optimal(candidates, args.service, args.duration)

    elif args.command == "checklist":
        cl = scheduler.generate_checklist(args.service, args.type)
        print_checklist(cl)

    elif args.command == "demo":
        print("\n--- Analyzing Tuesday 04:00 UTC window for wallet-service ---")
        start = datetime(2026, 3, 10, 4, 0, tzinfo=timezone.utc)
        analysis = scheduler.analyze_window(start, 60, "wallet-service", "rolling")
        print_analysis(analysis)

        print("\n--- Finding optimal windows for platform-core (120min) ---")
        after = datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc)
        candidates = scheduler.find_optimal_window("platform-core", 120, after)
        print_optimal(candidates, "platform-core", 120)

        print("\n--- Generating checklist for database-primary ---")
        cl = scheduler.generate_checklist("database-primary", "database")
        print_checklist(cl)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
