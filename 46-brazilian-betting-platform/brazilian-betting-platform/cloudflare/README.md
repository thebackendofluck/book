# Brazilian Betting Platform — Cloudflare Workers

Cloudflare Workers backend for a SIGAP-regulated Brazilian sports betting platform.
Deployed on `*.acmetocasino.bet.br`.

---

## Architecture

```
 PLAYER / PIX PSP / AWS ODDS PUBLISHER
                  │
       Cloudflare DDoS · WAF · Geo
                  │
 api-gateway · pix-webhook · odds-feed
        │                  │
        │                  └── eventual, read-only odds KV
        └── authenticated requests ──► AWS CORE
            bets · wallet · PAM/KYC (authoritative)

 Edge state: BettingSession DO · PIX delivery D1 · KYC intake R2
 Regulatory: prepared signed batch ──► Queue ──► SIGAP reporter
                                      D1 delivery ledger
                                      mTLS Fetcher ──► SIGAP
```

---

## Prerequisites

- Node.js >= 18.x
- Wrangler CLI >= 3.36.0
- Cloudflare account with Workers Paid plan (required for Cloudflare Queues; SQLite-backed Durable Objects also run on the Free plan, only legacy key-value Durable Objects require Paid)
- `.bet.br` domain added to your Cloudflare zone
- SIGAP operator credentials (issued by Secretaria de Prêmios e Apostas)
- Licensed PIX PSP agreement

---

## Initial Setup

### 1. Login to Cloudflare

```bash
npx wrangler login
```

Verify your account ID:
```bash
npx wrangler whoami
```

### 2. Install dependencies

```bash
npm install
```

### 3. Create D1 database

```bash
npm run db:create
```

Copy the database ID printed to stdout and set it in `wrangler.toml`:
```toml
[[d1_databases]]
binding     = "DB"
database_name = "bet_brazil_db"
database_id = "<PASTE-DATABASE-ID-HERE>"
```

Apply schema migrations:
```bash
# Local (development)
npm run db:migrate:local

# Remote (production)
npm run db:migrate:remote
```

### 4. Create KV namespaces

```bash
# Create all three namespaces
npm run kv:create:sessions
npm run kv:create:odds
npm run kv:create:limits
```

Each command prints an `id` and a `preview_id`. Update `wrangler.toml`:

```toml
[[kv_namespaces]]
binding    = "PLAYER_SESSIONS"
id         = "<PLAYER-SESSIONS-ID>"
preview_id = "<PLAYER-SESSIONS-PREVIEW-ID>"

[[kv_namespaces]]
binding    = "ODDS_CACHE"
id         = "<ODDS-CACHE-ID>"
preview_id = "<ODDS-CACHE-PREVIEW-ID>"

[[kv_namespaces]]
binding    = "RATE_LIMITS"
id         = "<RATE-LIMITS-ID>"
preview_id = "<RATE-LIMITS-PREVIEW-ID>"
```

### 5. Create R2 bucket

```bash
npm run r2:create
```

Verify in the Cloudflare dashboard under R2 > Buckets.

### 6. Configure the `.bet.br` domain

In the Cloudflare dashboard:

1. Add your zone `acmetocasino.bet.br`
2. Confirm nameservers are delegated to Cloudflare at Registro.br
3. The worker route `*.acmetocasino.bet.br/*` will be registered automatically on deploy

For the SIGAP-required `.bet.br` TLD:
- Register at [registro.bet.br](https://registro.bet.br) (requires active SIGAP license)
- Delegate NS to Cloudflare after registration is confirmed by SPA/MF

### 7. Set secrets

Never put secrets in `wrangler.toml`. Use `wrangler secret put` for each:

```bash
# JWT signing key (min 32 chars, high entropy)
echo "$(openssl rand -base64 48)" | npx wrangler secret put JWT_SECRET

# HMAC secret shared with PIX PSP (get from PSP onboarding portal)
npx wrangler secret put PIX_HMAC_SECRET

# HMAC used by Workers when calling the authoritative AWS core
npx wrangler secret put AWS_CORE_HMAC_SECRET

# HMAC used to authenticate snapshots pushed by the AWS odds publisher
npx wrangler secret put ODDS_PUBLISHER_HMAC_SECRET --config wrangler.odds-feed.toml

# Temporary reception token issued by SIGAP (rotate before expiry)
npx wrangler secret put SIGAP_BEARER_TOKEN --config wrangler.sigap-reporter.toml

# AES-256-GCM key for PII at rest (base64-encoded 32 bytes)
echo "$(openssl rand -base64 32)" | npx wrangler secret put ENCRYPTION_KEY
```

Repeat with `--env staging` and `--env production` for each environment.

Create the `sigap-batch-delivery` Queue and its `sigap-batch-dead-letter` DLQ,
then upload the SIGAP client certificate through Cloudflare and configure its
`mtls_certificates` Fetcher binding as `SIGAP_MTLS`. Certificate PEM and private
keys are not application secrets read by Worker code.

---

## Development

Start a local development server:

```bash
npm run dev
```

The local server uses Miniflare under the hood. D1 and KV are emulated in-memory.
Durable Objects work locally starting with wrangler 3.x.

---

## Testing

```bash
# Run all tests
npm test

# Watch mode
npm run test:watch

# With coverage
npm run test:coverage
```

Tests use Vitest with a Web Standards environment (no Node.js APIs). Cloudflare
Worker APIs (`crypto.subtle`, `Request`, `Response`, `KVNamespace`) are available
natively in the Vitest environment.

---

## Deployment

### Staging

```bash
npm run deploy:staging
```

Staging is deployed to `staging.acmetocasino.bet.br` and uses the SIGAP
homologation environment (`homolog.sigap.gov.br`).

### Production

```bash
npm run deploy:production
```

Production deploys all workers. The first deployment will also register
Durable Object namespaces — this cannot be undone without data loss.

**Checklist before production deploy:**

- [ ] D1 schema migration applied to remote database
- [ ] All secrets set via `wrangler secret put` for `production` environment
- [ ] SIGAP mTLS certificate is valid and not expired
- [ ] PIX PSP webhook URL configured to `https://api.acmetocasino.bet.br/api/pix`
- [ ] SIGAP operator registration confirmed (get `SIGAP_OPERATOR_ID`)
- [ ] `.bet.br` DNS delegated to Cloudflare
- [ ] WAF rules configured in Cloudflare dashboard
- [ ] Responsible gambling (RG) limits configured in D1

---

## Worker overview

| Worker | Entry point | Route |
|--------|-------------|-------|
| api-gateway | `src/api-gateway.ts` | `*.acmetocasino.bet.br/*` |
| pix-webhook | `src/pix-webhook.ts` | Internal service binding |
| sigap-reporter | `src/sigap-reporter.ts` | Queue consumer + mTLS Fetcher |
| odds-feed | `src/odds-feed.ts` | Internal service binding |
| session-manager | `src/session-manager.ts` | Durable Object |

---

## Brazilian Compliance Notes

### SIGAP Reporting (Lei 14.790/2023)

The AWS/Flink reporting pipeline prepares and signs official batch envelopes.
Those immutable envelopes enter Cloudflare Queues; the reporter consumes each
batch, records application delivery state in D1, and submits it through the
`SIGAP_MTLS` Fetcher. D1 is a delivery ledger only—it is not a native database
WAL and it is not the official transport. HTTP 2xx and duplicate HTTP 409 are
delivered; every other outcome remains pending for Queue retry.

### PIX Closed Payment Loop

The PIX Worker verifies the PSP signature, stores an idempotent receipt and
returns its acknowledgement without changing a balance. It then notifies the
AWS core asynchronously, with D1-backed retry. The AWS wallet enforces the
closed payment loop and is the only authoritative monetary ledger.

### Odds snapshots

The AWS publisher pushes versioned snapshots to the authenticated odds ingest
endpoint. The Worker verifies timestamp and HMAC, rejects stale or replayed
versions, and writes freshness metadata with the market to KV. KV serves
eventual player reads only; bet placement always revalidates in the AWS core.

### CPF Identification

Every player account is bound to a unique CPF. The mod-11 validation algorithm
is implemented in `src/utils/cpf.ts`. The API Gateway validates CPF format on
registration. SIGAP reports include the player's CPF (unmasked) for regulatory
purposes; CPF is masked in all client-facing responses per LGPD.

### Geolocation Enforcement

The API Gateway rejects all requests where `cf.country !== "BR"` with HTTP
451 (Unavailable For Legal Reasons). The BettingSession Durable Object
re-verifies player geolocation every 30 minutes per SIGAP Art. 38.

### Responsible Gambling

Deposit limits, cool-off periods, and self-exclusion are stored in the
`responsible_gambling` table in D1. Self-exclusion is enforced at the
JWT validation layer — excluded players cannot obtain a valid token.

### LGPD (Lei 13.709/2018)

- CPF is stored encrypted at rest in D1 (AES-256-GCM via `ENCRYPTION_KEY`)
- KYC documents in R2 are prefixed by player UUID, not CPF
- The `maskCPF` utility is used in all log and API responses
- Player data deletion requests are handled via the `/api/gdpr/delete` endpoint
  (not shown in this implementation; required before production launch)

---

## D1 Schema reference

See `schema.sql` for full DDL. Key tables:

| Table | Purpose |
|-------|---------|
| `players` | Player accounts (CPF, email, status, KYC) |
| `pix_transactions` | PIX deposit/withdrawal records |
| `pix_origin_notifications` | Application-managed AWS notification retry state |
| `sigap_delivery_ledger` | Application delivery state for prepared batches |
| `sigap_ggr_reports` | Daily GGR report history |
| `request_log` | API security events (geo blocks, threat hits) |
| `security_events` | Fraud signals (amount mismatch, unknown TXID) |

---

## Troubleshooting

**Worker returns 451 in development**
Set `country = "BR"` in the request headers or use `wrangler dev --local` which
populates `cf.country` from your real IP.

**Durable Object ID errors on first deploy**
Run `wrangler deploy` twice — the first deploy registers the DO namespace and the
second deploy can bind to it.

**SIGAP mTLS errors**
Cloudflare's `mtls_certificates` binding requires uploading the certificate and
binding its certificate ID as `SIGAP_MTLS`. Confirm the reporter calls
`env.SIGAP_MTLS.fetch`; global `fetch` does not present that client certificate.

**D1 `SQLITE_ERROR: no such table` in local dev**
Run `npm run db:migrate:local` before `npm run dev`.
