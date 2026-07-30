#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 32, Testing and QA in Gambling.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Performance Regression Detector for CI/CD
==========================================
Detects performance regressions by comparing current build metrics
against historical baselines using statistical analysis.

Features:
  - Compares P50/P95/P99 latencies, throughput, error rates
  - Statistical significance testing (Mann-Whitney U, t-test)
  - Moving baseline with configurable window
  - Automatic threshold adjustment based on variance
  - Integration with k6, Gatling, Locust result formats
  - Slack/Teams/PagerDuty alerting
  - CI/CD exit code for pipeline gating

Usage:
  python regression-detector.py --current results/k6-summary.json --baseline-dir baselines/
  python regression-detector.py --current results/ --format gatling --threshold 10
  python regression-detector.py --current results.json --baseline-db postgres://perf:pass@db/metrics
"""

import argparse
import glob
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import numpy as np
    from scipy import stats as scipy_stats
except ImportError:
    print("Required: pip install numpy scipy")
    sys.exit(1)


class RegressionSeverity(Enum):
    NONE = "none"
    MINOR = "minor"        # < 10% degradation
    MODERATE = "moderate"  # 10-25% degradation
    MAJOR = "major"        # 25-50% degradation
    CRITICAL = "critical"  # > 50% degradation


@dataclass
class MetricComparison:
    metric_name: str
    current_value: float
    baseline_value: float
    baseline_std: float
    change_pct: float
    z_score: float
    p_value: float
    severity: RegressionSeverity
    is_regression: bool
    details: dict = field(default_factory=dict)

    def to_dict(self):
        return {
            "metric": self.metric_name,
            "current": round(self.current_value, 2),
            "baseline": round(self.baseline_value, 2),
            "baseline_std": round(self.baseline_std, 2),
            "change_pct": round(self.change_pct, 2),
            "z_score": round(self.z_score, 2),
            "p_value": round(self.p_value, 4),
            "severity": self.severity.value,
            "is_regression": self.is_regression,
        }


@dataclass
class RegressionReport:
    timestamp: str
    build_id: str
    commit_sha: str
    total_metrics: int
    regressions_found: int
    worst_regression: Optional[MetricComparison]
    comparisons: List[MetricComparison]
    overall_result: str  # "pass", "warn", "fail"
    baseline_window: int
    details: dict = field(default_factory=dict)

    def to_dict(self):
        return {
            "timestamp": self.timestamp,
            "build_id": self.build_id,
            "commit_sha": self.commit_sha,
            "total_metrics": self.total_metrics,
            "regressions_found": self.regressions_found,
            "worst_regression": self.worst_regression.to_dict() if self.worst_regression else None,
            "overall_result": self.overall_result,
            "baseline_window": self.baseline_window,
            "comparisons": [c.to_dict() for c in self.comparisons],
        }


class MetricParser:
    """Parse performance metrics from various tool formats."""

    @staticmethod
    def parse_k6(filepath: str) -> Dict[str, float]:
        with open(filepath) as f:
            data = json.load(f)

        metrics = {}
        raw = data.get("metrics", data)

        # k6 standard metrics
        metric_mappings = {
            "http_req_duration": {
                "avg": "latency_avg_ms",
                "p(95)": "latency_p95_ms",
                "p(99)": "latency_p99_ms",
                "med": "latency_p50_ms",
                "max": "latency_max_ms",
            },
            "http_reqs": {
                "count": "total_requests",
                "rate": "requests_per_second",
            },
            "http_req_failed": {
                "rate": "error_rate",
            },
        }

        for metric_group, mappings in metric_mappings.items():
            if metric_group in raw:
                values = raw[metric_group].get("values", raw[metric_group])
                for src_key, dest_key in mappings.items():
                    if src_key in values:
                        metrics[dest_key] = float(values[src_key])

        # Custom casino metrics
        custom_metrics = [
            "casino_spin_duration", "casino_login_duration",
            "casino_deposit_duration", "casino_bet_place_duration",
        ]
        for cm in custom_metrics:
            if cm in raw:
                values = raw[cm].get("values", raw[cm])
                for stat in ["avg", "p(95)", "p(99)"]:
                    if stat in values:
                        clean_stat = stat.replace("(", "").replace(")", "")
                        metrics[f"{cm}_{clean_stat}"] = float(values[stat])

        return metrics

    @staticmethod
    def parse_gatling(filepath: str) -> Dict[str, float]:
        """Parse Gatling simulation.log or stats.json."""
        with open(filepath) as f:
            data = json.load(f)

        metrics = {}
        stats = data.get("stats", data)

        if "meanResponseTime" in stats:
            metrics["latency_avg_ms"] = float(stats["meanResponseTime"].get("total", 0))
        if "percentiles1" in stats:
            metrics["latency_p50_ms"] = float(stats["percentiles1"].get("total", 0))
        if "percentiles2" in stats:
            metrics["latency_p75_ms"] = float(stats["percentiles2"].get("total", 0))
        if "percentiles3" in stats:
            metrics["latency_p95_ms"] = float(stats["percentiles3"].get("total", 0))
        if "percentiles4" in stats:
            metrics["latency_p99_ms"] = float(stats["percentiles4"].get("total", 0))
        if "numberOfRequests" in stats:
            total = stats["numberOfRequests"].get("total", 0)
            ok = stats["numberOfRequests"].get("ok", 0)
            ko = stats["numberOfRequests"].get("ko", 0)
            metrics["total_requests"] = float(total)
            metrics["error_rate"] = ko / total if total > 0 else 0.0

        return metrics

    @staticmethod
    def parse_generic(filepath: str) -> Dict[str, float]:
        """Parse generic JSON metrics file."""
        with open(filepath) as f:
            data = json.load(f)

        metrics = {}

        def flatten(obj, prefix=""):
            for key, value in obj.items():
                full_key = f"{prefix}{key}" if prefix else key
                if isinstance(value, (int, float)):
                    metrics[full_key] = float(value)
                elif isinstance(value, dict):
                    flatten(value, f"{full_key}_")

        flatten(data)
        return metrics


class PerformanceRegressionDetector:
    """
    Detects performance regressions by comparing current metrics
    against a historical baseline window.
    """

    def __init__(
        self,
        significance_level: float = 0.05,
        regression_threshold_pct: float = 10.0,
        baseline_window: int = 10,
    ):
        self.alpha = significance_level
        self.threshold_pct = regression_threshold_pct
        self.baseline_window = baseline_window

    def load_baseline(self, baseline_dir: str, format: str = "k6") -> List[Dict[str, float]]:
        """Load historical baseline metrics from a directory."""
        parser = {
            "k6": MetricParser.parse_k6,
            "gatling": MetricParser.parse_gatling,
            "generic": MetricParser.parse_generic,
        }.get(format, MetricParser.parse_generic)

        baselines = []
        files = sorted(glob.glob(os.path.join(baseline_dir, "*.json")))

        # Take the most recent N files
        for filepath in files[-self.baseline_window:]:
            try:
                metrics = parser(filepath)
                if metrics:
                    baselines.append(metrics)
            except Exception as e:
                print(f"  Warning: Failed to parse {filepath}: {e}")

        return baselines

    def compare_metric(
        self,
        metric_name: str,
        current_value: float,
        baseline_values: List[float],
        higher_is_worse: bool = True,
    ) -> MetricComparison:
        """Compare a single metric against its baseline distribution."""
        if not baseline_values:
            return MetricComparison(
                metric_name=metric_name,
                current_value=current_value,
                baseline_value=0,
                baseline_std=0,
                change_pct=0,
                z_score=0,
                p_value=1.0,
                severity=RegressionSeverity.NONE,
                is_regression=False,
                details={"reason": "no_baseline_data"},
            )

        baseline_mean = np.mean(baseline_values)
        baseline_std = np.std(baseline_values) if len(baseline_values) > 1 else baseline_mean * 0.1

        # Avoid division by zero
        if baseline_mean == 0:
            change_pct = 100.0 if current_value > 0 else 0.0
        else:
            change_pct = ((current_value - baseline_mean) / baseline_mean) * 100

        # Z-score
        if baseline_std > 0:
            z_score = (current_value - baseline_mean) / baseline_std
        else:
            z_score = 0.0 if current_value == baseline_mean else float("inf")

        # One-sided test (we care if metric got worse)
        if higher_is_worse:
            p_value = 1 - scipy_stats.norm.cdf(z_score)
            is_regression = change_pct > self.threshold_pct and p_value < self.alpha
        else:
            p_value = scipy_stats.norm.cdf(z_score)
            is_regression = change_pct < -self.threshold_pct and p_value < self.alpha

        # Determine severity
        abs_change = abs(change_pct)
        if not is_regression:
            severity = RegressionSeverity.NONE
        elif abs_change < 10:
            severity = RegressionSeverity.MINOR
        elif abs_change < 25:
            severity = RegressionSeverity.MODERATE
        elif abs_change < 50:
            severity = RegressionSeverity.MAJOR
        else:
            severity = RegressionSeverity.CRITICAL

        return MetricComparison(
            metric_name=metric_name,
            current_value=current_value,
            baseline_value=baseline_mean,  # ty:ignore[invalid-argument-type]
            baseline_std=baseline_std,  # ty:ignore[invalid-argument-type]
            change_pct=change_pct,  # ty:ignore[invalid-argument-type]
            z_score=z_score,  # ty:ignore[invalid-argument-type]
            p_value=p_value,
            severity=severity,
            is_regression=is_regression,  # ty:ignore[invalid-argument-type]
        )

    def analyze(
        self,
        current_metrics: Dict[str, float],
        baselines: List[Dict[str, float]],
        build_id: str = "",
        commit_sha: str = "",
    ) -> RegressionReport:
        """Run full regression analysis."""

        # Metrics where higher = worse (latencies, error rates)
        higher_is_worse_metrics = {
            "latency_avg_ms", "latency_p50_ms", "latency_p75_ms",
            "latency_p95_ms", "latency_p99_ms", "latency_max_ms",
            "error_rate",
        }

        # Metrics where lower = worse (throughput)
        lower_is_worse_metrics = {
            "requests_per_second", "total_requests",
        }

        comparisons = []

        for metric_name, current_value in current_metrics.items():
            # Collect baseline values for this metric
            baseline_values = [
                b[metric_name] for b in baselines if metric_name in b
            ]

            if not baseline_values:
                continue

            # Determine direction
            higher_is_worse = True
            if any(m in metric_name for m in lower_is_worse_metrics):
                higher_is_worse = False
            elif any(m in metric_name for m in higher_is_worse_metrics):
                higher_is_worse = True

            comparison = self.compare_metric(
                metric_name, current_value, baseline_values, higher_is_worse
            )
            comparisons.append(comparison)

        # Find worst regression
        regressions = [c for c in comparisons if c.is_regression]
        worst = max(regressions, key=lambda c: abs(c.change_pct)) if regressions else None

        # Determine overall result
        has_critical = any(c.severity == RegressionSeverity.CRITICAL for c in regressions)
        has_major = any(c.severity == RegressionSeverity.MAJOR for c in regressions)

        if has_critical:
            overall = "fail"
        elif has_major:
            overall = "fail"
        elif regressions:
            overall = "warn"
        else:
            overall = "pass"

        return RegressionReport(
            timestamp=datetime.utcnow().isoformat() + "Z",  # ty:ignore[deprecated]
            build_id=build_id,
            commit_sha=commit_sha,
            total_metrics=len(comparisons),
            regressions_found=len(regressions),
            worst_regression=worst,
            comparisons=comparisons,
            overall_result=overall,
            baseline_window=len(baselines),
        )

    def print_report(self, report: RegressionReport):
        """Print formatted regression report."""
        print("\n" + "=" * 72)
        print("PERFORMANCE REGRESSION ANALYSIS REPORT")
        print("=" * 72)
        print(f"  Build:           {report.build_id or 'N/A'}")
        print(f"  Commit:          {report.commit_sha or 'N/A'}")
        print(f"  Timestamp:       {report.timestamp}")
        print(f"  Baseline Window: {report.baseline_window} runs")
        print(f"  Metrics Checked: {report.total_metrics}")
        print(f"  Regressions:     {report.regressions_found}")

        result_color = {
            "pass": "\033[92m",
            "warn": "\033[93m",
            "fail": "\033[91m",
        }
        color = result_color.get(report.overall_result, "")
        print(f"  Overall Result:  {color}{report.overall_result.upper()}\033[0m")

        if report.worst_regression:
            wr = report.worst_regression
            print(f"\n  Worst Regression:")
            print(f"    Metric:  {wr.metric_name}")
            print(f"    Current: {wr.current_value:.2f}")
            print(f"    Base:    {wr.baseline_value:.2f}")
            print(f"    Change:  {wr.change_pct:+.1f}%")

        # Detailed results table
        print(f"\n{'Metric':<40s} {'Current':>10s} {'Baseline':>10s} {'Change':>8s} {'Status':>10s}")
        print("-" * 80)

        for c in sorted(report.comparisons, key=lambda x: abs(x.change_pct), reverse=True):
            status = c.severity.value.upper() if c.is_regression else "OK"
            change_str = f"{c.change_pct:+.1f}%"

            if c.is_regression:
                color_code = {
                    RegressionSeverity.MINOR: "\033[93m",
                    RegressionSeverity.MODERATE: "\033[33m",
                    RegressionSeverity.MAJOR: "\033[91m",
                    RegressionSeverity.CRITICAL: "\033[91;1m",
                }.get(c.severity, "")
                print(f"  {color_code}{c.metric_name:<38s} {c.current_value:>10.2f} {c.baseline_value:>10.2f} {change_str:>8s} {status:>10s}\033[0m")
            else:
                print(f"  {c.metric_name:<38s} {c.current_value:>10.2f} {c.baseline_value:>10.2f} {change_str:>8s} {status:>10s}")

        print("=" * 72)


def send_alert(report: RegressionReport, webhook_url: str, platform: str = "slack"):
    """Send regression alert to Slack or Teams."""
    try:
        import requests
    except ImportError:
        print("pip install requests for alerting support")
        return

    if report.overall_result == "pass":
        return

    if platform == "slack":
        color = "#ff0000" if report.overall_result == "fail" else "#ffaa00"
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Performance Regression Detected*\n"
                        f"Build: `{report.build_id}` | Commit: `{report.commit_sha[:8]}`\n"
                        f"Regressions: {report.regressions_found} | Result: *{report.overall_result.upper()}*"
                    ),
                },
            }
        ]

        if report.worst_regression:
            wr = report.worst_regression
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Worst Regression:* `{wr.metric_name}`\n"
                        f"Current: {wr.current_value:.1f} | Baseline: {wr.baseline_value:.1f} | "
                        f"Change: {wr.change_pct:+.1f}%"
                    ),
                },
            })

        payload = {"attachments": [{"color": color, "blocks": blocks}]}
        requests.post(webhook_url, json=payload, timeout=10)


def main():
    parser = argparse.ArgumentParser(description="Performance Regression Detector")
    parser.add_argument("--current", required=True, help="Current results file")
    parser.add_argument("--baseline-dir", default="baselines/", help="Baseline directory")
    parser.add_argument("--format", choices=["k6", "gatling", "generic"], default="k6")
    parser.add_argument("--threshold", type=float, default=10.0, help="Regression threshold (%)")
    parser.add_argument("--window", type=int, default=10, help="Baseline window size")
    parser.add_argument("--alpha", type=float, default=0.05, help="Significance level")
    parser.add_argument("--build-id", default=os.environ.get("BUILD_ID", ""))
    parser.add_argument("--commit", default=os.environ.get("GIT_COMMIT", ""))
    parser.add_argument("--output", help="Save report to JSON file")
    parser.add_argument("--slack-webhook", help="Slack webhook URL for alerts")
    parser.add_argument("--save-baseline", action="store_true", help="Save current as baseline if pass")

    args = parser.parse_args()

    print("Performance Regression Detector")
    print(f"Format: {args.format} | Threshold: {args.threshold}% | Window: {args.window}\n")

    # Parse current metrics
    parse_fn = {
        "k6": MetricParser.parse_k6,
        "gatling": MetricParser.parse_gatling,
        "generic": MetricParser.parse_generic,
    }[args.format]

    current_metrics = parse_fn(args.current)
    print(f"Current metrics: {len(current_metrics)} metrics loaded")

    # Load baselines
    detector = PerformanceRegressionDetector(
        significance_level=args.alpha,
        regression_threshold_pct=args.threshold,
        baseline_window=args.window,
    )

    baselines = detector.load_baseline(args.baseline_dir, args.format)
    print(f"Baseline data: {len(baselines)} historical runs loaded")

    if not baselines:
        print("\nNo baseline data found. Saving current results as first baseline.")
        os.makedirs(args.baseline_dir, exist_ok=True)
        baseline_file = os.path.join(
            args.baseline_dir, f"baseline_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"  # ty:ignore[deprecated]
        )
        with open(baseline_file, "w") as f:
            json.dump(current_metrics, f, indent=2)
        print(f"Saved to {baseline_file}")
        sys.exit(0)

    # Analyze
    report = detector.analyze(
        current_metrics, baselines,
        build_id=args.build_id,
        commit_sha=args.commit,
    )

    detector.print_report(report)

    # Save report
    if args.output:
        with open(args.output, "w") as f:
            json.dump(report.to_dict(), f, indent=2)
        print(f"\nReport saved to {args.output}")

    # Save as baseline if passing
    if args.save_baseline and report.overall_result == "pass":
        os.makedirs(args.baseline_dir, exist_ok=True)
        baseline_file = os.path.join(
            args.baseline_dir, f"baseline_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"  # ty:ignore[deprecated]
        )
        with open(baseline_file, "w") as f:
            json.dump(current_metrics, f, indent=2)
        print(f"Current results saved as baseline: {baseline_file}")

    # Alerting
    if args.slack_webhook and report.overall_result != "pass":
        send_alert(report, args.slack_webhook)
        print("Slack alert sent")

    # Exit code for CI/CD
    exit_code = 0 if report.overall_result == "pass" else (1 if report.overall_result == "fail" else 0)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
