#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 31, Performance Benchmarks and Metrics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Chapter 31 — Performance Implementation Checklist
==================================================
Automated readiness checker for platform performance engineering.
Validates load testing tools, baseline metrics, cache hit rates,
database query performance, API latency (p50/p95/p99), connection pool sizes,
memory/CPU limits, and CDN configuration.

Usage:
    python performance_checklist.py [--env staging|production] [--host HOST]
    python performance_checklist.py --report-only
    python performance_checklist.py --json report.json
"""

import argparse
import json
import shutil
import subprocess
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
# Helpers
# ---------------------------------------------------------------------------

def http_get(url: str, timeout: int = 5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, str(e)
    except Exception as e:
        return None, str(e)


def binary_available(name: str) -> bool:
    return shutil.which(name) is not None


def run_cmd(cmd: list, timeout: int = 10) -> tuple:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout, result.stderr
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return -1, "", str(e)


# ---------------------------------------------------------------------------
# Check functions
# ---------------------------------------------------------------------------

def check_load_testing_tools(report: ChecklistReport, report_only: bool) -> None:
    """Load testing tool availability checks."""

    tools = [
        ("k6", ["k6", "version"], "Grafana k6 — modern load testing with JavaScript scripting"),
        ("locust", ["locust", "--version"], "Locust — Python-based distributed load testing"),
        ("wrk", ["wrk", "--version"], "wrk — HTTP benchmarking tool for latency profiling"),
        ("ab", ["ab", "-V"], "Apache Bench — quick HTTP endpoint throughput testing"),
        ("hey", ["hey", "--help"], "hey — HTTP load generator (similar to wrk)"),
        ("artillery", ["artillery", "version"], "Artillery — scenario-based API load testing"),
    ]

    for tool_name, cmd, description in tools:
        if not report_only:
            rc, out, err = run_cmd(cmd)
            if rc == 0:
                version = (out + err).strip().split("\n")[0][:60]
                st, det = Status.PASS, f"Installed: {version}"
            else:
                st, det = Status.WARN, f"Not found — {description}"
        else:
            st, det = Status.SKIP, "Skipped (--report-only mode)"

        report.add(CheckResult(
            name=f"Load Testing Tool: {tool_name}",
            category="Load Testing Tools",
            status=st,
            detail=det,
            requirement="Chapter 31 — Performance test toolchain",
        ))

    # Test scripts
    test_scenarios = [
        ("Baseline Load Test (100 concurrent users)", "Establish baseline: p50, p95, p99 under normal load"),
        ("Stress Test (500+ concurrent users)", "Find breaking point — identify bottlenecks before production"),
        ("Soak Test (1h sustained load)", "Detect memory leaks and connection pool exhaustion over time"),
        ("Spike Test (sudden 10x traffic)", "Simulate viral event or match start surge — test auto-scaling"),
        ("Bet Placement Load Script", "100 concurrent bets/s — validate <200ms p95 under peak load"),
        ("PIX Deposit Load Script", "50 concurrent PIX deposits/s — validate payment gateway throughput"),
    ]

    for scenario_name, description in test_scenarios:
        report.add(CheckResult(
            name=f"Test Scenario: {scenario_name}",
            category="Load Testing Tools",
            status=Status.WARN,
            detail=description,
            requirement="Chapter 31 — Performance test coverage",
        ))


def check_baseline_metrics(report: ChecklistReport, host: str, report_only: bool) -> None:
    """Baseline performance metrics collection checks."""

    prometheus_base = f"http://{host}:9090"

    if not report_only:
        code, body = http_get(f"{prometheus_base}/-/ready")
        if code == 200:
            st, det = Status.PASS, "Prometheus ready endpoint OK"
        else:
            st, det = Status.FAIL, f"Prometheus not reachable at {prometheus_base} — HTTP {code}"
    else:
        st, det = Status.SKIP, "Skipped (--report-only mode)"

    report.add(CheckResult(
        name="Prometheus Metrics Endpoint",
        category="Baseline Metrics",
        status=st,
        detail=det,
        requirement="Chapter 31 — Metrics collection infrastructure",
    ))

    baselines = [
        ("Baseline: Request Rate (req/s per service)", "7-day rolling baseline per endpoint — alert on 50% deviation"),
        ("Baseline: Error Rate (< 0.1%)", "Normal error rate established per service — alert on 2x increase"),
        ("Baseline: CPU Utilisation (< 60% average)", "Baseline CPU per pod — alert on 80% sustained for 5 min"),
        ("Baseline: Memory Usage (< 70% of limit)", "Baseline memory per pod — alert on 90% of configured limit"),
        ("Baseline: GC Pause Time (JVM services)", "Alert when JVM GC pause exceeds 200ms — tune heap accordingly"),
        ("Baseline: DB Connection Wait Time", "Average DB connection acquisition time tracked as baseline"),
        ("Baseline: Kafka Consumer Lag", "Per-consumer-group lag baseline — alert when lag grows >1min"),
        ("SLO Defined: Availability 99.9% per month", "43.2 minutes downtime budget per month — track error budget"),
    ]

    for name, detail in baselines:
        report.add(CheckResult(
            name=name,
            category="Baseline Metrics",
            status=Status.WARN,
            detail=detail,
            requirement="Chapter 31 — Performance baseline documentation",
        ))


def check_cache_performance(report: ChecklistReport, host: str, report_only: bool) -> None:
    """Cache hit rate and configuration checks."""

    redis_base = f"http://{host}:6379"

    cache_checks = [
        ("Redis Cache Hit Rate >= 90%", "keyspace_hits / (keyspace_hits + keyspace_misses) must be >= 90%"),
        ("Redis Memory Usage < 80%", "Alert when used_memory_rss exceeds 80% of maxmemory"),
        ("Redis Eviction Policy: allkeys-lru", "Eviction policy set to allkeys-lru for session and odds caches"),
        ("Redis Cluster Mode (3+ shards)", "Redis Cluster with 3 shards + replicas for high availability"),
        ("Cache Warm-Up on Deploy", "Odds, game configs, and player segment data pre-loaded after deployment"),
        ("TTL Strategy per Cache Type", "Session: 30min, Odds: 5s, Game configs: 1h, Player profile: 15min"),
        ("Cache Stampede Prevention", "Probabilistic early expiration or mutex lock to prevent cache stampedes"),
        ("Cache Miss Rate Alerting", "Alert when cache miss rate exceeds 20% for >5 minutes"),
    ]

    for name, detail in cache_checks:
        report.add(CheckResult(
            name=name,
            category="Cache Performance",
            status=Status.WARN,
            detail=detail,
            requirement="Chapter 31 — Caching strategy",
        ))


def check_database_performance(report: ChecklistReport, host: str) -> None:
    """Database query performance and optimisation checks."""

    db_checks = [
        ("Slow Query Log Enabled (> 100ms threshold)", "PostgreSQL log_min_duration_statement=100ms — capture all slow queries"),
        ("Query Plan Analysis (EXPLAIN ANALYZE)", "Top 20 slowest queries reviewed weekly with EXPLAIN ANALYZE"),
        ("Index Coverage > 95% for Hot Queries", "All queries in hot path use index scans — no sequential table scans"),
        ("N+1 Query Detection", "APM tool (Datadog/New Relic) configured to detect N+1 query patterns"),
        ("Connection Pooling: PgBouncer/pgcat", "PgBouncer or pgcat in transaction mode — pool size tuned per service"),
        ("Read Replicas for Analytics Queries", "Reporting and analytics routed to read replicas — never primary"),
        ("Partitioning on High-Volume Tables", "transaction_ledger, bet_history, audit_events partitioned by date"),
        ("Autovacuum Tuned for Write-Heavy Tables", "Autovacuum settings aggressive on high-write tables to prevent bloat"),
        ("Database Metrics in Grafana", "pg_stat_activity, locks, replication lag all visible in Grafana"),
    ]

    for name, detail in db_checks:
        report.add(CheckResult(
            name=name,
            category="Database Performance",
            status=Status.WARN,
            detail=detail,
            requirement="Chapter 31 — Database optimisation",
        ))


def check_api_latency(report: ChecklistReport, host: str, report_only: bool) -> None:
    """API latency targets and SLO verification."""

    api_targets = [
        ("/api/v1/bet/place", "p50 < 100ms, p95 < 200ms, p99 < 500ms"),
        ("/api/v1/wallet/balance", "p50 < 50ms, p95 < 100ms, p99 < 200ms"),
        ("/api/v1/odds/live", "p50 < 30ms, p95 < 80ms, p99 < 150ms"),
        ("/api/v1/auth/login", "p50 < 200ms, p95 < 500ms, p99 < 1000ms"),
        ("/api/v1/payments/pix/deposit", "p50 < 500ms, p95 < 2000ms, p99 < 5000ms"),
        ("/api/v1/games/list", "p50 < 50ms, p95 < 100ms, p99 < 200ms"),
    ]

    for endpoint, latency_target in api_targets:
        if not report_only:
            code, body = http_get(f"http://{host}{endpoint}", timeout=5)
            if code is not None:
                st = Status.WARN
                det = f"HTTP {code} — latency thresholds must be validated under load: {latency_target}"
            else:
                st = Status.FAIL
                det = f"Endpoint unreachable — {body[:60]}"
        else:
            st, det = Status.SKIP, "Skipped (--report-only mode)"

        report.add(CheckResult(
            name=f"API Latency SLO: {endpoint}",
            category="API Latency",
            status=st,
            detail=det if report_only or code is None else f"Target: {latency_target}",
            requirement="Chapter 31 — API latency SLOs",
        ))

    # Latency tooling
    latency_tooling = [
        ("Prometheus Histogram Buckets Configured", "http_request_duration_seconds histogram with le buckets at 0.05, 0.1, 0.2, 0.5, 1.0, 2.0"),
        ("Latency Percentile Alerts (p99 > SLO)", "Grafana alert fires when p99 exceeds SLO threshold for 5 minutes"),
        ("Latency Heatmap Dashboard", "Grafana heatmap showing latency distribution over time per endpoint"),
    ]

    for name, detail in latency_tooling:
        report.add(CheckResult(
            name=name,
            category="API Latency",
            status=Status.WARN,
            detail=detail,
            requirement="Chapter 31 — Latency observability",
        ))


def check_connection_pools(report: ChecklistReport) -> None:
    """Connection pool sizing and configuration checks."""

    pool_checks = [
        ("PostgreSQL Max Connections Set", "max_connections=500; PgBouncer limits application-level to 20 per service"),
        ("PgBouncer Pool Size Per Service", "pool_size = (CPU_cores * 2) + effective_spindle_count per service"),
        ("Redis Connection Pool: max=50 per pod", "Max 50 Redis connections per pod; alert on pool exhaustion"),
        ("HTTP Client Connection Pool", "Apache HttpClient / aiohttp configured with max 200 connections, keepalive enabled"),
        ("Kafka Producer Batch Size Tuned", "batch.size=32768, linger.ms=5 for throughput vs latency balance"),
        ("Kafka Consumer Thread Count", "Consumer threads = partition count per topic per service"),
        ("Connection Pool Metrics Exposed", "Pool wait time, size, and timeout counts in Prometheus"),
        ("Pool Exhaustion Alerting", "Alert when connection wait time > 500ms or pool is > 90% utilised"),
    ]

    for name, detail in pool_checks:
        report.add(CheckResult(
            name=name,
            category="Connection Pools",
            status=Status.WARN,
            detail=detail,
            requirement="Chapter 31 — Connection pool optimisation",
        ))


def check_resource_limits(report: ChecklistReport) -> None:
    """Memory and CPU limit configuration checks."""

    resource_checks = [
        ("CPU Requests Defined on All Pods", "requests.cpu set on all containers — enables Kubernetes scheduling"),
        ("Memory Requests Defined on All Pods", "requests.memory set on all containers — prevents OOMKill"),
        ("CPU Limits: 2x CPU Requests", "limits.cpu = 2x requests.cpu — allows burst without monopolising node"),
        ("Memory Limits: = Memory Requests (Guaranteed QoS)", "For payment/fraud services: limits.memory = requests.memory"),
        ("Vertical Pod Autoscaler (VPA) Configured", "VPA in recommendation mode — review weekly, apply with rollout"),
        ("Horizontal Pod Autoscaler: CPU 70% Target", "HPA scales out at 70% average CPU utilisation"),
        ("HPA: Custom Metric (Kafka Lag)", "HPA for consumer pods scales on Kafka consumer lag > 1000 messages"),
        ("Node Autoscaler Configured", "Cluster Autoscaler or Karpenter to add nodes when pods pending > 2min"),
        ("Resource Quota per Namespace", "ResourceQuota objects prevent namespace from consuming excessive cluster resources"),
        ("LimitRange per Namespace", "LimitRange enforces default requests/limits if not set in pod spec"),
    ]

    for name, detail in resource_checks:
        report.add(CheckResult(
            name=name,
            category="Resource Limits",
            status=Status.WARN,
            detail=detail,
            requirement="Chapter 31 — Kubernetes resource management",
        ))


def check_cdn_configuration(report: ChecklistReport) -> None:
    """CDN configuration and static asset caching checks."""

    cdn_checks = [
        ("CDN Deployed for Static Assets", "CloudFront/Cloudflare/Fastly serving JS, CSS, images, fonts"),
        ("CDN Cache Hit Rate >= 85%", "Static assets must achieve >= 85% cache hit rate at CDN edge"),
        ("Cache-Control Headers Set", "JS/CSS: max-age=31536000, immutable. HTML: no-cache. API: no-store"),
        ("Asset Fingerprinting / Content Hash", "All static assets include content hash in filename for cache busting"),
        ("CDN Geo-Distribution: Brazil PoPs", "CDN must have PoPs in Sao Paulo, Rio de Janeiro for low-latency delivery"),
        ("Image Optimisation (WebP/AVIF)", "Images served in WebP with AVIF fallback — 30-50% size reduction"),
        ("Brotli/Gzip Compression", "Brotli compression enabled at CDN and origin for text assets"),
        ("CDN Failover to Origin", "CDN configured to failover to origin on cache miss or CDN outage"),
        ("CDN Access Logs to SIEM", "CDN access logs (Cloudflare Logpush / CloudFront → S3) forwarded to SIEM"),
        ("Prefetch / Preload Critical Resources", "Link rel=preload for fonts, critical CSS, and LCP image"),
    ]

    for name, detail in cdn_checks:
        report.add(CheckResult(
            name=name,
            category="CDN Configuration",
            status=Status.WARN,
            detail=detail,
            requirement="Chapter 31 — Content delivery optimisation",
        ))


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def print_report(report: ChecklistReport) -> None:
    print()
    print("=" * 70)
    print("  CHAPTER 31 — PERFORMANCE IMPLEMENTATION CHECKLIST")
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
    print(f"\n  Performance readiness: {readiness:.0f}%")

    if report.failed > 0:
        print(f"\n  NOT READY — {report.failed} critical checks failed")
    elif report.warnings > 5:
        print(f"\n  REVIEW NEEDED — {report.warnings} items require manual verification")
    else:
        print(f"\n  READY — Performance checks passed")

    print(f"\n{'=' * 70}\n")


def export_json(report: ChecklistReport, path: str) -> None:
    data = {
        "timestamp": report.timestamp,
        "environment": report.environment,
        "chapter": 31,
        "title": "Performance",
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
    parser = argparse.ArgumentParser(description="Chapter 31 — Performance Checklist")
    parser.add_argument("--env", default="staging", choices=["staging", "production"])
    parser.add_argument("--host", default="127.0.0.1", help="Host for live performance checks")
    parser.add_argument("--report-only", action="store_true", help="Show checklist without live checks")
    parser.add_argument("--json", type=str, help="Export report to JSON file")
    args = parser.parse_args()

    report = ChecklistReport(
        timestamp=datetime.now(timezone.utc).isoformat(),
        environment=args.env,
    )

    check_load_testing_tools(report, args.report_only)
    check_baseline_metrics(report, args.host, args.report_only)
    check_cache_performance(report, args.host, args.report_only)
    check_database_performance(report, args.host)
    check_api_latency(report, args.host, args.report_only)
    check_connection_pools(report)
    check_resource_limits(report)
    check_cdn_configuration(report)

    print_report(report)

    if args.json:
        export_json(report, args.json)

    sys.exit(1 if report.failed > 0 else 0)


if __name__ == "__main__":
    main()
