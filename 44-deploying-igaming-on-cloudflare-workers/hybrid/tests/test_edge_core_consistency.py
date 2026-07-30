#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 44, Deploying iGaming Platforms on Cloudflare Workers.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Tests for edge_core_consistency.py."""

from __future__ import annotations

import time
import unittest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from edge_core_consistency import (
    ConsistencyStatus,
    CorePostgres,
    CoreRedis,
    EdgeKV,
    check_balance_consistency,
    check_config_consistency,
    check_session_consistency,
    run_consistency_suite,
    simulate_degraded_core_down,
    simulate_degraded_edge_down,
    simulate_degraded_split_brain,
)


class TestBalanceConsistency(unittest.TestCase):
    def test_consistent_balances(self):
        edge = EdgeKV()
        core = CorePostgres()
        bal = {"balance": 100.00, "currency": "EUR"}
        edge.put("balance:p1", bal)
        core.upsert("wallets", "p1", bal)
        result = check_balance_consistency(edge, core, "p1")
        self.assertEqual(result.status, ConsistencyStatus.CONSISTENT)

    def test_drifted_balances(self):
        edge = EdgeKV()
        core = CorePostgres()
        edge.put("balance:p1", {"balance": 95.00, "currency": "EUR"})
        core.upsert("wallets", "p1", {"balance": 100.00, "currency": "EUR"})
        result = check_balance_consistency(edge, core, "p1")
        self.assertEqual(result.status, ConsistencyStatus.DRIFT)

    def test_missing_from_edge(self):
        edge = EdgeKV()
        core = CorePostgres()
        core.upsert("wallets", "p1", {"balance": 100.00})
        result = check_balance_consistency(edge, core, "p1")
        self.assertEqual(result.status, ConsistencyStatus.DRIFT)

    def test_missing_from_both(self):
        edge = EdgeKV()
        core = CorePostgres()
        result = check_balance_consistency(edge, core, "p1")
        self.assertEqual(result.status, ConsistencyStatus.CONSISTENT)


class TestSessionConsistency(unittest.TestCase):
    def test_consistent_sessions(self):
        edge = EdgeKV()
        core = CoreRedis()
        sess = {"player_id": "p1", "active": True}
        edge.put("session:s1", sess)
        core.set("session:s1", sess)
        result = check_session_consistency(edge, core, "s1")
        self.assertEqual(result.status, ConsistencyStatus.CONSISTENT)

    def test_drifted_sessions(self):
        edge = EdgeKV()
        core = CoreRedis()
        edge.put("session:s1", {"player_id": "p1", "active": True})
        core.set("session:s1", {"player_id": "p1", "active": False})
        result = check_session_consistency(edge, core, "s1")
        self.assertEqual(result.status, ConsistencyStatus.DRIFT)

    def test_missing_from_core(self):
        edge = EdgeKV()
        core = CoreRedis()
        edge.put("session:s1", {"player_id": "p1", "active": True})
        result = check_session_consistency(edge, core, "s1")
        self.assertEqual(result.status, ConsistencyStatus.DRIFT)


class TestConfigConsistency(unittest.TestCase):
    def test_consistent_config(self):
        edge = EdgeKV()
        core = CoreRedis()
        cfg = {"enabled": True, "rollout_pct": 100}
        edge.put("config:feature:x", cfg)
        core.set("config:feature:x", cfg)
        result = check_config_consistency(edge, core, "feature:x")
        self.assertEqual(result.status, ConsistencyStatus.CONSISTENT)

    def test_drifted_config(self):
        edge = EdgeKV()
        core = CoreRedis()
        edge.put("config:feature:x", {"enabled": True})
        core.set("config:feature:x", {"enabled": False})
        result = check_config_consistency(edge, core, "feature:x")
        self.assertEqual(result.status, ConsistencyStatus.DRIFT)


class TestDegradedCoreDown(unittest.TestCase):
    def test_edge_continues(self):
        edge = EdgeKV()
        core_pg = CorePostgres()
        core_redis = CoreRedis()
        result = simulate_degraded_core_down(edge, core_pg, core_redis, "p1")
        self.assertTrue(result.edge_available)
        self.assertFalse(result.core_available)
        self.assertTrue(result.edge_served_stale)

    def test_core_restored_after(self):
        edge = EdgeKV()
        core_pg = CorePostgres()
        core_redis = CoreRedis()
        simulate_degraded_core_down(edge, core_pg, core_redis, "p1")
        # Core should be available again
        core_pg.upsert("wallets", "p1", {"balance": 200})
        self.assertIsNotNone(core_pg.get("wallets", "p1"))


class TestDegradedEdgeDown(unittest.TestCase):
    def test_core_continues(self):
        edge = EdgeKV()
        core_pg = CorePostgres()
        core_redis = CoreRedis()
        result = simulate_degraded_edge_down(edge, core_pg, core_redis, "p2")
        self.assertFalse(result.edge_available)
        self.assertTrue(result.core_available)


class TestDegradedSplitBrain(unittest.TestCase):
    def test_detects_divergence(self):
        edge = EdgeKV()
        core_pg = CorePostgres()
        result = simulate_degraded_split_brain(edge, core_pg, "p3")
        self.assertTrue(result.edge_served_stale)
        self.assertIn("core", result.recovery_action.lower())


class TestFullSuite(unittest.TestCase):
    def test_suite_all_consistent(self):
        report = run_consistency_suite("staging")
        self.assertTrue(report.all_consistent)
        self.assertEqual(report.drift_count, 0)

    def test_suite_has_checks(self):
        report = run_consistency_suite("staging")
        self.assertGreater(len(report.checks), 0)

    def test_suite_has_degraded_scenarios(self):
        report = run_consistency_suite("staging")
        self.assertGreater(len(report.degraded_scenarios), 0)

    def test_suite_report_serializable(self):
        report = run_consistency_suite("staging")
        d = report.to_dict()
        self.assertIn("all_consistent", d)
        self.assertIn("checks", d)
        self.assertIn("degraded_scenarios", d)


if __name__ == "__main__":
    unittest.main()
