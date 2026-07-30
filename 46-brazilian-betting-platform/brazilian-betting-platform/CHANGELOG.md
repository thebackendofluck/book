# Changelog

All notable changes to the Brazilian Betting Platform are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and [Semantic Versioning](https://semver.org/).

## Tested Environment

### Go Microservices

| Component                | Version  | Tested On  |
|--------------------------|----------|------------|
| Go                       | 1.22     | 2026-03-23 |
| go-chi/chi/v5            | 5.1.0    | 2026-03-23 |
| google/uuid              | 1.6.0    | 2026-03-23 |
| redis/go-redis/v9        | 9.5.1    | 2026-03-23 |
| jackc/pgx/v5             | 5.7.4    | 2026-03-23 |
| segmentio/kafka-go       | 0.4.47   | 2026-03-23 |
| rs/zerolog               | 1.33.0   | 2026-03-23 |
| stretchr/testify         | 1.9.0    | 2026-03-23 |

### Python Microservices

| Component                | Version  | Tested On  |
|--------------------------|----------|------------|
| FastAPI                  | 0.115.0 - 0.115.5 | 2026-03-23 |
| Uvicorn                  | 0.30.6 - 0.32.1   | 2026-03-23 |
| Pydantic                 | 2.9.2    | 2026-03-23 |
| SQLAlchemy               | 2.0.35 - 2.0.36   | 2026-03-23 |
| asyncpg                  | 0.30.0   | 2026-03-23 |
| redis (Python)           | 5.1.1 - 5.2.0     | 2026-03-23 |
| httpx                    | 0.27.2 - 0.28.0   | 2026-03-23 |
| structlog                | 24.4.0   | 2026-03-23 |
| neo4j                    | 5.27.0   | 2026-03-23 |
| scikit-learn             | 1.6.1    | 2026-03-23 |
| numpy                    | 2.2.3    | 2026-03-23 |
| aiokafka                 | 0.11.0   | 2026-03-23 |
| aiohttp                  | 3.11.7   | 2026-03-23 |
| pytest                   | 8.3.3    | 2026-03-23 |
| pytest-asyncio           | 0.24.0   | 2026-03-23 |
| pytest-cov               | 6.0.0    | 2026-03-23 |

### Infrastructure

| Component                | Version  | Tested On  |
|--------------------------|----------|------------|
| Terraform                | >= 1.7.0 | 2026-03-23 |
| AWS Provider             | >= 5.40.0 | 2026-03-23 |
| Docker Compose           | 3.9      | 2026-03-23 |

## [1.0.0] - 2026-03-23

### Added
- **odds-feed**: Real-time odds ingestion microservice (Go) with Redis caching and SSE streaming
- **betting-engine**: Core bet placement and validation engine (Go) with PostgreSQL and Redis
- **cashout-pricing**: Dynamic cashout price calculation microservice (Go)
- **bet-builder**: Multi-leg bet composition service (Go) for accumulators and system bets
- **settlement**: Bet settlement pipeline (Go) with Kafka event streaming and PostgreSQL
- **wallet**: Player wallet service (Go) with PostgreSQL transactional balance management
- **casino-aggregation**: Casino game aggregation layer (Go) with zerolog structured logging
- **aml-fraud**: AML/fraud detection microservice (Python) with Neo4j graph analysis and scikit-learn ML scoring
- **bonus-engine**: Promotional bonus management (Python) with Redis caching
- **pam**: Player Account Management service (Python) with async HTTP and structured logging
- **responsible-gaming**: Self-exclusion, deposit limits, and session monitoring (Python) with Kafka and Redis
- PIX payment gateway integration with sandbox demo mode
- CPF-based KYC verification service
- SIGAP regulatory reporter for Lei 14.790/2023 compliance
- Geolocation service for Brazilian state-level jurisdiction enforcement
- Responsible gaming controls (Lei 14.790/2023 compliant)
- Brazil launch checklist validation script
- Cloudflare CDN configuration for .bet.br domain
- Landing page for Brazilian market
- AWS infrastructure Terraform (sa-east-1): VPC, EKS, RDS, ElastiCache, MSK, WAF, CloudFront, S3, Route53
- Docker Compose stack with network isolation, non-root containers, read-only filesystems
- Phase 2 live betting: SSE feed, incidents, circuit breaker, exposure tracking, risk alerts
- Cashout UI with real-time pricing
- Bet builder frontend integration
- Event pages with betslip API sync

### Tested With
- Go 1.22 (all Go microservices)
- Python 3.12+ (all Python microservices)
- PostgreSQL (via pgx/v5 5.7.4 and asyncpg 0.30.0)
- Redis (via go-redis/v9 9.5.1 and redis-py 5.2.0)
- Kafka (via kafka-go 0.4.47 and aiokafka 0.11.0)
- Neo4j 5.27.0 (AML graph analysis)
- Terraform >= 1.7.0 with AWS provider >= 5.40.0
