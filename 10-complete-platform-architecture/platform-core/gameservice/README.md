# Game Aggregation Layer (GAL)

A production-grade game integration layer written in Python 3.12+, demonstrating how a
modern online gambling platform coordinates transactions across multiple game suppliers.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   FastAPI (main.py)                  │
│  /api/v1/wallet/*   /supplier/<name>/*   /health    │
└────────────────────┬────────────────────────────────┘
                     │
          ┌──────────▼──────────┐
          │   AccountsBridge    │  accounts_bridge.py
          │                     │
          │  • Per-player lock  │
          │  • Idempotency      │
          │  • Audit log        │
          │  • Error mapping    │
          └──────────┬──────────┘
                     │  resolves via
          ┌──────────▼──────────┐
          │  SupplierRegistry   │  suppliers/registry.py
          └──────────┬──────────┘
                     │
     ┌───────────────┼───────────────────┐
     ▼               ▼                   ▼
EvolutionProvider  PragmaticProvider  KambiProvider  ...
 (live dealer)      (slots)           (sportsbook)
```

## Key Design Decisions

### Per-player locking
`AccountsBridge` maintains one `asyncio.Lock` per `player_id`. This serialises
concurrent wallet operations for the same player — preventing race conditions on
balance checks and idempotency lookups.

In a multi-instance deployment, replace `PlayerLockRegistry` with a distributed
lock backed by Redis (`SET NX PX`).

### Transaction idempotency
Every supplier callback carries a unique `supplierRef` (or equivalent). The bridge
writes an idempotency tombstone **before** calling the wallet. If the same ref arrives
again (network retry, duplicate callback), the bridge returns the cached result without
re-executing the wallet operation.

### Supplier protocol
Every supplier implements `AccountsProvider` (defined in `accounts_provider.py`). The
bridge delegates all wallet operations to the provider — it never contains
supplier-specific logic.

### Amounts in minor units
All amounts flow through the system in minor units (pence for GBP, cents for EUR).
Individual provider implementations convert to/from major units when the supplier
API requires it.

## Supplier Inventory

| Supplier | Type | Integration Model |
|---|---|---|
| Evolution Gaming | Live dealer | Seamless wallet, combined WITHDRAW+DEPOSIT |
| Pragmatic Play | Slots + live | Seamless wallet, MD5 hash auth |
| NetEnt | Slots + free rounds | Seamless wallet, HMAC-SHA256 |
| Play'n GO | Slots | Seamless wallet, REST JSON |
| Kambi | Sportsbook | Fund/withdraw model |
| Relax Gaming | Aggregator | Seamless wallet (wraps studios) |
| IGT | Slots (land-based crossover) | REST JSON (FortuNet) |
| Hacksaw Gaming | Crash + instant win | Seamless wallet, 0-amount credits |
| Push Gaming | High-volatility slots | Seamless wallet, max-win cap |
| NYX Interactive | Aggregator (OGS) | Open Gaming System protocol |
| Bet Genius | Sports data + trading | Operator-triggered settlement |

## Running the service

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables (see .env.example)
export EVOLUTION_API_SECRET=your-secret
export EVOLUTION_OPERATOR_ID=your-operator-id
# ... other suppliers

# Every route under /api/v1 (auth + wallet) requires a per-supplier HMAC
# signature on the request itself, separate from the secrets above (which
# sign our outbound/launch-token traffic to each supplier). A supplier with
# no *_CALLBACK_SECRET configured is rejected, not silently let through.
export EVOLUTION_CALLBACK_SECRET=your-callback-secret
export PRAGMATIC_CALLBACK_SECRET=your-callback-secret
# ... one <SUPPLIER>_CALLBACK_SECRET per registered supplier

# Start the server
uvicorn main:app --host 0.0.0.0 --port 8080 --workers 4
```

## Docker

```bash
docker build -t acmeto-gal:latest .
docker run -p 8080:8080 --env-file .env acmeto-gal:latest
```

## Running tests

```bash
pytest tests/ -v --cov=. --cov-report=term-missing
```

## Health endpoints

- `GET /health` — Liveness probe (always 200 if process is alive)
- `GET /ready` — Readiness probe (checks supplier registry)
- `GET /docs` — OpenAPI documentation (FastAPI auto-generated)

## File structure

```
gameservice/
├── main.py                      FastAPI app and route handlers
├── accounts_bridge.py           Central transaction coordinator
├── accounts_provider.py         Protocol and operation descriptors
├── transaction_result.py        Canonical result models and exceptions
├── Dockerfile                   Multi-stage, non-root, health check
├── requirements.txt
├── README.md
├── accounts/
│   └── wallet.py                In-house wallet operations
├── suppliers/
│   ├── registry.py              Supplier registration and lookup
│   ├── settings.py              Per-supplier configuration
│   ├── evolution/provider.py
│   ├── pragmatic/provider.py
│   ├── netent/provider.py
│   ├── playngo/provider.py
│   ├── kambi/provider.py
│   ├── relax/provider.py
│   ├── igt/provider.py
│   ├── hacksaw/provider.py
│   ├── push_gaming/provider.py
│   ├── nyx/provider.py
│   └── betgenius/provider.py
└── tests/
    ├── test_accounts_bridge.py  20+ bridge tests
    └── test_suppliers.py        Registry and provider tests
```

## Production checklist

- Replace `PlayerLockRegistry` with Redis-backed distributed locks
- Replace `StubPlayerRepository` with asyncpg/SQLAlchemy implementation
- Replace `TransactionCache` with Redis-backed idempotency store
- Add secrets management (AWS Secrets Manager / Vault)
- Enable OpenTelemetry tracing with the OTLP exporter
- Configure Prometheus metrics scraping at `/metrics`
- Set `ALLOWED_ORIGINS` to your frontend domain(s)
