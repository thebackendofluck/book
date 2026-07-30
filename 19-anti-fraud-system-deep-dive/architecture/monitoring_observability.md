# Monitoring and Observability Architecture

## Overview

This document outlines the comprehensive monitoring and observability system for the real-time anti-fraud detection platform. The system provides end-to-end visibility into application performance, business metrics, and infrastructure health with automated alerting and intelligent anomaly detection.

## Monitoring Architecture Overview

```mermaid
graph TB
    subgraph "Data Collection Layer"
        A1[Application Metrics<br/>Custom Business Metrics]
        A2[Infrastructure Metrics<br/>System Resources]
        A3[Business Metrics<br/>Fraud Detection KPIs]
        A4[External Metrics<br/>Third-party APIs]
    end

    subgraph "Metrics Pipeline"
        B1[OpenTelemetry Collectors<br/>Standardized Collection]
        B2[Prometheus<br/>Time Series Database]
        B3[Telegraf<br/>Infrastructure Agent]
        B4[StatsD<br/>Application Metrics]
    end

    subgraph "Log Aggregation"
        C1[Fluent Bit<br/>Log Collection]
        C2[Elasticsearch<br/>Log Storage]
        C3[Kibana<br/>Log Visualization]
        C4[Log Correlation<br/>Distributed Tracing]
    end

    subgraph "Visualization & Alerting"
        D1[Grafana<br/>Dashboards]
        D2[Alert Manager<br/>Alert Routing]
        D3[Synthetic Monitoring<br/>End-to-End Tests]
        D4[Anomaly Detection<br/>ML-based Monitoring]
    end

    subgraph "Analytics & Intelligence"
        E1[Metrics Correlation<br/>Root Cause Analysis]
        E2[Predictive Analytics<br/>Failure Prediction]
        E3[Automated Actions<br/>Self-healing]
        E4[Reporting<br/>Business Intelligence]
    end

    A1 --> B1
    A2 --> B3
    A3 --> B4
    A4 --> B1

    B1 --> B2
    B3 --> B2
    B4 --> B2

    C1 --> C2
    C2 --> C3
    C2 --> C4

    B2 --> D1
    B2 --> D2
    C2 --> D1
    D1 --> D3

    D2 --> E1
    E1 --> E2
    E2 --> E3
    E3 --> E4
```

## Metrics Collection Strategy

### Application Metrics

```python
from prometheus_client import Counter, Histogram, Gauge, Summary
import time
from typing import Dict, Any

class FraudDetectionMetrics:
    """Application metrics for fraud detection system"""

    def __init__(self):
        # Request metrics
        self.request_total = Counter(
            'fraud_detection_requests_total',
            'Total number of requests processed',
            ['endpoint', 'method', 'status']
        )

        self.request_duration = Histogram(
            'fraud_detection_request_duration_seconds',
            'Request duration in seconds',
            ['endpoint', 'method'],
            buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0]
        )

        # Fraud detection metrics
        self.fraud_score_distribution = Histogram(
            'fraud_detection_score_distribution',
            'Distribution of fraud scores',
            buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
        )

        self.alerts_generated = Counter(
            'fraud_detection_alerts_total',
            'Total number of alerts generated',
            ['category', 'severity']
        )

        # Model performance metrics
        self.model_prediction_time = Histogram(
            'fraud_detection_model_prediction_seconds',
            'Time taken for model predictions',
            ['model_type'],
            buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5]
        )

        self.model_accuracy = Gauge(
            'fraud_detection_model_accuracy',
            'Current model accuracy',
            ['model_type']
        )

        # Data processing metrics
        self.data_processed_total = Counter(
            'fraud_detection_data_processed_total',
            'Total amount of data processed',
            ['data_type', 'pipeline_stage']
        )

        self.processing_lag = Gauge(
            'fraud_detection_processing_lag_seconds',
            'Current processing lag in seconds',
            ['pipeline_stage']
        )

        # Business metrics
        self.transactions_processed = Counter(
            'fraud_detection_transactions_processed_total',
            'Total transactions processed'
        )

        self.fraud_detected = Counter(
            'fraud_detection_fraud_detected_total',
            'Total fraudulent transactions detected'
        )

        self.false_positives = Counter(
            'fraud_detection_false_positives_total',
            'Total false positive detections'
        )

    def record_request(self, endpoint: str, method: str, status: int, duration: float):
        """Record an API request"""
        self.request_total.labels(endpoint=endpoint, method=method, status=status).inc()
        self.request_duration.labels(endpoint=endpoint, method=method).observe(duration)

    def record_fraud_score(self, score: float):
        """Record a fraud score"""
        self.fraud_score_distribution.observe(score)

    def record_alert(self, category: str, severity: int):
        """Record an alert generation"""
        self.alerts_generated.labels(category=category, severity=severity).inc()

    def record_model_prediction(self, model_type: str, prediction_time: float):
        """Record model prediction timing"""
        self.model_prediction_time.labels(model_type=model_type).observe(prediction_time)

    def update_model_accuracy(self, model_type: str, accuracy: float):
        """Update model accuracy metric"""
        self.model_accuracy.labels(model_type=model_type).set(accuracy)

    def record_data_processed(self, data_type: str, pipeline_stage: str, amount: int):
        """Record data processing volume"""
        self.data_processed_total.labels(
            data_type=data_type,
            pipeline_stage=pipeline_stage
        ).inc(amount)

    def update_processing_lag(self, pipeline_stage: str, lag_seconds: float):
        """Update processing lag"""
        self.processing_lag.labels(pipeline_stage=pipeline_stage).set(lag_seconds)

    def record_transaction(self, is_fraud: bool = False):
        """Record transaction processing"""
        self.transactions_processed.inc()
        if is_fraud:
            self.fraud_detected.inc()

    def record_false_positive(self):
        """Record a false positive"""
        self.false_positives.inc()

# Global metrics instance
metrics = FraudDetectionMetrics()

# Usage in application code
class FraudDetectionAPI:
    """Example API with metrics instrumentation"""

    async def predict_fraud(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.time()

        try:
            # Process request
            result = await self._process_prediction(request_data)

            # Record metrics
            duration = time.time() - start_time
            metrics.record_request('/api/v1/predict', 'POST', 200, duration)
            metrics.record_fraud_score(result['fraud_score'])
            metrics.record_transaction(is_fraud=result['is_fraud'])

            if result['is_fraud']:
                metrics.record_alert('fraud', 4)  # High severity

            return result

        except Exception as e:
            duration = time.time() - start_time
            metrics.record_request('/api/v1/predict', 'POST', 500, duration)
            raise e

    async def _process_prediction(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Mock prediction processing"""
        # Simulate processing time
        await asyncio.sleep(0.01)

        # Mock fraud score
        fraud_score = 0.15  # Low risk

        return {
            'fraud_score': fraud_score,
            'is_fraud': fraud_score > 0.8,
            'confidence': 0.95
        }
```

### Infrastructure Metrics

```yaml
# telegraf-config.yaml
# Telegraf configuration for infrastructure metrics
global_tags:
  environment: "production"
  service: "fraud-detection"

agent:
  interval: "10s"
  round_interval: true
  metric_batch_size: 1000
  metric_buffer_limit: 10000
  collection_jitter: "0s"
  flush_interval: "10s"
  flush_jitter: "0s"
  precision: ""
  hostname: ""
  omit_hostname: false

inputs:
  # System metrics
  - cpu:
      percpu: true
      totalcpu: true
      collect_cpu_time: false
      report_active: false

  - disk:
      ignore_fs: ["tmpfs", "devtmpfs", "devfs", "overlay", "aufs", "squashfs"]

  - diskio:
      devices: ["sda", "sdb", "nvme0n1"]
      skip_serial_number: false

  - mem:
      # No configuration needed

  - net:
      interfaces: ["eth0", "eth1"]

  - netstat:
      # No configuration needed

  # Kafka metrics
  - kafka_consumer:
      brokers: ["kafka-cluster:9092"]
      topics: ["transactions", "user_events", "game_events"]
      consumer_group: "telegraf-monitoring"
      offset_reset_policy: "earliest"
      max_message_len: 1000000
      metadata_refresh_interval: "30s"

  # Redis metrics
  - redis:
      servers: ["tcp://redis-cluster:6379"]
      password: "${REDIS_PASSWORD}"

  # PostgreSQL metrics
  - postgresql:
      address: "postgres://user:password@postgresql:5432/fraud_detection"
      max_lifetime: "0s"

outputs:
  - prometheus_client:
      listen: ":9273"
      path: "/metrics"
      collectors_exclude: ["gocollector", "process"]
      export_timestamp: true
```

## Log Aggregation and Analysis

### Structured Logging Configuration

```python
import logging
import json
import sys
from datetime import datetime
from typing import Dict, Any, Optional

class StructuredLogger:
    """Structured logging with correlation IDs and context"""

    def __init__(self, service_name: str, log_level: str = "INFO"):
        self.service_name = service_name
        self.logger = logging.getLogger(service_name)
        self.logger.setLevel(getattr(logging, log_level))

        # Remove existing handlers
        self.logger.handlers = []

        # Create structured formatter
        formatter = StructuredFormatter()

        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        self.logger.addHandler(console_handler)

    def log_request(self, method: str, url: str, status_code: int,
                   duration: float, user_id: Optional[str] = None,
                   correlation_id: Optional[str] = None):
        """Log HTTP request"""

        extra = {
            'event_type': 'http_request',
            'method': method,
            'url': url,
            'status_code': status_code,
            'duration': duration,
            'user_id': user_id,
            'correlation_id': correlation_id
        }

        if status_code >= 400:
            self.logger.error(f"HTTP {method} {url} failed with {status_code}", extra=extra)
        else:
            self.logger.info(f"HTTP {method} {url} completed", extra=extra)

    def log_fraud_prediction(self, player_id: str, fraud_score: float,
                           is_fraud: bool, model_version: str,
                           correlation_id: Optional[str] = None):
        """Log fraud prediction"""

        extra = {
            'event_type': 'fraud_prediction',
            'player_id': player_id,
            'fraud_score': fraud_score,
            'is_fraud': is_fraud,
            'model_version': model_version,
            'correlation_id': correlation_id
        }

        if is_fraud:
            self.logger.warning(f"Fraud detected for player {player_id}", extra=extra)
        else:
            self.logger.info(f"Normal activity for player {player_id}", extra=extra)

    def log_alert_generated(self, alert_id: str, alert_type: str,
                          severity: int, player_id: Optional[str] = None,
                          correlation_id: Optional[str] = None):
        """Log alert generation"""

        extra = {
            'event_type': 'alert_generated',
            'alert_id': alert_id,
            'alert_type': alert_type,
            'severity': severity,
            'player_id': player_id,
            'correlation_id': correlation_id
        }

        self.logger.warning(f"Alert generated: {alert_type}", extra=extra)

    def log_error(self, error: Exception, context: Optional[Dict[str, Any]] = None,
                 correlation_id: Optional[str] = None):
        """Log application error"""

        extra = {
            'event_type': 'application_error',
            'error_type': type(error).__name__,
            'error_message': str(error),
            'correlation_id': correlation_id
        }

        if context:
            extra.update(context)

        self.logger.error(f"Application error: {error}", extra=extra)

class StructuredFormatter(logging.Formatter):
    """Custom formatter for structured logging"""

    def format(self, record: logging.LogRecord) -> str:
        # Create base log entry
        log_entry = {
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'level': record.levelname,
            'service': getattr(record, 'service', 'unknown'),
            'message': record.getMessage(),
            'logger': record.name
        }

        # Add extra fields
        if hasattr(record, 'event_type'):
            log_entry['event_type'] = record.event_type

        # Add any additional fields from record
        for key, value in record.__dict__.items():
            if key not in ['name', 'msg', 'args', 'levelname', 'levelno',
                          'pathname', 'filename', 'module', 'exc_info',
                          'exc_text', 'stack_info', 'lineno', 'funcName',
                          'created', 'msecs', 'relativeCreated', 'thread',
                          'threadName', 'processName', 'process', 'message']:
                log_entry[key] = value

        return json.dumps(log_entry, default=str)

# Global logger instance
logger = StructuredLogger("fraud-detection-api")

# Usage examples
async def handle_prediction_request(request):
    correlation_id = request.headers.get('X-Correlation-ID', str(uuid.uuid4()))

    start_time = time.time()
    try:
        # Process request
        result = await process_prediction(request.data)

        duration = time.time() - start_time
        logger.log_request(
            method=request.method,
            url=request.url,
            status_code=200,
            duration=duration,
            user_id=result.get('player_id'),
            correlation_id=correlation_id
        )

        logger.log_fraud_prediction(
            player_id=result['player_id'],
            fraud_score=result['fraud_score'],
            is_fraud=result['is_fraud'],
            model_version="v1.2.3",
            correlation_id=correlation_id
        )

        return result

    except Exception as e:
        duration = time.time() - start_time
        logger.log_request(
            method=request.method,
            url=request.url,
            status_code=500,
            duration=duration,
            correlation_id=correlation_id
        )

        logger.log_error(e, {'url': request.url}, correlation_id)
        raise e
```

### Log Pipeline Configuration

```yaml
# fluent-bit-config.yaml
# Fluent Bit configuration for log aggregation
service:
  daemon: off
  log_level: info
  parsers_file: parsers.conf

pipeline:
  inputs:
    - name: tail
      path: /var/log/containers/*fraud-detection*.log
      parser: docker
      tag: fraud-detection.*
      mem_buf_limit: 5MB

    - name: tail
      path: /var/log/fraud-detection/*.log
      parser: json
      tag: fraud-detection.app.*
      mem_buf_limit: 5MB

  filters:
    - name: grep
      match: fraud-detection.*
      regex: log .

    - name: lua
      match: fraud-detection.*
      script: enrich.lua
      call: enrich_log

    - name: record_modifier
      match: fraud-detection.*
      records:
        - cluster: fraud-detection-prod
        - environment: production

  outputs:
    - name: es
      match: fraud-detection.*
      host: elasticsearch
      port: 9200
      index: fraud-detection-logs
      type: _doc
      logstash_format: on
      logstash_prefix: fraud-detection
      replace_dots: on
      retry_limit: 3

    - name: stdout
      match: fraud-detection.*
      format: json_lines
```

## Dashboard and Visualization

### Grafana Dashboard Configuration

```json
// grafana-dashboard.json
{
  "dashboard": {
    "title": "Fraud Detection System Overview",
    "tags": ["fraud-detection", "production"],
    "timezone": "UTC",
    "refresh": "30s",
    "time": {
      "from": "now-1h",
      "to": "now"
    },
    "panels": [
      {
        "title": "Request Rate",
        "type": "graph",
        "targets": [
          {
            "expr": "rate(fraud_detection_requests_total[5m])",
            "legendFormat": "{{endpoint}} {{method}}"
          }
        ],
        "yAxes": [
          {
            "unit": "reqps",
            "min": 0
          }
        ]
      },
      {
        "title": "Fraud Score Distribution",
        "type": "histogram",
        "targets": [
          {
            "expr": "fraud_detection_score_distribution_bucket",
            "legendFormat": "{{le}}"
          }
        ]
      },
      {
        "title": "Alert Rate by Category",
        "type": "barchart",
        "targets": [
          {
            "expr": "rate(fraud_detection_alerts_total[1h])",
            "legendFormat": "{{category}}"
          }
        ]
      },
      {
        "title": "Model Performance",
        "type": "table",
        "targets": [
          {
            "expr": "fraud_detection_model_accuracy",
            "legendFormat": "{{model_type}}"
          }
        ]
      },
      {
        "title": "System Resources",
        "type": "row",
        "panels": [
          {
            "title": "CPU Usage",
            "type": "graph",
            "targets": [
              {
                "expr": "100 - (avg by(instance) (irate(node_cpu_seconds_total{mode=\"idle\"}[5m])) * 100)",
                "legendFormat": "{{instance}}"
              }
            ]
          },
          {
            "title": "Memory Usage",
            "type": "graph",
            "targets": [
              {
                "expr": "(1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100",
                "legendFormat": "{{instance}}"
              }
            ]
          }
        ]
      },
      {
        "title": "Business Metrics",
        "type": "stat",
        "targets": [
          {
            "expr": "rate(fraud_detection_fraud_detected_total[1h]) / rate(fraud_detection_transactions_processed_total[1h]) * 100",
            "legendFormat": "Fraud Rate %"
          }
        ],
        "fieldConfig": {
          "defaults": {
            "unit": "percent"
          }
        }
      }
    ]
  }
}
```

## Alerting and Anomaly Detection

### Prometheus Alerting Rules

```yaml
# prometheus-alerts.yaml
groups:
  - name: fraud_detection_alerts
    rules:
      - alert: HighFraudScoreRate
        expr: rate(fraud_detection_score_distribution_bucket{le="0.9"}[5m]) / rate(fraud_detection_requests_total[5m]) > 0.1
        for: 5m
        labels:
          severity: critical
          service: fraud-detection
        annotations:
          summary: "High fraud score rate detected"
          description: "Fraud score rate is {{ $value }}% over the last 5 minutes"

      - alert: HighErrorRate
        expr: rate(fraud_detection_requests_total{status=~"5.."}[5m]) / rate(fraud_detection_requests_total[5m]) > 0.05
        for: 5m
        labels:
          severity: warning
          service: fraud-detection
        annotations:
          summary: "High error rate detected"
          description: "Error rate is {{ $value }}% over the last 5 minutes"

      - alert: HighLatency
        expr: histogram_quantile(0.95, rate(fraud_detection_request_duration_seconds_bucket[5m])) > 1.0
        for: 5m
        labels:
          severity: warning
          service: fraud-detection
        annotations:
          summary: "High request latency detected"
          description: "95th percentile latency is {{ $value }}s over the last 5 minutes"

      - alert: ModelAccuracyDrop
        expr: fraud_detection_model_accuracy < 0.8
        for: 10m
        labels:
          severity: critical
          service: fraud-detection
        annotations:
          summary: "Model accuracy dropped"
          description: "Model accuracy for {{ $labels.model_type }} is {{ $value }}"

      - alert: ProcessingLagHigh
        expr: fraud_detection_processing_lag_seconds > 300
        for: 5m
        labels:
          severity: warning
          service: fraud-detection
        annotations:
          summary: "High processing lag detected"
          description: "Processing lag is {{ $value }} seconds for {{ $labels.pipeline_stage }}"
```

### Anomaly Detection with Machine Learning

```python
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
from prometheus_api_client import PrometheusConnect
import time

class MetricsAnomalyDetector:
    """ML-based anomaly detection for metrics"""

    def __init__(self, prometheus_url: str = "http://prometheus:9090"):
        self.prometheus = PrometheusConnect(url=prometheus_url)
        self.models = {}
        self.scalers = {}
        self.training_data = {}

    def train_anomaly_model(self, metric_name: str, lookback_hours: int = 24):
        """Train anomaly detection model for a metric"""

        # Fetch historical data
        end_time = time.time()
        start_time = end_time - (lookback_hours * 3600)

        metric_data = self.prometheus.get_metric_range_data(
            metric_name,
            start_time=start_time,
            end_time=end_time
        )

        if not metric_data:
            print(f"No data found for metric {metric_name}")
            return

        # Extract values
        values = []
        for series in metric_data:
            for point in series['values']:
                values.append(float(point[1]))

        if len(values) < 100:
            print(f"Insufficient data for training: {len(values)} points")
            return

        # Prepare training data
        df = pd.DataFrame({'value': values})
        df['rolling_mean'] = df['value'].rolling(window=10).mean()
        df['rolling_std'] = df['value'].rolling(window=10).std()
        df['diff'] = df['value'].diff()
        df = df.dropna()

        # Scale features
        scaler = StandardScaler()
        features = ['value', 'rolling_mean', 'rolling_std', 'diff']
        scaled_features = scaler.fit_transform(df[features])

        # Train Isolation Forest
        model = IsolationForest(
            contamination=0.05,  # 5% expected anomalies
            random_state=42,
            n_estimators=100
        )
        model.fit(scaled_features)

        # Store model and scaler
        self.models[metric_name] = model
        self.scalers[metric_name] = scaler
        self.training_data[metric_name] = df

        print(f"Trained anomaly model for {metric_name}")

    def detect_anomalies(self, metric_name: str, current_values: list) -> list:
        """Detect anomalies in current metric values"""

        if metric_name not in self.models:
            return []

        model = self.models[metric_name]
        scaler = self.scalers[metric_name]

        # Prepare features for current values
        df_current = pd.DataFrame({'value': current_values})
        df_current['rolling_mean'] = df_current['value'].rolling(window=10).mean()
        df_current['rolling_std'] = df_current['value'].rolling(window=10).std()
        df_current['diff'] = df_current['value'].diff()
        df_current = df_current.dropna()

        if df_current.empty:
            return []

        # Scale and predict
        features = ['value', 'rolling_mean', 'rolling_std', 'diff']
        scaled_features = scaler.transform(df_current[features])
        predictions = model.predict(scaled_features)

        # Return indices of anomalies (-1 = anomaly)
        anomaly_indices = [i for i, pred in enumerate(predictions) if pred == -1]

        return anomaly_indices

    def get_anomaly_score(self, metric_name: str, value: float) -> float:
        """Get anomaly score for a single value"""

        if metric_name not in self.models:
            return 0.0

        model = self.models[metric_name]
        scaler = self.scalers[metric_name]

        # Create feature vector (simplified)
        features = np.array([[value, value, 0, 0]])  # Simplified features
        scaled_features = scaler.transform(features)

        # Get anomaly score (negative = more anomalous)
        score = model.score_samples(scaled_features)[0]

        # Convert to 0-1 scale (1 = most anomalous)
        return (score - (-1)) / (1 - (-1))  # Normalize from [-1, 1] to [0, 1]

# Usage
anomaly_detector = MetricsAnomalyDetector()

# Train models for key metrics
anomaly_detector.train_anomaly_model("fraud_detection_request_duration_seconds", lookback_hours=24)
anomaly_detector.train_anomaly_model("fraud_detection_fraud_detected_total", lookback_hours=24)

# Detect anomalies in real-time
latency_values = [0.1, 0.15, 0.12, 2.5, 0.11]  # 2.5s is anomalous
anomalies = anomaly_detector.detect_anomalies("fraud_detection_request_duration_seconds", latency_values)
print(f"Anomalies detected at indices: {anomalies}")
```

## Distributed Tracing

### Jaeger Configuration

```yaml
# jaeger-config.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: jaeger
  namespace: monitoring
spec:
  replicas: 1
  selector:
    matchLabels:
      app: jaeger
  template:
    metadata:
      labels:
        app: jaeger
    spec:
      containers:
      - name: jaeger
        image: jaegertracing/all-in-one:latest
        ports:
        - containerPort: 16686
        - containerPort: 14268
        env:
        - name: COLLECTOR_OTLP_ENABLED
          value: "true"
        - name: COLLECTOR_ZIPKIN_HOST_PORT
          value: ":9411"

---
apiVersion: v1
kind: Service
metadata:
  name: jaeger
  namespace: monitoring
spec:
  selector:
    app: jaeger
  ports:
  - name: ui
    port: 16686
    targetPort: 16686
  - name: collector
    port: 14268
    targetPort: 14268
  type: ClusterIP
```

### OpenTelemetry Instrumentation

```python
from opentelemetry import trace
from opentelemetry.exporter.jaeger import JaegerExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
import fastapi

# Configure tracing
trace.set_tracer_provider(TracerProvider())
tracer = trace.get_tracer(__name__)

# Configure Jaeger exporter
jaeger_exporter = JaegerExporter(
    agent_host_name="jaeger",
    agent_port=14268,
)

# Add span processor
span_processor = BatchSpanProcessor(jaeger_exporter)
trace.get_tracer_provider().add_span_processor(span_processor)

# Instrument FastAPI
app = fastapi.FastAPI()

@app.on_event("startup")
async def startup_event():
    FastAPIInstrumentor.instrument_app(app)
    RequestsInstrumentor().instrument()

@app.get("/api/v1/predict")
async def predict_fraud(request_data: dict):
    with tracer.start_as_current_span("fraud_prediction") as span:
        span.set_attribute("player_id", request_data.get("player_id"))
        span.set_attribute("amount", request_data.get("amount"))

        # Add custom span for model inference
        with tracer.start_as_current_span("model_inference") as child_span:
            child_span.set_attribute("model_version", "v1.2.3")

            # Simulate model prediction
            import time
            time.sleep(0.01)  # Mock processing time

            result = {"fraud_score": 0.15, "is_fraud": False}

            child_span.set_attribute("fraud_score", result["fraud_score"])
            child_span.set_attribute("prediction_time", 0.01)

        span.set_attribute("result", str(result))
        return result
```

## Automated Actions and Self-Healing

### Auto-Scaling Configuration

```yaml
# keda-scaledobject.yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: fraud-detection-scaler
  namespace: fraud-detection
spec:
  scaleTargetRef:
    name: fraud-detection-deployment
  pollingInterval: 30
  cooldownPeriod: 300
  minReplicaCount: 3
  maxReplicaCount: 20
  triggers:
  - type: prometheus
    metadata:
      serverAddress: http://prometheus:9090
      metricName: fraud_detection_requests_total
      query: rate(fraud_detection_requests_total[5m])
      threshold: "100"
  - type: prometheus
    metadata:
      serverAddress: http://prometheus:9090
      metricName: fraud_detection_processing_lag_seconds
      query: fraud_detection_processing_lag_seconds
      threshold: "60"
```

### Automated Incident Response

```python
from typing import Dict, Any, List
import asyncio
import aiohttp

class AutomatedIncidentResponder:
    """Automated incident response system"""

    def __init__(self):
        self.actions = {
            "high_latency": self._scale_up_service,
            "high_error_rate": self._restart_service,
            "model_accuracy_drop": self._rollback_model,
            "disk_space_low": self._cleanup_logs
        }

    async def respond_to_alert(self, alert: Dict[str, Any]):
        """Respond to an alert with automated actions"""

        alert_name = alert.get("labels", {}).get("alertname")
        severity = alert.get("labels", {}).get("severity")

        if severity not in ["critical", "warning"]:
            return

        action_func = self.actions.get(alert_name)
        if action_func:
            try:
                await action_func(alert)
                print(f"Executed automated action for {alert_name}")
            except Exception as e:
                print(f"Failed to execute automated action: {e}")

    async def _scale_up_service(self, alert: Dict[str, Any]):
        """Scale up a service in response to high load"""

        service_name = alert.get("labels", {}).get("service", "fraud-detection")

        # Kubernetes API call to scale deployment
        async with aiohttp.ClientSession() as session:
            scale_payload = {
                "spec": {
                    "replicas": 10  # Scale to 10 replicas
                }
            }

            headers = {
                "Authorization": f"Bearer {os.getenv('KUBE_TOKEN')}",
                "Content-Type": "application/json"
            }

            url = f"https://kubernetes.default.svc/api/v1/namespaces/fraud-detection/deployments/{service_name}/scale"

            async with session.patch(url, json=scale_payload, headers=headers) as response:
                if response.status == 200:
                    print(f"Scaled up {service_name} to 10 replicas")
                else:
                    print(f"Failed to scale {service_name}: {response.status}")

    async def _restart_service(self, alert: Dict[str, Any]):
        """Restart a service in response to errors"""

        service_name = alert.get("labels", {}).get("service", "fraud-detection")

        # Trigger rolling restart
        async with aiohttp.ClientSession() as session:
            restart_payload = {
                "spec": {
                    "template": {
                        "metadata": {
                            "annotations": {
                                "kubectl.kubernetes.io/restartedAt": datetime.utcnow().isoformat()
                            }
                        }
                    }
                }
            }

            headers = {
                "Authorization": f"Bearer {os.getenv('KUBE_TOKEN')}",
                "Content-Type": "application/json"
            }

            url = f"https://kubernetes.default.svc/apis/apps/v1/namespaces/fraud-detection/deployments/{service_name}"

            async with session.patch(url, json=restart_payload, headers=headers) as response:
                if response.status == 200:
                    print(f"Restarted {service_name}")
                else:
                    print(f"Failed to restart {service_name}: {response.status}")

    async def _rollback_model(self, alert: Dict[str, Any]):
        """Rollback model to previous version"""

        model_name = alert.get("labels", {}).get("model_name", "fraud-detection-model")

        # Rollback logic would integrate with MLflow or similar
        print(f"Rolling back model {model_name} to previous version")

    async def _cleanup_logs(self, alert: Dict[str, Any]):
        """Clean up old logs to free disk space"""

        # Execute log cleanup script
        print("Cleaning up old log files")

# Usage in alert webhook
responder = AutomatedIncidentResponder()

@app.post("/webhook/alert")
async def alert_webhook(alert_data: Dict[str, Any]):
    """Webhook endpoint for Prometheus alerts"""

    for alert in alert_data.get("alerts", []):
        if alert.get("status") == "firing":
            await responder.respond_to_alert(alert)

    return {"status": "ok"}
```

This comprehensive monitoring and observability system provides end-to-end visibility, automated alerting, and intelligent anomaly detection for the fraud detection platform.