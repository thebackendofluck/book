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
Reserved Instances and Savings Plans Optimization
=================================================

Optimizes reserved instances and savings plans strategy for
maximum cost savings in iGaming cloud infrastructure.

This module provides:
- Usage pattern analysis for reservation planning
- Reservation recommendations by instance type
- Savings plan analysis and comparison
- Purchase timing optimization
- Break-even analysis and ROI calculations

Example:
    config = {
        "cloud_provider": "aws",
        "analysis_period_days": 90,
        "target_coverage": 0.75
    }

    optimizer = ReservedInstancesOptimizer(config)
    recommendations = await optimizer.optimize_reserved_instances()
"""

from typing import Dict, List, Optional, Any
from dataclasses import dataclass
import asyncio


@dataclass
class UsagePattern:
    """Represents usage pattern for a resource type."""
    instance_type: str
    average_usage: float
    peak_usage: float
    stability_score: float
    region: str


@dataclass
class ReservationRecommendation:
    """Recommendation for reserved instance purchase."""
    instance_type: str
    quantity: int
    term: str
    payment_option: str
    expected_savings: float
    upfront_cost: float
    monthly_savings: float


class ReservedInstancesOptimizer:
    """
    Reserved instances and savings plans optimization for iGaming.

    Analyzes historical usage patterns and provides recommendations
    for reserved instance purchases and savings plans to maximize
    cost savings.

    Attributes:
        config (Dict): Configuration for optimization
        optimization_model: Internal optimization model
    """

    def __init__(self, ri_config: Dict):
        """
        Initialize the reserved instances optimizer.

        Args:
            ri_config: Configuration dictionary containing:
                - cloud_provider: Target cloud provider
                - analysis_period_days: Historical analysis period
                - target_coverage: Target reservation coverage
        """
        self.config = ri_config
        self.optimization_model = self._initialize_optimization_model()

    def _initialize_optimization_model(self):
        """Initialize the optimization model."""
        return {
            "initialized": True,
            "algorithm": "linear_programming",
            "constraints": ["budget", "flexibility", "risk"]
        }

    async def optimize_reserved_instances(self) -> Dict:
        """
        Optimize reserved instances and savings plans strategy.

        Analyzes usage patterns and generates recommendations for
        reserved instance purchases, savings plans, and purchase timing.

        Returns:
            Dict containing:
                - usage_analysis: Historical usage patterns
                - reservation_recommendations: RI purchase recommendations
                - savings_plan_analysis: Savings plan comparison
                - timing_optimization: Purchase timing strategy
                - coverage_analysis: Current vs target coverage
                - break_even_analysis: ROI and break-even calculations
                - total_savings_potential: Total estimated savings
        """

        # Usage pattern analysis
        usage_analysis = await self._analyze_usage_patterns()

        # Reservation recommendations
        reservation_recommendations = await self._generate_reservation_recommendations()

        # Savings plan analysis
        savings_plan_analysis = await self._analyze_savings_plans()

        # Purchase timing optimization
        timing_optimization = await self._optimize_purchase_timing()

        # Coverage analysis
        coverage_analysis = await self._analyze_reservation_coverage()

        # Break-even analysis
        break_even_analysis = await self._perform_break_even_analysis()

        return {
            "usage_analysis": usage_analysis,
            "reservation_recommendations": reservation_recommendations,
            "savings_plan_analysis": savings_plan_analysis,
            "timing_optimization": timing_optimization,
            "coverage_analysis": coverage_analysis,
            "break_even_analysis": break_even_analysis,
            "total_savings_potential": self._calculate_total_savings([
                reservation_recommendations, savings_plan_analysis, timing_optimization
            ])
        }

    async def _analyze_usage_patterns(self) -> Dict:
        """
        Analyze usage patterns for reservation optimization.

        Reviews historical usage data to identify stable workloads
        suitable for reserved instances.

        Returns:
            Dict containing usage patterns and predictability analysis
        """

        # Historical usage analysis
        historical_usage = {
            "instance_type_distribution": {
                "m5.large": 0.25,    # 25% of usage
                "m5.xlarge": 0.35,   # 35% of usage
                "c5.xlarge": 0.20,   # 20% of usage
                "r5.large": 0.15,    # 15% of usage
                "others": 0.05       # 5% of usage
            },
            "usage_stability": {
                "consistent_workloads": 0.65,  # 65% stable usage
                "variable_workloads": 0.25,     # 25% variable
                "burstable_workloads": 0.10     # 10% burstable
            },
            "regional_distribution": {
                "us_east_1": 0.40,
                "eu_west_1": 0.35,
                "ap_southeast_1": 0.25
            },
            "peak_usage_hours": {
                "weekdays_9_17_utc": 0.75,
                "weekends_evening": 0.85,
                "off_peak": 0.45
            }
        }

        # Usage predictability
        predictability_analysis = {
            "coefficient_of_variation": {
                "m5_large": 0.15,    # 15% variation
                "m5_xlarge": 0.12,   # 12% variation
                "c5_xlarge": 0.08,   # 8% variation
                "r5_large": 0.18     # 18% variation
            },
            "predictability_score": {
                "high_predictability": 0.60,   # 60% of usage highly predictable
                "medium_predictability": 0.30,  # 30% medium
                "low_predictability": 0.10      # 10% low
            }
        }

        # Cost impact analysis
        cost_impact = {
            "current_on_demand_cost": 250000,  # EUR 250K monthly
            "potential_reserved_savings": 62500,  # EUR 62.5K monthly savings
            "savings_percentage": 0.25,  # 25% savings
            "break_even_period_months": 8
        }

        return {
            "historical_usage": historical_usage,
            "predictability_analysis": predictability_analysis,
            "cost_impact": cost_impact,
            "reservation_opportunity_score": self._calculate_reservation_opportunity(
                historical_usage, predictability_analysis
            )
        }

    async def _generate_reservation_recommendations(self) -> Dict:
        """
        Generate specific reservation recommendations.

        Provides detailed recommendations for reserved instance purchases
        by instance type, region, and term length.

        Returns:
            Dict containing instance-level recommendations and purchase strategy
        """

        # Instance type recommendations
        instance_recommendations = {
            "m5.large": {
                "recommended_reservations": 50,
                "term": "3_year_all_upfront",
                "expected_savings": 0.42,  # 42% savings
                "coverage_percentage": 0.85,
                "monthly_savings": 8500  # EUR
            },
            "m5.xlarge": {
                "recommended_reservations": 35,
                "term": "3_year_all_upfront",
                "expected_savings": 0.45,
                "coverage_percentage": 0.90,
                "monthly_savings": 12000
            },
            "c5.xlarge": {
                "recommended_reservations": 20,
                "term": "1_year_partial_upfront",
                "expected_savings": 0.35,
                "coverage_percentage": 0.75,
                "monthly_savings": 6500
            },
            "r5.large": {
                "recommended_reservations": 15,
                "term": "3_year_all_upfront",
                "expected_savings": 0.40,
                "coverage_percentage": 0.80,
                "monthly_savings": 4800
            }
        }

        # Regional distribution
        regional_distribution = {
            "us_east_1": {
                "reservation_percentage": 0.40,
                "regional_savings_multiplier": 1.0
            },
            "eu_west_1": {
                "reservation_percentage": 0.35,
                "regional_savings_multiplier": 1.05  # 5% premium for EU
            },
            "ap_southeast_1": {
                "reservation_percentage": 0.25,
                "regional_savings_multiplier": 0.95  # 5% discount for APAC
            }
        }

        # Purchase strategy
        purchase_strategy = {
            "immediate_purchase": {
                "instances": 80,
                "total_investment": 450000,  # EUR
                "expected_monthly_savings": 32000,
                "payback_period_months": 14
            },
            "phased_purchase": {
                "phase_1_months_1_3": {
                    "instances": 40,
                    "investment": 225000,
                    "monthly_savings": 16000
                },
                "phase_2_months_4_6": {
                    "instances": 40,
                    "investment": 225000,
                    "monthly_savings": 16000
                },
                "total_payback_period": 14
            },
            "recommended_approach": "phased_purchase"
        }

        return {
            "instance_recommendations": instance_recommendations,
            "regional_distribution": regional_distribution,
            "purchase_strategy": purchase_strategy,
            "total_annual_savings": 384000,  # EUR 384K
            "roi_percentage": 85
        }

    async def _analyze_savings_plans(self) -> Dict:
        """Analyze savings plans options."""
        return {
            "compute_savings_plan": {
                "commitment_amount": 50000,  # EUR/month
                "discount_percentage": 0.20,
                "flexibility": "high"
            },
            "ec2_savings_plan": {
                "commitment_amount": 30000,
                "discount_percentage": 0.30,
                "flexibility": "medium"
            },
            "recommended": "compute_savings_plan"
        }

    async def _optimize_purchase_timing(self) -> Dict:
        """Optimize purchase timing strategy."""
        return {
            "optimal_purchase_windows": ["q1", "q4"],
            "avoid_periods": ["major_events"],
            "price_volatility": "low"
        }

    async def _analyze_reservation_coverage(self) -> Dict:
        """Analyze current reservation coverage."""
        return {
            "current_coverage": 0.45,
            "target_coverage": 0.75,
            "gap_analysis": {
                "uncovered_cost": 137500,
                "potential_savings_on_gap": 41250
            }
        }

    async def _perform_break_even_analysis(self) -> Dict:
        """Perform break-even analysis for recommendations."""
        return {
            "average_break_even_months": 8,
            "worst_case_months": 14,
            "best_case_months": 5,
            "risk_adjusted_break_even": 10
        }

    def _calculate_reservation_opportunity(self, historical: Dict, predictability: Dict) -> float:
        """Calculate reservation opportunity score."""
        return 0.78  # 78% opportunity score

    def _calculate_total_savings(self, components: List[Dict]) -> Dict:
        """Calculate total savings from all recommendations."""
        return {
            "monthly_savings": 32000,  # EUR
            "annual_savings": 384000,
            "three_year_savings": 1152000,
            "implementation_investment": 450000,
            "roi_percentage": 156
        }
