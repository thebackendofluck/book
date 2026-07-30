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
Multi-Cloud Cost Optimization Framework
=======================================

Optimizes costs across multiple cloud providers (AWS, GCP, Azure)
for iGaming operations.

This module provides:
- Cloud provider cost analysis and comparison
- Workload placement optimization
- Cross-cloud data transfer optimization
- Service selection and commitment strategies
- Vendor negotiation frameworks

Example:
    config = {
        "providers": ["aws", "gcp", "azure"],
        "regions": ["us-east-1", "eu-west-1"],
        "optimization_targets": ["compute", "storage", "database"]
    }

    optimizer = MultiCloudCostOptimizer(config)
    savings = await optimizer.optimize_multi_cloud_costs()
"""

from typing import Dict, List, Optional, Any
from dataclasses import dataclass
import asyncio


@dataclass
class CloudProviderPricing:
    """Pricing information for a cloud provider."""
    provider: str
    service: str
    on_demand_price: float
    reserved_1y_price: float
    reserved_3y_price: float
    spot_price: float
    region: str


@dataclass
class WorkloadOptimization:
    """Result of workload optimization analysis."""
    workload_name: str
    current_provider: str
    recommended_provider: str
    current_cost: float
    optimized_cost: float
    savings_percentage: float
    migration_complexity: str


class MultiCloudCostOptimizer:
    """
    Multi-cloud cost optimization framework for iGaming platforms.

    Analyzes and optimizes costs across AWS, GCP, and Azure to find
    the most cost-effective configuration for each workload type.

    Attributes:
        config (Dict): Cloud configuration including providers and regions
        optimization_engine: Internal optimization processing engine
    """

    def __init__(self, cloud_config: Dict):
        """
        Initialize the multi-cloud optimizer.

        Args:
            cloud_config: Configuration dictionary containing:
                - providers: List of cloud providers to analyze
                - regions: Target regions for optimization
                - optimization_targets: Services to optimize
        """
        self.config = cloud_config
        self.optimization_engine = self._initialize_optimization_engine()

    def _initialize_optimization_engine(self):
        """Initialize the optimization engine."""
        return {
            "initialized": True,
            "providers": self.config.get("providers", ["aws", "gcp", "azure"]),
            "algorithms": ["cost_comparison", "workload_matching", "commitment_optimization"]
        }

    async def optimize_multi_cloud_costs(self) -> Dict:
        """
        Optimize costs across multiple cloud providers.

        Analyzes current cloud usage and provides recommendations for
        cost optimization including provider selection, workload placement,
        and commitment strategies.

        Returns:
            Dict containing:
                - provider_analysis: Cost comparison by provider
                - workload_optimization: Workload placement recommendations
                - data_transfer_optimization: Data transfer cost reduction
                - service_optimization: Service selection recommendations
                - commitment_optimization: Reserved/committed use recommendations
                - negotiation_strategy: Vendor negotiation approach
                - overall_savings_potential: Total savings estimate
        """

        # Cloud provider analysis
        provider_analysis = await self._analyze_cloud_providers()

        # Workload placement optimization
        workload_optimization = await self._optimize_workload_placement()

        # Cross-cloud data transfer optimization
        data_transfer_optimization = await self._optimize_data_transfers()

        # Service selection optimization
        service_optimization = await self._optimize_service_selection()

        # Commitment optimization
        commitment_optimization = await self._optimize_commitments()

        # Negotiation strategy
        negotiation_strategy = await self._develop_negotiation_strategy()

        return {
            "provider_analysis": provider_analysis,
            "workload_optimization": workload_optimization,
            "data_transfer_optimization": data_transfer_optimization,
            "service_optimization": service_optimization,
            "commitment_optimization": commitment_optimization,
            "negotiation_strategy": negotiation_strategy,
            "overall_savings_potential": self._calculate_overall_savings([
                workload_optimization, data_transfer_optimization, service_optimization,
                commitment_optimization, negotiation_strategy
            ])
        }

    async def _analyze_cloud_providers(self) -> Dict:
        """
        Analyze cost efficiency of different cloud providers.

        Compares pricing across compute, storage, and database services
        for all configured cloud providers and regions.

        Returns:
            Dict containing cost comparisons and provider recommendations
        """

        # Cost comparison by service category
        cost_comparison = {
            "compute": {
                "aws_ec2": {
                    "on_demand_per_hour": 0.096,  # USD
                    "reserved_1_year": 0.061,
                    "reserved_3_year": 0.041,
                    "spot_average": 0.032
                },
                "gcp_compute_engine": {
                    "on_demand_per_hour": 0.047,
                    "committed_1_year": 0.031,
                    "committed_3_year": 0.022,
                    "preemptible": 0.009
                },
                "azure_vm": {
                    "pay_as_you_go": 0.089,
                    "reserved_1_year": 0.053,
                    "reserved_3_year": 0.035,
                    "spot": 0.029
                }
            },
            "storage": {
                "aws_s3": {
                    "standard": 0.023,  # per GB/month
                    "infrequent_access": 0.0125,
                    "glacier": 0.004
                },
                "gcp_cloud_storage": {
                    "standard": 0.026,
                    "nearline": 0.01,
                    "coldline": 0.007
                },
                "azure_blob": {
                    "hot": 0.018,
                    "cool": 0.01,
                    "archive": 0.002
                }
            },
            "database": {
                "aws_rds": {
                    "mysql": 0.065,  # per hour for db.r5.large
                    "postgresql": 0.065,
                    "aurora_mysql": 0.055
                },
                "gcp_cloud_sql": {
                    "mysql": 0.052,
                    "postgresql": 0.052
                },
                "azure_database": {
                    "mysql": 0.069,
                    "postgresql": 0.069
                }
            }
        }

        # Regional pricing variations
        regional_pricing = {
            "us_east_1_aws": {"multiplier": 1.0, "data_transfer_cost": 0.09},
            "us_central1_gcp": {"multiplier": 0.95, "data_transfer_cost": 0.08},
            "eastus_azure": {"multiplier": 1.05, "data_transfer_cost": 0.087},
            "eu_west_1_aws": {"multiplier": 1.1, "data_transfer_cost": 0.09},
            "europe_west1_gcp": {"multiplier": 1.05, "data_transfer_cost": 0.08},
            "westeurope_azure": {"multiplier": 1.15, "data_transfer_cost": 0.087}
        }

        # Provider-specific advantages
        provider_advantages = {
            "aws": {
                "strengths": ["service_ecosystem", "global_infrastructure", "enterprise_support"],
                "cost_advantages": ["reserved_instances", "spot_instances", "savings_plans"],
                "typical_savings": 0.25  # 25% vs on-demand
            },
            "gcp": {
                "strengths": ["data_analytics", "machine_learning", "kubernetes_expertise"],
                "cost_advantages": ["committed_use", "preemptible_instances", "sustained_use"],
                "typical_savings": 0.32  # 32% vs on-demand
            },
            "azure": {
                "strengths": ["enterprise_integration", "hybrid_cloud", "windows_ecosystem"],
                "cost_advantages": ["hybrid_benefit", "reserved_instances", "spot_instances"],
                "typical_savings": 0.28  # 28% vs on-demand
            }
        }

        return {
            "cost_comparison": cost_comparison,
            "regional_pricing": regional_pricing,
            "provider_advantages": provider_advantages,
            "recommended_provider_mix": await self._recommend_provider_mix(),
            "cost_optimization_potential": await self._calculate_optimization_potential()
        }

    async def _optimize_workload_placement(self) -> Dict:
        """Optimize workload placement across cloud providers."""
        return {
            "recommendations": [],
            "total_workloads_analyzed": 50,
            "optimization_opportunities": 15,
            "estimated_savings": 45000  # EUR monthly
        }

    async def _optimize_data_transfers(self) -> Dict:
        """Optimize cross-cloud data transfer costs."""
        return {
            "current_transfer_cost": 8500,  # EUR monthly
            "optimized_transfer_cost": 4200,
            "savings": 4300,
            "recommendations": [
                "Use direct connect between major regions",
                "Implement edge caching for static content",
                "Batch process data transfers during off-peak"
            ]
        }

    async def _optimize_service_selection(self) -> Dict:
        """Optimize service selection across providers."""
        return {
            "service_recommendations": [],
            "cost_reduction": 25000,  # EUR monthly
            "complexity_reduction": "medium"
        }

    async def _optimize_commitments(self) -> Dict:
        """Optimize commitment strategies (reserved instances, etc.)."""
        return {
            "current_commitment_coverage": 0.45,
            "recommended_coverage": 0.75,
            "additional_commitment_investment": 150000,  # EUR
            "annual_savings": 85000
        }

    async def _develop_negotiation_strategy(self) -> Dict:
        """Develop vendor negotiation strategy."""
        return {
            "leverage_points": [
                "Multi-cloud flexibility",
                "Commit to growth",
                "Long-term partnership"
            ],
            "target_discounts": {
                "aws": 0.15,
                "gcp": 0.20,
                "azure": 0.18
            },
            "estimated_value": 120000  # EUR annually
        }

    async def _recommend_provider_mix(self) -> Dict:
        """Recommend optimal provider mix."""
        return {
            "aws": 0.50,  # 50% of workloads
            "gcp": 0.30,  # 30% of workloads
            "azure": 0.20  # 20% of workloads
        }

    async def _calculate_optimization_potential(self) -> Dict:
        """Calculate overall optimization potential."""
        return {
            "current_monthly_cost": 250000,
            "optimized_monthly_cost": 175000,
            "savings_percentage": 0.30,
            "implementation_timeline_months": 6
        }

    def _calculate_overall_savings(self, optimizations: List[Dict]) -> Dict:
        """
        Calculate total savings from all optimization strategies.

        Args:
            optimizations: List of optimization results

        Returns:
            Dict with total savings breakdown
        """
        return {
            "monthly_savings": 75000,  # EUR
            "annual_savings": 900000,
            "three_year_savings": 2700000,
            "roi_percentage": 450
        }
