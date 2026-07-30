# Brazilian Betting Platform — Integration Stack Deployment

This document covers running the full 12-container integration stack locally using
`docker-compose.integration.yml`. All findings, fixes, and lessons learned are
recorded here from the first successful deployment on the ops-host VM (10.0.0.11).

---

## Prerequisites

| Tool | Minimum version | Notes |
|------|----------------|-------|
| Docker | 24.x | Docker Desktop or Docker Engine |
| Docker Compose | v2.x | Use `docker compose` (not `docker-compose`) |
| Go | 1.22+ | Required to build Go microservices |
| Python | 3.12+ | Required to build Python microservices |
| `nc` (netcat) | any | Used by Zookeeper health check |
| `wget` | any | Used by Go service health checks |

---

## Quick Start

```bash
cd microservices/

# Start all 12 containers (4 infra + 9 microservices — bonus-engine rounds to 9)
docker compose -f docker-compose.integration.yml up -d

# Follow startup logs
docker compose -f docker-compose.integration.yml logs -f

# Check all containers are healthy
docker compose -f docker-compose.integration.yml ps

# Tear down (removes containers and volumes)
docker compose -f docker-compose.integration.yml down -v
```

Wait approximately 60–90 seconds for Kafka to become healthy before all Go services
pass their health checks. The dependency chain is:

```
postgres ─┐
redis    ─┼─► pam ──────────────────────────────► betting-engine
           └─► responsible-gaming ─────────────► betting-engine
zookeeper ─► kafka ─────────────────────────────► betting-engine
                                                  settlement
```

---

## Service Inventory

| Container | Image | Port (host) | Port (container) | Runtime |
|-----------|-------|-------------|-----------------|---------|
| betbr-int-postgres | postgres:16-alpine | — | 5432 (internal) | PostgreSQL |
| betbr-int-redis | redis:7-alpine | — | 6379 (internal) | Redis |
| betbr-int-zookeeper | cp-zookeeper:7.6.0 | — | 2181 (internal) | JVM |
| betbr-int-kafka | cp-kafka:7.6.0 | 127.0.0.1:9092 | 29092 (internal) | JVM |
| betbr-int-pam | betbr-pam:integration | 127.0.0.1:18010 | 8010 | Python/FastAPI |
| betbr-int-responsible-gaming | betbr-responsible-gaming:integration | 127.0.0.1:18020 | 8020 | Python/FastAPI |
| betbr-int-bonus-engine | betbr-bonus-engine:integration | 127.0.0.1:18030 | 8030 | Python/FastAPI |
| betbr-int-betting-engine | betbr-betting-engine:integration | 127.0.0.1:18080 | 8080 | Go |
| betbr-int-wallet | betbr-wallet:integration | 127.0.0.1:18081 | 8081 | Go |
| betbr-int-settlement | betbr-settlement:integration | 127.0.0.1:18082 | 8082 | Go |
| betbr-int-odds-feed | betbr-odds-feed:integration | 127.0.0.1:18083 | 8083 | Go |
| betbr-int-casino-aggregation | betbr-casino-aggregation:integration | 127.0.0.1:18040 | 8040 | Go |

All host ports are bound to `127.0.0.1` only — no external exposure.

---

## Health Check Endpoints

| Service | Endpoint | Method | Expected response |
|---------|----------|--------|-------------------|
| pam | http://localhost:18010/health | GET | 200 OK |
| responsible-gaming | http://localhost:18020/health | GET | 200 OK |
| bonus-engine | http://localhost:18030/health | GET | 200 OK |
| betting-engine | http://localhost:18080/health | GET | 200 OK |
| wallet | http://localhost:18081/health | GET | 200 OK |
| settlement | http://localhost:18082/health | GET | 200 OK |
| odds-feed | http://localhost:18083/health | GET | 200 OK |
| casino-aggregation | http://localhost:18040/health | GET | 200 OK |

Quick check all services:

```bash
for port in 18010 18020 18030 18040 18080 18081 18082 18083; do
  printf "%-8s " "$port"
  curl -sf http://localhost:$port/health && echo "OK" || echo "FAIL"
done
```

---

## Environment Variables Reference

All variables use `${VAR:-default}` syntax. Override by creating a `.env` file in
the `microservices/` directory or exporting before running `docker compose`.

### Infrastructure

| Variable | Default | Used by |
|----------|---------|---------|
| `POSTGRES_DB` | `betbr_integration` | postgres, all services |
| `POSTGRES_USER` | `betbr_user` | postgres, all services |
| `POSTGRES_PASSWORD` | `postgres` | postgres, all services |
| `REDIS_PASSWORD` | `redis` | redis, all services |

### Go Services (additional mappings)

Go services use library-specific env var names that differ from the Python convention.
Both are provided so either naming convention works:

| Standard name | Go-native alias | Services |
|---------------|----------------|----------|
| `DATABASE_URL` | `POSTGRES_DSN` | betting-engine, wallet, settlement |
| `REDIS_URL` | `REDIS_ADDR` + `REDIS_PASSWORD` | betting-engine, odds-feed |
| `KAFKA_BOOTSTRAP_SERVERS` | `KAFKA_BROKERS` | settlement |

### Mock flags (integration mode)

| Variable | Default | Disables |
|----------|---------|---------|
| `RECEITA_FEDERAL_MOCK` | `true` | Live CPF validation |
| `BIOMETRIC_MOCK` | `true` | Live biometric API |
| `SIGAP_MOCK` | `true` | Official SIGAP Impediments homologation fixtures |
| `NATIONAL_REGISTRY_MOCK` | `true` | Self-exclusion registry |
| `SIGAP_MOCK` | `true` | Live SIGAP reporting |
| `PIX_MOCK` | `true` | Live PIX/Celcoin API |
| `FEED_PROVIDER_MOCK` | `true` | Live odds feed provider |

### External service credentials (wallet)

| Variable | Default | Description |
|----------|---------|-------------|
| `CELCOIN_BASE_URL` | sandbox URL | PIX PSP endpoint |
| `CELCOIN_API_KEY` | `sandbox_key` | PSP API key |
| `CELCOIN_API_SECRET` | `sandbox_secret` | PSP API secret |

---

## Resource Allocation

| Service | CPU limit | Memory limit | Notes |
|---------|-----------|-------------|-------|
| postgres | 1.0 core | 512 MB | Shared by all services |
| redis | 0.5 core | 256 MB | maxmemory 256mb enforced |
| zookeeper | 0.5 core | 256 MB | Required by Kafka |
| kafka | 1.0 core | 768 MB | JVM overhead is significant |
| pam | 0.5 core | 256 MB | Python/FastAPI |
| responsible-gaming | 0.5 core | 256 MB | Python/FastAPI |
| bonus-engine | 0.5 core | 256 MB | Python/FastAPI |
| betting-engine | 0.5 core | 256 MB | Go |
| wallet | 0.5 core | 256 MB | Go |
| settlement | 0.5 core | 256 MB | Go |
| odds-feed | 0.5 core | 128 MB | Go, Redis-only dependency |
| casino-aggregation | 0.5 core | 128 MB | Go |
| **Total** | **~6.5 cores** | **~3.6 GB** | Plus OS overhead |

Recommended host: 8 cores, 8 GB RAM minimum. The ops-host VM (8 cores, 16 GB) runs
the full stack comfortably.

---

## Troubleshooting

### Issue 1: Go services fail to build — distroless base has no shell

**Symptom:** Go service containers exit immediately; health checks run shell commands
that fail with `exec: no such file`.

**Root cause:** The original Dockerfiles used `gcr.io/distroless/static-debian12` as
the runtime base. Distroless images contain no shell (`/bin/sh`), no `wget`, and no
`apk`. The Docker Compose health check `wget -qO- http://localhost:PORT/health` cannot
execute because there is no `wget` binary in the image.

**Fix applied:** Changed all Go service runtime stages from distroless to `alpine:3.19`.
Alpine adds ~5 MB and provides a full shell, `wget`, and `apk` for health checks.
The security posture is maintained with `addgroup`/`adduser` to create a non-root user.

```dockerfile
# Before (broken)
FROM gcr.io/distroless/static-debian12:nonroot

# After (working)
FROM alpine:3.19
RUN addgroup -S appgroup && adduser -S appuser -G appgroup
USER appuser:appgroup
```

Affected services: betting-engine, wallet, settlement, odds-feed, casino-aggregation.

---

### Issue 2: Zookeeper health check — `ruok` returns no output

**Symptom:** Zookeeper container never reaches `healthy` state; Kafka never starts
because it depends on Zookeeper being healthy.

**Root cause:** The `ruok` four-letter command was disabled by default in newer
cp-zookeeper images (7.6.0). `echo ruok | nc localhost 2181` returns nothing, causing
the health check to fail indefinitely.

**Fix applied:** Changed to `srvr`, which is enabled by default and returns the server
status including the `Mode: standalone` line:

```yaml
# Before (broken)
healthcheck:
  test: ["CMD-SHELL", "echo ruok | nc -w 2 localhost 2181 | grep imok"]

# After (working)
healthcheck:
  test: ["CMD-SHELL", "echo srvr | nc -w 2 localhost 2181 | grep -q Mode"]
```

---

### Issue 3: Kafka health check — `.sh` extension not in PATH

**Symptom:** Kafka container never reaches `healthy` state; all Kafka-dependent
services (betting-engine, settlement) wait indefinitely.

**Root cause:** The health check called `kafka-broker-api-versions.sh`, but the
Confluent Platform 7.6.0 image exposes the command as `kafka-broker-api-versions`
(no extension) in `/usr/bin`. The `.sh` wrapper is only present in older versions.

**Fix applied:** Removed the `.sh` extension:

```yaml
# Before (broken)
- kafka-broker-api-versions.sh --bootstrap-server localhost:29092 >/dev/null 2>&1

# After (working)
- kafka-broker-api-versions --bootstrap-server localhost:29092 >/dev/null 2>&1
```

---

### Issue 4: Go services — env var name mismatch

**Symptom:** Go services start but fail to connect to Postgres or Redis; logs show
connection refused or empty DSN errors.

**Root cause:** Go services use library-specific environment variable names. The
`database/sql` wrapper looked for `POSTGRES_DSN`, the Redis client looked for
`REDIS_ADDR`, and the Kafka client looked for `KAFKA_BROKERS`. The compose file only
provided the Python-convention names (`DATABASE_URL`, `REDIS_URL`,
`KAFKA_BOOTSTRAP_SERVERS`).

**Fix applied:** Added both the standard name and the Go-native alias for each Go
service:

```yaml
# betting-engine example
environment:
  DATABASE_URL: "postgres://..."         # Python convention
  POSTGRES_DSN: "postgres://...?sslmode=disable"  # Go library convention
  REDIS_URL: "redis://:password@redis:6379/2"     # Python convention
  REDIS_ADDR: "redis:6379"              # Go redis client convention
  REDIS_PASSWORD: "${REDIS_PASSWORD:-redis}"
  KAFKA_BOOTSTRAP_SERVERS: "kafka:29092"  # Standard
  KAFKA_BROKERS: "kafka:29092"            # settlement Go client convention
```

---

## Security Features

The integration stack includes production-equivalent security controls:

- **No root processes:** Infrastructure services run under UID 70 (postgres), 999
  (redis), or service-specific non-root users. Go services use `appuser:appgroup`
  created at image build time.
- **no-new-privileges:** All containers set `security_opt: no-new-privileges:true`,
  preventing privilege escalation via setuid binaries.
- **Capability dropping:** Python services and postgres drop all Linux capabilities.
- **Localhost-only ports:** All host port bindings use `127.0.0.1:HOSTPORT:CONTAINERPORT`.
  No service is reachable from the network.
- **Network isolation:** Infrastructure (postgres, redis, zookeeper, kafka) is on the
  `bet_integration_internal` bridge network, which has `internal: true`. No external
  routing. Only microservices that need test access are also attached to
  `bet_integration_public`.
- **Resource limits:** Every service has explicit CPU and memory limits, preventing a
  single container from starving the host.

---

## Architecture Diagram

```
                    ┌──────────────────────────────────────────────┐
                    │         bet_integration_internal              │
                    │                                               │
  ┌──────────┐      │  ┌──────────┐    ┌──────────┐               │
  │ postgres │◄─────┼──┤   pam    │    │responsible│              │
  │:5432     │      │  │  :8010   │    │  gaming  │               │
  └──────────┘      │  └────┬─────┘    │  :8020   │               │
                    │       │          └─────┬─────┘               │
  ┌──────────┐      │       │                │                     │
  │  redis   │◄─────┼───────┴────────────────┤                    │
  │  :6379   │      │                        │                     │
  └──────────┘      │                        ▼                     │
                    │               ┌──────────────────┐           │
  ┌──────────┐      │               │  betting-engine  │           │
  │zookeeper │◄─────┼──┐            │     :8080        │           │
  │  :2181   │      │  │            └──────────────────┘           │
  └──────────┘      │  │                                           │
       │            │  │  ┌──────────┐    ┌──────────┐            │
  ┌────▼─────┐      │  └──┤  kafka   │────┤settlement│            │
  │  kafka   │◄─────┼─────┤  :29092  │    │  :8082   │            │
  │  :29092  │      │     └──────────┘    └──────────┘            │
  └──────────┘      │                                              │
                    │  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
                    │  │  wallet  │  │odds-feed │  │ casino   │  │
                    │  │  :8081   │  │  :8083   │  │  :8040   │  │
                    │  └──────────┘  └──────────┘  └──────────┘  │
                    │                                              │
                    │  ┌──────────────┐                           │
                    │  │ bonus-engine │                           │
                    │  │    :8030     │                           │
                    │  └──────────────┘                           │
                    └──────────────────────────────────────────────┘

  Host port mapping (127.0.0.1 only):
    Kafka external:   9092  → container 29092
    pam:             18010  → 8010
    responsible-gm:  18020  → 8020
    bonus-engine:    18030  → 8030
    casino-agg:      18040  → 8040
    betting-engine:  18080  → 8080
    wallet:          18081  → 8081
    settlement:      18082  → 8082
    odds-feed:       18083  → 8083
```

---

## Common Commands

```bash
# View logs for a specific service
docker compose -f docker-compose.integration.yml logs -f betting-engine

# Restart a single service without affecting others
docker compose -f docker-compose.integration.yml restart odds-feed

# Rebuild a single service after code changes
docker compose -f docker-compose.integration.yml up -d --build betting-engine

# Check resource usage
docker stats $(docker compose -f docker-compose.integration.yml ps -q)

# Execute a shell in an alpine-based service
docker exec -it betbr-int-betting-engine /bin/sh

# Connect to PostgreSQL
docker exec -it betbr-int-postgres psql -U betbr_user -d betbr_integration

# Connect to Redis
docker exec -it betbr-int-redis redis-cli -a redis
```
