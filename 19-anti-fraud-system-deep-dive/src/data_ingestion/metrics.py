# Companion code for "The Backend of Luck" - Chapter 19, Anti-Fraud System Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Metrics collection for the Fraud Detection Data Ingestion Service
"""

from typing import Dict, Any, Optional
import time
from collections import defaultdict

from prometheus_client import Counter, Gauge, Histogram, CollectorRegistry


class MetricsCollector:
    """Prometheus metrics collector for data ingestion service"""

    def __init__(self, registry: Optional[CollectorRegistry] = None):
        self.registry = registry or CollectorRegistry()

        # Counters
        self.events_ingested = Counter(
            'fraud_detection_events_ingested_total',
            'Total number of events ingested',
            ['type', 'source'],
            registry=self.registry
        )

        self.ingestion_errors = Counter(
            'fraud_detection_ingestion_errors_total',
            'Total number of ingestion errors',
            ['type', 'error_type'],
            registry=self.registry
        )

        self.data_validation_errors = Counter(
            'fraud_detection_data_validation_errors_total',
            'Total number of data validation errors',
            ['type', 'field'],
            registry=self.registry
        )

        self.api_requests = Counter(
            'fraud_detection_api_requests_total',
            'Total number of API requests',
            ['method', 'endpoint', 'status'],
            registry=self.registry
        )

        # Gauges
        self.active_connections = Gauge(
            'fraud_detection_active_connections',
            'Number of active connections',
            registry=self.registry
        )

        self.queue_size = Gauge(
            'fraud_detection_queue_size',
            'Current queue size',
            ['queue_type'],
            registry=self.registry
        )

        # Histograms
        self.request_duration = Histogram(
            'fraud_detection_request_duration_seconds',
            'Request duration in seconds',
            ['method', 'endpoint'],
            buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0),
            registry=self.registry
        )

        self.event_processing_duration = Histogram(
            'fraud_detection_event_processing_duration_seconds',
            'Event processing duration in seconds',
            ['event_type'],
            buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0),
            registry=self.registry
        )

        self.kafka_publish_duration = Histogram(
            'fraud_detection_kafka_publish_duration_seconds',
            'Kafka publish duration in seconds',
            ['topic'],
            buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0),
            registry=self.registry
        )

        # In-memory metrics for calculations
        self._counter_values = defaultdict(int)
        self._histogram_values = defaultdict(list)

    def _normalize_labels(self, metric_name: str, labels: Dict[str, str]) -> Dict[str, str]:
        """Map simplified test labels to the concrete Prometheus label schema."""

        normalized = labels.copy()

        if metric_name == 'events_ingested_total':
            normalized.setdefault('type', normalized.get('event_type', 'unknown'))
            normalized.setdefault('source', 'unknown')
        elif metric_name == 'ingestion_errors_total':
            normalized.setdefault('type', 'unknown')
            normalized.setdefault('error_type', 'unknown')
        elif metric_name == 'data_validation_errors_total':
            normalized.setdefault('type', 'unknown')
            normalized.setdefault('field', 'unknown')
        elif metric_name == 'event_processing_duration_seconds':
            event_type = normalized.pop('type', normalized.get('event_type', 'unknown'))
            normalized['event_type'] = event_type

        return normalized

    def increment_counter(self, name: str, labels: Optional[Dict[str, str]] = None,
                         value: int = 1):
        """
        Increment a counter metric

        Args:
            name: Counter name
            labels: Label values
            value: Increment value
        """

        labels = labels or {}
        prom_labels = self._normalize_labels(name, labels)

        # Update Prometheus counter
        if name == 'events_ingested_total':
            self.events_ingested.labels(**prom_labels).inc(value)
        elif name == 'ingestion_errors_total':
            self.ingestion_errors.labels(**prom_labels).inc(value)
        elif name == 'data_validation_errors_total':
            self.data_validation_errors.labels(**prom_labels).inc(value)
        elif name == 'api_requests_total':
            self.api_requests.labels(**prom_labels).inc(value)

        # Update in-memory counter for calculations
        key = f"{name}_{labels}"
        self._counter_values[key] += value

    def set_gauge(self, name: str, value: float,
                 labels: Optional[Dict[str, str]] = None):
        """
        Set a gauge metric value

        Args:
            name: Gauge name
            value: Gauge value
            labels: Label values
        """

        labels = labels or {}

        if name == 'active_connections':
            self.active_connections.set(value)
        elif name == 'queue_size':
            self.queue_size.labels(**labels).set(value)

    def observe_histogram(self, name: str, value: float,
                         labels: Optional[Dict[str, str]] = None):
        """
        Observe a histogram metric value

        Args:
            name: Histogram name
            value: Observed value
            labels: Label values
        """

        labels = labels or {}
        metric_aliases = {
            'event_processing_duration': 'event_processing_duration_seconds',
        }
        metric_name = metric_aliases.get(name, name)
        prom_labels = self._normalize_labels(metric_name, labels)

        # Update Prometheus histogram
        if metric_name == 'request_duration_seconds':
            self.request_duration.labels(**prom_labels).observe(value)
        elif metric_name == 'event_processing_duration_seconds':
            self.event_processing_duration.labels(**prom_labels).observe(value)
        elif metric_name == 'kafka_publish_duration_seconds':
            self.kafka_publish_duration.labels(**prom_labels).observe(value)

        # Update in-memory histogram for calculations
        key = f"{metric_name}_{labels}"
        self._histogram_values[key].append(value)

        # Keep only last 1000 values to prevent memory issues
        if len(self._histogram_values[key]) > 1000:
            self._histogram_values[key] = self._histogram_values[key][-1000:]

    def get_counter_value(self, name: str, labels: Optional[Dict[str, str]] = None) -> int:
        """
        Get current counter value

        Args:
            name: Counter name
            labels: Label values

        Returns:
            Counter value
        """

        labels = labels or {}
        key = f"{name}_{labels}"
        return self._counter_values[key]

    def get_histogram_avg(self, name: str, labels: Optional[Dict[str, str]] = None) -> float:
        """
        Get histogram average value

        Args:
            name: Histogram name
            labels: Label values

        Returns:
            Average value
        """

        labels = labels or {}
        key = f"{name}_{labels}"
        values = self._histogram_values[key]

        return sum(values) / len(values) if values else 0.0

    def get_histogram_percentile(self, name: str, percentile: float,
                                labels: Optional[Dict[str, str]] = None) -> float:
        """
        Get histogram percentile value

        Args:
            name: Histogram name
            percentile: Percentile (0.0-1.0)
            labels: Label values

        Returns:
            Percentile value
        """

        labels = labels or {}
        key = f"{name}_{labels}"
        values = sorted(self._histogram_values[key])

        if not values:
            return 0.0

        index = int(len(values) * percentile)
        index = min(index, len(values) - 1)

        return values[index]

    def time_request(self, method: str, endpoint: str):
        """
        Context manager for timing requests

        Args:
            method: HTTP method
            endpoint: API endpoint

        Returns:
            Context manager
        """

        return _Timer(self.request_duration, {'method': method, 'endpoint': endpoint})

    def time_event_processing(self, event_type: str):
        """
        Context manager for timing event processing

        Args:
            event_type: Type of event being processed

        Returns:
            Context manager
        """

        return _Timer(self.event_processing_duration, {'event_type': event_type})

    def time_kafka_publish(self, topic: str):
        """
        Context manager for timing Kafka publishes

        Args:
            topic: Kafka topic

        Returns:
            Context manager
        """

        return _Timer(self.kafka_publish_duration, {'topic': topic})

    def get_summary_stats(self) -> Dict[str, Any]:
        """
        Get summary statistics for all metrics

        Returns:
            Summary statistics
        """

        return {
            'events_ingested': {
                'total': sum(self._counter_values.get(k, 0)
                           for k in self._counter_values.keys()
                           if k.startswith('events_ingested_total')),
                'by_type': {
                    k.replace('events_ingested_total_', ''): v
                    for k, v in self._counter_values.items()
                    if k.startswith('events_ingested_total')
                }
            },
            'errors': {
                'total': sum(self._counter_values.get(k, 0)
                           for k in self._counter_values.keys()
                           if 'errors_total' in k),
                'by_type': {
                    k: v for k, v in self._counter_values.items()
                    if 'errors_total' in k
                }
            },
            'performance': {
                'avg_request_duration': self.get_histogram_avg('request_duration_seconds'),
                'p95_request_duration': self.get_histogram_percentile('request_duration_seconds', 0.95),
                'avg_event_processing': self.get_histogram_avg('event_processing_duration_seconds'),
                'avg_kafka_publish': self.get_histogram_avg('kafka_publish_duration_seconds')
            }
        }


class _Timer:
    """Context manager for timing operations"""

    def __init__(self, histogram, labels):
        self.histogram = histogram
        self.labels = labels
        self.start_time = None

    def __enter__(self):
        self.start_time = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.start_time is not None:
            duration = time.time() - self.start_time
            self.histogram.labels(**self.labels).observe(duration)
