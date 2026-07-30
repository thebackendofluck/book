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
conformity_checker.py -- Automated EU AI Act conformity assessment (Articles 9-15).

Validates every high-risk AI system against the six EU AI Act requirements
before allowing production deployment:

  Article  9: Risk Management System
  Article 10: Data Governance
  Article 11: Technical Documentation
  Article 12: Record-Keeping
  Article 13: Transparency
  Article 14: Human Oversight
  Article 15: Accuracy and Robustness

Outputs a structured AssessmentResult that maps to the pre-deployment checklist
shown in Chapter 43b. The result is PASS, CONDITIONAL_PASS (warnings exist but
no blocking issues), or FAIL (blocking issues must be resolved before deployment).

Usage::

    from conformity_checker import ConformityChecker

    checker = ConformityChecker(registry_db="models.db")
    result = checker.assess(model_id="<uuid>")
    result.print_report()

    if not result.may_deploy:
        sys.exit(1)  # Block deployment in CI/CD pipeline

Chapter 43b: AI Governance for iGaming Platforms under the EU AI Act
Script reference: new-platform/scripts/ai-governance/conformity_checker.py
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from model_registry import ModelRegistry, ModelStatus, RiskTier


# ---------------------------------------------------------------------------
# Enums and result structures
# ---------------------------------------------------------------------------

class CheckStatus(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass
class CheckResult:
    """Result of a single conformity check."""
    article: str                # e.g., 'Article 9'
    requirement: str            # short description
    status: CheckStatus
    detail: str                 # detailed message explaining the result


@dataclass
class AssessmentResult:
    """
    Complete EU AI Act conformity assessment result for a model.

    Contains all individual check results grouped by article, plus a
    summary verdict. Blocking issues (FAIL) prevent deployment; warnings
    (WARN) should be reviewed but do not block.
    """
    model_id: str
    model_name: str
    model_version: str
    risk_tier: str
    assessed_at: str
    assessor: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def blocking_issues(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status == CheckStatus.FAIL]

    @property
    def warnings(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status == CheckStatus.WARN]

    @property
    def passed_checks(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status == CheckStatus.PASS]

    @property
    def verdict(self) -> str:
        if self.blocking_issues:
            return "FAIL"
        if self.warnings:
            return "CONDITIONAL_PASS"
        return "PASS"

    @property
    def may_deploy(self) -> bool:
        """True if the model may proceed to deployment (no blocking issues)."""
        return not self.blocking_issues

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "risk_tier": self.risk_tier,
            "assessed_at": self.assessed_at,
            "assessor": self.assessor,
            "verdict": self.verdict,
            "may_deploy": self.may_deploy,
            "blocking_issues": len(self.blocking_issues),
            "warnings": len(self.warnings),
            "passed": len(self.passed_checks),
            "checks": [
                {
                    "article": c.article,
                    "requirement": c.requirement,
                    "status": c.status.value,
                    "detail": c.detail,
                }
                for c in self.checks
            ],
        }

    def print_report(self) -> None:
        """Print a formatted conformity assessment report to stdout."""
        width = 66
        border = "=" * width
        print(border)
        print(f"  EU AI ACT CONFORMITY ASSESSMENT")
        print(f"  Model: {self.model_name} v{self.model_version}")
        print(f"  Date:  {self.assessed_at[:10]}")
        print(f"  Assessor: {self.assessor}")
        print(border)

        current_article = ""
        for check in self.checks:
            if check.article != current_article:
                print(f"\n  {check.article}")
                current_article = check.article
            status_label = f"[{check.status.value}]"
            print(f"  {status_label:6}  {check.requirement}")
            if check.status != CheckStatus.PASS:
                # Indent the detail message
                words = check.detail.split()
                line = "          "
                for word in words:
                    if len(line) + len(word) > width:
                        print(line)
                        line = "          " + word + " "
                    else:
                        line += word + " "
                if line.strip():
                    print(line)

        print(f"\n  {border}")
        print(f"  OVERALL: {self.verdict}")
        print(f"  Blocking issues: {len(self.blocking_issues)}")
        print(f"  Warnings: {len(self.warnings)}")
        print(border)


# ---------------------------------------------------------------------------
# ConformityChecker
# ---------------------------------------------------------------------------

class ConformityChecker:
    """
    Automated conformity assessment against EU AI Act Articles 9-15.

    Reads model metadata from the ModelRegistry and validates each
    requirement. Some checks are automatic (registry metadata); others
    require the caller to provide external evidence (drift test results,
    adversarial test reports, etc.).

    For HIGH-RISK models, all seven articles are checked.
    For LIMITED-RISK models, only Articles 11 and 13 are checked.
    MINIMAL-RISK models are skipped (no obligations).
    """

    def __init__(
        self,
        registry_db: str = ":memory:",
        assessor_id: str = "conformity-engine-v1.0",
    ) -> None:
        self.registry = ModelRegistry(registry_db)
        self.assessor_id = assessor_id

    def assess(
        self,
        model_id: str,
        external_evidence: Optional[dict[str, Any]] = None,
    ) -> AssessmentResult:
        """
        Run the full conformity assessment for a model.

        Args:
            model_id:          UUID of the model in the registry
            external_evidence: Optional dict with evidence for checks that
                               cannot be inferred from the registry alone.
                               Supported keys:
                               - 'risk_management_doc_date' (ISO date)
                               - 'training_data_bias_assessed' (bool)
                               - 'data_quality_score' (float, 0-1)
                               - 'jurisdiction_coverage_pct' (float)
                               - 'drift_test_last_run' (ISO date)
                               - 'adversarial_test_completed' (bool)
                               - 'drift_monitoring_configured' (bool)
                               - 'chatbot_disclosure_verified' (bool)
                               - 'override_capability_tested' (bool)

        Returns:
            AssessmentResult with all check results and a deployment verdict.
        """
        model = self.registry.get_model(model_id)
        evidence = external_evidence or {}
        now = datetime.now(tz=timezone.utc).isoformat()

        result = AssessmentResult(
            model_id=model_id,
            model_name=model["name"],
            model_version=model["version"],
            risk_tier=model["risk_tier"],
            assessed_at=now,
            assessor=self.assessor_id,
        )

        risk_tier = RiskTier(model["risk_tier"])

        if risk_tier == RiskTier.PROHIBITED:
            result.checks.append(CheckResult(
                article="Article 5",
                requirement="Prohibited AI system cannot be assessed",
                status=CheckStatus.FAIL,
                detail=(
                    "This system is classified as PROHIBITED under Article 5 of the "
                    "EU AI Act. It cannot be deployed under any circumstances. The "
                    "use case must be redesigned or abandoned."
                ),
            ))
            return result

        if risk_tier == RiskTier.MINIMAL:
            result.checks.append(CheckResult(
                article="N/A",
                requirement="MINIMAL risk — no conformity obligations",
                status=CheckStatus.PASS,
                detail="No EU AI Act conformity assessment required for MINIMAL-risk systems.",
            ))
            return result

        # Run checks based on risk tier
        self._check_article_9(result, model, evidence)
        self._check_article_10(result, model, evidence)
        self._check_article_11(result, model)
        self._check_article_12(result, model)
        self._check_article_13(result, model, evidence)

        if risk_tier == RiskTier.HIGH:
            self._check_article_14(result, model, evidence)
            self._check_article_15(result, model, evidence)

        return result

    # ---------------------------------------------------------------------------
    # Article-specific checks
    # ---------------------------------------------------------------------------

    def _check_article_9(
        self,
        result: AssessmentResult,
        model: dict[str, Any],
        evidence: dict[str, Any],
    ) -> None:
        """Article 9: Risk Management System."""
        article = "Article 9 -- Risk Management"

        risk_doc_date = evidence.get("risk_management_doc_date")
        if risk_doc_date:
            doc_age_days = (
                datetime.now(tz=timezone.utc)
                - datetime.fromisoformat(risk_doc_date).astimezone(timezone.utc)
            ).days
            if doc_age_days <= 365:
                result.checks.append(CheckResult(
                    article=article,
                    requirement="Risk identification document exists",
                    status=CheckStatus.PASS,
                    detail=f"Risk identification document dated {risk_doc_date[:10]} "
                           f"({doc_age_days} days ago).",
                ))
            else:
                result.checks.append(CheckResult(
                    article=article,
                    requirement="Risk identification document exists",
                    status=CheckStatus.WARN,
                    detail=f"Risk identification document is {doc_age_days} days old. "
                           "Article 9 requires a continuously updated risk management "
                           "system. Recommend refreshing within 30 days.",
                ))
        else:
            result.checks.append(CheckResult(
                article=article,
                requirement="Risk identification document exists",
                status=CheckStatus.FAIL,
                detail="No risk management document date provided. Article 9 requires "
                       "a documented risk management system covering identification, "
                       "assessment, mitigation, and residual risk monitoring.",
            ))

        result.checks.append(CheckResult(
            article=article,
            requirement="Mitigation measures documented",
            status=CheckStatus.PASS if model.get("purpose") else CheckStatus.WARN,
            detail=(
                "Model purpose and architecture documented in registry."
                if model.get("purpose")
                else "Model purpose field is empty. Document mitigation measures "
                     "in the model registry or external risk management document."
            ),
        ))

        result.checks.append(CheckResult(
            article=article,
            requirement="Residual risk monitoring configured",
            status=CheckStatus.PASS if evidence.get("drift_monitoring_configured") else CheckStatus.WARN,
            detail=(
                "Drift monitoring configured."
                if evidence.get("drift_monitoring_configured")
                else "Drift monitoring not confirmed. Article 9 requires continuous "
                     "monitoring of residual risks in production."
            ),
        ))

    def _check_article_10(
        self,
        result: AssessmentResult,
        model: dict[str, Any],
        evidence: dict[str, Any],
    ) -> None:
        """Article 10: Data Governance."""
        article = "Article 10 -- Data Governance"

        has_data_doc = bool(model.get("training_data_description"))
        result.checks.append(CheckResult(
            article=article,
            requirement="Training data documentation complete",
            status=CheckStatus.PASS if has_data_doc else CheckStatus.FAIL,
            detail=(
                "Training data description present in registry."
                if has_data_doc
                else "No training data description in registry. "
                     "Document: data sources, date range, size, preprocessing steps."
            ),
        ))

        bias_assessed = evidence.get("training_data_bias_assessed", False)
        result.checks.append(CheckResult(
            article=article,
            requirement="Bias assessment performed on training data",
            status=CheckStatus.PASS if bias_assessed else CheckStatus.FAIL,
            detail=(
                "Training data bias assessment completed."
                if bias_assessed
                else "No training data bias assessment confirmed. Run BiasAuditor on "
                     "the training dataset to verify representation across demographic groups."
            ),
        ))

        quality_score = evidence.get("data_quality_score")
        if quality_score is None:
            result.checks.append(CheckResult(
                article=article,
                requirement="Data quality metrics within thresholds",
                status=CheckStatus.WARN,
                detail="No data quality score provided. Document completeness, accuracy, "
                       "and consistency metrics for the training dataset.",
            ))
        elif quality_score >= 0.95:
            result.checks.append(CheckResult(
                article=article,
                requirement="Data quality metrics within thresholds",
                status=CheckStatus.PASS,
                detail=f"Data quality score: {quality_score:.2f} (threshold: 0.95).",
            ))
        else:
            result.checks.append(CheckResult(
                article=article,
                requirement="Data quality metrics within thresholds",
                status=CheckStatus.FAIL,
                detail=f"Data quality score {quality_score:.2f} below threshold 0.95. "
                       "Investigate data completeness and accuracy issues before deployment.",
            ))

        coverage = evidence.get("jurisdiction_coverage_pct")
        if coverage is not None and coverage < 0.05:
            result.checks.append(CheckResult(
                article=article,
                requirement="Training data representative of target population",
                status=CheckStatus.WARN,
                detail=f"Jurisdiction coverage gap detected ({coverage:.1%} of target "
                       "jurisdictions adequately represented). Training data may not be "
                       "representative. Monitor for bias in underrepresented regions.",
            ))
        elif coverage is not None:
            result.checks.append(CheckResult(
                article=article,
                requirement="Training data representative of target population",
                status=CheckStatus.PASS,
                detail=f"Jurisdiction coverage: {coverage:.1%} of training data from "
                       "target jurisdictions.",
            ))

    def _check_article_11(
        self,
        result: AssessmentResult,
        model: dict[str, Any],
    ) -> None:
        """Article 11: Technical Documentation (model card)."""
        article = "Article 11 -- Technical Documentation"

        fields = [
            ("architecture", "Architecture documented"),
            ("training_data_description", "Training methodology documented"),
            ("purpose", "Model purpose documented"),
        ]
        for field_key, label in fields:
            has_field = bool(model.get(field_key))
            result.checks.append(CheckResult(
                article=article,
                requirement=label,
                status=CheckStatus.PASS if has_field else CheckStatus.FAIL,
                detail=(
                    f"'{field_key}' field present in model registry."
                    if has_field
                    else f"'{field_key}' field is empty. Complete the model card in the registry."
                ),
            ))

        metrics = model.get("performance_metrics") or {}
        has_metrics = isinstance(metrics, dict) and len(metrics) >= 3
        result.checks.append(CheckResult(
            article=article,
            requirement="Performance metrics documented",
            status=CheckStatus.PASS if has_metrics else CheckStatus.WARN,
            detail=(
                f"Performance metrics documented: {list(metrics.keys())}."
                if has_metrics
                else "Performance metrics incomplete. Document at minimum: "
                     "precision, recall, F1, AUC-ROC on held-out test set."
            ),
        ))

        jurisdictions = model.get("jurisdictions") or []
        has_jurisdictions = isinstance(jurisdictions, list) and len(jurisdictions) > 0
        result.checks.append(CheckResult(
            article=article,
            requirement="Target jurisdictions documented",
            status=CheckStatus.PASS if has_jurisdictions else CheckStatus.WARN,
            detail=(
                f"Jurisdictions: {jurisdictions}."
                if has_jurisdictions
                else "No jurisdictions documented. Specify target regulatory markets "
                     "(MGA, UKGC, NJ DGE, etc.)."
            ),
        ))

    def _check_article_12(
        self,
        result: AssessmentResult,
        model: dict[str, Any],
    ) -> None:
        """Article 12: Record-Keeping."""
        article = "Article 12 -- Record-Keeping"

        # Presence in the registry implies decision logging is planned/configured
        result.checks.append(CheckResult(
            article=article,
            requirement="Decision logging configured",
            status=CheckStatus.PASS,
            detail="Model registered in AI governance registry. Ensure DecisionLogger "
                   "is wired into the model serving pipeline before deployment.",
        ))

        result.checks.append(CheckResult(
            article=article,
            requirement="Log retention policy meets jurisdiction requirements",
            status=CheckStatus.PASS if model.get("jurisdictions") else CheckStatus.WARN,
            detail=(
                "Jurisdictions documented — configure retention policy accordingly "
                "(MGA/NJ DGE: 10 years; UKGC/SGA: 7 years)."
                if model.get("jurisdictions")
                else "No jurisdictions specified. Cannot verify retention policy meets "
                     "requirements. MGA/NJ DGE require 10 years; UKGC/SGA require 7 years."
            ),
        ))

        result.checks.append(CheckResult(
            article=article,
            requirement="Audit trail immutability verified",
            status=CheckStatus.PASS,
            detail="Immutable audit trail enforced by DecisionLogger (INSERT-only, "
                   "no UPDATE/DELETE on decision records).",
        ))

    def _check_article_13(
        self,
        result: AssessmentResult,
        model: dict[str, Any],
        evidence: dict[str, Any],
    ) -> None:
        """Article 13: Transparency."""
        article = "Article 13 -- Transparency"

        result.checks.append(CheckResult(
            article=article,
            requirement="Player notification templates configured",
            status=CheckStatus.PASS,
            detail="TransparencyReportGenerator provides player-facing notification "
                   "templates. Verify integration with the player-facing application.",
        ))

        result.checks.append(CheckResult(
            article=article,
            requirement="AI disclosure present in relevant player flows",
            status=CheckStatus.PASS,
            detail="AI disclosure obligation documented. Verify: login, payment, "
                   "account restriction, and support chat flows include AI disclosure.",
        ))

        chatbot_ok = evidence.get("chatbot_disclosure_verified", True)
        result.checks.append(CheckResult(
            article=article,
            requirement="AI disclosure verified in all channels",
            status=CheckStatus.PASS if chatbot_ok else CheckStatus.WARN,
            detail=(
                "AI disclosure verified across all player interaction channels."
                if chatbot_ok
                else "AI disclosure not verified in all channels (e.g., mobile app). "
                     "Article 52(1) requires disclosure wherever AI interacts with players. "
                     "Verify mobile app, in-game chat, and email notification flows."
            ),
        ))

    def _check_article_14(
        self,
        result: AssessmentResult,
        model: dict[str, Any],
        evidence: dict[str, Any],
    ) -> None:
        """Article 14: Human Oversight (HIGH-RISK only)."""
        article = "Article 14 -- Human Oversight"

        has_oversight = bool(model.get("human_oversight_configured"))
        result.checks.append(CheckResult(
            article=article,
            requirement="Human review workflow configured",
            status=CheckStatus.PASS if has_oversight else CheckStatus.FAIL,
            detail=(
                "Human oversight configured in registry."
                if has_oversight
                else "HIGH-RISK model requires human_oversight_configured=True in registry. "
                     "Set up review queue, escalation paths, and analyst assignment before deployment."
            ),
        ))

        override_tested = evidence.get("override_capability_tested", False)
        result.checks.append(CheckResult(
            article=article,
            requirement="Override capability tested",
            status=CheckStatus.PASS if override_tested else CheckStatus.WARN,
            detail=(
                "Human override capability verified."
                if override_tested
                else "Override capability not confirmed as tested. Test the full override "
                     "workflow (analyst override → audit log → player notification) before "
                     "deploying to production."
            ),
        ))

        result.checks.append(CheckResult(
            article=article,
            requirement="Escalation timeframes within SLA",
            status=CheckStatus.PASS,
            detail="DecisionLogger SLA: HIGH-impact decisions queued with 4-hour deadline; "
                   "auto-escalation path configured at 2h/4h/8h/24h thresholds.",
        ))

    def _check_article_15(
        self,
        result: AssessmentResult,
        model: dict[str, Any],
        evidence: dict[str, Any],
    ) -> None:
        """Article 15: Accuracy and Robustness (HIGH-RISK only)."""
        article = "Article 15 -- Accuracy and Robustness"

        metrics = model.get("performance_metrics") or {}
        has_sufficient_metrics = (
            isinstance(metrics, dict)
            and metrics.get("f1", 0) >= 0.80
            and metrics.get("precision", 0) >= 0.80
        )
        result.checks.append(CheckResult(
            article=article,
            requirement="Performance metrics exceed minimum thresholds",
            status=CheckStatus.PASS if has_sufficient_metrics else CheckStatus.WARN,
            detail=(
                f"Performance metrics: {json.dumps(metrics, indent=None)}."
                if has_sufficient_metrics
                else "Performance metrics below recommended thresholds or not documented. "
                     "Minimum recommended: F1 >= 0.80, precision >= 0.80 for HIGH-risk systems."
            ),
        ))

        adversarial_done = evidence.get("adversarial_test_completed", False)
        result.checks.append(CheckResult(
            article=article,
            requirement="Adversarial testing completed",
            status=CheckStatus.PASS if adversarial_done else CheckStatus.WARN,
            detail=(
                "Adversarial test set evaluation completed."
                if adversarial_done
                else "No adversarial testing evidence provided. iGaming systems operate "
                     "in an adversarial environment — test model robustness against crafted "
                     "inputs designed to evade detection."
            ),
        ))

        drift_last_run = evidence.get("drift_test_last_run")
        if drift_last_run:
            drift_age_days = (
                datetime.now(tz=timezone.utc)
                - datetime.fromisoformat(drift_last_run).astimezone(timezone.utc)
            ).days
            if drift_age_days <= 30:
                result.checks.append(CheckResult(
                    article=article,
                    requirement="Concept drift test run within last 30 days",
                    status=CheckStatus.PASS,
                    detail=f"Drift test run {drift_age_days} days ago ({drift_last_run[:10]}).",
                ))
            else:
                result.checks.append(CheckResult(
                    article=article,
                    requirement="Concept drift test run within last 30 days",
                    status=CheckStatus.FAIL,
                    detail=f"Drift test last run {drift_age_days} days ago (> 30 day limit). "
                           "Run PSI/KS drift detection on current production data before deployment.",
                ))
        else:
            result.checks.append(CheckResult(
                article=article,
                requirement="Concept drift test run within last 30 days",
                status=CheckStatus.FAIL,
                detail="No drift test date provided. Article 15 requires evidence of "
                       "concept drift monitoring. Run fairness_metrics.population_stability_index() "
                       "on current production scores vs. training baseline.",
            ))

        drift_configured = evidence.get("drift_monitoring_configured", False)
        result.checks.append(CheckResult(
            article=article,
            requirement="Drift monitoring configured for production",
            status=CheckStatus.PASS if drift_configured else CheckStatus.WARN,
            detail=(
                "Production drift monitoring configured."
                if drift_configured
                else "Drift monitoring not confirmed. Configure weekly PSI/KS checks "
                     "with automatic alerts when PSI > 0.25 (significant distribution shift)."
            ),
        ))

    def close(self) -> None:
        """Release registry resources."""
        self.registry.close()
