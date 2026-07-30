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
Database Performance Monitor Module
====================================

Comprehensive database performance monitoring and optimization for iGaming platforms.
Tracks query performance, connection pools, storage I/O, and replication metrics.
"""

from typing import Any, Dict, List, Optional


class DatabasePerformanceMonitor:
    """Monitor and optimize database performance for iGaming platforms."""

    def __init__(self, database_config: Optional[Dict[str, Any]] = None):
        self.config = database_config or {}
        self.performance_baselines = self._load_database_baselines()

    async def monitor_database_performance(self) -> Dict[str, Any]:
        """
        Monitor comprehensive database performance metrics.

        Returns:
            Dict containing query performance, connections, storage, and analysis.
        """
        # Query performance metrics
        query_performance = await self._monitor_query_performance()

        # Connection pool metrics
        connection_metrics = await self._monitor_connection_pool()

        # Storage and I/O metrics
        storage_metrics = await self._monitor_storage_performance()

        # Replication lag monitoring
        replication_metrics = await self._monitor_replication_lag()

        # Index performance analysis
        index_metrics = await self._analyze_index_performance()

        # Lock and contention analysis
        lock_analysis = await self._analyze_lock_contention()

        return {
            "query_performance": query_performance,
            "connection_metrics": connection_metrics,
            "storage_metrics": storage_metrics,
            "replication_metrics": replication_metrics,
            "index_metrics": index_metrics,
            "lock_analysis": lock_analysis,
            "overall_performance_score": self._calculate_database_performance_score([
                query_performance, connection_metrics, storage_metrics,
                replication_metrics, index_metrics, lock_analysis
            ])
        }

    def _load_database_baselines(self) -> Dict[str, Any]:
        """Load database performance baselines."""
        return {
            "query_performance": {
                "avg_query_time_target": 50,      # milliseconds
                "slow_query_threshold": 1000,     # milliseconds
                "query_timeout_threshold": 30000, # milliseconds
                "concurrent_queries_target": 1000
            },
            "connection_pool": {
                "max_connections": 2000,
                "idle_connections_target": 100,
                "connection_wait_time_target": 10,  # milliseconds
                "connection_failure_rate_target": 0.001  # 0.001%
            },
            "storage_performance": {
                "read_iops_target": 50000,
                "write_iops_target": 25000,
                "read_latency_target": 5,    # milliseconds
                "write_latency_target": 10,  # milliseconds
                "storage_utilization_target": 80  # percentage
            },
            "replication": {
                "max_lag_target": 30,       # seconds
                "replication_delay_alert": 60,  # seconds
                "replica_lag_std_dev_target": 5  # seconds
            }
        }

    async def _monitor_query_performance(self) -> Dict[str, Any]:
        """Monitor database query performance."""
        # Query execution times
        query_times = {
            "select_queries": {
                "p50": 12,
                "p95": 89,
                "p99": 234,
                "avg": 34
            },
            "insert_queries": {
                "p50": 8,
                "p95": 45,
                "p99": 123,
                "avg": 18
            },
            "update_queries": {
                "p50": 15,
                "p95": 112,
                "p99": 345,
                "avg": 42
            },
            "complex_queries": {
                "p50": 89,
                "p95": 567,
                "p99": 1234,
                "avg": 234
            }
        }

        # Slow query analysis
        slow_queries = {
            "queries_over_1s": 23,
            "queries_over_5s": 5,
            "queries_over_30s": 1,
            "most_expensive_queries": [
                {
                    "query": "SELECT * FROM bets WHERE user_id = ? AND created_at > ?",
                    "avg_time": 2340,
                    "execution_count": 15000,
                    "optimization_recommendation": "Add composite index on (user_id, created_at)"
                }
            ]
        }

        # Query optimization opportunities
        optimization_opportunities = [
            {
                "table": "user_bets",
                "issue": "Missing index on bet_amount",
                "impact": "45% of queries slowed",
                "recommendation": "CREATE INDEX idx_user_bets_amount ON user_bets(bet_amount)"
            },
            {
                "table": "game_sessions",
                "issue": "Table scan on large dataset",
                "impact": "23% of query time",
                "recommendation": "Partition by date and add covering index"
            }
        ]

        return {
            "query_execution_times": query_times,
            "slow_query_analysis": slow_queries,
            "optimization_opportunities": optimization_opportunities,
            "performance_score": self._calculate_query_performance_score(query_times, slow_queries)
        }

    async def _monitor_connection_pool(self) -> Dict[str, Any]:
        """Monitor database connection pool metrics."""
        return {
            "active_connections": 450,
            "idle_connections": 150,
            "max_connections": 2000,
            "connection_wait_time_avg": 5.2,  # milliseconds
            "connection_errors_rate": 0.0005,
            "pool_utilization": 0.30,
            "health_status": "healthy"
        }

    async def _monitor_storage_performance(self) -> Dict[str, Any]:
        """Monitor storage and I/O performance."""
        return {
            "read_iops": 42500,
            "write_iops": 18700,
            "read_latency_avg": 3.2,   # milliseconds
            "write_latency_avg": 7.8,  # milliseconds
            "storage_utilization": 67,  # percentage
            "iops_utilization": 0.72,
            "throughput_mb_s": 850
        }

    async def _monitor_replication_lag(self) -> Dict[str, Any]:
        """Monitor database replication metrics."""
        return {
            "primary_status": "healthy",
            "replicas": [
                {"name": "replica-1", "lag_seconds": 0.5, "status": "healthy"},
                {"name": "replica-2", "lag_seconds": 0.8, "status": "healthy"},
                {"name": "replica-3", "lag_seconds": 1.2, "status": "healthy"}
            ],
            "max_lag_seconds": 1.2,
            "avg_lag_seconds": 0.83,
            "replication_health": "excellent"
        }

    async def _analyze_index_performance(self) -> Dict[str, Any]:
        """Analyze database index performance."""
        return {
            "index_hit_rate": 0.97,
            "unused_indexes": [
                {"name": "idx_old_status", "table": "transactions", "size_mb": 256},
                {"name": "idx_deprecated_flag", "table": "users", "size_mb": 128}
            ],
            "missing_indexes": [
                {"table": "bets", "columns": ["user_id", "created_at"], "estimated_improvement": "45%"},
                {"table": "sessions", "columns": ["game_id", "status"], "estimated_improvement": "30%"}
            ],
            "fragmented_indexes": [
                {"name": "idx_transactions_date", "fragmentation": 0.23}
            ]
        }

    async def _analyze_lock_contention(self) -> Dict[str, Any]:
        """Analyze lock contention and deadlocks."""
        return {
            "deadlocks_last_hour": 0,
            "deadlocks_last_24h": 2,
            "lock_wait_time_avg": 12.5,  # milliseconds
            "lock_contention_rate": 0.002,
            "hot_tables": [
                {"table": "user_balances", "lock_waits": 234},
                {"table": "active_bets", "lock_waits": 156}
            ],
            "recommendations": [
                "Consider optimistic locking for user_balances",
                "Reduce transaction scope for high-contention tables"
            ]
        }

    def _calculate_query_performance_score(
        self,
        query_times: Dict[str, Any],
        slow_queries: Dict[str, Any]
    ) -> float:
        """Calculate query performance score."""
        baselines = self.performance_baselines["query_performance"]

        # Check average query times against targets
        avg_times = [qt.get("avg", 100) for qt in query_times.values()]
        avg_score = sum(
            1.0 if t < baselines["avg_query_time_target"] else 0.7 if t < baselines["slow_query_threshold"] else 0.3
            for t in avg_times
        ) / max(len(avg_times), 1)

        # Penalize for slow queries
        slow_penalty = min(slow_queries.get("queries_over_1s", 0) * 0.01, 0.2)

        return max(avg_score - slow_penalty, 0.0)

    def _calculate_database_performance_score(
        self,
        metrics_list: List[Dict[str, Any]]
    ) -> float:
        """Calculate overall database performance score."""
        base_score = 0.85

        for metrics in metrics_list:
            if isinstance(metrics, dict):
                if metrics.get("performance_score"):
                    base_score = (base_score + metrics["performance_score"]) / 2
                elif metrics.get("health_status") == "healthy":
                    base_score += 0.02
                elif metrics.get("replication_health") == "excellent":
                    base_score += 0.02

        return min(max(base_score, 0.0), 1.0)
