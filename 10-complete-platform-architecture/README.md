<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-02.jpg" alt="Volume 2" width="150" /></a>

# Chapter 10: Complete Platform Architecture

**📗 Part of Volume 2 — Platform, Game, and Product Architecture** · €59.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0HBS2RGXR) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 10 of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

> A runnable Python implementation of the full casino platform — game service, supplier control plane, wallet, bonus, and release-gate tooling — with a contract-test suite covering Evolution, Pragmatic, NetEnt, and Kambi.

## Overview

Chapter 10 assembles every prior subsystem into a single cohesive view of how a regulated casino platform runs under real traffic, with real money, and real failure modes. The `acmetocasino` package is the executable reference implementation: game-service accounts, supplier callbacks, wallet adapter, and a Dockerised game server. The `platform-core` layer adds operational scripts (session revocation, round-state integrity checks, supplier matrix validation) and Cloudflare Workers for backoffice, payments, and risk. The `implementation/` directory provides the surrounding infrastructure: API gateway, Kafka, service mesh, observability, saga patterns, and load tests.

## Contents

- `acmetocasino/` — Core Python package (`pydantic ≥ 2.0`):
  - `gameservice/accounts/` — `balance_policy`, `bonus_service`, `ledger_adapter`, `limits_service`, `wallet_service`
  - `gameservice/suppliers/` — Per-supplier adapters and callback handlers
  - `router.py` — FastAPI-style game-launch router
- `platform-core/` — Operational scripts and control planes:
  - `gameservice/` — Dockerised game server with `main.py`, `fake_traffic.py`, supplier callback replay
  - `supplier-control-plane/` — `capability_matrix`, `credential_manager`, `degradation_controller`, `health_monitor`, `registry`
  - `workers/` — Cloudflare Workers: `backoffice-worker`, `exclusion-worker`, `payments-worker`, `risk-worker`
  - `pam_smoke_test.py`, `round_state_integrity_check.py`, `session_revocation_check.py`, `supplier_matrix_check.py`
- `release-gates/` — Pre-deployment validation scripts: `check_round_state_integrity.py`, `psp_callback_replay.py`, `supplier_callback_replay.py`
- `tests/` — pytest suite:
  - `unit/` — Wallet, bonus, limits, balance policy, feature flags, retry, errors
  - `contract/` — Provider contracts: Evolution, Pragmatic, NetEnt, Kambi, base supplier
  - `integration/` — `test_capability_matrix.py`
- `implementation/` — Infrastructure reference: API gateway (Kong), Kafka topics, service mesh (Istio), observability stack, saga pattern, payment-failover, load-testing scripts
- `platform-analysis-report.md` — Architecture decision record for the platform baseline

## Technology Stack

- **Core package:** Python 3.11+, Pydantic v2
- **Game server:** Python + Docker
- **Edge workers:** Cloudflare Workers (JavaScript/TypeScript via Wrangler)
- **Testing:** pytest 8+, pytest-timeout
- **Infrastructure:** Kafka, Kong, Istio, Prometheus

## Prerequisites

- Python ≥ 3.11
- Install dependencies: `pip install -e ".[test]"` from `scripts/chapter-10/`
- Docker (for `platform-core/gameservice/`)
- Wrangler CLI (for Cloudflare Workers deployment)

## How to Run

```bash
cd scripts/chapter-10

# Install
pip install -e ".[test]"

# Run unit + contract tests
pytest tests/ -v

# Run only contract tests
pytest tests/contract/ -v

# Start Dockerised game server
docker build -t acmetocasino-gameservice platform-core/gameservice/
docker run -p 8080:8080 acmetocasino-gameservice

# Run release-gate checks
python release-gates/check_round_state_integrity.py
python release-gates/psp_callback_replay.py
```

## Security Notes

The supplier control plane's `credential_manager.py` and `secret_backends.py` use placeholder secrets. Do not deploy with real provider credentials without connecting to a proper secrets backend (Vault, AWS Secrets Manager, etc.).

## Related

- See Chapter 10 in the book for the full system-level architecture walkthrough and the chapter on supplier integration control planes (`10b`).
- [The Backend of Luck →](https://thebackendofluck.com)

---

<sub>© Backend of Luck — Volume 1 · last updated 2026-04-16.</sub>
