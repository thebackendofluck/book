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
Monitoring and Alerting Framework Module
=========================================

Comprehensive monitoring and alerting framework for iGaming platforms.
Implements infrastructure, application, and business metrics monitoring.
"""

from typing import Any, Dict, List, Optional


class MonitoringAlertingFramework:
    """Implement comprehensive monitoring and alerting for iGaming platforms."""

    def __init__(self, monitoring_config: Optional[Dict[str, Any]] = None):
        self.config = monitoring_config or {}
        self.alert_engine = self._initialize_alert_engine()
        self.monitoring_dashboards = self._initialize_dashboards()

    def _initialize_alert_engine(self) -> Dict[str, Any]:
        """Initialize the alert engine configuration."""
        return {
            "enabled": True,
            "default_channels": ["slack", "email"],
            "escalation_enabled": True,
            "deduplication_window": 300  # seconds
        }

    def _initialize_dashboards(self) -> Dict[str, Any]:
        """Initialize monitoring dashboards configuration."""
        return {
            "executive": {"refresh_rate": 300, "metrics": ["revenue", "users", "uptime"]},
            "operations": {"refresh_rate": 60, "metrics": ["errors", "latency", "throughput"]},
            "engineering": {"refresh_rate": 10, "metrics": ["cpu", "memory", "queries"]}
        }

    async def implement_monitoring_framework(self) -> Dict[str, Any]:
        """
        Implement comprehensive monitoring and alerting framework.

        Returns:
            Dict containing monitoring setup, alerts, and dashboards.
        """
        # Infrastructure monitoring
        infrastructure_monitoring = await self._setup_infrastructure_monitoring()

        # Application monitoring
        application_monitoring = await self._setup_application_monitoring()

        # Business metrics monitoring
        business_monitoring = await self._setup_business_monitoring()

        # Alert configuration
        alert_configuration = await self._configure_alerts()

        # Dashboard creation
        dashboard_setup = await self._create_monitoring_dashboards()

        # Alert escalation procedures
        escalation_procedures = await self._setup_escalation_procedures()

        return {
            "infrastructure_monitoring": infrastructure_monitoring,
            "application_monitoring": application_monitoring,
            "business_monitoring": business_monitoring,
            "alert_configuration": alert_configuration,
            "dashboard_setup": dashboard_setup,
            "escalation_procedures": escalation_procedures,
            "monitoring_maturity_score": self._calculate_monitoring_maturity([
                infrastructure_monitoring, application_monitoring, business_monitoring,
                alert_configuration, dashboard_setup, escalation_procedures
            ])
        }

    async def _setup_infrastructure_monitoring(self) -> Dict[str, Any]:
        """Setup infrastructure monitoring."""
        return {
            "compute_monitoring": {
                "metrics": ["cpu", "memory", "disk", "network"],
                "agents": ["prometheus-node-exporter", "datadog-agent"],
                "coverage": 0.98
            },
            "container_monitoring": {
                "metrics": ["pod_cpu", "pod_memory", "container_restarts"],
                "tools": ["prometheus", "cadvisor"],
                "coverage": 0.99
            },
            "network_monitoring": {
                "metrics": ["latency", "packet_loss", "bandwidth"],
                "tools": ["smokeping", "blackbox-exporter"],
                "coverage": 0.95
            },
            "storage_monitoring": {
                "metrics": ["iops", "throughput", "latency", "utilization"],
                "tools": ["prometheus", "cloudwatch"],
                "coverage": 0.97
            }
        }

    async def _setup_application_monitoring(self) -> Dict[str, Any]:
        """Setup application performance monitoring."""
        return {
            "apm_configuration": {
                "tool": "datadog-apm",
                "sampling_rate": 1.0,
                "trace_retention_days": 15,
                "error_tracking": True
            },
            "distributed_tracing": {
                "enabled": True,
                "propagation": "w3c",
                "service_map": True
            },
            "log_aggregation": {
                "tool": "elasticsearch",
                "retention_days": 30,
                "structured_logging": True
            },
            "metrics_collection": {
                "custom_metrics": True,
                "business_metrics": True,
                "latency_histograms": True
            }
        }

    async def _setup_business_monitoring(self) -> Dict[str, Any]:
        """Setup business metrics monitoring."""
        return {
            "revenue_metrics": {
                "real_time": True,
                "granularity": "1m",
                "alerts_enabled": True
            },
            "user_metrics": {
                "active_users": True,
                "session_tracking": True,
                "funnel_analysis": True
            },
            "gaming_metrics": {
                "bets_per_second": True,
                "game_launches": True,
                "payout_tracking": True
            },
            "compliance_metrics": {
                "responsible_gaming": True,
                "regulatory_reporting": True,
                "audit_trail": True
            }
        }

    async def _configure_alerts(self) -> Dict[str, Any]:
        """Configure comprehensive alerting system."""
        # Critical alerts (immediate response required)
        critical_alerts = {
            "service_down": {
                "condition": "uptime < 99.9%",
                "duration": "5_minutes",
                "channels": ["pagerduty", "slack", "sms"],
                "escalation_time": "5_minutes",
                "response_team": "platform_team"
            },
            "payment_system_failure": {
                "condition": "payment_success_rate < 99.5%",
                "duration": "2_minutes",
                "channels": ["pagerduty", "slack", "sms"],
                "escalation_time": "2_minutes",
                "response_team": "payment_team"
            },
            "data_breach_indicators": {
                "condition": "anomalous_data_access > threshold",
                "duration": "1_minute",
                "channels": ["pagerduty", "security_team", "legal"],
                "escalation_time": "immediate",
                "response_team": "security_team"
            }
        }

        # High priority alerts
        high_priority_alerts = {
            "api_performance_degradation": {
                "condition": "p95_response_time > 500ms",
                "duration": "10_minutes",
                "channels": ["slack", "email"],
                "escalation_time": "30_minutes",
                "response_team": "platform_team"
            },
            "database_performance_issues": {
                "condition": "query_latency > 1000ms",
                "duration": "5_minutes",
                "channels": ["slack", "email"],
                "escalation_time": "15_minutes",
                "response_team": "database_team"
            },
            "user_experience_impact": {
                "condition": "error_rate > 0.5%",
                "duration": "5_minutes",
                "channels": ["slack", "email"],
                "escalation_time": "15_minutes",
                "response_team": "frontend_team"
            }
        }

        # Medium priority alerts
        medium_priority_alerts = {
            "resource_utilization_high": {
                "condition": "cpu_utilization > 85%",
                "duration": "15_minutes",
                "channels": ["slack"],
                "escalation_time": "2_hours",
                "response_team": "infrastructure_team"
            },
            "slow_query_alerts": {
                "condition": "slow_queries_per_minute > 10",
                "duration": "10_minutes",
                "channels": ["slack"],
                "escalation_time": "1_hour",
                "response_team": "database_team"
            }
        }

        return {
            "critical_alerts": critical_alerts,
            "high_priority_alerts": high_priority_alerts,
            "medium_priority_alerts": medium_priority_alerts,
            "alert_coverage_score": self._calculate_alert_coverage(
                critical_alerts, high_priority_alerts, medium_priority_alerts
            ),
            "false_positive_rate": await self._analyze_false_positive_rate(),
            "mean_time_to_detection": await self._calculate_mttr_metrics()
        }

    async def _create_monitoring_dashboards(self) -> Dict[str, Any]:
        """Create monitoring dashboards."""
        return {
            "executive_dashboard": {
                "widgets": ["revenue_trend", "user_metrics", "uptime_sla"],
                "refresh_rate": 300,
                "access_level": "executive"
            },
            "operations_dashboard": {
                "widgets": ["error_rate", "latency_percentiles", "throughput", "alerts"],
                "refresh_rate": 60,
                "access_level": "operations"
            },
            "engineering_dashboard": {
                "widgets": ["cpu", "memory", "queries", "traces", "logs"],
                "refresh_rate": 10,
                "access_level": "engineering"
            },
            "gaming_dashboard": {
                "widgets": ["bets_per_second", "active_games", "jackpot_status", "provider_health"],
                "refresh_rate": 5,
                "access_level": "gaming_operations"
            }
        }

    async def _setup_escalation_procedures(self) -> Dict[str, Any]:
        """Setup alert escalation procedures."""
        return {
            "escalation_matrix": {
                "critical": {
                    "initial": "on_call_engineer",
                    "5_min": "team_lead",
                    "15_min": "engineering_manager",
                    "30_min": "vp_engineering",
                    "60_min": "cto"
                },
                "high": {
                    "initial": "on_call_engineer",
                    "30_min": "team_lead",
                    "2_hours": "engineering_manager"
                },
                "medium": {
                    "initial": "team_slack_channel",
                    "2_hours": "on_call_engineer"
                }
            },
            "communication_templates": {
                "incident_start": True,
                "status_update": True,
                "resolution": True,
                "postmortem": True
            },
            "runbooks_linked": 45,
            "auto_remediation_enabled": True
        }

    def _calculate_alert_coverage(
        self,
        critical: Dict[str, Any],
        high: Dict[str, Any],
        medium: Dict[str, Any]
    ) -> float:
        """Calculate alert coverage score."""
        total_alerts = len(critical) + len(high) + len(medium)
        weighted_score = (len(critical) * 0.5 + len(high) * 0.3 + len(medium) * 0.2)
        return min(weighted_score / 3.0, 1.0)

    async def _analyze_false_positive_rate(self) -> Dict[str, Any]:
        """Analyze alert false positive rate."""
        return {
            "overall_rate": 0.05,
            "by_severity": {
                "critical": 0.02,
                "high": 0.04,
                "medium": 0.08
            },
            "trend": "decreasing",
            "top_false_positive_sources": [
                {"alert": "cpu_spike", "rate": 0.12, "action": "Increase threshold"},
                {"alert": "memory_warning", "rate": 0.08, "action": "Add hysteresis"}
            ]
        }

    async def _calculate_mttr_metrics(self) -> Dict[str, Any]:
        """Calculate mean time to resolution metrics."""
        return {
            "mttd": 2.5,   # Mean time to detection (minutes)
            "mtta": 5.2,   # Mean time to acknowledge (minutes)
            "mttr": 23.4,  # Mean time to resolution (minutes)
            "by_severity": {
                "critical": {"mttd": 1.2, "mtta": 2.0, "mttr": 15.0},
                "high": {"mttd": 3.5, "mtta": 8.0, "mttr": 35.0},
                "medium": {"mttd": 10.0, "mtta": 30.0, "mttr": 120.0}
            }
        }

    def _calculate_monitoring_maturity(
        self,
        metrics_list: List[Dict[str, Any]]
    ) -> float:
        """Calculate monitoring maturity score."""
        maturity_indicators = 0

        for metrics in metrics_list:
            if isinstance(metrics, dict):
                # Check for completeness indicators
                if metrics.get("coverage") and metrics["coverage"] > 0.9:
                    maturity_indicators += 1
                if metrics.get("alert_coverage_score") and metrics["alert_coverage_score"] > 0.7:
                    maturity_indicators += 1
                if "escalation_matrix" in metrics:
                    maturity_indicators += 1
                if metrics.get("auto_remediation_enabled"):
                    maturity_indicators += 1

        return min(maturity_indicators / 8.0, 1.0)
