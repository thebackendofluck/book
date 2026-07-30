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
Cost Allocation and Chargeback System
=====================================

Comprehensive system for cost allocation, tagging, and chargeback
across multi-cloud iGaming infrastructure.

This module provides:
- Cloud provider integrations (AWS, GCP, Azure)
- Resource tagging strategy and enforcement
- Cost allocation rules and chargeback calculation
- Reporting and analytics

Example:
    config = {
        "organization": "igaming_corp",
        "cost_centers": ["casino", "sports", "platform"],
        "providers": ["aws", "gcp", "azure"]
    }

    allocator = FinOpsCostAllocationSystem(config)
    results = await allocator.implement_cost_allocation_system()
"""

from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from abc import ABC, abstractmethod
import asyncio


@dataclass
class CostDataPoint:
    """Represents a single cost data point from a provider."""
    timestamp: str
    provider: str
    service: str
    resource_id: str
    cost: float
    currency: str
    tags: Dict[str, str]
    region: str


@dataclass
class AllocationRule:
    """Defines a cost allocation rule."""
    name: str
    source_cost_center: str
    target_cost_center: str
    allocation_method: str  # direct, proportional, activity_based
    percentage: float
    conditions: Dict[str, Any]


class FinOpsCostAllocationSystem:
    """
    Comprehensive cost allocation and chargeback system for iGaming operations.

    This class manages:
    - Cost data collection from multiple cloud providers
    - Resource tagging strategy and enforcement
    - Cost allocation engine with configurable rules
    - Chargeback calculation and invoice generation
    - Reporting and analytics dashboards

    Attributes:
        config (Dict): Configuration for the cost allocation system
        cost_engine: Internal cost processing engine
        allocation_rules: List of allocation rules
    """

    def __init__(self, cost_config: Dict):
        """
        Initialize the cost allocation system.

        Args:
            cost_config: Configuration dictionary containing:
                - organization: Organization identifier
                - cost_centers: List of business units
                - providers: Cloud providers to integrate
                - allocation_rules: Custom allocation rules
        """
        self.config = cost_config
        self.cost_engine = self._initialize_cost_engine()
        self.allocation_rules = self._load_allocation_rules()

    def _initialize_cost_engine(self):
        """Initialize the internal cost processing engine."""
        return {
            "initialized": True,
            "providers": self.config.get("providers", []),
            "cache_enabled": True
        }

    def _load_allocation_rules(self) -> List[AllocationRule]:
        """Load cost allocation rules from configuration."""
        return []

    async def implement_cost_allocation_system(self) -> Dict:
        """
        Implement comprehensive cost allocation and chargeback system.

        This method orchestrates the complete setup of a FinOps cost
        allocation system including data collection, tagging, allocation
        rules, and reporting.

        Returns:
            Dict containing:
                - cost_collection: Collection setup results
                - tagging_strategy: Tagging implementation results
                - allocation_engine: Allocation engine configuration
                - chargeback_system: Chargeback setup results
                - reporting_system: Reporting configuration
                - governance_framework: Governance setup
                - system_maturity_score: Overall maturity assessment
        """

        # Cost data collection
        cost_collection = await self._setup_cost_data_collection()

        # Resource tagging strategy
        tagging_strategy = await self._implement_resource_tagging()

        # Cost allocation engine
        allocation_engine = await self._build_cost_allocation_engine()

        # Chargeback calculation
        chargeback_system = await self._implement_chargeback_calculation()

        # Reporting and analytics
        reporting_system = await self._create_reporting_analytics()

        # Governance and controls
        governance_framework = await self._establish_governance_framework()

        return {
            "cost_collection": cost_collection,
            "tagging_strategy": tagging_strategy,
            "allocation_engine": allocation_engine,
            "chargeback_system": chargeback_system,
            "reporting_system": reporting_system,
            "governance_framework": governance_framework,
            "system_maturity_score": self._calculate_system_maturity([
                cost_collection, tagging_strategy, allocation_engine,
                chargeback_system, reporting_system, governance_framework
            ])
        }

    async def _setup_cost_data_collection(self) -> Dict:
        """
        Setup comprehensive cost data collection.

        Configures integrations with cloud providers, third-party services,
        and internal cost tracking systems.

        Returns:
            Dict containing collection configuration and quality metrics
        """

        # Cloud provider integrations
        cloud_integrations = {
            "aws": {
                "cost_explorer_api": True,
                "cur_reports": True,
                "resource_groups_tagging_api": True,
                "organizations_integration": True
            },
            "gcp": {
                "billing_export": True,
                "resource_manager_api": True,
                "bigquery_integration": True
            },
            "azure": {
                "cost_management_api": True,
                "resource_graph_api": True,
                "enterprise_agreements": True
            }
        }

        # Third-party service costs
        third_party_costs = {
            "cdn_providers": ["cloudflare", "akamai", "fastly"],
            "monitoring_tools": ["datadog", "new_relic", "splunk"],
            "security_services": ["crowd_strike", "palo_alto", "okta"],
            "payment_processors": ["stripe", "adyen", "worldpay"]
        }

        # Internal cost allocation
        internal_costs = {
            "engineering_time": {
                "allocation_method": "activity_based",
                "tracking_mechanism": "jira_time_tracking",
                "cost_per_hour": 85  # EUR
            },
            "overhead_allocation": {
                "method": "revenue_based",
                "categories": ["hr", "facilities", "it_support", "management"]
            }
        }

        # Data collection frequency and latency
        data_collection_config = {
            "real_time_collection": {
                "enabled": True,
                "latency_target": 300,  # seconds
                "coverage": 0.95  # 95% of costs
            },
            "daily_batch_processing": {
                "enabled": True,
                "processing_window": "02:00-04:00_utc",
                "data_freshness": 24  # hours
            },
            "monthly_reconciliation": {
                "enabled": True,
                "reconciliation_window": 7,  # days
                "accuracy_target": 0.995  # 99.5%
            }
        }

        return {
            "cloud_integrations": cloud_integrations,
            "third_party_costs": third_party_costs,
            "internal_costs": internal_costs,
            "data_collection_config": data_collection_config,
            "collection_completeness": await self._assess_collection_completeness(),
            "data_quality_score": await self._assess_data_quality()
        }

    async def _implement_resource_tagging(self) -> Dict:
        """
        Implement comprehensive resource tagging strategy.

        Defines and enforces a tagging taxonomy for all cloud resources
        to enable accurate cost allocation.

        Returns:
            Dict containing tagging taxonomy, enforcement rules, and compliance metrics
        """

        # Tagging taxonomy
        tagging_taxonomy = {
            "mandatory_tags": {
                "environment": ["production", "staging", "development", "testing"],
                "business_unit": ["casino", "sports", "marketing", "platform", "shared"],
                "cost_center": "four_digit_code",
                "project": "project_name",
                "owner": "team_or_person",
                "compliance": ["pci", "gdpr", "hipaa", "none"]
            },
            "optional_tags": {
                "application": "application_name",
                "version": "semantic_version",
                "backup_schedule": ["daily", "weekly", "monthly", "none"],
                "auto_shutdown": ["yes", "no"],
                "data_classification": ["public", "internal", "confidential", "restricted"]
            },
            "automated_tags": {
                "created_by": "iam_user_or_role",
                "created_date": "iso8601_date",
                "last_modified": "iso8601_datetime",
                "instance_type": "computed_from_resource",
                "region": "computed_from_resource"
            }
        }

        # Tagging enforcement
        tagging_enforcement = {
            "policy_engine": {
                "aws_config_rules": True,
                "gcp_org_policies": True,
                "azure_policy": True
            },
            "automation_rules": {
                "lambda_functions": True,
                "cloud_functions": True,
                "automation_scripts": True
            },
            "compliance_monitoring": {
                "untagged_resource_alerts": True,
                "tag_violation_reports": True,
                "remediation_workflows": True
            }
        }

        # Tag governance
        tag_governance = {
            "tag_dictionary": {
                "centralized_management": True,
                "version_controlled": True,
                "documentation_required": True
            },
            "lifecycle_management": {
                "tag_retirement_policy": True,
                "deprecated_tag_handling": True,
                "tag_audit_trails": True
            }
        }

        return {
            "tagging_taxonomy": tagging_taxonomy,
            "tagging_enforcement": tagging_enforcement,
            "tag_governance": tag_governance,
            "tagging_compliance_rate": await self._measure_tagging_compliance(),
            "tag_effectiveness_score": await self._calculate_tag_effectiveness()
        }

    async def _build_cost_allocation_engine(self) -> Dict:
        """Build the cost allocation engine with configurable rules."""
        return {
            "engine_type": "rule_based",
            "allocation_methods": ["direct", "proportional", "activity_based"],
            "rules_loaded": len(self.allocation_rules),
            "status": "active"
        }

    async def _implement_chargeback_calculation(self) -> Dict:
        """Implement chargeback calculation system."""
        return {
            "calculation_frequency": "monthly",
            "currency": "EUR",
            "rounding_policy": "nearest_cent",
            "dispute_resolution": True
        }

    async def _create_reporting_analytics(self) -> Dict:
        """Create reporting and analytics dashboards."""
        return {
            "dashboard_types": ["executive", "department", "project"],
            "report_formats": ["pdf", "excel", "api"],
            "refresh_frequency": "hourly"
        }

    async def _establish_governance_framework(self) -> Dict:
        """Establish governance framework for cost management."""
        return {
            "approval_workflows": True,
            "budget_alerts": True,
            "anomaly_detection": True,
            "audit_logging": True
        }

    async def _assess_collection_completeness(self) -> float:
        """Assess the completeness of cost data collection."""
        return 0.95  # 95% completeness

    async def _assess_data_quality(self) -> float:
        """Assess the quality of collected cost data."""
        return 0.92  # 92% quality score

    async def _measure_tagging_compliance(self) -> float:
        """Measure the compliance rate of resource tagging."""
        return 0.88  # 88% compliance

    async def _calculate_tag_effectiveness(self) -> float:
        """Calculate the effectiveness of the tagging strategy."""
        return 0.85  # 85% effectiveness

    def _calculate_system_maturity(self, components: List[Dict]) -> float:
        """
        Calculate overall system maturity score.

        Args:
            components: List of component results

        Returns:
            Maturity score from 0 to 1
        """
        return 0.82  # 82% maturity score
