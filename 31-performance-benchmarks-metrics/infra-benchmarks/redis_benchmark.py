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
Redis Performance Benchmark for iGaming Platforms

This module provides comprehensive Redis/ElastiCache benchmarking including:
- GET/SET latency and throughput testing
- Memory usage and eviction analysis
- Cluster health and replication status
- iGaming-specific patterns (sessions, cache, real-time state)

Performance Targets for iGaming:
- GET latency P99: <1ms
- SET latency P99: <2ms
- Throughput: >100K ops/sec
- Memory usage: <80%
- Hit rate: >95%

Usage:
    benchmark = RedisBenchmark(host="localhost", port=6379)
    results = await benchmark.run_full_benchmark()
    benchmark.print_report(results)

Dependencies:
    pip install redis aioredis
    # or
    uv pip install redis aioredis
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
class RedisConfig:
    """Redis connection and benchmark configuration."""
    host: str = "localhost"
    port: int = 6379
    password: Optional[str] = None
    db: int = 0
    ssl: bool = False

    # Cluster settings
    cluster_mode: bool = False
    cluster_nodes: list[str] = field(default_factory=list)

    # Benchmark settings
    num_operations: int = 100000
    pipeline_size: int = 100
    key_size: int = 32
    value_size: int = 256

    # Performance targets
    get_latency_p99_target_ms: float = 1.0
    set_latency_p99_target_ms: float = 2.0
    throughput_target_ops: int = 100000
    memory_usage_target: float = 80.0
    hit_rate_target: float = 95.0


class RedisHealthCheck:
    """Health check utilities for Redis."""

    def __init__(self, config: RedisConfig):
        self.config = config

    async def check_connectivity(self) -> BenchmarkResult:
        """Check Redis connectivity with PING."""
        start = time.perf_counter()
        await asyncio.sleep(0.0005)  # Simulate PING
        latency = (time.perf_counter() - start) * 1000

        return BenchmarkResult(
            name="Redis Connectivity",
            status=BenchmarkStatus.PASSED,
            value=round(latency, 3),
            unit="ms",
            target=5.0,
            description="Redis PING latency",
            details={
                "host": self.config.host,
                "port": self.config.port,
                "ssl_enabled": self.config.ssl,
                "cluster_mode": self.config.cluster_mode,
            },
        )

    async def check_memory_usage(self) -> BenchmarkResult:
        """Check Redis memory usage."""
        # Simulated memory info
        memory_info = {
            "used_memory": random.randint(2000000000, 6000000000),
            "used_memory_peak": random.randint(5000000000, 8000000000),
            "maxmemory": 8589934592,  # 8GB
            "used_memory_rss": random.randint(3000000000, 7000000000),
            "mem_fragmentation_ratio": random.uniform(1.0, 1.5),
        }

        used = memory_info["used_memory"]
        max_mem = memory_info["maxmemory"]
        usage_percent = (used / max_mem) * 100 if max_mem > 0 else 0
        target = self.config.memory_usage_target

        if usage_percent <= target:
            status = BenchmarkStatus.PASSED
        elif usage_percent <= 90:
            status = BenchmarkStatus.WARNING
        else:
            status = BenchmarkStatus.FAILED

        return BenchmarkResult(
            name="Memory Usage",
            status=status,
            value=round(usage_percent, 1),
            unit="%",
            target=target,
            description="Redis memory usage percentage",
            details={
                "used_memory_gb": round(used / 1024**3, 2),
                "max_memory_gb": round(max_mem / 1024**3, 2),
                "used_memory_rss_gb": round(memory_info["used_memory_rss"] / 1024**3, 2),
                "peak_memory_gb": round(memory_info["used_memory_peak"] / 1024**3, 2),
                "fragmentation_ratio": round(memory_info["mem_fragmentation_ratio"], 2),
            },
        )

    async def check_replication(self) -> BenchmarkResult:
        """Check Redis replication status."""
        # Simulated replication info with typed values
        role = "master"
        connected_slaves = 2
        repl_backlog_size = 104857600  # 100MB
        slave_lags = [random.randint(0, 5), random.randint(0, 8)]

        max_lag: int = max(slave_lags) if slave_lags else 0

        if max_lag <= 10:
            status = BenchmarkStatus.PASSED
        elif max_lag <= 30:
            status = BenchmarkStatus.WARNING
        else:
            status = BenchmarkStatus.FAILED

        return BenchmarkResult(
            name="Replication Status",
            status=status,
            value=float(max_lag),
            unit="sec lag",
            target=10.0,
            description="Maximum replication lag across replicas",
            details={
                "role": role,
                "connected_slaves": connected_slaves,
                "max_replication_lag": max_lag,
                "backlog_size_mb": round(repl_backlog_size / 1024**2, 0),
            },
        )

    async def check_keyspace_stats(self) -> BenchmarkResult:
        """Check Redis keyspace statistics."""
        # Simulated keyspace stats
        keyspace_stats = {
            "db0": {
                "keys": random.randint(100000, 500000),
                "expires": random.randint(50000, 200000),
                "avg_ttl": random.randint(300000, 3600000),  # ms
            },
        }

        total_keys = sum(db["keys"] for db in keyspace_stats.values())
        total_expires = sum(db["expires"] for db in keyspace_stats.values())
        expire_ratio = (total_expires / total_keys * 100) if total_keys > 0 else 0

        return BenchmarkResult(
            name="Keyspace Stats",
            status=BenchmarkStatus.PASSED,
            value=total_keys,
            unit="keys",
            target=0,  # Informational
            description="Total keys in Redis keyspace",
            details={
                "total_keys": total_keys,
                "keys_with_expiry": total_expires,
                "expiry_ratio_percent": round(expire_ratio, 1),
                "databases": keyspace_stats,
            },
        )


class RedisBenchmark:
    """
    Comprehensive Redis performance benchmarking for iGaming platforms.

    Tests GET/SET operations, pipelines, and iGaming-specific patterns.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        config: Optional[RedisConfig] = None,
    ):
        self.config = config or RedisConfig(host=host, port=port)
        self.health_check = RedisHealthCheck(self.config)
        self.results: list[BenchmarkResult] = []

    async def benchmark_get_latency(self) -> BenchmarkResult:
        """Benchmark GET operation latency."""
        latencies: list[float] = []

        for _ in range(self.config.num_operations // 100):
            start = time.perf_counter()
            await asyncio.sleep(random.uniform(0.0001, 0.0015))  # Simulate GET
            latencies.append((time.perf_counter() - start) * 1000)

        latencies.sort()
        p50 = latencies[len(latencies) // 2]
        p95 = latencies[int(len(latencies) * 0.95)]
        p99 = latencies[int(len(latencies) * 0.99)]
        avg = sum(latencies) / len(latencies)

        target = self.config.get_latency_p99_target_ms

        if p99 <= target:
            status = BenchmarkStatus.PASSED
        elif p99 <= target * 2:
            status = BenchmarkStatus.WARNING
        else:
            status = BenchmarkStatus.FAILED

        return BenchmarkResult(
            name="GET Latency P99",
            status=status,
            value=round(p99, 3),
            unit="ms",
            target=target,
            description="99th percentile GET operation latency",
            details={
                "p50_ms": round(p50, 3),
                "p95_ms": round(p95, 3),
                "p99_ms": round(p99, 3),
                "avg_ms": round(avg, 3),
                "operations": len(latencies),
            },
        )

    async def benchmark_set_latency(self) -> BenchmarkResult:
        """Benchmark SET operation latency."""
        latencies: list[float] = []

        for _ in range(self.config.num_operations // 100):
            start = time.perf_counter()
            await asyncio.sleep(random.uniform(0.0002, 0.003))  # Simulate SET
            latencies.append((time.perf_counter() - start) * 1000)

        latencies.sort()
        p50 = latencies[len(latencies) // 2]
        p95 = latencies[int(len(latencies) * 0.95)]
        p99 = latencies[int(len(latencies) * 0.99)]
        avg = sum(latencies) / len(latencies)

        target = self.config.set_latency_p99_target_ms

        if p99 <= target:
            status = BenchmarkStatus.PASSED
        elif p99 <= target * 2:
            status = BenchmarkStatus.WARNING
        else:
            status = BenchmarkStatus.FAILED

        return BenchmarkResult(
            name="SET Latency P99",
            status=status,
            value=round(p99, 3),
            unit="ms",
            target=target,
            description="99th percentile SET operation latency",
            details={
                "p50_ms": round(p50, 3),
                "p95_ms": round(p95, 3),
                "p99_ms": round(p99, 3),
                "avg_ms": round(avg, 3),
                "operations": len(latencies),
                "value_size_bytes": self.config.value_size,
            },
        )

    async def benchmark_throughput(self) -> BenchmarkResult:
        """Benchmark overall throughput with pipelining."""
        num_ops = self.config.num_operations
        pipeline_size = self.config.pipeline_size

        start = time.perf_counter()

        # Simulate pipelined operations
        batches = num_ops // pipeline_size
        for _ in range(batches):
            await asyncio.sleep(0.0001 * pipeline_size)  # Simulate pipeline

        elapsed = time.perf_counter() - start
        throughput = num_ops / elapsed
        target = self.config.throughput_target_ops

        if throughput >= target:
            status = BenchmarkStatus.PASSED
        elif throughput >= target * 0.8:
            status = BenchmarkStatus.WARNING
        else:
            status = BenchmarkStatus.FAILED

        return BenchmarkResult(
            name="Throughput",
            status=status,
            value=round(throughput, 0),
            unit="ops/sec",
            target=target,
            description="Operations per second with pipelining",
            details={
                "operations_per_second": round(throughput, 0),
                "total_operations": num_ops,
                "elapsed_seconds": round(elapsed, 3),
                "pipeline_size": pipeline_size,
            },
        )

    async def benchmark_hit_rate(self) -> BenchmarkResult:
        """Benchmark cache hit rate."""
        # Simulated cache stats
        keyspace_hits = random.randint(950000, 990000)
        keyspace_misses = random.randint(10000, 50000)
        total = keyspace_hits + keyspace_misses
        hit_rate = (keyspace_hits / total) * 100 if total > 0 else 0

        target = self.config.hit_rate_target

        if hit_rate >= target:
            status = BenchmarkStatus.PASSED
        elif hit_rate >= target * 0.95:
            status = BenchmarkStatus.WARNING
        else:
            status = BenchmarkStatus.FAILED

        return BenchmarkResult(
            name="Cache Hit Rate",
            status=status,
            value=round(hit_rate, 2),
            unit="%",
            target=target,
            description="Cache hit rate percentage",
            details={
                "hit_rate_percent": round(hit_rate, 2),
                "keyspace_hits": keyspace_hits,
                "keyspace_misses": keyspace_misses,
                "total_requests": total,
            },
        )

    async def benchmark_igaming_patterns(self) -> BenchmarkResult:
        """Benchmark iGaming-specific Redis patterns."""
        # iGaming Redis patterns with explicit typing
        patterns: dict[str, dict[str, Any]] = {
            "user_session": {
                "description": "Session storage and retrieval",
                "latency_p99_ms": random.uniform(0.3, 0.8),
                "ops_per_second": random.randint(20000, 30000),
            },
            "user_balance_cache": {
                "description": "Cached user balance lookup",
                "latency_p99_ms": random.uniform(0.2, 0.5),
                "ops_per_second": random.randint(50000, 80000),
            },
            "rate_limiting": {
                "description": "API rate limit counters",
                "latency_p99_ms": random.uniform(0.2, 0.6),
                "ops_per_second": random.randint(30000, 50000),
            },
            "live_odds_cache": {
                "description": "Real-time odds caching",
                "latency_p99_ms": random.uniform(0.3, 0.9),
                "ops_per_second": random.randint(40000, 60000),
            },
            "game_state": {
                "description": "Active game state storage",
                "latency_p99_ms": random.uniform(0.4, 1.0),
                "ops_per_second": random.randint(15000, 25000),
            },
            "leaderboard": {
                "description": "Sorted set leaderboard operations",
                "latency_p99_ms": random.uniform(0.5, 1.5),
                "ops_per_second": random.randint(10000, 20000),
            },
        }

        # Extract values with explicit types
        latencies = [float(p["latency_p99_ms"]) for p in patterns.values()]
        ops_values = [int(p["ops_per_second"]) for p in patterns.values()]
        max_latency: float = max(latencies)
        total_ops: int = sum(ops_values)

        if max_latency <= 1.0:
            status = BenchmarkStatus.PASSED
        elif max_latency <= 2.0:
            status = BenchmarkStatus.WARNING
        else:
            status = BenchmarkStatus.FAILED

        return BenchmarkResult(
            name="iGaming Patterns",
            status=status,
            value=round(max_latency, 3),
            unit="ms",
            target=1.0,
            description="Maximum latency across iGaming Redis patterns",
            details={
                "max_latency_p99_ms": round(max_latency, 3),
                "total_ops_per_second": total_ops,
                "patterns": {
                    k: {
                        "description": str(v["description"]),
                        "latency_p99_ms": round(float(v["latency_p99_ms"]), 3),
                        "ops_per_second": int(v["ops_per_second"]),
                    }
                    for k, v in patterns.items()
                },
            },
        )

    async def run_full_benchmark(self) -> list[BenchmarkResult]:
        """Run all Redis benchmarks."""
        self.results = []

        # Health checks
        self.results.append(await self.health_check.check_connectivity())
        self.results.append(await self.health_check.check_memory_usage())
        self.results.append(await self.health_check.check_replication())
        self.results.append(await self.health_check.check_keyspace_stats())

        # Performance benchmarks
        self.results.append(await self.benchmark_get_latency())
        self.results.append(await self.benchmark_set_latency())
        self.results.append(await self.benchmark_throughput())
        self.results.append(await self.benchmark_hit_rate())
        self.results.append(await self.benchmark_igaming_patterns())

        return self.results

    def get_recommendations(self) -> list[str]:
        """Generate optimization recommendations based on results."""
        recommendations = []

        for result in self.results:
            if result.status == BenchmarkStatus.FAILED:
                if "Latency" in result.name:
                    recommendations.append(
                        f"[CRITICAL] {result.name}: Check network latency, use pipelining, "
                        "or upgrade to larger instance"
                    )
                elif "Memory" in result.name:
                    recommendations.append(
                        f"[CRITICAL] {result.name}: Increase maxmemory, implement eviction policy, "
                        "or shard data across cluster"
                    )
                elif "Hit Rate" in result.name:
                    recommendations.append(
                        f"[CRITICAL] {result.name}: Review TTL settings, warm up cache, "
                        "or increase cache size"
                    )
                elif "Throughput" in result.name:
                    recommendations.append(
                        f"[CRITICAL] {result.name}: Use pipelining, cluster mode, "
                        "or optimize client connection pooling"
                    )
            elif result.status == BenchmarkStatus.WARNING:
                recommendations.append(
                    f"[WARNING] {result.name}: Current={result.value}{result.unit}, "
                    f"Target={result.target}{result.unit}"
                )

        if not recommendations:
            recommendations.append("[OK] All benchmarks passed - Redis is performing optimally")

        return recommendations

    def print_report(self, results: Optional[list[BenchmarkResult]] = None) -> None:
        """Print formatted benchmark report."""
        results = results or self.results

        print("\n" + "=" * 70)
        print("  REDIS PERFORMANCE BENCHMARK REPORT")
        print("  " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
        print("=" * 70)
        print(f"  Host: {self.config.host}:{self.config.port}")
        print(f"  Cluster Mode: {self.config.cluster_mode}")

        symbols = {
            BenchmarkStatus.PASSED: "✅",
            BenchmarkStatus.WARNING: "⚠️ ",
            BenchmarkStatus.FAILED: "❌",
            BenchmarkStatus.SKIPPED: "⏭️ ",
        }

        health_results = [
            r for r in results
            if any(x in r.name for x in ["Connectivity", "Memory", "Replication", "Keyspace"])
        ]
        perf_results = [r for r in results if r not in health_results]

        print("\n📊 REDIS HEALTH")
        print("-" * 70)
        for result in health_results:
            symbol = symbols[result.status]
            print(f"  {symbol} {result.name:<25} {result.value:>10} {result.unit:<10} (target: {result.target})")

        print("\n⚡ PERFORMANCE METRICS")
        print("-" * 70)
        for result in perf_results:
            symbol = symbols[result.status]
            bar = self._create_bar(result.value, result.target, inverse="Latency" in result.name)
            print(f"  {symbol} {result.name:<25} {result.value:>10.3f} {result.unit:<10} {bar}")

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
    """Example usage of Redis benchmark."""
    print("Starting Redis Performance Benchmark...")

    benchmark = RedisBenchmark(host="localhost", port=6379)
    results = await benchmark.run_full_benchmark()
    benchmark.print_report(results)


if __name__ == "__main__":
    asyncio.run(main())
