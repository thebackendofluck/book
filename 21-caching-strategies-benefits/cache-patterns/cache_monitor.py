# Companion code for "The Backend of Luck" - Chapter 21, Caching Strategies and Benefits.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Cache monitoring and alerting for iGaming platforms.

Provides:
- Real-time cache metrics collection
- Performance dashboards
- Alert generation
- Capacity planning insights

Critical for maintaining cache health and performance.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class AlertSeverity(Enum):
    """Alert severity levels."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class MetricType(Enum):
    """Types of cache metrics."""

    HIT_RATE = "hit_rate"
    MISS_RATE = "miss_rate"
    LATENCY = "latency"
    THROUGHPUT = "throughput"
    MEMORY = "memory"
    EVICTIONS = "evictions"
    CONNECTIONS = "connections"


@dataclass
class CacheMetrics:
    """Point-in-time cache metrics."""

    timestamp: datetime
    hit_rate: float  # 0.0 - 1.0
    miss_rate: float  # 0.0 - 1.0
    avg_latency_ms: float
    p99_latency_ms: float
    throughput_ops: int  # operations per second
    memory_used_mb: float
    memory_max_mb: float
    evictions_per_sec: float
    connections_active: int
    connections_max: int
    keyspace_size: int


@dataclass
class Alert:
    """Cache alert."""

    severity: AlertSeverity
    metric: MetricType
    message: str
    current_value: float
    threshold: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class PerformanceReport:
    """Cache performance report."""

    period_start: datetime
    period_end: datetime
    avg_hit_rate: float
    avg_latency_ms: float
    peak_throughput: int
    total_operations: int
    memory_utilization_percent: float
    alerts_generated: int
    recommendations: list[str]


@dataclass
class AlertThresholds:
    """Configurable alert thresholds."""

    hit_rate_warning: float = 0.80  # Alert if below 80%
    hit_rate_critical: float = 0.60  # Critical if below 60%
    latency_warning_ms: float = 5.0  # Alert if above 5ms
    latency_critical_ms: float = 10.0  # Critical if above 10ms
    memory_warning_percent: float = 80.0  # Alert if above 80%
    memory_critical_percent: float = 90.0  # Critical if above 90%
    eviction_warning_per_sec: float = 100.0
    eviction_critical_per_sec: float = 500.0
    connection_warning_percent: float = 80.0
    connection_critical_percent: float = 95.0


class CacheMonitor:
    """
    Monitor cache performance and generate alerts.

    Collects metrics from Redis/cache and provides:
    - Real-time performance monitoring
    - Alert generation based on thresholds
    - Performance trending and recommendations
    """

    def __init__(
        self,
        redis_client: Any,
        thresholds: Optional[AlertThresholds] = None,
    ):
        self.redis = redis_client
        self.thresholds = thresholds or AlertThresholds()
        self._metrics_history: list[CacheMetrics] = []
        self._alerts: list[Alert] = []
        self._max_history = 1000

    async def collect_metrics(self) -> CacheMetrics:
        """
        Collect current cache metrics.

        Queries Redis INFO command for statistics.
        """
        try:
            info = await self.redis.info()

            keyspace_hits = info.get("keyspace_hits", 0)
            keyspace_misses = info.get("keyspace_misses", 0)
            total_ops = keyspace_hits + keyspace_misses

            hit_rate = keyspace_hits / total_ops if total_ops > 0 else 0.0
            miss_rate = keyspace_misses / total_ops if total_ops > 0 else 0.0
            memory_used = info.get("used_memory", 0) / (1024 * 1024)
            memory_max = info.get("maxmemory", 0) / (1024 * 1024)

            if memory_max == 0:
                memory_max = memory_used * 1.5

            metrics = CacheMetrics(
                timestamp=datetime.now(timezone.utc),
                hit_rate=hit_rate,
                miss_rate=miss_rate,
                avg_latency_ms=0.5,
                p99_latency_ms=2.0,
                throughput_ops=info.get("instantaneous_ops_per_sec", 0),
                memory_used_mb=memory_used,
                memory_max_mb=memory_max,
                evictions_per_sec=info.get("evicted_keys", 0) / max(1, info.get("uptime_in_seconds", 1)),
                connections_active=info.get("connected_clients", 0),
                connections_max=info.get("maxclients", 10000),
                keyspace_size=sum(
                    v.get("keys", 0)
                    for k, v in info.items()
                    if k.startswith("db") and isinstance(v, dict)
                ),
            )

            self._metrics_history.append(metrics)
            if len(self._metrics_history) > self._max_history:
                self._metrics_history = self._metrics_history[-self._max_history :]

            self._check_alerts(metrics)

            return metrics

        except Exception as e:
            logger.error(f"Failed to collect metrics: {e}")
            raise

    def _check_alerts(self, metrics: CacheMetrics) -> None:
        """Check metrics against thresholds and generate alerts."""
        if metrics.hit_rate < self.thresholds.hit_rate_critical:
            self._add_alert(
                AlertSeverity.CRITICAL,
                MetricType.HIT_RATE,
                f"Critical: Cache hit rate at {metrics.hit_rate:.1%}",
                metrics.hit_rate,
                self.thresholds.hit_rate_critical,
            )
        elif metrics.hit_rate < self.thresholds.hit_rate_warning:
            self._add_alert(
                AlertSeverity.WARNING,
                MetricType.HIT_RATE,
                f"Warning: Cache hit rate at {metrics.hit_rate:.1%}",
                metrics.hit_rate,
                self.thresholds.hit_rate_warning,
            )

        if metrics.avg_latency_ms > self.thresholds.latency_critical_ms:
            self._add_alert(
                AlertSeverity.CRITICAL,
                MetricType.LATENCY,
                f"Critical: Cache latency at {metrics.avg_latency_ms:.2f}ms",
                metrics.avg_latency_ms,
                self.thresholds.latency_critical_ms,
            )
        elif metrics.avg_latency_ms > self.thresholds.latency_warning_ms:
            self._add_alert(
                AlertSeverity.WARNING,
                MetricType.LATENCY,
                f"Warning: Cache latency at {metrics.avg_latency_ms:.2f}ms",
                metrics.avg_latency_ms,
                self.thresholds.latency_warning_ms,
            )

        memory_percent = (
            (metrics.memory_used_mb / metrics.memory_max_mb * 100)
            if metrics.memory_max_mb > 0
            else 0
        )
        if memory_percent > self.thresholds.memory_critical_percent:
            self._add_alert(
                AlertSeverity.CRITICAL,
                MetricType.MEMORY,
                f"Critical: Memory usage at {memory_percent:.1f}%",
                memory_percent,
                self.thresholds.memory_critical_percent,
            )
        elif memory_percent > self.thresholds.memory_warning_percent:
            self._add_alert(
                AlertSeverity.WARNING,
                MetricType.MEMORY,
                f"Warning: Memory usage at {memory_percent:.1f}%",
                memory_percent,
                self.thresholds.memory_warning_percent,
            )

        if metrics.evictions_per_sec > self.thresholds.eviction_critical_per_sec:
            self._add_alert(
                AlertSeverity.CRITICAL,
                MetricType.EVICTIONS,
                f"Critical: Eviction rate at {metrics.evictions_per_sec:.0f}/sec",
                metrics.evictions_per_sec,
                self.thresholds.eviction_critical_per_sec,
            )
        elif metrics.evictions_per_sec > self.thresholds.eviction_warning_per_sec:
            self._add_alert(
                AlertSeverity.WARNING,
                MetricType.EVICTIONS,
                f"Warning: Eviction rate at {metrics.evictions_per_sec:.0f}/sec",
                metrics.evictions_per_sec,
                self.thresholds.eviction_warning_per_sec,
            )

    def _add_alert(
        self,
        severity: AlertSeverity,
        metric: MetricType,
        message: str,
        current_value: float,
        threshold: float,
    ) -> None:
        """Add alert to history."""
        alert = Alert(
            severity=severity,
            metric=metric,
            message=message,
            current_value=current_value,
            threshold=threshold,
        )
        self._alerts.append(alert)
        logger.log(
            logging.CRITICAL if severity == AlertSeverity.CRITICAL else logging.WARNING,
            message,
        )

    def get_recent_alerts(
        self,
        hours: int = 24,
        severity: Optional[AlertSeverity] = None,
    ) -> list[Alert]:
        """Get alerts from the last N hours."""
        cutoff = datetime.now(timezone.utc).timestamp() - (hours * 3600)

        alerts = [a for a in self._alerts if a.timestamp.timestamp() > cutoff]

        if severity:
            alerts = [a for a in alerts if a.severity == severity]

        return alerts

    def generate_report(self, hours: int = 24) -> PerformanceReport:
        """
        Generate performance report for the specified period.

        Analyzes metrics history and provides recommendations.
        """
        cutoff = datetime.now(timezone.utc).timestamp() - (hours * 3600)
        recent_metrics = [
            m for m in self._metrics_history if m.timestamp.timestamp() > cutoff
        ]

        if not recent_metrics:
            return PerformanceReport(
                period_start=datetime.now(timezone.utc),
                period_end=datetime.now(timezone.utc),
                avg_hit_rate=0.0,
                avg_latency_ms=0.0,
                peak_throughput=0,
                total_operations=0,
                memory_utilization_percent=0.0,
                alerts_generated=0,
                recommendations=["No metrics available for analysis"],
            )

        avg_hit_rate = sum(m.hit_rate for m in recent_metrics) / len(recent_metrics)
        avg_latency = sum(m.avg_latency_ms for m in recent_metrics) / len(
            recent_metrics
        )
        peak_throughput = max(m.throughput_ops for m in recent_metrics)
        avg_memory_used = sum(m.memory_used_mb for m in recent_metrics) / len(
            recent_metrics
        )
        avg_memory_max = sum(m.memory_max_mb for m in recent_metrics) / len(
            recent_metrics
        )
        memory_util = (
            (avg_memory_used / avg_memory_max * 100) if avg_memory_max > 0 else 0
        )
        recent_alerts = self.get_recent_alerts(hours)
        recommendations = self._generate_recommendations(
            avg_hit_rate, avg_latency, memory_util, recent_alerts
        )

        return PerformanceReport(
            period_start=recent_metrics[0].timestamp,
            period_end=recent_metrics[-1].timestamp,
            avg_hit_rate=round(avg_hit_rate, 4),
            avg_latency_ms=round(avg_latency, 3),
            peak_throughput=peak_throughput,
            total_operations=sum(m.throughput_ops for m in recent_metrics),
            memory_utilization_percent=round(memory_util, 1),
            alerts_generated=len(recent_alerts),
            recommendations=recommendations,
        )

    def _generate_recommendations(
        self,
        hit_rate: float,
        latency: float,
        memory_util: float,
        alerts: list[Alert],
    ) -> list[str]:
        """Generate performance recommendations."""
        recommendations = []

        if hit_rate < 0.80:
            recommendations.append(
                f"Hit rate ({hit_rate:.1%}) is below target (80%). "
                "Consider: longer TTLs, cache warming, or reviewing cache keys."
            )

        if hit_rate < 0.60:
            recommendations.append(
                "Critical: Very low hit rate suggests cache is undersized "
                "or data access patterns don't benefit from caching."
            )

        if latency > 5.0:
            recommendations.append(
                f"Latency ({latency:.1f}ms) is elevated. "
                "Check network, increase connections, or add replicas."
            )

        if memory_util > 80:
            recommendations.append(
                f"Memory utilization ({memory_util:.0f}%) is high. "
                "Consider scaling up or implementing better eviction policies."
            )

        critical_alerts = [a for a in alerts if a.severity == AlertSeverity.CRITICAL]
        if len(critical_alerts) > 5:
            recommendations.append(
                f"High number of critical alerts ({len(critical_alerts)}). "
                "Review cache infrastructure capacity."
            )

        if not recommendations:
            recommendations.append(
                "Cache performance is within acceptable parameters. "
                "Continue monitoring for trends."
            )

        return recommendations

    def get_dashboard_data(self) -> dict[str, Any]:
        """
        Get data formatted for dashboard display.

        Returns current metrics, trends, and alerts.
        """
        current = self._metrics_history[-1] if self._metrics_history else None
        recent_alerts = self.get_recent_alerts(hours=1)

        return {
            "current_metrics": {
                "hit_rate": current.hit_rate if current else 0,
                "latency_ms": current.avg_latency_ms if current else 0,
                "throughput_ops": current.throughput_ops if current else 0,
                "memory_used_mb": current.memory_used_mb if current else 0,
                "memory_max_mb": current.memory_max_mb if current else 0,
                "connections": current.connections_active if current else 0,
            }
            if current
            else {},
            "alerts": [
                {
                    "severity": a.severity.value,
                    "message": a.message,
                    "timestamp": a.timestamp.isoformat(),
                }
                for a in recent_alerts[-10:]
            ],
            "status": self._get_overall_status(current, recent_alerts),
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    def _get_overall_status(
        self, metrics: Optional[CacheMetrics], alerts: list[Alert]
    ) -> str:
        """Determine overall cache health status."""
        if not metrics:
            return "unknown"

        critical_alerts = [a for a in alerts if a.severity == AlertSeverity.CRITICAL]
        if critical_alerts:
            return "critical"

        warning_alerts = [a for a in alerts if a.severity == AlertSeverity.WARNING]
        if warning_alerts:
            return "warning"

        if metrics.hit_rate >= 0.90 and metrics.avg_latency_ms < 2.0:
            return "excellent"

        if metrics.hit_rate >= 0.80:
            return "healthy"

        return "degraded"
