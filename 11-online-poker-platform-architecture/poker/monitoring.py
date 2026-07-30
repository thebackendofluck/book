# Companion code for "The Backend of Luck" - Chapter 11, Online Poker Platform Architecture.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Chapter 4: Online Poker Platform Architecture
Monitoring and Analytics Implementation

This module contains monitoring, analytics, and infrastructure classes:
- MonitoringSystem: Real-time system health monitoring and alerting
- AnalyticsDashboard: Key business metrics collection
- LoadBalancer: Player routing using weighted round-robin and other algorithms
- CacheManager: Redis-backed session and table state caching

Reference: Chapter 4 - Monitoring and Analytics, Performance Optimization sections
"""

import json

import redis


class MonitoringSystem:
    def __init__(self):
        self.metrics_collector = MetricsCollector()  # ty:ignore[unresolved-reference]
        self.alert_manager = AlertManager()  # ty:ignore[unresolved-reference]

    async def monitor_system_health(self):
        """Monitor system health metrics"""
        metrics = {
            'server_status': await self.check_servers(),  # ty:ignore[possibly-missing-attribute]
            'database_status': await self.check_databases(),  # ty:ignore[possibly-missing-attribute]
            'api_latency': await self.measure_api_latency(),  # ty:ignore[possibly-missing-attribute]
            'error_rate': await self.calculate_error_rate(),  # ty:ignore[possibly-missing-attribute]
            'active_players': await self.count_active_players(),  # ty:ignore[possibly-missing-attribute]
            'concurrent_games': await self.count_concurrent_games()  # ty:ignore[possibly-missing-attribute]
        }

        # Check thresholds and alert if necessary
        for metric_name, value in metrics.items():
            if self.exceeds_threshold(metric_name, value):  # ty:ignore[possibly-missing-attribute]
                await self.alert_manager.send_alert(metric_name, value)

        return metrics


class AnalyticsDashboard:
    def get_key_metrics(self):
        return {
            'daily_active_users': self.get_dau(),  # ty:ignore[possibly-missing-attribute]
            'monthly_revenue': self.get_monthly_revenue(),  # ty:ignore[possibly-missing-attribute]
            'average_session_duration': self.get_avg_session_duration(),  # ty:ignore[possibly-missing-attribute]
            'player_retention_rate': self.get_retention_rate(),  # ty:ignore[possibly-missing-attribute]
            'game_completion_rate': self.get_completion_rate(),  # ty:ignore[possibly-missing-attribute]
            'rake_generated': self.get_total_rake(),  # ty:ignore[possibly-missing-attribute]
            'new_registrations': self.get_new_registrations(),  # ty:ignore[possibly-missing-attribute]
            'deposit_conversion_rate': self.get_deposit_conversion()  # ty:ignore[possibly-missing-attribute]
        }


class LoadBalancer:
    def __init__(self):
        self.servers = []
        self.algorithm = 'weighted_round_robin'

    def route_player(self, player_id):
        """Route player to optimal server"""
        if self.algorithm == 'weighted_round_robin':
            server = self.get_next_weighted_server()  # ty:ignore[possibly-missing-attribute]
        elif self.algorithm == 'least_connections':
            server = self.get_least_loaded_server()  # ty:ignore[possibly-missing-attribute]
        elif self.algorithm == 'geographic':
            server = self.get_nearest_server(player_id)  # ty:ignore[possibly-missing-attribute]

        return server

    def get_server_metrics(self, server):
        """Get server performance metrics"""
        return {
            'cpu_usage': server.get_cpu_usage(),
            'memory_usage': server.get_memory_usage(),
            'active_connections': server.get_connection_count(),
            'response_time': server.get_avg_response_time()
        }


class CacheManager:
    def __init__(self):
        self.redis_client = redis.Redis(
            host='localhost',
            port=6379,
            decode_responses=True
        )

    def cache_player_session(self, player_id, session_data):
        """Cache player session for quick access"""
        key = f"session:{player_id}"
        self.redis_client.setex(
            key,
            3600,  # 1 hour TTL
            json.dumps(session_data)
        )

    def cache_table_state(self, table_id, state):
        """Cache current table state"""
        key = f"table:{table_id}"
        self.redis_client.set(key, json.dumps(state))

    def get_leaderboard(self, game_type, limit=100):
        """Get cached leaderboard"""
        key = f"leaderboard:{game_type}"
        return self.redis_client.zrevrange(key, 0, limit-1, withscores=True)
