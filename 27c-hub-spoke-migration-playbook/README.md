<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-04.jpg" alt="Volume 4" width="150" /></a>

# Chapter 27c: Migrating a Single-Jurisdiction Casino Platform to Hub & Spoke

**📕 Part of Volume 4 — Compliance, Player Safety, Data Residency, and Governance** · €64.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0HBS473SJ) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 27c of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

> A running reference implementation of a multi-jurisdiction casino platform: hub (global identity + mailer) and spoke-BR (Brazilian wallet), wired by Redis pub/sub event bus.

## Overview

This is the companion code for Chapter 27c. It implements the hub-and-spoke pattern described in the migration playbook: a central hub holding the player identity graph and exclusion registry, with jurisdiction-specific spokes handling local wallets, local compliance, and local data residency. The spoke subscribes to hub exclusion events via Redis pub/sub and enforces them locally without ever exposing the global player database to the spoke's jurisdiction.

Runs on K3s (`ops-host`, 10.0.0.11). Self-contained: uses its own Redis pub/sub event bus inside the `hub` namespace rather than the casino Kafka (which is off-cluster).

## Contents

| Path | Purpose |
|---|---|
| `hub/global-id/app/main.py` | Hub service: player identity, exclusion registry, CRUD + pub/sub publish |
| `hub/global-id/Dockerfile` | Multi-stage build → distroless nonroot |
| `hub/mailer/app/main.py` | Hub service: per-jurisdiction opt-in gated transactional email |
| `hub/mailer/Dockerfile` | Same pattern: builder → distroless |
| `spoke-br/wallet/app/main.py` | Spoke (Brazil): local wallet, subscribes to hub exclusion events |
| `spoke-br/wallet/Dockerfile` | Same pattern |
| `k8s/namespace-hub.yaml` | Namespace for hub services |
| `k8s/namespace-spoke-br.yaml` | Namespace for Brazilian spoke |
| `k8s/hub-postgres.yaml` | Hub PostgreSQL (global identity store) |
| `k8s/hub-redis.yaml` | Shared Redis for pub/sub event bus |
| `k8s/spoke-br-postgres.yaml` | Spoke PostgreSQL (local BR data — data residency) |
| `k8s/global-id.yaml` | Hub global-id Deployment + ConfigMap (source inlined via render script) |
| `k8s/mailer.yaml` | Hub mailer Deployment + ConfigMap |
| `k8s/wallet-br.yaml` | Spoke wallet Deployment + ConfigMap |
| `k8s/networkpolicy-spoke-cannot-reach-hub-db.yaml` | NetworkPolicy: spoke CANNOT reach hub's Postgres directly (compliance boundary) |
| `render-manifests.py` | Inlines `main.py` into ConfigMap YAML (ops-host has no registry) |
| `tests/test_hub_spoke.py` | 5-scenario pytest suite (identity create, exclusion propagate, wallet block, mailer gate, network isolation) |
| `VALIDATION-REPORT.md` | Full deployment + test run log |

## Architecture

```
┌─────────────── Hub Namespace ───────────────┐
│                                              │
│  global-id (FastAPI)  ──publish──▶  Redis    │
│  mailer    (FastAPI)                pub/sub  │
│  hub-postgres (identity DB)                  │
│                                              │
└────────────────────┬─────────────────────────┘
                     │ exclusion events
                     ▼
┌──────────── Spoke-BR Namespace ──────────────┐
│                                               │
│  wallet-br (FastAPI)  ◀──subscribe── Redis    │
│  spoke-br-postgres (local BR data)            │
│                                               │
│  ╳ NetworkPolicy: CANNOT reach hub-postgres   │
│    (data residency enforcement)               │
│                                               │
└───────────────────────────────────────────────┘
```

## Technology Stack

- **Runtime:** Python 3.12, FastAPI, uvicorn
- **Messaging:** Redis pub/sub (event bus between hub and spoke)
- **Storage:** PostgreSQL per namespace (hub: global, spoke: local BR)
- **Orchestration:** Kubernetes (K3s) with NetworkPolicy enforcement
- **Container:** Distroless nonroot base images (multi-stage builds)
- **Templating:** `render-manifests.py` — inlines source into ConfigMaps (no private registry needed)

## Prerequisites

```bash
# Python (for local dev / tests)
python3 -m venv .venv && source .venv/bin/activate
pip install fastapi uvicorn redis psycopg2-binary httpx pytest

# K3s cluster with NetworkPolicy support (Flannel + kube-router or Calico)
kubectl get nodes
```

## How to Run

```bash
# 1. Render ConfigMaps (inlines main.py into YAML)
python3 render-manifests.py

# 2. Deploy hub
kubectl apply -f k8s/namespace-hub.yaml
kubectl apply -f k8s/hub-redis.yaml
kubectl apply -f k8s/hub-postgres.yaml
kubectl apply -f k8s/global-id.yaml
kubectl apply -f k8s/mailer.yaml

# 3. Deploy spoke (separate namespace = separate jurisdiction)
kubectl apply -f k8s/namespace-spoke-br.yaml
kubectl apply -f k8s/spoke-br-postgres.yaml
kubectl apply -f k8s/wallet-br.yaml

# 4. Enforce compliance boundary
kubectl apply -f k8s/networkpolicy-spoke-cannot-reach-hub-db.yaml

# 5. Run tests
pytest tests/test_hub_spoke.py -v
```

## Security Notes

- Database passwords in K8s manifests (`hubpass`, `spokepass`) are **development placeholders**. In production, source from OpenBao Kubernetes auth or Sealed Secrets.
- The `networkpolicy-spoke-cannot-reach-hub-db.yaml` is the critical compliance control: it enforces that the Brazilian spoke cannot query the hub's PostgreSQL directly. All data flows through the global-id API, which filters what the spoke is permitted to see (data minimization under LGPD).
- The `PG_DSN` values in the YAML use internal K8s DNS (`*.svc.cluster.local`) — not exposed outside the cluster.
- Redis pub/sub channel names are not sensitive, but the event payloads include player IDs. In production, encrypt the Redis transport (TLS) and restrict pub/sub ACLs.

## Related

- See Chapter 27c in the book for the full migration playbook (CTO decision tree, 6-phase rollout, rollback gates).
- Chapter 27b (Jurisdiction Transfer Gateway) for the legal transfer decision engine that gates hub→spoke flows.
- Chapter 26 (Responsible Gaming & Player Protection) for the exclusion registry design this hub implements.
- Chapter 29f (LATAM Datacenter Infrastructure) for the Brazilian data residency requirements driving the spoke model.
- [The Backend of Luck →](https://thebackendofluck.com)
