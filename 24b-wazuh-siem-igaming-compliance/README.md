<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-03.jpg" alt="Volume 3" width="150" /></a>

# Chapter 24b: Wazuh SIEM for iGaming Compliance

**📙 Part of Volume 3 — Security Engineering and Runtime Defense** · €84.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0GZCRSTMH) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 24b of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

> Ansible automation for deploying Wazuh manager and agents across an iGaming fleet, with iGaming-specific FIM and alert rules.

## Overview

Ansible playbooks and inventory for deploying a Wazuh SIEM cluster (manager + agents) to iGaming production infrastructure. Covers manager initialisation, agent enrollment, file integrity monitoring of game binaries and RNG libraries, PCI-DSS compliance module, and the 5-year log retention policy required by NJ DGE and MGA.

## Contents

- `ansible/playbooks/playbook-wazuh-manager.yml` — Installs and configures the Wazuh all-in-one stack: manager, indexer, dashboard, API, FIM, log collection and the custom iGaming detection rules
- `ansible/playbooks/playbook-wazuh-agent.yml` — Enrols Wazuh agents on casino hosts; configures FIM over game binaries and RNG paths, and collects the platform, transaction, auth and RNG audit logs
- `ansible/inventory/hosts.yml` — Inventory file defining the `wazuh_manager` and `wazuh_agents` groups
- `ansible/README.md` — Quickstart, role locations, and variable reference

Both playbooks reference their roles at `new-platform/ansible/wazuh/roles/` rather than shipping a second copy. See `ansible/README.md`.

## Technology Stack

- **SIEM:** Wazuh ≥ 4.7 (manager + agents)
- **Automation:** Ansible ≥ 2.15
- **OS targets:** Ubuntu 22.04, Rocky Linux 9
- **Storage:** OpenSearch (bundled with Wazuh) with 5-year ILM policy

## Prerequisites

- Ansible control node with SSH access to all targets
- Wazuh manager host: ≥ 4 vCPU, 8 GB RAM, 500 GB storage for log retention
- Inventory variables set in `ansible/inventory/hosts.yml`:
  - `wazuh_manager_ip`, `wazuh_api_user`, `wazuh_api_password`
- Ports 1514/TCP (agent), 55000/TCP (API), 443/TCP (dashboard) open between hosts

## How to Run

```bash
cd ansible

# Deploy Wazuh manager
ansible-playbook -i inventory/hosts.yml playbooks/playbook-wazuh-manager.yml

# Enroll agents across the fleet
ansible-playbook -i inventory/hosts.yml playbooks/playbook-wazuh-agent.yml
```

## Security Notes

The Wazuh API password must be rotated after initial deployment. Store credentials in OpenBao (Chapter 20) rather than plain inventory vars. Agent FIM monitors `/opt/casino/rng/{lib,config,seeds}` in realtime plus who-data, `/var/ossec/etc` (the agent's own configuration, since tampering there blinds the SIEM), and the game binary, platform and provider library directories. Any unauthorised modification triggers a critical alert sent to the SOC. Who-data attribution needs `auditd` on the host; without it Wazuh silently degrades to realtime and you lose the "which user and process" half of the answer.

## Related

- See Chapter 24b in the book for the full Wazuh architecture, custom rule writing, and US-state regulatory mapping.
- [The Backend of Luck →](https://thebackendofluck.com)

---

<sub>© Backend of Luck — Volume 2 · last updated 2026-04-16.</sub>
