#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 09, Legal Framework and Contracts.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
SLA Monitoring System for iGaming Operator Contracts.

Monitors Service Level Agreements across game providers, payment processors,
and platform vendors. Tracks:
  - Uptime/availability (99.9%, 99.95%, 99.99%)
  - API response time (p50, p95, p99)
  - Transaction success rates
  - Game round resolution time
  - Settlement timeliness
  - Support response time (P1-P4)
  - RTP compliance (Return to Player deviation)

Features:
  - Real-time SLA metric collection (simulated for demo)
  - Breach detection and severity classification
  - Automated alerting via webhook/email
  - Penalty calculation based on contract terms
  - Monthly SLA report generation for regulatory filings
  - Historical trend analysis

Usage:
    python sla_monitor.py --demo
    python sla_monitor.py --check --provider megaslots
    python sla_monitor.py --report --period 2026-02 --format json
"""

import argparse
import json
import random
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# SLA definitions
# ---------------------------------------------------------------------------

class SLAMetricType(str, Enum):
    UPTIME = "uptime"
    API_LATENCY_P95 = "api_latency_p95"
    API_LATENCY_P99 = "api_latency_p99"
    TRANSACTION_SUCCESS_RATE = "tx_success_rate"
    GAME_ROUND_RESOLUTION = "game_round_resolution"
    SETTLEMENT_TIMELINESS = "settlement_timeliness"
    SUPPORT_P1_RESPONSE = "support_p1_response"
    SUPPORT_P2_RESPONSE = "support_p2_response"
    RTP_DEVIATION = "rtp_deviation"


class BreachSeverity(str, Enum):
    WARNING = "warning"       # approaching threshold
    MINOR = "minor"           # <1h cumulative breach
    MAJOR = "major"           # 1-4h breach or repeated minor
    CRITICAL = "critical"     # >4h breach, regulatory impact
    SEVERE = "severe"         # service unusable, player impact


@dataclass
class SLAThreshold:
    metric: SLAMetricType
    target: float              # target value
    warning_threshold: float   # trigger warning
    breach_threshold: float    # trigger breach
    unit: str                  # "percent", "ms", "minutes", "hours"
    direction: str = "min"     # "min" (higher is better) or "max" (lower is better)
    penalty_pct_per_breach: float = 0.0  # % of monthly fee per breach


@dataclass
class SLAMeasurement:
    provider_id: str
    metric: SLAMetricType
    value: float
    timestamp: str
    period: str                # YYYY-MM
    jurisdiction: str = ""


@dataclass
class SLABreach:
    breach_id: str
    provider_id: str
    provider_name: str
    metric: SLAMetricType
    target: float
    actual: float
    severity: BreachSeverity
    duration_minutes: float
    detected_at: str
    resolved_at: Optional[str] = None
    penalty_amount: float = 0.0
    penalty_currency: str = "EUR"
    notified: bool = False
    regulatory_reportable: bool = False
    notes: str = ""


# ---------------------------------------------------------------------------
# Provider SLA configurations
# ---------------------------------------------------------------------------

PROVIDER_SLA_CONFIGS = {
    "megaslots": {
        "provider_name": "MegaSlots International",
        "monthly_fee": Decimal("25000"),
        "currency": "EUR",
        "thresholds": [
            SLAThreshold(SLAMetricType.UPTIME, 99.95, 99.90, 99.50,
                          "percent", "min", penalty_pct_per_breach=2.0),
            SLAThreshold(SLAMetricType.API_LATENCY_P95, 200, 250, 500,
                          "ms", "max", penalty_pct_per_breach=1.0),
            SLAThreshold(SLAMetricType.API_LATENCY_P99, 500, 750, 1000,
                          "ms", "max", penalty_pct_per_breach=0.5),
            SLAThreshold(SLAMetricType.TRANSACTION_SUCCESS_RATE, 99.9, 99.5, 99.0,
                          "percent", "min", penalty_pct_per_breach=3.0),
            SLAThreshold(SLAMetricType.GAME_ROUND_RESOLUTION, 30, 45, 120,
                          "seconds", "max", penalty_pct_per_breach=2.0),
            SLAThreshold(SLAMetricType.RTP_DEVIATION, 0.5, 1.0, 2.0,
                          "percent", "max", penalty_pct_per_breach=5.0),
            SLAThreshold(SLAMetricType.SUPPORT_P1_RESPONSE, 15, 20, 30,
                          "minutes", "max", penalty_pct_per_breach=1.5),
        ],
    },
    "paysecure": {
        "provider_name": "PaySecure Solutions",
        "monthly_fee": Decimal("15000"),
        "currency": "EUR",
        "thresholds": [
            SLAThreshold(SLAMetricType.UPTIME, 99.99, 99.95, 99.90,
                          "percent", "min", penalty_pct_per_breach=5.0),
            SLAThreshold(SLAMetricType.API_LATENCY_P95, 100, 150, 300,
                          "ms", "max", penalty_pct_per_breach=2.0),
            SLAThreshold(SLAMetricType.TRANSACTION_SUCCESS_RATE, 99.95, 99.80, 99.50,
                          "percent", "min", penalty_pct_per_breach=5.0),
            SLAThreshold(SLAMetricType.SETTLEMENT_TIMELINESS, 24, 36, 48,
                          "hours", "max", penalty_pct_per_breach=3.0),
        ],
    },
    "livedealer": {
        "provider_name": "LiveDealer Pro",
        "monthly_fee": Decimal("40000"),
        "currency": "EUR",
        "thresholds": [
            SLAThreshold(SLAMetricType.UPTIME, 99.9, 99.8, 99.5,
                          "percent", "min", penalty_pct_per_breach=2.0),
            SLAThreshold(SLAMetricType.API_LATENCY_P95, 300, 400, 800,
                          "ms", "max", penalty_pct_per_breach=1.0),
            SLAThreshold(SLAMetricType.GAME_ROUND_RESOLUTION, 60, 90, 180,
                          "seconds", "max", penalty_pct_per_breach=2.0),
            SLAThreshold(SLAMetricType.RTP_DEVIATION, 1.0, 1.5, 3.0,
                          "percent", "max", penalty_pct_per_breach=5.0),
        ],
    },
}


# ---------------------------------------------------------------------------
# SLA Monitor
# ---------------------------------------------------------------------------

class SLAMonitor:

    def __init__(self, db_path: str = ":memory:"):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_db()
        self.breaches: list[SLABreach] = []
        self.alert_hooks: list = []

    def _init_db(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS sla_measurements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_id TEXT NOT NULL,
                metric TEXT NOT NULL,
                value REAL NOT NULL,
                timestamp TEXT NOT NULL,
                period TEXT NOT NULL,
                jurisdiction TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS sla_breaches (
                breach_id TEXT PRIMARY KEY,
                provider_id TEXT NOT NULL,
                provider_name TEXT NOT NULL,
                metric TEXT NOT NULL,
                target REAL NOT NULL,
                actual REAL NOT NULL,
                severity TEXT NOT NULL,
                duration_minutes REAL DEFAULT 0,
                detected_at TEXT NOT NULL,
                resolved_at TEXT,
                penalty_amount REAL DEFAULT 0,
                penalty_currency TEXT DEFAULT 'EUR',
                notified INTEGER DEFAULT 0,
                regulatory_reportable INTEGER DEFAULT 0,
                notes TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_measurements_provider
                ON sla_measurements(provider_id, metric, period);
            CREATE INDEX IF NOT EXISTS idx_breaches_provider
                ON sla_breaches(provider_id, detected_at);
        """)

    def record_measurement(self, measurement: SLAMeasurement):
        self.conn.execute(
            """INSERT INTO sla_measurements
               (provider_id, metric, value, timestamp, period, jurisdiction)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (measurement.provider_id, measurement.metric,
             measurement.value, measurement.timestamp,
             measurement.period, measurement.jurisdiction)
        )
        self.conn.commit()

    def check_sla(self, provider_id: str, measurement: SLAMeasurement) -> Optional[SLABreach]:
        config = PROVIDER_SLA_CONFIGS.get(provider_id)
        if not config:
            return None

        for threshold in config["thresholds"]:  # ty:ignore[not-iterable]
            if threshold.metric != measurement.metric:  # ty:ignore[possibly-missing-attribute]
                continue

            is_breach = False
            is_warning = False

            if threshold.direction == "min":  # ty:ignore[possibly-missing-attribute]
                is_breach = measurement.value < threshold.breach_threshold  # ty:ignore[possibly-missing-attribute]
                is_warning = measurement.value < threshold.warning_threshold  # ty:ignore[possibly-missing-attribute]
            else:
                is_breach = measurement.value > threshold.breach_threshold  # ty:ignore[possibly-missing-attribute]
                is_warning = measurement.value > threshold.warning_threshold  # ty:ignore[possibly-missing-attribute]

            if is_breach:
                severity = self._classify_severity(
                    threshold, measurement.value, provider_id)  # ty:ignore[invalid-argument-type]
                penalty = self._calculate_penalty(
                    config["monthly_fee"], threshold.penalty_pct_per_breach, severity)  # ty:ignore[invalid-argument-type, possibly-missing-attribute]

                breach = SLABreach(
                    breach_id=f"BRE-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{provider_id[:4].upper()}",
                    provider_id=provider_id,
                    provider_name=config["provider_name"],  # ty:ignore[invalid-argument-type]
                    metric=measurement.metric,
                    target=threshold.target,  # ty:ignore[possibly-missing-attribute]
                    actual=measurement.value,
                    severity=severity,
                    duration_minutes=0,
                    detected_at=measurement.timestamp,
                    penalty_amount=float(penalty),
                    penalty_currency=config["currency"],  # ty:ignore[invalid-argument-type]
                    regulatory_reportable=(severity in [BreachSeverity.CRITICAL, BreachSeverity.SEVERE]),
                )
                self._record_breach(breach)
                self._send_alerts(breach)
                return breach

            elif is_warning:
                print(f"  [WARN] {config['provider_name']} - "
                      f"{measurement.metric}: {measurement.value} "
                      f"(target: {threshold.target}, warning at {threshold.warning_threshold})")  # ty:ignore[possibly-missing-attribute]

        return None

    def _classify_severity(self, threshold: SLAThreshold,
                           actual: float, provider_id: str) -> BreachSeverity:
        if threshold.direction == "min":
            deviation = (threshold.target - actual) / threshold.target
        else:
            deviation = (actual - threshold.target) / threshold.target

        # Count recent breaches for escalation
        recent = self.conn.execute(
            """SELECT COUNT(*) FROM sla_breaches
               WHERE provider_id = ? AND metric = ?
               AND detected_at > datetime('now', '-30 days')""",
            (provider_id, threshold.metric)
        ).fetchone()[0]

        if deviation > 0.5 or recent >= 5:
            return BreachSeverity.SEVERE
        elif deviation > 0.2 or recent >= 3:
            return BreachSeverity.CRITICAL
        elif deviation > 0.1 or recent >= 2:
            return BreachSeverity.MAJOR
        else:
            return BreachSeverity.MINOR

    def _calculate_penalty(self, monthly_fee: Decimal,
                           penalty_pct: float, severity: BreachSeverity) -> Decimal:
        multiplier = {
            BreachSeverity.WARNING: Decimal("0"),
            BreachSeverity.MINOR: Decimal("0.5"),
            BreachSeverity.MAJOR: Decimal("1.0"),
            BreachSeverity.CRITICAL: Decimal("2.0"),
            BreachSeverity.SEVERE: Decimal("3.0"),
        }
        base_penalty = monthly_fee * Decimal(str(penalty_pct / 100))
        return (base_penalty * multiplier[severity]).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP)

    def _record_breach(self, breach: SLABreach):
        self.breaches.append(breach)
        self.conn.execute(
            """INSERT INTO sla_breaches
               (breach_id, provider_id, provider_name, metric, target, actual,
                severity, duration_minutes, detected_at, penalty_amount,
                penalty_currency, regulatory_reportable, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (breach.breach_id, breach.provider_id, breach.provider_name,
             breach.metric, breach.target, breach.actual,
             breach.severity, breach.duration_minutes, breach.detected_at,
             breach.penalty_amount, breach.penalty_currency,
             int(breach.regulatory_reportable), breach.notes)
        )
        self.conn.commit()

    def _send_alerts(self, breach: SLABreach):
        """Send alerts based on severity. In production: webhook, PagerDuty, email."""
        severity_icon = {
            BreachSeverity.MINOR: "[!]",
            BreachSeverity.MAJOR: "[!!]",
            BreachSeverity.CRITICAL: "[!!!]",
            BreachSeverity.SEVERE: "[!!!!]",
        }
        icon = severity_icon.get(breach.severity, "[?]")
        print(f"  {icon} BREACH: {breach.provider_name} - {breach.metric} = "
              f"{breach.actual} (target: {breach.target}) | "
              f"Severity: {breach.severity.value} | "
              f"Penalty: {breach.penalty_currency} {breach.penalty_amount:.2f}")
        if breach.regulatory_reportable:
            print(f"       >> REGULATORY REPORTABLE - escalate to compliance team")

        # Webhook payload for alerting systems
        webhook_payload = {
            "breach_id": breach.breach_id,
            "provider": breach.provider_name,
            "metric": breach.metric,
            "severity": breach.severity.value,
            "actual": breach.actual,
            "target": breach.target,
            "penalty": breach.penalty_amount,
            "regulatory_reportable": breach.regulatory_reportable,
            "timestamp": breach.detected_at,
        }
        # In production: requests.post(webhook_url, json=webhook_payload)
        for hook in self.alert_hooks:
            hook(webhook_payload)

    def monthly_report(self, period: str) -> dict:
        """Generate monthly SLA compliance report."""
        providers_out: dict[str, dict] = {}

        for pid, config in PROVIDER_SLA_CONFIGS.items():
            metrics_out: dict = {}
            breaches_out: list[dict] = []
            total_penalties = 0.0

            for threshold in config["thresholds"]:  # ty:ignore[not-iterable]
                rows = self.conn.execute(
                    """SELECT AVG(value) as avg_val, MIN(value) as min_val,
                              MAX(value) as max_val, COUNT(*) as samples
                       FROM sla_measurements
                       WHERE provider_id = ? AND metric = ? AND period = ?""",
                    (pid, threshold.metric, period)  # ty:ignore[possibly-missing-attribute]
                ).fetchone()

                met_target = True
                if rows["avg_val"] is not None:
                    if threshold.direction == "min":  # ty:ignore[possibly-missing-attribute]
                        met_target = rows["avg_val"] >= threshold.target  # ty:ignore[possibly-missing-attribute]
                    else:
                        met_target = rows["avg_val"] <= threshold.target  # ty:ignore[possibly-missing-attribute]

                metrics_out[threshold.metric] = {  # ty:ignore[possibly-missing-attribute]
                    "target": threshold.target,  # ty:ignore[possibly-missing-attribute]
                    "avg": round(rows["avg_val"] or 0, 3),
                    "min": round(rows["min_val"] or 0, 3),
                    "max": round(rows["max_val"] or 0, 3),
                    "samples": rows["samples"],
                    "unit": threshold.unit,  # ty:ignore[possibly-missing-attribute]
                    "met_target": met_target,
                }

            # Breaches for this provider in this period
            breach_rows = self.conn.execute(
                """SELECT * FROM sla_breaches
                   WHERE provider_id = ? AND detected_at LIKE ?
                   ORDER BY detected_at""",
                (pid, f"{period}%")
            ).fetchall()

            for br in breach_rows:
                breaches_out.append({
                    "breach_id": br["breach_id"],
                    "metric": br["metric"],
                    "severity": br["severity"],
                    "actual": br["actual"],
                    "target": br["target"],
                    "penalty": br["penalty_amount"],
                    "regulatory_reportable": bool(br["regulatory_reportable"]),
                })
                total_penalties += br["penalty_amount"]

            providers_out[pid] = {
                "provider_name": config["provider_name"],
                "metrics": metrics_out,
                "breaches": breaches_out,
                "total_penalties": total_penalties,
            }

        return {"period": period, "providers": providers_out}


# ---------------------------------------------------------------------------
# Simulation & Demo
# ---------------------------------------------------------------------------

def simulate_measurements(monitor: SLAMonitor, period: str = "2026-02"):
    """Generate realistic SLA measurements with occasional breaches."""
    providers = {
        "megaslots": {
            SLAMetricType.UPTIME: (99.97, 0.1),           # mean, stddev
            SLAMetricType.API_LATENCY_P95: (180, 40),
            SLAMetricType.API_LATENCY_P99: (400, 100),
            SLAMetricType.TRANSACTION_SUCCESS_RATE: (99.92, 0.15),
            SLAMetricType.GAME_ROUND_RESOLUTION: (25, 8),
            SLAMetricType.RTP_DEVIATION: (0.3, 0.2),
            SLAMetricType.SUPPORT_P1_RESPONSE: (12, 5),
        },
        "paysecure": {
            SLAMetricType.UPTIME: (99.995, 0.02),
            SLAMetricType.API_LATENCY_P95: (85, 25),
            SLAMetricType.TRANSACTION_SUCCESS_RATE: (99.96, 0.08),
            SLAMetricType.SETTLEMENT_TIMELINESS: (18, 8),
        },
        "livedealer": {
            SLAMetricType.UPTIME: (99.85, 0.15),          # slightly worse
            SLAMetricType.API_LATENCY_P95: (280, 80),
            SLAMetricType.GAME_ROUND_RESOLUTION: (55, 20),
            SLAMetricType.RTP_DEVIATION: (0.8, 0.5),
        },
    }

    print("=" * 80)
    print("SLA MONITOR - SIMULATED MEASUREMENT INGESTION")
    print(f"Period: {period}")
    print("=" * 80)

    base_date = datetime.strptime(f"{period}-01", "%Y-%m-%d")
    total_measurements = 0
    total_breaches = 0

    for pid, metrics in providers.items():
        print(f"\n--- {PROVIDER_SLA_CONFIGS[pid]['provider_name']} ---")
        for metric, (mean, stddev) in metrics.items():
            # Simulate 720 hourly measurements per month
            for hour in range(720):
                ts = (base_date + timedelta(hours=hour)).isoformat()
                value = max(0, random.gauss(mean, stddev))

                # Inject occasional spikes
                if random.random() < 0.005:  # 0.5% chance of anomaly
                    if metric in [SLAMetricType.API_LATENCY_P95,
                                  SLAMetricType.API_LATENCY_P99,
                                  SLAMetricType.GAME_ROUND_RESOLUTION,
                                  SLAMetricType.SUPPORT_P1_RESPONSE,
                                  SLAMetricType.SETTLEMENT_TIMELINESS]:
                        value *= random.uniform(2.0, 5.0)
                    elif metric in [SLAMetricType.UPTIME,
                                    SLAMetricType.TRANSACTION_SUCCESS_RATE]:
                        value -= random.uniform(0.5, 2.0)

                measurement = SLAMeasurement(
                    provider_id=pid,
                    metric=metric,
                    value=round(value, 3),
                    timestamp=ts,
                    period=period,
                )
                monitor.record_measurement(measurement)
                breach = monitor.check_sla(pid, measurement)
                if breach:
                    total_breaches += 1
                total_measurements += 1

    print(f"\n[i] Recorded {total_measurements} measurements, "
          f"detected {total_breaches} breaches")
    return total_measurements, total_breaches


def demo():
    monitor = SLAMonitor(":memory:")
    period = "2026-02"

    # Run simulation
    measurements, breaches = simulate_measurements(monitor, period)

    # Generate report
    report = monitor.monthly_report(period)

    print("\n" + "=" * 80)
    print("MONTHLY SLA COMPLIANCE REPORT")
    print(f"Period: {period}")
    print("=" * 80)

    for pid, pdata in report["providers"].items():
        print(f"\n{'─'*60}")
        print(f"Provider: {pdata['provider_name']}")
        print(f"{'─'*60}")
        print(f"  {'Metric':<30} {'Target':>8} {'Avg':>8} {'Min':>8} {'Max':>8} {'OK?':>5}")
        print(f"  {'-'*75}")
        for metric, mdata in pdata["metrics"].items():
            ok = "PASS" if mdata["met_target"] else "FAIL"
            print(f"  {metric:<30} {mdata['target']:>8.2f} {mdata['avg']:>8.2f} "
                  f"{mdata['min']:>8.2f} {mdata['max']:>8.2f} {ok:>5}")
        print(f"\n  Breaches: {len(pdata['breaches'])}")
        print(f"  Total Penalties: EUR {pdata['total_penalties']:,.2f}")

        if pdata["breaches"]:
            regulatory = [b for b in pdata["breaches"] if b["regulatory_reportable"]]
            if regulatory:
                print(f"  REGULATORY REPORTABLE: {len(regulatory)} breach(es)")

    print(f"\n{'='*80}")
    print("Report generated for regulatory filing (UKGC/MGA annual review)")
    print(f"{'='*80}")
    print("\n[OK] SLA monitoring demo complete.")


def main():
    parser = argparse.ArgumentParser(description="SLA Monitor for iGaming")
    parser.add_argument("--demo", action="store_true", default=True)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--provider", default=None)
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--period", default="2026-02")
    parser.add_argument("--format", choices=["json", "text"], default="text")
    args = parser.parse_args()
    demo()


if __name__ == "__main__":
    main()
