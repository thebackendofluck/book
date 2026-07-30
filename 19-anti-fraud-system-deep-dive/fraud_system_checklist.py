#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 19, Anti-Fraud System Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Chapter 19 — Anti-Fraud System Implementation Checklist
========================================================
Automated readiness checker for the anti-fraud and AML detection pipeline.
Validates Elasticsearch cluster health, Kibana dashboards, fraud detection
pipeline, alert types, risk scoring model, COAF reporting, and dashboard panels.

Usage:
    python fraud_system_checklist.py [--env staging|production] [--host HOST]
    python fraud_system_checklist.py --report-only
    python fraud_system_checklist.py --json report.json
"""

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
import urllib.request
import urllib.error


class Status(Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    SKIP = "SKIP"


@dataclass
class CheckResult:
    name: str
    category: str
    status: Status
    detail: str
    requirement: str
    cost_usd: Optional[float] = None


@dataclass
class ChecklistReport:
    timestamp: str = ""
    environment: str = "staging"
    results: list = field(default_factory=list)
    total: int = 0
    passed: int = 0
    failed: int = 0
    warnings: int = 0
    skipped: int = 0

    def add(self, result: CheckResult) -> None:
        self.results.append(result)
        self.total += 1
        if result.status == Status.PASS:
            self.passed += 1
        elif result.status == Status.FAIL:
            self.failed += 1
        elif result.status == Status.WARN:
            self.warnings += 1
        else:
            self.skipped += 1


# ---------------------------------------------------------------------------
# Check functions
# ---------------------------------------------------------------------------

def http_get(url: str, timeout: int = 5):
    """Perform a simple HTTP GET and return (status_code, body_str)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, str(e)
    except Exception as e:
        return None, str(e)


def check_elasticsearch(report: ChecklistReport, host: str) -> None:
    """Elasticsearch cluster health checks."""

    es_base = f"http://{host}:9200"

    # Cluster health
    code, body = http_get(f"{es_base}/_cluster/health")
    if code == 200:
        try:
            data = json.loads(body)
            color = data.get("status", "unknown")
            if color == "green":
                status, detail = Status.PASS, f"Cluster status: green — {data.get('number_of_nodes', '?')} nodes"
            elif color == "yellow":
                status, detail = Status.WARN, f"Cluster status: yellow — check unassigned shards"
            else:
                status, detail = Status.FAIL, f"Cluster status: {color}"
        except json.JSONDecodeError:
            status, detail = Status.WARN, "Response not valid JSON"
    else:
        status, detail = Status.FAIL, f"Cannot reach Elasticsearch at {es_base} — {body[:100]}"

    report.add(CheckResult(
        name="Elasticsearch Cluster Health",
        category="Elasticsearch",
        status=status,
        detail=detail,
        requirement="Chapter 19 — Fraud detection data store",
    ))

    # Required indices
    fraud_indices = [
        "fraud-events",
        "risk-scores",
        "player-sessions",
        "transaction-logs",
        "alert-history",
    ]
    for idx in fraud_indices:
        code, body = http_get(f"{es_base}/{idx}/_count")
        if code == 200:
            try:
                count = json.loads(body).get("count", 0)
                st = Status.PASS if count >= 0 else Status.WARN
                det = f"Index exists — {count} documents"
            except Exception:
                st, det = Status.WARN, "Index exists but count unreadable"
        elif code == 404:
            st, det = Status.WARN, f"Index '{idx}' not yet created"
        else:
            st, det = Status.FAIL, f"Cannot query index '{idx}' — HTTP {code}"

        report.add(CheckResult(
            name=f"Elasticsearch Index: {idx}",
            category="Elasticsearch",
            status=st,
            detail=det,
            requirement="Chapter 19 — Fraud data schema",
        ))

    # Node count
    code, body = http_get(f"{es_base}/_nodes/stats/os")
    if code == 200:
        try:
            nodes = json.loads(body).get("nodes", {})
            count = len(nodes)
            st = Status.PASS if count >= 3 else Status.WARN
            det = f"{count} Elasticsearch node(s) detected (3+ recommended for HA)"
        except Exception:
            st, det = Status.WARN, "Could not parse node stats"
    else:
        st, det = Status.FAIL, f"Node stats unreachable — HTTP {code}"

    report.add(CheckResult(
        name="Elasticsearch Node Count (3+ for HA)",
        category="Elasticsearch",
        status=st,
        detail=det,
        requirement="Chapter 19 — High-availability cluster",
    ))


def check_kibana(report: ChecklistReport, host: str) -> None:
    """Kibana dashboard accessibility checks."""

    kibana_base = f"http://{host}:5601"

    code, body = http_get(f"{kibana_base}/api/status")
    if code == 200:
        try:
            data = json.loads(body)
            overall = data.get("status", {}).get("overall", {}).get("level", "unknown")
            st = Status.PASS if overall == "available" else Status.WARN
            det = f"Kibana status: {overall}"
        except Exception:
            st, det = Status.WARN, "Kibana reachable but status unreadable"
    else:
        st, det = Status.FAIL, f"Kibana not reachable at {kibana_base} — HTTP {code}"

    report.add(CheckResult(
        name="Kibana Dashboard Accessible",
        category="Kibana",
        status=st,
        detail=det,
        requirement="Chapter 19 — Fraud monitoring dashboards",
    ))

    # Expected dashboard panels
    panels = [
        "Fraud Events Overview",
        "Risk Score Distribution",
        "Alert Type Breakdown",
        "Velocity Abuse Timeline",
        "Geographic Anomaly Map",
        "Player Session Heatmap",
    ]
    for panel in panels:
        report.add(CheckResult(
            name=f"Dashboard Panel: {panel}",
            category="Kibana",
            status=Status.WARN,
            detail=f"Verify panel '{panel}' renders correctly with live data",
            requirement="Chapter 19 — Fraud monitoring UI",
        ))


def check_fraud_pipeline(report: ChecklistReport, host: str, report_only: bool) -> None:
    """Fraud detection pipeline checks."""

    pipeline_base = f"http://{host}:8080"

    if not report_only:
        code, body = http_get(f"{pipeline_base}/health")
        if code == 200:
            st, det = Status.PASS, f"Pipeline health endpoint OK — {body[:80]}"
        else:
            st, det = Status.FAIL, f"Pipeline not reachable — HTTP {code}: {body[:80]}"
    else:
        st, det = Status.SKIP, "Skipped (--report-only mode)"

    report.add(CheckResult(
        name="Fraud Detection Pipeline Running",
        category="Pipeline",
        status=st,
        detail=det,
        requirement="Chapter 19 — Real-time event processing",
    ))

    # Alert types
    alert_types = [
        ("velocity_abuse", "Detects abnormal transaction frequency per player/IP"),
        ("bonus_abuse", "Identifies players exploiting bonus structures"),
        ("multi_accounting", "Flags multiple accounts from same device/IP/CPF"),
        ("payment_fraud", "Detects stolen cards and unauthorized payment methods"),
        ("collusion", "Graph-based detection of coordinated player behaviour"),
        ("gnb_money_laundering", "Structured transactions to evade reporting thresholds"),
        ("account_takeover", "Login anomalies suggesting credential compromise"),
        ("chargeback_fraud", "Patterns consistent with friendly fraud / chargeback abuse"),
    ]
    for alert_type, description in alert_types:
        report.add(CheckResult(
            name=f"Alert Type: {alert_type}",
            category="Pipeline/Alerts",
            status=Status.WARN,
            detail=description,
            requirement="Chapter 19 — Fraud rule engine",
        ))

    # Processing latency
    report.add(CheckResult(
        name="Event Processing Latency < 500ms",
        category="Pipeline",
        status=Status.WARN,
        detail="Real-time fraud checks must complete within 500ms to avoid blocking transactions",
        requirement="Chapter 19 — Latency SLA",
    ))

    # Kafka connectivity
    report.add(CheckResult(
        name="Kafka Topic: fraud-events-raw",
        category="Pipeline",
        status=Status.WARN,
        detail="Verify Kafka topic exists and pipeline consumer is subscribed",
        requirement="Chapter 19 — Event ingestion",
    ))

    report.add(CheckResult(
        name="Kafka Topic: fraud-decisions",
        category="Pipeline",
        status=Status.WARN,
        detail="Verify decision output topic exists and downstream consumers are connected",
        requirement="Chapter 19 — Decision publishing",
    ))


def check_risk_scoring(report: ChecklistReport, host: str) -> None:
    """Risk scoring model checks."""

    scoring_base = f"http://{host}:8081"

    report.add(CheckResult(
        name="Risk Scoring Model Loaded",
        category="Risk Scoring",
        status=Status.WARN,
        detail="Verify ML model (XGBoost/RandomForest) is loaded and returning scores 0-100",
        requirement="Chapter 19 — ML risk model",
    ))

    report.add(CheckResult(
        name="Model Version Tracked",
        category="Risk Scoring",
        status=Status.WARN,
        detail="MLflow or equivalent model registry must track model version, training date, and AUC score",
        requirement="Chapter 19 — Model governance",
    ))

    report.add(CheckResult(
        name="Feature Store Connected",
        category="Risk Scoring",
        status=Status.WARN,
        detail="Real-time feature retrieval from Redis/Feast must be available for scoring",
        requirement="Chapter 19 — Feature engineering",
    ))

    report.add(CheckResult(
        name="Model AUC > 0.85",
        category="Risk Scoring",
        status=Status.WARN,
        detail="Fraud model must achieve minimum AUC of 0.85 on validation dataset",
        requirement="Chapter 19 — Model quality threshold",
    ))

    report.add(CheckResult(
        name="False Positive Rate < 2%",
        category="Risk Scoring",
        status=Status.WARN,
        detail="FPR must remain below 2% to avoid blocking legitimate players",
        requirement="Chapter 19 — Model calibration",
    ))

    report.add(CheckResult(
        name="Score Threshold Configured (default: 75)",
        category="Risk Scoring",
        status=Status.WARN,
        detail="Scores above threshold trigger automatic review queue; above 90 trigger block",
        requirement="Chapter 19 — Risk threshold policy",
    ))


def check_coaf_reporting(report: ChecklistReport, host: str) -> None:
    """COAF AML reporting endpoint checks."""

    coaf_base = f"http://{host}:8082"

    report.add(CheckResult(
        name="COAF Reporting Endpoint Available",
        category="COAF/AML",
        status=Status.WARN,
        detail=f"POST {coaf_base}/api/v1/coaf/report — SAR submission endpoint must be reachable",
        requirement="Chapter 19 — Lei 9.613/1998 AML reporting",
    ))

    report.add(CheckResult(
        name="SAR Auto-Generation for Transactions > R$10,000",
        category="COAF/AML",
        status=Status.WARN,
        detail="Suspicious Activity Reports must be auto-generated for cash transactions above R$10,000",
        requirement="Chapter 19 — Portaria SPA/MF 1143/2024",
    ))

    report.add(CheckResult(
        name="COAF Report Retention (5 years)",
        category="COAF/AML",
        status=Status.WARN,
        detail="All submitted SAR records must be retained for 5 years with immutable audit trail",
        requirement="Chapter 19 — Lei 9.613/1998 Art. 11",
    ))

    report.add(CheckResult(
        name="Structured Transaction Detection (Smurfing)",
        category="COAF/AML",
        status=Status.WARN,
        detail="Detect intentional splitting of large transactions to evade R$10,000 threshold",
        requirement="Chapter 19 — AML pattern detection",
    ))

    report.add(CheckResult(
        name="PEP (Politically Exposed Person) Screening",
        category="COAF/AML",
        status=Status.WARN,
        detail="Automated PEP screening at onboarding and ongoing monthly re-check",
        requirement="Chapter 19 — COAF Resolution 36/2021",
    ))


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def print_report(report: ChecklistReport) -> None:
    print()
    print("=" * 70)
    print("  CHAPTER 19 — ANTI-FRAUD SYSTEM IMPLEMENTATION CHECKLIST")
    print(f"  Environment: {report.environment}")
    print(f"  Generated:   {report.timestamp}")
    print("=" * 70)

    current_category = ""
    for r in report.results:
        if r.category != current_category:
            current_category = r.category
            print(f"\n  {'─' * 64}")
            print(f"  {current_category.upper()}")
            print(f"  {'─' * 64}")

        icon = {
            Status.PASS: "[PASS]",
            Status.FAIL: "[FAIL]",
            Status.WARN: "[WARN]",
            Status.SKIP: "[SKIP]",
        }[r.status]

        print(f"  {icon} {r.name}")
        print(f"         {r.detail}")
        print(f"         Ref: {r.requirement}")

    print(f"\n  {'=' * 64}")
    print(f"  SUMMARY")
    print(f"  {'=' * 64}")
    print(f"  Total checks:  {report.total}")
    print(f"  Passed:        {report.passed}")
    print(f"  Failed:        {report.failed}")
    print(f"  Warnings:      {report.warnings}")
    print(f"  Skipped:       {report.skipped}")

    readiness = report.passed / report.total * 100 if report.total else 0
    print(f"\n  Implementation readiness: {readiness:.0f}%")

    if report.failed > 0:
        print(f"\n  NOT READY — {report.failed} critical checks failed")
    elif report.warnings > 5:
        print(f"\n  REVIEW NEEDED — {report.warnings} items require manual verification")
    else:
        print(f"\n  READY — Anti-fraud system checks passed")

    print(f"\n{'=' * 70}\n")


def export_json(report: ChecklistReport, path: str) -> None:
    data = {
        "timestamp": report.timestamp,
        "environment": report.environment,
        "chapter": 19,
        "title": "Anti-Fraud System",
        "summary": {
            "total": report.total,
            "passed": report.passed,
            "failed": report.failed,
            "warnings": report.warnings,
            "skipped": report.skipped,
        },
        "checks": [
            {
                "name": r.name,
                "category": r.category,
                "status": r.status.value,
                "detail": r.detail,
                "requirement": r.requirement,
            }
            for r in report.results
        ],
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"  Report exported to {path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Chapter 19 — Anti-Fraud System Checklist")
    parser.add_argument("--env", default="staging", choices=["staging", "production"])
    parser.add_argument("--host", default="127.0.0.1", help="Host for service health checks")
    parser.add_argument("--report-only", action="store_true", help="Show checklist without live checks")
    parser.add_argument("--json", type=str, help="Export report to JSON file")
    args = parser.parse_args()

    report = ChecklistReport(
        timestamp=datetime.now(timezone.utc).isoformat(),
        environment=args.env,
    )

    check_elasticsearch(report, args.host)
    check_kibana(report, args.host)
    check_fraud_pipeline(report, args.host, args.report_only)
    check_risk_scoring(report, args.host)
    check_coaf_reporting(report, args.host)

    print_report(report)

    if args.json:
        export_json(report, args.json)

    sys.exit(1 if report.failed > 0 else 0)


if __name__ == "__main__":
    main()
