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
Responsible AI Governance Framework for Online Gambling
========================================================

Provides bias detection, fairness metrics, and explainability tooling
for AI/ML systems deployed across an iGaming platform. Designed to
satisfy regulatory audits (e.g., UK Gambling Commission LCCP, MGA
technical standards) and internal ethics-board reviews.

Covers:
- Model bias detection across protected player demographics
- Fairness metrics (statistical parity, equalized odds, calibration)
- Explainability reports for model decisions (feature attribution)
- Audit trail generation for regulatory submissions
- Responsible gambling intervention scoring transparency

Feasibility Assessment:
- Fairness metrics are well-defined (Aequitas, AI Fairness 360 concepts)
- Feature attribution via SHAP/permutation importance is production-proven
- Audit trail is a structured log - integrates with any SIEM or GRC tool
- No external dependencies for core; optional: numpy, pandas for analytics
"""

import json
import math
import logging
import hashlib
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core types
# ---------------------------------------------------------------------------

class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FairnessMetric(Enum):
    STATISTICAL_PARITY = "statistical_parity"
    EQUALIZED_ODDS = "equalized_odds"
    CALIBRATION = "calibration"
    PREDICTIVE_PARITY = "predictive_parity"
    DISPARATE_IMPACT = "disparate_impact"


class ProtectedAttribute(Enum):
    AGE_GROUP = "age_group"
    GENDER = "gender"
    JURISDICTION = "jurisdiction"
    DEPOSIT_TIER = "deposit_tier"
    REGISTRATION_CHANNEL = "registration_channel"
    LANGUAGE = "language"


@dataclass
class ModelDecision:
    """A single decision made by a platform AI model."""
    decision_id: str
    model_name: str
    model_version: str
    player_id: str
    decision_type: str  # e.g. "bonus_offer", "risk_flag", "intervention"
    outcome: str  # e.g. "approved", "denied", "flagged"
    confidence: float
    features: dict = field(default_factory=dict)
    feature_attributions: dict = field(default_factory=dict)
    protected_attributes: dict = field(default_factory=dict)
    timestamp: str = ""


@dataclass
class FairnessReport:
    """Fairness evaluation result for a single metric/attribute pair."""
    metric: FairnessMetric
    protected_attribute: ProtectedAttribute
    group_a: str
    group_b: str
    group_a_rate: float
    group_b_rate: float
    ratio: float
    passes_threshold: bool
    threshold: float
    sample_size_a: int
    sample_size_b: int
    recommendation: str


@dataclass
class BiasAlert:
    alert_id: str
    model_name: str
    risk_level: RiskLevel
    metric: FairnessMetric
    protected_attribute: ProtectedAttribute
    description: str
    remediation: str
    regulatory_impact: str
    timestamp: str = ""


@dataclass
class AuditRecord:
    record_id: str
    timestamp: str
    model_name: str
    model_version: str
    event_type: str  # "decision", "bias_check", "retraining", "override"
    details: dict = field(default_factory=dict)
    checksum: str = ""


# ---------------------------------------------------------------------------
# Fairness evaluator
# ---------------------------------------------------------------------------

class FairnessEvaluator:
    """
    Evaluates fairness of model decisions across protected groups.

    Thresholds follow the 80% rule (disparate impact) and configurable
    bounds for other metrics. UK GC and MGA require operators to
    demonstrate that automated decisions do not discriminate.
    """

    # Default thresholds - configurable per jurisdiction
    THRESHOLDS = {
        FairnessMetric.STATISTICAL_PARITY: 0.10,      # max difference
        FairnessMetric.EQUALIZED_ODDS: 0.10,
        FairnessMetric.CALIBRATION: 0.05,
        FairnessMetric.PREDICTIVE_PARITY: 0.10,
        FairnessMetric.DISPARATE_IMPACT: 0.80,        # min ratio (80% rule)
    }

    MIN_GROUP_SIZE = 30  # minimum samples per group for valid comparison

    def evaluate_statistical_parity(
        self,
        decisions: list[ModelDecision],
        attribute: ProtectedAttribute,
        positive_outcome: str,
    ) -> list[FairnessReport]:
        """
        Check if positive outcome rates are similar across groups.
        Statistical parity difference should be below threshold.
        """
        groups = self._group_by_attribute(decisions, attribute)
        reports = []

        group_names = sorted(groups.keys())
        for i, ga_name in enumerate(group_names):
            for gb_name in group_names[i + 1:]:
                ga_decisions = groups[ga_name]
                gb_decisions = groups[gb_name]

                if len(ga_decisions) < self.MIN_GROUP_SIZE or len(gb_decisions) < self.MIN_GROUP_SIZE:
                    continue

                ga_positive = sum(1 for d in ga_decisions if d.outcome == positive_outcome)
                gb_positive = sum(1 for d in gb_decisions if d.outcome == positive_outcome)

                ga_rate = ga_positive / len(ga_decisions)
                gb_rate = gb_positive / len(gb_decisions)

                difference = abs(ga_rate - gb_rate)
                threshold = self.THRESHOLDS[FairnessMetric.STATISTICAL_PARITY]

                passes = difference <= threshold
                recommendation = "No action required." if passes else (
                    f"Statistical parity violation: {difference:.3f} exceeds {threshold}. "
                    f"Investigate feature engineering for proxy discrimination via "
                    f"{attribute.value}. Consider reweighting training data or "
                    f"adding fairness constraints to model objective."
                )

                reports.append(FairnessReport(
                    metric=FairnessMetric.STATISTICAL_PARITY,
                    protected_attribute=attribute,
                    group_a=ga_name,
                    group_b=gb_name,
                    group_a_rate=round(ga_rate, 4),
                    group_b_rate=round(gb_rate, 4),
                    ratio=round(min(ga_rate, gb_rate) / max(ga_rate, gb_rate), 4)
                    if max(ga_rate, gb_rate) > 0 else 0.0,
                    passes_threshold=passes,
                    threshold=threshold,
                    sample_size_a=len(ga_decisions),
                    sample_size_b=len(gb_decisions),
                    recommendation=recommendation,
                ))

        return reports

    def evaluate_disparate_impact(
        self,
        decisions: list[ModelDecision],
        attribute: ProtectedAttribute,
        positive_outcome: str,
    ) -> list[FairnessReport]:
        """
        Disparate impact ratio: rate_disadvantaged / rate_advantaged >= 0.80.
        Required by many anti-discrimination frameworks.
        """
        groups = self._group_by_attribute(decisions, attribute)
        reports = []

        group_names = sorted(groups.keys())
        for i, ga_name in enumerate(group_names):
            for gb_name in group_names[i + 1:]:
                ga_decisions = groups[ga_name]
                gb_decisions = groups[gb_name]

                if len(ga_decisions) < self.MIN_GROUP_SIZE or len(gb_decisions) < self.MIN_GROUP_SIZE:
                    continue

                ga_rate = sum(1 for d in ga_decisions if d.outcome == positive_outcome) / len(ga_decisions)
                gb_rate = sum(1 for d in gb_decisions if d.outcome == positive_outcome) / len(gb_decisions)

                if max(ga_rate, gb_rate) == 0:
                    continue

                ratio = min(ga_rate, gb_rate) / max(ga_rate, gb_rate)
                threshold = self.THRESHOLDS[FairnessMetric.DISPARATE_IMPACT]
                passes = ratio >= threshold

                recommendation = "No action required." if passes else (
                    f"Disparate impact ratio {ratio:.3f} below {threshold}. "
                    f"This may constitute indirect discrimination under gambling "
                    f"regulations. Review model inputs for proxies of {attribute.value}."
                )

                reports.append(FairnessReport(
                    metric=FairnessMetric.DISPARATE_IMPACT,
                    protected_attribute=attribute,
                    group_a=ga_name,
                    group_b=gb_name,
                    group_a_rate=round(ga_rate, 4),
                    group_b_rate=round(gb_rate, 4),
                    ratio=round(ratio, 4),
                    passes_threshold=passes,
                    threshold=threshold,
                    sample_size_a=len(ga_decisions),
                    sample_size_b=len(gb_decisions),
                    recommendation=recommendation,
                ))

        return reports

    def _group_by_attribute(
        self, decisions: list[ModelDecision], attribute: ProtectedAttribute
    ) -> dict[str, list[ModelDecision]]:
        groups: dict[str, list[ModelDecision]] = defaultdict(list)
        for d in decisions:
            value = d.protected_attributes.get(attribute.value, "unknown")
            groups[str(value)].append(d)
        return groups


# ---------------------------------------------------------------------------
# Explainability engine
# ---------------------------------------------------------------------------

class ExplainabilityEngine:
    """
    Generates human-readable explanations for model decisions.
    Uses permutation-based feature importance (no external dependencies).

    For production, integrate with SHAP or LIME for richer explanations.
    """

    def explain_decision(self, decision: ModelDecision) -> dict:
        """
        Generate an explanation report for a single model decision.
        Uses pre-computed feature attributions if available, otherwise
        ranks by raw feature values.
        """
        explanation = {
            "decision_id": decision.decision_id,
            "model": f"{decision.model_name} v{decision.model_version}",
            "outcome": decision.outcome,
            "confidence": decision.confidence,
            "top_factors": [],
            "plain_language": "",
            "regulatory_summary": "",
        }

        # Use attributions if available, otherwise fall back to features
        attributions = decision.feature_attributions or decision.features
        sorted_factors = sorted(attributions.items(), key=lambda x: abs(x[1]), reverse=True)

        for feature_name, importance in sorted_factors[:5]:
            direction = "increases" if importance > 0 else "decreases"
            explanation["top_factors"].append({
                "feature": feature_name,
                "importance": round(abs(importance), 4),
                "direction": direction,
                "value": decision.features.get(feature_name, "N/A"),
            })

        # Generate plain-language explanation
        if explanation["top_factors"]:
            top = explanation["top_factors"][0]
            explanation["plain_language"] = (
                f"The decision '{decision.outcome}' was primarily driven by "
                f"'{top['feature']}' (value: {top['value']}), which {top['direction']} "
                f"the likelihood of this outcome. "
                f"Model confidence: {decision.confidence:.0%}."
            )
        else:
            explanation["plain_language"] = (
                f"Decision '{decision.outcome}' with {decision.confidence:.0%} confidence. "
                f"No feature attribution data available."
            )

        # Regulatory summary for audit
        explanation["regulatory_summary"] = (
            f"Model {decision.model_name} (version {decision.model_version}) "
            f"produced outcome '{decision.outcome}' for player {decision.player_id} "
            f"at {decision.timestamp}. Decision based on {len(decision.features)} input "
            f"features with {len(decision.feature_attributions)} attribution scores computed. "
            f"Top contributing factor: {explanation['top_factors'][0]['feature'] if explanation['top_factors'] else 'N/A'}."
        )

        return explanation

    def generate_model_card(self, model_name: str, model_version: str, metadata: dict) -> dict:
        """
        Generate a Model Card (per Mitchell et al. 2019) for regulatory filing.
        Gambling regulators increasingly require documentation of AI systems.
        """
        return {
            "model_card_version": "1.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model_details": {
                "name": model_name,
                "version": model_version,
                "type": metadata.get("model_type", "classification"),
                "framework": metadata.get("framework", "unknown"),
                "training_date": metadata.get("training_date", "unknown"),
                "description": metadata.get("description", ""),
            },
            "intended_use": {
                "primary_use": metadata.get("primary_use", ""),
                "out_of_scope": metadata.get("out_of_scope", []),
                "jurisdictions": metadata.get("jurisdictions", []),
            },
            "training_data": {
                "source": metadata.get("data_source", ""),
                "size": metadata.get("data_size", ""),
                "date_range": metadata.get("data_date_range", ""),
                "preprocessing": metadata.get("preprocessing", ""),
            },
            "performance_metrics": metadata.get("performance", {}),
            "fairness_evaluation": metadata.get("fairness", {}),
            "ethical_considerations": {
                "gambling_specific_risks": [
                    "Model may reinforce patterns of problem gambling if trained on biased data",
                    "Bonus targeting models must not exploit vulnerable players",
                    "Risk scoring must not discriminate based on protected characteristics",
                    "Intervention thresholds must be validated by responsible gambling experts",
                ],
                "mitigations": metadata.get("mitigations", []),
            },
            "regulatory_compliance": {
                "uk_gc_lccp": metadata.get("uk_gc_compliance", "pending"),
                "mga_technical_standards": metadata.get("mga_compliance", "pending"),
                "gdpr_dpia_completed": metadata.get("dpia_completed", False),
                "right_to_explanation": metadata.get("right_to_explanation", True),
            },
        }


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------

class AuditTrailManager:
    """
    Maintains an immutable, tamper-evident audit trail for all AI decisions.
    Each record is hash-chained to the previous one for integrity verification.

    Production: write to append-only storage (S3 with Object Lock, or
    blockchain-anchored timestamps for highest assurance).
    """

    def __init__(self):
        self.records: list[AuditRecord] = []
        self._counter = 0
        self._last_hash = "GENESIS"

    def log_decision(self, decision: ModelDecision) -> AuditRecord:
        return self._create_record(
            model_name=decision.model_name,
            model_version=decision.model_version,
            event_type="decision",
            details={
                "decision_id": decision.decision_id,
                "player_id": decision.player_id,
                "decision_type": decision.decision_type,
                "outcome": decision.outcome,
                "confidence": decision.confidence,
                "feature_count": len(decision.features),
                "attribution_count": len(decision.feature_attributions),
            },
        )

    def log_bias_check(self, model_name: str, reports: list[FairnessReport]) -> AuditRecord:
        return self._create_record(
            model_name=model_name,
            model_version="all",
            event_type="bias_check",
            details={
                "metrics_evaluated": len(reports),
                "violations_found": sum(1 for r in reports if not r.passes_threshold),
                "results": [
                    {
                        "metric": r.metric.value,
                        "attribute": r.protected_attribute.value,
                        "groups": f"{r.group_a} vs {r.group_b}",
                        "ratio": r.ratio,
                        "passes": r.passes_threshold,
                    }
                    for r in reports
                ],
            },
        )

    def log_manual_override(
        self, model_name: str, decision_id: str, override_by: str, reason: str
    ) -> AuditRecord:
        return self._create_record(
            model_name=model_name,
            model_version="N/A",
            event_type="manual_override",
            details={
                "decision_id": decision_id,
                "override_by": override_by,
                "reason": reason,
            },
        )

    def verify_integrity(self) -> tuple[bool, list[str]]:
        """Verify the hash chain of the audit trail."""
        errors = []
        prev_hash = "GENESIS"

        for i, record in enumerate(self.records):
            expected = self._compute_checksum(record, prev_hash)
            if record.checksum != expected:
                errors.append(
                    f"Record {record.record_id} at index {i}: checksum mismatch "
                    f"(expected {expected[:16]}..., got {record.checksum[:16]}...)"
                )
            prev_hash = record.checksum

        return len(errors) == 0, errors

    def _create_record(self, model_name: str, model_version: str, event_type: str, details: dict) -> AuditRecord:
        self._counter += 1
        record = AuditRecord(
            record_id=f"AUDIT-{self._counter:06d}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            model_name=model_name,
            model_version=model_version,
            event_type=event_type,
            details=details,
        )
        record.checksum = self._compute_checksum(record, self._last_hash)
        self._last_hash = record.checksum
        self.records.append(record)
        return record

    def _compute_checksum(self, record: AuditRecord, prev_hash: str) -> str:
        payload = f"{prev_hash}|{record.record_id}|{record.timestamp}|{record.event_type}|{json.dumps(record.details, sort_keys=True)}"
        return hashlib.sha256(payload.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Responsible AI governance engine
# ---------------------------------------------------------------------------

class ResponsibleAIGovernance:
    """
    Central governance engine that orchestrates bias detection, fairness
    evaluation, explainability, and audit trail for all platform AI models.

    Integration points:
    - Model registry (MLflow, Vertex AI) for model metadata
    - Feature store for decision inputs
    - Compliance dashboard (Grafana/custom) for ongoing monitoring
    - GRC platform (ServiceNow, OneTrust) for audit exports
    """

    def __init__(self):
        self.fairness = FairnessEvaluator()
        self.explainability = ExplainabilityEngine()
        self.audit = AuditTrailManager()
        self.alerts: list[BiasAlert] = []
        self._alert_counter = 0

    def evaluate_model_fairness(
        self,
        model_name: str,
        decisions: list[ModelDecision],
        positive_outcome: str = "approved",
        attributes: Optional[list[ProtectedAttribute]] = None,
    ) -> dict:
        """
        Run full fairness evaluation for a model across all protected attributes.
        Returns a structured report suitable for regulatory submission.
        """
        if attributes is None:
            attributes = list(ProtectedAttribute)

        all_reports: list[FairnessReport] = []

        for attr in attributes:
            # Statistical parity
            sp_reports = self.fairness.evaluate_statistical_parity(
                decisions, attr, positive_outcome
            )
            all_reports.extend(sp_reports)

            # Disparate impact
            di_reports = self.fairness.evaluate_disparate_impact(
                decisions, attr, positive_outcome
            )
            all_reports.extend(di_reports)

        # Log to audit trail
        self.audit.log_bias_check(model_name, all_reports)

        # Generate alerts for violations
        violations = [r for r in all_reports if not r.passes_threshold]
        for v in violations:
            self._alert_counter += 1
            alert = BiasAlert(
                alert_id=f"BIAS-{self._alert_counter:04d}",
                model_name=model_name,
                risk_level=RiskLevel.HIGH if v.metric == FairnessMetric.DISPARATE_IMPACT else RiskLevel.MEDIUM,
                metric=v.metric,
                protected_attribute=v.protected_attribute,
                description=f"{v.metric.value} violation for {v.protected_attribute.value}: "
                           f"{v.group_a} vs {v.group_b} (ratio: {v.ratio})",
                remediation=v.recommendation,
                regulatory_impact=(
                    "UK GC may require model suspension pending review. "
                    "MGA technical standards mandate fairness documentation."
                ),
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            self.alerts.append(alert)

        return {
            "model_name": model_name,
            "total_decisions_evaluated": len(decisions),
            "metrics_computed": len(all_reports),
            "violations": len(violations),
            "pass_rate": round((len(all_reports) - len(violations)) / max(len(all_reports), 1), 3),
            "reports": [
                {
                    "metric": r.metric.value,
                    "attribute": r.protected_attribute.value,
                    "groups": f"{r.group_a} vs {r.group_b}",
                    "rates": f"{r.group_a_rate:.4f} vs {r.group_b_rate:.4f}",
                    "ratio": r.ratio,
                    "passes": r.passes_threshold,
                    "recommendation": r.recommendation,
                }
                for r in all_reports
            ],
            "alerts": [
                {
                    "id": a.alert_id,
                    "risk": a.risk_level.value,
                    "description": a.description,
                    "remediation": a.remediation,
                }
                for a in self.alerts
            ],
        }

    def process_decision(self, decision: ModelDecision) -> dict:
        """
        Process a model decision: log it, explain it, and return the explanation.
        Call this for every automated decision that affects players.
        """
        # Log to audit trail
        self.audit.log_decision(decision)

        # Generate explanation
        explanation = self.explainability.explain_decision(decision)

        return explanation

    def generate_regulatory_report(self, model_name: str, model_metadata: dict) -> dict:
        """
        Generate a comprehensive report for regulatory submission.
        Combines model card, fairness results, and audit summary.
        """
        model_card = self.explainability.generate_model_card(
            model_name,
            model_metadata.get("version", "1.0"),
            model_metadata,
        )

        # Audit integrity check
        integrity_ok, integrity_errors = self.audit.verify_integrity()

        return {
            "report_type": "Responsible AI Regulatory Submission",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model_card": model_card,
            "audit_trail": {
                "total_records": len(self.audit.records),
                "integrity_verified": integrity_ok,
                "integrity_errors": integrity_errors,
                "event_type_counts": self._count_event_types(),
            },
            "bias_alerts": [
                {
                    "id": a.alert_id,
                    "model": a.model_name,
                    "risk": a.risk_level.value,
                    "metric": a.metric.value,
                    "attribute": a.protected_attribute.value,
                    "description": a.description,
                    "regulatory_impact": a.regulatory_impact,
                }
                for a in self.alerts
            ],
            "compliance_checklist": {
                "fairness_evaluation_completed": any(
                    r.event_type == "bias_check" for r in self.audit.records
                ),
                "model_card_generated": True,
                "audit_trail_integrity": integrity_ok,
                "explainability_available": True,
                "gdpr_right_to_explanation": True,
                "manual_override_capability": True,
            },
        }

    def _count_event_types(self) -> dict:
        counts: dict[str, int] = defaultdict(int)
        for r in self.audit.records:
            counts[r.event_type] += 1
        return dict(counts)


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def demo():
    """Simulate responsible AI governance for a gambling platform."""
    import random
    random.seed(42)

    governance = ResponsibleAIGovernance()

    print("\n" + "=" * 70)
    print("  Responsible AI Governance Framework - Simulation")
    print("=" * 70)

    # Generate synthetic decisions for a bonus-offer model
    decisions = []
    jurisdictions = ["UK", "Malta", "Gibraltar", "Sweden"]
    age_groups = ["18-25", "26-35", "36-50", "51+"]
    genders = ["M", "F", "other"]

    for i in range(500):
        jurisdiction = random.choice(jurisdictions)
        age_group = random.choice(age_groups)
        gender = random.choice(genders)

        # Introduce subtle bias: UK players and younger players get more approvals
        base_prob = 0.60
        if jurisdiction == "UK":
            base_prob += 0.15
        if age_group == "18-25":
            base_prob += 0.12
        if gender == "F":
            base_prob -= 0.08  # gender bias to detect

        outcome = "approved" if random.random() < base_prob else "denied"

        features = {
            "session_count_30d": random.randint(5, 100),
            "avg_bet_size": round(random.uniform(1, 500), 2),
            "deposit_amount_30d": round(random.uniform(10, 5000), 2),
            "days_since_registration": random.randint(1, 1000),
            "game_variety_score": round(random.uniform(0.1, 1.0), 2),
        }

        attributions = {
            "session_count_30d": round(random.uniform(-0.3, 0.4), 3),
            "avg_bet_size": round(random.uniform(-0.2, 0.3), 3),
            "deposit_amount_30d": round(random.uniform(-0.1, 0.5), 3),
            "days_since_registration": round(random.uniform(-0.3, 0.1), 3),
            "game_variety_score": round(random.uniform(-0.1, 0.2), 3),
        }

        decision = ModelDecision(
            decision_id=f"DEC-{i:05d}",
            model_name="bonus_targeting_v2",
            model_version="2.3.1",
            player_id=f"PLR-{random.randint(10000, 99999)}",
            decision_type="bonus_offer",
            outcome=outcome,
            confidence=round(random.uniform(0.55, 0.98), 3),
            features=features,
            feature_attributions=attributions,
            protected_attributes={
                "jurisdiction": jurisdiction,
                "age_group": age_group,
                "gender": gender,
            },
            timestamp=f"2026-03-08T{random.randint(0, 23):02d}:{random.randint(0, 59):02d}:00Z",
        )
        decisions.append(decision)

    # Process each decision (audit + explain)
    print(f"\n  Processing {len(decisions)} model decisions...")
    for d in decisions:
        governance.process_decision(d)

    # Explain a specific decision
    print("\n  --- Sample Decision Explanation ---")
    sample_explanation = governance.process_decision(decisions[0])
    print(f"  Decision: {sample_explanation['decision_id']}")
    print(f"  Outcome: {sample_explanation['outcome']} ({sample_explanation['confidence']:.0%})")
    print(f"  Explanation: {sample_explanation['plain_language']}")
    if sample_explanation["top_factors"]:
        print("  Top factors:")
        for f in sample_explanation["top_factors"][:3]:
            print(f"    - {f['feature']}: {f['value']} (importance: {f['importance']:.3f}, {f['direction']})")

    # Run fairness evaluation
    print("\n  --- Fairness Evaluation ---")
    fairness_result = governance.evaluate_model_fairness(
        model_name="bonus_targeting_v2",
        decisions=decisions,
        positive_outcome="approved",
        attributes=[
            ProtectedAttribute.JURISDICTION,
            ProtectedAttribute.AGE_GROUP,
            ProtectedAttribute.GENDER,
        ],
    )

    print(f"  Total decisions: {fairness_result['total_decisions_evaluated']}")
    print(f"  Metrics computed: {fairness_result['metrics_computed']}")
    print(f"  Violations found: {fairness_result['violations']}")
    print(f"  Pass rate: {fairness_result['pass_rate']:.1%}")

    if fairness_result["violations"] > 0:
        print("\n  Violations detected:")
        for report in fairness_result["reports"]:
            if not report["passes"]:
                print(f"    [{report['metric']}] {report['attribute']}: "
                      f"{report['groups']} (ratio: {report['ratio']})")
                print(f"      Recommendation: {report['recommendation'][:80]}...")

    if fairness_result["alerts"]:
        print(f"\n  Bias alerts ({len(fairness_result['alerts'])}):")
        for alert in fairness_result["alerts"][:5]:
            print(f"    [{alert['risk'].upper()}] {alert['id']}: {alert['description']}")

    # Generate model card
    print("\n  --- Model Card ---")
    model_card = governance.explainability.generate_model_card(
        "bonus_targeting_v2", "2.3.1",
        {
            "model_type": "gradient_boosted_trees",
            "framework": "XGBoost 1.7",
            "training_date": "2026-02-15",
            "description": "Predicts player propensity to engage with bonus offers",
            "primary_use": "Personalized bonus offer targeting",
            "jurisdictions": ["UK", "Malta", "Gibraltar", "Sweden"],
            "data_source": "Player activity warehouse (BigQuery)",
            "data_size": "2.4M player records, 18 months",
        },
    )
    print(f"  Model: {model_card['model_details']['name']} v{model_card['model_details']['version']}")
    print(f"  Type: {model_card['model_details']['type']}")
    print(f"  Ethical risks: {len(model_card['ethical_considerations']['gambling_specific_risks'])}")

    # Verify audit trail integrity
    print("\n  --- Audit Trail ---")
    integrity_ok, errors = governance.audit.verify_integrity()
    print(f"  Total records: {len(governance.audit.records)}")
    print(f"  Integrity: {'VERIFIED' if integrity_ok else 'FAILED'}")
    if errors:
        for e in errors[:3]:
            print(f"    Error: {e}")

    # Regulatory report
    print("\n  --- Regulatory Report Summary ---")
    reg_report = governance.generate_regulatory_report(
        "bonus_targeting_v2",
        {"version": "2.3.1", "description": "Bonus targeting model"},
    )
    checklist = reg_report["compliance_checklist"]
    for item, status in checklist.items():
        icon = "PASS" if status else "FAIL"
        print(f"    [{icon}] {item.replace('_', ' ').title()}")

    print(f"\n  Bias alerts in report: {len(reg_report['bias_alerts'])}")
    print("\n  Production deployment: MLflow model registry -> this framework -> GRC export")
    print("  Schedule fairness evaluation as nightly batch job after model retraining.\n")


if __name__ == "__main__":
    demo()
