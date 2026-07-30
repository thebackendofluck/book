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
OpenSearch Performance Benchmark for iGaming Platforms

This module provides comprehensive OpenSearch benchmarking including:
- Search latency and throughput testing
- Indexing performance measurement
- Cluster health and shard balance analysis
- Query optimization recommendations
- iGaming-specific search patterns (game search, bet history, user activity)

Performance Targets for iGaming:
- Search latency P99: <100ms
- Indexing throughput: >10K docs/sec
- Cluster status: Green
- JVM heap usage: <75%

Usage:
    benchmark = OpenSearchBenchmark(hosts=["localhost:9200"])
    results = await benchmark.run_full_benchmark()
    benchmark.print_report(results)

Dependencies:
    pip install opensearch-py aiohttp
    # or
    uv pip install opensearch-py aiohttp
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
import asyncio
import random
import time


class BenchmarkStatus(Enum):
    """Status of benchmark execution."""
    PASSED = "PASSED"
    WARNING = "WARNING"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


@dataclass
class BenchmarkResult:
    """Result of a single benchmark test."""
    name: str
    status: BenchmarkStatus
    value: float
    unit: str
    target: float
    description: str
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def passed(self) -> bool:
        return self.status == BenchmarkStatus.PASSED

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "value": self.value,
            "unit": self.unit,
            "target": self.target,
            "description": self.description,
            "details": self.details,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class OpenSearchConfig:
    """OpenSearch connection and benchmark configuration."""
    hosts: list[str] = field(default_factory=lambda: ["localhost:9200"])
    use_ssl: bool = False
    verify_certs: bool = True
    username: Optional[str] = None
    password: Optional[str] = None

    # Benchmark settings
    test_index: str = "benchmark-test"
    num_shards: int = 5
    num_replicas: int = 1
    document_count: int = 100000
    bulk_size: int = 1000
    search_iterations: int = 1000

    # iGaming-specific targets
    search_latency_p99_target_ms: float = 100.0
    indexing_throughput_target: int = 10000
    jvm_heap_target_percent: float = 75.0
    query_cache_hit_rate_target: float = 80.0


class OpenSearchHealthCheck:
    """Health check utilities for OpenSearch cluster."""

    def __init__(self, config: OpenSearchConfig):
        self.config = config

    async def check_cluster_health(self) -> BenchmarkResult:
        """Check OpenSearch cluster health status."""
        # Simulated cluster health
        cluster_status = "green"
        active_shards = 50
        relocating_shards = 0
        initializing_shards = 0
        unassigned_shards = 0

        status_map = {
            "green": BenchmarkStatus.PASSED,
            "yellow": BenchmarkStatus.WARNING,
            "red": BenchmarkStatus.FAILED,
        }

        return BenchmarkResult(
            name="Cluster Health",
            status=status_map.get(cluster_status, BenchmarkStatus.FAILED),
            value=1 if cluster_status == "green" else 0,
            unit="green",
            target=1,
            description="OpenSearch cluster health status",
            details={
                "status": cluster_status,
                "active_primary_shards": active_shards // 2,
                "active_shards": active_shards,
                "relocating_shards": relocating_shards,
                "initializing_shards": initializing_shards,
                "unassigned_shards": unassigned_shards,
                "number_of_nodes": 3,
                "number_of_data_nodes": 3,
            },
        )

    async def check_jvm_memory(self) -> BenchmarkResult:
        """Check JVM heap memory usage across nodes."""
        # Simulated JVM metrics per node
        node_memory = {
            "node-1": {"heap_used_percent": 65, "heap_max_bytes": 8589934592},
            "node-2": {"heap_used_percent": 58, "heap_max_bytes": 8589934592},
            "node-3": {"heap_used_percent": 71, "heap_max_bytes": 8589934592},
        }

        max_heap_percent = max(n["heap_used_percent"] for n in node_memory.values())
        avg_heap_percent = sum(n["heap_used_percent"] for n in node_memory.values()) / len(node_memory)
        target = self.config.jvm_heap_target_percent

        if max_heap_percent <= target:
            status = BenchmarkStatus.PASSED
        elif max_heap_percent <= 85:
            status = BenchmarkStatus.WARNING
        else:
            status = BenchmarkStatus.FAILED

        return BenchmarkResult(
            name="JVM Heap Usage",
            status=status,
            value=round(max_heap_percent, 1),
            unit="%",
            target=target,
            description="Maximum JVM heap usage across nodes",
            details={
                "max_heap_percent": max_heap_percent,
                "avg_heap_percent": round(avg_heap_percent, 1),
                "node_memory": node_memory,
                "gc_recommendation": "OK" if max_heap_percent < 75 else "Consider GC tuning",
            },
        )

    async def check_disk_usage(self) -> BenchmarkResult:
        """Check disk usage across data nodes."""
        # Simulated disk usage
        node_disk = {
            "node-1": {"used_percent": 45, "total_bytes": 1099511627776},
            "node-2": {"used_percent": 52, "total_bytes": 1099511627776},
            "node-3": {"used_percent": 48, "total_bytes": 1099511627776},
        }

        max_disk_percent = max(n["used_percent"] for n in node_disk.values())

        if max_disk_percent <= 70:
            status = BenchmarkStatus.PASSED
        elif max_disk_percent <= 85:
            status = BenchmarkStatus.WARNING
        else:
            status = BenchmarkStatus.FAILED

        return BenchmarkResult(
            name="Disk Usage",
            status=status,
            value=round(max_disk_percent, 1),
            unit="%",
            target=70.0,
            description="Maximum disk usage across data nodes",
            details={
                "max_disk_percent": max_disk_percent,
                "node_disk": node_disk,
                "watermark_low": "85%",
                "watermark_high": "90%",
                "watermark_flood": "95%",
            },
        )

    async def check_shard_balance(self) -> BenchmarkResult:
        """Check shard distribution balance across nodes."""
        # Simulated shard distribution
        shards_per_node = {
            "node-1": 17,
            "node-2": 16,
            "node-3": 17,
        }

        total_shards = sum(shards_per_node.values())
        ideal_per_node = total_shards / len(shards_per_node)
        max_deviation = max(abs(s - ideal_per_node) for s in shards_per_node.values())
        balance_score = 100 - (max_deviation / ideal_per_node * 100) if ideal_per_node > 0 else 100

        if balance_score >= 90:
            status = BenchmarkStatus.PASSED
        elif balance_score >= 75:
            status = BenchmarkStatus.WARNING
        else:
            status = BenchmarkStatus.FAILED

        return BenchmarkResult(
            name="Shard Balance",
            status=status,
            value=round(balance_score, 1),
            unit="%",
            target=90.0,
            description="Shard distribution balance across nodes",
            details={
                "shards_per_node": shards_per_node,
                "total_shards": total_shards,
                "ideal_per_node": round(ideal_per_node, 1),
                "max_deviation": round(max_deviation, 1),
            },
        )


class OpenSearchBenchmark:
    """
    Comprehensive OpenSearch performance benchmarking for iGaming platforms.

    Tests search and indexing performance with iGaming-specific patterns.
    """

    def __init__(
        self,
        hosts: Optional[list[str]] = None,
        config: Optional[OpenSearchConfig] = None,
    ):
        self.config = config or OpenSearchConfig(hosts=hosts or ["localhost:9200"])
        self.health_check = OpenSearchHealthCheck(self.config)
        self.results: list[BenchmarkResult] = []

    async def benchmark_search_latency(self) -> BenchmarkResult:
        """Benchmark search query latency."""
        latencies: list[float] = []

        # Simulate search queries
        for _ in range(self.config.search_iterations):
            start = time.perf_counter()
            await asyncio.sleep(random.uniform(0.005, 0.08))  # Simulate search
            latencies.append((time.perf_counter() - start) * 1000)

        latencies.sort()
        p50 = latencies[len(latencies) // 2]
        p95 = latencies[int(len(latencies) * 0.95)]
        p99 = latencies[int(len(latencies) * 0.99)]
        avg = sum(latencies) / len(latencies)

        target = self.config.search_latency_p99_target_ms

        if p99 <= target:
            status = BenchmarkStatus.PASSED
        elif p99 <= target * 1.5:
            status = BenchmarkStatus.WARNING
        else:
            status = BenchmarkStatus.FAILED

        return BenchmarkResult(
            name="Search Latency P99",
            status=status,
            value=round(p99, 2),
            unit="ms",
            target=target,
            description="99th percentile search latency",
            details={
                "p50_ms": round(p50, 2),
                "p95_ms": round(p95, 2),
                "p99_ms": round(p99, 2),
                "avg_ms": round(avg, 2),
                "min_ms": round(min(latencies), 2),
                "max_ms": round(max(latencies), 2),
                "iterations": len(latencies),
            },
        )

    async def benchmark_indexing_throughput(self) -> BenchmarkResult:
        """Benchmark document indexing throughput."""
        doc_count = self.config.document_count
        bulk_size = self.config.bulk_size

        start = time.perf_counter()

        # Simulate bulk indexing
        batches = doc_count // bulk_size
        for _ in range(batches):
            await asyncio.sleep(0.01)  # Simulate bulk index

        elapsed = time.perf_counter() - start
        throughput = doc_count / elapsed
        target = self.config.indexing_throughput_target

        if throughput >= target:
            status = BenchmarkStatus.PASSED
        elif throughput >= target * 0.8:
            status = BenchmarkStatus.WARNING
        else:
            status = BenchmarkStatus.FAILED

        return BenchmarkResult(
            name="Indexing Throughput",
            status=status,
            value=round(throughput, 0),
            unit="docs/sec",
            target=target,
            description="Documents indexed per second",
            details={
                "documents_indexed": doc_count,
                "elapsed_seconds": round(elapsed, 3),
                "bulk_size": bulk_size,
                "batches": batches,
                "refresh_interval": "1s",
            },
        )

    async def benchmark_query_cache(self) -> BenchmarkResult:
        """Benchmark query cache hit rate."""
        # Simulated cache metrics
        cache_hits = random.randint(8000, 9500)
        cache_misses = random.randint(500, 2000)
        total_queries = cache_hits + cache_misses
        hit_rate = (cache_hits / total_queries) * 100 if total_queries > 0 else 0

        target = self.config.query_cache_hit_rate_target

        if hit_rate >= target:
            status = BenchmarkStatus.PASSED
        elif hit_rate >= target * 0.9:
            status = BenchmarkStatus.WARNING
        else:
            status = BenchmarkStatus.FAILED

        return BenchmarkResult(
            name="Query Cache Hit Rate",
            status=status,
            value=round(hit_rate, 1),
            unit="%",
            target=target,
            description="Query cache hit rate percentage",
            details={
                "cache_hits": cache_hits,
                "cache_misses": cache_misses,
                "total_queries": total_queries,
                "cache_size_bytes": 104857600,  # 100MB
                "evictions": random.randint(10, 100),
            },
        )

    async def benchmark_aggregation_performance(self) -> BenchmarkResult:
        """Benchmark aggregation query performance."""
        # Simulate different aggregation types
        agg_results = {
            "terms_aggregation": {
                "latency_ms": random.uniform(20, 80),
                "doc_count": 1000000,
            },
            "date_histogram": {
                "latency_ms": random.uniform(30, 100),
                "doc_count": 1000000,
            },
            "nested_aggregation": {
                "latency_ms": random.uniform(50, 150),
                "doc_count": 500000,
            },
            "cardinality": {
                "latency_ms": random.uniform(15, 50),
                "doc_count": 1000000,
            },
        }

        max_latency = max(r["latency_ms"] for r in agg_results.values())
        avg_latency = sum(r["latency_ms"] for r in agg_results.values()) / len(agg_results)

        if max_latency <= 100:
            status = BenchmarkStatus.PASSED
        elif max_latency <= 150:
            status = BenchmarkStatus.WARNING
        else:
            status = BenchmarkStatus.FAILED

        return BenchmarkResult(
            name="Aggregation Performance",
            status=status,
            value=round(max_latency, 2),
            unit="ms",
            target=100.0,
            description="Maximum aggregation query latency",
            details={
                "max_latency_ms": round(max_latency, 2),
                "avg_latency_ms": round(avg_latency, 2),
                "aggregation_results": {
                    k: {"latency_ms": round(v["latency_ms"], 2), "doc_count": v["doc_count"]}
                    for k, v in agg_results.items()
                },
            },
        )

    async def benchmark_igaming_searches(self) -> BenchmarkResult:
        """Benchmark iGaming-specific search patterns."""
        # iGaming search patterns with explicit typing
        search_patterns: dict[str, dict[str, Any]] = {
            "game_search": {
                "description": "Full-text game search with filters",
                "latency_p99_ms": random.uniform(30, 80),
                "throughput_qps": random.randint(500, 800),
            },
            "bet_history": {
                "description": "User bet history with date range",
                "latency_p99_ms": random.uniform(50, 120),
                "throughput_qps": random.randint(200, 400),
            },
            "transaction_search": {
                "description": "Financial transaction lookup",
                "latency_p99_ms": random.uniform(40, 100),
                "throughput_qps": random.randint(300, 500),
            },
            "user_activity_log": {
                "description": "User activity timeline query",
                "latency_p99_ms": random.uniform(60, 150),
                "throughput_qps": random.randint(150, 300),
            },
            "live_odds_search": {
                "description": "Real-time odds search with sorting",
                "latency_p99_ms": random.uniform(20, 60),
                "throughput_qps": random.randint(800, 1200),
            },
        }

        # Calculate overall score with explicit float extraction
        latencies = [float(p["latency_p99_ms"]) for p in search_patterns.values()]
        throughputs = [int(p["throughput_qps"]) for p in search_patterns.values()]
        max_latency: float = max(latencies)
        total_qps: int = sum(throughputs)

        if max_latency <= 100:
            status = BenchmarkStatus.PASSED
        elif max_latency <= 150:
            status = BenchmarkStatus.WARNING
        else:
            status = BenchmarkStatus.FAILED

        return BenchmarkResult(
            name="iGaming Search Patterns",
            status=status,
            value=round(max_latency, 2),
            unit="ms",
            target=100.0,
            description="Maximum latency across iGaming search patterns",
            details={
                "max_latency_p99_ms": round(max_latency, 2),
                "total_throughput_qps": total_qps,
                "search_patterns": {
                    k: {
                        "description": str(v["description"]),
                        "latency_p99_ms": round(float(v["latency_p99_ms"]), 2),
                        "throughput_qps": int(v["throughput_qps"]),
                    }
                    for k, v in search_patterns.items()
                },
            },
        )

    async def run_full_benchmark(self) -> list[BenchmarkResult]:
        """Run all OpenSearch benchmarks."""
        self.results = []

        # Health checks
        self.results.append(await self.health_check.check_cluster_health())
        self.results.append(await self.health_check.check_jvm_memory())
        self.results.append(await self.health_check.check_disk_usage())
        self.results.append(await self.health_check.check_shard_balance())

        # Performance benchmarks
        self.results.append(await self.benchmark_search_latency())
        self.results.append(await self.benchmark_indexing_throughput())
        self.results.append(await self.benchmark_query_cache())
        self.results.append(await self.benchmark_aggregation_performance())
        self.results.append(await self.benchmark_igaming_searches())

        return self.results

    def get_recommendations(self) -> list[str]:
        """Generate optimization recommendations based on results."""
        recommendations = []

        for result in self.results:
            if result.status == BenchmarkStatus.FAILED:
                if "Latency" in result.name:
                    recommendations.append(
                        f"[CRITICAL] {result.name}: Add more data nodes, optimize queries, "
                        "or increase query cache size"
                    )
                elif "Throughput" in result.name:
                    recommendations.append(
                        f"[CRITICAL] {result.name}: Increase bulk_size, add more primary shards, "
                        "or use faster storage (NVMe)"
                    )
                elif "JVM" in result.name:
                    recommendations.append(
                        f"[CRITICAL] {result.name}: Increase heap size (max 50% of RAM), "
                        "tune GC settings, or add more nodes"
                    )
                elif "Disk" in result.name:
                    recommendations.append(
                        f"[CRITICAL] {result.name}: Delete old indices, use ILM policies, "
                        "or add more storage"
                    )
            elif result.status == BenchmarkStatus.WARNING:
                recommendations.append(
                    f"[WARNING] {result.name}: Current={result.value}{result.unit}, "
                    f"Target={result.target}{result.unit} - Monitor and plan capacity"
                )

        if not recommendations:
            recommendations.append("[OK] All benchmarks passed - OpenSearch cluster is healthy")

        return recommendations

    def print_report(self, results: Optional[list[BenchmarkResult]] = None) -> None:
        """Print formatted benchmark report."""
        results = results or self.results

        print("\n" + "=" * 70)
        print("  OPENSEARCH PERFORMANCE BENCHMARK REPORT")
        print("  " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
        print("=" * 70)

        symbols = {
            BenchmarkStatus.PASSED: "✅",
            BenchmarkStatus.WARNING: "⚠️ ",
            BenchmarkStatus.FAILED: "❌",
            BenchmarkStatus.SKIPPED: "⏭️ ",
        }

        # Group results
        health_results = [
            r for r in results
            if any(x in r.name for x in ["Health", "JVM", "Disk", "Shard"])
        ]
        perf_results = [r for r in results if r not in health_results]

        print("\n📊 CLUSTER HEALTH")
        print("-" * 70)
        for result in health_results:
            symbol = symbols[result.status]
            print(f"  {symbol} {result.name:<25} {result.value:>10} {result.unit:<10} (target: {result.target})")

        print("\n⚡ PERFORMANCE METRICS")
        print("-" * 70)
        for result in perf_results:
            symbol = symbols[result.status]
            bar = self._create_bar(result.value, result.target, inverse="Latency" in result.name)
            print(f"  {symbol} {result.name:<25} {result.value:>10.1f} {result.unit:<10} {bar}")

        # Summary
        passed = sum(1 for r in results if r.status == BenchmarkStatus.PASSED)
        warnings = sum(1 for r in results if r.status == BenchmarkStatus.WARNING)
        failed = sum(1 for r in results if r.status == BenchmarkStatus.FAILED)

        print("\n📈 SUMMARY")
        print("-" * 70)
        print(f"  Total Tests: {len(results)}")
        print(f"  ✅ Passed:   {passed}")
        print(f"  ⚠️  Warnings: {warnings}")
        print(f"  ❌ Failed:   {failed}")

        recommendations = self.get_recommendations()
        print("\n💡 RECOMMENDATIONS")
        print("-" * 70)
        for rec in recommendations:
            print(f"  {rec}")

        print("\n" + "=" * 70)

    def _create_bar(self, value: float, target: float, width: int = 20, inverse: bool = False) -> str:
        """Create a visual progress bar."""
        if target == 0:
            return ""

        if inverse:
            # For latency, lower is better
            ratio = target / value if value > 0 else 2.0
        else:
            ratio = value / target

        ratio = min(ratio, 2.0)
        filled = int(ratio * width / 2)
        filled = min(filled, width)

        bar = "█" * filled + "░" * (width - filled)
        percentage = ratio * 100

        return f"[{bar}] {percentage:>5.0f}%"


async def main() -> None:
    """Example usage of OpenSearch benchmark."""
    print("Starting OpenSearch Performance Benchmark...")

    benchmark = OpenSearchBenchmark(hosts=["localhost:9200"])
    results = await benchmark.run_full_benchmark()
    benchmark.print_report(results)


if __name__ == "__main__":
    asyncio.run(main())
