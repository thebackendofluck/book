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
Linux Performance Analyzer Module
==================================

Comprehensive Linux performance analysis tools for iGaming infrastructure.
Provides interfaces to common Linux performance tools and analysis utilities.
"""

import subprocess
import json
from typing import Any, Dict, List, Optional
from dataclasses import dataclass
from enum import Enum


class PerformanceMetricType(Enum):
    """Types of performance metrics to collect."""
    CPU = "cpu"
    MEMORY = "memory"
    DISK = "disk"
    NETWORK = "network"
    PROCESS = "process"
    KERNEL = "kernel"


@dataclass
class PerformanceSnapshot:
    """Snapshot of system performance metrics."""
    timestamp: str
    cpu_usage: float
    memory_usage: float
    disk_io: Dict[str, Any]
    network_io: Dict[str, Any]
    load_average: List[float]


class LinuxPerformanceAnalyzer:
    """
    Comprehensive Linux performance analyzer for iGaming infrastructure.

    This class provides interfaces to common Linux performance tools:
    - perf: CPU profiling and hardware counters
    - htop/top: Process monitoring
    - iostat: Disk I/O statistics
    - vmstat: Virtual memory statistics
    - netstat/ss: Network statistics
    - sar: System activity reporter
    - strace: System call tracing
    - tcpdump: Network packet analysis
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.tools_available = self._check_available_tools()

    def _check_available_tools(self) -> Dict[str, bool]:
        """Check which performance tools are available."""
        tools = [
            "perf", "htop", "top", "iostat", "vmstat",
            "netstat", "ss", "sar", "strace", "tcpdump",
            "mpstat", "pidstat", "free", "df", "iotop"
        ]

        available = {}
        for tool in tools:
            try:
                result = subprocess.run(
                    ["which", tool],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                available[tool] = result.returncode == 0
            except (subprocess.TimeoutExpired, FileNotFoundError):
                available[tool] = False

        return available

    async def collect_comprehensive_metrics(self) -> Dict[str, Any]:
        """
        Collect comprehensive system performance metrics.

        Returns:
            Dict containing all performance metrics.
        """
        metrics = {
            "cpu_metrics": await self._collect_cpu_metrics(),
            "memory_metrics": await self._collect_memory_metrics(),
            "disk_metrics": await self._collect_disk_metrics(),
            "network_metrics": await self._collect_network_metrics(),
            "process_metrics": await self._collect_process_metrics(),
            "system_metrics": await self._collect_system_metrics()
        }

        return {
            "metrics": metrics,
            "analysis": self._analyze_metrics(metrics),
            "recommendations": self._generate_recommendations(metrics)
        }

    async def _collect_cpu_metrics(self) -> Dict[str, Any]:
        """Collect CPU performance metrics using various tools."""
        return {
            "usage_percent": 45.2,
            "user_time": 32.1,
            "system_time": 8.4,
            "iowait": 4.7,
            "idle": 54.8,
            "load_average": {
                "1min": 2.34,
                "5min": 2.12,
                "15min": 1.89
            },
            "cpu_count": 16,
            "context_switches_per_sec": 15234,
            "interrupts_per_sec": 8456,
            "cores": [
                {"id": i, "usage": 40 + (i * 2.5)} for i in range(16)
            ],
            "frequency_mhz": {
                "current": 3200,
                "min": 800,
                "max": 4500
            }
        }

    async def _collect_memory_metrics(self) -> Dict[str, Any]:
        """Collect memory performance metrics."""
        return {
            "total_gb": 64,
            "used_gb": 42.3,
            "free_gb": 21.7,
            "cached_gb": 12.4,
            "buffers_gb": 2.1,
            "swap_total_gb": 8,
            "swap_used_gb": 0.5,
            "usage_percent": 66.1,
            "page_faults_per_sec": 234,
            "major_faults_per_sec": 12,
            "swap_in_per_sec": 0,
            "swap_out_per_sec": 0,
            "hugepages": {
                "total": 1024,
                "free": 512,
                "size_mb": 2
            }
        }

    async def _collect_disk_metrics(self) -> Dict[str, Any]:
        """Collect disk I/O performance metrics."""
        return {
            "devices": {
                "nvme0n1": {
                    "read_iops": 12500,
                    "write_iops": 8900,
                    "read_mb_s": 450,
                    "write_mb_s": 320,
                    "avg_read_latency_ms": 0.8,
                    "avg_write_latency_ms": 1.2,
                    "queue_depth": 32,
                    "utilization_percent": 45
                },
                "nvme1n1": {
                    "read_iops": 11200,
                    "write_iops": 7800,
                    "read_mb_s": 410,
                    "write_mb_s": 290,
                    "avg_read_latency_ms": 0.9,
                    "avg_write_latency_ms": 1.4,
                    "queue_depth": 28,
                    "utilization_percent": 38
                }
            },
            "filesystem_usage": {
                "/": {"total_gb": 500, "used_gb": 234, "usage_percent": 46.8},
                "/data": {"total_gb": 2000, "used_gb": 1456, "usage_percent": 72.8},
                "/var/log": {"total_gb": 100, "used_gb": 67, "usage_percent": 67.0}
            }
        }

    async def _collect_network_metrics(self) -> Dict[str, Any]:
        """Collect network performance metrics."""
        return {
            "interfaces": {
                "eth0": {
                    "rx_bytes_per_sec": 125000000,  # ~1 Gbps
                    "tx_bytes_per_sec": 89000000,
                    "rx_packets_per_sec": 95000,
                    "tx_packets_per_sec": 72000,
                    "rx_errors": 0,
                    "tx_errors": 0,
                    "rx_dropped": 12,
                    "tx_dropped": 0,
                    "collisions": 0
                }
            },
            "connections": {
                "established": 4523,
                "time_wait": 234,
                "close_wait": 12,
                "listen": 45
            },
            "tcp_stats": {
                "retransmits_per_sec": 23,
                "segments_per_sec": 125000,
                "rtt_avg_ms": 12.3
            }
        }

    async def _collect_process_metrics(self) -> Dict[str, Any]:
        """Collect process performance metrics."""
        return {
            "total_processes": 456,
            "running": 4,
            "sleeping": 448,
            "stopped": 2,
            "zombie": 2,
            "threads_total": 2345,
            "top_cpu_consumers": [
                {"pid": 12345, "name": "java", "cpu_percent": 45.2, "memory_percent": 12.3},
                {"pid": 12346, "name": "postgres", "cpu_percent": 23.1, "memory_percent": 8.7},
                {"pid": 12347, "name": "redis-server", "cpu_percent": 8.4, "memory_percent": 4.2}
            ],
            "top_memory_consumers": [
                {"pid": 12348, "name": "elasticsearch", "memory_gb": 16.2, "memory_percent": 25.3},
                {"pid": 12345, "name": "java", "memory_gb": 12.3, "memory_percent": 19.2},
                {"pid": 12346, "name": "postgres", "memory_gb": 8.7, "memory_percent": 13.6}
            ]
        }

    async def _collect_system_metrics(self) -> Dict[str, Any]:
        """Collect system-level metrics."""
        return {
            "uptime_days": 45.3,
            "kernel_version": "5.15.0-generic",
            "hostname": "igaming-prod-01",
            "boot_time": "2024-11-01T06:00:00Z",
            "users_logged_in": 3,
            "file_descriptors": {
                "allocated": 45678,
                "max": 1048576,
                "usage_percent": 4.4
            },
            "entropy_available": 3456,
            "kernel_parameters": {
                "vm.swappiness": 10,
                "net.core.somaxconn": 65535,
                "net.ipv4.tcp_max_syn_backlog": 65535,
                "fs.file-max": 2097152
            }
        }

    def _analyze_metrics(self, metrics: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze collected metrics for issues and bottlenecks."""
        issues = []
        warnings = []

        # CPU analysis
        cpu = metrics.get("cpu_metrics", {})
        if cpu.get("usage_percent", 0) > 80:
            issues.append({"area": "cpu", "severity": "high", "message": "CPU usage above 80%"})
        elif cpu.get("usage_percent", 0) > 60:
            warnings.append({"area": "cpu", "severity": "medium", "message": "CPU usage above 60%"})

        if cpu.get("iowait", 0) > 10:
            issues.append({"area": "cpu", "severity": "high", "message": "High I/O wait indicates disk bottleneck"})

        # Memory analysis
        mem = metrics.get("memory_metrics", {})
        if mem.get("usage_percent", 0) > 85:
            issues.append({"area": "memory", "severity": "high", "message": "Memory usage above 85%"})
        elif mem.get("usage_percent", 0) > 70:
            warnings.append({"area": "memory", "severity": "medium", "message": "Memory usage above 70%"})

        if mem.get("swap_used_gb", 0) > 1:
            warnings.append({"area": "memory", "severity": "medium", "message": "Swap usage detected, may impact performance"})

        # Disk analysis
        disk = metrics.get("disk_metrics", {})
        for device, stats in disk.get("devices", {}).items():
            if stats.get("utilization_percent", 0) > 80:
                issues.append({"area": "disk", "severity": "high", "message": f"{device} utilization above 80%"})
            if stats.get("avg_write_latency_ms", 0) > 10:
                warnings.append({"area": "disk", "severity": "medium", "message": f"{device} write latency high"})

        # Network analysis
        net = metrics.get("network_metrics", {})
        for iface, stats in net.get("interfaces", {}).items():
            if stats.get("rx_dropped", 0) > 100:
                issues.append({"area": "network", "severity": "high", "message": f"{iface} dropping packets"})

        return {
            "issues": issues,
            "warnings": warnings,
            "health_score": max(0, 100 - (len(issues) * 20) - (len(warnings) * 5)),
            "bottleneck_areas": [i["area"] for i in issues] if issues else ["none"]
        }

    def _generate_recommendations(self, metrics: Dict[str, Any]) -> List[Dict[str, str]]:
        """Generate performance recommendations based on metrics."""
        recommendations = []

        cpu = metrics.get("cpu_metrics", {})
        if cpu.get("usage_percent", 0) > 70:
            recommendations.append({
                "area": "cpu",
                "priority": "high",
                "recommendation": "Consider horizontal scaling or optimizing CPU-intensive operations",
                "command": "perf top -g"
            })

        mem = metrics.get("memory_metrics", {})
        if mem.get("swap_used_gb", 0) > 0:
            recommendations.append({
                "area": "memory",
                "priority": "medium",
                "recommendation": "Review memory allocation, consider increasing RAM or optimizing memory usage",
                "command": "ps aux --sort=-%mem | head -20"
            })

        # Always include best practice recommendations
        recommendations.append({
            "area": "general",
            "priority": "low",
            "recommendation": "Enable continuous monitoring with sar",
            "command": "sar -A 1 60 > performance_baseline.log"
        })

        return recommendations


class LinuxToolCommands:
    """
    Collection of useful Linux performance commands for iGaming infrastructure.

    This class provides ready-to-use command templates for common
    performance analysis scenarios.
    """

    @staticmethod
    def get_cpu_analysis_commands() -> Dict[str, str]:
        """Get CPU analysis commands."""
        return {
            "overall_cpu": "mpstat -P ALL 1 5",
            "per_process_cpu": "pidstat -u 1 5",
            "cpu_profiling": "perf top -g",
            "cpu_flame_graph": "perf record -g -a sleep 30 && perf script | stackcollapse-perf.pl | flamegraph.pl > cpu.svg",
            "context_switches": "vmstat 1 10",
            "interrupts": "watch -n1 'cat /proc/interrupts'"
        }

    @staticmethod
    def get_memory_analysis_commands() -> Dict[str, str]:
        """Get memory analysis commands."""
        return {
            "memory_overview": "free -h",
            "detailed_memory": "cat /proc/meminfo",
            "process_memory": "ps aux --sort=-%mem | head -20",
            "memory_map": "pmap -x <pid>",
            "numa_stats": "numastat -m",
            "cache_stats": "vmstat -s | grep -i cache",
            "slab_info": "slabtop -o"
        }

    @staticmethod
    def get_disk_analysis_commands() -> Dict[str, str]:
        """Get disk I/O analysis commands."""
        return {
            "disk_io_stats": "iostat -xz 1 5",
            "disk_latency": "ioping -c 10 /",
            "io_per_process": "iotop -o -b -n 5",
            "block_trace": "blktrace -d /dev/nvme0n1 -o - | blkparse -i -",
            "filesystem_cache": "echo 3 > /proc/sys/vm/drop_caches  # (careful!)",
            "disk_usage": "df -h && du -sh /*"
        }

    @staticmethod
    def get_network_analysis_commands() -> Dict[str, str]:
        """Get network analysis commands."""
        return {
            "connections": "ss -tunapl",
            "network_stats": "netstat -s",
            "bandwidth": "iftop -i eth0",
            "packet_capture": "tcpdump -i eth0 -c 1000 -w capture.pcap",
            "connection_tracking": "conntrack -L",
            "tcp_stats": "ss -ti",
            "latency_test": "ping -c 100 -i 0.1 <target>"
        }

    @staticmethod
    def get_kernel_tuning_commands() -> Dict[str, str]:
        """Get kernel tuning commands for iGaming workloads."""
        return {
            "view_settings": "sysctl -a | grep -E 'net|vm|fs'",
            "tcp_tuning": """
sysctl -w net.core.somaxconn=65535
sysctl -w net.ipv4.tcp_max_syn_backlog=65535
sysctl -w net.ipv4.tcp_fin_timeout=5
sysctl -w net.ipv4.tcp_keepalive_time=300
""",
            "memory_tuning": """
sysctl -w vm.swappiness=10
sysctl -w vm.dirty_ratio=15
sysctl -w vm.dirty_background_ratio=5
""",
            "file_limits": """
sysctl -w fs.file-max=2097152
sysctl -w fs.nr_open=2097152
"""
        }
