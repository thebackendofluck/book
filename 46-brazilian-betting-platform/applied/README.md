# Chapter 46 — Applied in production 2026-04-14

The Brazilian payment flows (PIX, CPF handling, SIGAP reporting) reuse
shared infrastructure. To avoid duplication, this directory references the
canonical copies by path rather than re-shipping them:

| Concern | Canonical artifact |
| --- | --- |
| PIX idempotency (FastAPI middleware) | `../../chapter-36/applied/idempotency.py` |
| `wallet_events` dedup constraint | `../../chapter-36/applied/003-add-unique-wallet-events.sql` |
| CPF tokenization / envelope encryption | `../../chapter-36/applied/pii_crypto.py` |
| SIGAP reporting job | run by the reconciliation-service cron; see Chapter 46 section "SIGAP Reporting" |

This README is intentionally the only file in `chapter-46/applied/` — the
production code mirrors what is documented in Chapters 28a/36/44, and
maintaining two copies would drift.
