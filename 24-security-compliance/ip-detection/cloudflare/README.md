# Chapter 24 — Cloudflare Workers IP Detection Pipeline

An 8-gate sequential security chain that runs at the Cloudflare edge for
iGaming platforms. Every decision completes in under 5 ms total (p95) using
only Cloudflare-native APIs — no external HTTP calls during request processing.

## Architecture

```
Incoming Request
      |
      v
 ┌─────────────────────────────────────────────────────────────┐
 │                  CF Edge Worker                              │
 │                                                             │
 │  Gate 1: IP Type          cf.asn, cf.isTor, botScore       │
 │     |                     <0.05 ms  in-memory               │
 │     v (pass)                                                │
 │  Gate 2: VPN              cf.isAnonymousVpn, cf.isAnonymous │
 │     |                     <0.05 ms  in-memory               │
 │     v (pass)                                                │
 │  Gate 3: Known Proxy      PROXY_ASNS, org-name patterns     │
 │     |                     <0.05 ms  in-memory               │
 │     v (pass)                                                │
 │  Gate 4: IP Blacklist     KV: IP_BLACKLIST                  │
 │     |                     <1 ms     1 KV read               │
 │     v (pass)                                                │
 │  Gate 5: Fraud Score      KV: FRAUD_VELOCITY (3 parallel)   │
 │     |                     <1 ms     velocity + signals      │
 │     v (pass)                                                │
 │  Gate 6: Device FP        KV: DEVICE_FINGERPRINTS (JA3)     │
 │     |                     <1 ms     2–3 KV reads            │
 │     v (pass)                                                │
 │  Gate 7: Sanctions        in-memory + KV: SANCTIONS_LIST    │
 │     |                     <1 ms     1–5 parallel KV reads   │
 │     v (pass)                                                │
 │  Gate 8: KYC Status       D1: PLAYER_DB                     │
 │                           <2 ms     1 prepared statement    │
 └─────────────────────────────────────────────────────────────┘
      |
      v
  pass  →  forward to origin (with X-Security-* headers)
  block →  403 { error: "ACCESS_DENIED", code: "<ReasonCode>" }
  review → 403 { error: "UNDER_REVIEW",  code: "<ReasonCode>" }
```

### Early-Return Pattern

The chain exits at the first `block` action. A request blocked at Gate 1
never triggers any KV reads. A request blocked at Gate 4 never runs the D1
query in Gate 8. This keeps the common-case latency near the in-memory gates
(< 0.1 ms) while only paying KV and D1 costs for traffic that passes the
cheap gates.

## Performance Budget

| Gate | Check                 | Mechanism              | Budget  |
|------|-----------------------|------------------------|---------|
| 1    | IP type / Tor / ASN   | In-memory Set + regex  | 0.05 ms |
| 2    | VPN detection         | CF object flags        | 0.05 ms |
| 3    | Known proxy           | In-memory Set + regex  | 0.05 ms |
| 4    | IP blacklist          | 1 KV read (cached)     | <1 ms   |
| 5    | Fraud score           | 3 parallel KV reads    | <1 ms   |
| 6    | Device fingerprint    | 2–3 parallel KV reads  | <1 ms   |
| 7    | Sanctions / PEP       | In-mem + 1–5 KV reads  | <1 ms   |
| 8    | KYC status            | 1 D1 prepared stmt     | <2 ms   |
|      | **Total (worst case)**|                        | **<5 ms** |

KV reads use `cacheTtl` so edge-cached entries are served from local memory
after the first request to a PoP — effectively 0 ms for hot keys.

## Reason Codes

| Code                  | Gate | Description                                |
|-----------------------|------|--------------------------------------------|
| `BANNED_PROXY_TOR`    | 1    | Tor exit node or bot score below threshold |
| `BANNED_PROXY_DC`     | 1    | Datacenter / hosting ASN or org name       |
| `BANNED_PROXY_VPN`    | 2    | Cloudflare VPN / anonymous proxy flag      |
| `BANNED_PROXY_KNOWN`  | 3    | Known VPN provider ASN or org name         |
| `BANNED_IP_BLACKLIST` | 4    | IP is on the manual blacklist              |
| `HIGH_FRAUD_SCORE`    | 5    | Weighted fraud score above threshold       |
| `DEVICE_ANOMALY`      | 6    | JA3 blocklist hit or fingerprint churn     |
| `SANCTIONS_MATCH`     | 7    | OFAC country block or SDN name token match |
| `KYC_BLOCKED`         | 8    | Player KYC rejected, frozen, or absent     |
| `KYC_PENDING`         | 8    | Player KYC under review (soft block)       |

## Cloudflare Resources

### KV Namespaces

| Binding              | Purpose                                       | Key pattern                        |
|----------------------|-----------------------------------------------|------------------------------------|
| `IP_BLACKLIST`       | Manual IP bans with optional TTL              | `bl:<ip>`                          |
| `DEVICE_FINGERPRINTS`| JA3 hash history per IP and per hash          | `ja3:ip:<ip>`, `ja3:hash:<hash>`   |
| `FRAUD_VELOCITY`     | Velocity counters (1m, 5m, 1h windows)        | `vel:<ip>:1m`, `vel:<ip>:5m`, ...  |
| `SANCTIONS_LIST`     | OFAC country codes and SDN name tokens        | `sanctions:country:<ISO2>`, `sanctions:name:<token>` |

### D1 Database

Table: `player_kyc`

```sql
CREATE TABLE player_kyc (
  player_id   TEXT PRIMARY KEY,
  status      TEXT NOT NULL CHECK (status IN ('none','pending','approved','rejected','frozen')),
  tier        INTEGER NOT NULL DEFAULT 0,
  reviewed_at TEXT,
  reviewer    TEXT,
  notes       TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
```

## Deployment

### 1. Provision infrastructure

```bash
cd terraform
terraform init
terraform apply -var="cloudflare_account_id=<your-account-id>" -var="environment=production"
```

Copy the output IDs into `wrangler.toml` — replace every `*_ID` placeholder.

### 2. Run D1 migrations

```bash
npm run db:migrate:production
```

### 3. Deploy the worker

```bash
npm install
npm run deploy:production
```

### 4. Verify

```bash
# Health check (bypasses the security pipeline)
curl https://casino.example.com/health

# Expected: {"status":"ok"}
```

## Local Development

```bash
npm install
wrangler dev
```

Wrangler dev runs a local miniflare instance with KV and D1 stubs. Modify
`wrangler.toml` to point at your preview KV namespace IDs.

## Tests

```bash
npm test              # run once
npm run test:watch    # watch mode
npm run test:coverage # with coverage report
```

The test suite uses in-memory mocks for all KV and D1 bindings — no live
Cloudflare resources are required.

## Configuration Tuning

All thresholds are in `src/types.ts` under `DEFAULT_CONFIG`:

| Field                  | Default | Description                                       |
|------------------------|---------|---------------------------------------------------|
| `botScoreThreshold`    | 40      | CF Bot score below which traffic is blocked       |
| `fraudBlockThreshold`  | 75      | Fraud score that triggers an outright block       |
| `fraudReviewThreshold` | 50      | Fraud score that triggers compliance review       |
| `rateLimit1m`          | 60      | Max requests per IP per minute                    |
| `ja3DistinctLimit`     | 3       | Max distinct JA3 hashes per IP in 1h window       |

Adjust these values by modifying `DEFAULT_CONFIG` or by reading them from a KV
config key at startup — the latter enables runtime updates without redeployment.

## Fraud Score Weights

Gate 5 computes a weighted additive score (0–100):

| Signal                         | Max Weight |
|--------------------------------|------------|
| 1-minute request velocity      | 20         |
| 5-minute request velocity      | 15         |
| 1-hour request velocity        | 10         |
| CF Bot score (inverted)        | 20         |
| Accept-Language vs country     | 15         |
| Missing User-Agent             | 10         |
| Known automation UA string     | 10         |

## Security Notes

- The worker **never** exposes the `detail` field to the client. The 403
  response body contains only `error` and `code` — sufficient for client-side
  localisation without leaking intelligence about detection logic.
- Gate 7 returns `review` (not `block`) on name token matches to avoid
  false-positive blocks on common names. Full name verification happens in the
  compliance back-office.
- Gate 8 is skipped for unauthenticated requests. This is intentional:
  unauthenticated browsing does not require KYC.
- Velocity counters (Gate 5) use KV put/get rather than a Durable Object.
  This provides approximate counting — acceptable for rate-limiting. For
  strict atomicity (e.g., hard deposit limits), replace with a Durable Object
  counter.
