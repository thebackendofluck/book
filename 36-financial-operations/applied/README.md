# Chapter 36 — Applied in production 2026-04-14

These artifacts were applied to the live platform on 2026-04-14 as part of the
S0/S1 financial-integrity work described at the end of Chapter 36.

| File | Role |
| --- | --- |
| `001-create-idempotency-records.sql` | Schema for the `idempotency_records` table used by the FastAPI middleware and Cloudflare Worker. |
| `002-dedup-wallet-events.sql` | One-shot dedup of historical `wallet_events` rows. Run before 003. |
| `003-add-unique-wallet-events.sql` | Adds the `UNIQUE (player_id, external_ref)` constraint that closes the double-credit class of bugs. |
| `idempotency.py` | FastAPI middleware: intercepts POST/PUT with `Idempotency-Key`, returns cached response on replay. |
| `postgres_store.py` | Postgres-backed store for `idempotency_records` (used by the middleware). |
| `pii_crypto.py` | Envelope encryption helpers for player PII (CPF, IBAN) using libsodium + per-tenant data keys. |

Production log: `security/EXECUTION-LOG-2026-04-14.md`.

These scripts are production artifacts; pytest only validates syntactic
correctness (`py_compile` for Python, parser check for SQL).
