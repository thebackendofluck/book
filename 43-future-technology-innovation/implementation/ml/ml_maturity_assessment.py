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
AI/ML Maturity Assessment Tool for iGaming Organizations
==========================================================

Assesses an organization's AI/ML maturity across 5 levels
(ad-hoc, repeatable, defined, managed, optimizing) with detailed
scoring per capability domain. Produces gap analysis and roadmap.

Covers:
- 8 capability domains assessed across 5 maturity levels
- Scoring rubric with evidence-based assessment
- Gap analysis with prioritized improvement recommendations
- Investment roadmap generation
- Benchmark comparison against industry averages
- Executive summary for board reporting

Maturity Levels (based on CMMI and Google MLOps frameworks):
  Level 1 - Ad-Hoc: Manual, no processes, individual efforts
  Level 2 - Repeatable: Some documented processes, basic tooling
  Level 3 - Defined: Standardized processes, centralized platform
  Level 4 - Managed: Measured, monitored, automated pipelines
  Level 5 - Optimizing: Continuous improvement, advanced automation

Feasibility Assessment:
- Assessment framework is a structured questionnaire + scoring engine
- No ML required - uses weighted scoring and gap analysis
- Output feeds into strategic planning and budget requests
- No external dependencies

Dependencies: None
"""

import json
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core types
# ---------------------------------------------------------------------------

class MaturityLevel(Enum):
    AD_HOC = 1
    REPEATABLE = 2
    DEFINED = 3
    MANAGED = 4
    OPTIMIZING = 5


class CapabilityDomain(Enum):
    DATA_MANAGEMENT = "data_management"
    MODEL_DEVELOPMENT = "model_development"
    MLOPS_INFRASTRUCTURE = "mlops_infrastructure"
    MONITORING_OBSERVABILITY = "monitoring_observability"
    GOVERNANCE_COMPLIANCE = "governance_compliance"
    TALENT_ORGANIZATION = "talent_organization"
    BUSINESS_INTEGRATION = "business_integration"
    RESPONSIBLE_AI = "responsible_ai"


class InvestmentPriority(Enum):
    CRITICAL = "critical"      # blocking regulatory compliance
    HIGH = "high"              # needed for next maturity level
    MEDIUM = "medium"          # improves efficiency
    LOW = "low"                # nice to have


@dataclass
class CapabilityAssessment:
    """Assessment result for a single capability within a domain."""
    capability: str
    description: str
    current_level: MaturityLevel
    target_level: MaturityLevel
    evidence: str = ""
    gap: int = 0  # target - current
    recommendation: str = ""
    investment_priority: InvestmentPriority = InvestmentPriority.MEDIUM
    estimated_effort_months: int = 0


@dataclass
class DomainAssessment:
    """Assessment result for a full capability domain."""
    domain: CapabilityDomain
    capabilities: list[CapabilityAssessment] = field(default_factory=list)
    avg_current_level: float = 0.0
    avg_target_level: float = 0.0
    maturity_level: MaturityLevel = MaturityLevel.AD_HOC
    gap_score: float = 0.0
    top_priorities: list[str] = field(default_factory=list)


@dataclass
class MaturityReport:
    """Complete maturity assessment report."""
    organization_name: str
    assessment_date: str
    overall_maturity: MaturityLevel
    overall_score: float
    domain_assessments: list[DomainAssessment] = field(default_factory=list)
    executive_summary: str = ""
    roadmap: list[dict] = field(default_factory=list)
    total_investment_estimate: dict = field(default_factory=dict)
    industry_benchmark: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Assessment criteria
# ---------------------------------------------------------------------------

# {domain: [(capability_name, description, {level: criteria_text})]}
ASSESSMENT_CRITERIA: dict[CapabilityDomain, list[tuple[str, str, dict[int, str]]]] = {
    CapabilityDomain.DATA_MANAGEMENT: [
        ("data_collection", "Player and operational data collection", {
            1: "Manual data exports, CSV files, no schema management",
            2: "Basic ETL pipelines, some automated collection",
            3: "Centralized data warehouse, schema registry, data catalog",
            4: "Real-time streaming (Kafka), feature store, data quality monitoring",
            5: "Self-healing pipelines, automated data discovery, lineage tracking",
        }),
        ("data_quality", "Data quality assurance and monitoring", {
            1: "No data quality checks, issues found ad-hoc",
            2: "Basic validation rules, manual quality reviews",
            3: "Automated data quality tests (Great Expectations), SLAs defined",
            4: "Real-time data quality monitoring, anomaly detection, automated remediation",
            5: "ML-powered data quality, predictive issue detection, zero-touch resolution",
        }),
        ("data_governance", "Data governance and access control", {
            1: "No governance framework, unrestricted data access",
            2: "Basic access controls, some documentation",
            3: "Data ownership defined, RBAC, classification scheme",
            4: "Automated policy enforcement, audit trails, GDPR/DPA compliant",
            5: "Dynamic governance, automated compliance checking, privacy-by-design",
        }),
        ("feature_engineering", "Feature store and reusable features", {
            1: "Features computed in notebooks, no reuse",
            2: "Shared feature scripts, some documentation",
            3: "Centralized feature store (Feast/Tecton), versioned features",
            4: "Real-time feature computation, online/offline consistency",
            5: "Auto-feature engineering, feature marketplace, impact tracking",
        }),
    ],
    CapabilityDomain.MODEL_DEVELOPMENT: [
        ("experimentation", "Experiment tracking and reproducibility", {
            1: "No experiment tracking, results in spreadsheets",
            2: "Basic MLflow/W&B tracking, manual logging",
            3: "Standardized experiment framework, versioned datasets",
            4: "Automated experiment pipelines, hyperparameter optimization",
            5: "AutoML, neural architecture search, multi-objective optimization",
        }),
        ("model_registry", "Model versioning and registry", {
            1: "Models saved as files, no versioning",
            2: "Git-based model storage, manual versioning",
            3: "MLflow Model Registry, stage transitions, approval workflows",
            4: "Automated model promotion, A/B testing integration",
            5: "Multi-model orchestration, ensemble management, auto-retirement",
        }),
        ("training_pipeline", "Model training automation", {
            1: "Manual training in notebooks",
            2: "Scripted training, some automation",
            3: "CI/CD for model training, scheduled retraining",
            4: "Trigger-based retraining (data drift), distributed training",
            5: "Continuous learning, online learning, self-optimizing pipelines",
        }),
        ("validation_testing", "Model validation and testing", {
            1: "Basic accuracy checks, no systematic testing",
            2: "Train/test split, standard metrics reported",
            3: "Cross-validation, fairness testing, adversarial testing",
            4: "Automated model comparison, regression detection, shadow deployment",
            5: "Continuous model evaluation, population stability monitoring",
        }),
    ],
    CapabilityDomain.MLOPS_INFRASTRUCTURE: [
        ("serving_infrastructure", "Model serving and deployment", {
            1: "Manual deployment, models in application code",
            2: "Containerized models, basic API serving",
            3: "Dedicated serving infrastructure (Triton, Seldon), canary deployment",
            4: "Auto-scaling, multi-model serving, A/B testing",
            5: "Edge deployment, serverless inference, global model distribution",
        }),
        ("ci_cd_ml", "CI/CD for ML pipelines", {
            1: "No CI/CD for ML, manual processes",
            2: "Basic CI for model code, manual deployment",
            3: "Automated training + validation pipelines, GitOps",
            4: "Full MLOps pipeline with automated promotion and rollback",
            5: "Self-healing pipelines, automated architecture selection",
        }),
        ("compute_management", "GPU/compute resource management", {
            1: "Local development, no GPU management",
            2: "Cloud GPUs provisioned manually",
            3: "Kubernetes-based training (Kubeflow), resource quotas",
            4: "Auto-scaling GPU clusters, spot instance optimization",
            5: "Multi-cloud compute orchestration, cost-optimized scheduling",
        }),
    ],
    CapabilityDomain.MONITORING_OBSERVABILITY: [
        ("model_monitoring", "Production model monitoring", {
            1: "No monitoring, issues found by users",
            2: "Basic accuracy tracking, manual checks",
            3: "Automated drift detection (data + concept), alerting",
            4: "Real-time performance dashboards, SLO tracking",
            5: "Predictive model degradation, automated retraining triggers",
        }),
        ("data_drift", "Data and concept drift detection", {
            1: "No drift detection",
            2: "Periodic manual comparison of data distributions",
            3: "Automated statistical tests (KS, PSI), scheduled checks",
            4: "Real-time drift monitoring, multi-variate analysis",
            5: "Adaptive thresholds, causal drift analysis, auto-remediation",
        }),
        ("observability_stack", "End-to-end ML observability", {
            1: "No ML-specific observability",
            2: "Basic logging of predictions and latency",
            3: "Integrated ML observability (Arize, Evidently), dashboards",
            4: "Full request tracing, feature attribution in production",
            5: "Root cause analysis automation, impact correlation",
        }),
    ],
    CapabilityDomain.GOVERNANCE_COMPLIANCE: [
        ("model_governance", "Model governance framework", {
            1: "No governance, models deployed without review",
            2: "Basic review process, informal approvals",
            3: "Model risk committee, documented approval workflow",
            4: "Automated compliance checks, regulatory model inventory",
            5: "Real-time governance, automated regulatory reporting",
        }),
        ("audit_trail", "Decision audit trail and explainability", {
            1: "No audit trail for model decisions",
            2: "Basic logging of predictions",
            3: "Full audit trail with feature inputs and model outputs",
            4: "Explainability reports (SHAP/LIME), regulatory-ready documentation",
            5: "Real-time explainability, counterfactual analysis, interactive audit",
        }),
        ("regulatory_alignment", "Alignment with gambling regulations", {
            1: "No awareness of AI-related gambling regulations",
            2: "Basic understanding, reactive compliance",
            3: "Proactive compliance mapping, DPIA for AI systems",
            4: "Continuous regulatory monitoring, automated compliance checks",
            5: "Regulatory technology leadership, industry standards contribution",
        }),
    ],
    CapabilityDomain.TALENT_ORGANIZATION: [
        ("team_structure", "Data science / ML engineering team", {
            1: "No dedicated ML roles, ad-hoc analysis",
            2: "1-2 data scientists, limited engineering support",
            3: "Cross-functional ML team (DS, MLE, DE), defined roles",
            4: "ML platform team, embedded ML engineers in product teams",
            5: "ML Center of Excellence, research team, external collaboration",
        }),
        ("skills_training", "ML skills and continuous learning", {
            1: "No ML training programs",
            2: "Ad-hoc training, conference attendance",
            3: "Structured learning paths, internal ML workshops",
            4: "ML certification programs, knowledge sharing platforms",
            5: "Research publication, patent programs, academic partnerships",
        }),
    ],
    CapabilityDomain.BUSINESS_INTEGRATION: [
        ("use_case_portfolio", "ML use case portfolio management", {
            1: "No portfolio view, isolated experiments",
            2: "List of ML projects, basic prioritization",
            3: "Portfolio with ROI tracking, business sponsor for each use case",
            4: "Systematic use case discovery, impact measurement framework",
            5: "AI-first strategy, ML embedded in all business decisions",
        }),
        ("business_impact", "Measuring ML business impact", {
            1: "No impact measurement",
            2: "Basic before/after comparisons",
            3: "A/B testing with business metrics, attribution models",
            4: "Causal impact analysis, continuous ROI monitoring",
            5: "ML-driven P&L attribution, board-level ML dashboards",
        }),
    ],
    CapabilityDomain.RESPONSIBLE_AI: [
        ("fairness_evaluation", "Bias detection and fairness metrics", {
            1: "No fairness evaluation of ML models",
            2: "Basic demographic analysis of model outputs",
            3: "Systematic fairness metrics (disparate impact, equalized odds)",
            4: "Continuous fairness monitoring, bias alerts in production",
            5: "Fairness-constrained training, intersectional analysis, industry leadership",
        }),
        ("responsible_gambling_ai", "AI for responsible gambling", {
            1: "No AI-driven player protection",
            2: "Basic rules for intervention triggers",
            3: "ML-based player risk scoring, behavioral markers",
            4: "Real-time intervention engine, multi-signal risk assessment",
            5: "Personalized protection, predictive harm prevention, research collaboration",
        }),
    ],
}

# Industry benchmark averages (for comparison)
INDUSTRY_BENCHMARKS = {
    CapabilityDomain.DATA_MANAGEMENT: 2.8,
    CapabilityDomain.MODEL_DEVELOPMENT: 2.5,
    CapabilityDomain.MLOPS_INFRASTRUCTURE: 2.2,
    CapabilityDomain.MONITORING_OBSERVABILITY: 2.0,
    CapabilityDomain.GOVERNANCE_COMPLIANCE: 2.3,
    CapabilityDomain.TALENT_ORGANIZATION: 2.5,
    CapabilityDomain.BUSINESS_INTEGRATION: 2.4,
    CapabilityDomain.RESPONSIBLE_AI: 1.8,
}


# ---------------------------------------------------------------------------
# Assessment engine
# ---------------------------------------------------------------------------

class MLMaturityAssessor:
    """
    Conducts ML maturity assessment and generates improvement roadmap.

    Usage:
        1. Instantiate assessor
        2. Provide scores for each capability (1-5 maturity level)
        3. Set target levels per capability
        4. Generate report with gap analysis and roadmap
    """

    def assess(
        self,
        organization_name: str,
        scores: dict[CapabilityDomain, dict[str, int]],
        targets: Optional[dict[CapabilityDomain, dict[str, int]]] = None,
    ) -> MaturityReport:
        """
        Run full maturity assessment.

        Args:
            organization_name: Name of the organization
            scores: {domain: {capability_name: current_level_1_to_5}}
            targets: {domain: {capability_name: target_level_1_to_5}} (optional)
        """
        if targets is None:
            # Default: target one level above current, capped at 5
            targets = {}
            for domain, caps in scores.items():
                targets[domain] = {k: min(v + 1, 5) for k, v in caps.items()}

        domain_assessments = []
        all_priorities = []

        for domain, criteria in ASSESSMENT_CRITERIA.items():
            domain_scores = scores.get(domain, {})
            domain_targets = targets.get(domain, {})
            capabilities = []

            for cap_name, description, level_criteria in criteria:
                current = domain_scores.get(cap_name, 1)
                target = domain_targets.get(cap_name, min(current + 1, 5))
                gap = target - current

                current_level = MaturityLevel(current)
                target_level = MaturityLevel(target)

                # Determine priority
                if gap >= 3:
                    priority = InvestmentPriority.CRITICAL
                elif gap == 2:
                    priority = InvestmentPriority.HIGH
                elif gap == 1:
                    priority = InvestmentPriority.MEDIUM
                else:
                    priority = InvestmentPriority.LOW

                # Gambling-specific: governance and responsible AI get priority boost
                if domain in (CapabilityDomain.GOVERNANCE_COMPLIANCE, CapabilityDomain.RESPONSIBLE_AI):
                    if priority == InvestmentPriority.MEDIUM:
                        priority = InvestmentPriority.HIGH
                    elif priority == InvestmentPriority.HIGH:
                        priority = InvestmentPriority.CRITICAL

                # Effort estimate (months)
                effort = gap * 3  # rough: 3 months per level gap

                recommendation = self._generate_recommendation(
                    cap_name, current_level, target_level, level_criteria
                )

                cap_assessment = CapabilityAssessment(
                    capability=cap_name,
                    description=description,
                    current_level=current_level,
                    target_level=target_level,
                    evidence=level_criteria.get(current, ""),
                    gap=gap,
                    recommendation=recommendation,
                    investment_priority=priority,
                    estimated_effort_months=effort,
                )
                capabilities.append(cap_assessment)

                if gap > 0:
                    all_priorities.append((cap_assessment, domain))

            # Domain-level aggregation
            if capabilities:
                avg_current = sum(c.current_level.value for c in capabilities) / len(capabilities)
                avg_target = sum(c.target_level.value for c in capabilities) / len(capabilities)
                gap_score = avg_target - avg_current
                maturity = MaturityLevel(round(avg_current))
            else:
                avg_current = 1.0
                avg_target = 2.0
                gap_score = 1.0
                maturity = MaturityLevel.AD_HOC

            top_priorities = [
                c.capability for c in sorted(
                    capabilities, key=lambda x: x.gap, reverse=True
                )[:3] if c.gap > 0
            ]

            domain_assessments.append(DomainAssessment(
                domain=domain,
                capabilities=capabilities,
                avg_current_level=round(avg_current, 2),
                avg_target_level=round(avg_target, 2),
                maturity_level=maturity,
                gap_score=round(gap_score, 2),
                top_priorities=top_priorities,
            ))

        # Overall score
        overall_score = sum(da.avg_current_level for da in domain_assessments) / len(domain_assessments)
        overall_maturity = MaturityLevel(round(overall_score))

        # Investment estimate
        total_effort = sum(c.estimated_effort_months for c, _ in all_priorities)
        investment = self._estimate_investment(all_priorities)

        # Roadmap
        roadmap = self._generate_roadmap(all_priorities)

        # Benchmark
        benchmark = {
            domain.value: {
                "your_score": round(da.avg_current_level, 2),
                "industry_avg": INDUSTRY_BENCHMARKS.get(domain, 2.0),
                "vs_industry": "above" if da.avg_current_level > INDUSTRY_BENCHMARKS.get(domain, 2.0) else "below",
            }
            for da, domain in zip(domain_assessments, ASSESSMENT_CRITERIA.keys())
        }

        # Executive summary
        summary = self._generate_executive_summary(
            organization_name, overall_maturity, overall_score, domain_assessments
        )

        return MaturityReport(
            organization_name=organization_name,
            assessment_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            overall_maturity=overall_maturity,
            overall_score=round(overall_score, 2),
            domain_assessments=domain_assessments,
            executive_summary=summary,
            roadmap=roadmap,
            total_investment_estimate=investment,
            industry_benchmark=benchmark,
        )

    def _generate_recommendation(
        self, cap_name: str, current: MaturityLevel, target: MaturityLevel,
        criteria: dict[int, str],
    ) -> str:
        if current == target:
            return f"Maintain current {cap_name.replace('_', ' ')} capabilities."

        next_level = min(current.value + 1, 5)
        next_criteria = criteria.get(next_level, "")
        return (
            f"Advance {cap_name.replace('_', ' ')} from Level {current.value} "
            f"({current.name}) to Level {target.value} ({target.name}). "
            f"Next milestone: {next_criteria}"
        )

    def _estimate_investment(self, priorities: list[tuple]) -> dict:
        # Rough cost model: $15K/month per engineer-month
        cost_per_month = 15000
        critical = sum(c.estimated_effort_months for c, _ in priorities if c.investment_priority == InvestmentPriority.CRITICAL)
        high = sum(c.estimated_effort_months for c, _ in priorities if c.investment_priority == InvestmentPriority.HIGH)
        medium = sum(c.estimated_effort_months for c, _ in priorities if c.investment_priority == InvestmentPriority.MEDIUM)

        return {
            "critical_items": {
                "effort_months": critical,
                "estimated_cost": critical * cost_per_month,
            },
            "high_items": {
                "effort_months": high,
                "estimated_cost": high * cost_per_month,
            },
            "medium_items": {
                "effort_months": medium,
                "estimated_cost": medium * cost_per_month,
            },
            "total_effort_months": critical + high + medium,
            "total_estimated_cost": (critical + high + medium) * cost_per_month,
            "recommended_team_size": max(2, round((critical + high) / 12)),
        }

    def _generate_roadmap(self, priorities: list[tuple]) -> list[dict]:
        # Sort by priority then gap size
        priority_order = {
            InvestmentPriority.CRITICAL: 0,
            InvestmentPriority.HIGH: 1,
            InvestmentPriority.MEDIUM: 2,
            InvestmentPriority.LOW: 3,
        }
        sorted_items = sorted(priorities, key=lambda x: (priority_order[x[0].investment_priority], -x[0].gap))

        roadmap = []
        quarter = 1
        months_in_quarter = 0

        for cap, domain in sorted_items:
            if cap.gap <= 0:
                continue

            if months_in_quarter + cap.estimated_effort_months > 9:
                quarter += 1
                months_in_quarter = 0

            roadmap.append({
                "quarter": f"Q{quarter}",
                "domain": domain.value,
                "capability": cap.capability,
                "current": cap.current_level.name,
                "target": cap.target_level.name,
                "priority": cap.investment_priority.value,
                "effort_months": cap.estimated_effort_months,
                "action": cap.recommendation[:120],
            })
            months_in_quarter += cap.estimated_effort_months

        return roadmap

    def _generate_executive_summary(
        self, org: str, maturity: MaturityLevel, score: float,
        assessments: list[DomainAssessment],
    ) -> str:
        strengths = [da.domain.value for da in assessments if da.avg_current_level >= 3.0]
        weaknesses = [da.domain.value for da in assessments if da.avg_current_level < 2.0]

        summary = (
            f"{org} is assessed at ML Maturity Level {maturity.value} ({maturity.name}), "
            f"with an overall score of {score:.1f}/5.0. "
        )

        if strengths:
            summary += f"Strengths: {', '.join(s.replace('_', ' ') for s in strengths)}. "
        if weaknesses:
            summary += f"Priority gaps: {', '.join(w.replace('_', ' ') for w in weaknesses)}. "

        summary += (
            "Recommendation: focus investment on governance/compliance and responsible AI "
            "capabilities, as these directly impact gambling license conditions."
        )

        return summary


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def demo():
    """Simulate ML maturity assessment for an iGaming operator."""

    assessor = MLMaturityAssessor()

    print("\n" + "=" * 70)
    print("  AI/ML Maturity Assessment Tool - iGaming Operator")
    print("=" * 70)

    # Simulated assessment scores for a mid-size operator
    scores = {
        CapabilityDomain.DATA_MANAGEMENT: {
            "data_collection": 3,
            "data_quality": 2,
            "data_governance": 2,
            "feature_engineering": 2,
        },
        CapabilityDomain.MODEL_DEVELOPMENT: {
            "experimentation": 2,
            "model_registry": 2,
            "training_pipeline": 1,
            "validation_testing": 2,
        },
        CapabilityDomain.MLOPS_INFRASTRUCTURE: {
            "serving_infrastructure": 2,
            "ci_cd_ml": 1,
            "compute_management": 2,
        },
        CapabilityDomain.MONITORING_OBSERVABILITY: {
            "model_monitoring": 1,
            "data_drift": 1,
            "observability_stack": 1,
        },
        CapabilityDomain.GOVERNANCE_COMPLIANCE: {
            "model_governance": 1,
            "audit_trail": 2,
            "regulatory_alignment": 2,
        },
        CapabilityDomain.TALENT_ORGANIZATION: {
            "team_structure": 2,
            "skills_training": 2,
        },
        CapabilityDomain.BUSINESS_INTEGRATION: {
            "use_case_portfolio": 2,
            "business_impact": 1,
        },
        CapabilityDomain.RESPONSIBLE_AI: {
            "fairness_evaluation": 1,
            "responsible_gambling_ai": 2,
        },
    }

    # Target: Level 3-4 for most capabilities
    targets = {
        CapabilityDomain.DATA_MANAGEMENT: {
            "data_collection": 4, "data_quality": 4,
            "data_governance": 4, "feature_engineering": 3,
        },
        CapabilityDomain.MODEL_DEVELOPMENT: {
            "experimentation": 3, "model_registry": 3,
            "training_pipeline": 3, "validation_testing": 3,
        },
        CapabilityDomain.MLOPS_INFRASTRUCTURE: {
            "serving_infrastructure": 3, "ci_cd_ml": 3,
            "compute_management": 3,
        },
        CapabilityDomain.MONITORING_OBSERVABILITY: {
            "model_monitoring": 3, "data_drift": 3,
            "observability_stack": 3,
        },
        CapabilityDomain.GOVERNANCE_COMPLIANCE: {
            "model_governance": 4, "audit_trail": 4,
            "regulatory_alignment": 4,
        },
        CapabilityDomain.TALENT_ORGANIZATION: {
            "team_structure": 3, "skills_training": 3,
        },
        CapabilityDomain.BUSINESS_INTEGRATION: {
            "use_case_portfolio": 3, "business_impact": 3,
        },
        CapabilityDomain.RESPONSIBLE_AI: {
            "fairness_evaluation": 4, "responsible_gambling_ai": 4,
        },
    }

    report = assessor.assess("Acme Casino Group", scores, targets)

    # Display results
    print(f"\n  Organization: {report.organization_name}")
    print(f"  Assessment Date: {report.assessment_date}")
    print(f"  Overall Maturity: Level {report.overall_maturity.value} ({report.overall_maturity.name})")
    print(f"  Overall Score: {report.overall_score}/5.0")

    print(f"\n  Executive Summary:")
    print(f"  {report.executive_summary}")

    print(f"\n  Domain Scores:")
    for da in report.domain_assessments:
        bar_len = int(da.avg_current_level * 4)
        target_bar = int(da.avg_target_level * 4)
        bar = "#" * bar_len + "." * (20 - bar_len)
        benchmark = INDUSTRY_BENCHMARKS.get(da.domain, 2.0)
        vs = "+" if da.avg_current_level >= benchmark else "-"
        print(f"    {da.domain.value:30s} [{bar}] {da.avg_current_level:.1f}/{da.avg_target_level:.1f} "
              f"(gap: {da.gap_score:.1f}) [{vs}ind]")
        if da.top_priorities:
            print(f"      Priorities: {', '.join(da.top_priorities)}")

    print(f"\n  Investment Estimate:")
    inv = report.total_investment_estimate
    print(f"    Critical items: {inv['critical_items']['effort_months']} months "
          f"(${inv['critical_items']['estimated_cost']:,.0f})")
    print(f"    High items: {inv['high_items']['effort_months']} months "
          f"(${inv['high_items']['estimated_cost']:,.0f})")
    print(f"    Total: {inv['total_effort_months']} months "
          f"(${inv['total_estimated_cost']:,.0f})")
    print(f"    Recommended team size: {inv['recommended_team_size']} engineers")

    print(f"\n  Improvement Roadmap (top 10 items):")
    for item in report.roadmap[:10]:
        print(f"    [{item['quarter']}] {item['priority'].upper():8s} "
              f"{item['domain']:25s} {item['capability']}")
        print(f"           {item['current']} -> {item['target']} ({item['effort_months']}mo)")

    print(f"\n  Industry Benchmark Comparison:")
    for domain, bench in report.industry_benchmark.items():
        icon = "+" if bench["vs_industry"] == "above" else "-"
        print(f"    [{icon}] {domain:30s} You: {bench['your_score']:.1f} | "
              f"Industry: {bench['industry_avg']:.1f}")

    print(f"\n  Usage: Run quarterly to track progress against roadmap.")
    print("  Export to PowerPoint/PDF for board and regulator presentations.\n")


if __name__ == "__main__":
    demo()
