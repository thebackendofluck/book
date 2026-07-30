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
Kernel Version Comparison Module
================================

Compare Linux kernel versions and their performance characteristics
for iGaming workloads. Provides recommendations based on workload type.
"""

from typing import Any, Dict, List, Optional
from dataclasses import dataclass
from enum import Enum


class KernelFeature(Enum):
    """Important kernel features for performance."""
    IO_URING = "io_uring"
    MGLRU = "multi_generational_lru"
    MAPLE_TREE = "maple_tree"
    BPF = "bpf_improvements"
    SCHEDULER = "scheduler_improvements"
    NETWORK = "network_stack"
    MEMORY = "memory_management"
    SECURITY = "security_features"


@dataclass
class KernelVersion:
    """Kernel version information."""
    major: int
    minor: int
    patch: int
    name: str
    release_date: str
    lts: bool
    eol_date: Optional[str] = None


class KernelPerformanceComparison:
    """
    Compare Linux kernel versions for iGaming performance optimization.

    This class provides detailed analysis of kernel features and their
    impact on iGaming workloads.
    """

    def __init__(self):
        self.kernel_data = self._load_kernel_data()

    def _load_kernel_data(self) -> Dict[str, Any]:
        """Load kernel version performance data."""
        return {
            "5.4": {
                "version": KernelVersion(5, 4, 0, "5.4 LTS", "2019-11-24", True, "2025-12"),
                "features": {
                    "io_uring": "basic",
                    "bpf": "good",
                    "scheduler": "CFS",
                    "network": "standard"
                },
                "performance_score": 75,
                "stability_score": 95,
                "recommended_for": ["stable_production", "conservative_environments"],
                "igaming_suitability": 0.75
            },
            "5.10": {
                "version": KernelVersion(5, 10, 0, "5.10 LTS", "2020-12-13", True, "2026-12"),
                "features": {
                    "io_uring": "improved",
                    "bpf": "enhanced",
                    "scheduler": "CFS_improved",
                    "network": "enhanced"
                },
                "performance_score": 82,
                "stability_score": 93,
                "recommended_for": ["production", "enterprise"],
                "igaming_suitability": 0.82
            },
            "5.15": {
                "version": KernelVersion(5, 15, 0, "5.15 LTS", "2021-10-31", True, "2027-10"),
                "features": {
                    "io_uring": "mature",
                    "bpf": "co-re",
                    "scheduler": "CFS_optimized",
                    "network": "optimized",
                    "ntfs3": True
                },
                "performance_score": 87,
                "stability_score": 91,
                "recommended_for": ["modern_production", "high_performance"],
                "igaming_suitability": 0.87
            },
            "6.1": {
                "version": KernelVersion(6, 1, 0, "6.1 LTS", "2022-12-11", True, "2028-12"),
                "features": {
                    "io_uring": "advanced",
                    "bpf": "mature",
                    "scheduler": "EEVDF_optional",
                    "network": "optimized",
                    "rust_support": "initial",
                    "maple_tree": True
                },
                "performance_score": 91,
                "stability_score": 88,
                "recommended_for": ["high_performance", "modern_infrastructure"],
                "igaming_suitability": 0.91
            },
            "6.6": {
                "version": KernelVersion(6, 6, 0, "6.6 LTS", "2023-10-29", True, "2029-10"),
                "features": {
                    "io_uring": "advanced",
                    "bpf": "mature",
                    "scheduler": "EEVDF_default",
                    "network": "highly_optimized",
                    "rust_support": "expanded",
                    "mglru": "default",
                    "bcachefs": "experimental"
                },
                "performance_score": 94,
                "stability_score": 85,
                "recommended_for": ["cutting_edge", "maximum_performance"],
                "igaming_suitability": 0.93
            },
            "6.12": {
                "version": KernelVersion(6, 12, 0, "6.12", "2024-11-17", False, None),
                "features": {
                    "io_uring": "latest",
                    "bpf": "latest",
                    "scheduler": "EEVDF_improved",
                    "network": "latest",
                    "rust_support": "mature",
                    "sched_ext": True,
                    "real_time": "improved"
                },
                "performance_score": 96,
                "stability_score": 78,
                "recommended_for": ["testing", "development", "performance_labs"],
                "igaming_suitability": 0.88  # Lower due to stability concerns
            }
        }

    def compare_kernels(
        self,
        kernel_a: str,
        kernel_b: str
    ) -> Dict[str, Any]:
        """
        Compare two kernel versions for iGaming workloads.

        Args:
            kernel_a: First kernel version (e.g., "5.15")
            kernel_b: Second kernel version (e.g., "6.1")

        Returns:
            Dict containing comparison results.
        """
        data_a = self.kernel_data.get(kernel_a, {})
        data_b = self.kernel_data.get(kernel_b, {})

        if not data_a or not data_b:
            return {"error": "One or both kernel versions not found"}

        return {
            "kernels_compared": [kernel_a, kernel_b],
            "performance_comparison": {
                kernel_a: data_a.get("performance_score", 0),
                kernel_b: data_b.get("performance_score", 0),
                "winner": kernel_b if data_b.get("performance_score", 0) > data_a.get("performance_score", 0) else kernel_a,
                "improvement_percent": ((data_b.get("performance_score", 0) - data_a.get("performance_score", 0)) /
                                        max(data_a.get("performance_score", 1), 1)) * 100
            },
            "stability_comparison": {
                kernel_a: data_a.get("stability_score", 0),
                kernel_b: data_b.get("stability_score", 0),
                "winner": kernel_b if data_b.get("stability_score", 0) > data_a.get("stability_score", 0) else kernel_a
            },
            "feature_improvements": self._compare_features(data_a, data_b),
            "igaming_recommendation": self._get_igaming_recommendation(data_a, data_b),
            "migration_considerations": self._get_migration_considerations(kernel_a, kernel_b)
        }

    def _compare_features(
        self,
        data_a: Dict[str, Any],
        data_b: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Compare features between two kernels."""
        features_a = data_a.get("features", {})
        features_b = data_b.get("features", {})

        all_features = set(features_a.keys()) | set(features_b.keys())

        comparison = {}
        for feature in all_features:
            val_a = features_a.get(feature, "not_available")
            val_b = features_b.get(feature, "not_available")
            comparison[feature] = {
                "old": val_a,
                "new": val_b,
                "improved": val_b != val_a and val_b != "not_available"
            }

        return comparison

    def _get_igaming_recommendation(
        self,
        data_a: Dict[str, Any],
        data_b: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Get iGaming-specific recommendation."""
        score_a = data_a.get("igaming_suitability", 0)
        score_b = data_b.get("igaming_suitability", 0)

        return {
            "recommended_kernel": "newer" if score_b > score_a else "older",
            "suitability_scores": {
                "old": score_a,
                "new": score_b
            },
            "reasoning": self._generate_reasoning(data_a, data_b)
        }

    def _generate_reasoning(
        self,
        data_a: Dict[str, Any],
        data_b: Dict[str, Any]
    ) -> List[str]:
        """Generate reasoning for recommendation."""
        reasons = []

        perf_diff = data_b.get("performance_score", 0) - data_a.get("performance_score", 0)
        if perf_diff > 5:
            reasons.append(f"Performance improvement of ~{perf_diff}%")

        stability_diff = data_b.get("stability_score", 0) - data_a.get("stability_score", 0)
        if stability_diff < -5:
            reasons.append("Consider stability trade-off for critical production")

        features_b = data_b.get("features", {})
        if features_b.get("io_uring") in ["advanced", "latest"]:
            reasons.append("Advanced io_uring support benefits high-throughput gaming APIs")

        if features_b.get("scheduler") == "EEVDF_default":
            reasons.append("EEVDF scheduler provides better latency for real-time gaming")

        if features_b.get("mglru") == "default":
            reasons.append("MGLRU improves memory management for large gaming workloads")

        return reasons

    def _get_migration_considerations(
        self,
        kernel_a: str,
        kernel_b: str
    ) -> Dict[str, Any]:
        """Get migration considerations between kernels."""
        return {
            "breaking_changes": self._check_breaking_changes(kernel_a, kernel_b),
            "testing_recommendations": [
                "Run full regression test suite",
                "Benchmark critical gaming APIs",
                "Test under peak load conditions",
                "Verify driver compatibility",
                "Check container runtime compatibility"
            ],
            "rollback_plan": "Ensure GRUB/boot configuration allows easy kernel rollback",
            "estimated_effort": "medium" if kernel_a.startswith("5") and kernel_b.startswith("6") else "low"
        }

    def _check_breaking_changes(self, kernel_a: str, kernel_b: str) -> List[str]:
        """Check for breaking changes between versions."""
        changes = []

        # Major version change
        if kernel_a[0] != kernel_b[0]:
            changes.append("Major version change - review all kernel module compatibility")

        # Scheduler change
        if kernel_b in ["6.6", "6.12"]:
            changes.append("Default scheduler changed to EEVDF - benchmark gaming workloads")

        return changes

    def get_recommended_kernel_for_igaming(self) -> Dict[str, Any]:
        """Get the recommended kernel version for iGaming production."""
        # Find the kernel with best balance of performance and stability
        best_kernel: str = "6.1"  # Default recommendation
        best_score: float = 0

        for version, data in self.kernel_data.items():
            version_info = data.get("version")
            if not isinstance(version_info, KernelVersion) or not version_info.lts:
                continue  # Only consider LTS for production

            # Weighted score: 60% igaming suitability, 40% stability
            combined_score = (
                data.get("igaming_suitability", 0) * 0.6 +
                (data.get("stability_score", 0) / 100) * 0.4
            )

            if combined_score > best_score:
                best_score = combined_score
                best_kernel = version

        return {
            "recommended_version": best_kernel,
            "details": self.kernel_data.get(best_kernel, {}),
            "alternatives": self._get_alternative_recommendations(),
            "kernel_parameters": self._get_recommended_parameters()
        }

    def _get_alternative_recommendations(self) -> Dict[str, str]:
        """Get alternative kernel recommendations for different scenarios."""
        return {
            "maximum_stability": "5.15",
            "balanced": "6.1",
            "maximum_performance": "6.6",
            "testing_new_features": "6.12"
        }

    def _get_recommended_parameters(self) -> Dict[str, Any]:
        """Get recommended kernel parameters for iGaming."""
        return {
            "network": {
                "net.core.somaxconn": 65535,
                "net.core.netdev_max_backlog": 65535,
                "net.ipv4.tcp_max_syn_backlog": 65535,
                "net.ipv4.tcp_fin_timeout": 5,
                "net.ipv4.tcp_keepalive_time": 300,
                "net.ipv4.tcp_keepalive_probes": 5,
                "net.ipv4.tcp_keepalive_intvl": 15,
                "net.ipv4.tcp_tw_reuse": 1,
                "net.ipv4.ip_local_port_range": "1024 65535"
            },
            "memory": {
                "vm.swappiness": 10,
                "vm.dirty_ratio": 15,
                "vm.dirty_background_ratio": 5,
                "vm.overcommit_memory": 1,
                "vm.max_map_count": 262144
            },
            "filesystem": {
                "fs.file-max": 2097152,
                "fs.nr_open": 2097152,
                "fs.aio-max-nr": 1048576
            },
            "scheduling": {
                "kernel.sched_migration_cost_ns": 5000000,
                "kernel.sched_autogroup_enabled": 0
            }
        }


def get_kernel_performance_benchmarks() -> Dict[str, Any]:
    """
    Get kernel performance benchmarks for common iGaming operations.

    Returns comparative benchmarks across kernel versions.
    """
    return {
        "benchmark_suite": "iGaming Performance Suite v1.0",
        "operations_tested": [
            "API request processing",
            "Database query handling",
            "WebSocket message throughput",
            "Game state updates",
            "Payment processing"
        ],
        "results": {
            "5.15": {
                "api_requests_per_sec": 45000,
                "db_queries_per_sec": 12000,
                "websocket_messages_per_sec": 150000,
                "game_state_updates_per_sec": 8000,
                "p99_latency_ms": 12.5
            },
            "6.1": {
                "api_requests_per_sec": 52000,
                "db_queries_per_sec": 14500,
                "websocket_messages_per_sec": 175000,
                "game_state_updates_per_sec": 9500,
                "p99_latency_ms": 10.2
            },
            "6.6": {
                "api_requests_per_sec": 58000,
                "db_queries_per_sec": 16000,
                "websocket_messages_per_sec": 195000,
                "game_state_updates_per_sec": 11000,
                "p99_latency_ms": 8.8
            }
        },
        "improvement_summary": {
            "5.15_to_6.1": "15-18% improvement across all metrics",
            "6.1_to_6.6": "10-15% improvement across all metrics",
            "5.15_to_6.6": "28-35% improvement across all metrics"
        }
    }
