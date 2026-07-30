<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-03.jpg" alt="Volume 3" width="150" /></a>

# Chapter 23b: DevSecOps Pipeline Implementation: From GitHub Actions to Self-Hosted GitLab CI

**📙 Part of Volume 3 — Security Engineering and Runtime Defense** · €84.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0GZCRSTMH) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 23b of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

> Self-hosted GitLab CI on K3s with Semgrep, Trivy, Gitleaks, Checkov, and DAST — unlimited scan minutes at zero licensing cost.

## Overview

Full implementation of a self-hosted GitLab CE CI/CD pipeline with integrated security scanning replacing GitHub Actions. Covers the SAST/SCA/container/IaC scan suite, Grafana OnCall alerting for pipeline failures, DAST against staging environments, and vendor integration shims for Semgrep AppSec.

## Contents

- `pipeline/` — GitLab CI pipeline definitions: security scan jobs, SAST, secrets detection, dependency scanning
- `gitlab/` — GitLab CE runner configuration and registration scripts
- `security/` — Kubernetes, WAF, rate-limit, egress, and emergency compensating-control manifests
- `dast/` — OWASP ZAP / Nuclei DAST scan configurations targeting iGaming endpoints
- `deployment/` — K3s manifests and Helm values for GitLab CE and runner pool
- `grafana/` — Grafana dashboards and alert rules for pipeline health metrics
- `oncall/` — Grafana OnCall escalation policies for critical security scan failures
- `tests/` — Pipeline integration tests and scan result validators
- `vendor-integrations/` — Semgrep AppSec cloud integration and Snyk connector

## Technology Stack

- **CI/CD:** GitLab CE (self-hosted on K3s)
- **SAST:** Semgrep OSS + AppSec cloud
- **Container scanning:** Trivy
- **Secrets detection:** Gitleaks
- **IaC scanning:** Checkov
- **DAST:** OWASP ZAP, Nuclei
- **Monitoring:** Grafana + Grafana OnCall
- **Infrastructure:** Kubernetes (K3s), Helm

## Prerequisites

- K3s cluster with ≥ 4 CPU / 8 GB RAM available for GitLab CE
- GitLab CE ≥ 16.x deployed via Helm chart
- `semgrep`, `trivy`, `gitleaks`, `checkov` available in runner image
- `SEMGREP_APP_TOKEN`, `GITLAB_REGISTRATION_TOKEN` env vars

## How to Run

```bash
# Deploy GitLab CE and runner pool to K3s
kubectl apply -f deployment/

# Register a GitLab runner
bash gitlab/register-runner.sh

# Trigger a full security pipeline manually
# (Push a commit or use GitLab UI → CI/CD → Run Pipeline)

# Run DAST against staging
bash dast/run-zap.sh https://staging.casino.internal
```

## Security Notes

The runner pool must run with `privileged: false` for container scanning. Use the Docker-in-Docker (DinD) socket bind-mount pattern only in isolated namespaces. Gitleaks baseline file must be updated after any intentional test secret rotation to avoid false-positive noise.

For short patch windows where a dependency upgrade cannot be regression-tested immediately, start from `security/emergency-compensating-controls.yaml` and set an owner plus expiry date before applying it.

The local audit gate is designed for bounded feedback on large repositories: run `pipeline/security-audit.sh --jobs auto` for the pull-request path, and reserve deeper settings such as full dependency resolution and full git-history secret scanning for scheduled jobs. Python dependency audits use a separate `PIP_AUDIT_MAX_JOBS` cap because package-index and build-metadata calls can slow down under excessive fan-out. Per-process timeouts are intentional; a slow or malformed manifest should fail visibly instead of blocking every other check.

## Related

- See Chapter 23b in the book for the GitHub Actions → self-hosted GitLab CI migration rationale and cost analysis.
- [The Backend of Luck →](https://thebackendofluck.com)

---

<sub>© Backend of Luck — Volume 2 · last updated 2026-04-16.</sub>
