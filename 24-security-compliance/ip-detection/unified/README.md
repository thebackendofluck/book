# Unified IP Detection Sync Layer

Chapter 24 — Multi-Platform Threat Intelligence Synchronisation

## Architecture Overview

Three independent enforcement layers share the same threat intelligence:

```
┌─────────────────────────────────────────────────────────────────────┐
│                     sync_manager.py (FastAPI)                       │
│                                                                     │
│  POST /sync/block         POST /sync/unblock                        │
│  POST /sync/import-threats  POST /sync/full-sync                    │
│  GET  /sync/status                                                  │
└──────────┬─────────────────┬────────────────┬───────────────────────┘
           │                 │                │             │
           ▼                 ▼                ▼             ▼
    ┌────────────┐  ┌──────────────┐  ┌───────────┐  ┌──────────┐
    │   Redis    │  │   DynamoDB   │  │    CF KV  │  │ AWS WAF  │
    │ (on-prem)  │  │   (AWS)      │  │ (Workers) │  │  IP Set  │
    └────────────┘  └──────────────┘  └───────────┘  └──────────┘
         ▲                                  ▲
         │                                  │
    ┌────────────┐              ┌────────────────────────────┐
    │ ip_detection│              │  CF Worker (TypeScript)    │
    │ _pipeline.py│              │  cloudflare/src/           │
    │ (FastAPI)   │              │  Gates 1-8 at edge         │
    └────────────┘              └────────────────────────────┘
```

### How the three platforms share threat intelligence

**On-premises (Redis)** is the canonical source of truth. All manual block/unblock operations write here first. The Redis ZSET (`ip_blacklist:entries`) holds the live set with lazy TTL expiry.

**AWS DynamoDB** mirrors the Redis canonical set with DynamoDB-native TTL auto-expiry. It powers the Lambda-based gateway (`lambda_ip_gate.py`) and is the read source for the AWS WAF sync. The Lambda reads from DynamoDB; the WAF IP set is rebuilt from DynamoDB HIGH/PERMANENT severity entries.

**Cloudflare Workers KV** (`IP_BLACKLIST` namespace) is the edge-cached read layer. The TypeScript Worker reads `bl:<ip>` keys with a 5-minute edge-cache TTL. After the first request to a CF edge PoP for a given IP, subsequent lookups are served from memory. Writes happen out-of-band through this sync layer, never from the hot-path Worker.

### Sync patterns

| Operation | Pattern | Consistency |
|-----------|---------|-------------|
| `POST /sync/block` | Fan-out push to all platforms in parallel | Eventual — all platforms converge within seconds |
| `POST /sync/unblock` | Fan-out delete to all platforms in parallel | Eventual |
| `POST /sync/import-threats` | Bulk push; Redis and DynamoDB use batch writers, CF KV uses the 10,000-item bulk endpoint | Eventual |
| `POST /sync/full-sync` | Read canonical list from Redis; compute diff per platform; push delta | Converges to Redis state |
| Cron (`deploy.sh`) | Run consolidator, then `import-threats` | Daily refresh |

The system intentionally accepts eventual consistency. A freshly blocked IP may take up to 5 minutes to propagate to all CF edge PoPs (the KV `cacheTtl: 300` in `blacklist.ts`). For immediate edge enforcement, use `POST /sync/block` without the cache layer — the KV PUT bypasses the edge cache because it's a write, not a read.

### Data source priority in gate_orchestrator.py

Each gate selects the richest available signal source at runtime:

```
CF Worker headers  →  MaxMind GeoIP2 databases  →  Redis heuristics  →  skip
(best accuracy)       (good accuracy)               (fallback)
```

Gate 1 (IP Type) and Gate 2 (VPN Detection) benefit most from CF headers because Cloudflare's Radar threat intelligence is real-time and global. Without CF headers (e.g., direct on-premises traffic), the orchestrator falls back to MaxMind ASN lookups, which are updated monthly and may miss newly-registered datacenter ranges.

---

## File Reference

| File | Purpose |
|------|---------|
| `platform_adapters.py` | Adapter classes for Redis, DynamoDB, CF KV, AWS WAF |
| `sync_manager.py` | FastAPI sync service — all 5 API endpoints |
| `gate_orchestrator.py` | Unified 8-gate pipeline with data-source selection |
| `test_sync.py` | Integration tests (fakeredis, moto, respx) |
| `deploy.sh` | Cron-friendly deployment script |

---

## Deployment

### Prerequisites

```bash
pip install fastapi uvicorn redis boto3 httpx
# For test suite only:
pip install pytest pytest-asyncio fakeredis "moto[dynamodb,wafv2]" respx
```

MaxMind GeoIP2 databases are optional. Download from https://dev.maxmind.com/geoip/geolite2-free-geolocation-data and set:

```bash
export MAXMIND_DB_PATH=/var/lib/GeoIP/GeoLite2-ASN.mmdb
export MAXMIND_CITY_DB_PATH=/var/lib/GeoIP/GeoLite2-City.mmdb
```

### On-premises (Redis + FastAPI)

```bash
# Required
export REDIS_URL=redis://localhost:6379/0

# Optional — AWS and Cloudflare adapters are disabled when not set
export AWS_REGION=us-east-1
export DYNAMODB_TABLE=ip-blacklist
export WAF_IP_SET_ID=<your-waf-ip-set-id>
export WAF_IP_SET_NAME=igaming-blocked-ips
export CF_ACCOUNT_ID=<your-cf-account-id>
export CF_API_TOKEN=<your-cf-api-token>
export CF_KV_NAMESPACE_ID=<your-kv-namespace-id>

cd scripts/chapter-24/ip-detection/unified
uvicorn sync_manager:app --host 0.0.0.0 --port 8080
```

### AWS (Lambda / ECS)

The sync manager is a standard FastAPI application. For ECS:

```bash
# Build and push to ECR
docker build -t ip-sync-manager .
aws ecr get-login-password | docker login --username AWS --password-stdin <ecr-uri>
docker push <ecr-uri>/ip-sync-manager:latest
```

For Lambda, use Mangum as the ASGI adapter:

```python
from mangum import Mangum
from sync_manager import app
handler = Mangum(app)
```

### Cloudflare Workers

The sync manager pushes to CF KV via the REST API. The Workers themselves (`cloudflare/src/ip-detection-worker.ts`) only read from KV — they never write. Deploy the Worker separately with Wrangler:

```bash
cd scripts/chapter-24/ip-detection/cloudflare
wrangler deploy
```

After deploying, register the KV namespace ID:

```bash
export CF_KV_NAMESPACE_ID=$(wrangler kv:namespace list | jq -r '.[] | select(.title=="IP_BLACKLIST") | .id')
```

---

## Running the daily cron

```bash
# Minimal configuration (on-premises only)
REDIS_URL=redis://localhost:6379/0 \
THREAT_LIST_OUTPUT_DIR=/opt/threat-lists/output \
./deploy.sh

# Full three-platform deployment
REDIS_URL=redis://localhost:6379/0 \
AWS_REGION=us-east-1 \
DYNAMODB_TABLE=ip-blacklist \
WAF_IP_SET_ID=<id> \
CF_ACCOUNT_ID=<id> \
CF_API_TOKEN=<token> \
CF_KV_NAMESPACE_ID=<id> \
./deploy.sh

# Dry run — prints what would happen without making any changes
./deploy.sh --dry-run

# Skip the consolidator and push existing output files only
./deploy.sh --skip-consolidate

# Target a subset of platforms
./deploy.sh --platforms redis,cloudflare_kv
```

Crontab entry (daily at 03:15 UTC):

```cron
15 3 * * * /opt/ip-detection/unified/deploy.sh >> /var/log/ip-sync-deploy.log 2>&1
```

---

## Adding a new platform adapter

1. Create a class that extends `PlatformAdapter` in `platform_adapters.py`.
2. Implement all four abstract methods: `block_ip`, `unblock_ip`, `list_blocked`, `health_check`.
3. Optionally add a `bulk_block` method for efficient batch imports.
4. Register the adapter in `AdapterRegistry._init_if_needed()` in `sync_manager.py`.
5. Add integration tests to `test_sync.py` using an appropriate mock transport.

Minimal skeleton:

```python
class NewPlatformAdapter(PlatformAdapter):

    @property
    def platform_name(self) -> str:
        return "new_platform"

    def block_ip(self, ip: str, reason: str, ttl_seconds: int = 0, **kwargs) -> bool:
        ip = self._validate_ip(ip)
        # ... write to your platform ...
        return True  # True = newly added; False = already existed

    def unblock_ip(self, ip: str, **kwargs) -> bool:
        ip = self._validate_ip(ip)
        # ... delete from your platform ...
        return True  # True = was present and deleted

    def list_blocked(self) -> list[BlockedIP]:
        # ... read all active entries ...
        return []

    def health_check(self) -> HealthStatus:
        t0 = time.perf_counter()
        try:
            # ... probe the platform ...
            return HealthStatus(
                platform=self.platform_name,
                healthy=True,
                latency_ms=round((time.perf_counter() - t0) * 1000, 2),
            )
        except Exception as exc:
            return HealthStatus(
                platform=self.platform_name,
                healthy=False,
                latency_ms=round((time.perf_counter() - t0) * 1000, 2),
                error=str(exc),
            )
```

---

## Key design decisions

**Redis as the canonical source.** The sorted set scoring scheme (`score=0` for permanent, `score=expiry_epoch` for TTL entries) allows range queries against the expiry time without a secondary index. The `full-sync` reconciliation endpoint reads Redis and pushes deltas outward — this means Redis is the recovery source if any cloud platform loses state.

**Cloudflare KV bulk endpoint.** The `POST /bulk` endpoint accepts up to 10,000 key-value pairs per request, which maps cleanly to the 10K-entry batch sizes used in `consolidate-lists.py`. For the 307K+ IP dataset, the adapter issues approximately 31 bulk requests in sequence.

**WAF overflow handling.** AWS WAF enforces a hard 10,000-address limit per IP set. The `AWSWAFAdapter.batch_block()` method logs and reports overflow rather than silently truncating. For datasets larger than 10,000 IPs at WAF-worthy severity, create multiple WAF IP sets (one per category) and configure a WAF rule group that ORs all sets.

**Optimistic locking on WAF.** Every WAF UpdateIPSet call requires the current lock token. On `WAFOptimisticLockException`, the adapter retries up to 3 times with exponential back-off (100ms, 200ms, 400ms). Concurrent updates to the same IP set from multiple processes should use distributed locking (e.g., a Redis lock) to reduce retry frequency.

**Graceful degradation.** The gate orchestrator runs gates regardless of which data sources are available. If MaxMind databases are absent, gates 1-3 fall back to CF headers only. If CF headers are absent (direct traffic), they fall back to MaxMind. If both are absent, ASN-based gates still check the static DATACENTER_ASNS set, which covers the 14 largest cloud providers.
