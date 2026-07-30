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

"""Tests for supplier_enablement.py."""

from __future__ import annotations

import unittest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from supplier_enablement import (
    EnablementStatus,
    IntegrationType,
    SupplierEnablement,
    configure_callbacks,
    enable_supplier_for_operator,
    enablement_report,
    execute_test_rounds,
    production_sign_off,
    setup_credentials,
    SUPPLIER_CATALOGUE,
)


def _make_enablement(supplier_id: str = "pragmatic-play") -> SupplierEnablement:
    cat = SUPPLIER_CATALOGUE[supplier_id]
    return SupplierEnablement(
        supplier_id=supplier_id,
        operator_id="testop",
        supplier_name=cat["name"],
        integration_type=cat["integration_type"],
    )


class TestCredentialSetup(unittest.TestCase):
    def test_sets_credentials(self):
        e = setup_credentials(_make_enablement())
        self.assertIsNotNone(e.credentials)
        self.assertEqual(e.status, EnablementStatus.CREDENTIALS_SET)

    def test_vault_path_contains_operator(self):
        e = setup_credentials(_make_enablement())
        self.assertIn("testop", e.credentials.vault_path)

    def test_api_key_contains_supplier(self):
        e = setup_credentials(_make_enablement("evolution"))
        self.assertIn("evolution", e.credentials.api_key)


class TestCallbackConfig(unittest.TestCase):
    def test_seamless_requires_callback(self):
        e = setup_credentials(_make_enablement("pragmatic-play"))
        e = configure_callbacks(e, "testcasino.com")
        self.assertIsNotNone(e.callback)
        self.assertTrue(e.callback.verified)

    def test_callback_url_format(self):
        e = setup_credentials(_make_enablement("evolution"))
        e = configure_callbacks(e, "example.com")
        self.assertIn("example.com", e.callback.callback_url)
        self.assertIn("evolution", e.callback.callback_url)

    def test_transfer_skips_callback(self):
        e = setup_credentials(_make_enablement("novomatic"))
        e = configure_callbacks(e, "test.com")
        self.assertIsNone(e.callback)
        self.assertEqual(e.status, EnablementStatus.CALLBACKS_CONFIGURED)


class TestTestRounds(unittest.TestCase):
    def test_runs_all_test_games(self):
        e = setup_credentials(_make_enablement("netent"))
        e = configure_callbacks(e, "test.com")
        e = execute_test_rounds(e, "EUR")
        cat = SUPPLIER_CATALOGUE["netent"]
        self.assertEqual(len(e.test_rounds), len(cat["test_games"]))

    def test_all_rounds_settle(self):
        e = setup_credentials(_make_enablement())
        e = configure_callbacks(e, "test.com")
        e = execute_test_rounds(e)
        self.assertTrue(all(r.settled for r in e.test_rounds))

    def test_status_after_test_rounds(self):
        e = setup_credentials(_make_enablement())
        e = configure_callbacks(e, "test.com")
        e = execute_test_rounds(e)
        self.assertEqual(e.status, EnablementStatus.TEST_ROUND_PASSED)


class TestProductionSignOff(unittest.TestCase):
    def test_sign_off_succeeds(self):
        e = setup_credentials(_make_enablement())
        e = configure_callbacks(e, "test.com")
        e = execute_test_rounds(e)
        e = production_sign_off(e, "lead-engineer")
        self.assertEqual(e.status, EnablementStatus.PRODUCTION_READY)
        self.assertEqual(e.sign_off_by, "lead-engineer")

    def test_sign_off_without_test_rounds_fails(self):
        e = setup_credentials(_make_enablement())
        e = configure_callbacks(e, "test.com")
        # skip test rounds
        e = production_sign_off(e, "lead-engineer")
        self.assertNotEqual(e.status, EnablementStatus.PRODUCTION_READY)
        self.assertGreater(len(e.errors), 0)


class TestFullPipeline(unittest.TestCase):
    def test_pragmatic_play(self):
        e = enable_supplier_for_operator("pragmatic-play", "op1", "op1.com")
        self.assertEqual(e.status, EnablementStatus.PRODUCTION_READY)

    def test_evolution(self):
        e = enable_supplier_for_operator("evolution", "op2", "op2.com")
        self.assertEqual(e.status, EnablementStatus.PRODUCTION_READY)

    def test_novomatic_transfer(self):
        e = enable_supplier_for_operator("novomatic", "op3", "op3.com")
        self.assertEqual(e.status, EnablementStatus.PRODUCTION_READY)
        self.assertEqual(e.integration_type, IntegrationType.TRANSFER)

    def test_unknown_supplier_fails(self):
        e = enable_supplier_for_operator("ghost", "op1", "op1.com")
        self.assertEqual(e.status, EnablementStatus.FAILED)
        self.assertGreater(len(e.errors), 0)


class TestReport(unittest.TestCase):
    def test_report_structure(self):
        e = enable_supplier_for_operator("pragmatic-play", "op1", "op1.com")
        r = enablement_report(e)
        self.assertIn("supplier_id", r)
        self.assertIn("status", r)
        self.assertIn("test_rounds", r)
        self.assertIsInstance(r["test_rounds"], list)

    def test_report_test_round_fields(self):
        e = enable_supplier_for_operator("netent", "op1", "op1.com")
        r = enablement_report(e)
        for tr in r["test_rounds"]:
            self.assertIn("game", tr)
            self.assertIn("bet", tr)
            self.assertIn("settled", tr)


if __name__ == "__main__":
    unittest.main()
