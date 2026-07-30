# AcmeToCasino Demo Platform

This directory contains the core platform code from the AcmeToCasino modular monolith,
as referenced in Chapter 10 (Complete Platform Architecture).

## Files

- **main.py** — FastAPI application entry point with all router registrations, lifespan
  management (DB pool, Redis, background subscriber thread), CORS middleware, metrics
  middleware with correlation IDs, health check, stats aggregation, and WebSocket endpoint
  for real-time event streaming.

- **docker-compose.yml** — Five-container stack: PostgreSQL 18, Redis 7, FastAPI API,
  Prometheus, and a 50-bot seeder. All bound to localhost with memory limits.

- **database.py** — psycopg2 threaded connection pool with context managers for
  connections and cursors. Includes the full DDL migration: players, wallet_events,
  game_sessions, game_rounds, rtp_configs, kyc_checks, aml_alerts, deposit_limits,
  and self_exclusions tables.

- **config.py** — Dataclass-based settings loaded from environment variables with
  sensible defaults for local development.

## How This Maps to Chapter 10

The chapter introduces the modular monolith architecture pattern for iGambling platforms.
These files demonstrate the concrete implementation:

1. **Domain-Driven Design** — Six domain modules (PAM, Wallet, GAL, Compliance,
   Responsible Gaming, Game Control) registered as FastAPI routers in main.py.
2. **Infrastructure Layer** — Connection pooling, Redis pub/sub, Prometheus metrics.
3. **Event-Sourced Design** — The wallet_events table uses append-only inserts;
   balance is always computed, never stored.
4. **Container Orchestration** — docker-compose.yml shows how all components
   fit together with health checks and resource constraints.
5. **Observability** — Correlation IDs, structured logging, /health and /metrics
   endpoints baked into the application from day one.
