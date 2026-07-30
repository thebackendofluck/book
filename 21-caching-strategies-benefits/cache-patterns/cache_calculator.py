# Companion code for "The Backend of Luck" - Chapter 21, Caching Strategies and Benefits.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Cache sizing and ROI calculators for iGaming platforms.

Provides:
- Memory requirement calculations
- Cost-benefit analysis
- ROI projections
- Performance improvement estimates

Helps platform architects make data-driven caching decisions.
"""

import logging
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class SizingResult:
    """Result of cache sizing calculation."""

    concurrent_users: int
    data_per_user_kb: int
    total_cache_data_gb: float
    required_memory_gb: float
    recommended_memory_gb: float
    cache_hit_ratio: float
    monthly_cost_estimate: float
    instance_recommendation: str


@dataclass
class ROIResult:
    """Result of ROI calculation."""

    total_cost: float
    total_benefits: float
    net_benefit: float
    roi_percentage: float
    payback_months: float
    break_even_year: float
    monthly_savings: float
    performance_improvement_percent: float


@dataclass
class CostConfig:
    """AWS/Cloud pricing configuration."""

    # ElastiCache Redis pricing (per GB/hour, us-east-1)
    redis_per_gb_hour: float = 0.068  # cache.r6g.large
    redis_reserved_discount: float = 0.35  # 1-year reserved

    # Network costs
    data_transfer_per_gb: float = 0.01

    # Database costs avoided
    rds_per_query: float = 0.0000001  # Approximate per-query cost
    rds_cpu_hour: float = 0.096  # db.r6g.large

    # Human costs
    developer_hour: float = 150.0
    ops_hour: float = 100.0


class CacheSizingCalculator:
    """
    Calculate cache memory requirements for casino platforms.

    Considers:
    - Concurrent user estimates
    - Data per user (balance, session, preferences)
    - Redis overhead (~25%)
    - Cache hit ratio targets
    - Growth projections
    """

    # Redis memory overhead factors
    REDIS_OVERHEAD = 1.25  # 25% overhead for data structures
    HEADROOM_FACTOR = 1.5  # 50% headroom for growth

    # AWS ElastiCache instance types
    INSTANCE_TYPES = {
        "cache.t3.micro": {"memory_gb": 0.5, "cost_hour": 0.017},
        "cache.t3.small": {"memory_gb": 1.5, "cost_hour": 0.034},
        "cache.t3.medium": {"memory_gb": 3.0, "cost_hour": 0.068},
        "cache.r6g.large": {"memory_gb": 13.0, "cost_hour": 0.136},
        "cache.r6g.xlarge": {"memory_gb": 26.0, "cost_hour": 0.272},
        "cache.r6g.2xlarge": {"memory_gb": 52.0, "cost_hour": 0.544},
        "cache.r6g.4xlarge": {"memory_gb": 104.0, "cost_hour": 1.088},
        "cache.r7g.xlarge": {"memory_gb": 26.0, "cost_hour": 0.286},
        "cache.r7g.2xlarge": {"memory_gb": 52.0, "cost_hour": 0.571},
    }

    def __init__(self, cost_config: Optional[CostConfig] = None):
        self.cost_config = cost_config or CostConfig()

    def calculate_requirements(
        self,
        daily_active_users: int,
        average_session_time_minutes: int = 30,
        data_per_user_kb: int = 50,
        cache_hit_ratio: float = 0.85,
        peak_concurrency_percent: float = 0.20,
    ) -> SizingResult:
        """
        Calculate cache memory requirements for casino platform.

        Args:
            daily_active_users: Expected daily active users
            average_session_time_minutes: Average session length
            data_per_user_kb: Average cached data per user in KB
            cache_hit_ratio: Target cache hit ratio (0.0-1.0)
            peak_concurrency_percent: Percent of DAU online at peak

        Returns:
            SizingResult with memory requirements and recommendations
        """
        concurrent_users = int(daily_active_users * peak_concurrency_percent)
        data_per_user_bytes = data_per_user_kb * 1024
        total_cache_data = concurrent_users * data_per_user_bytes
        required_memory_bytes = total_cache_data * self.REDIS_OVERHEAD
        miss_factor = 1 / cache_hit_ratio
        total_required_memory = required_memory_bytes * miss_factor
        required_memory_gb = total_required_memory / (1024**3)
        recommended_memory_gb = required_memory_gb * self.HEADROOM_FACTOR
        instance_type = self._recommend_instance(recommended_memory_gb)
        monthly_cost = self._calculate_monthly_cost(instance_type)

        return SizingResult(
            concurrent_users=concurrent_users,
            data_per_user_kb=data_per_user_kb,
            total_cache_data_gb=total_cache_data / (1024**3),
            required_memory_gb=round(required_memory_gb, 2),
            recommended_memory_gb=round(recommended_memory_gb, 2),
            cache_hit_ratio=cache_hit_ratio,
            monthly_cost_estimate=monthly_cost,
            instance_recommendation=instance_type,
        )

    def _recommend_instance(self, required_gb: float) -> str:
        """Recommend smallest instance that fits requirements."""
        for instance_type, specs in sorted(
            self.INSTANCE_TYPES.items(), key=lambda x: x[1]["memory_gb"]
        ):
            if specs["memory_gb"] >= required_gb:
                return instance_type
        return "cache.r7g.2xlarge"  # Largest available

    def _calculate_monthly_cost(self, instance_type: str) -> float:
        """Calculate monthly cost for instance type."""
        specs = self.INSTANCE_TYPES.get(instance_type, {"cost_hour": 0.5})
        hourly_cost = specs["cost_hour"]
        monthly_hours = 730  # Average hours per month
        return round(hourly_cost * monthly_hours, 2)

    def calculate_cluster_requirements(
        self,
        daily_active_users: int,
        data_per_user_kb: int = 50,
        replication_factor: int = 2,
        num_shards: int = 3,
    ) -> dict[str, Any]:
        """
        Calculate requirements for Redis Cluster deployment.

        For high-availability casino platforms requiring:
        - Multi-shard for horizontal scaling
        - Replication for fault tolerance
        """
        base_result = self.calculate_requirements(
            daily_active_users=daily_active_users,
            data_per_user_kb=data_per_user_kb,
        )

        per_shard_memory = base_result.recommended_memory_gb / num_shards
        nodes_per_shard = 1 + replication_factor
        total_nodes = num_shards * nodes_per_shard
        shard_instance = self._recommend_instance(per_shard_memory)
        per_node_cost = self._calculate_monthly_cost(shard_instance)
        total_monthly_cost = per_node_cost * total_nodes

        return {
            "base_requirements": base_result,
            "cluster_config": {
                "num_shards": num_shards,
                "replication_factor": replication_factor,
                "nodes_per_shard": nodes_per_shard,
                "total_nodes": total_nodes,
                "per_shard_memory_gb": round(per_shard_memory, 2),
            },
            "instance_recommendation": shard_instance,
            "monthly_cost": total_monthly_cost,
            "annual_cost": total_monthly_cost * 12,
            "reserved_annual_cost": total_monthly_cost * 12 * 0.65,
        }


class CacheROICalculator:
    """
    Calculate return on investment for cache implementation.

    Models:
    - Implementation costs (development, infrastructure)
    - Operational savings (reduced DB load, improved latency)
    - Revenue impact (improved user experience)
    """

    def __init__(self, cost_config: Optional[CostConfig] = None):
        self.cost_config = cost_config or CostConfig()

    def calculate_roi(
        self,
        implementation_cost: float,
        monthly_infrastructure_cost: float,
        monthly_savings: float,
        monthly_revenue_increase: float,
        months: int = 36,
    ) -> ROIResult:
        """
        Calculate return on investment for cache implementation.

        Args:
            implementation_cost: One-time development/setup cost
            monthly_infrastructure_cost: Monthly cache infrastructure cost
            monthly_savings: Monthly operational savings
            monthly_revenue_increase: Monthly revenue increase from better UX
            months: Analysis period in months

        Returns:
            ROIResult with financial projections
        """
        total_cost = implementation_cost + (monthly_infrastructure_cost * months)
        total_benefits = (monthly_savings + monthly_revenue_increase) * months
        net_benefit = total_benefits - total_cost
        roi_percentage = ((net_benefit) / total_cost) * 100 if total_cost > 0 else 0
        monthly_net = monthly_savings + monthly_revenue_increase - monthly_infrastructure_cost
        payback_months = (
            implementation_cost / monthly_net if monthly_net > 0 else float("inf")
        )

        return ROIResult(
            total_cost=round(total_cost, 2),
            total_benefits=round(total_benefits, 2),
            net_benefit=round(net_benefit, 2),
            roi_percentage=round(roi_percentage, 1),
            payback_months=round(payback_months, 1),
            break_even_year=round(payback_months / 12, 2),
            monthly_savings=monthly_savings,
            performance_improvement_percent=0.0,
        )

    def estimate_savings(
        self,
        current_db_queries_per_second: int,
        cache_hit_rate: float = 0.85,
        avg_query_latency_ms: float = 10.0,
        cache_latency_ms: float = 0.5,
    ) -> dict[str, Any]:
        """
        Estimate savings from cache implementation.

        Calculates:
        - Database load reduction
        - Latency improvement
        - Cost savings
        """
        cached_queries = current_db_queries_per_second * cache_hit_rate
        remaining_db_queries = current_db_queries_per_second * (1 - cache_hit_rate)
        seconds_per_month = 30 * 24 * 3600
        monthly_db_queries_saved = cached_queries * seconds_per_month
        db_cost_saved = monthly_db_queries_saved * self.cost_config.rds_per_query
        weighted_latency = (cache_hit_rate * cache_latency_ms) + (
            (1 - cache_hit_rate) * avg_query_latency_ms
        )
        latency_improvement = (
            (avg_query_latency_ms - weighted_latency) / avg_query_latency_ms * 100
        )
        cpu_hours_saved = (cached_queries / 1000) * 730
        cpu_cost_saved = cpu_hours_saved * self.cost_config.rds_cpu_hour * 0.1

        return {
            "database_load_reduction_percent": round(cache_hit_rate * 100, 1),
            "queries_offloaded_per_second": round(cached_queries, 0),
            "monthly_queries_saved": int(monthly_db_queries_saved),
            "latency_improvement_percent": round(latency_improvement, 1),
            "new_avg_latency_ms": round(weighted_latency, 2),
            "monthly_db_cost_saved": round(db_cost_saved, 2),
            "monthly_cpu_cost_saved": round(cpu_cost_saved, 2),
            "total_monthly_savings": round(db_cost_saved + cpu_cost_saved, 2),
        }

    def generate_business_case(
        self,
        daily_active_users: int,
        current_response_time_ms: float = 200.0,
        target_response_time_ms: float = 50.0,
        monthly_revenue: float = 1000000.0,
    ) -> dict[str, Any]:
        """
        Generate comprehensive business case for cache investment.

        Includes:
        - Performance impact analysis
        - User experience improvement estimates
        - Revenue impact projections
        """
        latency_improvement = (
            (current_response_time_ms - target_response_time_ms)
            / current_response_time_ms
            * 100
        )
        conversion_rate_improvement = min(latency_improvement * 0.1, 15.0)
        retention_rate_improvement = min(latency_improvement * 0.2, 25.0)
        revenue_uplift = monthly_revenue * (conversion_rate_improvement / 100)
        sizing = CacheSizingCalculator().calculate_requirements(daily_active_users)
        implementation_cost = 50000.0
        monthly_cache_cost = sizing.monthly_cost_estimate
        savings_estimate = self.estimate_savings(
            current_db_queries_per_second=daily_active_users // 100
        )
        roi = self.calculate_roi(
            implementation_cost=implementation_cost,
            monthly_infrastructure_cost=monthly_cache_cost,
            monthly_savings=savings_estimate["total_monthly_savings"],
            monthly_revenue_increase=revenue_uplift,
        )

        return {
            "executive_summary": {
                "investment_required": implementation_cost + (monthly_cache_cost * 12),
                "annual_benefit": (
                    savings_estimate["total_monthly_savings"] + revenue_uplift
                )
                * 12,
                "roi_percentage": roi.roi_percentage,
                "payback_period_months": roi.payback_months,
            },
            "performance_impact": {
                "current_response_time_ms": current_response_time_ms,
                "target_response_time_ms": target_response_time_ms,
                "improvement_percent": round(latency_improvement, 1),
            },
            "user_experience_impact": {
                "conversion_rate_improvement_percent": round(
                    conversion_rate_improvement, 1
                ),
                "retention_rate_improvement_percent": round(
                    retention_rate_improvement, 1
                ),
            },
            "financial_impact": {
                "monthly_revenue_uplift": round(revenue_uplift, 2),
                "monthly_cost_savings": savings_estimate["total_monthly_savings"],
                "monthly_infrastructure_cost": monthly_cache_cost,
                "net_monthly_benefit": round(
                    revenue_uplift
                    + savings_estimate["total_monthly_savings"]
                    - monthly_cache_cost,
                    2,
                ),
            },
            "infrastructure_recommendation": sizing,
            "roi_analysis": roi,
        }
