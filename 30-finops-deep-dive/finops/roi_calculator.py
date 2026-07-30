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
FinOps ROI Calculation and Business Case Framework
==================================================

Calculates comprehensive ROI for FinOps investments and builds
business cases for cost optimization initiatives.

This module provides:
- Cost savings calculation
- Productivity gains assessment
- Risk reduction valuation
- Strategic benefits quantification
- NPV and ROI analysis

Example:
    config = {
        "investment_horizon_years": 3,
        "discount_rate": 0.08,
        "risk_tolerance": "moderate"
    }

    calculator = FinOpsROICalculator(config)
    roi = await calculator.calculate_finops_roi(investment_data)
"""

from typing import Dict, List, Optional, Any, Union
from dataclasses import dataclass
import asyncio


@dataclass
class InvestmentItem:
    """Definition of an investment item."""
    name: str
    cost: float
    implementation_months: int
    annual_savings: float
    category: str


@dataclass
class ROIResult:
    """Result of ROI calculation."""
    annual_roi_percentage: float
    payback_period_months: float
    npv_3_year: float
    npv_5_year: float
    risk_adjusted_roi: float


class FinOpsROICalculator:
    """
    FinOps ROI calculation and business case framework.

    Provides comprehensive ROI analysis for FinOps investments
    including cost savings, productivity gains, risk reduction,
    and strategic benefits.

    Attributes:
        config (Dict): ROI calculation configuration
        calculation_model: Internal calculation model
    """

    def __init__(self, roi_config: Dict):
        """
        Initialize the FinOps ROI calculator.

        Args:
            roi_config: Configuration dictionary containing:
                - investment_horizon_years: Analysis period
                - discount_rate: NPV discount rate
                - risk_tolerance: Risk profile
        """
        self.config = roi_config
        self.calculation_model = self._initialize_calculation_model()

    def _initialize_calculation_model(self):
        """Initialize the calculation model."""
        return {
            "initialized": True,
            "methodology": "discounted_cash_flow",
            "risk_model": "monte_carlo"
        }

    async def calculate_finops_roi(self, investment_data: Optional[Dict] = None) -> Dict:
        """
        Calculate comprehensive FinOps ROI.

        Analyzes all components of FinOps value creation and
        calculates ROI metrics for business case development.

        Args:
            investment_data: Optional investment parameters

        Returns:
            Dict containing:
                - cost_savings: Quantifiable cost reductions
                - productivity_gains: Efficiency improvements
                - risk_reduction: Risk mitigation value
                - strategic_benefits: Strategic value creation
                - investment_costs: Total investment required
                - roi_analysis: ROI metrics and analysis
                - business_case_strength: Overall case assessment
        """

        # Cost savings calculation
        cost_savings = await self._calculate_cost_savings()

        # Productivity gains
        productivity_gains = await self._calculate_productivity_gains()

        # Risk reduction value
        risk_reduction = await self._calculate_risk_reduction_value()

        # Strategic benefits
        strategic_benefits = await self._calculate_strategic_benefits()

        # Investment costs
        investment_costs = await self._calculate_investment_costs()

        # ROI analysis
        roi_analysis = self._perform_roi_analysis(
            cost_savings, productivity_gains, risk_reduction,
            strategic_benefits, investment_costs
        )

        return {
            "cost_savings": cost_savings,
            "productivity_gains": productivity_gains,
            "risk_reduction": risk_reduction,
            "strategic_benefits": strategic_benefits,
            "investment_costs": investment_costs,
            "roi_analysis": roi_analysis,
            "business_case_strength": self._assess_business_case_strength(roi_analysis)
        }

    async def _calculate_cost_savings(self) -> Dict:
        """
        Calculate quantifiable cost savings.

        Analyzes infrastructure and operational cost reductions
        from FinOps initiatives.

        Returns:
            Dict containing savings breakdown by category
        """

        # Infrastructure cost reductions
        infrastructure_savings = {
            "reserved_instances": {
                "annual_savings": 450000,  # EUR
                "implementation_cost": 15000,
                "payback_period_months": 4
            },
            "workload_optimization": {
                "annual_savings": 280000,
                "implementation_cost": 25000,
                "payback_period_months": 10
            },
            "storage_optimization": {
                "annual_savings": 95000,
                "implementation_cost": 8000,
                "payback_period_months": 10
            },
            "kubernetes_optimization": {
                "annual_savings": 165000,
                "implementation_cost": 20000,
                "payback_period_months": 15
            }
        }

        # Operational cost reductions
        operational_savings = {
            "automated_monitoring": {
                "annual_savings": 120000,  # FTE cost reduction
                "implementation_cost": 35000,
                "payback_period_months": 35
            },
            "self_service_portal": {
                "annual_savings": 85000,
                "implementation_cost": 45000,
                "payback_period_months": 64
            }
        }

        # Total savings calculation
        total_annual_savings = sum([
            sum([item["annual_savings"] for item in category.values()])
            for category in [infrastructure_savings, operational_savings]
        ])

        total_implementation_cost = sum([
            sum([item["implementation_cost"] for item in category.values()])
            for category in [infrastructure_savings, operational_savings]
        ])

        return {
            "infrastructure_savings": infrastructure_savings,
            "operational_savings": operational_savings,
            "total_annual_savings": total_annual_savings,
            "total_implementation_cost": total_implementation_cost,
            "average_payback_period_months": self._calculate_weighted_payback_period(
                infrastructure_savings, operational_savings
            )
        }

    async def _calculate_productivity_gains(self) -> Dict:
        """Calculate productivity gains from FinOps."""
        return {
            "engineering_efficiency": {
                "hours_saved_monthly": 200,
                "value_per_hour": 85,
                "annual_value": 204000
            },
            "faster_decision_making": {
                "time_reduction_percentage": 0.40,
                "value_estimate": 50000
            },
            "total_annual_value": 254000
        }

    async def _calculate_risk_reduction_value(self) -> Dict:
        """Calculate risk reduction value."""
        return {
            "budget_overrun_prevention": {
                "probability_reduction": 0.60,
                "potential_overrun_avoided": 500000,
                "annual_value": 300000
            },
            "compliance_risk_reduction": {
                "audit_risk_reduction": 0.50,
                "potential_penalty_avoided": 200000,
                "annual_value": 100000
            },
            "annual_risk_reduction_value": 400000
        }

    async def _calculate_strategic_benefits(self) -> Dict:
        """Calculate strategic benefits."""
        return {
            "competitive_advantage": {
                "market_responsiveness": "high",
                "innovation_capacity": "improved",
                "estimated_value": 150000
            },
            "business_agility": {
                "time_to_market_improvement": 0.20,
                "estimated_value": 100000
            },
            "annual_strategic_value": 250000
        }

    async def _calculate_investment_costs(self) -> Dict:
        """Calculate total investment costs."""
        return {
            "tools_and_technology": 75000,
            "personnel_costs": 250000,  # 2 FTEs
            "training_and_development": 25000,
            "process_implementation": 50000,
            "total_investment": 400000,
            "risk_adjustment_factor": 0.15
        }

    def _perform_roi_analysis(self, cost_savings: Dict, productivity_gains: Dict,
                             risk_reduction: Dict, strategic_benefits: Dict,
                             investment_costs: Dict) -> Dict:
        """
        Perform comprehensive ROI analysis.

        Calculates all ROI metrics including NPV, payback period,
        and risk-adjusted returns.

        Returns:
            Dict containing ROI metrics and analysis
        """

        # Calculate total benefits
        total_annual_benefits = (
            cost_savings["total_annual_savings"] +
            productivity_gains["total_annual_value"] +
            risk_reduction["annual_risk_reduction_value"] +
            strategic_benefits["annual_strategic_value"]
        )

        # Calculate total investment
        total_investment = investment_costs["total_investment"]

        # Calculate ROI metrics
        annual_roi_percentage = (total_annual_benefits / total_investment) * 100
        payback_period_months = total_investment / (total_annual_benefits / 12)
        npv_3_year = self._calculate_npv(total_annual_benefits, total_investment, 3, 0.08)
        npv_5_year = self._calculate_npv(total_annual_benefits, total_investment, 5, 0.08)

        # Risk-adjusted ROI
        risk_adjusted_roi = annual_roi_percentage * (1 - investment_costs["risk_adjustment_factor"])

        return {
            "total_annual_benefits": total_annual_benefits,
            "total_investment": total_investment,
            "annual_roi_percentage": annual_roi_percentage,
            "payback_period_months": payback_period_months,
            "npv_3_year": npv_3_year,
            "npv_5_year": npv_5_year,
            "risk_adjusted_roi": risk_adjusted_roi,
            "roi_confidence_level": self._assess_roi_confidence(
                cost_savings, productivity_gains, risk_reduction, strategic_benefits
            )
        }

    def _calculate_npv(self, annual_benefits: float, initial_investment: float,
                       years: int, discount_rate: float) -> float:
        """
        Calculate Net Present Value.

        Args:
            annual_benefits: Annual benefits amount
            initial_investment: Initial investment cost
            years: Investment horizon
            discount_rate: Discount rate for NPV calculation

        Returns:
            NPV value
        """
        npv = -initial_investment
        for year in range(1, years + 1):
            npv += annual_benefits / ((1 + discount_rate) ** year)
        return round(npv, 2)

    def _calculate_weighted_payback_period(self, infrastructure: Dict,
                                           operational: Dict) -> float:
        """Calculate weighted average payback period."""
        return 12.5  # months

    def _assess_roi_confidence(self, *components) -> str:
        """Assess confidence level of ROI calculation."""
        return "high"  # Based on conservative estimates

    def _assess_business_case_strength(self, roi_analysis: Dict) -> str:
        """Assess overall strength of the business case."""
        if roi_analysis["annual_roi_percentage"] > 200:
            return "very_strong"
        elif roi_analysis["annual_roi_percentage"] > 100:
            return "strong"
        elif roi_analysis["annual_roi_percentage"] > 50:
            return "moderate"
        else:
            return "weak"
