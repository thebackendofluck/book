# Chapter 46: Brazilian Betting Platform

Production-ready reference implementation for a Lei 14.790/2023 compliant
Brazilian sports betting platform. Each script addresses a distinct regulatory
or technical domain and is designed to be deployed as an independent
microservice.

---

## Directory Structure

```
brazilian-betting-platform/
├── pix_payment_gateway.py      # PIX payment integration (deposit, withdrawal, reconciliation)
├── cpf_kyc_service.py          # CPF validation and KYC pipeline
├── sigap_reporter.py           # Legacy internal-report aggregation teaching stub
├── geolocation_service.py      # Geolocation enforcement (Brazil-only)
├── responsible_gaming.py       # Responsible gaming compliance engine
├── docker-compose.yml          # Local dev / staging stack
├── terraform/
│   └── main.tf                 # AWS infrastructure (sa-east-1)
├── landing_page/
│   └── index.html              # Brazilian betting landing page
└── tests/
    ├── test_pix_payment.py     # PIX gateway test suite
    └── test_cpf_kyc.py         # KYC pipeline test suite
```

---

## Services Overview

### 1. `pix_payment_gateway.py` — Port 8001

PIX payment integration service implementing the full payment lifecycle.

**Key features:**
- QR Code generation (static and dynamic) via Celcoin, Asaas, or Transfeera
- Payment state machine: `pending → processing → confirmed → settled / failed → refunded`
- Webhook handler with HMAC-SHA256 signature verification
- PSP abstraction layer (swap PSPs without touching business logic)
- Fraud scoring (velocity, amount, IP reputation)
- Reconciliation engine comparing PSP ledger vs internal store
- Rate limiting per player per minute

**Endpoints:**
```
POST /v1/pix/deposits         Create a PIX deposit charge
POST /v1/pix/withdrawals      Process a PIX payout
POST /v1/pix/webhooks/{psp}   Receive PSP notification
GET  /v1/pix/payments/{id}    Get payment status
POST /v1/pix/reconcile/{psp}  Run reconciliation
GET  /healthz
```

---

### 2. `cpf_kyc_service.py` — Port 8002

Full KYC pipeline gating account creation and ongoing compliance.

**Key features:**
- Receita Federal digit check algorithm (mod 11)
- Receita Federal API integration (mock; wire mTLS cert in production)
- Biometric facial verification (configurable confidence threshold: 0.80)
- SIGAP Impediments API v2 check for centralized self-exclusion and current legal impediments
- Social-program block when SIGAP returns `PROGRAMA_SOCIAL`; no direct CadÚnico/CNIS query and no tracing of PIX funds
- 18+ age verification
- Checks at onboarding, first daily login, and a full-base scan every 15 days
- LGPD right-to-erasure (PII anonymisation; CPF hash retained 5 years)

**Endpoints:**
```
POST   /v1/kyc/register            Step 1: CPF + identity
POST   /v1/kyc/biometric           Step 2: Facial biometric
GET    /v1/kyc/players/{id}        KYC status
DELETE /v1/kyc/players/{id}        LGPD erasure
POST   /v1/kyc/reverify            Trigger re-verification
GET    /healthz
```

---

### 3. `sigap_reporter.py` — Port 8003 (legacy teaching stub)

This file demonstrates internal event aggregation, retry and audit concepts. Its historical JSON routes are **not** the current SIGAP wire contract and must not be pointed at production. The production-oriented reference is `cloudflare/src/sigap-reporter.ts`, which accepts an already XSD-validated, e-CNPJ-signed, GZIP/Base64 batch envelope for delivery through the category-specific `/lote` endpoint.

**Key features:**
- Internal source-event ingestion
- Period-level aggregation examples
- Durable delivery-ledger concepts
- Write-ahead log (WAL) for at-least-once delivery guarantee
- Exponential backoff retry (up to 5 attempts)
- JSON validation for the stub's internal records, not official SIGAP XSD validation
- Historical scheduler example

**Legacy local endpoints:**
```
POST /v1/events/bet              Ingest bet event
POST /v1/reports/daily-ggr       Trigger GGR report
POST /v1/reports/bet-detail      Trigger bet detail report
POST /v1/reports/player-activity Trigger player activity report
GET  /healthz
```

---

### 4. `geolocation_service.py` — Port 8004

Enforces Brazil-only access per Lei 14.790/2023.

**Key features:**
- Fallback chain: GPS (< 1km accuracy) → WiFi (≥ 2 networks) → IP → block
- Brazilian state identification via coordinate bounding boxes
- VPN / proxy / Tor exit node detection
- Per-session 30-minute re-verification via background monitor
- WebSocket push notification when session geo expires
- BACEN geographic boundary check (lat/lon bounding box)

**Endpoints:**
```
POST /v1/geo/verify              Verify player location
GET  /v1/geo/sessions/{id}       Session geo status
WS   /v1/geo/ws/{session_id}     Real-time reverification notifications
GET  /healthz
```

---

### 5. `responsible_gaming.py` — Port 8005

Lei 14.790/2023 responsible gaming compliance engine.

**Key features:**
- Deposit limits (daily / weekly / monthly) with 24h cooldown on increases
- Loss limits with real-time enforcement
- Session time limits with warnings at 15, 5, and 1 minute remaining
- Self-exclusion (temporary 1-1825 days or permanent)
- National Aposta Responsável registry API integration
- Behavioral risk scoring (chasing losses, late-night sessions)
- Alert dispatcher (email / SMS / WebSocket stubs)
- Background session monitor (60-second polling)

**Endpoints:**
```
POST /v1/rg/limits                    Set deposit/loss/session limits
POST /v1/rg/sessions/start            Start a betting session
POST /v1/rg/bet-check                 Check bet allowed under limits
POST /v1/rg/self-exclusion            Activate self-exclusion
POST /v1/rg/cooling-off/{player_id}   Activate cooling-off
GET  /v1/rg/players/{id}/status       Full RG status
GET  /healthz
```

---

## Running Locally

### Prerequisites

- Python 3.11+
- Docker and Docker Compose
- `pip install -r requirements.txt`

### Install dependencies

```bash
pip install \
    fastapi uvicorn pydantic structlog tenacity \
    aiohttp jsonschema pytest pytest-asyncio
```

### Start the full stack

```bash
# Copy and edit environment file
cp .env.example .env

# Start all services
docker compose up -d

# Watch logs
docker compose logs -f api
```

### Run individual services

```bash
# PIX Payment Gateway
uvicorn pix_payment_gateway:app --port 8001 --reload

# CPF KYC Service
uvicorn cpf_kyc_service:app --port 8002 --reload

# SIGAP Reporter
uvicorn sigap_reporter:app --port 8003 --reload

# Geolocation Service
uvicorn geolocation_service:app --port 8004 --reload

# Responsible Gaming Engine
uvicorn responsible_gaming:app --port 8005 --reload
```

---

## Running Tests

```bash
# All tests
pytest tests/ -v

# PIX payment tests only
pytest tests/test_pix_payment.py -v

# KYC tests only
pytest tests/test_cpf_kyc.py -v

# With coverage
pytest tests/ --cov=. --cov-report=term-missing
```

---

## Infrastructure (Terraform)

The `terraform/main.tf` provisions production AWS infrastructure in `sa-east-1`:

| Resource            | Details                                              |
|---------------------|------------------------------------------------------|
| VPC                 | 10.46.0.0/16, 3 AZs, public/private/database tiers  |
| EKS                 | v1.30, t3.xlarge nodes, IMDSv2, encrypted EBS        |
| RDS PostgreSQL      | v16, Multi-AZ, gp3, encrypted, 35-day backup         |
| ElastiCache Redis   | r6g.large, cluster mode, TLS, auth token             |
| MSK Kafka           | v3.6, 3 brokers, TLS, at-rest encryption             |
| S3 KYC bucket       | SSE-KMS, versioning, 5-year LGPD lifecycle           |
| WAF                 | Common rule set, known bad inputs, IP rate limit      |
| CloudFront          | TLSv1.2, WAF attached, .bet.br certificate           |
| KMS                 | Per-service keys, annual rotation                    |

```bash
cd terraform
terraform init
terraform plan -var="environment=production"
terraform apply
```

---

## Regulatory Compliance Map

| Requirement                         | Implementation                                   |
|-------------------------------------|--------------------------------------------------|
| Lei 14.790/2023 — KYC               | `cpf_kyc_service.py` full pipeline               |
| Portaria 2.217/2025 + IN 22/2025 — social programs | SIGAP v2 client in the KYC service |
| Centralized self-exclusion          | Same SIGAP v2 client + `responsible_gaming.py`   |
| Lei 14.790/2023 — geolocation       | `geolocation_service.py` Brazil-only check       |
| BACEN PIX Resolution 1/2020         | `pix_payment_gateway.py` state machine           |
| SPA/MF SIGAP reporting              | `cloudflare/src/sigap-reporter.ts` prepared signed-batch delivery; Python file is an internal aggregation stub |
| LGPD Art. 18 — right to erasure     | `KYCPipeline.process_lgpd_deletion()`            |
| LGPD — data minimisation            | PII fields SHA-256 hashed; plaintext not stored  |
| Age restriction (18+)               | `_assert_minimum_age()` in KYC registration      |
| COAF AML reporting                  | Separate COAF workflow; do not send suspicious-activity reports through an invented SIGAP report type |

---

## Environment Variables

| Variable              | Description                         | Default                 |
|-----------------------|-------------------------------------|-------------------------|
| `DATABASE_URL`        | PostgreSQL connection string        | —                       |
| `REDIS_URL`           | Redis connection string             | —                       |
| `KAFKA_BOOTSTRAP_SERVERS` | Kafka broker addresses          | `kafka:29092`           |
| `SIGAP_ACCESS_TOKEN` | Short-lived Bearer token from the e-CNPJ authentication flow | — |
| `SIGAP_MOCK`         | Use only the official homologation fixtures | `false`          |
| `OPERATOR_CNPJ`       | Operator CNPJ (14 digits)          | `12345678000195`        |
| `CELCOIN_API_KEY`     | Celcoin API key                     | `sandbox_key`           |
| `CELCOIN_API_SECRET`  | Celcoin API secret                  | `sandbox_secret`        |
| `CELCOIN_BASE_URL`    | Celcoin base URL                    | sandbox URL             |
| `ENV`                 | `production` / `staging`           | `staging`               |
| `LOG_LEVEL`           | `DEBUG` / `INFO` / `WARNING`       | `INFO`                  |
| `POSTGRES_PASSWORD`   | PostgreSQL master password          | —                       |
| `REDIS_PASSWORD`      | Redis AUTH token                    | —                       |
