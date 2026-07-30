<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-06.jpg" alt="Volume 6" width="150" /></a>

# Chapter 38: Case Study: On-Premises to Cloud Migration

**📓 Part of Volume 6 — Operations, Finance, Growth, and Case Studies** · €64.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0GZLM5J8M) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 38 of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

## Overview

Real-world tooling from a casino platform migration: from on-premises data center infrastructure (HAProxy, Ansible, Windows/IIS) through cloud migration scripts to blue-green deployment automation.

## Contents

- `cloud_migration/` - Python migration toolkit:
  - `migration_assessment.py` - Automated assessment of on-prem workloads for cloud readiness
  - `database_migration.py` - Database migration with zero-downtime cutover strategies
  - `game_server_migration.py` - Game integration server migration with session draining
  - `post_migration_optimizer.py` - Post-migration cost and performance optimization
- `infrastructure/` - Terraform and Ansible IaC for target cloud environment
- `rebuild-casino/` - Ansible playbooks for rebuilding the casino platform stack (full server provisioning)
- `platform-proxy/` - HAProxy configurations:
  - `haproxy-prod.cfg` / `haproxy-dev.cfg` - Production and development load balancer configs
  - `add_query_param.lua` - Lua script for HAProxy request manipulation
  - `datadog-haproxy.yaml` - Datadog monitoring integration for HAProxy
- `bluegreen-deployment/` - Blue-green deployment scripts for zero-downtime releases
- `hardware-monitoring/` - Python hardware health monitoring with ML-based failure prediction
- `meraki-network/` - Cisco Meraki network topology and firewall rules for data center networking
- `windows-automation/` - Legacy Windows infrastructure:
  - `ansible-iis-role.yaml` - Ansible role for IIS web server provisioning
  - `bigip-irules.tf` - F5 BIG-IP iRules in Terraform
  - `vcenter-windows.tf` - VMware vCenter Windows VM provisioning

## Technology Stack

- **Migration scripts:** Python
- **IaC:** Terraform, Ansible
- **Load balancing:** HAProxy, F5 BIG-IP
- **Monitoring:** Datadog, Prometheus
- **Networking:** Cisco Meraki
- **Legacy:** Windows Server, IIS, VMware vCenter

## Key Concepts

- **Zero-Downtime Migration** - Session draining and blue-green cutover to avoid player disruption
- **Assessment-First Approach** - Automated workload analysis before committing to migration strategy
- **Hybrid Transition** - Running on-prem and cloud in parallel during migration window

## Related

- See Chapter 38 in the book for full context on the on-premises to cloud migration case study
