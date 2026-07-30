<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-06.jpg" alt="Volume 6" width="150" /></a>

# Chapter 47c: Operating 100 Casinos From One Dashboard

**📓 Part of Volume 6 — Operations, Finance, Growth, and Case Studies** · €64.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0GZLM5J8M) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 47c of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

> Applied implementations of the versioned config-service patterns from Chapter 47b: multi-tenant feature flags, jurisdiction-rule propagation, and a React/TypeScript app shell with design-token-driven brand theming.

## Overview

Chapter 47b describes the config-service architecture. This directory contains the working applied artefacts: a TypeScript `AppShell` component that reads brand tokens and jurisdiction rules at runtime, a parity checker that validates edge-layer config against the core config-service, a cutover plan for migrating a live operator to the new distribution model, and a Dockerised test runner.

## Contents

- `applied/` — All applied artefacts:
  - `AppShell.tsx` — React app shell: reads brand TOML tokens and renders jurisdiction-aware UI
  - `compare-parity.ts` — CLI tool comparing Cloudflare KV edge config with core PostgreSQL config; reports drift
  - `tokens.css` — CSS custom properties generated from brand TOML (design tokens)
  - `cutover-plan.md` — Step-by-step migration plan for cutting a live operator to the new config-service
  - `Dockerfile` — Container for running parity checks in CI
  - `test_applied.py` — Pytest suite validating parity checker and cutover dry-run

## Technology Stack

- **Frontend:** React 18, TypeScript
- **Config distribution:** Cloudflare KV (edge) + PostgreSQL (core)
- **Containerisation:** Docker
- **Testing:** pytest, Jest (via Dockerfile)

## Prerequisites

```bash
# Python tests
pip install pytest httpx

# TypeScript (for compare-parity.ts and AppShell.tsx)
npm install  # expects package.json in parent chapter-44 or a local one
```

Requires `CORE_CONFIG_DB_URL` and `CF_KV_NAMESPACE_ID` + `CF_API_TOKEN` environment variables for the parity checker.

## How to Run

```bash
# Check config parity between edge and core
npx ts-node applied/compare-parity.ts --operator acmetocasino --env production

# Run Python test suite
pytest applied/test_applied.py -v

# Build parity-check Docker image
docker build -f applied/Dockerfile -t config-parity-check .
docker run --env-file .env config-parity-check
```

## Operational Notes

- **Go/no-go gate:** `compare-parity.ts` must report 0 drift items before any config promotion to production. Drift in jurisdiction-rule keys (deposit limits, self-exclusion) is treated as a P0 incident.
- **Cutover sequence:** follow `cutover-plan.md` exactly — it includes a 15-minute observation window after each propagation phase where bet acceptance continues on the old config while the new config is validated in shadow mode.
- **Rollback:** Cloudflare KV supports versioned namespaces; a one-command KV namespace restore returns all edge nodes to the previous config within 30 seconds.

## Related

- See Chapter 47 and Chapter 47b in the book for the config-service architecture and propagation model.
- [The Backend of Luck →](https://thebackendofluck.com)

---

<sub>© Backend of Luck — Volume 4 · last updated 2026-04-16.</sub>
