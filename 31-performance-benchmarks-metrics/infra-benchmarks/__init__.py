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
Infrastructure Performance Benchmarks for iGaming Platforms

This package provides comprehensive benchmarking tools for critical
infrastructure components used in iGaming platforms:

- Kafka: Message streaming and event processing
- OpenSearch: Search and analytics engine
- AWS RDS: Relational database service (PostgreSQL/MySQL)
- Redis: In-memory cache and session store

Each module includes:
- Performance benchmarking with detailed metrics
- Health checks and diagnostics
- Optimization recommendations
- Visual output for easy interpretation

Usage:
    from infra_benchmarks import (
        KafkaBenchmark,
        OpenSearchBenchmark,
        RDSBenchmark,
        RedisBenchmark,
        InfrastructureBenchmarkRunner
    )

    # Run all benchmarks
    runner = InfrastructureBenchmarkRunner()
    results = await runner.run_all_benchmarks()
    runner.print_report(results)
"""

from .kafka_benchmark import KafkaBenchmark, KafkaHealthCheck  # ty:ignore[unresolved-import]
from .opensearch_benchmark import OpenSearchBenchmark, OpenSearchHealthCheck  # ty:ignore[unresolved-import]
from .rds_benchmark import RDSBenchmark, RDSHealthCheck  # ty:ignore[unresolved-import]
from .redis_benchmark import RedisBenchmark, RedisHealthCheck  # ty:ignore[unresolved-import]
from .benchmark_runner import InfrastructureBenchmarkRunner  # ty:ignore[unresolved-import]

__all__ = [
    "KafkaBenchmark",
    "KafkaHealthCheck",
    "OpenSearchBenchmark",
    "OpenSearchHealthCheck",
    "RDSBenchmark",
    "RDSHealthCheck",
    "RedisBenchmark",
    "RedisHealthCheck",
    "InfrastructureBenchmarkRunner",
]
