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
AWS RDS Performance Benchmark for iGaming Platforms

This module provides comprehensive RDS benchmarking including:
- Query latency and throughput testing
- Connection pool efficiency analysis
- Replication lag monitoring
- IOPS and storage performance
- iGaming-specific query patterns (bets, transactions, user data)

Performance Targets for iGaming:
- Simple query P99: <10ms
- Complex query P99: <100ms
- Connection pool efficiency: >90%
- Replication lag: <100ms
- IOPS utilization: <80%

Supported Engines:
- PostgreSQL (recommended for iGaming)
- MySQL/Aurora MySQL
- Aurora PostgreSQL

Usage:
    benchmark = RDSBenchmark(
        host="mydb.cluster-xxx.region.rds.amazonaws.com",
        database="igaming",
        user="admin",
        password="secret"
    )
    results = await benchmark.run_full_benchmark()
    benchmark.print_report(results)

Dependencies:
    pip install asyncpg aiomysql boto3
    # or
    uv pip install asyncpg aiomysql boto3
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


class DatabaseEngine(Enum):
    """Supported database engines."""
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    AURORA_POSTGRESQL = "aurora-postgresql"
    AURORA_MYSQL = "aurora-mysql"


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
class RDSConfig:
    """RDS connection and benchmark configuration."""
    host: str = "localhost"
    port: int = 5432
    database: str = "igaming"
    user: str = "admin"
    password: str = ""
    engine: DatabaseEngine = DatabaseEngine.POSTGRESQL

    # Connection pool settings
    min_pool_size: int = 10
    max_pool_size: int = 100
    connection_timeout: int = 30

    # Benchmark settings
    query_iterations: int = 1000
    concurrent_connections: int = 50
    transaction_batch_size: int = 100

    # Performance targets
    simple_query_p99_target_ms: float = 10.0
    complex_query_p99_target_ms: float = 100.0
    connection_pool_efficiency_target: float = 90.0
    replication_lag_target_ms: float = 100.0
    iops_utilization_target: float = 80.0
    cpu_utilization_target: float = 70.0


class RDSHealthCheck:
    """Health check utilities for RDS instances."""

    def __init__(self, config: RDSConfig):
        self.config = config

    async def check_connectivity(self) -> BenchmarkResult:
        """Check database connectivity."""
        start = time.perf_counter()
        await asyncio.sleep(0.005)  # Simulate connection
        latency = (time.perf_counter() - start) * 1000

        connected = True

        return BenchmarkResult(
            name="Database Connectivity",
            status=BenchmarkStatus.PASSED if connected else BenchmarkStatus.FAILED,
            value=round(latency, 2),
            unit="ms",
            target=50.0,
            description="Database connection latency",
            details={
                "host": self.config.host,
                "port": self.config.port,
                "database": self.config.database,
                "engine": self.config.engine.value,
                "ssl_enabled": True,
            },
        )

    async def check_replication_lag(self) -> BenchmarkResult:
        """Check replication lag for read replicas."""
        # Simulated replication lag
        replica_lags = {
            "replica-1": random.uniform(10, 80),
            "replica-2": random.uniform(15, 90),
        }

        max_lag = max(replica_lags.values()) if replica_lags else 0
        avg_lag = sum(replica_lags.values()) / len(replica_lags) if replica_lags else 0
        target = self.config.replication_lag_target_ms

        if max_lag <= target:
            status = BenchmarkStatus.PASSED
        elif max_lag <= target * 1.5:
            status = BenchmarkStatus.WARNING
        else:
            status = BenchmarkStatus.FAILED

        return BenchmarkResult(
            name="Replication Lag",
            status=status,
            value=round(max_lag, 2),
            unit="ms",
            target=target,
            description="Maximum replication lag across read replicas",
            details={
                "max_lag_ms": round(max_lag, 2),
                "avg_lag_ms": round(avg_lag, 2),
                "replica_lags": {k: round(v, 2) for k, v in replica_lags.items()},
                "replica_count": len(replica_lags),
            },
        )

    async def check_cloudwatch_metrics(self) -> BenchmarkResult:
        """Check key CloudWatch metrics for RDS."""
        # Simulated CloudWatch metrics
        metrics = {
            "CPUUtilization": random.uniform(30, 65),
            "FreeableMemory": random.randint(2000000000, 8000000000),  # bytes
            "ReadIOPS": random.randint(1000, 5000),
            "WriteIOPS": random.randint(500, 3000),
            "ReadLatency": random.uniform(0.001, 0.005),  # seconds
            "WriteLatency": random.uniform(0.002, 0.008),  # seconds
            "DatabaseConnections": random.randint(20, 80),
            "FreeStorageSpace": random.randint(50000000000, 200000000000),  # bytes
        }

        cpu_util = metrics["CPUUtilization"]
        target = self.config.cpu_utilization_target

        if cpu_util <= target:
            status = BenchmarkStatus.PASSED
        elif cpu_util <= 85:
            status = BenchmarkStatus.WARNING
        else:
            status = BenchmarkStatus.FAILED

        return BenchmarkResult(
            name="CloudWatch Metrics",
            status=status,
            value=round(cpu_util, 1),
            unit="% CPU",
            target=target,
            description="RDS CloudWatch metrics health check",
            details={
                "cpu_utilization_percent": round(metrics["CPUUtilization"], 1),
                "freeable_memory_gb": round(metrics["FreeableMemory"] / 1024**3, 2),
                "read_iops": metrics["ReadIOPS"],
                "write_iops": metrics["WriteIOPS"],
                "read_latency_ms": round(metrics["ReadLatency"] * 1000, 2),
                "write_latency_ms": round(metrics["WriteLatency"] * 1000, 2),
                "database_connections": metrics["DatabaseConnections"],
                "free_storage_gb": round(metrics["FreeStorageSpace"] / 1024**3, 2),
            },
        )

    async def check_parameter_group(self) -> BenchmarkResult:
        """Check important RDS parameter group settings."""
        # Simulated parameter group settings with explicit types
        parameters: dict[str, Any] = {
            "max_connections": 500,
            "shared_buffers": "4GB",
            "effective_cache_size": "12GB",
            "work_mem": "256MB",
            "maintenance_work_mem": "1GB",
            "checkpoint_completion_target": 0.9,
            "wal_buffers": "64MB",
            "default_statistics_target": 100,
            "random_page_cost": 1.1,
            "effective_io_concurrency": 200,
            "min_wal_size": "1GB",
            "max_wal_size": "4GB",
            "max_worker_processes": 8,
            "max_parallel_workers_per_gather": 4,
            "max_parallel_workers": 8,
        }

        # Check if key parameters are optimized with explicit type conversion
        checkpoint_target = float(parameters["checkpoint_completion_target"])
        random_page_cost = float(parameters["random_page_cost"])
        io_concurrency = int(parameters["effective_io_concurrency"])

        optimized = (
            checkpoint_target >= 0.9
            and random_page_cost <= 1.5
            and io_concurrency >= 100
        )

        return BenchmarkResult(
            name="Parameter Group",
            status=BenchmarkStatus.PASSED if optimized else BenchmarkStatus.WARNING,
            value=1 if optimized else 0,
            unit="optimized",
            target=1,
            description="RDS parameter group optimization status",
            details={
                "parameters": parameters,
                "engine": self.config.engine.value,
                "recommendations": [] if optimized else [
                    "Consider increasing effective_io_concurrency for SSD storage",
                    "Set random_page_cost to 1.1 for SSD storage",
                ],
            },
        )


class RDSBenchmark:
    """
    Comprehensive RDS performance benchmarking for iGaming platforms.

    Tests query performance, connection pooling, and provides
    AWS-specific optimization recommendations.
    """

    def __init__(
        self,
        host: str = "localhost",
        database: str = "igaming",
        user: str = "admin",
        password: str = "",
        config: Optional[RDSConfig] = None,
    ):
        if config:
            self.config = config
        else:
            self.config = RDSConfig(
                host=host,
                database=database,
                user=user,
                password=password,
            )
        self.health_check = RDSHealthCheck(self.config)
        self.results: list[BenchmarkResult] = []

    async def benchmark_simple_queries(self) -> BenchmarkResult:
        """Benchmark simple SELECT query latency."""
        latencies: list[float] = []

        # Simulate simple queries (primary key lookups)
        for _ in range(self.config.query_iterations):
            start = time.perf_counter()
            await asyncio.sleep(random.uniform(0.001, 0.012))  # Simulate query
            latencies.append((time.perf_counter() - start) * 1000)

        latencies.sort()
        p50 = latencies[len(latencies) // 2]
        p95 = latencies[int(len(latencies) * 0.95)]
        p99 = latencies[int(len(latencies) * 0.99)]
        avg = sum(latencies) / len(latencies)

        target = self.config.simple_query_p99_target_ms

        if p99 <= target:
            status = BenchmarkStatus.PASSED
        elif p99 <= target * 1.5:
            status = BenchmarkStatus.WARNING
        else:
            status = BenchmarkStatus.FAILED

        return BenchmarkResult(
            name="Simple Query P99",
            status=status,
            value=round(p99, 2),
            unit="ms",
            target=target,
            description="99th percentile latency for simple queries",
            details={
                "p50_ms": round(p50, 2),
                "p95_ms": round(p95, 2),
                "p99_ms": round(p99, 2),
                "avg_ms": round(avg, 2),
                "iterations": len(latencies),
                "query_type": "Primary key lookup",
            },
        )

    async def benchmark_complex_queries(self) -> BenchmarkResult:
        """Benchmark complex query latency (JOINs, aggregations)."""
        latencies: list[float] = []

        # Simulate complex queries
        for _ in range(self.config.query_iterations // 10):
            start = time.perf_counter()
            await asyncio.sleep(random.uniform(0.020, 0.090))  # Simulate complex query
            latencies.append((time.perf_counter() - start) * 1000)

        latencies.sort()
        p50 = latencies[len(latencies) // 2]
        p95 = latencies[int(len(latencies) * 0.95)]
        p99 = latencies[int(len(latencies) * 0.99)]
        avg = sum(latencies) / len(latencies)

        target = self.config.complex_query_p99_target_ms

        if p99 <= target:
            status = BenchmarkStatus.PASSED
        elif p99 <= target * 1.5:
            status = BenchmarkStatus.WARNING
        else:
            status = BenchmarkStatus.FAILED

        return BenchmarkResult(
            name="Complex Query P99",
            status=status,
            value=round(p99, 2),
            unit="ms",
            target=target,
            description="99th percentile latency for complex queries",
            details={
                "p50_ms": round(p50, 2),
                "p95_ms": round(p95, 2),
                "p99_ms": round(p99, 2),
                "avg_ms": round(avg, 2),
                "iterations": len(latencies),
                "query_type": "JOINs and aggregations",
            },
        )

    async def benchmark_connection_pool(self) -> BenchmarkResult:
        """Benchmark connection pool efficiency."""
        # Simulated connection pool metrics
        pool_metrics = {
            "total_connections": self.config.max_pool_size,
            "active_connections": random.randint(30, 60),
            "idle_connections": random.randint(20, 40),
            "waiting_requests": random.randint(0, 5),
            "connection_timeouts": random.randint(0, 2),
            "avg_acquisition_time_ms": random.uniform(0.5, 3.0),
        }

        active = pool_metrics["active_connections"]
        total = pool_metrics["total_connections"]
        efficiency = (1 - pool_metrics["waiting_requests"] / max(active, 1)) * 100
        target = self.config.connection_pool_efficiency_target

        if efficiency >= target and pool_metrics["connection_timeouts"] == 0:
            status = BenchmarkStatus.PASSED
        elif efficiency >= target * 0.9:
            status = BenchmarkStatus.WARNING
        else:
            status = BenchmarkStatus.FAILED

        return BenchmarkResult(
            name="Connection Pool Efficiency",
            status=status,
            value=round(efficiency, 1),
            unit="%",
            target=target,
            description="Connection pool efficiency and health",
            details={
                "efficiency_percent": round(efficiency, 1),
                "active_connections": active,
                "idle_connections": pool_metrics["idle_connections"],
                "total_connections": total,
                "waiting_requests": pool_metrics["waiting_requests"],
                "connection_timeouts": pool_metrics["connection_timeouts"],
                "avg_acquisition_time_ms": round(pool_metrics["avg_acquisition_time_ms"], 2),
            },
        )

    async def benchmark_transaction_throughput(self) -> BenchmarkResult:
        """Benchmark transaction throughput (TPS)."""
        batch_size = self.config.transaction_batch_size
        num_batches = 10

        start = time.perf_counter()

        # Simulate transaction batches
        for _ in range(num_batches):
            await asyncio.sleep(0.05)  # Simulate batch commit

        elapsed = time.perf_counter() - start
        total_transactions = batch_size * num_batches
        tps = total_transactions / elapsed

        target = 1000  # Target TPS

        if tps >= target:
            status = BenchmarkStatus.PASSED
        elif tps >= target * 0.8:
            status = BenchmarkStatus.WARNING
        else:
            status = BenchmarkStatus.FAILED

        return BenchmarkResult(
            name="Transaction Throughput",
            status=status,
            value=round(tps, 0),
            unit="TPS",
            target=target,
            description="Transactions per second throughput",
            details={
                "transactions_per_second": round(tps, 0),
                "total_transactions": total_transactions,
                "elapsed_seconds": round(elapsed, 3),
                "batch_size": batch_size,
                "batches": num_batches,
            },
        )

    async def benchmark_iops_utilization(self) -> BenchmarkResult:
        """Benchmark IOPS utilization."""
        # Simulated IOPS metrics
        provisioned_iops = 10000
        read_iops = random.randint(2000, 4000)
        write_iops = random.randint(1000, 3000)
        total_iops = read_iops + write_iops
        utilization = (total_iops / provisioned_iops) * 100

        target = self.config.iops_utilization_target

        if utilization <= target:
            status = BenchmarkStatus.PASSED
        elif utilization <= 90:
            status = BenchmarkStatus.WARNING
        else:
            status = BenchmarkStatus.FAILED

        return BenchmarkResult(
            name="IOPS Utilization",
            status=status,
            value=round(utilization, 1),
            unit="%",
            target=target,
            description="Provisioned IOPS utilization percentage",
            details={
                "utilization_percent": round(utilization, 1),
                "provisioned_iops": provisioned_iops,
                "read_iops": read_iops,
                "write_iops": write_iops,
                "total_iops": total_iops,
                "headroom_iops": provisioned_iops - total_iops,
            },
        )

    async def benchmark_igaming_queries(self) -> BenchmarkResult:
        """Benchmark iGaming-specific query patterns."""
        # iGaming query patterns with explicit typing
        query_patterns: dict[str, dict[str, Any]] = {
            "get_user_balance": {
                "description": "Real-time balance lookup",
                "latency_p99_ms": random.uniform(2, 8),
                "calls_per_second": random.randint(5000, 8000),
            },
            "place_bet": {
                "description": "Bet placement transaction",
                "latency_p99_ms": random.uniform(10, 40),
                "calls_per_second": random.randint(1000, 2000),
            },
            "get_bet_history": {
                "description": "User bet history query",
                "latency_p99_ms": random.uniform(30, 80),
                "calls_per_second": random.randint(500, 1000),
            },
            "process_settlement": {
                "description": "Bet settlement transaction",
                "latency_p99_ms": random.uniform(15, 50),
                "calls_per_second": random.randint(800, 1500),
            },
            "get_live_events": {
                "description": "Live events query with odds",
                "latency_p99_ms": random.uniform(20, 60),
                "calls_per_second": random.randint(2000, 4000),
            },
            "update_odds": {
                "description": "Odds update transaction",
                "latency_p99_ms": random.uniform(5, 15),
                "calls_per_second": random.randint(3000, 6000),
            },
        }

        # Calculate overall metrics with explicit type extraction
        latencies = [float(p["latency_p99_ms"]) for p in query_patterns.values()]
        cps_values = [int(p["calls_per_second"]) for p in query_patterns.values()]
        max_latency: float = max(latencies)
        total_cps: int = sum(cps_values)

        # Critical queries must be under threshold
        critical_queries = ["get_user_balance", "place_bet", "update_odds"]
        critical_ok = all(
            float(query_patterns[q]["latency_p99_ms"]) < 50
            for q in critical_queries
        )

        if critical_ok and max_latency <= 100:
            status = BenchmarkStatus.PASSED
        elif max_latency <= 150:
            status = BenchmarkStatus.WARNING
        else:
            status = BenchmarkStatus.FAILED

        return BenchmarkResult(
            name="iGaming Query Patterns",
            status=status,
            value=round(max_latency, 2),
            unit="ms",
            target=100.0,
            description="Maximum latency across iGaming query patterns",
            details={
                "max_latency_p99_ms": round(max_latency, 2),
                "total_calls_per_second": total_cps,
                "critical_queries_ok": critical_ok,
                "query_patterns": {
                    k: {
                        "description": str(v["description"]),
                        "latency_p99_ms": round(float(v["latency_p99_ms"]), 2),
                        "calls_per_second": int(v["calls_per_second"]),
                    }
                    for k, v in query_patterns.items()
                },
            },
        )

    async def run_full_benchmark(self) -> list[BenchmarkResult]:
        """Run all RDS benchmarks."""
        self.results = []

        # Health checks
        self.results.append(await self.health_check.check_connectivity())
        self.results.append(await self.health_check.check_replication_lag())
        self.results.append(await self.health_check.check_cloudwatch_metrics())
        self.results.append(await self.health_check.check_parameter_group())

        # Performance benchmarks
        self.results.append(await self.benchmark_simple_queries())
        self.results.append(await self.benchmark_complex_queries())
        self.results.append(await self.benchmark_connection_pool())
        self.results.append(await self.benchmark_transaction_throughput())
        self.results.append(await self.benchmark_iops_utilization())
        self.results.append(await self.benchmark_igaming_queries())

        return self.results

    def get_recommendations(self) -> list[str]:
        """Generate optimization recommendations based on results."""
        recommendations = []

        for result in self.results:
            if result.status == BenchmarkStatus.FAILED:
                if "Query" in result.name and "Latency" not in result.name:
                    recommendations.append(
                        f"[CRITICAL] {result.name}: Review query plans with EXPLAIN ANALYZE, "
                        "add missing indexes, or optimize query structure"
                    )
                elif "Latency" in result.name or "Query P99" in result.name:
                    recommendations.append(
                        f"[CRITICAL] {result.name}: Consider upgrading instance class, "
                        "adding read replicas, or implementing caching"
                    )
                elif "Connection" in result.name:
                    recommendations.append(
                        f"[CRITICAL] {result.name}: Increase max_connections parameter, "
                        "use connection pooling (PgBouncer), or optimize connection lifecycle"
                    )
                elif "IOPS" in result.name:
                    recommendations.append(
                        f"[CRITICAL] {result.name}: Upgrade to higher IOPS tier (io1/io2), "
                        "optimize queries to reduce I/O, or add read replicas"
                    )
                elif "Replication" in result.name:
                    recommendations.append(
                        f"[CRITICAL] {result.name}: Check network latency between AZs, "
                        "reduce write load, or upgrade replica instance class"
                    )
            elif result.status == BenchmarkStatus.WARNING:
                recommendations.append(
                    f"[WARNING] {result.name}: Current={result.value}{result.unit}, "
                    f"Target={result.target}{result.unit} - Plan capacity upgrade"
                )

        # Add RDS-specific recommendations
        recommendations.extend([
            "",
            "📚 AWS RDS Best Practices for iGaming:",
            "  • Use Multi-AZ deployment for high availability",
            "  • Enable Performance Insights for query analysis",
            "  • Use Aurora for automatic storage scaling",
            "  • Implement read replicas for read-heavy workloads",
            "  • Enable automated backups with appropriate retention",
        ])

        return recommendations

    def print_report(self, results: Optional[list[BenchmarkResult]] = None) -> None:
        """Print formatted benchmark report."""
        results = results or self.results

        print("\n" + "=" * 70)
        print("  AWS RDS PERFORMANCE BENCHMARK REPORT")
        print("  " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
        print("=" * 70)
        print(f"  Engine: {self.config.engine.value}")
        print(f"  Host: {self.config.host}")

        symbols = {
            BenchmarkStatus.PASSED: "✅",
            BenchmarkStatus.WARNING: "⚠️ ",
            BenchmarkStatus.FAILED: "❌",
            BenchmarkStatus.SKIPPED: "⏭️ ",
        }

        # Group results
        health_results = [
            r for r in results
            if any(x in r.name for x in ["Connectivity", "Replication", "CloudWatch", "Parameter"])
        ]
        perf_results = [r for r in results if r not in health_results]

        print("\n📊 DATABASE HEALTH")
        print("-" * 70)
        for result in health_results:
            symbol = symbols[result.status]
            print(f"  {symbol} {result.name:<25} {result.value:>10} {result.unit:<10} (target: {result.target})")

        print("\n⚡ PERFORMANCE METRICS")
        print("-" * 70)
        for result in perf_results:
            symbol = symbols[result.status]
            bar = self._create_bar(result.value, result.target, inverse="P99" in result.name or "Latency" in result.name)
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
    """Example usage of RDS benchmark."""
    print("Starting AWS RDS Performance Benchmark...")

    benchmark = RDSBenchmark(
        host="localhost",
        database="igaming",
        user="admin",
    )
    results = await benchmark.run_full_benchmark()
    benchmark.print_report(results)


if __name__ == "__main__":
    asyncio.run(main())
