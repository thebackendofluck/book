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

"""Tests for market_readiness_validator.py."""

from __future__ import annotations

import time
import unittest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from market_readiness_validator import (
    CheckStatus,
    EnvironmentProfile,
    MARKET_REGISTRY,
    validate_market,
)


def _good_licenses():
    return {
        code: {"license_id": f"LIC-{code}", "expires_at": time.time() + 365 * 86400}
        for code in MARKET_REGISTRY
    }


def _good_rg():
    return {
        "deposit_limits": True, "cool_off": True, "self_exclusion": True,
        "reality_check": True, "affordability_check": True,
        "mandatory_play_break": True, "spelpaus_integration": True,
        "monthly_limit_1000_eur": True, "panic_button": True,
        "no_autoplay": True, "spin_interval_5s": True,
        "geofence_verification": True, "cpf_verification": True,
    }


def _good_geofence():
    return {"provider": "geocomply", "boundary_tested": True}


def _good_test_results():
    return {"accounts_tested": 5, "full_cycle_passed": True}


class TestMarketReadiness(unittest.TestCase):
    def test_gb_ready(self):
        report = validate_market(
            "GB", _good_licenses(),
            ["visa", "mastercard", "paypal"],
            _good_rg(), _good_geofence(), _good_test_results(),
        )
        self.assertTrue(report.ready)
        self.assertEqual(report.blockers, 0)

    def test_us_nj_ready(self):
        report = validate_market(
            "US-NJ", _good_licenses(),
            ["visa", "ach", "paypal"],
            _good_rg(), _good_geofence(), _good_test_results(),
        )
        self.assertTrue(report.ready)

    def test_br_ready(self):
        report = validate_market(
            "BR", _good_licenses(),
            ["pix"],
            _good_rg(), _good_geofence(), _good_test_results(),
        )
        self.assertTrue(report.ready)

    def test_de_ready(self):
        report = validate_market(
            "DE", _good_licenses(),
            ["visa", "sofort", "giropay"],
            _good_rg(), _good_geofence(), _good_test_results(),
        )
        self.assertTrue(report.ready)


class TestLicenseValidation(unittest.TestCase):
    def test_missing_license_blocks(self):
        report = validate_market(
            "GB", {},
            ["visa", "mastercard", "paypal"],
            _good_rg(), _good_geofence(), _good_test_results(),
        )
        self.assertFalse(report.ready)
        self.assertGreater(report.blockers, 0)

    def test_expired_license_blocks(self):
        licenses = {"GB": {"license_id": "X", "expires_at": time.time() + 10 * 86400}}
        report = validate_market(
            "GB", licenses,
            ["visa", "mastercard", "paypal"],
            _good_rg(), _good_geofence(), _good_test_results(),
        )
        self.assertFalse(report.ready)

    def test_expiring_soon_warns(self):
        licenses = {"GB": {"license_id": "X", "expires_at": time.time() + 60 * 86400}}
        report = validate_market(
            "GB", licenses,
            ["visa", "mastercard", "paypal"],
            _good_rg(), _good_geofence(), _good_test_results(),
        )
        self.assertTrue(report.ready)  # warning, not blocker
        self.assertGreater(report.warnings, 0)


class TestGeofenceValidation(unittest.TestCase):
    def test_us_nj_no_geofence_blocks(self):
        report = validate_market(
            "US-NJ", _good_licenses(),
            ["visa", "ach", "paypal"],
            _good_rg(), {}, _good_test_results(),
        )
        self.assertFalse(report.ready)

    def test_gb_skips_geofence(self):
        report = validate_market(
            "GB", _good_licenses(),
            ["visa", "mastercard", "paypal"],
            _good_rg(), {}, _good_test_results(),
        )
        checks = [c for c in report.checks if c.category == "geofencing"]
        self.assertTrue(any(c.status == CheckStatus.SKIP for c in checks))

    def test_boundary_not_tested_blocks(self):
        report = validate_market(
            "US-NJ", _good_licenses(),
            ["visa", "ach", "paypal"],
            _good_rg(),
            {"provider": "geocomply", "boundary_tested": False},
            _good_test_results(),
        )
        self.assertFalse(report.ready)


class TestPaymentMethods(unittest.TestCase):
    def test_missing_required_method_blocks(self):
        report = validate_market(
            "GB", _good_licenses(),
            ["visa"],  # missing mastercard, paypal
            _good_rg(), _good_geofence(), _good_test_results(),
        )
        self.assertFalse(report.ready)

    def test_br_requires_pix(self):
        report = validate_market(
            "BR", _good_licenses(),
            ["visa", "mastercard"],  # missing pix
            _good_rg(), _good_geofence(), _good_test_results(),
        )
        self.assertFalse(report.ready)


class TestResponsibleGaming(unittest.TestCase):
    def test_missing_rg_control_blocks(self):
        rg = _good_rg()
        rg["deposit_limits"] = False
        report = validate_market(
            "GB", _good_licenses(),
            ["visa", "mastercard", "paypal"],
            rg, _good_geofence(), _good_test_results(),
        )
        self.assertFalse(report.ready)

    def test_de_requires_panic_button(self):
        rg = _good_rg()
        rg["panic_button"] = False
        report = validate_market(
            "DE", _good_licenses(),
            ["visa", "sofort", "giropay"],
            rg, _good_geofence(), _good_test_results(),
        )
        self.assertFalse(report.ready)


class TestTestAccounts(unittest.TestCase):
    def test_too_few_accounts_blocks(self):
        report = validate_market(
            "GB", _good_licenses(),
            ["visa", "mastercard", "paypal"],
            _good_rg(), _good_geofence(),
            {"accounts_tested": 1, "full_cycle_passed": True},
        )
        self.assertFalse(report.ready)

    def test_no_full_cycle_blocks(self):
        report = validate_market(
            "GB", _good_licenses(),
            ["visa", "mastercard", "paypal"],
            _good_rg(), _good_geofence(),
            {"accounts_tested": 5, "full_cycle_passed": False},
        )
        self.assertFalse(report.ready)


class TestUnknownMarket(unittest.TestCase):
    def test_raises(self):
        with self.assertRaises(ValueError):
            validate_market("XX", {}, [], {}, {}, {})


class TestReportSerialization(unittest.TestCase):
    def test_to_dict(self):
        report = validate_market(
            "MT", _good_licenses(),
            ["visa", "mastercard"],
            _good_rg(), _good_geofence(), _good_test_results(),
        )
        d = report.to_dict()
        self.assertIn("market", d)
        self.assertIn("ready", d)
        self.assertIn("checks", d)
        self.assertIsInstance(d["checks"], list)


if __name__ == "__main__":
    unittest.main()


class TestEnvironmentProfiles(unittest.TestCase):
    """Verify STAGING (advisory) vs PRODUCTION (strict) behavior."""

    def test_production_blocks_on_missing_license(self):
        report = validate_market(
            "GB", {},
            ["visa", "mastercard", "paypal"],
            _good_rg(), _good_geofence(), _good_test_results(),
            profile=EnvironmentProfile.PRODUCTION,
        )
        self.assertFalse(report.ready)
        self.assertGreater(report.blockers, 0)

    def test_staging_does_not_block_on_missing_license(self):
        report = validate_market(
            "GB", {},
            ["visa", "mastercard", "paypal"],
            _good_rg(), _good_geofence(), _good_test_results(),
            profile=EnvironmentProfile.STAGING,
        )
        self.assertTrue(report.ready)
        self.assertEqual(report.blockers, 0)
        self.assertGreater(report.warnings, 0)

    def test_staging_downgrades_blockers_to_warnings(self):
        report = validate_market(
            "GB", {},
            ["visa", "mastercard", "paypal"],
            _good_rg(), _good_geofence(), _good_test_results(),
            profile=EnvironmentProfile.STAGING,
        )
        for check in report.checks:
            if check.status == CheckStatus.FAIL:
                self.assertNotEqual(check.severity, "blocker",
                                    "STAGING must not have blocker failures")

    def test_production_missing_payment_blocks(self):
        report = validate_market(
            "BR", _good_licenses(),
            ["visa"],
            _good_rg(), _good_geofence(), _good_test_results(),
            profile=EnvironmentProfile.PRODUCTION,
        )
        self.assertFalse(report.ready)

    def test_staging_missing_payment_warns(self):
        report = validate_market(
            "BR", _good_licenses(),
            ["visa"],
            _good_rg(), _good_geofence(), _good_test_results(),
            profile=EnvironmentProfile.STAGING,
        )
        self.assertTrue(report.ready)
        self.assertGreater(report.warnings, 0)

    def test_production_default_profile(self):
        report = validate_market(
            "GB", {},
            ["visa", "mastercard", "paypal"],
            _good_rg(), _good_geofence(), _good_test_results(),
        )
        self.assertFalse(report.ready)

    def test_staging_advisory_detail_prefix(self):
        report = validate_market(
            "GB", {},
            ["visa", "mastercard", "paypal"],
            _good_rg(), _good_geofence(), _good_test_results(),
            profile=EnvironmentProfile.STAGING,
        )
        advisory_checks = [c for c in report.checks
                           if "[STAGING advisory]" in c.detail]
        self.assertGreater(len(advisory_checks), 0)
