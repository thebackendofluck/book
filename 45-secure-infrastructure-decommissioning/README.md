<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-05.jpg" alt="Volume 5" width="150" /></a>

# Chapter 45: Secure Infrastructure Decommissioning

**📔 Part of Volume 5 — Infrastructure, Datacenter, and Deployment** · €49.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0GYYG1HZ3) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 45 of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

> The Secure Data Destruction System (SDDS): cryptographic erasure, hardware self-destruction, and compliance reporting for iGaming platform decommissioning under GDPR, PCI DSS, GLI-11, and SOX.

## Overview

When an operator exits a jurisdiction or loses a license, regulators demand proof of destruction — not just deletion. This toolkit automates the full decommissioning lifecycle: infrastructure discovery, sequenced cryptographic erasure across AWS accounts, Ansible-managed server wipe, MikroTik/Meraki network device zero-fill, YubiHSM key material destruction, and a tamper-evident audit trail. The Zymbit SEN500 secure edge node acts as the hardware root of trust and self-destructs after the final phase completes.

## Contents

- `master_orchestrator.py` — Main coordinator: phases, timeouts, emergency abort, and final attestation
- `aws_nuke_enhanced.py` — Enhanced `aws-nuke` wrapper with iGaming-specific resource tagging and dry-run gate
- `s3_destroyer.sh` / `configure_s3_accounts.sh` — S3 bucket enumeration, versioning purge, and bucket deletion across all accounts
- `terraform_obliterator.sh` — Destroys all Terraform-managed resources in correct dependency order
- `ansible_destroyer.py` — Triggers Ansible playbooks for OS-level cryptographic wipe
- `ansible/playbooks/` — Wipe playbooks for Linux and Windows servers
- `meraki_eliminator.py` — Cisco Meraki API: factory-reset all network devices
- `mikrotik_zeroizer.py` — MikroTik RouterOS zero-fill via API
- `yubihsm_destroyer.py` / `yubikey_revoker.py` — HSM key material destruction and YubiKey certificate revocation
- `sen500_selfdestruct.py` — SEN500 hardware self-destruct trigger (requires dual SMS auth)
- `sms_auth.py` — Two-person integrity SMS authentication for destructive operations
- `rollback_system.py` — Abort and partial rollback for pre-destruction phases only
- `audit_logger.py` — Append-only tamper-evident audit log (WORM-compatible output)
- `repo_monitor.py` — Watches a monitored repository for the "cleaner" trigger word
- `doc_parser.py` — Parses regulatory destruction requirements from uploaded compliance docs
- `config/` — `master_config.json`, `aws_nuke_config.yml`, Meraki/MikroTik inventory files

## Technology Stack

- **Language:** Python 3.12, Bash
- **Cloud:** AWS (`aws-nuke`, boto3), multi-account strategy
- **IaC:** Terraform, Ansible
- **Networking:** Cisco Meraki API, MikroTik RouterOS API
- **Hardware:** Zymbit SEN500, YubiHSM2, YubiKey
- **Auth:** SMS two-person integrity (Twilio-compatible)

## Prerequisites

```bash
pip install -r requirements.txt
# AWS: credentials for all accounts in master_config.json
# Meraki: MERAKI_API_KEY env var
# MikroTik: credentials in config/mikrotik_inventory.json
```

**Warning:** these scripts permanently and irreversibly destroy infrastructure. Always run `aws_nuke_enhanced.py --dry-run` first.

## How to Run

```bash
# Step 1: Dry-run to validate destruction scope
python aws_nuke_enhanced.py --dry-run --config config/aws_nuke_config.yml

# Step 2: Start orchestrated destruction (requires SMS auth)
python master_orchestrator.py --config config/master_config.json

# Step 3: Generate compliance attestation report
python audit_logger.py --export compliance_report_$(date +%Y%m%d).json
```

## Operational Notes

- **Go/no-go gate:** `master_orchestrator.py` will not proceed past Phase 1 (discovery) without sign-off from two named personnel via SMS.
- **Rollback:** `rollback_system.py` can abort only Phases 1-3 (pre-erasure). Once `aws_nuke_enhanced.py` executes live, data destruction is irreversible.
- **Pitfall:** The EUR 340,000 fine documented in the chapter came from a forgotten secondary S3 bucket. Run `configure_s3_accounts.sh` against *all* AWS accounts including sandbox and logging accounts before Phase 2.
- **SEN500 self-destruct** requires physical presence and is the final step after all digital destruction is attested.

## Related

- See Chapter 45 in the book for the full decommissioning case study and regulatory mapping table.
- [The Backend of Luck →](https://thebackendofluck.com)

---

<sub>© Backend of Luck — Volume 4 · last updated 2026-04-16.</sub>
