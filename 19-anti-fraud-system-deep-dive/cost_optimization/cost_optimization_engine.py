# Companion code for "The Backend of Luck" - Chapter 19, Anti-Fraud System Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Cost Optimization Engine

This module provides comprehensive cost optimization strategies for the fraud detection system,
including resource optimization, usage-based pricing, and automated cost management.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple
import structlog

import aiohttp
import pandas as pd
from pydantic import BaseModel, Field

from ..performance.performance_monitoring import get_performance_monitor  # ty:ignore[unresolved-import]

logger = structlog.get_logger(__name__)


class CostOptimizationRule(BaseModel):
    """Cost optimization rule definition"""

    rule_id: str
    name: str
    description: str
    category: str  # compute, storage, network, database
    condition: str
    action: str
    savings_potential_percent: float
    risk_level: str  # low, medium, high
    enabled: bool = True


class CostAnalysis(BaseModel):
    """Cost analysis result"""

    analysis_id: str
    timestamp: str
    period_days: int
    total_cost: float
    cost_breakdown: Dict[str, float]
    optimization_opportunities: List[Dict[str, Any]]
    projected_savings: float
    recommendations: List[Dict[str, Any]]


class ResourceUsage(BaseModel):
    """Resource usage metrics"""

    resource_type: str
    resource_id: str
    usage_percent: float
    cost_per_hour: float
    optimization_potential: float
    last_updated: str


class CostOptimizationEngine:
    """Main cost optimization engine"""

    def __init__(self):
        self.rules: Dict[str, CostOptimizationRule] = {}
        self.resource_usage: Dict[str, ResourceUsage] = {}
        self.cost_history: List[Dict[str, Any]] = []

    async def initialize(self):
        """Initialize cost optimization engine"""
        await self.load_cost_optimization_rules()
        logger.info("Cost optimization engine initialized")

    async def load_cost_optimization_rules(self):
        """Load default cost optimization rules"""

        default_rules = [
            # Compute optimization rules
            CostOptimizationRule(
                rule_id="compute_idle_instances",
                name="Idle Instance Detection",
                description="Identify and terminate idle compute instances",
                category="compute",
                condition="cpu_usage_percent < 5 AND memory_usage_percent < 10",
                action="terminate_idle_instances",
                savings_potential_percent=100.0,
                risk_level="low"
            ),
            CostOptimizationRule(
                rule_id="compute_right_sizing",
                name="Instance Right-Sizing",
                description="Downsize over-provisioned instances",
                category="compute",
                condition="cpu_usage_percent < 30 AND memory_usage_percent < 40",
                action="downsize_instances",
                savings_potential_percent=50.0,
                risk_level="medium"
            ),
            CostOptimizationRule(
                rule_id="compute_spot_instances",
                name="Spot Instance Optimization",
                description="Use spot instances for fault-tolerant workloads",
                category="compute",
                condition="workload_type == 'batch' OR workload_type == 'development'",
                action="migrate_to_spot_instances",
                savings_potential_percent=70.0,
                risk_level="medium"
            ),

            # Storage optimization rules
            CostOptimizationRule(
                rule_id="storage_unused_volumes",
                name="Unused Storage Volumes",
                description="Delete unattached storage volumes",
                category="storage",
                condition="volume_attached == false AND age_days > 30",
                action="delete_unused_volumes",
                savings_potential_percent=100.0,
                risk_level="low"
            ),
            CostOptimizationRule(
                rule_id="storage_lifecycle",
                name="Storage Lifecycle Management",
                description="Move old data to cheaper storage tiers",
                category="storage",
                condition="data_age_days > 90 AND access_frequency == 'rare'",
                action="move_to_cold_storage",
                savings_potential_percent=60.0,
                risk_level="low"
            ),

            # Database optimization rules
            CostOptimizationRule(
                rule_id="database_connection_pooling",
                name="Database Connection Optimization",
                description="Optimize database connection usage",
                category="database",
                condition="idle_connections > 50",
                action="optimize_connection_pool",
                savings_potential_percent=30.0,
                risk_level="low"
            ),
            CostOptimizationRule(
                rule_id="database_read_replicas",
                name="Read Replica Optimization",
                description="Use read replicas for read-heavy workloads",
                category="database",
                condition="read_write_ratio > 5",
                action="implement_read_replicas",
                savings_potential_percent=40.0,
                risk_level="medium"
            ),

            # Network optimization rules
            CostOptimizationRule(
                rule_id="network_data_transfer",
                name="Data Transfer Optimization",
                description="Optimize data transfer costs",
                category="network",
                condition="data_transfer_gb > 1000",
                action="optimize_data_transfer",
                savings_potential_percent=25.0,
                risk_level="medium"
            )
        ]

        for rule in default_rules:
            self.rules[rule.rule_id] = rule

        logger.info(f"Loaded {len(default_rules)} cost optimization rules")

    async def analyze_costs(self, period_days: int = 30) -> CostAnalysis:
        """Analyze costs and identify optimization opportunities"""

        analysis_id = f"cost_analysis_{int(datetime.now(timezone.utc).timestamp())}"

        # Get cost data (placeholder - would integrate with cloud provider APIs)
        cost_data = await self._get_cost_data(period_days)

        # Analyze resource usage
        resource_analysis = await self._analyze_resource_usage()

        # Identify optimization opportunities
        opportunities = await self._identify_optimization_opportunities(resource_analysis)

        # Calculate projected savings
        projected_savings = sum(opp.get("potential_savings", 0) for opp in opportunities)

        # Generate recommendations
        recommendations = await self._generate_recommendations(opportunities)

        analysis = CostAnalysis(
            analysis_id=analysis_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            period_days=period_days,
            total_cost=cost_data.get("total_cost", 0),
            cost_breakdown=cost_data.get("breakdown", {}),
            optimization_opportunities=opportunities,
            projected_savings=projected_savings,
            recommendations=recommendations
        )

        # Store analysis
        await self._store_cost_analysis(analysis)

        return analysis

    async def _get_cost_data(self, period_days: int) -> Dict[str, Any]:
        """Get cost data from cloud provider APIs"""

        # Placeholder implementation - would integrate with AWS Cost Explorer, Azure Cost Management, etc.
        return {
            "total_cost": 15000.0,  # Example: $15,000 for the period
            "breakdown": {
                "compute": 8000.0,
                "storage": 3000.0,
                "database": 2500.0,
                "network": 1500.0
            }
        }

    async def _analyze_resource_usage(self) -> List[Dict[str, Any]]:
        """Analyze current resource usage"""

        performance_monitor = get_performance_monitor()
        stats = performance_monitor.get_performance_stats()

        analysis = []

        # CPU usage analysis
        cpu_usage = stats.get("cpu_percent", {}).get("average", 0)
        if cpu_usage < 20:
            analysis.append({
                "resource_type": "compute",
                "resource_id": "cpu_usage",
                "current_usage": cpu_usage,
                "optimization_potential": "high",
                "potential_savings_percent": 50.0,
                "recommendation": "Consider downsizing instances or using spot instances"
            })

        # Memory usage analysis
        memory_usage = stats.get("memory_percent", {}).get("average", 0)
        if memory_usage < 30:
            analysis.append({
                "resource_type": "compute",
                "resource_id": "memory_usage",
                "current_usage": memory_usage,
                "optimization_potential": "medium",
                "potential_savings_percent": 30.0,
                "recommendation": "Consider reducing memory allocation"
            })

        return analysis

    async def _identify_optimization_opportunities(self, resource_analysis: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Identify specific optimization opportunities"""

        opportunities = []

        for analysis in resource_analysis:
            # Apply optimization rules
            for rule in self.rules.values():
                if not rule.enabled:
                    continue

                # Simple condition evaluation (would be more sophisticated in production)
                if analysis["resource_type"] == rule.category:
                    opportunity = {
                        "rule_id": rule.rule_id,
                        "rule_name": rule.name,
                        "category": rule.category,
                        "description": rule.description,
                        "current_usage": analysis["current_usage"],
                        "potential_savings_percent": rule.savings_potential_percent,
                        "potential_savings": 0,  # Would calculate based on actual costs
                        "risk_level": rule.risk_level,
                        "action": rule.action,
                        "recommendation": analysis.get("recommendation", "")
                    }
                    opportunities.append(opportunity)

        return opportunities

    async def _generate_recommendations(self, opportunities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Generate prioritized recommendations"""

        # Sort by potential savings and risk
        sorted_opportunities = sorted(
            opportunities,
            key=lambda x: (x["potential_savings_percent"], -self._risk_score(x["risk_level"])),
            reverse=True
        )

        recommendations = []
        for opp in sorted_opportunities[:10]:  # Top 10 recommendations
            recommendation = {
                "priority": "high" if opp["potential_savings_percent"] > 50 else "medium",
                "title": opp["rule_name"],
                "description": opp["description"],
                "potential_savings_percent": opp["potential_savings_percent"],
                "risk_level": opp["risk_level"],
                "action_items": [opp["action"]],
                "implementation_complexity": "low" if opp["risk_level"] == "low" else "medium"
            }
            recommendations.append(recommendation)

        return recommendations

    def _risk_score(self, risk_level: str) -> int:
        """Convert risk level to numeric score"""
        risk_scores = {"low": 1, "medium": 2, "high": 3}
        return risk_scores.get(risk_level, 2)

    async def _store_cost_analysis(self, analysis: CostAnalysis):
        """Store cost analysis results"""

        # Store in memory for now (would use database in production)
        self.cost_history.append({
            "analysis_id": analysis.analysis_id,
            "timestamp": analysis.timestamp,
            "total_cost": analysis.total_cost,
            "projected_savings": analysis.projected_savings,
            "opportunities_count": len(analysis.optimization_opportunities)
        })

        # Keep only last 100 analyses
        if len(self.cost_history) > 100:
            self.cost_history = self.cost_history[-100:]

    async def implement_optimization(self, rule_id: str, resource_ids: List[str]) -> Dict[str, Any]:
        """Implement a cost optimization action"""

        if rule_id not in self.rules:
            raise ValueError(f"Cost optimization rule '{rule_id}' not found")

        rule = self.rules[rule_id]

        # Placeholder implementation - would integrate with cloud provider APIs
        implementation_result = {
            "rule_id": rule_id,
            "action": rule.action,
            "resources_affected": resource_ids,
            "status": "success",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "estimated_savings": rule.savings_potential_percent * len(resource_ids)
        }

        logger.info("Cost optimization implemented", **implementation_result)

        return implementation_result

    async def get_cost_trends(self, period_days: int = 90) -> Dict[str, Any]:
        """Get cost trends over time"""

        # Analyze cost history
        if not self.cost_history:
            return {"error": "No cost history available"}

        # Calculate trends
        recent_analyses = [
            analysis for analysis in self.cost_history
            if (datetime.now(timezone.utc) - datetime.fromisoformat(analysis["timestamp"])).days <= period_days
        ]

        if len(recent_analyses) < 2:
            return {"error": "Insufficient data for trend analysis"}

        costs = [analysis["total_cost"] for analysis in recent_analyses]
        savings = [analysis["projected_savings"] for analysis in recent_analyses]

        cost_trend = self._calculate_trend(costs)
        savings_trend = self._calculate_trend(savings)

        return {
            "period_days": period_days,
            "data_points": len(recent_analyses),
            "cost_trend_percent": cost_trend,
            "savings_trend_percent": savings_trend,
            "average_cost": sum(costs) / len(costs),
            "total_projected_savings": sum(savings),
            "cost_volatility": self._calculate_volatility(costs)
        }

    def _calculate_trend(self, values: List[float]) -> float:
        """Calculate linear trend as percentage change"""

        if len(values) < 2:
            return 0.0

        # Simple linear regression slope
        n = len(values)
        x = list(range(n))
        x_mean = sum(x) / n
        y_mean = sum(values) / n

        numerator = sum((xi - x_mean) * (yi - y_mean) for xi, yi in zip(x, values))
        denominator = sum((xi - x_mean) ** 2 for xi in x)

        if denominator == 0:
            return 0.0

        slope = numerator / denominator

        # Convert to percentage change over the period
        if values[0] != 0:
            return (slope * n / values[0]) * 100
        return 0.0

    def _calculate_volatility(self, values: List[float]) -> float:
        """Calculate coefficient of variation"""

        if len(values) < 2:
            return 0.0

        mean = sum(values) / len(values)
        if mean == 0:
            return 0.0

        variance = sum((x - mean) ** 2 for x in values) / len(values)
        std_dev = variance ** 0.5

        return (std_dev / mean) * 100

    async def generate_cost_report(self, period_days: int = 30) -> Dict[str, Any]:
        """Generate comprehensive cost report"""

        analysis = await self.analyze_costs(period_days)
        trends = await self.get_cost_trends(period_days)

        report = {
            "report_id": f"cost_report_{int(datetime.now(timezone.utc).timestamp())}",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "period_days": period_days,
            "executive_summary": {
                "total_cost": analysis.total_cost,
                "projected_savings": analysis.projected_savings,
                "savings_percentage": (analysis.projected_savings / analysis.total_cost * 100) if analysis.total_cost > 0 else 0,
                "opportunities_count": len(analysis.optimization_opportunities),
                "high_impact_opportunities": len([opp for opp in analysis.optimization_opportunities if opp["potential_savings_percent"] > 50])
            },
            "cost_breakdown": analysis.cost_breakdown,
            "top_recommendations": analysis.recommendations[:5],
            "cost_trends": trends,
            "implementation_roadmap": self._generate_implementation_roadmap(analysis.recommendations)
        }

        return report

    def _generate_implementation_roadmap(self, recommendations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Generate implementation roadmap"""

        roadmap = []

        # Group by implementation complexity and priority
        for rec in recommendations:
            phase = "Phase 1: Quick Wins" if rec["implementation_complexity"] == "low" and rec["priority"] == "high" else \
                   "Phase 2: Medium Impact" if rec["implementation_complexity"] == "medium" else \
                   "Phase 3: Long-term Optimization"

            roadmap_item = {
                "phase": phase,
                "title": rec["title"],
                "description": rec["description"],
                "potential_savings_percent": rec["potential_savings_percent"],
                "timeline": "1-2 weeks" if rec["implementation_complexity"] == "low" else "2-4 weeks",
                "risk_level": rec["risk_level"]
            }
            roadmap.append(roadmap_item)

        return roadmap


# Global cost optimization engine instance
cost_optimization_engine = CostOptimizationEngine()


async def initialize_cost_optimization():
    """Initialize the global cost optimization engine"""
    await cost_optimization_engine.initialize()


if __name__ == "__main__":
    # Example usage
    async def main():
        await initialize_cost_optimization()

        # Run cost analysis
        analysis = await cost_optimization_engine.analyze_costs(period_days=30)
        print(f"Total cost: ${analysis.total_cost}")
        print(f"Projected savings: ${analysis.projected_savings}")
        print(f"Optimization opportunities: {len(analysis.optimization_opportunities)}")

        # Generate cost report
        report = await cost_optimization_engine.generate_cost_report()
        print(f"Report generated with {len(report['top_recommendations'])} recommendations")

    asyncio.run(main())