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
Infrastructure Benchmark Runner for iGaming Platforms

This module provides a unified interface to run all infrastructure benchmarks
and generate comprehensive reports with actionable recommendations.

Supported Components:
- Kafka: Message streaming
- OpenSearch: Search and analytics
- AWS RDS: Relational database
- Redis: In-memory cache

Usage:
    runner = InfrastructureBenchmarkRunner()
    results = await runner.run_all_benchmarks()
    runner.print_comprehensive_report(results)

    # Or run specific benchmarks
    results = await runner.run_benchmarks(["kafka", "redis"])

Dependencies:
    pip install aiokafka opensearch-py asyncpg redis
    # or
    uv pip install aiokafka opensearch-py asyncpg redis
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
import asyncio
import json


# Import benchmark modules (relative imports for package use)
from .kafka_benchmark import KafkaBenchmark, KafkaConfig, BenchmarkResult, BenchmarkStatus  # ty:ignore[unresolved-import]
from .opensearch_benchmark import OpenSearchBenchmark, OpenSearchConfig  # ty:ignore[unresolved-import]
from .rds_benchmark import RDSBenchmark, RDSConfig  # ty:ignore[unresolved-import]
from .redis_benchmark import RedisBenchmark, RedisConfig  # ty:ignore[unresolved-import]


@dataclass
class InfrastructureConfig:
    """Configuration for all infrastructure components."""
    kafka: Optional[KafkaConfig] = None
    opensearch: Optional[OpenSearchConfig] = None
    rds: Optional[RDSConfig] = None
    redis: Optional[RedisConfig] = None

    # Output settings
    output_format: str = "console"  # console, json, html
    output_file: Optional[str] = None
    verbose: bool = True


@dataclass
class ComponentResults:
    """Results from a single component benchmark."""
    component: str
    results: list[BenchmarkResult]
    duration_seconds: float
    recommendations: list[str]

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.status == BenchmarkStatus.PASSED)

    @property
    def warnings(self) -> int:
        return sum(1 for r in self.results if r.status == BenchmarkStatus.WARNING)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status == BenchmarkStatus.FAILED)

    @property
    def health_score(self) -> float:
        """Calculate health score as percentage."""
        total = len(self.results)
        if total == 0:
            return 100.0
        # Passed = 100%, Warning = 50%, Failed = 0%
        score = (self.passed * 100 + self.warnings * 50) / total
        return round(score, 1)


class InfrastructureBenchmarkRunner:
    """
    Unified benchmark runner for all infrastructure components.

    Provides a single interface to benchmark Kafka, OpenSearch, RDS, and Redis,
    with comprehensive reporting and recommendations.
    """

    def __init__(self, config: Optional[InfrastructureConfig] = None):
        self.config = config or InfrastructureConfig()
        self.component_results: dict[str, ComponentResults] = {}

    async def run_kafka_benchmark(self) -> ComponentResults:
        """Run Kafka benchmarks."""
        kafka_config = self.config.kafka or KafkaConfig()
        benchmark = KafkaBenchmark(config=kafka_config)

        start = asyncio.get_running_loop().time()
        results = await benchmark.run_full_benchmark()
        duration = asyncio.get_running_loop().time() - start

        return ComponentResults(
            component="Kafka",
            results=results,
            duration_seconds=round(duration, 2),
            recommendations=benchmark.get_recommendations(),
        )

    async def run_opensearch_benchmark(self) -> ComponentResults:
        """Run OpenSearch benchmarks."""
        os_config = self.config.opensearch or OpenSearchConfig()
        benchmark = OpenSearchBenchmark(config=os_config)

        start = asyncio.get_running_loop().time()
        results = await benchmark.run_full_benchmark()
        duration = asyncio.get_running_loop().time() - start

        return ComponentResults(
            component="OpenSearch",
            results=results,
            duration_seconds=round(duration, 2),
            recommendations=benchmark.get_recommendations(),
        )

    async def run_rds_benchmark(self) -> ComponentResults:
        """Run RDS benchmarks."""
        rds_config = self.config.rds or RDSConfig()
        benchmark = RDSBenchmark(config=rds_config)

        start = asyncio.get_running_loop().time()
        results = await benchmark.run_full_benchmark()
        duration = asyncio.get_running_loop().time() - start

        return ComponentResults(
            component="AWS RDS",
            results=results,
            duration_seconds=round(duration, 2),
            recommendations=benchmark.get_recommendations(),
        )

    async def run_redis_benchmark(self) -> ComponentResults:
        """Run Redis benchmarks."""
        redis_config = self.config.redis or RedisConfig()
        benchmark = RedisBenchmark(config=redis_config)

        start = asyncio.get_running_loop().time()
        results = await benchmark.run_full_benchmark()
        duration = asyncio.get_running_loop().time() - start

        return ComponentResults(
            component="Redis",
            results=results,
            duration_seconds=round(duration, 2),
            recommendations=benchmark.get_recommendations(),
        )

    async def run_benchmarks(
        self,
        components: Optional[list[str]] = None,
    ) -> dict[str, ComponentResults]:
        """
        Run benchmarks for specified components.

        Args:
            components: List of components to benchmark.
                        Options: ["kafka", "opensearch", "rds", "redis"]
                        If None, runs all benchmarks.

        Returns:
            Dictionary mapping component name to results.
        """
        all_components = ["kafka", "opensearch", "rds", "redis"]
        components = components or all_components

        benchmark_map = {
            "kafka": self.run_kafka_benchmark,
            "opensearch": self.run_opensearch_benchmark,
            "rds": self.run_rds_benchmark,
            "redis": self.run_redis_benchmark,
        }

        self.component_results = {}

        for component in components:
            if component.lower() in benchmark_map:
                if self.config.verbose:
                    print(f"\n🔄 Running {component.upper()} benchmark...")
                try:
                    result = await benchmark_map[component.lower()]()
                    self.component_results[component] = result
                    if self.config.verbose:
                        print(f"✅ {component.upper()} benchmark complete "
                              f"(Health Score: {result.health_score}%)")
                except Exception as e:
                    print(f"❌ {component.upper()} benchmark failed: {e}")

        return self.component_results

    async def run_all_benchmarks(self) -> dict[str, ComponentResults]:
        """Run all infrastructure benchmarks."""
        return await self.run_benchmarks()

    def get_overall_health_score(self) -> float:
        """Calculate overall infrastructure health score."""
        if not self.component_results:
            return 0.0

        total_score = sum(r.health_score for r in self.component_results.values())
        return round(total_score / len(self.component_results), 1)

    def get_critical_issues(self) -> list[str]:
        """Get all critical (failed) issues across all components."""
        issues = []
        for component, results in self.component_results.items():
            for result in results.results:
                if result.status == BenchmarkStatus.FAILED:
                    issues.append(f"[{component}] {result.name}: {result.value}{result.unit} "
                                  f"(target: {result.target}{result.unit})")
        return issues

    def print_comprehensive_report(
        self,
        results: Optional[dict[str, ComponentResults]] = None,
    ) -> None:
        """Print comprehensive infrastructure benchmark report."""
        results = results or self.component_results

        print("\n" + "=" * 80)
        print("  🏗️  INFRASTRUCTURE PERFORMANCE BENCHMARK REPORT")
        print("  " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
        print("=" * 80)

        # Overall Summary
        overall_score = self.get_overall_health_score()
        score_bar = self._create_score_bar(overall_score)

        print(f"\n📊 OVERALL INFRASTRUCTURE HEALTH: {overall_score}% {score_bar}")
        print("-" * 80)

        # Component Summary Table
        print("\n┌─────────────────┬────────┬──────────┬────────┬──────────┬────────────┐")
        print("│ Component       │ Tests  │ Passed   │ Warn   │ Failed   │ Health     │")
        print("├─────────────────┼────────┼──────────┼────────┼──────────┼────────────┤")

        for component, comp_results in results.items():
            total = len(comp_results.results)
            passed = comp_results.passed
            warnings = comp_results.warnings
            failed = comp_results.failed
            score = comp_results.health_score
            score_indicator = "🟢" if score >= 90 else "🟡" if score >= 70 else "🔴"

            print(f"│ {component:<15} │ {total:>6} │ {passed:>8} │ {warnings:>6} │ {failed:>8} │ "
                  f"{score_indicator} {score:>5.1f}%   │")

        print("└─────────────────┴────────┴──────────┴────────┴──────────┴────────────┘")

        # Detailed Results per Component
        for component, comp_results in results.items():
            self._print_component_details(component, comp_results)

        # Critical Issues Summary
        critical_issues = self.get_critical_issues()
        if critical_issues:
            print("\n" + "=" * 80)
            print("  🚨 CRITICAL ISSUES REQUIRING IMMEDIATE ATTENTION")
            print("=" * 80)
            for i, issue in enumerate(critical_issues, 1):
                print(f"  {i}. {issue}")

        # Consolidated Recommendations
        print("\n" + "=" * 80)
        print("  💡 OPTIMIZATION RECOMMENDATIONS")
        print("=" * 80)

        for component, comp_results in results.items():
            if any("[CRITICAL]" in r or "[WARNING]" in r for r in comp_results.recommendations):
                print(f"\n  📌 {component}:")
                for rec in comp_results.recommendations:
                    if "[CRITICAL]" in rec or "[WARNING]" in rec:
                        print(f"     {rec}")

        # Performance Insights
        print("\n" + "=" * 80)
        print("  📈 PERFORMANCE INSIGHTS FOR iGAMING")
        print("=" * 80)
        self._print_igaming_insights(results)

        print("\n" + "=" * 80)
        print("  Report generated by iGaming Infrastructure Benchmark Suite")
        print("=" * 80 + "\n")

    def _print_component_details(
        self,
        component: str,
        comp_results: ComponentResults,
    ) -> None:
        """Print detailed results for a component."""
        print(f"\n{'─' * 80}")
        print(f"  {component.upper()} DETAILS (Duration: {comp_results.duration_seconds}s)")
        print("─" * 80)

        symbols = {
            BenchmarkStatus.PASSED: "✅",
            BenchmarkStatus.WARNING: "⚠️ ",
            BenchmarkStatus.FAILED: "❌",
            BenchmarkStatus.SKIPPED: "⏭️ ",
        }

        for result in comp_results.results:
            symbol = symbols[result.status]
            value_str = f"{result.value:.2f}" if isinstance(result.value, float) else str(result.value)
            print(f"  {symbol} {result.name:<30} {value_str:>10} {result.unit:<12} "
                  f"(target: {result.target})")

    def _create_score_bar(self, score: float, width: int = 30) -> str:
        """Create a visual score bar."""
        filled = int(score / 100 * width)
        bar = "█" * filled + "░" * (width - filled)

        if score >= 90:
            indicator = "🟢"
        elif score >= 70:
            indicator = "🟡"
        else:
            indicator = "🔴"

        return f"[{bar}] {indicator}"

    def _print_igaming_insights(
        self,
        results: dict[str, ComponentResults],
    ) -> None:
        """Print iGaming-specific performance insights."""
        insights = []

        # Check Kafka for event processing capacity
        if "kafka" in results:
            kafka_results = results["kafka"]
            throughput_result = next(
                (r for r in kafka_results.results if "Throughput" in r.name),
                None,
            )
            if throughput_result and throughput_result.value >= 100000:
                insights.append(
                    "  ✅ Kafka throughput sufficient for high-volume betting events"
                )
            else:
                insights.append(
                    "  ⚠️  Consider scaling Kafka for peak betting periods"
                )

        # Check Redis for session/cache performance
        if "redis" in results:
            redis_results = results["redis"]
            latency_result = next(
                (r for r in redis_results.results if "GET Latency" in r.name),
                None,
            )
            if latency_result and latency_result.value <= 1.0:
                insights.append(
                    "  ✅ Redis latency excellent for real-time balance checks"
                )
            else:
                insights.append(
                    "  ⚠️  Redis latency may impact user experience during peak load"
                )

        # Check RDS for transaction capacity
        if "rds" in results:
            rds_results = results["rds"]
            query_result = next(
                (r for r in rds_results.results if "Simple Query" in r.name),
                None,
            )
            if query_result and query_result.value <= 10:
                insights.append(
                    "  ✅ Database query latency within target for bet placement"
                )
            else:
                insights.append(
                    "  ⚠️  Consider query optimization or read replicas for scale"
                )

        # Check OpenSearch for search performance
        if "opensearch" in results:
            os_results = results["opensearch"]
            search_result = next(
                (r for r in os_results.results if "Search Latency" in r.name),
                None,
            )
            if search_result and search_result.value <= 100:
                insights.append(
                    "  ✅ Search latency suitable for game/event discovery"
                )
            else:
                insights.append(
                    "  ⚠️  Optimize search queries or add OpenSearch nodes"
                )

        if not insights:
            insights.append("  Run benchmarks to get iGaming-specific insights")

        for insight in insights:
            print(insight)

    def export_json(self, filepath: str) -> None:
        """Export benchmark results to JSON."""
        export_data: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "overall_health_score": self.get_overall_health_score(),
            "critical_issues": self.get_critical_issues(),
            "components": {},
        }

        for component, comp_results in self.component_results.items():
            export_data["components"][component] = {
                "health_score": comp_results.health_score,
                "duration_seconds": comp_results.duration_seconds,
                "passed": comp_results.passed,
                "warnings": comp_results.warnings,
                "failed": comp_results.failed,
                "results": [r.to_dict() for r in comp_results.results],
                "recommendations": comp_results.recommendations,
            }

        with open(filepath, "w") as f:
            json.dump(export_data, f, indent=2, default=str)

        print(f"📄 Results exported to {filepath}")


async def main() -> None:
    """Example usage of infrastructure benchmark runner."""
    print("🚀 Starting iGaming Infrastructure Benchmark Suite")
    print("=" * 80)

    # Create runner with default configuration
    runner = InfrastructureBenchmarkRunner(
        config=InfrastructureConfig(verbose=True)
    )

    # Run all benchmarks
    results = await runner.run_all_benchmarks()

    # Print comprehensive report
    runner.print_comprehensive_report(results)

    # Optionally export to JSON
    # runner.export_json("benchmark_results.json")


if __name__ == "__main__":
    asyncio.run(main())
