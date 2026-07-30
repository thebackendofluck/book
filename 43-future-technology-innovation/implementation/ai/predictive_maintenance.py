#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 43, Future Technology & Innovation in iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Predictive Maintenance for iGaming Platform Infrastructure
============================================================

Monitors platform health metrics and predicts failures before they impact
players. Covers game servers, payment gateways, databases, and CDN nodes.

Feasibility Assessment:
- Anomaly detection on time-series metrics is well-established
- Statistical methods (Z-score, IQR) work without training data
- Isolation Forest achievable with scikit-learn on modest hardware
- Expected lead time: 15-60 minutes before degradation impacts players
- Integration: Prometheus/Datadog metrics -> this analyzer -> PagerDuty/Slack
- Cost: Runs alongside existing monitoring stack, minimal added infrastructure

Dependencies: None for core. Optional: numpy, scikit-learn for ML methods
"""

import math
import json
import logging
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional
from collections import deque
from enum import Enum

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


class ComponentType(Enum):
    GAME_SERVER = "game_server"
    PAYMENT_GATEWAY = "payment_gateway"
    DATABASE = "database"
    CDN_NODE = "cdn_node"
    LOAD_BALANCER = "load_balancer"
    CACHE = "cache"
    MESSAGE_QUEUE = "message_queue"
    RNG_SERVICE = "rng_service"  # Random Number Generator - critical for gambling


class HealthStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    WARNING = "warning"
    CRITICAL = "critical"
    PREDICTED_FAILURE = "predicted_failure"


class AlertSeverity(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


@dataclass
class MetricSample:
    timestamp: str
    component_id: str
    component_type: ComponentType
    metric_name: str
    value: float
    unit: str = ""


@dataclass
class ComponentHealth:
    component_id: str
    component_type: ComponentType
    status: HealthStatus
    health_score: float  # 0.0 (dead) to 1.0 (perfect)
    metrics: dict = field(default_factory=dict)
    anomalies: list = field(default_factory=list)
    predicted_failure_hours: Optional[float] = None
    recommendations: list = field(default_factory=list)


@dataclass
class MaintenanceAlert:
    alert_id: str
    severity: AlertSeverity
    component_id: str
    component_type: ComponentType
    title: str
    description: str
    predicted_impact: str
    recommended_action: str
    estimated_time_to_failure: Optional[float] = None
    confidence: float = 0.0
    timestamp: str = ""


# ---------------------------------------------------------------------------
# Statistical anomaly detection
# ---------------------------------------------------------------------------

class StatisticalDetector:
    """
    Detects anomalies in metric time series using statistical methods.
    No ML dependencies required - uses Z-score and moving averages.
    """

    def __init__(self, window_size: int = 60, z_threshold: float = 3.0):
        self.window_size = window_size
        self.z_threshold = z_threshold
        self._windows: dict[str, deque] = {}

    def _get_window(self, key: str) -> deque:
        if key not in self._windows:
            self._windows[key] = deque(maxlen=self.window_size)
        return self._windows[key]

    def add_sample(self, component_id: str, metric_name: str, value: float) -> Optional[dict]:
        """Add a sample and check for anomalies. Returns anomaly info or None."""
        key = f"{component_id}:{metric_name}"
        window = self._get_window(key)
        window.append(value)

        if len(window) < 10:
            return None  # not enough data

        mean = sum(window) / len(window)
        variance = sum((x - mean) ** 2 for x in window) / len(window)
        std_dev = math.sqrt(variance) if variance > 0 else 0.001

        z_score = (value - mean) / std_dev

        if abs(z_score) > self.z_threshold:
            return {
                "component_id": component_id,
                "metric": metric_name,
                "value": value,
                "mean": round(mean, 3),
                "std_dev": round(std_dev, 3),
                "z_score": round(z_score, 3),
                "direction": "above" if z_score > 0 else "below",
            }
        return None

    def detect_trend(self, component_id: str, metric_name: str) -> Optional[dict]:
        """Detect if a metric is trending in a concerning direction."""
        key = f"{component_id}:{metric_name}"
        window = self._get_window(key)

        if len(window) < 20:
            return None

        values = list(window)
        first_half = values[:len(values) // 2]
        second_half = values[len(values) // 2:]

        first_avg = sum(first_half) / len(first_half)
        second_avg = sum(second_half) / len(second_half)

        change_pct = ((second_avg - first_avg) / max(abs(first_avg), 0.001)) * 100

        if abs(change_pct) > 20:  # 20% change is significant
            return {
                "component_id": component_id,
                "metric": metric_name,
                "trend": "increasing" if change_pct > 0 else "decreasing",
                "change_pct": round(change_pct, 1),
                "first_half_avg": round(first_avg, 3),
                "second_half_avg": round(second_avg, 3),
            }
        return None


# ---------------------------------------------------------------------------
# Component health thresholds
# ---------------------------------------------------------------------------

# Thresholds per component type and metric
# Format: {metric_name: (warning_threshold, critical_threshold, direction)}
# direction: "above" means alerting when value exceeds threshold
HEALTH_THRESHOLDS = {
    ComponentType.GAME_SERVER: {
        "cpu_percent": (75.0, 90.0, "above"),
        "memory_percent": (80.0, 95.0, "above"),
        "response_time_ms": (200.0, 500.0, "above"),
        "error_rate": (1.0, 5.0, "above"),
        "active_connections": (8000, 9500, "above"),
        "gc_pause_ms": (50.0, 200.0, "above"),
    },
    ComponentType.PAYMENT_GATEWAY: {
        "response_time_ms": (500.0, 2000.0, "above"),
        "error_rate": (0.5, 2.0, "above"),
        "queue_depth": (100, 500, "above"),
        "ssl_cert_days_remaining": (30.0, 7.0, "below"),
        "transaction_success_rate": (99.0, 95.0, "below"),
    },
    ComponentType.DATABASE: {
        "cpu_percent": (70.0, 85.0, "above"),
        "replication_lag_sec": (5.0, 30.0, "above"),
        "connections_used_pct": (70.0, 90.0, "above"),
        "disk_usage_pct": (75.0, 90.0, "above"),
        "slow_query_count": (10, 50, "above"),
        "deadlock_count": (1, 5, "above"),
    },
    ComponentType.CDN_NODE: {
        "cache_hit_ratio": (85.0, 70.0, "below"),
        "bandwidth_usage_pct": (75.0, 90.0, "above"),
        "error_rate_5xx": (0.5, 2.0, "above"),
        "ttfb_ms": (100.0, 300.0, "above"),
    },
    ComponentType.RNG_SERVICE: {
        "response_time_ms": (10.0, 50.0, "above"),
        "error_rate": (0.01, 0.1, "above"),
        "entropy_quality_score": (95.0, 90.0, "below"),
        "certification_valid_days": (60.0, 14.0, "below"),
    },
    ComponentType.CACHE: {
        "memory_usage_pct": (80.0, 95.0, "above"),
        "eviction_rate": (100, 1000, "above"),
        "hit_ratio": (90.0, 80.0, "below"),
        "connected_clients": (5000, 9000, "above"),
    },
    ComponentType.MESSAGE_QUEUE: {
        "queue_depth": (10000, 50000, "above"),
        "consumer_lag": (1000, 10000, "above"),
        "publish_rate": (50000, 100000, "above"),
        "disk_usage_pct": (70.0, 85.0, "above"),
    },
}


# ---------------------------------------------------------------------------
# Failure predictor
# ---------------------------------------------------------------------------

class FailurePredictor:
    """
    Predicts time-to-failure based on metric trends.
    Uses linear extrapolation on degradation trends.
    """

    def predict_time_to_threshold(
        self,
        recent_values: list[float],
        threshold: float,
        direction: str,
        sample_interval_sec: float = 60.0,
    ) -> Optional[float]:
        """
        Predict hours until a metric crosses its critical threshold.
        Returns None if no concerning trend or insufficient data.
        """
        if len(recent_values) < 10:
            return None

        # Simple linear regression
        n = len(recent_values)
        x_vals = list(range(n))
        x_mean = sum(x_vals) / n
        y_mean = sum(recent_values) / n

        numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_vals, recent_values))
        denominator = sum((x - x_mean) ** 2 for x in x_vals)

        if denominator == 0:
            return None

        slope = numerator / denominator
        intercept = y_mean - slope * x_mean

        # Check if trend is in the concerning direction
        if direction == "above" and slope <= 0:
            return None  # metric is decreasing, no concern
        if direction == "below" and slope >= 0:
            return None

        # Extrapolate: when will threshold be crossed?
        current_value = recent_values[-1]

        if direction == "above":
            if current_value >= threshold:
                return 0.0  # already past threshold
            samples_to_threshold = (threshold - intercept - slope * (n - 1)) / slope
        else:
            if current_value <= threshold:
                return 0.0
            samples_to_threshold = (threshold - intercept - slope * (n - 1)) / slope

        if samples_to_threshold < 0:
            return None  # threshold in the past, not future

        hours = (samples_to_threshold * sample_interval_sec) / 3600.0
        return round(hours, 2)


# ---------------------------------------------------------------------------
# Main predictive maintenance engine
# ---------------------------------------------------------------------------

class PredictiveMaintenanceEngine:
    """
    Central engine that monitors all platform components, detects anomalies,
    predicts failures, and generates actionable maintenance alerts.

    Production architecture:
        Prometheus -> this engine -> PagerDuty + Slack + Grafana annotations
        Run as a background service polling metrics every 30-60 seconds.
    """

    def __init__(self):
        self.detector = StatisticalDetector(window_size=120, z_threshold=2.5)
        self.predictor = FailurePredictor()
        self.components: dict[str, ComponentHealth] = {}
        self.alerts: list[MaintenanceAlert] = []
        self._alert_counter = 0
        self._metric_history: dict[str, list[float]] = {}

    def register_component(self, component_id: str, component_type: ComponentType):
        """Register a component for monitoring."""
        self.components[component_id] = ComponentHealth(
            component_id=component_id,
            component_type=component_type,
            status=HealthStatus.HEALTHY,
            health_score=1.0,
        )
        logger.info(f"Registered component: {component_id} ({component_type.value})")

    def ingest_metric(self, sample: MetricSample) -> list[MaintenanceAlert]:
        """
        Process a single metric sample. Returns any alerts generated.
        Call this for each metric scraped from Prometheus/Datadog.
        """
        alerts = []

        # Track metric history for trend prediction
        history_key = f"{sample.component_id}:{sample.metric_name}"
        if history_key not in self._metric_history:
            self._metric_history[history_key] = []
        self._metric_history[history_key].append(sample.value)

        # Keep last 300 samples (~5 hours at 1-minute intervals)
        if len(self._metric_history[history_key]) > 300:
            self._metric_history[history_key] = self._metric_history[history_key][-300:]

        # Check for statistical anomaly
        anomaly = self.detector.add_sample(
            sample.component_id, sample.metric_name, sample.value
        )
        if anomaly:
            alert = self._create_anomaly_alert(sample, anomaly)
            if alert:
                alerts.append(alert)

        # Check against static thresholds
        threshold_alert = self._check_thresholds(sample)
        if threshold_alert:
            alerts.append(threshold_alert)

        # Check for failure prediction
        prediction_alert = self._check_predictions(sample)
        if prediction_alert:
            alerts.append(prediction_alert)

        # Update component health
        self._update_health(sample)

        self.alerts.extend(alerts)
        return alerts

    def _check_thresholds(self, sample: MetricSample) -> Optional[MaintenanceAlert]:
        """Check if metric exceeds warning/critical thresholds."""
        thresholds = HEALTH_THRESHOLDS.get(sample.component_type, {})
        metric_thresholds = thresholds.get(sample.metric_name)

        if not metric_thresholds:
            return None

        warning_val, critical_val, direction = metric_thresholds
        is_critical = False
        is_warning = False

        if direction == "above":
            is_critical = sample.value >= critical_val
            is_warning = sample.value >= warning_val and not is_critical
        else:
            is_critical = sample.value <= critical_val
            is_warning = sample.value <= warning_val and not is_critical

        if not (is_warning or is_critical):
            return None

        severity = AlertSeverity.CRITICAL if is_critical else AlertSeverity.WARNING
        self._alert_counter += 1

        action = self._get_recommended_action(
            sample.component_type, sample.metric_name, is_critical
        )

        return MaintenanceAlert(
            alert_id=f"ALERT-{self._alert_counter:04d}",
            severity=severity,
            component_id=sample.component_id,
            component_type=sample.component_type,
            title=f"{sample.metric_name} {'CRITICAL' if is_critical else 'WARNING'} "
                  f"on {sample.component_id}",
            description=f"{sample.metric_name} = {sample.value}{sample.unit} "
                       f"(threshold: {critical_val if is_critical else warning_val})",
            predicted_impact=self._predict_impact(sample.component_type, sample.metric_name),
            recommended_action=action,
            confidence=0.95 if is_critical else 0.80,
            timestamp=sample.timestamp,
        )

    def _check_predictions(self, sample: MetricSample) -> Optional[MaintenanceAlert]:
        """Check if metrics are trending toward failure."""
        thresholds = HEALTH_THRESHOLDS.get(sample.component_type, {})
        metric_thresholds = thresholds.get(sample.metric_name)

        if not metric_thresholds:
            return None

        _, critical_val, direction = metric_thresholds
        history_key = f"{sample.component_id}:{sample.metric_name}"
        history = self._metric_history.get(history_key, [])

        hours_to_failure = self.predictor.predict_time_to_threshold(
            history, critical_val, direction
        )

        if hours_to_failure is not None and 0 < hours_to_failure < 4.0:
            # Predicted failure within 4 hours
            self._alert_counter += 1
            return MaintenanceAlert(
                alert_id=f"PREDICT-{self._alert_counter:04d}",
                severity=AlertSeverity.WARNING,
                component_id=sample.component_id,
                component_type=sample.component_type,
                title=f"Predicted failure: {sample.metric_name} on {sample.component_id}",
                description=f"{sample.metric_name} trending toward critical threshold. "
                           f"Estimated {hours_to_failure:.1f} hours until failure.",
                predicted_impact=self._predict_impact(sample.component_type, sample.metric_name),
                recommended_action=self._get_recommended_action(
                    sample.component_type, sample.metric_name, False
                ),
                estimated_time_to_failure=hours_to_failure,
                confidence=0.65,
                timestamp=sample.timestamp,
            )
        return None

    def _create_anomaly_alert(self, sample: MetricSample, anomaly: dict) -> Optional[MaintenanceAlert]:
        """Create alert from statistical anomaly detection."""
        if abs(anomaly["z_score"]) < 3.5:
            return None  # only alert on strong anomalies

        self._alert_counter += 1
        return MaintenanceAlert(
            alert_id=f"ANOMALY-{self._alert_counter:04d}",
            severity=AlertSeverity.WARNING,
            component_id=sample.component_id,
            component_type=sample.component_type,
            title=f"Anomaly detected: {sample.metric_name} on {sample.component_id}",
            description=f"Z-score: {anomaly['z_score']}, value: {sample.value} "
                       f"(mean: {anomaly['mean']}, std: {anomaly['std_dev']})",
            predicted_impact="Unusual behavior detected, may indicate emerging issue",
            recommended_action="Investigate metric deviation; check recent deployments or config changes",
            confidence=0.60,
            timestamp=sample.timestamp,
        )

    def _update_health(self, sample: MetricSample):
        """Update component health score based on latest metrics."""
        component = self.components.get(sample.component_id)
        if not component:
            return

        component.metrics[sample.metric_name] = sample.value

        # Recalculate health score
        thresholds = HEALTH_THRESHOLDS.get(sample.component_type, {})
        penalties = 0.0
        checks = 0

        for metric_name, value in component.metrics.items():
            mt = thresholds.get(metric_name)
            if not mt:
                continue
            warning_val, critical_val, direction = mt
            checks += 1

            if direction == "above":
                if value >= critical_val:
                    penalties += 0.4
                elif value >= warning_val:
                    penalties += 0.15
            else:
                if value <= critical_val:
                    penalties += 0.4
                elif value <= warning_val:
                    penalties += 0.15

        if checks > 0:
            component.health_score = max(0.0, 1.0 - penalties)

        # Update status
        if component.health_score >= 0.8:
            component.status = HealthStatus.HEALTHY
        elif component.health_score >= 0.6:
            component.status = HealthStatus.DEGRADED
        elif component.health_score >= 0.3:
            component.status = HealthStatus.WARNING
        else:
            component.status = HealthStatus.CRITICAL

    def _predict_impact(self, comp_type: ComponentType, metric: str) -> str:
        impacts = {
            ComponentType.GAME_SERVER: "Player sessions may freeze or disconnect",
            ComponentType.PAYMENT_GATEWAY: "Deposits and withdrawals may fail or delay",
            ComponentType.DATABASE: "Platform-wide slowdown or data inconsistency",
            ComponentType.CDN_NODE: "Slow game loading, poor player experience",
            ComponentType.RNG_SERVICE: "Game rounds may stall; regulatory compliance at risk",
            ComponentType.CACHE: "Increased database load, slower response times",
            ComponentType.MESSAGE_QUEUE: "Event processing delays, potential data loss",
        }
        return impacts.get(comp_type, "Service degradation possible")

    def _get_recommended_action(
        self, comp_type: ComponentType, metric: str, is_critical: bool
    ) -> str:
        actions = {
            ("cpu_percent", True): "Scale horizontally immediately; add instances to load balancer",
            ("cpu_percent", False): "Schedule scaling; review recent deployment for regression",
            ("memory_percent", True): "Restart service with memory profiling; check for memory leaks",
            ("disk_usage_pct", True): "Clear logs and temp files; expand storage volume",
            ("disk_usage_pct", False): "Schedule disk cleanup; review log rotation policies",
            ("replication_lag_sec", True): "Check replica health; consider failover if lag > 60s",
            ("response_time_ms", True): "Check for slow queries; verify network connectivity",
            ("error_rate", True): "Review error logs; consider rolling back recent deployment",
            ("entropy_quality_score", False): "Check hardware RNG source; notify certification body",
            ("queue_depth", True): "Scale consumers; check for stuck messages",
            ("ssl_cert_days_remaining", False): "Renew SSL certificate immediately",
        }
        return actions.get(
            (metric, is_critical),
            "Investigate logs and recent changes; consider scaling or restart"
        )

    def get_dashboard_summary(self) -> dict:
        """Generate a summary suitable for operations dashboard."""
        summary = {
            "total_components": len(self.components),
            "healthy": 0,
            "degraded": 0,
            "warning": 0,
            "critical": 0,
            "components": [],
            "recent_alerts": [],
        }

        for comp in self.components.values():
            summary[comp.status.value] = summary.get(comp.status.value, 0) + 1  # ty:ignore[unsupported-operator]
            summary["components"].append({
                "id": comp.component_id,
                "type": comp.component_type.value,
                "status": comp.status.value,
                "health_score": comp.health_score,
                "metrics": comp.metrics,
            })

        summary["recent_alerts"] = [
            {
                "id": a.alert_id,
                "severity": a.severity.value,
                "component": a.component_id,
                "title": a.title,
                "action": a.recommended_action,
                "ttf_hours": a.estimated_time_to_failure,
            }
            for a in self.alerts[-10:]  # last 10 alerts
        ]

        return summary


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def demo():
    """Simulate predictive maintenance monitoring."""
    import random

    engine = PredictiveMaintenanceEngine()

    # Register platform components
    components = [
        ("game-server-eu-1", ComponentType.GAME_SERVER),
        ("game-server-eu-2", ComponentType.GAME_SERVER),
        ("payment-gw-primary", ComponentType.PAYMENT_GATEWAY),
        ("postgres-primary", ComponentType.DATABASE),
        ("postgres-replica-1", ComponentType.DATABASE),
        ("redis-cache-1", ComponentType.CACHE),
        ("cdn-eu-west", ComponentType.CDN_NODE),
        ("rng-service-1", ComponentType.RNG_SERVICE),
        ("kafka-broker-1", ComponentType.MESSAGE_QUEUE),
    ]

    for comp_id, comp_type in components:
        engine.register_component(comp_id, comp_type)

    print("\n" + "=" * 70)
    print("  Predictive Maintenance Simulation")
    print("  Simulating 120 metric samples (2 hours at 1-min intervals)")
    print("=" * 70)

    all_alerts = []
    random.seed(42)

    # Simulate metrics over time with a degrading game server
    for minute in range(120):
        timestamp = f"2026-03-08T{10 + minute // 60:02d}:{minute % 60:02d}:00Z"

        # Game server 1: gradually degrading CPU (simulates memory leak)
        cpu = 45 + minute * 0.35 + random.uniform(-3, 3)
        mem = 50 + minute * 0.30 + random.uniform(-2, 2)

        for metric_name, value, unit in [
            ("cpu_percent", cpu, "%"),
            ("memory_percent", mem, "%"),
            ("response_time_ms", 80 + minute * 0.8 + random.uniform(-10, 10), "ms"),
        ]:
            alerts = engine.ingest_metric(MetricSample(
                timestamp=timestamp,
                component_id="game-server-eu-1",
                component_type=ComponentType.GAME_SERVER,
                metric_name=metric_name,
                value=round(value, 1),
                unit=unit,
            ))
            all_alerts.extend(alerts)

        # Game server 2: healthy
        alerts = engine.ingest_metric(MetricSample(
            timestamp=timestamp,
            component_id="game-server-eu-2",
            component_type=ComponentType.GAME_SERVER,
            metric_name="cpu_percent",
            value=round(35 + random.uniform(-5, 5), 1),
            unit="%",
        ))
        all_alerts.extend(alerts)

        # Database: replication lag spike at minute 80
        rep_lag = 0.5 + random.uniform(0, 0.5)
        if minute > 80:
            rep_lag = 2.0 + (minute - 80) * 0.5 + random.uniform(-0.5, 0.5)

        alerts = engine.ingest_metric(MetricSample(
            timestamp=timestamp,
            component_id="postgres-replica-1",
            component_type=ComponentType.DATABASE,
            metric_name="replication_lag_sec",
            value=round(rep_lag, 2),
            unit="s",
        ))
        all_alerts.extend(alerts)

    # Print alerts
    if all_alerts:
        print(f"\n  Generated {len(all_alerts)} alerts during simulation:\n")
        for alert in all_alerts[:15]:  # show first 15
            icon = {"warning": "WARN", "critical": "CRIT", "emergency": "EMRG"}.get(
                alert.severity.value, "INFO"
            )
            print(f"  [{icon}] {alert.alert_id}: {alert.title}")
            if alert.estimated_time_to_failure:
                print(f"         Time to failure: {alert.estimated_time_to_failure:.1f} hours")
            print(f"         Action: {alert.recommended_action}")
            print()

    # Dashboard summary
    dashboard = engine.get_dashboard_summary()
    print("=" * 70)
    print("  Dashboard Summary")
    print("=" * 70)
    print(f"  Total components: {dashboard['total_components']}")
    print(f"  Healthy: {dashboard['healthy']} | Degraded: {dashboard['degraded']} | "
          f"Warning: {dashboard['warning']} | Critical: {dashboard['critical']}")

    print("\n  Component Health:")
    for comp in dashboard["components"]:
        bar_len = int(comp["health_score"] * 20)
        bar = "#" * bar_len + "." * (20 - bar_len)
        print(f"    {comp['id']:25s} [{bar}] {comp['health_score']:.0%} ({comp['status']})")

    print("\n  Production integration: Prometheus exporter -> this engine -> PagerDuty")
    print("  Run as Kubernetes CronJob every 60s or as streaming Kafka consumer.\n")


if __name__ == "__main__":
    demo()
