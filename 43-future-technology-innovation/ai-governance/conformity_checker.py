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
Automated EU AI Act Conformity Assessment for iGaming AI Systems.

Performs pre-deployment and continuous conformity checks against
Articles 9-15 of the EU AI Act (Regulation 2024/1689). Validates
that high-risk AI systems meet all requirements before deployment
and during operation.

Usage:
    checker = ConformityChecker(registry_db="models.db", decision_db="decisions.db")
    result = checker.assess(model_id="abc-123")
    print(result.summary())

    if not result.deployable:
        sys.exit(1)

CLI:
    python conformity_checker.py \
        --model-id abc-123 \
        --registry-db models.db \
        --output-format json \
        --output-file assessment.json
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from model_registry import ModelRegistry, ModelStatus, RiskTier  # ty:ignore[unresolved-import]


@dataclass
class Check:
    """A single conformity check result."""

    article: str
    requirement: str
    status: str  # "PASS", "FAIL", "WARN", "N/A"
    detail: str
    blocking: bool = False


@dataclass
class AssessmentResult:
    """Complete conformity assessment result."""

    assessment_id: str
    model_id: str
    model_name: str
    risk_tier: str
    assessment_date: str
    assessor: str
    checks: list[Check]
    deployable: bool = True
    blocking_count: int = 0
    warning_count: int = 0
    pass_count: int = 0

    def summary(self) -> str:
        lines = [
            "=" * 66,
            "  EU AI ACT CONFORMITY ASSESSMENT",
            f"  Model: {self.model_name} ({self.model_id[:12]}...)",
            f"  Risk Tier: {self.risk_tier}",
            f"  Date: {self.assessment_date}",
            f"  Assessor: {self.assessor}",
            "=" * 66,
            "",
        ]

        current_article = ""
        for check in self.checks:
            if check.article != current_article:
                current_article = check.article
                lines.append(f"  {check.article}")

            status_marker = {
                "PASS": "PASS", "FAIL": "FAIL",
                "WARN": "WARN", "N/A": "N/A ",
            }.get(check.status, check.status)
            lines.append(f"  [{status_marker}] {check.requirement}")
            if check.detail:
                lines.append(f"         {check.detail}")

        lines.append("")
        lines.append("-" * 66)

        if self.deployable:
            if self.warning_count > 0:
                lines.append("  OVERALL: CONDITIONAL PASS")
                lines.append(f"  Warnings: {self.warning_count} (review recommended)")
            else:
                lines.append("  OVERALL: PASS")
        else:
            lines.append("  OVERALL: FAIL")
            lines.append(f"  Blocking issues: {self.blocking_count}")
            lines.append(f"  Warnings: {self.warning_count}")

        lines.append(f"  Passed checks: {self.pass_count}")
        lines.append("=" * 66)
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "assessment_id": self.assessment_id,
            "model_id": self.model_id,
            "model_name": self.model_name,
            "risk_tier": self.risk_tier,
            "assessment_date": self.assessment_date,
            "assessor": self.assessor,
            "deployable": self.deployable,
            "blocking_count": self.blocking_count,
            "warning_count": self.warning_count,
            "pass_count": self.pass_count,
            "checks": [
                {
                    "article": c.article,
                    "requirement": c.requirement,
                    "status": c.status,
                    "detail": c.detail,
                    "blocking": c.blocking,
                }
                for c in self.checks
            ],
        }


class ConformityChecker:
    """Automated EU AI Act conformity assessment engine.

    Validates AI models against Articles 9-15 requirements based on
    their risk tier. Integrates with the ModelRegistry and Decision
    Logger to verify compliance.
    """

    def __init__(
        self,
        registry_db: str | None = None,
        decision_db: str | None = None,
        assessor: str = "Conformity Engine v1.0",
    ) -> None:
        # Lazy registry init so callers that only use the high-level
        # `assess_from_model_dict` style API don't silently create a
        # `models.db` file in the current working directory.
        self._registry_db = registry_db
        self._registry: ModelRegistry | None = None
        self.assessor = assessor
        self._decision_db = decision_db

    @property
    def registry(self) -> ModelRegistry:
        if self._registry is None:
            self._registry = ModelRegistry(self._registry_db or "models.db")
        return self._registry

    def assess(self, model_id: str) -> AssessmentResult:
        """Run full conformity assessment on a model.

        Returns an AssessmentResult with all checks and pass/fail status.
        """
        model = self.registry.get_model(model_id)
        risk_tier = model["risk_tier"]
        checks: list[Check] = []

        # Article 5 -- Prohibited practices
        checks.extend(self._check_article_5(model))

        if risk_tier == RiskTier.HIGH.value:
            checks.extend(self._check_article_9(model))
            checks.extend(self._check_article_10(model))
            checks.extend(self._check_article_11(model))
            checks.extend(self._check_article_12(model))
            checks.extend(self._check_article_13(model))
            checks.extend(self._check_article_14(model))
            checks.extend(self._check_article_15(model))
        elif risk_tier == RiskTier.LIMITED.value:
            checks.extend(self._check_article_52(model))
        elif risk_tier == RiskTier.MINIMAL.value:
            checks.append(Check(
                article="MINIMAL RISK",
                requirement="No mandatory obligations",
                status="N/A",
                detail="Voluntary codes of conduct apply",
            ))

        # Compute totals
        blocking = sum(1 for c in checks if c.status == "FAIL" and c.blocking)
        warnings = sum(1 for c in checks if c.status == "WARN")
        passes = sum(1 for c in checks if c.status == "PASS")
        deployable = blocking == 0

        result = AssessmentResult(
            assessment_id=str(uuid.uuid4()),
            model_id=model_id,
            model_name=f"{model['name']} v{model['version']}",
            risk_tier=risk_tier,
            assessment_date=datetime.now(timezone.utc).isoformat(),
            assessor=self.assessor,
            checks=checks,
            deployable=deployable,
            blocking_count=blocking,
            warning_count=warnings,
            pass_count=passes,
        )

        # Record assessment date in registry
        if deployable:
            self.registry.set_conformity_assessment_date(
                model_id,
                result.assessment_date,
                actor=self.assessor,
            )

        return result

    def _check_article_5(self, model: dict[str, Any]) -> list[Check]:
        """Article 5 -- Prohibited AI practices."""
        checks = []
        if model["risk_tier"] == RiskTier.PROHIBITED.value:
            checks.append(Check(
                article="ARTICLE 5 -- Prohibited Practices",
                requirement="AI system must not be classified as prohibited",
                status="FAIL",
                detail="This model is classified as PROHIBITED and cannot be deployed",
                blocking=True,
            ))
        else:
            checks.append(Check(
                article="ARTICLE 5 -- Prohibited Practices",
                requirement="AI system must not be classified as prohibited",
                status="PASS",
                detail=f"Risk tier: {model['risk_tier']}",
            ))
        return checks

    def _check_article_9(self, model: dict[str, Any]) -> list[Check]:
        """Article 9 -- Risk Management System."""
        checks = []
        model_id = model["model_id"]

        # Check: risk identification documented
        has_purpose = bool(model.get("purpose"))
        checks.append(Check(
            article="ARTICLE 9 -- Risk Management",
            requirement="Risk identification documented",
            status="PASS" if has_purpose else "FAIL",
            detail=model.get("purpose", "No purpose documented")[:80],
            blocking=not has_purpose,
        ))

        # Check: performance metrics exist (proxy for assessment)
        has_metrics = bool(model.get("performance_metrics"))
        checks.append(Check(
            article="ARTICLE 9 -- Risk Management",
            requirement="Risk assessment completed (performance metrics)",
            status="PASS" if has_metrics else "FAIL",
            detail="Metrics present" if has_metrics else "No performance metrics",
            blocking=not has_metrics,
        ))

        # Check: bias audit exists (mitigation)
        latest_audit = self.registry.get_latest_bias_audit(model_id)
        if latest_audit:
            checks.append(Check(
                article="ARTICLE 9 -- Risk Management",
                requirement="Mitigation measures documented (bias audit)",
                status="PASS" if latest_audit["pass_fail"] == "PASS" else "WARN",
                detail=f"Latest audit: {latest_audit['pass_fail']} on {latest_audit['audit_date'][:10]}",
            ))
        else:
            checks.append(Check(
                article="ARTICLE 9 -- Risk Management",
                requirement="Mitigation measures documented (bias audit)",
                status="FAIL",
                detail="No bias audit performed",
                blocking=True,
            ))

        return checks

    def _check_article_10(self, model: dict[str, Any]) -> list[Check]:
        """Article 10 -- Data Governance."""
        checks = []

        has_training_desc = bool(model.get("training_data_description"))
        checks.append(Check(
            article="ARTICLE 10 -- Data Governance",
            requirement="Training data documentation complete",
            status="PASS" if has_training_desc else "FAIL",
            detail=model.get("training_data_description", "Not documented")[:80],
            blocking=not has_training_desc,
        ))

        # Check jurisdictions coverage
        jurisdictions = model.get("jurisdictions", [])
        if jurisdictions:
            checks.append(Check(
                article="ARTICLE 10 -- Data Governance",
                requirement="Jurisdictional representativeness documented",
                status="PASS",
                detail=f"Jurisdictions: {', '.join(jurisdictions)}",
            ))
        else:
            checks.append(Check(
                article="ARTICLE 10 -- Data Governance",
                requirement="Jurisdictional representativeness documented",
                status="WARN",
                detail="No jurisdictions specified for training data representativeness",
            ))

        return checks

    def _check_article_11(self, model: dict[str, Any]) -> list[Check]:
        """Article 11 -- Technical Documentation."""
        checks = []

        # Model card completeness
        required_fields = ["purpose", "architecture", "training_data_description"]
        missing = [f for f in required_fields if not model.get(f)]

        if not missing:
            checks.append(Check(
                article="ARTICLE 11 -- Technical Documentation",
                requirement="Model card complete",
                status="PASS",
                detail="All required fields documented",
            ))
        else:
            checks.append(Check(
                article="ARTICLE 11 -- Technical Documentation",
                requirement="Model card complete",
                status="FAIL",
                detail=f"Missing: {', '.join(missing)}",
                blocking=True,
            ))

        # Architecture documented
        has_arch = bool(model.get("architecture"))
        checks.append(Check(
            article="ARTICLE 11 -- Technical Documentation",
            requirement="Architecture documented",
            status="PASS" if has_arch else "FAIL",
            detail=model.get("architecture", "Not documented")[:80],
            blocking=not has_arch,
        ))

        # Performance metrics documented
        metrics = model.get("performance_metrics", {})
        if metrics:
            metric_summary = ", ".join(f"{k}={v}" for k, v in list(metrics.items())[:4])
            checks.append(Check(
                article="ARTICLE 11 -- Technical Documentation",
                requirement="Performance metrics documented",
                status="PASS",
                detail=metric_summary,
            ))
        else:
            checks.append(Check(
                article="ARTICLE 11 -- Technical Documentation",
                requirement="Performance metrics documented",
                status="FAIL",
                detail="No performance metrics",
                blocking=True,
            ))

        return checks

    def _check_article_12(self, model: dict[str, Any]) -> list[Check]:
        """Article 12 -- Record-Keeping."""
        checks = []

        # Decision logging configuration
        if self._decision_db and Path(self._decision_db).exists():
            checks.append(Check(
                article="ARTICLE 12 -- Record-Keeping",
                requirement="Decision logging configured",
                status="PASS",
                detail=f"Decision database: {self._decision_db}",
            ))
        else:
            checks.append(Check(
                article="ARTICLE 12 -- Record-Keeping",
                requirement="Decision logging configured",
                status="WARN",
                detail="Decision database not found or not specified",
            ))

        # Audit trail exists in registry
        trail = self.registry.get_audit_trail(model["model_id"], limit=1)
        if trail:
            checks.append(Check(
                article="ARTICLE 12 -- Record-Keeping",
                requirement="Audit trail active",
                status="PASS",
                detail=f"Latest event: {trail[0]['event_type']} at {trail[0]['timestamp'][:16]}",
            ))
        else:
            checks.append(Check(
                article="ARTICLE 12 -- Record-Keeping",
                requirement="Audit trail active",
                status="FAIL",
                detail="No audit trail events found",
                blocking=True,
            ))

        return checks

    def _check_article_13(self, model: dict[str, Any]) -> list[Check]:
        """Article 13 -- Transparency."""
        checks = []

        has_purpose = bool(model.get("purpose"))
        checks.append(Check(
            article="ARTICLE 13 -- Transparency",
            requirement="AI system purpose clearly documented",
            status="PASS" if has_purpose else "FAIL",
            detail=model.get("purpose", "Not documented")[:80],
            blocking=not has_purpose,
        ))

        jurisdictions = model.get("jurisdictions", [])
        if jurisdictions:
            checks.append(Check(
                article="ARTICLE 13 -- Transparency",
                requirement="Applicable jurisdictions documented",
                status="PASS",
                detail=f"Active in: {', '.join(jurisdictions)}",
            ))
        else:
            checks.append(Check(
                article="ARTICLE 13 -- Transparency",
                requirement="Applicable jurisdictions documented",
                status="WARN",
                detail="No jurisdictions specified",
            ))

        return checks

    def _check_article_14(self, model: dict[str, Any]) -> list[Check]:
        """Article 14 -- Human Oversight."""
        checks = []

        configured = bool(model.get("human_oversight_configured"))
        checks.append(Check(
            article="ARTICLE 14 -- Human Oversight",
            requirement="Human oversight mechanism configured",
            status="PASS" if configured else "FAIL",
            detail="Human review workflow active" if configured else "Not configured",
            blocking=not configured,
        ))

        return checks

    def _check_article_15(self, model: dict[str, Any]) -> list[Check]:
        """Article 15 -- Accuracy and Robustness."""
        checks = []

        metrics = model.get("performance_metrics", {})

        # Check minimum performance thresholds
        min_thresholds = {"precision": 0.80, "recall": 0.70, "f1": 0.75}
        for metric_name, min_val in min_thresholds.items():
            actual = metrics.get(metric_name)
            if actual is not None:
                passed = actual >= min_val
                checks.append(Check(
                    article="ARTICLE 15 -- Accuracy & Robustness",
                    requirement=f"{metric_name} >= {min_val}",
                    status="PASS" if passed else "FAIL",
                    detail=f"Actual: {actual}",
                    blocking=not passed,
                ))
            else:
                checks.append(Check(
                    article="ARTICLE 15 -- Accuracy & Robustness",
                    requirement=f"{metric_name} >= {min_val}",
                    status="WARN",
                    detail=f"{metric_name} not reported",
                ))

        return checks

    def _check_article_52(self, model: dict[str, Any]) -> list[Check]:
        """Article 52 -- Limited risk transparency obligations."""
        checks = []

        checks.append(Check(
            article="ARTICLE 52 -- Transparency (Limited Risk)",
            requirement="Users informed of AI interaction",
            status="WARN",
            detail="Manual verification required: ensure player-facing UI discloses AI use",
        ))

        has_purpose = bool(model.get("purpose"))
        checks.append(Check(
            article="ARTICLE 52 -- Transparency (Limited Risk)",
            requirement="AI system purpose documented",
            status="PASS" if has_purpose else "WARN",
            detail=model.get("purpose", "Not documented")[:80],
        ))

        return checks

    def close(self) -> None:
        self.registry.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="EU AI Act Conformity Assessment")
    parser.add_argument("--model-id", required=True, help="Model ID to assess")
    parser.add_argument("--registry-db", default="models.db")
    parser.add_argument("--decision-db", default=None)
    parser.add_argument("--output-format", choices=["text", "json"], default="text")
    parser.add_argument("--output-file", default=None)
    args = parser.parse_args()

    checker = ConformityChecker(
        registry_db=args.registry_db,
        decision_db=args.decision_db,
    )

    try:
        result = checker.assess(args.model_id)
    except KeyError:
        print(f"Model '{args.model_id}' not found in registry.")
        sys.exit(1)

    if args.output_format == "json":
        output = json.dumps(result.to_dict(), indent=2)
    else:
        output = result.summary()

    if args.output_file:
        Path(args.output_file).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output_file, "w") as f:
            f.write(output)
        print(f"Assessment saved to: {args.output_file}")
    else:
        print(output)

    if not result.deployable:
        sys.exit(1)

    checker.close()


if __name__ == "__main__":
    main()


# Alias for backward compatibility and test interface
ConformityAssessment = ConformityChecker


@dataclass
class ComplianceReport:
    """Compliance assessment report for test compatibility."""
    assessment_id: str
    model_id: str
    model_name: str
    risk_tier: str
    assessment_date: str
    assessor: str
    compliance_score: float
    failed_checks: int
    missing_items: list[str]
    remediation_steps: list[str]
    checks: list[Check]
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "assessment_id": self.assessment_id,
            "model_id": self.model_id,
            "model_name": self.model_name,
            "risk_tier": self.risk_tier,
            "assessment_date": self.assessment_date,
            "assessor": self.assessor,
            "compliance_score": self.compliance_score,
            "failed_checks": self.failed_checks,
            "missing_items": self.missing_items,
            "remediation_steps": self.remediation_steps,
            "checks": [
                {
                    "article": c.article,
                    "requirement": c.requirement,
                    "status": c.status,
                    "detail": c.detail,
                    "blocking": c.blocking,
                }
                for c in self.checks
            ],
        }


class ConformityAssessment:
    """EU AI Act Conformity Assessment (test-compatible wrapper).
    
    Accepts a ModelRegistry instance and provides test-compatible interface.
    """
    
    def __init__(self, registry: Any) -> None:
        """Initialize with a ModelRegistry instance (not a path)."""
        self.registry = registry
        self._assessor = "ConformityAssessment v1.0"
    
    def assess(
        self,
        model_id: str,
        assessor: str = "test",
        has_logging: bool = False,
        has_transparency_notice: bool = False,
        has_human_oversight: bool = False,
    ) -> ComplianceReport:
        """Run conformity assessment and return test-compatible report."""
        model = self.registry.get_model(model_id)
        risk_tier = model.get("risk_tier", "MINIMAL")
        
        checks: list[Check] = []
        missing_items: list[str] = []
        remediation_steps: list[str] = []
        
        # Article 5 -- Prohibited practices
        if risk_tier == "PROHIBITED":
            checks.append(Check(
                article="Art. 5",
                requirement="Model not prohibited",
                status="fail",
                detail="This model is PROHIBITED and cannot be deployed",
                blocking=True,
            ))
            missing_items.append("Risk tier must not be PROHIBITED")
            remediation_steps.append("Reclassify model risk tier")
        else:
            checks.append(Check(
                article="Art. 5",
                requirement="Model not prohibited",
                status="pass",
                detail=f"Risk tier: {risk_tier}",
            ))
        
        if risk_tier == "MINIMAL":
            # Minimal risk auto-passes
            compliance_score = 100.0
            failed_checks = 0
        elif risk_tier == "LIMITED":
            # Limited risk has fewer requirements
            checks.append(Check(
                article="Art. 52",
                requirement="Transparency notice",
                status="pass" if has_transparency_notice else "fail",
                detail="User informed of AI use",
            ))
            failed = sum(1 for c in checks if c.status == "fail")
            compliance_score = 100.0 - (failed * 10.0)
            failed_checks = failed
        else:  # HIGH or default
            # High-risk requires full compliance
            
            # Check purpose/documentation
            if model.get("purpose"):
                checks.append(Check(
                    article="Art. 9",
                    requirement="Risk identification documented",
                    status="pass",
                    detail=model.get("purpose", "")[:80],
                ))
            else:
                checks.append(Check(
                    article="Art. 9",
                    requirement="Risk identification documented",
                    status="fail",
                    detail="No purpose documented",
                    blocking=True,
                ))
                missing_items.append("Model purpose")
                remediation_steps.append("Document the purpose of the model")
            
            # Check training data
            if model.get("training_data_description"):
                checks.append(Check(
                    article="Art. 10",
                    requirement="Training data documented",
                    status="pass",
                    detail=model.get("training_data_description", "")[:80],
                ))
            else:
                checks.append(Check(
                    article="Art. 10",
                    requirement="Training data documented",
                    status="fail",
                    detail="No training data documentation",
                    blocking=True,
                ))
                missing_items.append("Training data description")
                remediation_steps.append("Document training data characteristics")
            
            # Check performance metrics
            metrics = model.get("performance_metrics", {})
            accuracy = metrics.get("accuracy", 0)
            
            if accuracy >= 0.75:
                checks.append(Check(
                    article="Art. 15",
                    requirement="Accuracy >= 0.75",
                    status="pass",
                    detail=f"Actual: {accuracy}",
                ))
            else:
                checks.append(Check(
                    article="Art. 15",
                    requirement="Accuracy >= 0.75",
                    status="fail",
                    detail=f"Actual: {accuracy}",
                    blocking=True,
                ))
                missing_items.append("Minimum accuracy threshold")
                remediation_steps.append("Improve model accuracy to >= 0.75")
            
            # Check logging
            if has_logging:
                checks.append(Check(
                    article="Art. 12",
                    requirement="Decision logging configured",
                    status="pass",
                    detail="Decision logging active",
                ))
            else:
                checks.append(Check(
                    article="Art. 12",
                    requirement="Decision logging configured",
                    status="fail",
                    detail="Decision logging not configured",
                ))
                missing_items.append("Decision logging system")
                remediation_steps.append("Implement decision logging")
            
            # Check transparency notice
            if has_transparency_notice:
                checks.append(Check(
                    article="Art. 13",
                    requirement="Transparency notice provided",
                    status="pass",
                    detail="Users notified of AI use",
                ))
            else:
                checks.append(Check(
                    article="Art. 13",
                    requirement="Transparency notice provided",
                    status="fail",
                    detail="No transparency notice",
                ))
                missing_items.append("Transparency notice")
                remediation_steps.append("Add transparency notice to user interface")
            
            # Check human oversight
            if has_human_oversight:
                checks.append(Check(
                    article="Art. 14",
                    requirement="Human oversight mechanism",
                    status="pass",
                    detail="Human review workflow active",
                ))
            else:
                checks.append(Check(
                    article="Art. 14",
                    requirement="Human oversight mechanism",
                    status="fail",
                    detail="No human oversight",
                ))
                missing_items.append("Human oversight mechanism")
                remediation_steps.append("Implement human review workflow")
            
            # Calculate compliance score
            failed = sum(1 for c in checks if c.status == "fail")
            failed_checks = failed
            compliance_score = max(0.0, 100.0 - (failed * 20.0))
        
        return ComplianceReport(
            assessment_id=str(uuid.uuid4()),
            model_id=model_id,
            model_name=f"{model.get('name', 'unknown')} v{model.get('version', '?')}",
            risk_tier=risk_tier,
            assessment_date=datetime.now(timezone.utc).isoformat(),
            assessor=assessor,
            compliance_score=compliance_score,
            failed_checks=failed_checks,
            missing_items=missing_items,
            remediation_steps=remediation_steps,
            checks=checks,
        )
    
    def report_to_json(self, report: ComplianceReport) -> str:
        """Convert report to JSON."""
        return json.dumps(report.to_dict(), indent=2)
    
    def close(self) -> None:
        """Close registry connection."""
        if hasattr(self.registry, 'close'):
            self.registry.close()


# Keep the old alias for backward compatibility
ConformityChecker_Original = ConformityChecker
# Override ConformityChecker in the module to avoid confusion
# (but keep original available)
