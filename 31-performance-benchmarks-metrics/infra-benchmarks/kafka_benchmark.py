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
Kafka Performance Benchmark for iGaming Platforms

This module provides comprehensive Kafka benchmarking capabilities including:
- Producer throughput and latency testing
- Consumer lag monitoring
- Partition balance analysis
- Broker health checks
- iGaming-specific workload simulation (bets, events, transactions)

Performance Targets for iGaming:
- Producer latency P99: <10ms
- Consumer lag: <1000 messages
- Throughput: >100K messages/sec per broker
- Replication lag: <100ms

Usage:
    benchmark = KafkaBenchmark(bootstrap_servers="localhost:9092")
    results = await benchmark.run_full_benchmark()
    benchmark.print_report(results)

Dependencies:
    pip install aiokafka confluent-kafka
    # or
    uv pip install aiokafka confluent-kafka
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
class KafkaConfig:
    """Kafka connection and benchmark configuration."""
    bootstrap_servers: str = "localhost:9092"
    security_protocol: str = "PLAINTEXT"
    sasl_mechanism: Optional[str] = None
    sasl_username: Optional[str] = None
    sasl_password: Optional[str] = None

    # Benchmark settings
    test_topic: str = "benchmark-test"
    num_partitions: int = 12
    replication_factor: int = 3
    message_size_bytes: int = 1024
    num_messages: int = 100000
    batch_size: int = 16384
    linger_ms: int = 5

    # iGaming-specific targets
    producer_latency_p99_target_ms: float = 10.0
    consumer_lag_target: int = 1000
    throughput_target_per_broker: int = 100000
    replication_lag_target_ms: float = 100.0


class KafkaHealthCheck:
    """Health check utilities for Kafka cluster."""

    def __init__(self, config: KafkaConfig):
        self.config = config

    async def check_broker_connectivity(self) -> BenchmarkResult:
        """Check connectivity to all Kafka brokers."""
        # Simulated check - in production, use AdminClient
        start = time.perf_counter()
        await asyncio.sleep(0.01)  # Simulate network call
        latency = (time.perf_counter() - start) * 1000

        brokers_available = 3  # Simulated
        total_brokers = 3

        status = BenchmarkStatus.PASSED if brokers_available == total_brokers else BenchmarkStatus.FAILED

        return BenchmarkResult(
            name="Broker Connectivity",
            status=status,
            value=brokers_available,
            unit="brokers",
            target=total_brokers,
            description="Number of reachable Kafka brokers",
            details={
                "latency_ms": round(latency, 2),
                "brokers_available": brokers_available,
                "total_brokers": total_brokers,
                "bootstrap_servers": self.config.bootstrap_servers,
            },
        )

    async def check_cluster_health(self) -> BenchmarkResult:
        """Check overall Kafka cluster health."""
        # Simulated metrics
        under_replicated_partitions = 0
        offline_partitions = 0
        controller_count = 1

        is_healthy = (
            under_replicated_partitions == 0
            and offline_partitions == 0
            and controller_count == 1
        )

        return BenchmarkResult(
            name="Cluster Health",
            status=BenchmarkStatus.PASSED if is_healthy else BenchmarkStatus.FAILED,
            value=1 if is_healthy else 0,
            unit="healthy",
            target=1,
            description="Overall Kafka cluster health status",
            details={
                "under_replicated_partitions": under_replicated_partitions,
                "offline_partitions": offline_partitions,
                "controller_count": controller_count,
                "active_controller_id": 1,
            },
        )

    async def check_topic_configuration(self, topic: str) -> BenchmarkResult:
        """Check topic configuration and health."""
        # Simulated topic config
        partition_count = self.config.num_partitions
        replication_factor = self.config.replication_factor
        in_sync_replicas = replication_factor

        is_healthy = in_sync_replicas == replication_factor

        return BenchmarkResult(
            name=f"Topic Config: {topic}",
            status=BenchmarkStatus.PASSED if is_healthy else BenchmarkStatus.WARNING,
            value=in_sync_replicas,
            unit="ISR",
            target=replication_factor,
            description="In-sync replica count for topic partitions",
            details={
                "topic": topic,
                "partitions": partition_count,
                "replication_factor": replication_factor,
                "min_isr": 2,
                "retention_ms": 604800000,  # 7 days
            },
        )


class KafkaBenchmark:
    """
    Comprehensive Kafka performance benchmarking for iGaming platforms.

    Tests producer/consumer performance, measures latencies, and provides
    actionable recommendations based on iGaming workload requirements.
    """

    def __init__(
        self,
        bootstrap_servers: str = "localhost:9092",
        config: Optional[KafkaConfig] = None,
    ):
        self.config = config or KafkaConfig(bootstrap_servers=bootstrap_servers)
        self.health_check = KafkaHealthCheck(self.config)
        self.results: list[BenchmarkResult] = []

    async def benchmark_producer_throughput(self) -> BenchmarkResult:
        """Benchmark producer throughput (messages per second)."""
        num_messages = self.config.num_messages
        message_size = self.config.message_size_bytes

        # Simulate production - in real implementation use aiokafka
        start = time.perf_counter()

        # Simulated batch sending
        batches = num_messages // 1000
        for _ in range(batches):
            await asyncio.sleep(0.001)  # Simulate batch send

        elapsed = time.perf_counter() - start
        throughput = num_messages / elapsed

        target = self.config.throughput_target_per_broker

        if throughput >= target:
            status = BenchmarkStatus.PASSED
        elif throughput >= target * 0.8:
            status = BenchmarkStatus.WARNING
        else:
            status = BenchmarkStatus.FAILED

        return BenchmarkResult(
            name="Producer Throughput",
            status=status,
            value=round(throughput, 0),
            unit="msg/sec",
            target=target,
            description="Messages produced per second",
            details={
                "messages_sent": num_messages,
                "message_size_bytes": message_size,
                "elapsed_seconds": round(elapsed, 3),
                "mb_per_sec": round((num_messages * message_size) / elapsed / 1024 / 1024, 2),
                "batch_size": self.config.batch_size,
                "linger_ms": self.config.linger_ms,
            },
        )

    async def benchmark_producer_latency(self) -> BenchmarkResult:
        """Benchmark producer latency percentiles."""
        latencies: list[float] = []

        # Simulate 1000 individual message sends
        for _ in range(1000):
            start = time.perf_counter()
            await asyncio.sleep(random.uniform(0.001, 0.015))  # Simulate send
            latencies.append((time.perf_counter() - start) * 1000)

        latencies.sort()
        p50 = latencies[500]
        p95 = latencies[950]
        p99 = latencies[990]
        avg = sum(latencies) / len(latencies)

        target = self.config.producer_latency_p99_target_ms

        if p99 <= target:
            status = BenchmarkStatus.PASSED
        elif p99 <= target * 1.5:
            status = BenchmarkStatus.WARNING
        else:
            status = BenchmarkStatus.FAILED

        return BenchmarkResult(
            name="Producer Latency P99",
            status=status,
            value=round(p99, 2),
            unit="ms",
            target=target,
            description="99th percentile producer latency",
            details={
                "p50_ms": round(p50, 2),
                "p95_ms": round(p95, 2),
                "p99_ms": round(p99, 2),
                "avg_ms": round(avg, 2),
                "min_ms": round(min(latencies), 2),
                "max_ms": round(max(latencies), 2),
                "samples": len(latencies),
            },
        )

    async def benchmark_consumer_lag(self) -> BenchmarkResult:
        """Benchmark consumer group lag."""
        # Simulated consumer lag per partition
        partition_lags = {
            f"partition-{i}": random.randint(0, 500)
            for i in range(self.config.num_partitions)
        }

        total_lag = sum(partition_lags.values())
        max_lag = max(partition_lags.values())
        target = self.config.consumer_lag_target

        if total_lag <= target:
            status = BenchmarkStatus.PASSED
        elif total_lag <= target * 2:
            status = BenchmarkStatus.WARNING
        else:
            status = BenchmarkStatus.FAILED

        return BenchmarkResult(
            name="Consumer Lag",
            status=status,
            value=total_lag,
            unit="messages",
            target=target,
            description="Total consumer group lag across all partitions",
            details={
                "total_lag": total_lag,
                "max_partition_lag": max_lag,
                "partition_count": self.config.num_partitions,
                "partition_lags": partition_lags,
                "consumer_group": "igaming-events-consumer",
            },
        )

    async def benchmark_replication_lag(self) -> BenchmarkResult:
        """Benchmark ISR replication lag."""
        # Simulated replication lag per broker
        broker_lags = {
            "broker-1": random.uniform(10, 50),
            "broker-2": random.uniform(10, 50),
            "broker-3": random.uniform(10, 50),
        }

        max_lag = max(broker_lags.values())
        avg_lag = sum(broker_lags.values()) / len(broker_lags)
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
            description="Maximum ISR replication lag",
            details={
                "max_lag_ms": round(max_lag, 2),
                "avg_lag_ms": round(avg_lag, 2),
                "broker_lags": {k: round(v, 2) for k, v in broker_lags.items()},
                "replication_factor": self.config.replication_factor,
            },
        )

    async def benchmark_partition_balance(self) -> BenchmarkResult:
        """Check partition leader balance across brokers."""
        # Simulated partition distribution
        partitions_per_broker = {
            "broker-1": 4,
            "broker-2": 4,
            "broker-3": 4,
        }

        total_partitions = sum(partitions_per_broker.values())
        ideal_per_broker = total_partitions / len(partitions_per_broker)
        max_deviation = max(
            abs(count - ideal_per_broker) for count in partitions_per_broker.values()
        )
        balance_score = 100 - (max_deviation / ideal_per_broker * 100)

        if balance_score >= 95:
            status = BenchmarkStatus.PASSED
        elif balance_score >= 80:
            status = BenchmarkStatus.WARNING
        else:
            status = BenchmarkStatus.FAILED

        return BenchmarkResult(
            name="Partition Balance",
            status=status,
            value=round(balance_score, 1),
            unit="%",
            target=95.0,
            description="Partition leader balance across brokers",
            details={
                "partitions_per_broker": partitions_per_broker,
                "total_partitions": total_partitions,
                "ideal_per_broker": ideal_per_broker,
                "max_deviation": max_deviation,
            },
        )

    async def benchmark_igaming_workload(self) -> BenchmarkResult:
        """Simulate iGaming-specific workload patterns."""
        # iGaming message types and their throughput requirements
        workload_results = {
            "bet_events": {
                "target_throughput": 10000,
                "achieved_throughput": random.randint(9500, 11000),
                "p99_latency_ms": random.uniform(5, 12),
            },
            "game_events": {
                "target_throughput": 50000,
                "achieved_throughput": random.randint(48000, 55000),
                "p99_latency_ms": random.uniform(3, 8),
            },
            "user_activity": {
                "target_throughput": 20000,
                "achieved_throughput": random.randint(19000, 22000),
                "p99_latency_ms": random.uniform(4, 10),
            },
            "transaction_events": {
                "target_throughput": 5000,
                "achieved_throughput": random.randint(4800, 5500),
                "p99_latency_ms": random.uniform(6, 15),
            },
        }

        # Calculate overall score
        all_passed = all(
            r["achieved_throughput"] >= r["target_throughput"] * 0.95
            for r in workload_results.values()
        )

        if all_passed:
            status = BenchmarkStatus.PASSED
        else:
            status = BenchmarkStatus.WARNING

        total_throughput = sum(r["achieved_throughput"] for r in workload_results.values())

        return BenchmarkResult(
            name="iGaming Workload",
            status=status,
            value=total_throughput,
            unit="msg/sec",
            target=85000,
            description="Combined iGaming workload throughput",
            details={
                "workload_breakdown": workload_results,
                "topics_tested": list(workload_results.keys()),
            },
        )

    async def run_full_benchmark(self) -> list[BenchmarkResult]:
        """Run all Kafka benchmarks."""
        self.results = []

        # Health checks
        self.results.append(await self.health_check.check_broker_connectivity())
        self.results.append(await self.health_check.check_cluster_health())
        self.results.append(
            await self.health_check.check_topic_configuration(self.config.test_topic)
        )

        # Performance benchmarks
        self.results.append(await self.benchmark_producer_throughput())
        self.results.append(await self.benchmark_producer_latency())
        self.results.append(await self.benchmark_consumer_lag())
        self.results.append(await self.benchmark_replication_lag())
        self.results.append(await self.benchmark_partition_balance())
        self.results.append(await self.benchmark_igaming_workload())

        return self.results

    def get_recommendations(self) -> list[str]:
        """Generate optimization recommendations based on results."""
        recommendations = []

        for result in self.results:
            if result.status == BenchmarkStatus.FAILED:
                if "Throughput" in result.name:
                    recommendations.append(
                        f"[CRITICAL] {result.name}: Increase batch.size and linger.ms, "
                        "or add more partitions for parallelism"
                    )
                elif "Latency" in result.name:
                    recommendations.append(
                        f"[CRITICAL] {result.name}: Reduce batch.size, enable compression, "
                        "or use acks=1 for non-critical data"
                    )
                elif "Lag" in result.name:
                    recommendations.append(
                        f"[CRITICAL] {result.name}: Scale consumer instances, "
                        "increase max.poll.records, or optimize processing logic"
                    )
                elif "Replication" in result.name:
                    recommendations.append(
                        f"[CRITICAL] {result.name}: Check broker network, "
                        "reduce replica.fetch.max.bytes, or upgrade broker hardware"
                    )
            elif result.status == BenchmarkStatus.WARNING:
                recommendations.append(
                    f"[WARNING] {result.name}: Current={result.value}{result.unit}, "
                    f"Target={result.target}{result.unit} - Monitor closely"
                )

        if not recommendations:
            recommendations.append("[OK] All benchmarks passed - Kafka cluster is healthy")

        return recommendations

    def print_report(self, results: Optional[list[BenchmarkResult]] = None) -> None:
        """Print formatted benchmark report with visual indicators."""
        results = results or self.results

        print("\n" + "=" * 70)
        print("  KAFKA PERFORMANCE BENCHMARK REPORT")
        print("  " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
        print("=" * 70)

        # Status symbols
        symbols = {
            BenchmarkStatus.PASSED: "✅",
            BenchmarkStatus.WARNING: "⚠️ ",
            BenchmarkStatus.FAILED: "❌",
            BenchmarkStatus.SKIPPED: "⏭️ ",
        }

        # Group results
        health_results = [r for r in results if "Health" in r.name or "Config" in r.name or "Connectivity" in r.name]
        perf_results = [r for r in results if r not in health_results]

        # Print health checks
        print("\n📊 CLUSTER HEALTH")
        print("-" * 70)
        for result in health_results:
            symbol = symbols[result.status]
            print(f"  {symbol} {result.name:<30} {result.value:>10} {result.unit:<10} (target: {result.target})")

        # Print performance metrics
        print("\n⚡ PERFORMANCE METRICS")
        print("-" * 70)
        for result in perf_results:
            symbol = symbols[result.status]
            bar = self._create_bar(result.value, result.target)
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

        # Recommendations
        recommendations = self.get_recommendations()
        print("\n💡 RECOMMENDATIONS")
        print("-" * 70)
        for rec in recommendations:
            print(f"  {rec}")

        print("\n" + "=" * 70)

    def _create_bar(self, value: float, target: float, width: int = 20) -> str:
        """Create a visual progress bar."""
        if target == 0:
            return ""

        ratio = min(value / target, 2.0)  # Cap at 200%
        filled = int(ratio * width / 2)  # Scale to half width for 100%
        filled = min(filled, width)

        if ratio >= 1.0:
            bar_char = "█"
            color = ""
        elif ratio >= 0.8:
            bar_char = "▓"
            color = ""
        else:
            bar_char = "░"
            color = ""

        bar = bar_char * filled + "░" * (width - filled)
        percentage = ratio * 100

        return f"[{bar}] {percentage:>5.0f}%"


async def main() -> None:
    """Example usage of Kafka benchmark."""
    print("Starting Kafka Performance Benchmark...")

    benchmark = KafkaBenchmark(bootstrap_servers="localhost:9092")
    results = await benchmark.run_full_benchmark()
    benchmark.print_report(results)


if __name__ == "__main__":
    asyncio.run(main())
