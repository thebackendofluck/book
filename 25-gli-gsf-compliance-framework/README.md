<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-04.jpg" alt="Volume 4" width="150" /></a>

# Chapter 25: GLI-GSF Compliance Framework: Online Gaming Information Security

**📕 Part of Volume 4 — Compliance, Player Safety, Data Residency, and Governance** · €64.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0HBS473SJ) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 25 of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

Implementation scripts for the 4-phase GLI-GSF (Gaming Laboratories International - Gaming Security Framework) compliance program. Each phase builds upon the previous, covering the complete journey from initial assessment to ISF (Independent Security Firm) certification readiness.

## Directory Structure

```
chapter-25/
├── phase1-foundation/              # Phase 1: GISMS Foundation
│   ├── cis-controls-mapper.py      # Map CIS Controls to GLI-GSF requirements
│   ├── csc-inventory.py            # Critical System Component inventory tool
│   ├── gisms-scope-generator.sh    # GISMS scope definition generator
│   ├── retention-policy/
│   │   └── docker-compose.yml      # Log retention infrastructure (MinIO + lifecycle)
│   └── risk-assessment.py          # CVSS v3.1 + ISO 31010 risk assessment tool
│
├── phase2-technical-controls/      # Phase 2: Technical Security Controls
│   ├── mfa-audit.sh                # OGIS-2 MFA coverage audit (AWS IAM, Okta, PAM)
│   ├── mobile-security/
│   │   └── pinning-check.py        # Certificate pinning validation
│   ├── rbac-matrix/
│   │   └── rbac_generator.py       # Role-Based Access Control matrix generator
│   ├── siem-setup/
│   │   ├── docker-compose.yml      # Wazuh SIEM deployment
│   │   └── igaming-rules.xml       # Gambling-specific SIEM detection rules
│   └── signature-verification/
│       ├── docker-compose.yml      # CCP verification infrastructure
│       └── verify_ccp.py           # OGIS-1 signature verification daemon
│
├── phase3-protection/              # Phase 3: Active Protection Controls
│   ├── api-security/
│   │   └── owasp-api-check.sh      # OWASP API Top 10 scanner for gambling APIs
│   ├── bot-mitigation/
│   │   ├── bot_detector.py         # Bot detection engine (JA3, behavioral, fingerprint)
│   │   └── nginx-bot-filter.conf   # Nginx bot filtering configuration
│   ├── ddos-protection/
│   │   ├── cloudflare-terraform/
│   │   │   └── main.tf             # CloudFlare DDoS protection (WAF, rate limiting, bot mgmt)
│   │   └── rate-limiter.py         # Adaptive rate limiting (token bucket + sliding window)
│   └── vendor-access/
│       ├── vendor_access_controller.py  # Vendor access lifecycle management
│       └── emergency-revoke.sh          # Emergency vendor revocation (<5 minutes)
│
├── phase4-assessment/              # Phase 4: Assessment & Certification Readiness
│   ├── gts-dry-run.sh              # GTS dry-run scanner (Nmap, Nikto, ZAP, TLS)
│   ├── evidence-collector.py       # OGIS evidence packager for all control domains
│   ├── pentest-scope.md            # Penetration testing scope template
│   └── remediation-tracker.py      # Finding remediation tracker with SLA enforcement
│
└── README.md                       # This file
```

## Phase 1: GISMS Foundation

Establish the Gaming Information Security Management System.

| Script | Purpose | Usage |
|--------|---------|-------|
| `risk-assessment.py` | CVSS v3.1 risk assessment with iGaming threat library | `python3 risk-assessment.py --demo` |
| `csc-inventory.py` | Inventory all Critical System Components | `python3 csc-inventory.py --scan` |
| `cis-controls-mapper.py` | Map CIS Controls to GLI-GSF requirements | `python3 cis-controls-mapper.py` |
| `gisms-scope-generator.sh` | Generate GISMS scope documentation | `./gisms-scope-generator.sh` |

## Phase 2: Technical Security Controls

Implement OGIS-mandated security controls.

| Script | OGIS Domain | Usage |
|--------|-------------|-------|
| `verify_ccp.py` | OGIS-1 | `python3 verify_ccp.py verify` or `python3 verify_ccp.py daemon` |
| `mfa-audit.sh` | OGIS-2 | `./mfa-audit.sh` or `./mfa-audit.sh --output json` |
| `rbac_generator.py` | OGIS-2 | `python3 rbac_generator.py` |
| `pinning-check.py` | OGIS-3 | `python3 pinning-check.py --apk app.apk` |
| `siem-setup/` | OGIS-5 | `docker-compose up -d` |

## Phase 3: Active Protection Controls

Deploy runtime protection and third-party access management.

| Script | Purpose | Usage |
|--------|---------|-------|
| `owasp-api-check.sh` | OWASP API Top 10 scan | `./owasp-api-check.sh -t https://api.casino.example.com` |
| `bot_detector.py` | Bot detection engine | `python3 bot_detector.py serve` or `python3 bot_detector.py test` |
| `main.tf` | CloudFlare DDoS protection | `terraform plan -var-file=production.tfvars` |
| `rate-limiter.py` | Adaptive rate limiting | `python3 rate-limiter.py demo` or `python3 rate-limiter.py serve` |
| `vendor_access_controller.py` | Vendor access lifecycle | `python3 vendor_access_controller.py demo` |
| `emergency-revoke.sh` | Emergency vendor revocation | `./emergency-revoke.sh --vendor "VendorName" --dry-run` |

## Phase 4: Assessment & Certification Readiness

Prepare for ISF assessment and manage findings.

| Script | Purpose | Usage |
|--------|---------|-------|
| `gts-dry-run.sh` | Pre-assessment vulnerability scan | `./gts-dry-run.sh --target casino.example.com` |
| `evidence-collector.py` | Package OGIS evidence for ISF | `python3 evidence-collector.py collect --period 2026-Q1` |
| `pentest-scope.md` | Penetration test scope template | Fill in and provide to testing firm |
| `remediation-tracker.py` | Track findings with SLA enforcement | `python3 remediation-tracker.py demo` |

### gli-13/ — GLI-13 v3.0 (2024) Monitoring and Control Systems

| Script | Purpose | Usage |
|--------|---------|-------|
| `mcs-connector-test.sh` | Liveness + mTLS auth + clock-drift + flush-freshness check on the regulator MCS connector (SPA/SIGAP, MGCB CIDS, KSA monitoring API, AGCO iGO). Designed for a 60-second cron / Prometheus blackbox probe. | `MCS_HOST=… MCS_CLIENT_CERT=… ./mcs-connector-test.sh` |

### k8s-deployment/ — Reference Kubernetes Deployment

Production-shaped deployment of the GLI compliance scripts as a Kubernetes service. Covers TDD-built FastAPI runner (`runner/`), pytest suite (`tests/`, 13 tests), hardened multi-stage Dockerfile, and 10 manifests under `k8s/` (namespace, RBAC, ConfigMaps, PVC, Service, Deployment, 3 CronJobs, ServiceMonitor). Validated live on the lab `k3s-casino` cluster (k3s v1.35.3, 9 nodes, Prometheus Operator). See `ARCHITECTURE.md` for the ADR.

| Component | Purpose |
|---|---|
| `runner/checks.py` | Registry + subprocess wrapper around the chapter-XX/gli-NN CLI scripts |
| `runner/server.py` | FastAPI app — `/healthz`, `/metrics`, `POST /run/<check>` |
| `runner/metrics.py` | Prometheus counters/histograms with success/failure/timeout outcome label |
| `tests/` | 13 pytest tests, written test-first per TDD discipline |
| `Dockerfile` | Multi-stage, non-root uid 10001, read-only rootfs, healthcheck |
| `k8s/00-namespace.yaml` | Namespace `gli-compliance` with `pod-security.kubernetes.io/enforce: restricted` |
| `k8s/10-rbac.yaml` | ServiceAccount + Role (read-only on own ns) + RoleBinding |
| `k8s/20-configmap.yaml` | Stub fixtures + runtime env (real deploy uses OpenBao) |
| `k8s/30-pvc.yaml` | 5Gi `local-path` PVC for evidence retention |
| `k8s/40-service.yaml` | ClusterIP service for `/metrics` scrape |
| `k8s/50-deployment-server.yaml` | FastAPI runner with restricted PSS-compliant security context |
| `k8s/60-…/61-…/62-cronjob-*.yaml` | GLI-12 jackpot (5min), GLI-13 MCS probe (5min), GLI-16 recon (daily) |
| `k8s/70-servicemonitor.yaml` | Prometheus Operator scrape target — no annotations |
| `k8s/kustomization.yaml` | Single-command apply: `kubectl apply -k k8s/` |

## GLI-GSF Remediation SLAs

All scripts enforce these timelines per GLI-GSF requirements:

| Severity | CVSS Score | Remediation Deadline | Regulatory Notice |
|----------|-----------|---------------------|-------------------|
| Critical | 9.0 - 10.0 | 24 hours | Immediate |
| High | 7.0 - 8.9 | 7 days | Within 30 days |
| Medium | 4.0 - 6.9 | 30 days | Within 30 days |
| Low | 0.1 - 3.9 | Next quarterly cycle | Annual report |

## Quick Start

```bash
# Phase 1: Run risk assessment with demo iGaming threats
python3 phase1-foundation/risk-assessment.py --demo --output risk-report.md

# Phase 2: Audit MFA coverage
./phase2-technical-controls/mfa-audit.sh --output json

# Phase 3: Test rate limiting
python3 phase3-protection/ddos-protection/rate-limiter.py demo

# Phase 3: Demo vendor access lifecycle
python3 phase3-protection/vendor-access/vendor_access_controller.py demo

# Phase 4: Run GTS dry-run scan
./phase4-assessment/gts-dry-run.sh --target casino.example.com

# Phase 4: Collect and validate evidence
python3 phase4-assessment/evidence-collector.py demo

# Phase 4: Track remediation findings
python3 phase4-assessment/remediation-tracker.py demo
```

## Requirements

Most scripts use Python standard library only. Optional dependencies:

- **Flask + Redis**: For `bot_detector.py` and `rate-limiter.py` serve mode
- **AWS CLI v2**: For `mfa-audit.sh` and `emergency-revoke.sh` IAM operations
- **Terraform >= 1.5**: For `cloudflare-terraform/main.tf`
- **Nmap, Nikto, OWASP ZAP, testssl.sh**: For `gts-dry-run.sh` (skips unavailable tools)
- **jq**: For JSON parsing in shell scripts
