# Companion code for "The Backend of Luck" - Chapter 40, Case Study.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Behavioral tests for Chapter 40 — New Market Launch (Ontario Compliance)."""

import sys
import os

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "market_launch")
)

from regulatory_compliance import OntarioRegulatoryCompliance


class TestOntarioRegulatoryCompliance:
    """Validate Ontario regulatory framework structure and values."""

    def _make_compliance(self):
        config = {"operator_name": "AcmeToCasino", "jurisdiction": "Ontario"}
        return OntarioRegulatoryCompliance(operator_config=config)

    def test_framework_contains_all_required_sections(self):
        compliance = self._make_compliance()
        framework = compliance.compliance_framework

        required_sections = [
            "licensing_requirements",
            "responsible_gaming_requirements",
            "technical_requirements",
            "content_requirements",
        ]
        for section in required_sections:
            assert section in framework, f"Missing section: {section}"

    def test_minimum_age_is_19_for_ontario(self):
        compliance = self._make_compliance()
        tech = compliance.compliance_framework["technical_requirements"]
        assert tech["age_verification"]["minimum_age"] == 19

    def test_geo_verification_is_mandatory(self):
        compliance = self._make_compliance()
        tech = compliance.compliance_framework["technical_requirements"]
        assert tech["geo_verification"]["mandatory"] is True
        assert len(tech["geo_verification"]["verification_methods"]) >= 2

    def test_responsible_gaming_deposit_limits_are_positive(self):
        compliance = self._make_compliance()
        rg = compliance.compliance_framework["responsible_gaming_requirements"]
        limits = rg["deposit_limits"]
        assert limits["daily_default"] > 0
        assert limits["weekly_default"] > limits["daily_default"]
        assert limits["monthly_default"] > limits["weekly_default"]

    def test_self_exclusion_is_mandatory(self):
        compliance = self._make_compliance()
        rg = compliance.compliance_framework["responsible_gaming_requirements"]
        assert rg["self_exclusion_system"]["mandatory_integration"] is True
        assert rg["self_exclusion_system"]["permanent_exclusion"] is True

    def test_data_localization_requires_canada(self):
        compliance = self._make_compliance()
        tech = compliance.compliance_framework["technical_requirements"]
        assert tech["data_localization"]["player_data_residency"] == "Canada"

    def test_french_language_support_mandatory(self):
        compliance = self._make_compliance()
        content = compliance.compliance_framework["content_requirements"]
        assert content["language_support"]["french"] is True
        assert content["language_support"]["mandatory_french_content"] is True
