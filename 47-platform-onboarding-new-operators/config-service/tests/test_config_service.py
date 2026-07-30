#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 47, Platform Onboarding.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Tests for the Runtime Configuration Governance Service.

Covers:
  - Feature flag CRUD and validation
  - Jurisdiction rules and defaults
  - Supplier config lifecycle
  - Config versioning and rollback
  - Edge/core propagation and consistency
  - Audit trail completeness
  - Environment-specific propagation behaviour
"""

from __future__ import annotations

import unittest

import sys
import os

# Allow importing from parent package without install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config_service import (
    AuditAction,
    CloudflareKVCache,
    ConfigScope,
    ConfigService,
    ConfigValidationError,
    Environment,
    PostgresConfigStore,
    RedisCache,
    _compute_checksum,
)


def _make_service(
    env: Environment = Environment.STAGING,
) -> tuple[ConfigService, PostgresConfigStore, RedisCache, CloudflareKVCache]:
    store = PostgresConfigStore()
    redis = RedisCache()
    kv = CloudflareKVCache()
    svc = ConfigService(store, redis, kv, environment=env)
    return svc, store, redis, kv


# ---------------------------------------------------------------------------
# Feature flags
# ---------------------------------------------------------------------------


class TestFeatureFlags(unittest.TestCase):
    def test_create_and_read(self):
        svc, *_ = _make_service()
        svc.set_feature_flag("game:slots", enabled=True, actor="ops")
        self.assertTrue(svc.is_feature_enabled("game:slots"))

    def test_disable_flag(self):
        svc, *_ = _make_service()
        svc.set_feature_flag("game:slots", enabled=True, actor="ops")
        svc.set_feature_flag("game:slots", enabled=False, actor="ops")
        self.assertFalse(svc.is_feature_enabled("game:slots"))

    def test_rollout_percentage(self):
        svc, *_ = _make_service()
        v = svc.set_feature_flag("game:crash", enabled=True, actor="ops",
                                 rollout_pct=25.0)
        self.assertEqual(v.value["rollout_pct"], 25.0)

    def test_invalid_rollout_pct(self):
        svc, *_ = _make_service()
        with self.assertRaises(ConfigValidationError):
            svc.set_feature_flag("game:x", enabled=True, actor="ops",
                                 rollout_pct=150.0)

    def test_missing_enabled_field(self):
        svc, *_ = _make_service()
        with self.assertRaises(ConfigValidationError):
            svc.set_config(ConfigScope.FEATURE_FLAG, "bad", {"foo": 1}, "ops")

    def test_non_boolean_enabled(self):
        svc, *_ = _make_service()
        with self.assertRaises(ConfigValidationError):
            svc.set_config(
                ConfigScope.FEATURE_FLAG, "bad",
                {"enabled": "yes"}, "ops",
            )

    def test_nonexistent_flag_returns_false(self):
        svc, *_ = _make_service()
        self.assertFalse(svc.is_feature_enabled("does:not:exist"))


# ---------------------------------------------------------------------------
# Jurisdiction rules
# ---------------------------------------------------------------------------


class TestJurisdictionRules(unittest.TestCase):
    def test_apply_gb_defaults(self):
        svc, *_ = _make_service()
        v = svc.apply_jurisdiction_defaults("GB", actor="compliance")
        self.assertIsNotNone(v)
        val = svc.get_config("jurisdiction:GB")
        self.assertEqual(val["min_age"], 18)
        self.assertTrue(val["kyc_required_before_deposit"])

    def test_apply_us_nj_defaults(self):
        svc, *_ = _make_service()
        v = svc.apply_jurisdiction_defaults("US-NJ", actor="compliance")
        self.assertIsNotNone(v)
        val = svc.get_config("jurisdiction:US-NJ")
        self.assertEqual(val["min_age"], 21)
        self.assertTrue(val["geofence_required"])

    def test_apply_de_defaults_blocks_live_games(self):
        svc, *_ = _make_service()
        svc.apply_jurisdiction_defaults("DE", actor="compliance")
        val = svc.get_config("jurisdiction:DE")
        self.assertIn("live-roulette", val["blocked_games"])
        self.assertTrue(val["autoplay_prohibited"])

    def test_apply_br_defaults(self):
        svc, *_ = _make_service()
        svc.apply_jurisdiction_defaults("BR", actor="compliance")
        val = svc.get_config("jurisdiction:BR")
        self.assertTrue(val["cpf_verification_required"])
        self.assertTrue(val["pix_mandatory"])

    def test_unknown_jurisdiction_returns_none(self):
        svc, *_ = _make_service()
        result = svc.apply_jurisdiction_defaults("XX", actor="compliance")
        self.assertIsNone(result)

    def test_invalid_min_age(self):
        svc, *_ = _make_service()
        with self.assertRaises(ConfigValidationError):
            svc.set_config(
                ConfigScope.JURISDICTION, "jurisdiction:XX",
                {"min_age": 16}, "compliance",
            )

    def test_missing_min_age(self):
        svc, *_ = _make_service()
        with self.assertRaises(ConfigValidationError):
            svc.set_config(
                ConfigScope.JURISDICTION, "jurisdiction:XX",
                {"blocked_games": []}, "compliance",
            )

    def test_blocked_games_must_be_list(self):
        svc, *_ = _make_service()
        with self.assertRaises(ConfigValidationError):
            svc.set_config(
                ConfigScope.JURISDICTION, "jurisdiction:XX",
                {"min_age": 18, "blocked_games": "poker"}, "compliance",
            )


# ---------------------------------------------------------------------------
# Supplier config
# ---------------------------------------------------------------------------


class TestSupplierConfig(unittest.TestCase):
    def test_enable_supplier(self):
        svc, *_ = _make_service()
        v = svc.enable_supplier("pragmatic", {
            "rtp_target": 96.5,
            "bet_limits": {"min": 0.20, "max": 100.0},
        }, actor="integration")
        self.assertTrue(v.value["enabled"])
        self.assertEqual(v.value["supplier_id"], "pragmatic")

    def test_disable_supplier(self):
        svc, *_ = _make_service()
        svc.enable_supplier("evolution", {
            "rtp_target": 97.3,
            "bet_limits": {"min": 1.0, "max": 5000.0},
        }, actor="integration")
        v = svc.disable_supplier("evolution", actor="ops")
        self.assertFalse(v.value["enabled"])

    def test_disable_nonexistent_supplier(self):
        svc, *_ = _make_service()
        with self.assertRaises(ConfigValidationError):
            svc.disable_supplier("ghost", actor="ops")

    def test_invalid_rtp_target(self):
        svc, *_ = _make_service()
        with self.assertRaises(ConfigValidationError):
            svc.enable_supplier("bad", {
                "rtp_target": 105.0,
                "bet_limits": {"min": 0.10, "max": 50.0},
            }, actor="integration")

    def test_invalid_bet_limits(self):
        svc, *_ = _make_service()
        with self.assertRaises(ConfigValidationError):
            svc.enable_supplier("bad", {
                "rtp_target": 96.0,
                "bet_limits": {"min": 100.0, "max": 50.0},
            }, actor="integration")

    def test_missing_supplier_id_auto_injected(self):
        svc, *_ = _make_service()
        v = svc.enable_supplier("netent", {
            "rtp_target": 96.0,
        }, actor="integration")
        self.assertEqual(v.value["supplier_id"], "netent")


# ---------------------------------------------------------------------------
# Versioning and rollback
# ---------------------------------------------------------------------------


class TestVersioning(unittest.TestCase):
    def test_versions_are_immutable(self):
        svc, *_ = _make_service()
        v1 = svc.set_feature_flag("flag:a", enabled=True, actor="ops")
        v2 = svc.set_feature_flag("flag:a", enabled=False, actor="ops")
        self.assertNotEqual(v1.version_id, v2.version_id)
        self.assertEqual(v2.previous_version_id, v1.version_id)

    def test_history_preserves_all_versions(self):
        svc, *_ = _make_service()
        svc.set_feature_flag("flag:b", enabled=True, actor="ops")
        svc.set_feature_flag("flag:b", enabled=False, actor="ops")
        svc.set_feature_flag("flag:b", enabled=True, actor="ops")
        history = svc.get_history("flag:b")
        self.assertEqual(len(history), 3)

    def test_rollback(self):
        svc, *_ = _make_service()
        v1 = svc.set_feature_flag("flag:c", enabled=True, actor="ops",
                                   rollout_pct=100.0)
        svc.set_feature_flag("flag:c", enabled=False, actor="ops")
        self.assertFalse(svc.is_feature_enabled("flag:c"))
        rb = svc.rollback("flag:c", v1.version_id, actor="ops")
        self.assertTrue(svc.is_feature_enabled("flag:c"))
        self.assertEqual(rb.checksum, v1.checksum)
        self.assertEqual(rb.metadata["rollback_to"], v1.version_id)

    def test_rollback_nonexistent_version(self):
        svc, *_ = _make_service()
        svc.set_feature_flag("flag:d", enabled=True, actor="ops")
        with self.assertRaises(ConfigValidationError):
            svc.rollback("flag:d", "nonexistent", actor="ops")

    def test_checksum_deterministic(self):
        val = {"enabled": True, "rollout_pct": 50.0}
        self.assertEqual(_compute_checksum(val), _compute_checksum(val))

    def test_checksum_changes_with_value(self):
        a = _compute_checksum({"enabled": True})
        b = _compute_checksum({"enabled": False})
        self.assertNotEqual(a, b)


# ---------------------------------------------------------------------------
# Propagation and consistency
# ---------------------------------------------------------------------------


class TestPropagation(unittest.TestCase):
    def test_redis_receives_config(self):
        svc, _, redis, _ = _make_service()
        svc.set_feature_flag("flag:e", enabled=True, actor="ops")
        val = redis.get_config("flag:e")
        self.assertIsNotNone(val)
        self.assertTrue(val["enabled"])

    def test_kv_receives_config(self):
        svc, _, _, kv = _make_service()
        svc.set_feature_flag("flag:f", enabled=True, actor="ops")
        val = kv.read("flag:f")
        self.assertIsNotNone(val)
        self.assertTrue(val["enabled"])

    def test_consistency_check_passes(self):
        svc, *_ = _make_service()
        svc.set_feature_flag("flag:g", enabled=True, actor="ops")
        result = svc.check_consistency("flag:g")
        self.assertTrue(result["all_consistent"])

    def test_consistency_detects_redis_drift(self):
        svc, _, redis, _ = _make_service()
        svc.set_feature_flag("flag:h", enabled=True, actor="ops")
        # simulate drift
        redis.set_config("flag:h", {"enabled": False, "rollout_pct": 100.0})
        result = svc.check_consistency("flag:h")
        self.assertFalse(result["redis_consistent"])
        self.assertFalse(result["all_consistent"])

    def test_consistency_detects_kv_drift(self):
        svc, _, _, kv = _make_service()
        svc.set_feature_flag("flag:i", enabled=True, actor="ops")
        # simulate drift
        kv.bulk_write({"flag:i": {"enabled": False, "rollout_pct": 100.0}})
        result = svc.check_consistency("flag:i")
        self.assertFalse(result["kv_consistent"])

    def test_consistency_nonexistent_key(self):
        svc, *_ = _make_service()
        result = svc.check_consistency("nope")
        self.assertFalse(result["exists"])

    def test_propagation_updates_on_change(self):
        svc, _, redis, kv = _make_service()
        svc.set_feature_flag("flag:j", enabled=True, actor="ops")
        svc.set_feature_flag("flag:j", enabled=False, actor="ops")
        self.assertFalse(redis.get_config("flag:j")["enabled"])
        self.assertFalse(kv.read("flag:j")["enabled"])


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------


class TestAuditTrail(unittest.TestCase):
    def test_create_generates_audit(self):
        svc, *_ = _make_service()
        svc.set_feature_flag("flag:k", enabled=True, actor="alice")
        trail = svc.get_audit_trail("flag:k")
        actions = [e.action for e in trail]
        self.assertIn(AuditAction.CREATE, actions)
        self.assertIn(AuditAction.PROPAGATE, actions)

    def test_update_generates_audit(self):
        svc, *_ = _make_service()
        svc.set_feature_flag("flag:l", enabled=True, actor="alice")
        svc.set_feature_flag("flag:l", enabled=False, actor="bob")
        trail = svc.get_audit_trail("flag:l")
        actions = [e.action for e in trail]
        self.assertIn(AuditAction.UPDATE, actions)

    def test_rollback_generates_audit(self):
        svc, *_ = _make_service()
        v1 = svc.set_feature_flag("flag:m", enabled=True, actor="ops")
        svc.set_feature_flag("flag:m", enabled=False, actor="ops")
        svc.rollback("flag:m", v1.version_id, actor="ops")
        trail = svc.get_audit_trail("flag:m")
        actions = [e.action for e in trail]
        self.assertIn(AuditAction.ROLLBACK, actions)

    def test_audit_records_actor(self):
        svc, *_ = _make_service()
        svc.set_feature_flag("flag:n", enabled=True, actor="charlie")
        trail = svc.get_audit_trail("flag:n")
        actors = {e.actor for e in trail}
        self.assertIn("charlie", actors)

    def test_audit_records_environment(self):
        svc, *_ = _make_service(env=Environment.PROD)
        svc.set_feature_flag("flag:o", enabled=True, actor="ops")
        trail = svc.get_audit_trail("flag:o")
        envs = {e.environment for e in trail}
        self.assertEqual(envs, {Environment.PROD})

    def test_audit_limit(self):
        svc, *_ = _make_service()
        for i in range(20):
            svc.set_feature_flag(f"flag:bulk:{i}", enabled=True, actor="ops")
        trail = svc.get_audit_trail(limit=5)
        self.assertEqual(len(trail), 5)


# ---------------------------------------------------------------------------
# Environment-specific behaviour
# ---------------------------------------------------------------------------


class TestEnvironments(unittest.TestCase):
    def test_staging_env(self):
        svc, *_ = _make_service(env=Environment.STAGING)
        v = svc.set_feature_flag("flag:p", enabled=True, actor="dev")
        self.assertEqual(v.environment, Environment.STAGING)

    def test_uat_env(self):
        svc, *_ = _make_service(env=Environment.UAT)
        v = svc.set_feature_flag("flag:q", enabled=True, actor="qa")
        self.assertEqual(v.environment, Environment.UAT)

    def test_prod_env(self):
        svc, *_ = _make_service(env=Environment.PROD)
        v = svc.set_feature_flag("flag:r", enabled=True, actor="ops")
        self.assertEqual(v.environment, Environment.PROD)


# ---------------------------------------------------------------------------
# Platform scope (no specific validator)
# ---------------------------------------------------------------------------


class TestPlatformConfig(unittest.TestCase):
    def test_platform_scope_no_validation(self):
        svc, *_ = _make_service()
        v = svc.set_config(
            ConfigScope.PLATFORM, "platform:maintenance",
            {"active": False, "message": "Scheduled maintenance at 03:00 UTC"},
            actor="ops",
        )
        self.assertEqual(v.scope, ConfigScope.PLATFORM)


if __name__ == "__main__":
    unittest.main()
