# k6 Load Testing Framework — iGaming Platform

Production-grade k6 load testing framework for the iGaming platform at `new.acmetocasino.com`. Covers all critical player journeys: casino game sessions, sportsbook betting, live in-play wagering, WebSocket odds feeds, and payment operations.

---

## Directory Structure

```
load-testing/
├── config.js                   Shared config: URLs, profiles, thresholds, test data
├── run.sh                      CLI runner: scenario selection, Docker/Cloud modes
├── helpers/
│   ├── requests.js             Reusable HTTP functions for every API endpoint
│   └── checks.js               Custom metrics + assertion helpers
└── scenarios/
    ├── sustained-load.js       Steady-state production load (30 min)
    ├── peak-traffic.js         World Cup Final simulation (255 min)
    ├── spike-test.js           Goal-event surge — 3 spike cycles (43 min)
    └── soak-test.js            4-hour stability / memory-leak detection
```

---

## Prerequisites

### Local execution

```bash
# macOS
brew install k6

# Linux
sudo gpg -k
sudo gpg --no-default-keyring \
     --keyring /usr/share/keyrings/k6-archive-keyring.gpg \
     --keyserver hkp://keyserver.ubuntu.com:80 \
     --recv-keys C5AD17C747E3415A3642D57D77C6C491D6AC1D69
echo "deb [signed-by=/usr/share/keyrings/k6-archive-keyring.gpg] https://dl.k6.io/deb stable main" \
     | sudo tee /etc/apt/sources.list.d/k6.list
sudo apt-get update && sudo apt-get install k6

# Verify
k6 version   # requires >= 0.47.0
```

### Docker (no local install)

```bash
docker pull grafana/k6:latest
./run.sh sustained -d
```

### k6 Cloud

```bash
export K6_CLOUD_TOKEN=<token-from-app.k6.io>
./run.sh sustained -c
```

---

## Quick Start

```bash
# Smoke test — verify scripts work end-to-end (5 min)
./run.sh sustained -p smoke

# Standard load test against dev
./run.sh sustained

# Load test against staging
./run.sh sustained -u https://staging.acmetocasino.com

# Spike test — goal surge simulation
./run.sh spike -u https://staging.acmetocasino.com

# Peak traffic — World Cup Final at 10% scale (dev/staging safe)
./run.sh peak -s 0.1 -u https://staging.acmetocasino.com

# Soak test — 4 hours at 30% load
./run.sh soak -u https://staging.acmetocasino.com

# Export results to InfluxDB + Grafana
./run.sh sustained -o influxdb -t http://localhost:8086/k6
```

---

## Test Profiles

| Profile  | VUs (×1 scale) | Duration | Purpose |
|----------|---------------|----------|---------|
| `smoke`  | 5             | 5 min    | Script validation, no load |
| `load`   | 500           | 30 min   | Normal production peak |
| `stress` | 500 → 3 000   | 35 min   | Find breaking point |
| `spike`  | 100 → 5 000   | 8 min    | Auto-scaling responsiveness |
| `soak`   | 300           | 4 h      | Memory / connection stability |

Set with `-p PROFILE` or `export TEST_PROFILE=stress`.

---

## VU Scaling

The `VU_SCALE` multiplier applies to all VU counts in every scenario. Use it to match your hardware and target environment capacity.

| VU_SCALE | Peak VUs (peak-traffic) | Equivalent concurrency | Recommended for |
|----------|------------------------|------------------------|-----------------|
| 0.01     | ~15                    | Development laptop     | Script debugging |
| 0.1      | ~150                   | Dev environment        | Feature validation |
| 1        | ~1 500                 | Single load generator  | Staging gate |
| 10       | ~15 000                | Small k6 Cloud cluster | Pre-production |
| 100      | ~150 000               | Large distributed run  | Production dress rehearsal |

```bash
./run.sh peak -s 0.1   # 10% scale, dev-safe
./run.sh peak -s 10    # 10x scale — requires distributed k6
```

---

## Scenarios in Detail

### `sustained-load.js`

Simulates a normal production day. Four concurrent VU groups:

- **Casino players** (45%): lobby → game category → launch game → play rounds → RNG verify
- **Sportsbook players** (30%): browse events → check odds → place bet → monitor
- **Wallet users** (10%): deposit → balance → transaction history → withdrawal
- **Passive browsers** (15%): lobby + category browsing only

SLO thresholds: `p95 < 500 ms`, `error rate < 1%`, `wallet success > 99.9%`.

### `peak-traffic.js`

Full World Cup Final lifecycle based on the Chapter 41 case study. Six concurrent scenarios:

1. Pre-match ramp (T-60 min to kickoff)
2. First half with goal spikes at ~23' and ~36'
3. Half-time burst (deposit + second-half markets)
4. Second half — Mbappe-brace double-spike (two goals, 97 seconds apart)
5. Extra time and penalty shootout (absolute peak: ~16× baseline)
6. Post-match settlement storm (1.2 M bet resolutions)

Parallel WebSocket odds feed runs throughout all six phases.

Use `VU_SCALE=0.1` for a staging dress rehearsal. Use `VU_SCALE=100` with a distributed k6 Cloud job for full production simulation.

### `spike-test.js`

Three spike cycles with increasing intensity:

| Cycle | Trigger | Multiplier |
|-------|---------|------------|
| 1     | First goal | 30× baseline |
| 2     | Second goal | 36× baseline |
| 3     | Penalty shootout | 45× baseline |

Each cycle: 30-second ramp → 3–5 minute hold → 5-minute recovery.
Tests that auto-scaling responds in under 90 seconds and that error rate stays below 5% during the peak.

### `soak-test.js`

Four-hour constant load at 300 VUs (scaled by `VU_SCALE`). Detects:

- Memory leaks: `soak_latency_p95_late` must not exceed `soak_latency_p95_early` by more than 20%
- Connection exhaustion: `soak_connection_errors` should not trend upward
- Token rotation bugs: `soak_token_renewals` counter validates JWT refresh pipeline
- WebSocket fd limits: `active_ws_connections` gauge stays bounded

Extend to 8 hours: `./run.sh soak --env SOAK_HOURS=8`.

---

## Custom Metrics

All custom metrics are defined in `helpers/checks.js` and exposed in k6 output.

| Metric | Type | Description |
|--------|------|-------------|
| `login_duration` | Trend | Auth endpoint latency |
| `lobby_load_duration` | Trend | Casino lobby response time |
| `game_launch_duration` | Trend | Game session init latency |
| `bet_placement_duration` | Trend | End-to-end bet placement |
| `wallet_op_duration` | Trend | Deposit / withdrawal latency |
| `rng_verify_duration` | Trend | RNG proof verification time |
| `odds_refresh_duration` | Trend | In-play odds fetch latency |
| `ws_connect_duration` | Trend | WebSocket handshake time |
| `bet_placement_success` | Rate | Successful bet ratio |
| `wallet_op_success` | Rate | Payment success ratio |
| `game_launch_success` | Rate | Game session success ratio |
| `bets_placed_total` | Counter | Total bets placed in run |
| `deposits_initiated_total` | Counter | Total deposit requests |
| `active_ws_connections` | Gauge | Live WebSocket connections |
| `active_game_sessions` | Gauge | Open game sessions |
| `soak_latency_p95_early` | Trend | Latency in first 20% of soak |
| `soak_latency_p95_late` | Trend | Latency in last 20% of soak |
| `spike_request_latency` | Trend | Latency at spike peak |
| `goals_simulated` | Counter | Reactive bets from WS events |

---

## Grafana + InfluxDB Dashboard

Send results to InfluxDB for real-time Grafana dashboards:

```bash
# Start local stack
docker compose up -d influxdb grafana

# Run test with InfluxDB output
./run.sh sustained -o influxdb -t http://localhost:8086/k6
```

Import the [k6 official Grafana dashboard](https://grafana.com/grafana/dashboards/2587) (ID: 2587), then add panels for the custom gambling metrics above.

Key panels to build:
- **Bet placement p95** over time (catch degradation during soak)
- **Active WebSocket connections** gauge
- **Wallet op success rate** — must stay above 99.9%
- **Error rate by endpoint** — heatmap showing which APIs fail first under stress
- **Spike recovery time** — seconds from spike start to p95 returning to baseline

---

## Infrastructure Requirements

### Self-Hosted k6 (Single Machine)

Generating realistic load requires substantial outbound network capacity. k6 is CPU-bound at ~1 000 VUs per core with HTTP keep-alive. Rule of thumb:

| VU Count | CPU | RAM | Network |
|----------|-----|-----|---------|
| Up to 500 | 2 vCPU | 2 GB | 100 Mbps |
| 500–2 000 | 4 vCPU | 4 GB | 500 Mbps |
| 2 000–10 000 | 8 vCPU | 8 GB | 1 Gbps |
| 10 000–50 000 | 32 vCPU | 32 GB | 10 Gbps |
| 50 000+ | Distributed | — | — |

For the full World Cup simulation (`VU_SCALE=100`, peak ~150 000 VUs), distributed k6 execution across 10–20 machines is required. k6 Cloud removes this operational burden.

Recommended instance types for self-hosted distributed load generation:

| Cloud | Instance | vCPU | RAM | Max VUs |
|-------|----------|------|-----|---------|
| AWS | c5.2xlarge | 8 | 16 GB | ~10 000 |
| AWS | c5.9xlarge | 36 | 72 GB | ~40 000 |
| GCP | c2-standard-8 | 8 | 32 GB | ~10 000 |
| Azure | F8s_v2 | 8 | 16 GB | ~10 000 |

### k6 Cloud Pricing

k6 Cloud charges per Virtual User Hour (VUH = 1 VU running for 1 hour).

| Test | VUs | Duration | VU-hours | Cost (est.) |
|------|-----|----------|----------|-------------|
| Smoke | 5 | 5 min | 0.4 | < $1 |
| Sustained load | 500 | 30 min | 250 | ~$3 |
| Sustained stress | 3 000 | 35 min | 1 750 | ~$18 |
| Spike (3 cycles) | 5 000 peak | 43 min | ~1 000 | ~$10 |
| Soak (4 h) | 300 | 4 h | 1 200 | ~$12 |
| Peak traffic (10% scale) | 1 500 peak | 255 min | ~2 500 | ~$25 |
| **Peak traffic (full)** | **150 000 peak** | **255 min** | **~150 000** | **~$1 500** |

Pricing based on k6 Cloud $0.01/VUH (Professional plan, March 2026). Actual costs depend on plan tier and committed usage. Large-scale tests benefit from the Enterprise plan with volume discounts.

**Self-hosted cost comparison (AWS, full World Cup simulation):**

| Component | Spec | Duration | Cost |
|-----------|------|----------|------|
| 10× c5.9xlarge load generators | 360 vCPU | 4.5 h | ~$120 |
| InfluxDB (r5.xlarge) | 4 vCPU | 4.5 h | ~$4 |
| Grafana (t3.medium) | 2 vCPU | 4.5 h | ~$0.50 |
| Data transfer | ~500 GB outbound | — | ~$45 |
| **Total** | | | **~$170** |

Self-hosted is approximately 9× cheaper for the full simulation, but requires an additional 4–8 hours of setup and teardown time. For teams running more than one full-scale simulation per month, self-hosted EC2 spot instances are the economical choice.

---

## CI/CD Integration

### GitHub Actions

```yaml
# .github/workflows/load-test.yml
name: Load Test Gate

on:
  push:
    branches: [main, staging]

jobs:
  load-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: grafana/setup-k6-action@v1

      - name: Smoke test
        run: ./writing/new-book/scripts/load-testing/run.sh sustained -p smoke
        env:
          BASE_URL: ${{ secrets.STAGING_URL }}

      - name: Load test
        if: github.ref == 'refs/heads/main'
        run: ./writing/new-book/scripts/load-testing/run.sh sustained -p load
        env:
          BASE_URL: ${{ secrets.STAGING_URL }}
```

### Threshold-as-code gate

k6 exits with code 99 when any threshold is breached.  CI pipelines treat exit code 99 as a build failure, blocking deployment when SLOs are not met.

```bash
# In your deploy pipeline:
./run.sh sustained -p load
if [ $? -ne 0 ]; then
  echo "Load test thresholds breached — deployment blocked"
  exit 1
fi
```

---

## Test Data Requirements

The target environment must have pre-seeded test accounts. The scripts expect:

- **Accounts**: `sustained_1` through `sustained_8000` (format: `sustained_N@load.acmetocasino.com`, password: `${LOADTEST_PASSWORD}`)
- **Casino games**: IDs listed in `config.js` under `TEST_DATA.slotGameIds`, `tableGameIds`, `liveCasinoGameIds`
- **Sports events**: IDs in `TEST_DATA.eventIds` with active markets
- **Payment sandbox**: Test card token pattern `test-card-token-{1-10}` accepted by payment gateway

Seed script example (adapt to your platform's admin API):

```bash
for i in $(seq 1 8000); do
  curl -s -X POST "${BASE_URL}/api/v2/admin/test-users" \
    -H "Authorization: Bearer ${ADMIN_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"sustained_${i}@load.acmetocasino.com\",\"password\":\"${LOADTEST_PASSWORD}\",\"balance\":10000}"
done
```

---

## Interpreting Results

### Pass / Fail

k6 exits 0 (pass) when all thresholds are met, 99 (fail) when any threshold is breached.

```
✓ http_req_failed.............: 0.12%  ✓ rate<0.01
✓ http_req_duration...........: p(95)=423ms p(99)=987ms ✓ p(95)<500 p(99)<1500
✓ bet_placement_duration......: p(95)=612ms ✓ p(95)<1000
✗ wallet_op_success...........: 99.7%  ✗ rate>0.999  ← FAIL: payment issues
```

### Key metrics to watch

- **`http_req_duration` p95 trend**: Should be flat throughout a soak. Rising p95 means resource exhaustion.
- **`bet_placement_success` rate**: Must stay above 99%. Drops indicate bet engine backpressure.
- **`wallet_op_success` rate**: Must stay above 99.9%. Any payment failure is a revenue leak.
- **`active_ws_connections`**: Should plateau, not grow unboundedly. Growth indicates connection leaks.
- **`http_req_failed` spike at test start**: Normal — first logins cold-start the auth cache. Should drop within 30 s.

---

## Related Chapters

- **Chapter 32** — Testing and QA in Gambling: statistical test batteries, RNG certification, WireMock service mocking
- **Chapter 41** — Case Study: Scaling for the World Cup: the production incident that informed the peak-traffic scenario design
- **Chapter 31** — Performance Benchmarks and Metrics: the SLO framework these thresholds enforce
