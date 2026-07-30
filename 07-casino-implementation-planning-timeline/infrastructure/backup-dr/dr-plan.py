#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 07, Casino Implementation Planning and Timeline.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Disaster Recovery Plan Generator for Gambling Platforms

Generates a comprehensive DR plan with RTO/RPO calculations, failover
procedures, communication plans, and regulatory notification requirements
specific to online gambling operations.

Usage:
    python3 dr-plan.py --primary eu-west-1 --dr eu-west-2
    python3 dr-plan.py --interactive
    python3 dr-plan.py --export dr-plan.json
"""

import argparse
import json
import sys
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Component definitions with RTO/RPO requirements
# ---------------------------------------------------------------------------

PLATFORM_COMPONENTS = {
    "wallet_service": {
        "name": "Wallet Service",
        "description": "Player wallet and balance management",
        "criticality": "P0",
        "rto_minutes": 15,
        "rpo_minutes": 0,  # Zero data loss - financial transactions
        "replication_strategy": "synchronous",
        "backup_frequency": "continuous",
        "failover_type": "automatic",
        "dependencies": ["postgresql_primary", "redis_sessions", "kafka"],
        "regulatory_impact": "Player balances must be recoverable to the last transaction. "
                             "Regulators require evidence of fund protection.",
        "test_frequency": "weekly",
    },
    "payment_service": {
        "name": "Payment Service",
        "description": "Deposit and withdrawal processing",
        "criticality": "P0",
        "rto_minutes": 30,
        "rpo_minutes": 0,
        "replication_strategy": "synchronous",
        "backup_frequency": "continuous",
        "failover_type": "automatic",
        "dependencies": ["wallet_service", "postgresql_primary", "payment_gateway_api"],
        "regulatory_impact": "Payment processing must maintain PCI DSS compliance during failover. "
                             "All pending withdrawals must be preserved.",
        "test_frequency": "weekly",
    },
    "game_aggregation": {
        "name": "Game Aggregation Layer",
        "description": "Game provider API integration and session management",
        "criticality": "P1",
        "rto_minutes": 30,
        "rpo_minutes": 5,
        "replication_strategy": "asynchronous",
        "backup_frequency": "every_5_minutes",
        "failover_type": "automatic",
        "dependencies": ["wallet_service", "redis_sessions", "game_provider_apis"],
        "regulatory_impact": "Active game sessions may be lost. Players must be refunded for "
                             "in-progress rounds per regulatory requirements.",
        "test_frequency": "monthly",
    },
    "user_service": {
        "name": "User Service",
        "description": "Player registration, authentication, KYC",
        "criticality": "P1",
        "rto_minutes": 30,
        "rpo_minutes": 5,
        "replication_strategy": "asynchronous",
        "backup_frequency": "every_5_minutes",
        "failover_type": "automatic",
        "dependencies": ["postgresql_primary", "redis_sessions", "kyc_provider_api"],
        "regulatory_impact": "KYC verification status must be preserved. Self-exclusion lists "
                             "must remain enforced during and after failover.",
        "test_frequency": "monthly",
    },
    "compliance_service": {
        "name": "Compliance & Reporting",
        "description": "AML monitoring, responsible gambling, regulatory reporting",
        "criticality": "P0",
        "rto_minutes": 15,
        "rpo_minutes": 0,
        "replication_strategy": "synchronous",
        "backup_frequency": "continuous",
        "failover_type": "automatic",
        "dependencies": ["postgresql_primary", "kafka", "elasticsearch"],
        "regulatory_impact": "AML transaction monitoring must not have gaps. Self-exclusion "
                             "enforcement is legally mandatory and must survive DR events.",
        "test_frequency": "weekly",
    },
    "responsible_gambling": {
        "name": "Responsible Gambling Controls",
        "description": "Deposit limits, session limits, reality checks, self-exclusion",
        "criticality": "P0",
        "rto_minutes": 5,
        "rpo_minutes": 0,
        "replication_strategy": "synchronous",
        "backup_frequency": "continuous",
        "failover_type": "automatic",
        "dependencies": ["redis_sessions", "postgresql_primary"],
        "regulatory_impact": "CRITICAL: Self-exclusion and deposit limits MUST be enforced "
                             "at all times. Failure to enforce is a license violation.",
        "test_frequency": "daily",
    },
    "postgresql_primary": {
        "name": "PostgreSQL Primary Database",
        "description": "Primary transactional database",
        "criticality": "P0",
        "rto_minutes": 10,
        "rpo_minutes": 0,
        "replication_strategy": "synchronous",
        "backup_frequency": "continuous_wal",
        "failover_type": "automatic_promotion",
        "dependencies": [],
        "regulatory_impact": "All player data, transaction history, and audit logs stored here. "
                             "Data loss would require regulatory notification.",
        "test_frequency": "weekly",
    },
    "redis_sessions": {
        "name": "Redis Session Store",
        "description": "Player sessions, rate limiting, caching",
        "criticality": "P1",
        "rto_minutes": 5,
        "rpo_minutes": 15,
        "replication_strategy": "asynchronous",
        "backup_frequency": "every_15_minutes",
        "failover_type": "automatic",
        "dependencies": [],
        "regulatory_impact": "Session loss forces re-authentication. Active game sessions "
                             "require investigation for stuck funds.",
        "test_frequency": "monthly",
    },
    "kafka": {
        "name": "Apache Kafka Event Bus",
        "description": "Event streaming for transactions, audit trail",
        "criticality": "P1",
        "rto_minutes": 15,
        "rpo_minutes": 0,
        "replication_strategy": "multi_az_replication",
        "backup_frequency": "continuous",
        "failover_type": "automatic",
        "dependencies": [],
        "regulatory_impact": "Transaction audit trail must be complete. Any gaps require "
                             "manual reconciliation and regulatory notification.",
        "test_frequency": "monthly",
    },
    "elasticsearch": {
        "name": "Elasticsearch",
        "description": "Search, audit log indexing, analytics",
        "criticality": "P2",
        "rto_minutes": 60,
        "rpo_minutes": 30,
        "replication_strategy": "asynchronous",
        "backup_frequency": "hourly_snapshot",
        "failover_type": "manual",
        "dependencies": [],
        "regulatory_impact": "Search and analytics downtime acceptable. Audit log source "
                             "of truth is in PostgreSQL and Kafka, not Elasticsearch.",
        "test_frequency": "quarterly",
    },
    "cdn_static_assets": {
        "name": "CDN & Static Assets",
        "description": "Game assets, images, JS bundles",
        "criticality": "P2",
        "rto_minutes": 5,
        "rpo_minutes": 60,
        "replication_strategy": "cdn_origin_failover",
        "backup_frequency": "daily",
        "failover_type": "automatic",
        "dependencies": [],
        "regulatory_impact": "None - static content only.",
        "test_frequency": "quarterly",
    },
}


REGULATORY_NOTIFICATIONS = {
    "uk": {
        "regulator": "UK Gambling Commission",
        "notification_required": True,
        "notification_deadline": "24 hours",
        "report_to": "Key Event notification via eServices portal",
        "threshold": "Any unplanned outage affecting player access or fund protection",
        "required_information": [
            "Nature of the incident",
            "Time of occurrence and detection",
            "Impact on player funds and active sessions",
            "Self-exclusion enforcement status during outage",
            "Steps taken to resolve",
            "Preventive measures for future",
        ],
    },
    "malta": {
        "regulator": "Malta Gaming Authority",
        "notification_required": True,
        "notification_deadline": "72 hours",
        "report_to": "MGA Compliance Department",
        "threshold": "Outage exceeding 4 hours or any data loss",
        "required_information": [
            "Incident description",
            "Duration of outage",
            "Number of affected players",
            "Data integrity assessment",
            "Root cause analysis",
        ],
    },
    "ontario": {
        "regulator": "AGCO / iGaming Ontario",
        "notification_required": True,
        "notification_deadline": "48 hours",
        "report_to": "iGO Operations Team",
        "threshold": "Any unplanned outage exceeding 2 hours",
        "required_information": [
            "Incident timeline",
            "Player impact assessment",
            "Financial reconciliation results",
            "Corrective actions",
        ],
    },
}


@dataclass
class DRPlan:
    """Complete disaster recovery plan."""
    generated_at: str
    primary_region: str
    dr_region: str
    components: dict
    overall_rto_minutes: int
    overall_rpo_minutes: int
    failover_procedure: list
    communication_plan: dict
    regulatory_notifications: dict
    testing_schedule: dict
    runbook: list


def generate_dr_plan(primary_region: str, dr_region: str, jurisdictions: list) -> DRPlan:
    """Generate a complete DR plan."""
    logger.info(f"Generating DR plan: {primary_region} -> {dr_region}")

    # Calculate overall RTO/RPO (worst case of P0 components)
    p0_components = {k: v for k, v in PLATFORM_COMPONENTS.items() if v["criticality"] == "P0"}
    overall_rto = max(c["rto_minutes"] for c in p0_components.values())
    overall_rpo = max(c["rpo_minutes"] for c in p0_components.values())

    # Generate failover procedure
    failover_steps = _generate_failover_procedure(primary_region, dr_region)

    # Communication plan
    comm_plan = _generate_communication_plan()

    # Regulatory notifications for selected jurisdictions
    reg_notifications = {j: REGULATORY_NOTIFICATIONS[j] for j in jurisdictions
                         if j in REGULATORY_NOTIFICATIONS}

    # Testing schedule
    testing = _generate_testing_schedule()

    # Runbook
    runbook = _generate_runbook(primary_region, dr_region)

    plan = DRPlan(
        generated_at=datetime.now(timezone.utc).isoformat(),
        primary_region=primary_region,
        dr_region=dr_region,
        components=PLATFORM_COMPONENTS,
        overall_rto_minutes=overall_rto,  # ty:ignore[invalid-argument-type]
        overall_rpo_minutes=overall_rpo,  # ty:ignore[invalid-argument-type]
        failover_procedure=failover_steps,
        communication_plan=comm_plan,
        regulatory_notifications=reg_notifications,
        testing_schedule=testing,
        runbook=runbook,
    )
    return plan


def _generate_failover_procedure(primary: str, dr: str) -> list:
    """Generate step-by-step failover procedure."""
    return [
        {
            "step": 1,
            "action": "DETECT - Automated monitoring detects failure",
            "responsible": "On-Call SRE",
            "time_estimate": "0-5 minutes",
            "details": [
                "Prometheus alerts fire for primary region health check failures",
                "PagerDuty escalates to on-call SRE and CTO",
                "Automated pre-checks validate DR region readiness",
                f"Verify {dr} cluster is healthy via kubectl cluster-info",
            ],
        },
        {
            "step": 2,
            "action": "ASSESS - Determine scope and authorize failover",
            "responsible": "CTO / On-Call SRE",
            "time_estimate": "5-10 minutes",
            "details": [
                "Determine if outage is region-wide or service-specific",
                "Check AWS Health Dashboard for known issues",
                "If single service: attempt service restart before full failover",
                "If region-wide: authorize full DR failover (requires CTO approval)",
                "Notify Compliance Officer of potential regulatory reporting requirement",
            ],
        },
        {
            "step": 3,
            "action": "HALT - Stop accepting new transactions in primary",
            "responsible": "SRE Team",
            "time_estimate": "1-2 minutes",
            "details": [
                "Set maintenance mode on API gateway (return 503 with retry-after)",
                "Halt all payment processing (pending withdrawals queued, not lost)",
                "Notify game providers to suspend game launches",
                "Send player-facing maintenance notification",
                "CRITICAL: Verify self-exclusion list is synced to DR region",
            ],
        },
        {
            "step": 4,
            "action": "PROMOTE - Activate DR infrastructure",
            "responsible": "SRE Team",
            "time_estimate": "5-15 minutes",
            "details": [
                f"Promote PostgreSQL read replica in {dr} to primary",
                f"Verify Redis cluster in {dr} is operational",
                f"Scale up Kubernetes deployments in {dr} to production levels",
                "Verify all P0 services pass health checks",
                "Validate wallet service can read/write balances",
                "Confirm responsible gambling limits are enforced",
            ],
        },
        {
            "step": 5,
            "action": "REDIRECT - Switch traffic to DR region",
            "responsible": "SRE Team",
            "time_estimate": "2-5 minutes",
            "details": [
                f"Update Route53 failover records to point to {dr}",
                "Verify DNS propagation (check from multiple regions)",
                "Update CDN origin to DR region endpoints",
                "Verify API gateway in DR region is serving traffic",
                "Monitor error rates during traffic shift",
            ],
        },
        {
            "step": 6,
            "action": "VALIDATE - Confirm DR environment is operational",
            "responsible": "SRE + QA Team",
            "time_estimate": "10-15 minutes",
            "details": [
                "Execute automated smoke test suite against DR endpoints",
                "Verify player login and session creation",
                "Test deposit flow (small amount, test payment provider)",
                "Test game launch for at least 2 providers",
                "Verify withdrawal queue processing",
                "Confirm compliance dashboards are receiving data",
                "Validate self-exclusion enforcement with test account",
            ],
        },
        {
            "step": 7,
            "action": "RESUME - Re-open platform to players",
            "responsible": "CTO / Operations",
            "time_estimate": "2-5 minutes",
            "details": [
                "Remove maintenance mode from API gateway",
                "Resume payment processing",
                "Notify game providers to resume game launches",
                "Send player notification that service is restored",
                "Begin monitoring for any data consistency issues",
            ],
        },
        {
            "step": 8,
            "action": "RECONCILE - Post-failover data verification",
            "responsible": "Finance + Engineering",
            "time_estimate": "1-4 hours",
            "details": [
                "Run wallet balance reconciliation across all players",
                "Verify all pending withdrawals are accounted for",
                "Check for any stuck game sessions and process refunds",
                "Validate transaction logs have no gaps",
                "Generate report for regulatory notification",
                "Begin root cause analysis of primary region failure",
            ],
        },
    ]


def _generate_communication_plan() -> dict:
    """Generate communication plan for DR events."""
    return {
        "internal_escalation": [
            {"level": 1, "role": "On-Call SRE", "notify_via": "PagerDuty", "within": "immediate"},
            {"level": 2, "role": "CTO", "notify_via": "Phone + Slack", "within": "5 minutes"},
            {"level": 3, "role": "Compliance Officer", "notify_via": "Phone + Email", "within": "10 minutes"},
            {"level": 4, "role": "CEO", "notify_via": "Phone", "within": "15 minutes"},
            {"level": 5, "role": "Board of Directors", "notify_via": "Email", "within": "1 hour"},
        ],
        "external_notifications": [
            {"party": "Players", "channel": "In-app banner + email", "timing": "Within 5 minutes of detection"},
            {"party": "Game Providers", "channel": "API notification + email", "timing": "Within 10 minutes"},
            {"party": "Payment Providers", "channel": "Direct API + phone", "timing": "Within 10 minutes"},
            {"party": "Regulator(s)", "channel": "Official reporting channel", "timing": "Per jurisdiction requirements"},
            {"party": "Affiliate Partners", "channel": "Email + partner portal", "timing": "Within 30 minutes"},
        ],
        "status_page": {
            "url": "https://status.casino-platform.com",
            "provider": "Statuspage.io or Instatus",
            "update_frequency": "Every 15 minutes during incident",
        },
    }


def _generate_testing_schedule() -> dict:
    """Generate DR testing schedule."""
    return {
        "weekly": {
            "tests": [
                "PostgreSQL backup restore to test instance",
                "Wallet balance reconciliation check",
                "Self-exclusion list sync verification",
                "Payment service failover simulation",
            ],
            "duration": "30 minutes",
            "impact": "None - uses test instances",
        },
        "monthly": {
            "tests": [
                "Full application failover to DR region",
                "Game provider API failover",
                "Redis cluster failover",
                "Kafka consumer group rebalance",
                "DNS failover simulation",
            ],
            "duration": "2-4 hours",
            "impact": "Minimal - scheduled maintenance window",
            "notification": "Notify players 48 hours in advance",
        },
        "quarterly": {
            "tests": [
                "Full DR failover with live traffic (controlled)",
                "Complete data reconciliation across regions",
                "Communication plan drill (all stakeholders)",
                "Regulatory notification drill",
                "Full RTO/RPO measurement and documentation",
            ],
            "duration": "4-8 hours",
            "impact": "Planned maintenance window required",
            "notification": "Notify regulator and players 1 week in advance",
        },
        "annually": {
            "tests": [
                "Tabletop exercise with full executive team",
                "Third-party DR audit (required by some regulators)",
                "Complete DR plan review and update",
                "Vendor DR capability assessment",
            ],
            "duration": "1-2 days",
            "impact": "Planning exercise - no production impact",
        },
    }


def _generate_runbook(primary: str, dr: str) -> list:
    """Generate operational runbook commands."""
    return [
        {
            "title": "Quick Health Check",
            "commands": [
                f"# Check primary region services",
                f"kubectl --context=casino-{primary} get pods -n casino-production",
                f"",
                f"# Check DR region readiness",
                f"kubectl --context=casino-{dr} get pods -n casino-production",
                f"",
                f"# Check database replication lag",
                f"psql -h casino-dr-db.{dr}.rds.amazonaws.com -U casino_admin -c "
                f"\"SELECT EXTRACT(EPOCH FROM replay_lag)::int AS lag_seconds FROM pg_stat_replication;\"",
            ],
        },
        {
            "title": "Initiate Failover",
            "commands": [
                f"# Step 1: Enable maintenance mode",
                f"kubectl --context=casino-{primary} set env deployment/api-gateway MAINTENANCE_MODE=true -n casino-production",
                f"",
                f"# Step 2: Promote DR database",
                f"aws rds promote-read-replica --db-instance-identifier casino-dr-{dr} --region {dr}",
                f"",
                f"# Step 3: Scale up DR services",
                f"kubectl --context=casino-{dr} scale deployment --all --replicas=3 -n casino-production",
                f"",
                f"# Step 4: Switch DNS",
                f"aws route53 change-resource-record-sets --hosted-zone-id ZXXXXX --change-batch file://dns-failover.json",
                f"",
                f"# Step 5: Verify",
                f"curl -s https://api.casino-platform.com/health | jq .",
            ],
        },
        {
            "title": "Post-Failover Reconciliation",
            "commands": [
                f"# Run wallet reconciliation",
                f"kubectl --context=casino-{dr} exec -it deploy/wallet-service -n casino-production -- "
                f"python3 manage.py reconcile_balances --report",
                f"",
                f"# Check for stuck game sessions",
                f"kubectl --context=casino-{dr} exec -it deploy/game-aggregator -n casino-production -- "
                f"node scripts/check-stuck-sessions.js --refund",
                f"",
                f"# Verify self-exclusion enforcement",
                f"kubectl --context=casino-{dr} exec -it deploy/compliance-service -n casino-production -- "
                f"python3 manage.py verify_exclusion_list",
            ],
        },
    ]


def print_plan(plan: DRPlan):
    """Print the DR plan in human-readable format."""
    print("\n" + "=" * 80)
    print("  DISASTER RECOVERY PLAN")
    print("  Online Gambling Platform")
    print("=" * 80)

    print(f"\n  Generated:      {plan.generated_at}")
    print(f"  Primary Region: {plan.primary_region}")
    print(f"  DR Region:      {plan.dr_region}")
    print(f"  Overall RTO:    {plan.overall_rto_minutes} minutes")
    print(f"  Overall RPO:    {plan.overall_rpo_minutes} minutes")

    # Component summary
    print(f"\n  COMPONENT RTO/RPO MATRIX")
    print(f"  {'-' * 70}")
    print(f"  {'Component':<30} {'Priority':<10} {'RTO':<10} {'RPO':<10} {'Failover':<15}")
    print(f"  {'-' * 70}")

    for key, comp in sorted(plan.components.items(), key=lambda x: x[1]["criticality"]):
        print(f"  {comp['name']:<30} {comp['criticality']:<10} "
              f"{comp['rto_minutes']}min{'':<5} {comp['rpo_minutes']}min{'':<5} "
              f"{comp['failover_type']}")

    # Failover procedure
    print(f"\n\n  FAILOVER PROCEDURE")
    print(f"  {'-' * 70}")
    for step in plan.failover_procedure:
        print(f"\n  Step {step['step']}: {step['action']}")
        print(f"  Responsible: {step['responsible']}  |  Time: {step['time_estimate']}")
        for detail in step["details"]:
            print(f"    - {detail}")

    # Regulatory notifications
    if plan.regulatory_notifications:
        print(f"\n\n  REGULATORY NOTIFICATION REQUIREMENTS")
        print(f"  {'-' * 70}")
        for jur, notif in plan.regulatory_notifications.items():
            print(f"\n  {notif['regulator']}")
            print(f"    Deadline:  {notif['notification_deadline']}")
            print(f"    Report to: {notif['report_to']}")
            print(f"    Threshold: {notif['threshold']}")
            print(f"    Required information:")
            for info in notif["required_information"]:
                print(f"      - {info}")

    # Testing schedule
    print(f"\n\n  DR TESTING SCHEDULE")
    print(f"  {'-' * 70}")
    for frequency, schedule in plan.testing_schedule.items():
        print(f"\n  {frequency.upper()} (Duration: {schedule['duration']})")
        for test in schedule["tests"]:
            print(f"    - {test}")

    print("\n" + "=" * 80)


def main():
    parser = argparse.ArgumentParser(description="DR Plan Generator for Gambling Platforms")
    parser.add_argument("--primary", type=str, default="eu-west-1",
                        help="Primary AWS region")
    parser.add_argument("--dr", type=str, default="eu-west-2",
                        help="DR AWS region")
    parser.add_argument("--jurisdictions", type=str, nargs="+",
                        default=["uk", "malta"],
                        help="Target jurisdictions for regulatory notifications")
    parser.add_argument("--export", type=str, default=None,
                        help="Export plan to JSON file")
    parser.add_argument("--interactive", action="store_true",
                        help="Run in interactive mode")

    args = parser.parse_args()

    if args.interactive:
        print("\n=== DR Plan Generator ===\n")
        primary = input("Primary region [eu-west-1]: ").strip() or "eu-west-1"
        dr = input("DR region [eu-west-2]: ").strip() or "eu-west-2"
        jur_input = input("Jurisdictions (comma-separated) [uk,malta]: ").strip() or "uk,malta"
        jurisdictions = [j.strip() for j in jur_input.split(",")]
    else:
        primary = args.primary
        dr = args.dr
        jurisdictions = args.jurisdictions

    plan = generate_dr_plan(primary, dr, jurisdictions)
    print_plan(plan)

    if args.export:
        export_data = {
            "generated_at": plan.generated_at,
            "primary_region": plan.primary_region,
            "dr_region": plan.dr_region,
            "overall_rto_minutes": plan.overall_rto_minutes,
            "overall_rpo_minutes": plan.overall_rpo_minutes,
            "components": plan.components,
            "failover_procedure": plan.failover_procedure,
            "communication_plan": plan.communication_plan,
            "regulatory_notifications": plan.regulatory_notifications,
            "testing_schedule": plan.testing_schedule,
            "runbook": plan.runbook,
        }
        with open(args.export, "w") as f:
            json.dump(export_data, f, indent=2, default=str)
        logger.info(f"DR plan exported to {args.export}")


if __name__ == "__main__":
    main()
