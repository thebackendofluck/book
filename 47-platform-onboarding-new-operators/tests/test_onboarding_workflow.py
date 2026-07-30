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

"""Tests for onboarding_workflow.py."""

from __future__ import annotations

import unittest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from onboarding_workflow import (
    ChecklistItem,
    DeploymentModel,
    OnboardingStage,
    OperatorProfile,
    StageStatus,
    check_dns_records,
    check_go_live_readiness,
    enable_suppliers,
    provision_databases,
    provision_ssl_certificates,
    run_onboarding,
    session_report,
    validate_jurisdiction_config,
)


def _make_operator(**overrides) -> OperatorProfile:
    defaults = dict(
        operator_id="testop",
        operator_name="TestCasino",
        domain="testcasino.com",
        jurisdictions=["GB", "MT"],
        deployment_model=DeploymentModel.CLOUD,
        suppliers=["pragmatic-play", "evolution"],
        payment_methods=["visa", "mastercard"],
        primary_currency="EUR",
        region="eu-west-1",
        sla_tier="standard",
    )
    defaults.update(overrides)
    return OperatorProfile(**defaults)


class TestDNSSetup(unittest.TestCase):
    def test_produces_items(self):
        items = check_dns_records(_make_operator())
        self.assertGreater(len(items), 0)
        self.assertTrue(all(i.stage == OnboardingStage.DNS_SETUP for i in items))

    def test_all_pass(self):
        items = check_dns_records(_make_operator())
        self.assertTrue(all(i.status == StageStatus.PASSED for i in items))

    def test_includes_email_auth(self):
        items = check_dns_records(_make_operator())
        names = [i.name for i in items]
        self.assertTrue(any("email" in n.lower() or "auth" in n.lower() for n in names))


class TestSSLProvisioning(unittest.TestCase):
    def test_produces_certs_for_all_domains(self):
        items = provision_ssl_certificates(_make_operator())
        self.assertGreaterEqual(len(items), 4)

    def test_all_pass(self):
        items = provision_ssl_certificates(_make_operator())
        self.assertTrue(all(i.status == StageStatus.PASSED for i in items))


class TestDatabaseProvisioning(unittest.TestCase):
    def test_creates_postgres_and_redis(self):
        items = provision_databases(_make_operator())
        names = [i.name for i in items]
        self.assertTrue(any("PostgreSQL" in n for n in names))
        self.assertTrue(any("Redis" in n for n in names))

    def test_cloud_creates_kv_namespace(self):
        items = provision_databases(_make_operator(deployment_model=DeploymentModel.CLOUD))
        names = [i.name for i in items]
        self.assertTrue(any("KV" in n for n in names))

    def test_on_prem_no_kv_namespace(self):
        items = provision_databases(_make_operator(deployment_model=DeploymentModel.ON_PREMISES))
        names = [i.name for i in items]
        self.assertFalse(any("KV" in n for n in names))

    def test_production_sla_gets_read_replica(self):
        items = provision_databases(_make_operator(sla_tier="production"))
        names = [i.name for i in items]
        self.assertTrue(any("replica" in n.lower() for n in names))

    def test_standard_sla_no_read_replica(self):
        items = provision_databases(_make_operator(sla_tier="standard"))
        names = [i.name for i in items]
        self.assertFalse(any("replica" in n.lower() for n in names))


class TestSupplierEnablement(unittest.TestCase):
    def test_known_suppliers_pass(self):
        op = _make_operator(suppliers=["pragmatic-play", "netent"])
        items = enable_suppliers(op)
        self.assertTrue(all(i.status == StageStatus.PASSED for i in items))

    def test_unknown_supplier_fails(self):
        op = _make_operator(suppliers=["unknown-vendor"])
        items = enable_suppliers(op)
        failed = [i for i in items if i.status == StageStatus.FAILED]
        self.assertGreater(len(failed), 0)

    def test_callback_url_contains_domain(self):
        op = _make_operator(suppliers=["evolution"])
        items = enable_suppliers(op)
        callback_items = [i for i in items if "callback" in i.name.lower()]
        self.assertTrue(any(op.domain in i.description for i in callback_items))


class TestJurisdictionValidation(unittest.TestCase):
    def test_gb_produces_checks(self):
        op = _make_operator(jurisdictions=["GB"])
        items = validate_jurisdiction_config(op)
        self.assertGreater(len(items), 0)

    def test_us_nj_includes_geofencing(self):
        op = _make_operator(jurisdictions=["US-NJ"])
        items = validate_jurisdiction_config(op)
        names = [i.name for i in items]
        self.assertTrue(any("geofenc" in n.lower() for n in names))

    def test_mt_no_geofencing(self):
        op = _make_operator(jurisdictions=["MT"])
        items = validate_jurisdiction_config(op)
        names = [i.name for i in items]
        self.assertFalse(any("geofenc" in n.lower() for n in names))


class TestGoLiveReadiness(unittest.TestCase):
    def test_full_onboarding_is_ready(self):
        session = run_onboarding(_make_operator())
        self.assertTrue(session.ready_for_go_live)

    def test_no_payment_methods_blocks_go_live(self):
        session = run_onboarding(_make_operator(payment_methods=[]))
        go_live_items = [i for i in session.checklist
                         if i.stage == OnboardingStage.GO_LIVE_READINESS]
        payment_items = [i for i in go_live_items if "payment" in i.name.lower()]
        self.assertTrue(any(i.status == StageStatus.FAILED for i in payment_items))


class TestSessionReport(unittest.TestCase):
    def test_report_structure(self):
        session = run_onboarding(_make_operator())
        report = session_report(session)
        self.assertIn("session_id", report)
        self.assertIn("operator_id", report)
        self.assertIn("ready_for_go_live", report)
        self.assertIn("stages", report)
        self.assertIn("checklist_summary", report)
        self.assertIn("checklist", report)

    def test_report_counts_match(self):
        session = run_onboarding(_make_operator())
        report = session_report(session)
        summary = report["checklist_summary"]
        total = summary["passed"] + summary["failed"] + summary["skipped"]
        self.assertLessEqual(total, summary["total"])


class TestEndToEnd(unittest.TestCase):
    def test_cloud_onboarding(self):
        session = run_onboarding(_make_operator(
            deployment_model=DeploymentModel.CLOUD,
        ))
        self.assertTrue(session.ready_for_go_live)
        self.assertEqual(len(session.stages), 6)

    def test_hybrid_onboarding(self):
        session = run_onboarding(_make_operator(
            deployment_model=DeploymentModel.HYBRID,
        ))
        self.assertTrue(session.ready_for_go_live)

    def test_on_prem_onboarding(self):
        session = run_onboarding(_make_operator(
            deployment_model=DeploymentModel.ON_PREMISES,
        ))
        self.assertTrue(session.ready_for_go_live)


if __name__ == "__main__":
    unittest.main()
