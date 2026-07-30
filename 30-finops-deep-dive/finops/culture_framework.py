# Companion code for "The Backend of Luck" - Chapter 30, FinOps Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
FinOps Culture and Governance Framework
=======================================

Establishes FinOps culture and governance structures for
iGaming organizations to drive cost-conscious engineering.

This module provides:
- FinOps Center of Excellence setup
- Decision-making frameworks
- Training and education programs
- Cost awareness initiatives
- Accountability and continuous improvement

Example:
    config = {
        "organization_size": "enterprise",
        "business_units": ["casino", "sports", "marketing"],
        "maturity_target": "run"
    }

    framework = FinOpsCultureFramework(config)
    culture = await framework.establish_finops_culture()
"""

from typing import Dict, List, Optional, Any
from dataclasses import dataclass
import asyncio


@dataclass
class FinOpsRole:
    """Definition of a FinOps role."""
    title: str
    responsibilities: List[str]
    required_skills: List[str]
    reports_to: str


@dataclass
class TrainingModule:
    """FinOps training module definition."""
    name: str
    target_audience: str
    duration_hours: int
    topics: List[str]
    certification: bool


class FinOpsCultureFramework:
    """
    FinOps culture and governance framework for iGaming organizations.

    Establishes the organizational structures, processes, and culture
    needed for effective cloud financial management.

    Attributes:
        config (Dict): Culture framework configuration
        governance_model: Internal governance model
    """

    def __init__(self, culture_config: Dict):
        """
        Initialize the FinOps culture framework.

        Args:
            culture_config: Configuration dictionary containing:
                - organization_size: Size of the organization
                - business_units: List of business units
                - maturity_target: Target maturity level (crawl/walk/run)
        """
        self.config = culture_config
        self.governance_model = self._initialize_governance_model()

    def _initialize_governance_model(self):
        """Initialize the governance model."""
        return {
            "initialized": True,
            "framework": "finops_foundation",
            "maturity_model": "capability_based"
        }

    async def establish_finops_culture(self) -> Dict:
        """
        Establish FinOps culture and governance.

        Creates the organizational structures, training programs,
        and processes needed for effective FinOps practices.

        Returns:
            Dict containing:
                - governance_structure: Organizational setup
                - training_program: Education initiatives
                - cost_awareness: Awareness programs
                - accountability: Accountability framework
                - continuous_improvement: Improvement processes
                - culture_maturity_score: Overall maturity assessment
        """

        # Governance structure
        governance_structure = await self._establish_governance_structure()

        # Training and education
        training_program = await self._implement_training_program()

        # Cost awareness initiatives
        cost_awareness = await self._implement_cost_awareness()

        # Accountability framework
        accountability = await self._establish_accountability()

        # Continuous improvement
        continuous_improvement = await self._implement_continuous_improvement()

        return {
            "governance_structure": governance_structure,
            "training_program": training_program,
            "cost_awareness": cost_awareness,
            "accountability": accountability,
            "continuous_improvement": continuous_improvement,
            "culture_maturity_score": self._assess_culture_maturity([
                governance_structure, training_program, cost_awareness,
                accountability, continuous_improvement
            ])
        }

    async def _establish_governance_structure(self) -> Dict:
        """
        Establish FinOps governance structure.

        Creates the Center of Excellence, decision frameworks,
        and reporting structures for FinOps.

        Returns:
            Dict containing governance components
        """

        # FinOps Center of Excellence
        finops_coe = {
            "leadership": {
                "finops_director": True,
                "cloud_cost_analysts": 3,
                "business_unit_liaisons": 5,
                "technical_architects": 2
            },
            "responsibilities": [
                "cost_optimization_strategy",
                "cloud_budget_management",
                "cost_allocation_methodology",
                "vendor_negotiation",
                "finops_training_program",
                "cost_reporting_standards"
            ],
            "authority_level": "enterprise_wide"
        }

        # Decision-making framework
        decision_framework = {
            "cost_thresholds": {
                "individual_decisions": 5000,  # EUR 5K individual authority
                "team_approval": 25000,        # EUR 25K team approval
                "executive_approval": 100000   # EUR 100K executive approval
            },
            "approval_workflows": {
                "infrastructure_changes": "automated_approval",
                "new_service_adoption": "architectural_review",
                "cost_center_changes": "finance_approval",
                "vendor_contracts": "legal_procurement_review"
            },
            "escalation_procedures": {
                "cost_overruns": {
                    "5_percent_threshold": "team_alert",
                    "10_percent_threshold": "management_alert",
                    "20_percent_threshold": "executive_alert"
                }
            }
        }

        # Reporting structure
        reporting_structure = {
            "frequency": {
                "daily": ["cost_anomalies", "budget_vs_actual"],
                "weekly": ["cost_center_reports", "optimization_opportunities"],
                "monthly": ["executive_summary", "trend_analysis"],
                "quarterly": ["strategic_reviews", "roi_analysis"]
            },
            "audiences": {
                "executives": ["roi_metrics", "strategic_initiatives"],
                "managers": ["team_performance", "budget_compliance"],
                "engineers": ["cost_impact", "optimization_tips"],
                "finance": ["detailed_costs", "budget_forecasting"]
            }
        }

        return {
            "finops_coe": finops_coe,
            "decision_framework": decision_framework,
            "reporting_structure": reporting_structure,
            "governance_effectiveness": await self._measure_governance_effectiveness()
        }

    async def _implement_training_program(self) -> Dict:
        """Implement FinOps training program."""
        return {
            "training_modules": [
                {"name": "FinOps Fundamentals", "audience": "all_staff", "hours": 4},
                {"name": "Cloud Cost Management", "audience": "engineers", "hours": 8},
                {"name": "FinOps for Leaders", "audience": "managers", "hours": 4},
                {"name": "Advanced Cost Optimization", "audience": "finops_team", "hours": 16}
            ],
            "certification_program": True,
            "ongoing_education": True
        }

    async def _implement_cost_awareness(self) -> Dict:
        """Implement cost awareness initiatives."""
        return {
            "dashboards": True,
            "cost_notifications": True,
            "gamification": True,
            "cost_showback": True
        }

    async def _establish_accountability(self) -> Dict:
        """Establish accountability framework."""
        return {
            "cost_ownership": "business_unit",
            "budget_accountability": True,
            "performance_metrics": True,
            "incentive_alignment": True
        }

    async def _implement_continuous_improvement(self) -> Dict:
        """Implement continuous improvement processes."""
        return {
            "regular_reviews": "monthly",
            "optimization_sprints": True,
            "feedback_loops": True,
            "benchmark_tracking": True
        }

    async def _measure_governance_effectiveness(self) -> float:
        """Measure governance effectiveness."""
        return 0.85  # 85% effectiveness

    def _assess_culture_maturity(self, components: List[Dict]) -> float:
        """Assess overall culture maturity score."""
        return 0.75  # 75% maturity (Walk stage)
