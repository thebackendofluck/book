<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-02.jpg" alt="Volume 2" width="150" /></a>

# Chapter 44: Deploying iGaming Platforms on Cloudflare Workers

**📗 Part of Volume 2 — Platform, Game, and Product Architecture** · €59.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0HBS2RGXR) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 44 of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

> Complete edge-native iGaming platform: Cloudflare Workers backend, D1/KV/R2 storage, multi-brand Wrangler deployment, GDPR crypto-shredding, remote HSM integration, and a live proof-of-concept at cfgp.cloud-acmetocasino.com.

## Overview

This is the most complete Cloudflare Workers iGaming reference in existence. The codebase covers the full request lifecycle — compliance checks at the edge boundary, JWT auth, KYC, wallet, game launch, RNG, and settlement — deployed as a single Worker with per-brand TOML configuration. The `hybrid/` and `remote-hsm/` subdirectories extend the architecture for operators who retain a traditional core but want edge acceleration and HSM-backed key operations.

## Contents

- **Worker modules (TypeScript):** `index.ts` (router), `auth.ts`, `kyc.ts`, `wallet.ts`, `payments.ts`, `games.ts`, `compliance.ts`, `security.ts`, `analytics.ts`, `cipher.ts`, `utils.ts`, `model.ts`, `frontend.ts`, `worker.ts`
- **Brand configs:** `wrangler.toml` (base), `acmedice.toml`, `acmegate.toml`, `acmevegas.toml`, `brand.toml`, `default.toml`
- **Database:** `schema.sql`, `seed-games.sql`
- **Deploy scripts:** `deploy.sh`, `deploy-all-brands.sh`, `brands.sh`, `keys.sh`, `healthcheck.sh`, `implementation-checklist.sh`
- `gdpr-encryption/` — `crypto-shredding.ts`, `field-cipher.ts`, `d1-encrypted-model.ts`, `data-residency-worker.ts`
- `remote-hsm/` — `hsm-poller.py`, `hsm-benchmark.py`, `deploy-hsm-api.sh`, benchmark results
- `hybrid/` — `edge_core_consistency.py`, `policy_propagation.py`
- `applied/` — `d1-schema.sql`, `idempotency.ts`, `payments.patch.ts`
- `terraform/` — `dns.tf` (Cloudflare DNS provisioning)
- `check_edge_core_consistency.py` — Validates parity between edge state and core database

## Technology Stack

- **Edge runtime:** Cloudflare Workers (V8 isolates)
- **Storage:** Cloudflare D1 (SQLite), KV, R2
- **Language:** TypeScript (Workers), Python 3.12 (tooling)
- **IaC:** Wrangler CLI, Terraform (Cloudflare provider)
- **HSM:** YubiHSM2 via remote API
- **CI/CD:** `preview.yml` GitHub Actions workflow

## Prerequisites

```bash
npm install          # installs @cloudflare/workers-types, wrangler, etc.
wrangler login       # authenticate to Cloudflare
```

Environment variables required: `JWT_SECRET`, `KYC_API_KEY`, `PAYMENT_GATEWAY_KEY` (set via `wrangler secret put` or `keys.sh`).

## How to Run

```bash
# Deploy single brand to production
./deploy.sh acmedice

# Deploy all three brands
./deploy-all-brands.sh

# Run implementation checklist (go/no-go gates)
bash implementation-checklist.sh

# Smoke-test live endpoints
bash healthcheck.sh https://acmedice.workers.dev
```

## Operational Notes

- **Go/no-go gate:** `implementation-checklist.sh` must exit 0 before any brand goes live — it validates compliance config, RNG seeding, session limits, and KYC provider reachability.
- **Staged rollout:** deploy to `preview` environment → run `applied/` end-to-end tests → promote to production with `wrangler deploy --env production`.
- **Rollback:** Cloudflare keeps the previous Worker version; `wrangler rollback` restores it in under 30 seconds globally.
- **Pitfall:** D1 is eventually consistent under high write concurrency — the `idempotency.ts` pattern in `applied/` is mandatory for wallet operations to prevent double-credits.
- **HSM latency:** `remote-hsm/hsm-benchmark-results.json` shows p99 < 8ms over mTLS; budget this into session-start latency.

## Related

- See Chapter 44 in the book for the full Cloudflare Workers iGaming architecture.
- [The Backend of Luck →](https://thebackendofluck.com)

---

<sub>© Backend of Luck — Volume 4 · last updated 2026-04-16.</sub>
