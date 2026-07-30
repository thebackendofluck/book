<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-06.jpg" alt="Volume 6" width="150" /></a>

# Chapter 33: Operational Playbooks

**📓 Part of Volume 6 — Operations, Finance, Growth, and Case Studies** · €64.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0GZLM5J8M) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 33 of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

> Production-tested runbooks and automation for incident response, DR, backoffice operations, hardware monitoring, and workflow management.

## Overview

These scripts implement the operational playbooks described in Chapter 33. They cover the full ops lifecycle: SEV-1 incident response automation, disaster recovery failover, hardware predictive maintenance, backoffice admin tooling, chargeback/dispute handling, notification delivery, and a workflow case-management engine. All modules are Python 3.12 with pytest coverage.

## Contents

- `playbooks/` — Core automation: `incident_response_playbook.py`, `disaster_recovery.py`, `change_management.py`, `compliance_operations.py`, `production_access.py`, `monitoring_framework.py`
- `hardware_monitoring/` — Telegraf + Prometheus + Grafana stack for predictive hardware maintenance; `data_collector.py`, `ml_predictor.py`, `whatsapp_alerter.py`, `docker-compose.yml`, Grafana dashboards
- `backoffice-admin/` — FastAPI backoffice service: player KYC/affordability, CRM bonuses, compliance reports, SOW tracking, withdrawal queue, RBAC access control
- `workflow-service/` — Workflow engine (`engine.py`, `models.py`) with FastAPI entrypoint and pytest suite
- `workflow/` — Case manager and workflow engine with separate test coverage
- `disputes/` — Chargeback/dispute resolution automation (`disputes.py`)
- `notifications/` — Multi-channel notification service (email, SMS, WhatsApp)
- `integrations/` — Jira/Confluence sync, CI bridge, webhook handler, HTML report templates
- `release-gates/` — Pre-release checks: `verify_regulatory_exports.py`, `check_case_slas.py`, `stuck_workflow_detector.py`
- `applied/` — Applied examples: `audit_log_middleware.py`, `C-01-auth-fix.sh`
- `ipmicheck.py` / `raidcheck.py` — IPMI and RAID health checks for bare-metal servers
- `smartd.conf` — S.M.A.R.T. disk monitoring configuration
- `ci-cd.yml` — CI/CD pipeline definition with security scan gate
- `dr-failover-records.json` — DNS and load-balancer failover record set for DR exercises

## Technology Stack

- **Backend:** Python 3.12, FastAPI
- **Monitoring:** Telegraf, Prometheus, Grafana, Alertmanager
- **Alerting:** WhatsApp Business API, email, SMS
- **Ticketing:** Jira, Confluence (REST API)
- **Testing:** pytest
- **Hardware:** IPMI, smartmontools, MegaRAID

## Prerequisites

- Python 3.12+: `pip install fastapi uvicorn prometheus-client pytest`
- Docker + Docker Compose for hardware monitoring stack
- IPMI toolset (`ipmitool`) for bare-metal health checks
- Jira/Confluence API tokens in environment variables

## How to Run

```bash
# Run full playbook test suite
pytest playbooks/ workflow-service/ disputes/ notifications/ -v

# Start hardware monitoring stack
cd hardware_monitoring && docker compose up -d

# Launch backoffice admin API
cd backoffice-admin && uvicorn main:app --port 8080

# Execute DR failover drill
python playbooks/disaster_recovery.py --dry-run

# Pre-release regulatory checks
python release-gates/verify_regulatory_exports.py
python release-gates/check_case_slas.py
```

## Compliance / Security Notes

The `compliance_operations.py` playbook automates regulatory report generation required by MGA (monthly), UK GC (quarterly), and NJ DGE (real-time event feeds). The `audit_log_middleware.py` ensures every backoffice action is logged immutably — a requirement under MGA Technical Standards §7 and NJ DGE §12. Release gates block deployment if regulatory exports are stale or SLA thresholds are breached, preventing accidental non-compliance deployments. Hardware predictive maintenance reduces unplanned downtime, which would constitute a reportable event in most GLI-certified jurisdictions.

## Related

- See Chapter 33 in the book for the full incident management and operational framework.
- [The Backend of Luck →](https://thebackendofluck.com)

---

<sub>© Backend of Luck — Volume 3 · last updated 2026-04-16.</sub>
