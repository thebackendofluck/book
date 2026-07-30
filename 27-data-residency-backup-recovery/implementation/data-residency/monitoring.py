#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Data Residency Monitoring and Alerting
=======================================
Continuously monitors data storage locations against jurisdiction policies.
Detects violations, generates alerts, and produces compliance dashboards.

Usage:
    python monitoring.py --demo
    python monitoring.py --check-all
    python monitoring.py --dashboard
"""

import json
import logging
import argparse
import random
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Any
from enum import Enum
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("residency-monitor")


# ---------------------------------------------------------------------------
# Alert levels
# ---------------------------------------------------------------------------
class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"  # regulator notification required


class ComplianceStatus(str, Enum):
    COMPLIANT = "compliant"
    WARNING = "warning"
    VIOLATION = "violation"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------
@dataclass
class DataAsset:
    asset_id: str
    name: str
    jurisdiction: str
    data_type: str
    current_region: str
    expected_regions: list[str]
    encrypted: bool
    last_verified: str
    size_gb: float


@dataclass
class Alert:
    alert_id: str
    severity: AlertSeverity
    jurisdiction: str
    asset_id: str
    message: str
    details: dict
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    acknowledged: bool = False
    resolved: bool = False


@dataclass
class ComplianceCheck:
    check_id: str
    jurisdiction: str
    check_type: str
    status: ComplianceStatus
    details: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ---------------------------------------------------------------------------
# Jurisdiction monitoring rules
# ---------------------------------------------------------------------------
MONITORING_RULES = {
    "UK": {
        "approved_regions": ["eu-west-2", "uk-dc-london-1", "uk-dc-london-2"],
        "max_replication_lag_seconds": 300,
        "encryption_algorithm": "AES-256-GCM",
        "compliance_check_interval_minutes": 60,
        "breach_notification_hours": 72,
        "regulator": "ICO",
        "alert_channels": ["pagerduty", "email_dpo", "slack_compliance"],
    },
    "MT": {
        "approved_regions": [
            "eu-central-1", "eu-west-1", "eu-south-1", "eu-north-1",
        ],
        "max_replication_lag_seconds": 600,
        "encryption_algorithm": "AES-256-GCM",
        "compliance_check_interval_minutes": 60,
        "breach_notification_hours": 72,
        "regulator": "IDPC Malta",
        "alert_channels": ["pagerduty", "email_dpo", "slack_compliance"],
    },
    "DE": {
        "approved_regions": ["eu-central-1", "de-dc-frankfurt-1", "de-dc-frankfurt-2"],
        "max_replication_lag_seconds": 120,  # Germany is stricter
        "encryption_algorithm": "AES-256-GCM",
        "compliance_check_interval_minutes": 30,
        "breach_notification_hours": 72,
        "regulator": "BfDI",
        "alert_channels": ["pagerduty", "email_dpo", "slack_compliance", "sms_cto"],
    },
    "ON": {
        "approved_regions": ["ca-central-1", "ca-dc-toronto-1", "ca-dc-montreal-1"],
        "max_replication_lag_seconds": 600,
        "encryption_algorithm": "AES-256-GCM",
        "compliance_check_interval_minutes": 120,
        "breach_notification_hours": 72,
        "regulator": "OPC Canada",
        "alert_channels": ["pagerduty", "email_dpo"],
    },
}


# ---------------------------------------------------------------------------
# Alert dispatcher
# ---------------------------------------------------------------------------
class AlertDispatcher:
    """
    Dispatches alerts to configured channels. In production, integrates
    with PagerDuty, Slack, email, and SMS providers.
    """

    def __init__(self):
        self._sent_alerts: list[Alert] = []

    def dispatch(self, alert: Alert, channels: list[str]):
        """Send alert to all configured channels."""
        for channel in channels:
            self._send_to_channel(alert, channel)
        self._sent_alerts.append(alert)

    def _send_to_channel(self, alert: Alert, channel: str):
        """Simulate sending to a channel. Replace with real integrations."""
        if channel == "pagerduty":
            logger.info(
                "[PagerDuty] %s: %s (asset=%s)",
                alert.severity.value.upper(),
                alert.message,
                alert.asset_id,
            )
        elif channel.startswith("email_"):
            recipient = channel.replace("email_", "")
            logger.info(
                "[Email->%s] %s: %s",
                recipient, alert.severity.value.upper(), alert.message,
            )
        elif channel.startswith("slack_"):
            slack_channel = channel.replace("slack_", "#")
            logger.info(
                "[Slack %s] %s: %s",
                slack_channel, alert.severity.value.upper(), alert.message,
            )
        elif channel.startswith("sms_"):
            recipient = channel.replace("sms_", "")
            logger.info(
                "[SMS->%s] %s: %s",
                recipient, alert.severity.value.upper(), alert.message,
            )

    def get_alert_history(self) -> list[Alert]:
        return list(self._sent_alerts)


# ---------------------------------------------------------------------------
# Residency monitor
# ---------------------------------------------------------------------------
class ResidencyMonitor:
    """
    Monitors data assets for residency compliance. Runs checks against
    jurisdiction rules and generates alerts for violations.
    """

    def __init__(self):
        self.rules = MONITORING_RULES
        self.dispatcher = AlertDispatcher()
        self.checks: list[ComplianceCheck] = []
        self._alert_counter = 0

    def check_asset(self, asset: DataAsset) -> ComplianceCheck:
        """Run all compliance checks on a single data asset."""
        jurisdiction = asset.jurisdiction
        if jurisdiction not in self.rules:
            return self._record_check(
                jurisdiction,
                "jurisdiction_validation",
                ComplianceStatus.UNKNOWN,
                f"No rules configured for jurisdiction: {jurisdiction}",
            )

        rules = self.rules[jurisdiction]
        violations = []

        # 1. Region check
        if asset.current_region not in rules["approved_regions"]:  # ty:ignore[unsupported-operator]
            violations.append(
                f"Asset '{asset.name}' is in region '{asset.current_region}' "
                f"which is NOT in approved regions: {rules['approved_regions']}"
            )
            self._raise_alert(
                AlertSeverity.CRITICAL,
                jurisdiction,
                asset.asset_id,
                f"DATA RESIDENCY VIOLATION: {asset.name} in unauthorized "
                f"region {asset.current_region}",
                {
                    "current_region": asset.current_region,
                    "approved_regions": rules["approved_regions"],
                    "data_type": asset.data_type,
                },
            )

        # 2. Encryption check
        if not asset.encrypted:
            violations.append(
                f"Asset '{asset.name}' is NOT encrypted. "
                f"{rules['encryption_algorithm']} required."
            )
            self._raise_alert(
                AlertSeverity.CRITICAL,
                jurisdiction,
                asset.asset_id,
                f"ENCRYPTION VIOLATION: {asset.name} is unencrypted",
                {"required_algorithm": rules["encryption_algorithm"]},
            )

        # 3. Verification freshness
        last_verified = datetime.fromisoformat(asset.last_verified)
        interval = timedelta(minutes=rules["compliance_check_interval_minutes"])  # ty:ignore[invalid-argument-type]
        if datetime.now(timezone.utc) - last_verified > interval:
            violations.append(
                f"Asset '{asset.name}' last verified "
                f"{asset.last_verified}, exceeds {interval}"
            )
            self._raise_alert(
                AlertSeverity.WARNING,
                jurisdiction,
                asset.asset_id,
                f"Stale verification: {asset.name} not checked within "
                f"{rules['compliance_check_interval_minutes']} minutes",
                {"last_verified": asset.last_verified},
            )

        # 4. Expected region match
        if asset.current_region not in asset.expected_regions:
            violations.append(
                f"Asset '{asset.name}' region mismatch: "
                f"current={asset.current_region}, "
                f"expected={asset.expected_regions}"
            )
            self._raise_alert(
                AlertSeverity.WARNING,
                jurisdiction,
                asset.asset_id,
                f"Region mismatch for {asset.name}",
                {
                    "current": asset.current_region,
                    "expected": asset.expected_regions,
                },
            )

        if violations:
            status = ComplianceStatus.VIOLATION
            details = "; ".join(violations)
        else:
            status = ComplianceStatus.COMPLIANT
            details = f"All checks passed for {asset.name}"

        return self._record_check(
            jurisdiction, "full_asset_check", status, details
        )

    def check_all_assets(
        self, assets: list[DataAsset]
    ) -> dict[str, list[ComplianceCheck]]:
        """Check all assets grouped by jurisdiction."""
        results: dict[str, list[ComplianceCheck]] = {}
        for asset in assets:
            check = self.check_asset(asset)
            results.setdefault(asset.jurisdiction, []).append(check)
        return results

    def generate_dashboard(
        self, assets: list[DataAsset]
    ) -> dict:
        """Generate a compliance dashboard summary."""
        results = self.check_all_assets(assets)

        dashboard: dict[str, Any] = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "jurisdictions": {},
            "overall_status": ComplianceStatus.COMPLIANT.value,
            "total_assets": len(assets),
            "total_violations": 0,
            "total_warnings": 0,
            "alerts_triggered": len(self.dispatcher.get_alert_history()),
        }

        overall_compliant = True
        for jur, checks in results.items():
            violations = [
                c for c in checks if c.status == ComplianceStatus.VIOLATION
            ]
            warnings = [
                c for c in checks if c.status == ComplianceStatus.WARNING
            ]
            compliant = [
                c for c in checks if c.status == ComplianceStatus.COMPLIANT
            ]

            jur_status = ComplianceStatus.COMPLIANT
            if violations:
                jur_status = ComplianceStatus.VIOLATION
                overall_compliant = False
            elif warnings:
                jur_status = ComplianceStatus.WARNING

            dashboard["jurisdictions"][jur] = {
                "status": jur_status.value,
                "total_assets": len(checks),
                "compliant": len(compliant),
                "warnings": len(warnings),
                "violations": len(violations),
                "regulator": self.rules.get(jur, {}).get("regulator", "unknown"),
                "violation_details": [
                    {"check_id": v.check_id, "details": v.details}
                    for v in violations
                ],
            }
            dashboard["total_violations"] += len(violations)
            dashboard["total_warnings"] += len(warnings)

        if not overall_compliant:
            dashboard["overall_status"] = ComplianceStatus.VIOLATION.value
        elif dashboard["total_warnings"] > 0:
            dashboard["overall_status"] = ComplianceStatus.WARNING.value

        return dashboard

    def _raise_alert(
        self,
        severity: AlertSeverity,
        jurisdiction: str,
        asset_id: str,
        message: str,
        details: dict,
    ):
        self._alert_counter += 1
        alert = Alert(
            alert_id=f"ALERT-{self._alert_counter:06d}",
            severity=severity,
            jurisdiction=jurisdiction,
            asset_id=asset_id,
            message=message,
            details=details,
        )
        channels = self.rules.get(jurisdiction, {}).get(
            "alert_channels", ["pagerduty"]
        )
        self.dispatcher.dispatch(alert, channels)  # ty:ignore[invalid-argument-type]

    def _record_check(
        self,
        jurisdiction: str,
        check_type: str,
        status: ComplianceStatus,
        details: str,
    ) -> ComplianceCheck:
        check = ComplianceCheck(
            check_id=f"CHK-{len(self.checks)+1:06d}",
            jurisdiction=jurisdiction,
            check_type=check_type,
            status=status,
            details=details,
        )
        self.checks.append(check)
        return check


# ---------------------------------------------------------------------------
# Example assets for demonstration
# ---------------------------------------------------------------------------
def generate_example_assets() -> list[DataAsset]:
    """Generate realistic iGaming data assets for testing."""
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(minutes=15)).isoformat()
    stale = (now - timedelta(hours=3)).isoformat()

    return [
        # UK -- compliant assets
        DataAsset("uk-db-001", "UK Player Database", "UK", "player_pii",
                  "eu-west-2", ["eu-west-2"], True, recent, 250.0),
        DataAsset("uk-tx-001", "UK Transaction Store", "UK", "financial",
                  "eu-west-2", ["eu-west-2"], True, recent, 180.0),
        # UK -- violation: wrong region
        DataAsset("uk-bk-001", "UK Backup (WRONG)", "UK", "player_pii",
                  "us-east-1", ["eu-west-2"], True, recent, 250.0),

        # Malta -- compliant
        DataAsset("mt-db-001", "Malta Player DB", "MT", "player_pii",
                  "eu-central-1", ["eu-central-1", "eu-west-1"], True, recent, 120.0),
        DataAsset("mt-gm-001", "Malta Game History", "MT", "gaming_activity",
                  "eu-west-1", ["eu-central-1", "eu-west-1"], True, recent, 500.0),

        # Germany -- compliant
        DataAsset("de-db-001", "Germany Player DB", "DE", "player_pii",
                  "eu-central-1", ["eu-central-1"], True, recent, 80.0),
        # Germany -- violation: unencrypted
        DataAsset("de-an-001", "Germany Analytics (UNENCRYPTED)", "DE", "analytics",
                  "eu-central-1", ["eu-central-1"], False, recent, 45.0),
        # Germany -- warning: stale verification
        DataAsset("de-bk-001", "Germany Backup", "DE", "financial",
                  "eu-central-1", ["eu-central-1"], True, stale, 80.0),

        # Ontario -- compliant
        DataAsset("on-db-001", "Ontario Player DB", "ON", "player_pii",
                  "ca-central-1", ["ca-central-1"], True, recent, 60.0),
        DataAsset("on-tx-001", "Ontario Transactions", "ON", "financial",
                  "ca-central-1", ["ca-central-1"], True, recent, 40.0),
    ]


# ---------------------------------------------------------------------------
# Demo and CLI
# ---------------------------------------------------------------------------
def run_demo():
    monitor = ResidencyMonitor()
    assets = generate_example_assets()

    print("=" * 80)
    print("DATA RESIDENCY MONITORING - COMPLIANCE SCAN")
    print("=" * 80)

    dashboard = monitor.generate_dashboard(assets)
    print(json.dumps(dashboard, indent=2))

    print("\n" + "=" * 80)
    print("ALERT HISTORY")
    print("=" * 80)
    for alert in monitor.dispatcher.get_alert_history():
        print(f"\n  [{alert.severity.value.upper()}] {alert.alert_id}")
        print(f"  Jurisdiction: {alert.jurisdiction}")
        print(f"  Asset:        {alert.asset_id}")
        print(f"  Message:      {alert.message}")
        print(f"  Time:         {alert.timestamp}")


def main():
    parser = argparse.ArgumentParser(
        description="Data Residency Monitoring and Alerting"
    )
    parser.add_argument("--demo", action="store_true", help="Run demo scan")
    parser.add_argument(
        "--check-all", action="store_true",
        help="Check all example assets",
    )
    parser.add_argument(
        "--dashboard", action="store_true",
        help="Generate compliance dashboard JSON",
    )

    args = parser.parse_args()

    if args.demo:
        run_demo()
    elif args.check_all or args.dashboard:
        monitor = ResidencyMonitor()
        assets = generate_example_assets()
        dashboard = monitor.generate_dashboard(assets)
        print(json.dumps(dashboard, indent=2))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
