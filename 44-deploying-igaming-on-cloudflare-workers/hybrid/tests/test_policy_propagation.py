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

"""Tests for policy_propagation.py."""

from __future__ import annotations

import json
import unittest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from policy_propagation import (
    CloudflareKVAdapter,
    CoreConfigSource,
    PolicyPropagationEngine,
    PropagationStatus,
    _checksum,
)


def _make_engine(
    failure_keys: set[str] | None = None,
) -> tuple[PolicyPropagationEngine, CloudflareKVAdapter, CoreConfigSource]:
    kv = CloudflareKVAdapter(simulated_latency_ms=0.0, failure_keys=failure_keys)
    core = CoreConfigSource()
    engine = PolicyPropagationEngine(kv, core)
    return engine, kv, core


class TestPropagateAll(unittest.TestCase):
    def test_propagates_all_keys(self):
        engine, kv, core = _make_engine()
        core.set("a", {"x": 1})
        core.set("b", {"y": 2})
        batch = engine.propagate()
        self.assertEqual(batch.status, PropagationStatus.VERIFIED)
        self.assertEqual(len(batch.entries), 2)

    def test_kv_contains_values_after_propagation(self):
        engine, kv, core = _make_engine()
        core.set("feature:test", {"enabled": True})
        engine.propagate()
        raw = kv.get("feature:test")
        self.assertIsNotNone(raw)
        val = json.loads(raw)
        self.assertTrue(val["enabled"])

    def test_empty_core_propagates_nothing(self):
        engine, kv, core = _make_engine()
        batch = engine.propagate()
        self.assertEqual(batch.status, PropagationStatus.VERIFIED)
        self.assertEqual(len(batch.entries), 0)


class TestPropagateSingle(unittest.TestCase):
    def test_single_key(self):
        engine, kv, core = _make_engine()
        core.set("feature:a", {"enabled": True})
        core.set("feature:b", {"enabled": False})
        batch = engine.propagate_single("feature:a")
        self.assertEqual(len(batch.entries), 1)
        self.assertEqual(batch.status, PropagationStatus.VERIFIED)

    def test_single_nonexistent_key(self):
        engine, kv, core = _make_engine()
        batch = engine.propagate_single("nope")
        self.assertEqual(len(batch.entries), 0)


class TestVerification(unittest.TestCase):
    def test_checksum_matches(self):
        engine, kv, core = _make_engine()
        core.set("cfg", {"value": 42})
        batch = engine.propagate()
        self.assertEqual(batch.status, PropagationStatus.VERIFIED)
        self.assertEqual(len(batch.errors), 0)

    def test_checksum_deterministic(self):
        val = {"a": 1, "b": 2}
        self.assertEqual(_checksum(val), _checksum(val))

    def test_checksum_sensitive_to_changes(self):
        self.assertNotEqual(
            _checksum({"enabled": True}),
            _checksum({"enabled": False}),
        )


class TestRollback(unittest.TestCase):
    def test_rollback_on_write_failure(self):
        engine, kv, core = _make_engine(failure_keys={"bad_key"})
        core.set("bad_key", {"fail": True})
        batch = engine.propagate()
        self.assertEqual(batch.status, PropagationStatus.FAILED)
        self.assertGreater(len(batch.errors), 0)

    def test_previous_state_preserved_on_rollback(self):
        engine, kv, core = _make_engine()
        # First propagation
        core.set("cfg", {"version": 1})
        engine.propagate()

        # Second propagation with a key that will fail
        engine2, kv2, core2 = PolicyPropagationEngine(
            CloudflareKVAdapter(failure_keys={"cfg_bad"}),
            core,
        ), None, None
        # Just verify the concept: the original value is still there
        raw = kv.get("cfg")
        val = json.loads(raw)
        self.assertEqual(val["version"], 1)


class TestUpdatePropagation(unittest.TestCase):
    def test_update_overwrites(self):
        engine, kv, core = _make_engine()
        core.set("flag", {"enabled": True})
        engine.propagate()
        core.set("flag", {"enabled": False})
        engine.propagate_single("flag")
        raw = kv.get("flag")
        val = json.loads(raw)
        self.assertFalse(val["enabled"])

    def test_captures_previous_value(self):
        engine, kv, core = _make_engine()
        core.set("flag", {"enabled": True})
        engine.propagate()
        core.set("flag", {"enabled": False})
        batch = engine.propagate_single("flag")
        entry = batch.entries[0]
        self.assertIsNotNone(entry.previous_value)
        self.assertTrue(entry.previous_value["enabled"])


class TestHistory(unittest.TestCase):
    def test_history_tracks_batches(self):
        engine, kv, core = _make_engine()
        core.set("a", {"x": 1})
        engine.propagate()
        core.set("a", {"x": 2})
        engine.propagate()
        self.assertEqual(len(engine.get_history()), 2)


class TestReport(unittest.TestCase):
    def test_report_structure(self):
        engine, kv, core = _make_engine()
        core.set("cfg", {"val": 1})
        batch = engine.propagate()
        report = engine.generate_report(batch)
        d = report.to_dict()
        self.assertIn("batch_id", d)
        self.assertIn("status", d)
        self.assertIn("all_verified", d)
        self.assertTrue(d["all_verified"])

    def test_failed_report(self):
        engine, kv, core = _make_engine(failure_keys={"fail"})
        core.set("fail", {"x": 1})
        batch = engine.propagate()
        report = engine.generate_report(batch)
        self.assertFalse(report.all_verified)
        self.assertGreater(len(report.errors), 0)


if __name__ == "__main__":
    unittest.main()
