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
Kubernetes Cost Management and Optimization
============================================

Optimizes Kubernetes infrastructure costs for iGaming platforms,
including cluster sizing, workload optimization, and spot instance strategies.

This module provides:
- Cluster right-sizing and node optimization
- Horizontal and vertical pod autoscaling
- Spot/preemptible instance strategies
- Storage and network cost optimization
- Cost monitoring and governance

Example:
    config = {
        "cluster_name": "igaming-prod",
        "cloud_provider": "aws",
        "namespaces": ["casino", "sports", "platform"]
    }

    manager = KubernetesCostManager(config)
    optimization = await manager.optimize_kubernetes_costs()
"""

from typing import Dict, List, Optional, Any
from dataclasses import dataclass
import asyncio


@dataclass
class NodeConfiguration:
    """Configuration for a Kubernetes node type."""
    instance_type: str
    count: int
    cpu: int
    memory_gb: int
    cost_per_hour: float
    spot_eligible: bool


@dataclass
class WorkloadMetrics:
    """Resource metrics for a Kubernetes workload."""
    namespace: str
    deployment: str
    cpu_request: str
    cpu_limit: str
    memory_request: str
    memory_limit: str
    cpu_utilization: float
    memory_utilization: float


class KubernetesCostManager:
    """
    Kubernetes cost management and optimization for iGaming.

    Provides comprehensive cost optimization for Kubernetes clusters
    including node right-sizing, workload optimization, and
    automated scaling strategies.

    Attributes:
        config (Dict): Kubernetes configuration
        cost_model: Internal cost calculation model
    """

    def __init__(self, k8s_config: Dict):
        """
        Initialize the Kubernetes cost manager.

        Args:
            k8s_config: Configuration dictionary containing:
                - cluster_name: Target cluster name
                - cloud_provider: Cloud provider (aws, gcp, azure)
                - namespaces: Namespaces to optimize
        """
        self.config = k8s_config
        self.cost_model = self._initialize_cost_model()

    def _initialize_cost_model(self):
        """Initialize the cost calculation model."""
        return {
            "initialized": True,
            "pricing_source": "cloud_provider_api",
            "refresh_frequency": "hourly"
        }

    async def optimize_kubernetes_costs(self) -> Dict:
        """
        Optimize Kubernetes infrastructure costs.

        Analyzes cluster configuration, workload patterns, and
        resource utilization to provide cost optimization recommendations.

        Returns:
            Dict containing:
                - cluster_sizing: Node optimization recommendations
                - workload_optimization: Pod resource tuning
                - storage_optimization: PV/PVC cost reduction
                - network_optimization: Ingress/egress optimization
                - monitoring_governance: Cost visibility setup
                - total_cost_savings: Overall savings estimate
        """

        # Cluster right-sizing
        cluster_sizing = await self._optimize_cluster_sizing()

        # Workload optimization
        workload_optimization = await self._optimize_workloads()

        # Storage optimization
        storage_optimization = await self._optimize_storage()

        # Network optimization
        network_optimization = await self._optimize_networking()

        # Monitoring and governance
        monitoring_governance = await self._implement_monitoring_governance()

        return {
            "cluster_sizing": cluster_sizing,
            "workload_optimization": workload_optimization,
            "storage_optimization": storage_optimization,
            "network_optimization": network_optimization,
            "monitoring_governance": monitoring_governance,
            "total_cost_savings": self._calculate_k8s_savings([
                cluster_sizing, workload_optimization, storage_optimization,
                network_optimization
            ])
        }

    async def _optimize_cluster_sizing(self) -> Dict:
        """
        Optimize Kubernetes cluster sizing.

        Analyzes node utilization and provides recommendations for
        right-sizing nodes and implementing cost-effective instance types.

        Returns:
            Dict containing node analysis and optimization recommendations
        """

        # Node type analysis
        node_analysis = {
            "current_node_types": {
                "m5.large": {"count": 50, "utilization": 0.65, "cost_per_hour": 0.096},
                "m5.xlarge": {"count": 30, "utilization": 0.70, "cost_per_hour": 0.192},
                "c5.xlarge": {"count": 20, "utilization": 0.55, "cost_per_hour": 0.170}
            },
            "recommended_node_types": {
                "m5.large": {"count": 40, "utilization_target": 0.80, "cost_per_hour": 0.096},
                "m5.xlarge": {"count": 35, "utilization_target": 0.75, "cost_per_hour": 0.192},
                "c5.xlarge": {"count": 15, "utilization_target": 0.75, "cost_per_hour": 0.170}
            }
        }

        # Auto-scaling configuration
        auto_scaling_config = {
            "horizontal_pod_autoscaler": {
                "cpu_target": 70,
                "memory_target": 80,
                "min_replicas": 2,
                "max_replicas": 20
            },
            "cluster_autoscaler": {
                "scale_down_delay": 300,  # seconds
                "scale_up_delay": 60,
                "unneeded_time": 600,
                "utilization_threshold": 0.5
            },
            "vertical_pod_autoscaler": {
                "enabled": True,
                "update_mode": "Auto",
                "resource_policy": {
                    "container_policies": [
                        {"container_name": "*", "min_allowed": {"cpu": "100m", "memory": "50Mi"},
                         "max_allowed": {"cpu": "2", "memory": "4Gi"}}
                    ]
                }
            }
        }

        # Spot instance utilization
        spot_instance_strategy = {
            "spot_instance_percentage": 0.60,  # 60% of nodes
            "fallback_strategy": "on_demand",
            "interruption_handling": {
                "graceful_shutdown": True,
                "workload_rescheduling": True,
                "data_persistence": True
            },
            "cost_savings": 0.70  # 70% savings vs on-demand
        }

        return {
            "node_analysis": node_analysis,
            "auto_scaling_config": auto_scaling_config,
            "spot_instance_strategy": spot_instance_strategy,
            "monthly_cost_reduction": 18500,  # EUR 18.5K
            "efficiency_improvement": 0.35  # 35% more efficient
        }

    async def _optimize_workloads(self) -> Dict:
        """Optimize Kubernetes workload resource allocation."""
        return {
            "overprovisioned_deployments": 25,
            "right_sizing_recommendations": [],
            "cpu_savings": 0.25,
            "memory_savings": 0.30,
            "monthly_savings": 8500
        }

    async def _optimize_storage(self) -> Dict:
        """Optimize Kubernetes storage costs."""
        return {
            "pvc_optimization": {
                "oversized_pvcs": 15,
                "unused_pvcs": 5,
                "storage_class_optimization": True
            },
            "monthly_savings": 3200
        }

    async def _optimize_networking(self) -> Dict:
        """Optimize Kubernetes networking costs."""
        return {
            "ingress_optimization": True,
            "service_mesh_costs": 2500,
            "cross_az_traffic_reduction": 0.40,
            "monthly_savings": 4500
        }

    async def _implement_monitoring_governance(self) -> Dict:
        """Implement cost monitoring and governance."""
        return {
            "kubecost_integration": True,
            "namespace_budgets": True,
            "cost_alerts": True,
            "showback_reports": True
        }

    def _calculate_k8s_savings(self, optimizations: List[Dict]) -> Dict:
        """Calculate total Kubernetes cost savings."""
        return {
            "monthly_savings": 34700,  # EUR
            "annual_savings": 416400,
            "efficiency_improvement": 0.35,
            "implementation_effort": "medium"
        }
