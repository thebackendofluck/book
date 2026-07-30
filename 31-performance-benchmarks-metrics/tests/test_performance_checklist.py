# Companion code for "The Backend of Luck" - Chapter 31, Performance Benchmarks and Metrics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Behavioral tests for Chapter 31 — Performance Checklist and Gaming Monitor."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from performance_checklist import (
    ChecklistReport,
    CheckResult,
    Status,
    check_load_testing_tools,
)


class TestChecklistReport:
    """Validate ChecklistReport tallies and aggregation logic."""

    def test_add_increments_counters_correctly(self):
        report = ChecklistReport()
        report.add(CheckResult("a", "cat", Status.PASS, "ok", "req"))
        report.add(CheckResult("b", "cat", Status.FAIL, "bad", "req"))
        report.add(CheckResult("c", "cat", Status.WARN, "meh", "req"))
        report.add(CheckResult("d", "cat", Status.SKIP, "skip", "req"))

        assert report.total == 4
        assert report.passed == 1
        assert report.failed == 1
        assert report.warnings == 1
        assert report.skipped == 1

    def test_empty_report_starts_at_zero(self):
        report = ChecklistReport()
        assert report.total == 0
        assert report.passed == 0
        assert report.failed == 0
        assert len(report.results) == 0

    def test_load_testing_tools_populates_report(self):
        report = ChecklistReport()
        check_load_testing_tools(report, report_only=True)
        # Should have entries for 6 tools + 6 test scenarios = 12
        assert report.total == 12
        # In report-only mode all tool checks are SKIP
        skip_count = sum(1 for r in report.results if r.status == Status.SKIP)
        assert skip_count == 6
        # Scenario checks should be WARN
        warn_count = sum(1 for r in report.results if r.status == Status.WARN)
        assert warn_count == 6


class TestGamingPerformanceBaselines:
    """Validate gaming performance baseline configuration."""

    def test_baselines_contain_required_game_categories(self):
        sys.path.insert(
            0, os.path.join(os.path.dirname(__file__), "..", "performance-monitor")
        )
        from gaming_performance import GamingPerformanceMonitor

        monitor = GamingPerformanceMonitor()
        baselines = monitor.gaming_baselines

        assert "game_loading" in baselines
        assert "realtime_performance" in baselines
        assert "betting_system" in baselines

        # Validate slot loading target is reasonable (under 5s)
        slot_target = baselines["game_loading"]["slot_games"]["loading_time_target"]
        assert 0 < slot_target <= 5000

    def test_baselines_bet_placement_latency_within_bounds(self):
        sys.path.insert(
            0, os.path.join(os.path.dirname(__file__), "..", "performance-monitor")
        )
        from gaming_performance import GamingPerformanceMonitor

        monitor = GamingPerformanceMonitor()
        bp = monitor.gaming_baselines["realtime_performance"]["bet_placement_latency"]
        assert bp["target"] < bp["maximum"]
        assert bp["target"] > 0
