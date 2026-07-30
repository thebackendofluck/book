# Chapter 31b Scripts: Cache, DNS, and Traffic Surge Capacity

These scripts support Chapter 31b. They are safe by default: they calculate or
inspect, but they do not apply DNS, firewall, sysctl, Kubernetes, or load
balancer changes.

## Scripts

| Script | Purpose |
|---|---|
| `capacity_model.py` | Calculates RPS, bandwidth, origin load after cache, public IP/LB/pod/shard/partition counts, and connection pressure. |
| `dns_ttl_plan.py` | Builds a DNS TTL runway for planned cutovers and failovers. |
| `linux_lb_preflight.sh` | Read-only Linux and load-balancer preflight for packet, socket, conntrack, backlog, file descriptor, and MTU indicators. |
| `k6-cache-surge.js` | k6 scenario for cacheable, uncached, and money-path endpoint mixes during surge tests. |
| `loaderio_plan.sh` | Generates loader.io API commands for host verification, controlled test creation, run, stop, and result retrieval. |

## Examples

Capacity model:

```bash
python3 writing/new-book/scripts/chapter-31b/cache-dns-capacity/capacity_model.py \
  --target-rps 100000 \
  --avg-response-bytes 8192 \
  --avg-request-bytes 1200 \
  --cache-hit-ratio 0.95 \
  --api-pod-rps 800 \
  --lb-node-rps 10000 \
  --public-ip-rps 50000 \
  --redis-ops-per-request 2 \
  --db-queries-per-request 0.2 \
  --accepted-bet-rps 5000 \
  --events-per-bet 8
```

DNS TTL runway:

```bash
python3 writing/new-book/scripts/chapter-31b/cache-dns-capacity/dns_ttl_plan.py \
  --hostname www.example-casino.com \
  --current-ttl 3600 \
  --normal-ttl 900 \
  --cutover-at 2026-05-01T20:00:00Z
```

Linux/LB preflight:

```bash
bash writing/new-book/scripts/chapter-31b/cache-dns-capacity/linux_lb_preflight.sh
bash writing/new-book/scripts/chapter-31b/cache-dns-capacity/linux_lb_preflight.sh --target 10.0.10.180
```

k6 cache surge:

```bash
k6 run \
  -e BASE_URL=https://staging.acmetocasino.com \
  -e TARGET_RPS=10000 \
  -e CACHEABLE_PATHS=/,/api/games,/api/games/categories \
  -e UNCACHED_PATHS=/api/v2/dash/health \
  -e MONEY_PATHS=/api/wallet/balance \
  writing/new-book/scripts/chapter-31b/cache-dns-capacity/k6-cache-surge.js
```

loader.io external edge canary:

```bash
./writing/new-book/scripts/chapter-31b/cache-dns-capacity/loaderio_plan.sh \
  --host loadtest.example-casino.com \
  --url https://loadtest.example-casino.com/ \
  --name edge-cache-canary \
  --type maintain-load \
  --duration 300 \
  --initial 100 \
  --total 1000
```

To execute API calls instead of printing the plan:

```bash
export LOADERIO_API_KEY="..."

./writing/new-book/scripts/chapter-31b/cache-dns-capacity/loaderio_plan.sh \
  --host loadtest.example-casino.com \
  --url https://loadtest.example-casino.com/ \
  --name edge-cache-canary \
  --type maintain-load \
  --duration 300 \
  --initial 100 \
  --total 1000 \
  --execute
```

## Interpretation

For any event rehearsal, collect:

- edge RPS and origin RPS
- cache hit ratio
- p95 and p99 latency
- error rate
- load balancer active connections
- TLS handshake rate
- Redis latency and evictions
- PostgreSQL query rate and lock waits
- Kafka event rate and consumer lag
- conntrack usage
- packet drops and retransmits

The scripts do not prove capacity alone. They give the test plan the numbers it
must prove.

## Safety

- Scripts calculate or inspect by default.
- No script changes DNS, firewall, sysctl, Kubernetes, Cloudflare or pfSense state.
- Do not run high-RPS k6 profiles against production without a signed test window.
- Do not run loader.io stress profiles against production without a signed test
  window, current generator IP allowlist review, Grafana annotation, and a
  rollback owner.
