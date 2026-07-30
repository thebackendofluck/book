# Real-Time Data Collection Architecture

## Overview

The data collection architecture is designed to handle high-throughput, low-latency ingestion of diverse data sources with exactly-once semantics for critical financial transactions. The system supports multiple data formats and provides robust buffering and retry mechanisms.

## Architecture Components

```mermaid
graph TB
    subgraph "Data Sources"
        A1[Gaming Platforms<br/>Slots, Tables, Online]
        A2[Payment Gateways<br/>Credit Cards, Wallets]
        A3[User Behavior<br/>Clickstream, Sessions]
        A4[Geolocation<br/>IP, GPS, Device]
        A5[KYC/AML Systems<br/>Verification Feeds]
        A6[External Data<br/>Social Media, Sanctions]
        A7[Historical Data<br/>Player Databases]
        A8[Regulatory Feeds<br/>Compliance Data]
    end

    subgraph "Ingestion Layer"
        B1[API Gateway<br/>Rate Limiting, Auth]
        B2[Message Queue<br/>Kafka/Kinesis]
        B3[Stream Buffer<br/>Redis Streams]
        B4[CDC Pipeline<br/>Debezium]
        B5[Schema Registry<br/>Avro/Protobuf]
        B6[Data Validation<br/>Format Checking]
    end

    subgraph "Processing Layer"
        C1[Stream Processor<br/>Spark Structured Streaming]
        C2[Data Enrichment<br/>External API Calls]
        C3[Format Conversion<br/>JSON → Avro]
        C4[Deduplication<br/>Exactly Once]
        C5[Error Handling<br/>Dead Letter Queue]
    end

    subgraph "Storage Layer"
        D1[Raw Data Lake<br/>S3/Delta Bronze]
        D2[Processed Events<br/>Kafka Topics]
        D3[Metadata Store<br/>PostgreSQL]
        D4[Cache Layer<br/>Redis]
    end

    A1 --> B1
    A2 --> B1
    A3 --> B2
    A4 --> B3
    A5 --> B4
    A6 --> B1
    A7 --> B4
    A8 --> B2

    B1 --> C1
    B2 --> C1
    B3 --> C2
    B4 --> C3
    C1 --> C4
    C2 --> C4
    C3 --> C4
    C4 --> C5

    C4 --> D1
    C4 --> D2
    C5 --> D3
    C2 --> D4
```

## Data Flow Patterns

### Transaction Data Flow

```mermaid
sequenceDiagram
    participant PG as Payment Gateway
    participant AG as API Gateway
    participant MQ as Message Queue
    participant SP as Stream Processor
    participant DL as Data Lake
    participant FE as Feature Engine

    PG->>AG: Transaction Event
    AG->>MQ: Validate & Queue
    MQ->>SP: Stream Processing
    SP->>DL: Store Raw Data
    SP->>FE: Enriched Features
```

### User Behavior Data Flow

```mermaid
flowchart TD
    A[User Action] --> B{Event Type}
    B -->|Click| C[Clickstream Topic]
    B -->|Session| D[Session Topic]
    B -->|Game| E[Game Event Topic]

    C --> F[Real-time Aggregation]
    D --> F
    E --> F

    F --> G[Feature Store]
    G --> H[Risk Scoring]
    H --> I[Alert Engine]
```

## Technology Stack

### Primary Streaming Technologies

| Component | Technology | Purpose |
|-----------|------------|---------|
| Message Broker | Apache Kafka | High-throughput event streaming |
| Cloud Streaming | AWS Kinesis | Managed streaming for AWS |
| Low-latency Buffer | Redis Streams | Sub-millisecond buffering |
| CDC | Debezium | Database change capture |
| Schema Management | Confluent Schema Registry | Data contract enforcement |

### Data Formats and Protocols

```python
# Example Avro Schema for Transaction Events
transaction_schema = {
    "type": "record",
    "name": "TransactionEvent",
    "fields": [
        {"name": "event_id", "type": "string"},
        {"name": "player_id", "type": "string"},
        {"name": "amount", "type": {"type": "bytes", "logicalType": "decimal"}},
        {"name": "currency", "type": "string"},
        {"name": "timestamp", "type": {"type": "long", "logicalType": "timestamp-millis"}},
        {"name": "payment_method", "type": "string"},
        {"name": "game_type", "type": ["string", "null"]},
        {"name": "location", "type": {
            "type": "record",
            "name": "Location",
            "fields": [
                {"name": "ip_address", "type": "string"},
                {"name": "country", "type": "string"},
                {"name": "city", "type": ["string", "null"]}
            ]
        }},
        {"name": "device_fingerprint", "type": ["string", "null"]}
    ]
}
```

## Configuration Examples

### Kafka Topic Configuration

```yaml
# kafka-topics.yaml
topics:
  - name: transactions
    partitions: 12
    replication-factor: 3
    config:
      retention.ms: 604800000  # 7 days
      cleanup.policy: delete
      compression.type: lz4

  - name: user_events
    partitions: 24
    replication-factor: 3
    config:
      retention.ms: 259200000  # 3 days
      cleanup.policy: compact
      compression.type: snappy

  - name: game_events
    partitions: 36
    replication-factor: 3
    config:
      retention.ms: 86400000   # 1 day
      cleanup.policy: delete
      compression.type: gzip
```

### AWS Kinesis Configuration

```json
{
  "StreamName": "fraud-detection-events",
  "ShardCount": 20,
  "StreamModeDetails": {
    "StreamMode": "PROVISIONED"
  },
  "Tags": [
    {
      "Key": "Environment",
      "Value": "production"
    },
    {
      "Key": "Application",
      "Value": "fraud-detection"
    }
  ]
}
```

### Redis Streams Configuration

```redis
# Redis configuration for streams
stream-max-len 10000
stream-max-age 3600000  # 1 hour
stream-read-block-timeout 1000
stream-read-count 100
```

## Performance Characteristics

### Throughput Requirements

| Data Source | Volume | Frequency | Latency Requirement |
|-------------|--------|-----------|-------------------|
| Transactions | 100K/sec | Real-time | < 10ms |
| User Events | 500K/sec | Real-time | < 50ms |
| Game Events | 1M/sec | Real-time | < 100ms |
| CDC Updates | 10K/sec | Near real-time | < 1s |

### Scalability Design

```mermaid
graph LR
    subgraph "Load Balancer"
        LB[API Gateway / Load Balancer]
    end

    subgraph "Ingestion Nodes"
        I1[Ingestion Node 1]
        I2[Ingestion Node 2]
        I3[Ingestion Node N]
    end

    subgraph "Processing Cluster"
        P1[Processing Node 1]
        P2[Processing Node 2]
        P3[Processing Node N]
    end

    LB --> I1
    LB --> I2
    LB --> I3

    I1 --> P1
    I2 --> P2
    I3 --> P3

    P1 --> DB[(Data Lake)]
    P2 --> DB
    P3 --> DB
```

## Error Handling and Resilience

### Retry and Dead Letter Queue Strategy

```python
from kafka import KafkaProducer
from redis import Redis
import json
from typing import Dict, Any

class ResilientProducer:
    def __init__(self, kafka_config: Dict[str, Any], redis_config: Dict[str, Any]):
        self.producer = KafkaProducer(**kafka_config)
        self.redis = Redis(**redis_config)
        self.max_retries = 3
        self.dlq_topic = "dead-letter-queue"

    def send_with_retry(self, topic: str, message: Dict[str, Any], key: str = None):
        """Send message with retry logic and DLQ fallback"""
        for attempt in range(self.max_retries):
            try:
                future = self.producer.send(topic, message, key=key)
                future.get(timeout=10)  # Wait for acknowledgment
                return True
            except Exception as e:
                print(f"Attempt {attempt + 1} failed: {e}")
                if attempt == self.max_retries - 1:
                    # Send to DLQ
                    dlq_message = {
                        "original_topic": topic,
                        "original_message": message,
                        "error": str(e),
                        "timestamp": datetime.utcnow().isoformat()
                    }
                    self.redis.xadd(self.dlq_topic, dlq_message)
                    return False
        return False
```

### Circuit Breaker Pattern

```python
import time
from enum import Enum

class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.last_failure_time = None
        self.state = CircuitState.CLOSED

    def call(self, func, *args, **kwargs):
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
            else:
                raise Exception("Circuit breaker is OPEN")

        try:
            result = func(*args, **kwargs)
            self.on_success()
            return result
        except Exception as e:
            self.on_failure()
            raise e

    def on_success(self):
        self.failure_count = 0
        self.state = CircuitState.CLOSED

    def on_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
```

## Monitoring and Observability

### Key Metrics to Monitor

```python
# Prometheus metrics configuration
metrics = {
    "ingestion_rate": "Rate of events ingested per second",
    "processing_latency": "End-to-end processing latency",
    "error_rate": "Percentage of failed processing attempts",
    "queue_depth": "Number of unprocessed messages in queue",
    "throughput": "Events processed per second",
    "data_quality": "Percentage of valid vs invalid events"
}
```

### Logging Strategy

```python
import logging
import json
from datetime import datetime

class StructuredLogger:
    def __init__(self, service_name: str):
        self.logger = logging.getLogger(service_name)
        self.service_name = service_name

        # Configure structured logging
        formatter = logging.Formatter(
            json.dumps({
                "timestamp": "%(asctime)s",
                "level": "%(levelname)s",
                "service": service_name,
                "message": "%(message)s",
                "extra": "%(extra)s"
            })
        )

        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.INFO)

    def log_event(self, event_type: str, event_data: Dict[str, Any], level: str = "info"):
        extra = {
            "event_type": event_type,
            "event_data": event_data,
            "correlation_id": event_data.get("correlation_id", "unknown")
        }

        if level == "error":
            self.logger.error(f"Event: {event_type}", extra=extra)
        elif level == "warning":
            self.logger.warning(f"Event: {event_type}", extra=extra)
        else:
            self.logger.info(f"Event: {event_type}", extra=extra)
```

## Security Considerations

### Data Encryption

- **In Transit:** TLS 1.3 for all network communications
- **At Rest:** AES-256 encryption for stored data
- **Key Management:** AWS KMS or HashiCorp Vault

### Access Control

```yaml
# API Gateway security configuration
security:
  authentication:
    type: JWT
    issuer: "https://auth.casino.com"
    audience: "fraud-detection-api"

  authorization:
    type: RBAC
    roles:
      - admin: "full access"
      - analyst: "read-only access"
      - service: "api access"

  rate_limiting:
    requests_per_minute: 1000
    burst_limit: 2000
```

### Data Privacy

- PII data masking and anonymization
- Data retention policies per regulation
- Audit logging for all data access
- GDPR compliance for EU data subjects

## Deployment Configuration

### Docker Compose for Development

```yaml
version: '3.8'
services:
  kafka:
    image: confluentinc/cp-kafka:7.3.0
    ports:
      - "9092:9092"
    environment:
      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://localhost:9092
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1

  redis:
    image: redis:8-alpine
    ports:
      - "6379:6379"

  ingestion-service:
    build: ./ingestion
    ports:
      - "8080:8080"
    depends_on:
      - kafka
      - redis
    environment:
      KAFKA_BOOTSTRAP_SERVERS: kafka:9092
      REDIS_URL: redis://redis:6379
```

### Kubernetes Deployment for Production

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: data-ingestion-service
spec:
  replicas: 3
  selector:
    matchLabels:
      app: data-ingestion
  template:
    metadata:
      labels:
        app: data-ingestion
    spec:
      containers:
      - name: ingestion
        image: casino/fraud-detection:ingestion-v1.0
        ports:
        - containerPort: 8080
        env:
        - name: KAFKA_SERVERS
          value: "kafka-cluster:9092"
        - name: REDIS_CLUSTER
          value: "redis-cluster:6379"
        resources:
          requests:
            memory: "1Gi"
            cpu: "500m"
          limits:
            memory: "2Gi"
            cpu: "1000m"
        livenessProbe:
          httpGet:
            path: /health
            port: 8080
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /ready
            port: 8080
          initialDelaySeconds: 5
          periodSeconds: 5
```

This architecture provides a robust, scalable foundation for real-time data collection with comprehensive error handling, monitoring, and security features.