# GDPR-Compliant Data Encryption on Cloudflare Workers

A practical reference for iGaming platform engineers implementing GDPR Art.32
encryption and Art.17 erasure on Cloudflare's serverless edge infrastructure.

---

## Why Edge Encryption Matters

The standard cloud database security model is: encrypt the disk, trust the
database endpoint. This means the cloud provider (and anyone who gains access
to the database endpoint) can read plaintext data. For iGaming platforms
handling player PII under GDPR, this is insufficient.

The edge encryption model inverts this: encrypt before the data leaves the
Workers runtime. The database (D1) never receives plaintext PII. It stores
JSON envelopes of the form `{"iv":"...","ct":"...","v":1}`. The key lives
in Workers Secrets — a separate, encrypted secrets store that Cloudflare
employees cannot access directly.

The result: even a full D1 database export reveals only ciphertext.
A SQL injection attack against the D1 API surface yields ciphertext.
A rogue Cloudflare employee with D1 console access sees ciphertext.
The plaintext is only ever reconstituted inside the Workers V8 isolate,
during the execution of an authenticated request.

This satisfies GDPR Art.32(1)(a): "encryption of personal data" as an
appropriate technical measure, and goes substantially further than the
minimum requirement.

---

## Encryption Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                    Cloudflare Workers Isolate                     │
│                                                                   │
│  ┌──────────────┐    ┌─────────────────┐    ┌─────────────────┐  │
│  │ Incoming     │    │  FieldCipher     │    │  EncryptedModel │  │
│  │ Request      │───▶│  AES-256-GCM    │───▶│  D1 wrapper     │  │
│  │ (plaintext)  │    │  Web Crypto API  │    │  auto encrypt/  │  │
│  └──────────────┘    └────────┬────────┘    │  decrypt PII    │  │
│                               │             └────────┬────────┘  │
│                       ┌───────▼───────┐              │           │
│                       │ ENCRYPTION_KEY│              │           │
│                       │ Workers Secret│              │           │
│                       │ (never in D1) │              ▼           │
│                       └───────────────┘    ┌─────────────────┐  │
│                                             │    D1 Database  │  │
│                                             │ (ciphertext     │  │
│                                             │  only — no PII) │  │
│                                             └─────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

**Key hierarchy:**

```
KEK (Key Encryption Key)
  — 256-bit AES, stored as Workers Secret ENCRYPTION_KEY
  — Encrypts all player DEKs

  └── DEK (Data Encryption Key) — one per player
        — 256-bit AES, generated at account creation
        — Stored encrypted in KV: DEK_STORE["dek:{playerId}"]
        — Encrypts all PII columns for that player in D1

              └── PII columns in D1
                    — email, full_name, phone, address, date_of_birth, ip_address
                    — Stored as JSON: {"iv":"...","ct":"...","v":1}
```

Single-key deployments (simpler): use FieldCipher directly with one key for
all players. DEK-per-player (more complex) enables crypto-shredding.

---

## Web Crypto API vs OpenBao Transit

| Aspect | Web Crypto API (Workers) | OpenBao / Vault Transit |
|--------|--------------------------|------------------------|
| Location | In-process, V8 isolate | External HTTPS call |
| Latency | <1ms per operation | 10-50ms per operation |
| Key storage | Workers Secrets | Vault secret engine |
| Hardware acceleration | Yes (AES-NI via SubtleCrypto) | Yes (via HSM/SE) |
| Key rotation | Manual re-encryption of DEKs | Transit key versioning (automatic) |
| Audit trail | Manual (compliance_events) | Built-in Vault audit log |
| Cost | Included in Workers pricing | Vault Enterprise licence or self-hosted |
| Cold start impact | None (no network call) | +10-50ms per encrypt/decrypt |
| FIPS 140-2 | No (SubtleCrypto is not FIPS-certified) | Yes (with HSM backend) |
| Recommendation | **Use for standard iGaming deployments** | Use for FIPS-regulated markets (US banking, DGE level 4) |

For the vast majority of iGaming operators, Web Crypto API with Workers Secrets
provides excellent security without the operational complexity of a Vault cluster.
Web Crypto API uses the browser/V8's native AES-NI hardware acceleration —
1,000 encrypt operations complete in under 150ms on Workers.

---

## Workers Secrets vs Environment Variables

This distinction is critical for GDPR Art.32 compliance:

| Aspect | `[vars]` in wrangler.toml | `wrangler secret put` |
|--------|--------------------------|----------------------|
| Storage | Committed to source control | Cloudflare encrypted secrets store |
| Visibility in deploy logs | Yes — plaintext in logs | No |
| Visibility in wrangler.toml | Yes | No (only the name, never the value) |
| Rotatable without redeploy | No | No (requires Worker redeploy) |
| Accessible to TypeScript | `env.MY_VAR` | `env.MY_SECRET` |
| GDPR Art.32 compliant | **No** — key material in source control | **Yes** |

**Rule:** Any key material — ENCRYPTION_KEY, HMAC_KEY, JWT_SECRET, API keys —
must be stored as Workers Secrets, never as `[vars]`. The only values that
belong in `[vars]` are non-sensitive configuration (ENVIRONMENT, PLATFORM_NAME).

---

## D1 Column Encryption Patterns

### Pattern 1: Encrypt all PII columns at the model layer (recommended)

Use `EncryptedModel` from `d1-encrypted-model.ts`. It wraps D1Database and
automatically encrypts/decrypts the columns you specify:

```typescript
const players = new EncryptedModel(env.DB, cipher, 'users', [
  'email', 'full_name', 'phone', 'address', 'date_of_birth', 'ip_address'
]);

// Write: plaintext → ciphertext in D1
await players.insert({ email: 'john@example.com', ... });

// Read: ciphertext → plaintext automatically
const player = await players.findById(42);
// player.email === 'john@example.com'
```

### Pattern 2: Deterministic search tokens alongside encrypted columns

AES-GCM is non-deterministic (different IV each time), so you cannot search
on an encrypted column. The solution is to store a stable HMAC search token:

```sql
-- D1 schema
ALTER TABLE users ADD COLUMN email_hash TEXT;  -- HMAC search token
-- email column stores AES-GCM ciphertext
```

```typescript
// On insert: compute both
const emailHash = await computeSearchToken(email, env.HMAC_KEY);
const emailCt   = await cipher.encrypt(email);

await env.DB.prepare(
  'INSERT INTO users (email, email_hash, ...) VALUES (?, ?, ...)'
).bind(emailCt, emailHash, ...).run();

// On lookup by email
const hash = await computeSearchToken(searchEmail, env.HMAC_KEY);
const row  = await env.DB.prepare(
  'SELECT * FROM users WHERE email_hash = ?'
).bind(hash).first();
```

### Pattern 3: Null out plaintext columns after encryption migration

When migrating an existing platform:

1. Add `_encrypted` suffix columns: `email_encrypted TEXT`
2. Run `DataMigrator` from `migrate-existing-data.ts`
3. Verify with `migrator.verifySample()`
4. Rename columns: `ALTER TABLE users RENAME COLUMN email_encrypted TO email`
5. Deploy `EncryptedModel` for all future writes

---

## Key Rotation Strategy

### When to rotate

- On any suspicion of key compromise
- Annually as standard practice (NIST SP 800-57 recommendation)
- When an employee with key access leaves the organisation
- After a security incident that touched the Workers runtime

### How to rotate with crypto-shredding (DEK-per-player)

```bash
# 1. Generate new KEK
NEW_KEK=$(openssl rand -base64 32)

# 2. Set as Workers Secret (does not affect existing DEKs yet)
echo "$NEW_KEK" | npx wrangler secret put ENCRYPTION_KEY

# 3. Deploy Worker with new KEK
npx wrangler deploy

# 4. Trigger DEK rotation job (cron handler re-wraps all DEKs under new KEK)
# The scheduled trigger in wrangler.toml handles this automatically:
#   crons = ["0 2 1 * *"]  — runs on the 1st of each month
# To trigger immediately via REST API:
curl -X POST https://api.cloudflare.com/client/v4/accounts/{id}/workers/scripts/{name}/schedules \
  -H "Authorization: Bearer $CF_API_TOKEN"
```

### How to rotate with single-key encryption (EncryptedModel without DEKs)

```typescript
// In a scheduled Worker cron handler:
const oldCipher = await FieldCipher.fromSecret(env.OLD_ENCRYPTION_KEY, 1);
const newCipher = await FieldCipher.fromSecret(env.ENCRYPTION_KEY, 2);
const model = new EncryptedModel(env.DB, newCipher, 'users', PII_COLUMNS);
const result = await model.reEncryptAll(oldCipher);
// result: { processed: 5000, failed: 0 }
```

Keep the old key as `OLD_ENCRYPTION_KEY` until rotation is verified complete,
then remove it.

---

## GDPR Erasure: Pseudonymisation vs Crypto-Shredding

Both satisfy GDPR Art.17(1). The choice depends on dataset size and architecture.

### Pseudonymisation (Pseudonymiser)

**When to use:** Simpler deployments without DEK-per-player. Suitable when
the player base is under ~100,000 and row-by-row updates are acceptable.

**How it works:** Replace each PII field with `PSEUDONYMISED:{HMAC}` using
an ephemeral salt that is never stored. The hashes are irreversible.

**Latency:** O(n) where n = number of PII columns. For 6 columns, ~6 HMAC
operations — completes in under 5ms.

**What's retained:** Transaction rows, AML flags, self-exclusion flag, KYC
status. The player_id (numeric) is retained to link these records.

### Crypto-shredding (CryptoShredder)

**When to use:** Large platforms with DEK-per-player. The most elegant
implementation: one KV delete makes all PII unrecoverable instantly.

**How it works:** Delete `KV["dek:{playerId}"]`. The DEK is gone. All PII
encrypted with that DEK in D1 is permanently unrecoverable (AES-256-GCM
ciphertext without the key is computationally indistinguishable from random
noise under the AES security assumption).

**Latency:** O(1) — a single KV delete operation (~5ms). The D1 rows are
not touched; they remain as inert ciphertext.

**What's retained:** Transaction rows use the player's DEK only for PII
columns (email, name). Financial columns (amount, currency, type, date) are
plaintext and always readable for AML purposes.

---

## Jurisdiction-Aware Data Placement with D1 Location Hints

D1 location hints are set at database creation time:

```bash
# EU player data — Western Europe (Frankfurt, Amsterdam, Paris)
npx wrangler d1 create acmetocasino-eu-db --location=weur

# UK player data — Western Europe is the nearest region
npx wrangler d1 create acmetocasino-uk-db --location=weur

# US (New Jersey) player data — Eastern North America
npx wrangler d1 create acmetocasino-us-db --location=enam

# Brazil player data — ENAM until Cloudflare opens LATAM region
npx wrangler d1 create acmetocasino-br-db --location=enam
```

Available locations:

| Code | Region | Primary Datacentres | GDPR relevance |
|------|--------|---------------------|----------------|
| `weur` | Western Europe | Amsterdam, Paris, Frankfurt | EU/UK player data |
| `eeur` | Eastern Europe | Warsaw, Vienna | EU player data (eastern) |
| `enam` | Eastern North America | Ashburn, Chicago | US, Brazil (interim) |
| `wnam` | Western North America | Los Angeles, Seattle | US West players |
| `apac` | Asia-Pacific | Singapore, Tokyo | APAC players |

**Important caveat:** D1 location hints are best-effort (not contractually
binding data residency). For legally binding guarantees, Cloudflare's
Enterprise Data Localisation Suite provides contractual commitments.
For most commercial iGaming operators, location hints combined with the
Cloudflare DPA satisfy GDPR Art.44 requirements in practice.

---

## Compliance Mapping

| Requirement | Regulation | Article | Implementation in This Codebase |
|-------------|-----------|---------|----------------------------------|
| Encryption at rest | EU GDPR | Art.32(1)(a) | `FieldCipher` — AES-256-GCM per PII column |
| Encryption in transit | EU GDPR | Art.32(1)(a) | TLS 1.3 — Cloudflare edge certificate (automatic) |
| Pseudonymisation | EU GDPR | Art.4(5), Art.25(1) | `Pseudonymiser` — HMAC-SHA-256 with ephemeral salt |
| Right to erasure | EU GDPR | Art.17(1) | `CryptoShredder.shredPlayer()` or `Pseudonymiser.erasePlayer()` |
| Data minimisation | EU GDPR | Art.5(1)(c) | Only declared PII columns encrypted; no unnecessary collection |
| Purpose limitation | EU GDPR | Art.5(1)(b) | Separate modules per domain (compliance.ts, wallet.ts, kyc.ts) |
| Data protection by design | EU GDPR | Art.25(1) | `EncryptedModel` — encryption is automatic, not optional |
| Cross-border restrictions | EU GDPR | Art.44-49 | `DataResidencyWorker` — location hints + transfer mechanism check |
| Processor agreement | EU GDPR | Art.28 | Cloudflare DPA — cloudflare.com/dpa |
| Retention limitation | EU GDPR | Art.5(1)(e) | Retention schedule in LEGISLATION-MAPPING.md |
| AML retention override | EU GDPR | Art.17(3)(b) | `Pseudonymiser.RETAINED_FIELDS` — never touches AML/transaction data |
| Encryption at rest | UK GDPR | Art.32(1)(a) | Identical to EU GDPR implementation |
| Right to erasure | UK GDPR | Art.17(1) | Identical to EU GDPR implementation |
| Encryption at rest | LGPD | Art.46(1) | Identical implementation — AES-256-GCM |
| Right to erasure | LGPD | Art.18(VI) | Identical implementation — pseudonymisation/shredding |
| Cross-border transfers | LGPD | Art.33-36 | SCCs required for non-adequate countries |

---

## Cloudflare-Specific GDPR Controls

### 1. Disable Logpush PII fields

Cloudflare Logpush exports Worker request logs to storage (R2, S3, Datadog).
By default, logs may include HTTP headers, query parameters, and request bodies
that contain PII. Configure a field filter to exclude PII before export:

```bash
# Create a Logpush job excluding PII fields
curl -X POST "https://api.cloudflare.com/client/v4/accounts/{id}/logpush/jobs" \
  -H "Authorization: Bearer $CF_API_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{
    "name": "workers-logs-gdpr-filtered",
    "logpull_options": "fields=WorkerStatus,WorkerCPUTime,WorkerExceptions&timestamps=rfc3339",
    "destination_conf": "r2://{bucket}?account-id={id}",
    "dataset": "workers_trace_events",
    "filter": "{\"where\":{\"key\":\"WorkerStatus\",\"operator\":\"!eq\",\"value\":\"ok\"}}"
  }'
```

Fields to exclude from Logpush: `ClientRequestHeaders`, `ClientIP` (if in PII),
`WorkerSubrequestResponseBody` (may contain PII).

### 2. Configure Cloudflare Access for admin endpoints

GDPR Art.25(2) requires "data minimisation by default". Restrict access to
admin and data-export endpoints using Cloudflare Access:

```toml
# wrangler.toml
[env.production]
# All requests to /admin/* must authenticate via Cloudflare Access
# (service tokens, SSO, or email OTP)
```

### 3. Enable Bot Management for GDPR Art.5 data minimisation

Bot traffic generates PII logs (IPs, user agents) with no legitimate purpose.
Enable Cloudflare Bot Management to block bots before they generate log records
containing PII — reducing the data minimisation burden.

### 4. R2 object-level encryption

KYC documents stored in R2 are encrypted at rest by Cloudflare automatically.
For additional control, encrypt documents before upload using `FieldCipher`:

```typescript
const encryptedDoc = await cipher.encrypt(await file.text());
await env.STORAGE.put(`kyc/${playerId}/${docType}`, encryptedDoc);
```

### 5. Workers jurisdiction field (Paid plan)

```toml
[env.production]
# All EU player requests are processed in EU datacenters
# Requires Workers Paid plan
# See: developers.cloudflare.com/workers/configuration/
# (Note: "jurisdiction" field support varies by plan)
```

---

## Operational Runbook: GDPR Erasure Request

When a player submits a "right to be forgotten" request:

1. **Verify identity** — confirm the requester is the account holder via KYC/2FA
2. **Check AML hold** — if the account has open AML investigations, erasure may be delayed
3. **Execute erasure:**
   ```typescript
   // Option A: Crypto-shredding (DEK-per-player)
   const shredder = await CryptoShredder.create(env.ENCRYPTION_KEY, env.DEK_STORE);
   await shredder.shredPlayer(env.DB, playerId, requestId);

   // Option B: Pseudonymisation (single-key)
   const pseudonymiser = new Pseudonymiser();
   await pseudonymiser.erasePlayer(env.DB, playerId, requestId);
   ```
4. **Revoke sessions** — delete `KV["session:{playerId}"]` and `KV["user:{playerId}"]`
5. **Retain transactions** — verify transaction rows are intact (AML Art.17(3)(b))
6. **Respond to player** — confirm erasure within 30 days (GDPR Art.12(3))
7. **Audit log** — `compliance_events` row is written automatically by both methods

---

*For the complete legislation mapping including legal bases for all processing
activities, retention periods, and cross-border transfer mechanisms, see
`LEGISLATION-MAPPING.md` in this directory.*
