<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-06.jpg" alt="Volume 6" width="150" /></a>

# Chapter 47: Platform Onboarding: From Contract to First Real-Money Bet

**📓 Part of Volume 6 — Operations, Finance, Growth, and Case Studies** · €64.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0GZLM5J8M) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 47 of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

> Automated B2B operator onboarding: market-readiness validation, multi-track provisioning workflows (cloud 6-8 wk / on-prem 12-16 wk), supplier enablement, config-service distribution, and smoke tests.

## Overview

Every week of onboarding delay costs an operator $500K–$2M in lost GGR. These scripts implement the automation layer that compresses the industry's 3-6 month average to 6-8 weeks for cloud and 12-16 weeks for on-premises deployments. They cover market readiness validation, Terraform/Ansible-driven environment provisioning, supplier (game provider / PSP / KYC) enablement, config-service setup, and a smoke-test harness that confirms the first bet can be placed before handing over to the operator.

## Contents

- `onboarding_workflow.py` — Orchestrates the full onboarding lifecycle across all phases; dispatches cloud vs on-prem track
- `market_readiness_validator.py` — Checks 40+ regulatory prerequisites for a target jurisdiction before provisioning begins
- `supplier_enablement.py` — Automates game-provider API credential exchange, IP whitelisting, and integration smoke-tests
- `config-service/config_service.py` — Versioned config distribution service: feature flags, jurisdiction rules, supplier config
- `config/operators/` — Per-operator TOML/JSON seed configs
- `playbooks/operator-onprem-deploy.yml` — Ansible playbook for on-premises environment provisioning
- `smoke-test.sh` — End-to-end smoke test: registration → KYC → deposit → bet → withdrawal → logout
- `tests/` — `test_onboarding_workflow.py`, `test_market_readiness.py`, `test_supplier_enablement.py`

## Technology Stack

- **Language:** Python 3.12, Bash
- **IaC:** Terraform (environment provisioning), Ansible (on-prem server config)
- **Config distribution:** custom versioned config service (PostgreSQL + Redis)
- **Testing:** pytest

## Prerequisites

```bash
pip install -r tests/../requirements.txt 2>/dev/null || pip install boto3 ansible-runner pydantic
```

For on-premises track: `ansible` installed on the control node, SSH access to target servers.

## How to Run

```bash
# Validate market readiness for a new jurisdiction
python market_readiness_validator.py --jurisdiction ontario --operator-id acmetocasino

# Run full cloud onboarding workflow
python onboarding_workflow.py --track cloud --operator acmetocasino --jurisdiction uk

# Enable game providers for a new operator
python supplier_enablement.py --operator acmetocasino --providers "evolution,pragmatic,netent"

# Run smoke test against staging environment
bash smoke-test.sh https://acmetocasino-staging.platform.io

# Run test suite
pytest tests/ -v
```

## Operational Notes

- **Go/no-go gate:** `market_readiness_validator.py` must return 40/40 before provisioning is triggered; any failure blocks the workflow and generates a remediation ticket.
- **Staged rollout (cloud):** Day 1 credentials → Day 2 branded staging → Week 3 integrations → Week 6 controlled launch with 100-player soft-open → full launch.
- **On-prem pitfall:** hardware lead time is 3-4 weeks — order servers before regulatory submission, not after approval.
- **Rollback:** `onboarding_workflow.py --rollback --phase <N>` tears down resources provisioned in that phase; phases are idempotent so re-run is safe.
- **Supplier IP whitelisting:** `supplier_enablement.py` stores all whitelisted IPs in config-service for traceability; missing whitelist entries are the #1 cause of integration delays.

## Related

- See Chapter 47 in the book for the full onboarding playbook, RACI matrices, and cost models.
- [The Backend of Luck →](https://thebackendofluck.com)

---

<sub>© Backend of Luck — Volume 4 · last updated 2026-04-16.</sub>
