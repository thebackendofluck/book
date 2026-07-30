#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 31, Performance Benchmarks and Metrics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
API Performance Monitor Module
==============================

Comprehensive API performance monitoring and benchmarking for iGaming platforms.
Tracks response times, error rates, throughput, and SLA compliance.
"""

from typing import Any, Dict, List, Optional


class APIPerformanceMonitor:
    """Monitor and benchmark API performance for iGaming platforms."""

    def __init__(self, monitoring_config: Optional[Dict[str, Any]] = None):
        self.config = monitoring_config or {}
        self.performance_baselines = self._load_performance_baselines()
        self.monitoring_engine = self._initialize_monitoring_engine()

    def _initialize_monitoring_engine(self) -> Dict[str, Any]:
        """Initialize the monitoring engine."""
        return {
            "enabled": True,
            "sampling_rate": self.config.get("sampling_rate", 1.0),
            "retention_days": self.config.get("retention_days", 30)
        }

    async def monitor_api_performance(self) -> Dict[str, Any]:
        """
        Monitor comprehensive API performance metrics.

        Returns:
            Dict containing performance metrics, trends, and analysis.
        """
        # Real-time performance metrics
        realtime_metrics = await self._collect_realtime_metrics()

        # Performance trend analysis
        trend_analysis = await self._analyze_performance_trends()

        # SLA compliance monitoring
        sla_compliance = await self._monitor_sla_compliance()

        # Error rate analysis
        error_analysis = await self._analyze_error_rates()

        # Geographic performance
        geographic_performance = await self._analyze_geographic_performance()

        # Business impact correlation
        business_impact = await self._correlate_business_impact()

        return {
            "realtime_metrics": realtime_metrics,
            "trend_analysis": trend_analysis,
            "sla_compliance": sla_compliance,
            "error_analysis": error_analysis,
            "geographic_performance": geographic_performance,
            "business_impact": business_impact,
            "performance_score": self._calculate_overall_performance_score([
                realtime_metrics, trend_analysis, sla_compliance,
                error_analysis, geographic_performance
            ])
        }

    def _load_performance_baselines(self) -> Dict[str, Any]:
        """Load performance baselines for different API endpoints."""
        return {
            "bet_placement": {
                "p50_target": 50,    # milliseconds
                "p95_target": 150,
                "p99_target": 300,
                "error_rate_target": 0.01,  # 0.01%
                "throughput_target": 1000,  # requests per second
                "business_impact": "critical"
            },
            "game_launch": {
                "p50_target": 100,
                "p95_target": 300,
                "p99_target": 600,
                "error_rate_target": 0.05,
                "throughput_target": 500,
                "business_impact": "critical"
            },
            "balance_check": {
                "p50_target": 30,
                "p95_target": 100,
                "p99_target": 200,
                "error_rate_target": 0.005,
                "throughput_target": 2000,
                "business_impact": "critical"
            },
            "user_authentication": {
                "p50_target": 200,
                "p95_target": 500,
                "p99_target": 1000,
                "error_rate_target": 0.02,
                "throughput_target": 300,
                "business_impact": "high"
            },
            "payment_processing": {
                "p50_target": 150,
                "p95_target": 400,
                "p99_target": 800,
                "error_rate_target": 0.01,
                "throughput_target": 200,
                "business_impact": "high"
            }
        }

    async def _collect_realtime_metrics(self) -> Dict[str, Any]:
        """Collect real-time API performance metrics."""
        # Response time percentiles
        response_times = {
            "bet_placement": {
                "p50": 45,
                "p95": 142,
                "p99": 289,
                "avg": 67
            },
            "game_launch": {
                "p50": 89,
                "p95": 267,
                "p99": 543,
                "avg": 124
            },
            "balance_check": {
                "p50": 28,
                "p95": 89,
                "p99": 178,
                "avg": 42
            }
        }

        # Error rates
        error_rates = {
            "bet_placement": 0.008,  # 0.008%
            "game_launch": 0.032,    # 0.032%
            "balance_check": 0.003   # 0.003%
        }

        # Throughput metrics
        throughput = {
            "bet_placement": 1250,   # requests per second
            "game_launch": 678,
            "balance_check": 2100
        }

        # Geographic breakdown
        geographic_breakdown = {
            "europe": {
                "avg_response_time": 89,
                "error_rate": 0.012
            },
            "asia": {
                "avg_response_time": 145,
                "error_rate": 0.028
            },
            "north_america": {
                "avg_response_time": 112,
                "error_rate": 0.015
            }
        }

        return {
            "response_times": response_times,
            "error_rates": error_rates,
            "throughput": throughput,
            "geographic_breakdown": geographic_breakdown,
            "overall_health_score": self._calculate_health_score(
                response_times, error_rates, throughput
            )
        }

    async def _analyze_performance_trends(self) -> Dict[str, Any]:
        """Analyze performance trends over time."""
        return {
            "response_time_trend": "improving",
            "error_rate_trend": "stable",
            "throughput_trend": "increasing",
            "week_over_week_change": {
                "response_time": -5.2,  # 5.2% improvement
                "error_rate": -1.3,
                "throughput": 8.7
            },
            "anomalies_detected": []
        }

    async def _monitor_sla_compliance(self) -> Dict[str, Any]:
        """Monitor SLA compliance metrics."""
        return {
            "overall_compliance": 99.87,
            "by_endpoint": {
                "bet_placement": {"compliance": 99.92, "breaches": 3},
                "game_launch": {"compliance": 99.78, "breaches": 8},
                "balance_check": {"compliance": 99.95, "breaches": 1}
            },
            "monthly_uptime": 99.97,
            "sla_credits_exposure": 0
        }

    async def _analyze_error_rates(self) -> Dict[str, Any]:
        """Analyze error rates and patterns."""
        return {
            "total_error_rate": 0.015,
            "by_type": {
                "5xx_errors": 0.003,
                "4xx_errors": 0.008,
                "timeout_errors": 0.002,
                "network_errors": 0.002
            },
            "top_errors": [
                {"code": 503, "count": 234, "description": "Service Unavailable"},
                {"code": 429, "count": 189, "description": "Rate Limited"},
                {"code": 408, "count": 145, "description": "Request Timeout"}
            ]
        }

    async def _analyze_geographic_performance(self) -> Dict[str, Any]:
        """Analyze performance by geographic region."""
        return {
            "regions": {
                "europe": {"latency": 45, "error_rate": 0.01, "users_percent": 55},
                "asia": {"latency": 120, "error_rate": 0.02, "users_percent": 25},
                "north_america": {"latency": 80, "error_rate": 0.015, "users_percent": 15},
                "latam": {"latency": 150, "error_rate": 0.025, "users_percent": 5}
            },
            "recommendations": [
                "Consider CDN edge location in Singapore",
                "Optimize database read replicas in US-East"
            ]
        }

    async def _correlate_business_impact(self) -> Dict[str, Any]:
        """Correlate performance metrics with business outcomes."""
        return {
            "conversion_correlation": 0.78,
            "revenue_impact_per_ms": 125.50,  # EUR per millisecond
            "user_satisfaction_score": 4.2,
            "churn_risk_reduction": 0.034
        }

    def _calculate_health_score(
        self,
        response_times: Dict[str, Any],
        error_rates: Dict[str, float],
        throughput: Dict[str, int]
    ) -> float:
        """Calculate overall health score from metrics."""
        # Simplified scoring logic
        response_score = sum(
            1.0 if rt["p95"] < self.performance_baselines.get(ep, {}).get("p95_target", 500) else 0.5
            for ep, rt in response_times.items()
        ) / max(len(response_times), 1)

        error_score = sum(
            1.0 if er < 0.01 else 0.5 if er < 0.05 else 0.2
            for er in error_rates.values()
        ) / max(len(error_rates), 1)

        return (response_score + error_score) / 2

    def _calculate_overall_performance_score(
        self,
        metrics_list: List[Dict[str, Any]]
    ) -> float:
        """Calculate overall performance score from all metrics."""
        # Weighted average based on component importance
        weights = [0.25, 0.15, 0.20, 0.15, 0.15, 0.10]
        base_score = 0.85  # Default base score

        # Adjust based on metrics health
        for i, metrics in enumerate(metrics_list):
            if isinstance(metrics, dict):
                if metrics.get("overall_health_score"):
                    base_score += weights[i] * (metrics["overall_health_score"] - 0.5)

        return min(max(base_score, 0.0), 1.0)
