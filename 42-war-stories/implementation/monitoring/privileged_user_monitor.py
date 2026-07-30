#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 42, War Stories.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Privileged User Activity Monitoring and Anomaly Detection
============================================================

Monitors privileged user activities on iGaming platforms for suspicious
behavior, policy violations, and anomalous access patterns. Detects
insider threats targeting player data, financial systems, and RNG.

Usage:
    python privileged_user_monitor.py --demo
    python privileged_user_monitor.py --analyze --window 24h
    python privileged_user_monitor.py --report
"""

import json
import logging
import argparse
import random
import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
from collections import defaultdict
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


class AlertSeverity(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ActivityCategory(Enum):
    DATA_ACCESS = "data_access"
    DATA_EXPORT = "data_export"
    CONFIG_CHANGE = "config_change"
    FINANCIAL_ACTION = "financial_action"
    PLAYER_MODIFICATION = "player_modification"
    SYSTEM_ACCESS = "system_access"
    AUTH_EVENT = "auth_event"
    RNG_ACCESS = "rng_access"


@dataclass
class ActivityEvent:
    timestamp: str
    user_id: str
    username: str
    role: str
    category: ActivityCategory
    action: str
    target_resource: str
    details: str = ""
    ip_address: str = ""
    geo_location: str = ""
    session_id: str = ""
    jit_request_id: str = ""
    risk_score: float = 0
    flagged: bool = False
    flag_reasons: list = field(default_factory=list)


@dataclass
class AnomalyAlert:
    id: str
    timestamp: str
    severity: AlertSeverity
    user_id: str
    username: str
    rule_id: str
    rule_name: str
    description: str
    evidence: list = field(default_factory=list)
    recommended_action: str = ""
    acknowledged: bool = False
    false_positive: bool = False


@dataclass
class UserBaseline:
    """Behavioral baseline for a privileged user."""
    user_id: str
    typical_hours: tuple = (8, 18)      # UTC working hours
    typical_locations: list = field(default_factory=list)
    avg_daily_queries: float = 0
    avg_daily_data_exports: float = 0
    typical_accessed_tables: set = field(default_factory=set)
    typical_actions: set = field(default_factory=set)
    max_records_per_query: int = 1000


# ---------------------------------------------------------------------------
# Detection rules
# ---------------------------------------------------------------------------

@dataclass
class DetectionRule:
    id: str
    name: str
    description: str
    severity: AlertSeverity
    category: str
    check_fn_name: str   # method name on the monitor class
    enabled: bool = True
    gambling_specific: bool = True


DETECTION_RULES = [
    DetectionRule("PUM-001", "Off-hours privileged access",
                  "Privileged access outside normal working hours (potential insider threat)",
                  AlertSeverity.MEDIUM, "temporal", "check_off_hours"),
    DetectionRule("PUM-002", "Excessive data export",
                  "User exported more data than their baseline (potential data exfiltration)",
                  AlertSeverity.HIGH, "volume", "check_excessive_export"),
    DetectionRule("PUM-003", "Player data bulk access",
                  "Bulk access to player PII records without corresponding support tickets",
                  AlertSeverity.HIGH, "data_access", "check_bulk_player_access"),
    DetectionRule("PUM-004", "Financial table access without JIT",
                  "Access to financial/transaction tables without approved JIT access request",
                  AlertSeverity.CRITICAL, "authorization", "check_financial_no_jit"),
    DetectionRule("PUM-005", "RNG system access",
                  "Any access to RNG configuration, seeds, or algorithm parameters",
                  AlertSeverity.CRITICAL, "rng", "check_rng_access", gambling_specific=True),
    DetectionRule("PUM-006", "Geo-location anomaly",
                  "Access from unusual geographic location",
                  AlertSeverity.HIGH, "location", "check_geo_anomaly"),
    DetectionRule("PUM-007", "Self-service on own account",
                  "Admin modifying their own player account (balance, bonus, KYC)",
                  AlertSeverity.CRITICAL, "conflict_of_interest", "check_self_service",
                  gambling_specific=True),
    DetectionRule("PUM-008", "Suspicious query patterns",
                  "Queries targeting high-value players or large balances",
                  AlertSeverity.HIGH, "query_pattern", "check_suspicious_queries",
                  gambling_specific=True),
    DetectionRule("PUM-009", "Rapid privilege escalation",
                  "Multiple JIT requests in short timeframe",
                  AlertSeverity.MEDIUM, "escalation", "check_rapid_escalation"),
    DetectionRule("PUM-010", "Balance modification without ticket",
                  "Manual balance adjustment without linked support ticket or approval",
                  AlertSeverity.CRITICAL, "financial", "check_balance_modification",
                  gambling_specific=True),
]


# ---------------------------------------------------------------------------
# Monitor engine
# ---------------------------------------------------------------------------

class PrivilegedUserMonitor:
    """Monitor privileged user activities for anomalies and policy violations."""

    def __init__(self):
        self.events: list[ActivityEvent] = []
        self.alerts: list[AnomalyAlert] = []
        self.baselines: dict[str, UserBaseline] = {}
        self.rules = DETECTION_RULES
        self._alert_counter = 0

    def ingest_event(self, event: ActivityEvent) -> list[AnomalyAlert]:
        """Ingest an activity event and check against detection rules."""
        self.events.append(event)
        new_alerts = []

        for rule in self.rules:
            if not rule.enabled:
                continue
            check_fn = getattr(self, rule.check_fn_name, None)
            if check_fn:
                alert = check_fn(event, rule)
                if alert:
                    new_alerts.append(alert)
                    self.alerts.append(alert)

        if new_alerts:
            event.flagged = True
            event.flag_reasons = [a.rule_name for a in new_alerts]

        return new_alerts

    def _create_alert(self, rule: DetectionRule, event: ActivityEvent,
                      description: str, evidence: list) -> AnomalyAlert:
        self._alert_counter += 1
        return AnomalyAlert(
            id=f"ALERT-{self._alert_counter:04d}",
            timestamp=datetime.utcnow().isoformat(),  # ty:ignore[deprecated]
            severity=rule.severity,
            user_id=event.user_id,
            username=event.username,
            rule_id=rule.id,
            rule_name=rule.name,
            description=description,
            evidence=evidence,
            recommended_action=self._get_recommendation(rule),
        )

    # Detection check methods
    def check_off_hours(self, event: ActivityEvent, rule: DetectionRule) -> Optional[AnomalyAlert]:
        baseline = self.baselines.get(event.user_id)
        if not baseline:
            return None
        hour = datetime.fromisoformat(event.timestamp).hour
        if hour < baseline.typical_hours[0] or hour > baseline.typical_hours[1]:
            return self._create_alert(rule, event,
                f"{event.username} accessed {event.target_resource} at {hour}:00 UTC (outside {baseline.typical_hours[0]}-{baseline.typical_hours[1]})",
                [f"Timestamp: {event.timestamp}", f"Action: {event.action}",
                 f"Normal hours: {baseline.typical_hours[0]}-{baseline.typical_hours[1]} UTC"])
        return None

    def check_excessive_export(self, event: ActivityEvent, rule: DetectionRule) -> Optional[AnomalyAlert]:
        if event.category != ActivityCategory.DATA_EXPORT:
            return None
        today = datetime.fromisoformat(event.timestamp).date()
        exports_today = sum(1 for e in self.events
                            if e.user_id == event.user_id
                            and e.category == ActivityCategory.DATA_EXPORT
                            and datetime.fromisoformat(e.timestamp).date() == today)
        baseline = self.baselines.get(event.user_id)
        threshold = baseline.avg_daily_data_exports * 3 if baseline else 5
        if exports_today > threshold:
            return self._create_alert(rule, event,
                f"{event.username} has {exports_today} data exports today (threshold: {threshold:.0f})",
                [f"Exports today: {exports_today}", f"Resource: {event.target_resource}"])
        return None

    def check_bulk_player_access(self, event: ActivityEvent, rule: DetectionRule) -> Optional[AnomalyAlert]:
        if "player" not in event.target_resource.lower():
            return None
        if "bulk" in event.action.lower() or "export" in event.action.lower() or "SELECT *" in event.details.upper():
            return self._create_alert(rule, event,
                f"{event.username} performed bulk player data access: {event.action}",
                [f"Resource: {event.target_resource}", f"Details: {event.details[:200]}"])
        return None

    def check_financial_no_jit(self, event: ActivityEvent, rule: DetectionRule) -> Optional[AnomalyAlert]:
        financial_resources = ["transactions", "payments", "withdrawals", "deposits", "player_wallets", "settlements"]
        if event.category == ActivityCategory.FINANCIAL_ACTION or \
           any(r in event.target_resource.lower() for r in financial_resources):
            if not event.jit_request_id:
                return self._create_alert(rule, event,
                    f"{event.username} accessed financial resource without JIT approval: {event.target_resource}",
                    [f"Action: {event.action}", f"No JIT request ID provided",
                     "CRITICAL: All financial access requires approved JIT request"])
        return None

    def check_rng_access(self, event: ActivityEvent, rule: DetectionRule) -> Optional[AnomalyAlert]:
        rng_keywords = ["rng", "random", "seed", "entropy", "algorithm"]
        if any(k in event.target_resource.lower() or k in event.action.lower() for k in rng_keywords):
            return self._create_alert(rule, event,
                f"RNG system access by {event.username}: {event.action} on {event.target_resource}",
                [f"Action: {event.action}", f"Details: {event.details}",
                 "RNG access is ALWAYS flagged per regulatory requirements"])
        return None

    def check_geo_anomaly(self, event: ActivityEvent, rule: DetectionRule) -> Optional[AnomalyAlert]:
        baseline = self.baselines.get(event.user_id)
        if baseline and event.geo_location and event.geo_location not in baseline.typical_locations:
            return self._create_alert(rule, event,
                f"{event.username} accessed from unusual location: {event.geo_location}",
                [f"Expected locations: {baseline.typical_locations}",
                 f"Actual: {event.geo_location}", f"IP: {event.ip_address}"])
        return None

    def check_self_service(self, event: ActivityEvent, rule: DetectionRule) -> Optional[AnomalyAlert]:
        if event.category == ActivityCategory.PLAYER_MODIFICATION:
            if event.user_id.lower() in event.target_resource.lower() or \
               event.username.lower() in event.details.lower():
                return self._create_alert(rule, event,
                    f"SELF-SERVICE: {event.username} modified their own player account",
                    [f"Action: {event.action}", f"Target: {event.target_resource}",
                     "Conflict of interest: admin modifying own account"])
        return None

    def check_suspicious_queries(self, event: ActivityEvent, rule: DetectionRule) -> Optional[AnomalyAlert]:
        suspicious = ["ORDER BY balance DESC", "WHERE balance >", "VIP", "whale", "high_roller",
                       "WHERE amount >", "LIMIT 1", "password", "token"]
        if event.category == ActivityCategory.DATA_ACCESS:
            for pattern in suspicious:
                if pattern.lower() in event.details.lower():
                    return self._create_alert(rule, event,
                        f"Suspicious query pattern by {event.username}: contains '{pattern}'",
                        [f"Query: {event.details[:300]}", f"Pattern matched: {pattern}"])
        return None

    def check_rapid_escalation(self, event: ActivityEvent, rule: DetectionRule) -> Optional[AnomalyAlert]:
        if event.category != ActivityCategory.AUTH_EVENT:
            return None
        recent = [e for e in self.events
                  if e.user_id == event.user_id
                  and e.category == ActivityCategory.AUTH_EVENT
                  and datetime.fromisoformat(e.timestamp) > datetime.utcnow() - timedelta(hours=1)]  # ty:ignore[deprecated]
        if len(recent) > 3:
            return self._create_alert(rule, event,
                f"{event.username} made {len(recent)} privilege requests in 1 hour",
                [f"Requests: {len(recent)}", "Possible privilege escalation attempt"])
        return None

    def check_balance_modification(self, event: ActivityEvent, rule: DetectionRule) -> Optional[AnomalyAlert]:
        if event.category == ActivityCategory.FINANCIAL_ACTION:
            balance_actions = ["adjust_balance", "manual_credit", "manual_debit", "void", "refund"]
            if any(a in event.action.lower() for a in balance_actions):
                if "ticket" not in event.details.lower() and "approved" not in event.details.lower():
                    return self._create_alert(rule, event,
                        f"{event.username} performed balance modification without ticket reference",
                        [f"Action: {event.action}", f"Target: {event.target_resource}",
                         f"Details: {event.details[:200]}",
                         "All manual balance changes require linked support ticket"])
        return None

    def _get_recommendation(self, rule: DetectionRule) -> str:
        recs = {
            "PUM-001": "Verify with user. If unauthorized, revoke access and investigate.",
            "PUM-002": "Immediately restrict export permissions. Check for data exfiltration.",
            "PUM-003": "Verify business justification. Check for linked support tickets.",
            "PUM-004": "IMMEDIATE: Revoke access. All financial access requires JIT approval.",
            "PUM-005": "REGULATORY: Log and report to compliance. Verify authorization.",
            "PUM-006": "Verify identity via secondary channel. Consider session termination.",
            "PUM-007": "IMMEDIATE: Revoke and investigate. Conflict of interest violation.",
            "PUM-008": "Review query intent. Flag for compliance review.",
            "PUM-009": "Review justification for multiple requests. Possible abuse.",
            "PUM-010": "IMMEDIATE: Freeze modifications. Require supervisor review.",
        }
        return recs.get(rule.id, "Investigate and document findings.")

    def get_summary(self, hours: int = 24) -> dict:
        cutoff = datetime.utcnow() - timedelta(hours=hours)  # ty:ignore[deprecated]
        recent_events = [e for e in self.events if datetime.fromisoformat(e.timestamp) > cutoff]
        recent_alerts = [a for a in self.alerts if datetime.fromisoformat(a.timestamp) > cutoff]

        by_severity = defaultdict(int)
        for a in recent_alerts:
            by_severity[a.severity.value] += 1

        by_user = defaultdict(int)
        for a in recent_alerts:
            by_user[a.username] += 1

        return {
            "period_hours": hours,
            "total_events": len(recent_events),
            "total_alerts": len(recent_alerts),
            "by_severity": dict(by_severity),
            "by_user": dict(by_user),
            "critical_alerts": [asdict(a) for a in recent_alerts if a.severity == AlertSeverity.CRITICAL],
            "flagged_users": list(by_user.keys()),
        }


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def demo():
    monitor = PrivilegedUserMonitor()

    # Set up baselines
    monitor.baselines["user-001"] = UserBaseline(
        "user-001", typical_hours=(8, 18),
        typical_locations=["London, UK", "Malta"],
        avg_daily_queries=25, avg_daily_data_exports=2,
    )

    print("=== Privileged User Monitor Demo ===\n")

    # Simulate events
    events = [
        ActivityEvent(datetime.utcnow().isoformat(), "user-001", "john.admin", "dba",  # ty:ignore[deprecated]
                      ActivityCategory.DATA_ACCESS, "SELECT query",
                      "players", "SELECT * FROM players WHERE balance > 100000 ORDER BY balance DESC",
                      "10.0.0.5", "London, UK"),
        ActivityEvent((datetime.utcnow() - timedelta(hours=0, minutes=5)).isoformat(),  # ty:ignore[deprecated]
                      "user-001", "john.admin", "dba",
                      ActivityCategory.FINANCIAL_ACTION, "adjust_balance",
                      "player_wallets", "Manual credit $500 to player PLR-1234",
                      "10.0.0.5", "London, UK"),
        ActivityEvent(datetime.utcnow().isoformat(), "user-002", "bob.ops", "sre",  # ty:ignore[deprecated]
                      ActivityCategory.SYSTEM_ACCESS, "kubectl exec",
                      "rng-service-pod", "Accessed RNG service container",
                      "10.0.1.10", "Dublin, IE"),
        ActivityEvent((datetime.utcnow().replace(hour=3)).isoformat(),  # ty:ignore[deprecated]
                      "user-001", "john.admin", "dba",
                      ActivityCategory.DATA_EXPORT, "CSV export",
                      "players", "Exported 50,000 player records",
                      "192.168.1.100", "Bucharest, RO"),
    ]

    for event in events:
        alerts = monitor.ingest_event(event)
        if alerts:
            for alert in alerts:
                print(f"  [{alert.severity.value.upper():8s}] {alert.rule_name}")
                print(f"             User: {alert.username} | {alert.description[:80]}")
                print(f"             Action: {alert.recommended_action}")
                print()

    print(f"\n=== Summary ===")
    print(json.dumps(monitor.get_summary(), indent=2, default=str))


def main():
    parser = argparse.ArgumentParser(description="Privileged User Activity Monitor")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--rules", action="store_true", help="List detection rules")
    args = parser.parse_args()

    if args.demo:
        demo()
    elif args.rules:
        print("\nDetection Rules:\n")
        for r in DETECTION_RULES:
            gambling = " [GAMBLING]" if r.gambling_specific else ""
            print(f"  {r.id:10s} [{r.severity.value:8s}] {r.name}{gambling}")
            print(f"             {r.description}")
            print()
    else:
        print("Usage: python privileged_user_monitor.py --demo")
        print("       python privileged_user_monitor.py --rules")


if __name__ == "__main__":
    main()
