#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 43, Future Technology & Innovation in iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Tests for AI governance modules.

Covers the full model lifecycle, bias detection with synthetic data,
decision logging/retrieval, conformity checks, fairness metrics, and
transparency report generation.
"""

import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

# Ensure the parent package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from model_registry import ModelRegistry, VALID_RISK_TIERS, VALID_STATUSES
from bias_audit import BiasAuditor
from decision_logger import AIDecisionLogger, VALID_DECISION_TYPES
from conformity_checker import ConformityAssessment
from fairness_metrics import (
    demographic_parity_difference,
    equalized_odds_difference,
    calibration_error_by_group,
    disparate_impact_ratio,
)
from transparency_report import (
    TransparencyReportGenerator,
    PlayerReportData,
    RegulatorReportData,
    ModelCard,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test_registry.db"


@pytest.fixture
def registry(db_path):
    r = ModelRegistry(db_path=db_path)
    yield r
    r.close()


@pytest.fixture
def decision_db(tmp_path):
    db = tmp_path / "test_decisions.db"
    logger = AIDecisionLogger(db_path=db)
    yield logger
    logger.close()


@pytest.fixture
def rng():
    return np.random.default_rng(42)


@pytest.fixture
def synthetic_data(rng):
    """Generate synthetic prediction data with slight bias."""
    n = 500
    groups = rng.choice(["A", "B", "C"], size=n, p=[0.5, 0.3, 0.2])
    labels = rng.integers(0, 2, size=n)
    predictions = labels.copy()
    # Inject bias: group A gets more false positives
    a_neg = np.where((groups == "A") & (labels == 0))[0]
    flip = rng.choice(a_neg, size=min(30, len(a_neg)), replace=False)
    predictions[flip] = 1
    probabilities = np.clip(predictions + rng.normal(0, 0.15, n), 0, 1)
    return predictions, labels, groups, probabilities


# ---------------------------------------------------------------------------
# Model Registry Tests
# ---------------------------------------------------------------------------

class TestModelRegistry:
    def test_register_and_get(self, registry):
        rec = registry.register(
            name="test_model", version="1.0", risk_tier="high",
            purpose="Unit test model",
        )
        assert rec.model_id
        assert rec.status == "draft"
        fetched = registry.get(rec.model_id)
        assert fetched.name == "test_model"

    def test_register_prohibited_raises(self, registry):
        with pytest.raises(ValueError, match="Prohibited"):
            registry.register(
                name="banned", risk_tier="prohibited",
                purpose="Should fail",
            )

    def test_register_invalid_tier_raises(self, registry):
        with pytest.raises(ValueError, match="risk_tier"):
            registry.register(name="bad", risk_tier="extreme", purpose="x")

    def test_register_duplicate_raises(self, registry):
        registry.register(name="dup", version="1.0",
                          risk_tier="minimal", purpose="first")
        with pytest.raises(Exception):
            registry.register(name="dup", version="1.0",
                              risk_tier="minimal", purpose="second")

    def test_list_models(self, registry):
        registry.register(name="m1", risk_tier="minimal", purpose="a")
        registry.register(name="m2", risk_tier="high", purpose="b")
        models = registry.list_models()
        assert len(models) == 2

    def test_list_models_by_status(self, registry):
        registry.register(name="m1", risk_tier="minimal", purpose="a")
        models = registry.list_models(status="draft")
        assert len(models) == 1
        assert registry.list_models(status="deployed") == []

    def test_status_transition_draft_to_review(self, registry):
        rec = registry.register(
            name="flow", risk_tier="minimal", purpose="test",
        )
        updated = registry.transition_status(
            rec.model_id, "review", "tester",
        )
        assert updated.status == "review"

    def test_invalid_transition_raises(self, registry):
        rec = registry.register(
            name="flow", risk_tier="minimal", purpose="test",
        )
        with pytest.raises(ValueError, match="Cannot transition"):
            registry.transition_status(
                rec.model_id, "deployed", "tester",
            )

    def test_high_risk_approval_requires_reason(self, registry):
        rec = registry.register(
            name="hr", risk_tier="high", purpose="high risk model",
        )
        registry.transition_status(rec.model_id, "review", "tester")
        with pytest.raises(ValueError, match="reason"):
            registry.transition_status(
                rec.model_id, "approved", "tester",
            )

    def test_high_risk_approval_with_reason(self, registry):
        rec = registry.register(
            name="hr2", risk_tier="high", purpose="high risk model",
        )
        registry.transition_status(rec.model_id, "review", "tester")
        updated = registry.transition_status(
            rec.model_id, "approved", "tester",
            reason="Reviewed bias audit and performance metrics",
        )
        assert updated.status == "approved"

    def test_full_lifecycle(self, registry):
        rec = registry.register(
            name="lifecycle", risk_tier="limited", purpose="test",
        )
        registry.transition_status(rec.model_id, "review", "dev")
        registry.transition_status(rec.model_id, "approved", "lead")
        registry.transition_status(rec.model_id, "deployed", "ops")
        registry.transition_status(rec.model_id, "retired", "ops")
        final = registry.get(rec.model_id)
        assert final.status == "retired"

    def test_audit_trail(self, registry):
        rec = registry.register(
            name="audited", risk_tier="minimal", purpose="test",
        )
        registry.transition_status(rec.model_id, "review", "dev")
        trail = registry.get_audit_trail(rec.model_id)
        assert len(trail) == 2  # register + status_change
        assert trail[0]["action"] == "register"
        assert trail[1]["action"] == "status_change"

    def test_get_nonexistent_raises(self, registry):
        with pytest.raises(KeyError):
            registry.get("nonexistent-id")


# ---------------------------------------------------------------------------
# Bias Audit Tests
# ---------------------------------------------------------------------------

class TestBiasAudit:
    def test_audit_runs(self, synthetic_data):
        preds, labels, groups, probs = synthetic_data
        auditor = BiasAuditor()
        report = auditor.audit(
            predictions=preds, groups=groups, labels=labels,
            probabilities=probs, model_name="test", model_version="1.0",
        )
        assert report.model_name == "test"
        assert report.sample_size == len(preds)
        assert len(report.metric_results) == 4

    def test_audit_without_labels(self, synthetic_data):
        preds, _, groups, _ = synthetic_data
        auditor = BiasAuditor()
        report = auditor.audit(predictions=preds, groups=groups)
        # Only demographic parity and disparate impact
        assert len(report.metric_results) == 2

    def test_audit_detects_bias(self, rng):
        """Create heavily biased predictions and verify detection."""
        n = 500
        groups = np.array(["majority"] * 400 + ["minority"] * 100)
        preds = np.zeros(n, dtype=int)
        preds[:200] = 1  # 50% positive rate for majority
        preds[400:410] = 1  # 10% positive rate for minority
        auditor = BiasAuditor(disparity_threshold=0.20)
        report = auditor.audit(predictions=preds, groups=groups,
                               model_name="biased")
        assert not report.overall_pass
        assert len(report.flagged_metrics) > 0

    def test_report_json_serializable(self, synthetic_data):
        preds, labels, groups, probs = synthetic_data
        auditor = BiasAuditor()
        report = auditor.audit(
            predictions=preds, groups=groups, labels=labels,
            probabilities=probs,
        )
        j = auditor.report_to_json(report)
        parsed = json.loads(j)
        assert "metric_results" in parsed


# ---------------------------------------------------------------------------
# Decision Logger Tests
# ---------------------------------------------------------------------------

class TestDecisionLogger:
    def test_log_and_query_by_player(self, decision_db):
        did = decision_db.log_decision(
            model_name="aml", model_version="1.0",
            input_features={"amount": 500},
            output="clear", affected_player_id="p1",
            decision_type="aml_flag",
        )
        assert did
        results = decision_db.query_by_player("p1")
        assert len(results) == 1
        assert results[0]["output"] == "clear"

    def test_log_invalid_type_raises(self, decision_db):
        with pytest.raises(ValueError, match="decision_type"):
            decision_db.log_decision(
                model_name="x", model_version="1.0",
                input_features={}, output="y",
                affected_player_id="p1",
                decision_type="invalid_type",
            )

    def test_query_by_model(self, decision_db):
        for i in range(5):
            decision_db.log_decision(
                model_name="scorer", model_version="2.0",
                input_features={"x": i}, output=str(i),
                affected_player_id=f"p{i}",
                decision_type="rg_trigger",
            )
        results = decision_db.query_by_model("scorer")
        assert len(results) == 5

    def test_query_by_date_range(self, decision_db):
        decision_db.log_decision(
            model_name="m", model_version="1.0",
            input_features={}, output="ok",
            affected_player_id="p1", decision_type="kyc_verify",
        )
        results = decision_db.query_by_date_range(
            "2000-01-01", "2099-12-31",
        )
        assert len(results) >= 1

    def test_export_csv(self, decision_db):
        decision_db.log_decision(
            model_name="m", model_version="1.0",
            input_features={"a": 1}, output="x",
            affected_player_id="p1", decision_type="bonus_offer",
        )
        decs = decision_db.query_by_player("p1")
        csv_out = decision_db.export_csv(decs)
        assert "decision_id" in csv_out
        assert "bonus_offer" in csv_out

    def test_export_json(self, decision_db):
        decision_db.log_decision(
            model_name="m", model_version="1.0",
            input_features={}, output="y",
            affected_player_id="p1", decision_type="fraud_alert",
        )
        decs = decision_db.query_by_player("p1")
        j = decision_db.export_json(decs)
        parsed = json.loads(j)
        assert len(parsed) == 1

    def test_input_features_hashed(self, decision_db):
        decision_db.log_decision(
            model_name="m", model_version="1.0",
            input_features={"ssn": "123-45-6789", "name": "John"},
            output="ok", affected_player_id="p1",
            decision_type="kyc_verify",
        )
        decs = decision_db.query_by_player("p1")
        # input_hash should be a hex digest, not the raw features
        assert len(decs[0]["input_hash"]) == 64  # SHA-256 hex length

    def test_retention_enforcement(self, decision_db):
        decision_db.log_decision(
            model_name="m", model_version="1.0",
            input_features={}, output="ok",
            affected_player_id="p1", decision_type="aml_flag",
        )
        # With 0 days retention, everything should be deleted
        deleted = decision_db.enforce_retention(max_age_days=0)
        assert deleted == 1


# ---------------------------------------------------------------------------
# Conformity Checker Tests
# ---------------------------------------------------------------------------

class TestConformityChecker:
    def test_full_compliance(self, db_path):
        registry = ModelRegistry(db_path=db_path)
        rec = registry.register(
            name="compliant", risk_tier="high",
            purpose="AML scoring",
            training_date="2025-01-01",
            dataset_description="1M transactions",
            performance_metrics={"accuracy": 0.95},
            bias_audit_results={"status": "pass"},
        )
        checker = ConformityAssessment(registry)
        report = checker.assess(
            rec.model_id, assessor="test",
            has_logging=True,
            has_transparency_notice=True,
            has_human_oversight=True,
        )
        assert report.compliance_score == 100.0
        assert report.failed_checks == 0
        registry.close()

    def test_missing_everything(self, db_path):
        registry = ModelRegistry(db_path=db_path)
        rec = registry.register(
            name="incomplete", risk_tier="high", purpose="test",
        )
        checker = ConformityAssessment(registry)
        report = checker.assess(rec.model_id, assessor="test")
        assert report.compliance_score < 100.0
        assert report.failed_checks > 0
        assert len(report.missing_items) > 0
        assert len(report.remediation_steps) > 0
        registry.close()

    def test_minimal_risk_auto_passes(self, db_path):
        registry = ModelRegistry(db_path=db_path)
        rec = registry.register(
            name="simple", risk_tier="minimal", purpose="test",
        )
        checker = ConformityAssessment(registry)
        report = checker.assess(rec.model_id)
        assert report.compliance_score == 100.0
        registry.close()

    def test_low_accuracy_fails_art15(self, db_path):
        registry = ModelRegistry(db_path=db_path)
        rec = registry.register(
            name="weak", risk_tier="high", purpose="test",
            training_date="2025-01-01",
            dataset_description="small dataset",
            performance_metrics={"accuracy": 0.60},
            bias_audit_results={"status": "pass"},
        )
        checker = ConformityAssessment(registry)
        report = checker.assess(
            rec.model_id, has_logging=True,
            has_transparency_notice=True,
            has_human_oversight=True,
        )
        art15 = [c for c in report.checks if c.article == "Art. 15"]
        assert len(art15) == 1
        assert art15[0].status == "fail"
        registry.close()

    def test_report_json(self, db_path):
        registry = ModelRegistry(db_path=db_path)
        rec = registry.register(
            name="json_test", risk_tier="limited", purpose="test",
        )
        checker = ConformityAssessment(registry)
        report = checker.assess(rec.model_id)
        j = checker.report_to_json(report)
        parsed = json.loads(j)
        assert "checks" in parsed
        registry.close()


# ---------------------------------------------------------------------------
# Fairness Metrics Tests
# ---------------------------------------------------------------------------

class TestFairnessMetrics:
    def test_demographic_parity_equal_groups(self):
        preds = np.array([1, 0, 1, 0, 1, 0])
        groups = np.array(["A", "A", "A", "B", "B", "B"])
        result = demographic_parity_difference(preds, groups)
        # A: 2/3 positive, B: 1/3 positive => diff = 1/3
        assert result.metric_name == "demographic_parity_difference"
        assert 0.3 <= result.value <= 0.35

    def test_demographic_parity_perfect(self):
        preds = np.array([1, 0, 1, 0])
        groups = np.array(["A", "A", "B", "B"])
        result = demographic_parity_difference(preds, groups)
        assert result.value == 0.0
        assert result.passed

    def test_equalized_odds(self, rng):
        n = 400
        preds = rng.integers(0, 2, size=n)
        labels = rng.integers(0, 2, size=n)
        groups = rng.choice(["X", "Y"], size=n)
        result = equalized_odds_difference(preds, labels, groups)
        assert "tpr_X" in result.groups or "tpr_Y" in result.groups

    def test_calibration_error(self, rng):
        n = 300
        probs = rng.random(n)
        labels = (probs > 0.5).astype(int)
        groups = rng.choice(["G1", "G2"], size=n)
        result = calibration_error_by_group(probs, labels, groups)
        assert result.metric_name == "calibration_error_by_group"

    def test_disparate_impact_fair(self):
        preds = np.array([1, 1, 0, 1, 1, 0])
        groups = np.array(["A", "A", "A", "B", "B", "B"])
        result = disparate_impact_ratio(preds, groups)
        # Both groups: 2/3 => ratio = 1.0
        assert result.value == 1.0
        assert result.passed

    def test_disparate_impact_unfair(self):
        preds = np.array([1, 1, 1, 1, 0, 0, 0, 0, 0, 1])
        groups = np.array(["A", "A", "A", "A", "B", "B", "B", "B", "B", "B"])
        result = disparate_impact_ratio(preds, groups)
        # A: 4/4=1.0, B: 1/6=0.167 => ratio = 0.167
        assert result.value < 0.80
        assert not result.passed


# ---------------------------------------------------------------------------
# Transparency Report Tests
# ---------------------------------------------------------------------------

class TestTransparencyReport:
    def test_player_report_contains_rights(self):
        gen = TransparencyReportGenerator()
        data = PlayerReportData(
            player_id="P001",
            platform_name="TestCasino",
            report_date="2025-06-15",
        )
        report = gen.generate_player_report(data)
        assert "Your Rights" in report
        assert "human review" in report.lower()

    def test_player_report_with_models(self):
        gen = TransparencyReportGenerator()
        data = PlayerReportData(
            player_id="P002",
            platform_name="TestCasino",
            report_date="2025-06-15",
            models_affecting_player=[
                ModelCard(name="aml_detector", version="1.0",
                          purpose="AML scoring", risk_tier="high"),
            ],
        )
        report = gen.generate_player_report(data)
        assert "Financial Safety Monitor" in report

    def test_regulator_report_structure(self):
        gen = TransparencyReportGenerator()
        data = RegulatorReportData(
            platform_name="TestCasino",
            report_date="2025-06-15",
            reporting_period="2025-Q2",
            models=[
                ModelCard(name="aml", version="1.0", purpose="AML",
                          risk_tier="high", accuracy=0.92,
                          bias_status="pass"),
            ],
            total_decisions=1000,
            decisions_by_type={"aml_flag": 500, "rg_trigger": 500},
        )
        report = gen.generate_regulator_report(data)
        assert "AI Systems Inventory" in report
        assert "Decision Volume" in report
        assert "1,000" in report
        assert "Article 13" in report

    def test_regulator_report_with_bias_audits(self):
        gen = TransparencyReportGenerator()
        data = RegulatorReportData(
            platform_name="TestCasino",
            report_date="2025-06-15",
            reporting_period="2025-Q2",
            bias_audit_summaries=[
                {"model_name": "aml", "overall_pass": True,
                 "audit_timestamp": "2025-06-01",
                 "sample_size": 2000, "flagged_metrics": []},
            ],
        )
        report = gen.generate_regulator_report(data)
        assert "Bias Audit" in report
        assert "PASS" in report
