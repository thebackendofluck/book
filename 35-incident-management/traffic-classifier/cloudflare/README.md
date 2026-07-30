# Edge Traffic Classifier — Cloudflare Workers

A Cloudflare Workers implementation of edge-level DDoS detection and campaign-aware rate limiting for the casino platform. Runs on every inbound request before it reaches the origin, classifying traffic in under 2 ms of CPU time.

## Architecture

```
Browser / Bot
     │
     ▼
┌─────────────────────────────────────────────────────────────────┐
│  Cloudflare Edge PoP                                            │
│                                                                 │
│  ┌──────────────────────┐   ┌──────────────┐  ┌────────────┐  │
│  │  edge-classifier.ts  │──▶│  RATE_LIMITS │  │ CAMPAIGNS  │  │
│  │  (main Worker)       │   │  KV (TTL)    │  │ KV (TTL)   │  │
│  │                      │──▶│              │  │            │  │
│  │  ① cf signals        │   └──────────────┘  └────────────┘  │
│  │  ② JA3 check         │   ┌──────────────┐  ┌────────────┐  │
│  │  ③ IP rate counter   │──▶│  JA3_BLOCKLIST│  │ ATTACK_LOG │  │
│  │  ④ ASN rate counter  │   │  KV           │  │ KV         │  │
│  │  ⑤ geo spike         │   └──────────────┘  └────────────┘  │
│  │  ⑥ campaign lookup   │   ┌──────────────────────────────┐  │
│  │  ⑦ classify + act    │──▶│  AttackCounter               │  │
│  └──────────────────────┘   │  Durable Object (per ASN)    │  │
│           │                 └──────────────────────────────┘  │
│    ALLOW  │  RATE_LIMIT  │  JS_CHALLENGE  │  CAPTCHA  │  BLOCK │
└───────────┼──────────────┼────────────────┼───────────┼────────┘
            │              │                │           │
            ▼              ▼                ▼           ▼
         Origin          429 →          Challenge    403 +
                        Retry           Page         waitUntil()
                                                   (persist event)
```

### Decision Pipeline (hot path, runs in < 2 ms)

All state reads are concurrent (`Promise.all`). No synchronous HTTP calls to origin during classification.

| Step | Check | Source | Cost |
|------|-------|--------|------|
| 1 | IP hard-block | `RATE_LIMITS` KV | 1 KV get |
| 2 | JA3 hash | `JA3_BLOCKLIST` KV | 1 KV get |
| 3 | Campaign multiplier | `CAMPAIGNS` KV | 1 KV get |
| 4 | IP rate counters (sec + min) | `RATE_LIMITS` KV | 4 KV get+put |
| 5 | ASN rate counters (sec + min) | `RATE_LIMITS` KV | (above batch) |
| 6 | Geo spike | `RATE_LIMITS` KV | 1 KV get |
| 7 | Bot/threat score | `request.cf` object | 0 (in-memory) |

Steps 1–6 all run in a single `Promise.all` call. KV reads at Cloudflare edge PoPs are served from the edge cache with sub-millisecond latency.

### Graduated Response

```
NORMAL   → ALLOW           (pass through to origin)
SUSPICIOUS (1 weak signal) → RATE_LIMIT (429, Retry-After)
SUSPICIOUS (1 signal)      → JS_CHALLENGE (proof-of-work HTML)
SUSPICIOUS (2+ signals)    → CAPTCHA
ATTACK                     → BLOCK (403) + async evidence persist
```

Campaign active for the request's geo: all rate limit thresholds are multiplied by `campaign.multiplier` (1–20x), and the JS challenge step is skipped for borderline traffic.

---

## Files

```
cloudflare/
├── src/
│   ├── types.ts            Shared types, threshold constants, KV TTLs
│   ├── edge-classifier.ts  Main Worker — classification hot path + response
│   ├── campaign-manager.ts Admin API for marketing campaign lifecycle
│   ├── attack-logger.ts    Durable Object + attack evidence export API
│   └── scale-signal.ts     Signals origin autoscaler on campaign/attack events
├── test/
│   └── edge-classifier.test.ts  Vitest tests for all scenarios
├── wrangler.toml
├── package.json
├── tsconfig.json
└── vitest.config.ts
```

---

## KV Namespaces

| Binding | Purpose | Key pattern | TTL |
|---------|---------|-------------|-----|
| `RATE_LIMITS` | Per-IP/ASN rolling window counters, blocked IP list, geo baselines | `ip_m:<ip>:<window>`, `blocked_ip:<ip>`, `geo_baseline:<CC>` | 2–3600 s |
| `CAMPAIGNS` | Active campaign records, campaign history | `campaign_active:geo:<CC>`, `campaign_history:<ts>:<CC>` | campaign duration / 30 days |
| `ATTACK_LOG` | Blocked event payloads | `event:<ts>:<ray>` | 24 h |
| `JA3_BLOCKLIST` | Blocked JA3 hashes | `<ja3_hash>` | indefinite |

---

## API Reference

All admin routes require the `X-Admin-Secret` header. The secret is stored as a Worker secret (not in `wrangler.toml`).

### Campaign Management

**Start a campaign**
```http
POST /campaign/start
X-Admin-Secret: <secret>
Content-Type: application/json

{
  "geo": "BR",
  "multiplier": 5,
  "durationSeconds": 10800,
  "note": "Super Bowl bonus launch"
}
```

Response `201`:
```json
{
  "status": "started",
  "campaign": { "geo": "BR", "multiplier": 5, "startedAt": 1711900000000, "expiresAt": 1711939800000 },
  "message": "Campaign for BR active for 10800s with 5x multiplier"
}
```

**Stop a campaign**
```http
POST /campaign/stop
X-Admin-Secret: <secret>
Content-Type: application/json

{ "geo": "BR" }
```

**List active campaigns**
```http
GET /campaign/active
X-Admin-Secret: <secret>
```

---

### Attack Log

**Export all blocked events (for ISP abuse reports)**
```http
GET /attacks/export
X-Admin-Secret: <secret>
```

Response `200`:
```json
{
  "exportedAt": "2026-03-31T00:00:00.000Z",
  "count": 142,
  "events": [
    {
      "ip": "1.2.3.4",
      "asn": "AS12345",
      "country": "XX",
      "ja3": "abc...def",
      "timestamp": 1711900000000,
      "reasons": ["bot_score:5", "ip_req_per_min:450"],
      "action": "BLOCK",
      "ray": "abc123-LHR"
    }
  ]
}
```

**ASN summary (for network-level blocking)**
```http
GET /attacks/asn-summary
X-Admin-Secret: <secret>
```

**Flush after filing abuse report**
```http
POST /attacks/flush
X-Admin-Secret: <secret>
Content-Type: application/json

{ "confirm": "FLUSH" }
```

---

### Scale Signals

**Manual scale-up**
```http
POST /scale/up
X-Admin-Secret: <secret>
Content-Type: application/json

{
  "profile": {
    "minInstances": 10,
    "maxInstances": 50,
    "targetCpuPercent": 60,
    "reason": "pre-campaign warmup"
  }
}
```

**Manual scale-down**
```http
POST /scale/down
X-Admin-Secret: <secret>
Content-Type: application/json

{ "gracePeriodSeconds": 900, "reason": "post-campaign drain" }
```

**Autoscaler status**
```http
GET /scale/status
X-Admin-Secret: <secret>
```

---

## Deployment

### Prerequisites

- Cloudflare Workers Paid plan (required for Durable Objects)
- Bot Management add-on (required for `cf.botManagement.score` and JA3 hash)
- Node.js 20+ and npm

### Initial setup

```bash
npm install
npx wrangler login

# Create KV namespaces
npx wrangler kv:namespace create RATE_LIMITS
npx wrangler kv:namespace create RATE_LIMITS --preview
npx wrangler kv:namespace create CAMPAIGNS
npx wrangler kv:namespace create CAMPAIGNS --preview
npx wrangler kv:namespace create ATTACK_LOG
npx wrangler kv:namespace create ATTACK_LOG --preview
npx wrangler kv:namespace create JA3_BLOCKLIST
npx wrangler kv:namespace create JA3_BLOCKLIST --preview

# Replace the placeholder IDs in wrangler.toml with the IDs output above.

# Store the admin secret as a Worker secret (never in wrangler.toml)
npx wrangler secret put ADMIN_SECRET
```

### Populate the JA3 blocklist

```bash
# Add a known-bad JA3 hash
npx wrangler kv:key put --binding=JA3_BLOCKLIST \
  "abc123def456abc123def456abc12345" "blocked"

# Remove a hash
npx wrangler kv:key delete --binding=JA3_BLOCKLIST \
  "abc123def456abc123def456abc12345"
```

### Local development

```bash
npm run dev
# Worker is available at http://localhost:8787
```

### Tests

```bash
npm test
# Or with coverage
npm run test:coverage
```

### Deploy

```bash
# Preview deployment
npm run deploy

# Production
npm run deploy:prod
```

---

## Thresholds (defaults)

These are defined in `src/types.ts` and apply before any campaign multiplier.

| Threshold | Value | Description |
|-----------|-------|-------------|
| `IP_PER_MINUTE_WARN` | 120 req/min | IP enters SUSPICIOUS |
| `IP_PER_MINUTE_ATTACK` | 300 req/min | IP enters ATTACK |
| `IP_PER_SECOND_ATTACK` | 20 req/s | IP enters ATTACK |
| `ASN_PER_MINUTE_WARN` | 3,000 req/min | ASN enters SUSPICIOUS |
| `ASN_PER_MINUTE_ATTACK` | 8,000 req/min | ASN enters ATTACK |
| `BOT_SCORE_SUSPICIOUS` | ≤ 30 | cf.botManagement.score threshold |
| `BOT_SCORE_ATTACK` | ≤ 10 | cf.botManagement.score threshold |
| `THREAT_SCORE_SUSPICIOUS` | ≥ 10 | cf.threatScore threshold |
| `THREAT_SCORE_ATTACK` | ≥ 25 | cf.threatScore threshold |
| `GEO_SPIKE_MULTIPLIER` | 5× baseline | Marks country as geo spike |

---

## Operational Notes

**Race conditions in KV counters**: The per-IP/ASN counters use a read-modify-write pattern. At very high throughput (>10,000 req/s to a single PoP) some increments will be lost due to concurrent writes. This is intentional — the error is bounded and self-corrects on each window rollover. For authoritative aggregate counts, use the Durable Object (`ATTACK_COUNTER`) which serialises all writes.

**KV eventual consistency**: Cloudflare Workers KV is eventually consistent across PoPs with ~60 s propagation. This means a blocked IP may briefly continue receiving challenges at distant PoPs after the `blocked_ip:` key is written. The 1-hour TTL on blocked IPs means this resolves automatically.

**Campaign rollover**: Campaigns expire via KV TTL. If the origin crashes before `POST /campaign/stop` can be called, the campaign will expire naturally at `expiresAt`. The scale-down signal in `onCampaignStop` should also be called manually in this case via `POST /scale/down`.

**Durable Object placement**: Each `AttackCounter` DO instance is pinned to a specific Cloudflare region based on its name (the ASN string). This means ASN summary data is accurate but may be slightly delayed at regional failover.
