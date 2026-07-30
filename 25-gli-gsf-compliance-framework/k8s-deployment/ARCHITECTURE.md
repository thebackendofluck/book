# GLI Compliance Runner — Architecture (ADR)

## Status
Accepted, 2026-05-05

## Context

The five GLI compliance scripts authored under `writing/new-book/scripts/chapter-XX/gli-NN/`
are CLI artifacts referenced by the book. They produce certifiable evidence (jackpot
reserve floor, MCS connector liveness, cashless reconciliation, client-server boundary,
Player UI compliance) but require an operator to invoke them on a cron.

A real iGaming platform runs these as in-cluster scheduled jobs with metrics scraped by
Prometheus and evidence retained for 5 years. This module deploys exactly that — a
production-shaped service that wraps the existing scripts as Kubernetes CronJobs +
a long-running HTTP server exposing on-demand `/run/<check>` endpoints and `/metrics`.

## Target

- Cluster: `k3s-casino` on a self-hosted lab cluster (9 nodes, k3s v1.35.3, etcd HA, Prometheus Operator
  installed, cert-manager present)
- Namespace: `gli-compliance` (new, isolated from `acmetocasino*`)
- Image registry: `registry.internal.example:5000/gli-compliance-runner` (insecure HTTP local registry)
- Prometheus scrape via ServiceMonitor CR (no annotations)
- Ingress: none (internal-only service)
- Storage: 5Gi PVC for evidence retention

## Non-goals

- Externally exposing the runner. Compliance evidence is internal.
- Real targets in the first deployment. The CronJobs use stub configs/echoes so the
  cron lifecycle and metric pipeline can be verified without OpenBao/PIX/MCS deps.
  A subsequent deploy wires real targets via OpenBao secret injection.
- Replacing the book's CLI scripts. They remain the source of truth; this module
  imports them as a sibling layer.

## Components

```
                          ┌────────────────────────────────┐
                          │  k3s-casino / gli-compliance ns │
                          │                                 │
   Prometheus Operator    │  ┌──────────────────────────┐   │
   (acmetocasino-mon ns)  │  │ Deployment               │   │
        ▲                 │  │  gli-compliance-runner   │   │
        │                 │  │  - FastAPI :8080         │   │
        │ scrape /metrics │  │  - /healthz /metrics     │   │
        └─────────────────┼─▶│  - /run/<check>          │   │
                          │  └──────────────────────────┘   │
                          │           ▲                     │
                          │           │ subprocess          │
                          │  ┌────────┴───────┐             │
                          │  │ CronJobs (4)   │             │
                          │  │ - jackpot 5m   │             │
                          │  │ - mcs 1m       │             │
                          │  │ - recon 03:00  │             │
                          │  │ - gli28 Sun 4  │             │
                          │  └────────┬───────┘             │
                          │           │ writes              │
                          │           ▼                     │
                          │  ┌────────────────┐             │
                          │  │  evidence-pvc  │ 5Gi         │
                          │  └────────────────┘             │
                          └────────────────────────────────┘
```

## Test strategy

TDD-first: tests in `tests/` are written before runner/ implementation. Suite uses
pytest + httpx.AsyncClient for the FastAPI endpoints, and mocks subprocess for the
CLI script invocations. The `gli28_runner.py` orchestrator from chapter-32 is
imported as a reference but not re-invoked in the unit tests; an integration test
calls it once against a fixture.

## Security

- ServiceAccount with Role read-only on own namespace. No ClusterRole.
- Non-root user (uid 10001) inside container.
- `runAsNonRoot: true`, `readOnlyRootFilesystem: true`, `allowPrivilegeEscalation: false`.
- NetworkPolicy: ingress from monitoring namespace only; egress to kube-dns +
  the stub target (later: OpenBao + the real regulator MCS).
- Image scan via existing kubescape + falco runtime in cluster.
- Secrets placeholder is a Secret with empty values; the operational secret comes
  from OpenBao via the existing OpenBao agent pattern (Cap 20b).

## Rollback

`kubectl delete -k k8s/` removes everything under namespace boundary. The PVC has
`persistentVolumeReclaimPolicy: Retain` so evidence survives a redeploy.
