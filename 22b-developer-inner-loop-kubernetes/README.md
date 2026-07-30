<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-05.jpg" alt="Volume 5" width="150" /></a>

# Chapter 22b: Developer Inner-Loop Experience in Containerized iGaming Platforms

**📔 Part of Volume 5 — Infrastructure, Datacenter, and Deployment** · €49.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0GYYG1HZ3) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 22b of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

> Tilt, Skaffold, Telepresence, and Docker Compose tooling to reduce Kubernetes iteration cycles to under 20 seconds.

## Overview

Scripts and configuration for the inner-loop developer workflow on a K3s iGaming cluster: hot-reload FastAPI services (550 ms restart via uvicorn + watchfiles), Tilt for live image sync, Skaffold pipelines, Telepresence intercepts for debugging payment webhooks, and zero-downtime hotfix deployment helpers.

## Contents

- `docker/Dockerfile.dev` / `Dockerfile.prod` / `Dockerfile.debug` — Multi-stage Dockerfiles with hot-reload development target
- `skaffold/skaffold.yaml` — Skaffold pipeline: build → push to local registry → sync to K3s
- `k8s/deployment-zero-downtime.yaml` — Kubernetes Deployment with rolling-update strategy and PodDisruptionBudget
- `k8s/docker-compose.dev.yml` — Docker Compose stack mirroring production service topology for local dev
- `python/main.py` — FastAPI entry point with uvicorn hot-reload
- `python/configmap_watcher.py` — Live ConfigMap reload without pod restart
- `python/test_websocket_reconnect.py` — WebSocket reconnection test harness
- `bash/tilt-start.sh` — Starts Tilt with iGaming service profile
- `bash/compose-dev-workflow.sh` / `compose-dependency-install.sh` — Docker Compose inner-loop helpers
- `bash/telepresence-intercept.sh` / `debug-bonus-telepresence.sh` / `debug-pix-webhook-ephemeral.sh` — Telepresence intercept scripts for bonus engine and PIX webhook debugging
- `bash/debug-mtls-cert.sh` — Inspect and validate mTLS certificate chain in-cluster
- `bash/debug-game-provider-mitmproxy.sh` — mitmproxy-based game provider API inspection
- `bash/hotfix-guard.sh` — Shared guardrails sourced by every hotfix script: required change reference, append-only audit log, typed namespace confirmation, explicit `--i-accept-production-impact` for production namespaces, and image provenance verification (cosign signature, or CI labels as fallback)
- `bash/hotfix-image-deploy.sh` / `hotfix-config.sh` / `hotfix-rollback.sh` / `hotfix-feature-flag.sh` — Emergency hotfix deployment and rollback. The image path deploys pipeline artefacts only; it deliberately does not build, and it refuses unattested images
- `bash/kubectl-debug-ephemeral.sh` / `tcpdump-payment-capture.sh` — Ephemeral debug containers and network capture
- `Makefile` — `make dev`, `make hotfix`, `make intercept` shortcuts

## Technology Stack

- **Runtime:** Python 3.11+ (FastAPI, uvicorn, watchfiles)
- **Container:** Docker, K3s (Kubernetes ≥ 1.34)
- **Inner-loop tools:** Tilt, Skaffold, Telepresence v2
- **Debugging:** mitmproxy, tcpdump, ephemeral containers

## Prerequisites

- K3s cluster accessible via `kubeconfig`
- Tilt ≥ 0.33, Skaffold ≥ 2.x, Telepresence v2 installed
- Local registry at `registry.ops-host.local:5000` (see Chapter 22)
- `make` and Docker Desktop or Colima

## How to Run

```bash
# Start full inner-loop with Tilt (hot reload + live sync)
bash bash/tilt-start.sh

# Or use Docker Compose for pure-local iteration (no K8s)
bash bash/compose-dev-workflow.sh

# Intercept bonus service via Telepresence
bash bash/debug-bonus-telepresence.sh
```

## Related

- See Chapter 22b in the book for measured iteration times and the inner-loop architecture rationale.
- [The Backend of Luck →](https://thebackendofluck.com)

---

<sub>© Backend of Luck — Volume 2 · last updated 2026-04-16.</sub>
