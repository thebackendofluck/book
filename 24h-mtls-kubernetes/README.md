<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-03.jpg" alt="Volume 3" width="150" /></a>

# Chapter 24h: Mutual TLS Between Kubernetes Services for iGaming Platforms

**📙 Part of Volume 3 — Security Engineering and Runtime Defense** · €84.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0GZCRSTMH) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 24h of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

> cert-manager, trust-manager, SPIFFE/SPIRE, and network policies for service-to-service mTLS on K3s iGaming clusters.

## Overview

Manifests, Go services, and automation scripts for enforcing mutual TLS across all iGaming microservice-to-microservice communication on K3s. Covers cert-manager Certificate issuance, trust-manager CA bundle federation across namespaces, SPIRE workload identity, network policies that complement mTLS enforcement, and rotation chaos testing under load (PCI-DSS 4.0 Req 4.2.1).

## Contents

- `manifests/pki/` — ClusterIssuer and CA Certificate resources (cert-manager)
- `manifests/player-service/certificate.yaml` / `deployment.yaml` — Per-service certificate and deployment with volume mount
- `manifests/wallet-service-service.yaml` — Service object with mTLS annotation
- `manifests/spire/server.yaml` / `agent.yaml` — SPIRE server and agent DaemonSet for SPIFFE workload identity
- `manifests/network-policies/default-deny.yaml` / `player-service-policy.yaml` — Default-deny + allowlist network policies
- `go/cmd/player-service/` — Go service implementing mTLS client with cert hot-reload
- `go/pkg/mtls/` — Reusable Go mTLS dialer and TLS config builder
- `go/pkg/spiffe/` — SPIFFE X.509-SVID fetcher using go-spiffe library
- `python/mtls_cert_reloader.py` — Python sidecar that watches cert secret and sends SIGHUP on rotation
- `python/compliance_reporter_main.py` — Compliance reporter service with mutual TLS to regulatory endpoint
- `bash/install-trust-manager.sh` — Installs trust-manager via Helm and creates Bundle resources
- `bash/create-namespace-pki.sh` — Creates per-namespace CA and Certificate resources
- `bash/register-spire-workloads.sh` — Registers workload SPIFFE IDs in SPIRE server
- `bash/check-cert-expiry.sh` — Scans all Certificate resources for expiry within 48 h
- `bash/check-ntp-sync.sh` — Validates NTP sync across nodes (required for cert validity windows)
- `bash/rotation-chaos-test.sh` / `rotation-chaos-test.sh` — Forces cert rotation under sustained load; measures zero-downtime

## Technology Stack

- **Languages:** Go 1.22+, Python 3.11+, Bash
- **Kubernetes:** K3s ≥ 1.34, cert-manager ≥ 1.14, trust-manager ≥ 0.9
- **Identity:** SPIRE (SPIFFE), go-spiffe library
- **Networking:** Kubernetes NetworkPolicy (Calico or Cilium)

## Prerequisites

- K3s cluster with cert-manager installed (`bash/install-trust-manager.sh` handles trust-manager)
- `kubectl` and `helm` configured
- `KUBECONFIG` pointing to target cluster
- Go ≥ 1.22 to build `go/` services

## How to Run

```bash
# Install trust-manager and configure CA bundles
bash bash/install-trust-manager.sh

# Create namespace PKI for payments namespace
bash bash/create-namespace-pki.sh payments

# Register SPIRE workload identities
bash bash/register-spire-workloads.sh

# Run rotation chaos test (sustained load + forced cert rotation)
bash bash/rotation-chaos-test.sh
```

## Security Notes

Default-deny network policies in `manifests/network-policies/` must be applied before enabling mTLS to prevent policy gaps during the transition window. The `check-ntp-sync.sh` script must pass on all nodes before issuing certificates — clock skew of more than 5 minutes will cause TLS handshake failures.

## Related

- See Chapter 24h in the book for the full mTLS threat model and PCI-DSS 4.0 mapping.
- [The Backend of Luck →](https://thebackendofluck.com)

---

<sub>© Backend of Luck — Volume 2 · last updated 2026-04-16.</sub>
