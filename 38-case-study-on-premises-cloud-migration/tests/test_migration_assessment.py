# Companion code for "The Backend of Luck" - Chapter 38, Case Study.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Behavioral tests for Chapter 38 — Cloud Migration Assessment."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "cloud_migration"))

from migration_assessment import (
    CloudMigrationArchitecture,
    MigrationAssessment,
    MigrationBestPractices,
)


class TestCloudMigrationArchitecture:
    """Validate target architecture design produces valid structure."""

    def test_architecture_contains_required_layers(self):
        config = {"provider": "aws", "region": "eu-west-1"}
        arch = CloudMigrationArchitecture(config)
        target = arch.target_architecture

        for layer in ("compute", "storage", "networking", "security"):
            assert layer in target, f"Missing architecture layer: {layer}"

    def test_architecture_specifies_multi_region(self):
        config = {"provider": "aws"}
        arch = CloudMigrationArchitecture(config)
        compute = arch.target_architecture["compute"]

        assert len(compute["secondary_regions"]) >= 1
        assert compute["kubernetes_clusters"] >= 1

    def test_security_layer_has_encryption(self):
        config = {}
        arch = CloudMigrationArchitecture(config)
        security = arch.target_architecture["security"]

        assert "encryption_at_rest" in security
        assert "encryption_in_transit" in security


class TestMigrationAssessment:
    """Validate assessment scoring and strategy recommendation."""

    def test_readiness_score_is_valid_fraction(self):
        infra = {"servers": 50, "databases": 10}
        assessment = MigrationAssessment(infra)
        score = assessment._calculate_readiness_score({})
        assert 0.0 <= score <= 1.0

    def test_complexity_returns_valid_level(self):
        infra = {"servers": 50}
        assessment = MigrationAssessment(infra)
        complexity = assessment._calculate_complexity_score()
        assert complexity in ("low", "medium", "high", "very_high")

    def test_migration_timeline_is_positive(self):
        infra = {}
        assessment = MigrationAssessment(infra)
        timeline = assessment._estimate_migration_timeline()
        assert timeline["minimum_months"] > 0
        assert timeline["minimum_months"] <= timeline["recommended_months"]
        assert timeline["recommended_months"] <= timeline["maximum_months"]


class TestMigrationBestPractices:
    """Validate migration checklist and ROI calculator."""

    def test_checklist_has_all_phases(self):
        checklist = MigrationBestPractices.create_migration_checklist()
        assert "pre_migration" in checklist
        assert "during_migration" in checklist
        assert "post_migration" in checklist
        for phase, items in checklist.items():
            assert len(items) > 0, f"Phase {phase} has no items"

    def test_roi_calculation_produces_valid_output(self):
        current_costs = {
            "infrastructure_annual": 2_000_000,
            "operational_annual": 800_000,
            "personnel_annual": 1_200_000,
        }
        projected_benefits = {
            "cloud_infrastructure_annual": 1_200_000,
            "cloud_operational_annual": 500_000,
            "cloud_personnel_annual": 1_000_000,
            "reliability_uplift_annual": 300_000,
            "scalability_uplift_annual": 200_000,
            "time_to_market_benefit_annual": 150_000,
            "migration_costs_total": 1_500_000,
        }
        roi = MigrationBestPractices.calculate_migration_roi(
            current_costs, projected_benefits
        )
        assert roi["roi_percentage"] > 0
        assert roi["payback_period_months"] > 0
        assert roi["annual_cost_savings"] > 0
        assert roi["total_annual_benefit"] > roi["annual_cost_savings"]
