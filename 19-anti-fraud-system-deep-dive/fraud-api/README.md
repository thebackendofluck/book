# AcmeToCasino Fraud Detection API

Companion code for **Chapter 19: Anti-Fraud System Deep Dive** and
**Chapter 24: Security and Compliance**.

A production-grade, real-time fraud detection service built on FastAPI,
Elasticsearch 8.x, Kafka, and Redis.  Scores every financial transaction
in under 50 ms using a deterministic rules engine with jurisdiction-aware
thresholds, and exposes a REST API for the operator compliance dashboard.

---

## Quick Start (Docker)

```bash
# 1. Clone / navigate to this directory
cd scripts/chapter-19/fraud-api

# 2. Start the full stack (ES, Kibana, Kafka, Redis, Fraud API)
docker compose up -d

# 3. Wait for services to initialise (~60 seconds for Elasticsearch)
docker compose ps
docker compose logs -f fraud-api

# 4. Verify the API is healthy
curl http://localhost:8080/fraud/status
```

Service URLs once running:

| Service | URL |
|---------|-----|
| Fraud API | http://localhost:8080 |
| API docs (Swagger) | http://localhost:8080/docs |
| Kibana | http://localhost:5601 |
| Kafka UI | http://localhost:8085 |
| Elasticsearch | http://localhost:9200 |

---

## Local Development (without Docker)

```bash
# Prerequisites: Python 3.11+, running Elasticsearch + Redis + Kafka

# 1. Install dependencies
pip install -r requirements.txt

# 2. Export environment variables
export ELASTICSEARCH_HOSTS="http://localhost:9200"
export REDIS_URL="redis://localhost:6379/0"
export KAFKA_BOOTSTRAP_SERVERS="localhost:9092"

# 3. Start the API
python -m app.main
# or with uvicorn directly:
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

---

## API Reference

### GET /fraud/status

System health and real-time operational metrics.

```bash
curl http://localhost:8080/fraud/status
```

```json
{
  "status": "healthy",
  "version": "1.0.0",
  "uptime_seconds": 342.1,
  "elasticsearch_connected": true,
  "redis_connected": true,
  "kafka_consumer_lag": 0,
  "events_indexed_24h": 15234,
  "alerts_generated_24h": 47,
  "rules_active": 10
}
```

### GET /fraud/alerts

Active fraud alerts from Elasticsearch, sorted by risk score descending.

```bash
# All open alerts
curl "http://localhost:8080/fraud/alerts"

# Critical alerts for MGA jurisdiction
curl "http://localhost:8080/fraud/alerts?risk_level=critical&jurisdiction=MGA"

# Paginated
curl "http://localhost:8080/fraud/alerts?page=2&page_size=10"
```

### GET /fraud/events

Recent fraud events, paginated and filterable.

```bash
# Events for a specific player
curl "http://localhost:8080/fraud/events?player_id=12345"

# High-risk events in a time range
curl "http://localhost:8080/fraud/events?risk_level=high&from_dt=2025-01-01T00:00:00Z"
```

### POST /fraud/analyze

Synchronous transaction scoring — called inline by the wallet service
before processing a deposit or withdrawal.

```bash
curl -X POST http://localhost:8080/fraud/analyze \
  -H "Content-Type: application/json" \
  -H "X-Correlation-ID: txn-abc-123" \
  -d '{
    "correlation_id": "txn-abc-123",
    "player_id": "12345",
    "brand_id": 1,
    "jurisdiction": "MGA",
    "transaction_type": "deposit",
    "amount": 50000,
    "currency": "EUR",
    "payment_method": "card",
    "deposit_number": 1,
    "ip_address": "5.62.56.160",
    "country_code": "MT",
    "device_fingerprint": "fp_abc123"
  }'
```

Response:

```json
{
  "correlation_id": "txn-abc-123",
  "player_id": "12345",
  "risk_score": 0.25,
  "risk_level": "low",
  "typologies": [],
  "rule_hits": [],
  "recommended_action": "allow",
  "block_reason": null,
  "feature_importances": {},
  "processing_time_ms": 4.2
}
```

### GET /fraud/rules

Fraud detection rule catalogue — for compliance dashboard audit view.

```bash
# All active rules
curl "http://localhost:8080/fraud/rules"

# Rules applicable to UKGC jurisdiction
curl "http://localhost:8080/fraud/rules?jurisdiction=UKGC"
```

### GET /fraud/player/{player_id}/risk

Player risk profile for KYC/AML case management.

```bash
curl http://localhost:8080/fraud/player/12345/risk
```

---

## Injecting Test Events

Send synthetic wallet events to Kafka to exercise the full pipeline:

```bash
# Connect to the Kafka container
docker exec -it fraud-kafka bash

# Publish a test deposit event
kafka-console-producer.sh \
  --bootstrap-server localhost:9092 \
  --topic wallet.events <<EOF
{"traceId":"test-001","eventType":"deposit","userId":99999,"brandId":1,"jurisdiction":"MGA","country":"MT","currency":"EUR","amount":50000,"depositNumber":1,"paymentMethod":"card","ipAddress":"1.2.3.4","deviceFp":"test-fp-001"}
EOF

# Publish a structuring-pattern test (5 deposits near the EUR 2,000 threshold)
for i in 1 2 3 4 5; do
  kafka-console-producer.sh --bootstrap-server localhost:9092 --topic wallet.events <<EOF
{"traceId":"struct-$i","eventType":"deposit","userId":88888,"brandId":1,"jurisdiction":"MGA","country":"MT","currency":"EUR","amount":185000,"depositNumber":$i,"paymentMethod":"card"}
EOF
done
```

After publishing, events appear in Kibana under the `fraud-events-*` index
and alerts appear under `fraud-alerts-*`.

---

## Kibana Setup

1. Open Kibana at http://localhost:5601
2. Go to **Stack Management → Index Patterns**
3. Create two index patterns:
   - `fraud-events-*` with `created_at` as the time field
   - `fraud-alerts-*` with `created_at` as the time field
4. Use **Discover** to explore events; filter by `risk_level: critical` to
   see the highest-severity records
5. Build a dashboard with:
   - Time series of `risk_score` (average per hour)
   - Pie chart of `typologies`
   - Table of open alerts sorted by `risk_score` descending
   - Map of `country_code` (requires Kibana Maps)

---

## Running Tests

```bash
# Install test dependencies
pip install pytest pytest-asyncio httpx

# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=app --cov-report=term-missing
```

---

## Configuration Reference

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `ELASTICSEARCH_HOSTS` | `http://elasticsearch:9200` | Comma-separated ES node URLs |
| `REDIS_URL` | `redis://redis:6379/0` | Redis connection string |
| `KAFKA_BOOTSTRAP_SERVERS` | `kafka:9092` | Kafka bootstrap servers |
| `API_VERSION` | `1.0.0` | Reported in /fraud/status |
| `CORS_ORIGINS` | `http://localhost:5601` | Allowed CORS origins (comma-separated) |
| `LOG_LEVEL` | `INFO` | Structlog log level |

---

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full system architecture
including Mermaid data-flow diagrams, Elasticsearch index design, Redis
key schema, and compliance reference mapping.

---

## Compliance Notes

This service implements controls required by:

- **AMLD6** (EU 2018/1673) — continuous, automated transaction monitoring
- **FATF Recommendations 10, 16, 20** — velocity monitoring, traceability,
  suspicious transaction reporting triggers
- **PCI DSS Requirement 10** — audit logging with `correlation_id` on
  every event and API request; 5-year log retention via Elasticsearch ILM
- **UKGC/MGA** — explainability of automated decisions via
  `feature_importances` in every `RiskScore` response

**Important:** this is a reference implementation for a book companion
repository.  Before using in a licensed gambling operation, engage your
compliance counsel to review against your specific jurisdictional obligations.
The rule thresholds in `rules_engine.py` are starting points — they must be
tuned to your actual player population using at least two weeks of baseline
traffic data before going live (see Chapter 24, IDS/IPS threshold tuning).
