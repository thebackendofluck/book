<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-03.jpg" alt="Volume 3" width="150" /></a>

# Chapter 20: Hardware Security Module Infrastructure

**📙 Part of Volume 3 — Security Engineering and Runtime Defense** · €84.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0GZCRSTMH) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 20 of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

> YubiHSM 2 FIPS key management, disk/database encryption, mTLS certificate automation, and HSM-backed API gateway for iGaming.

## Overview

Complete HSM infrastructure for regulated iGaming: LUKS disk encryption with HSM-wrapped keys, PostgreSQL TDE via OpenBao Transit, Docker container encryption, network mTLS setup with WireGuard, and compliance report generation. The chapter includes live validation results against a YubiHSM 2 FIPS (serial 36470346, firmware 2.4.1).

## Contents

- `hsm_setup.py` — YubiHSM 2 FIPS initialisation: key generation, domain configuration, audit log setup
- `disk-encryption.sh` / `get-volume-key.sh` / `get_luks_key.py` — LUKS AES-XTS-512 disk encryption with HSM key wrapping
- `mariadb_tde.sh` / `postgre_tde.sh` / `postgres_key_fetch.sh` / `check_tde_rotation.sh` — Database TDE using OpenBao Transit engine
- `docker_encrypted_storage.sh` — Encrypted overlay storage for Docker containers
- `api_gateway.py` / `api_gateway_firewall.sh` — HSM-backed API gateway with mTLS enforcement
- `network_mtls_setup.sh` / `network_wireguard_setup.sh` / `generate_mtls_certs.sh` — Network mTLS and WireGuard mesh setup
- `sed_ssd_setup.py` / `sed_ssd_management.sh` / `sed_ssd_monitoring.sh` — Self-Encrypting Drive management
- `password_security.py` / `password_vault.py` — HSM-derived password hashing and vault integration
- `aws_nitro_enclave.sh` / `aws-cloudhsm/` — AWS Nitro Enclave and CloudHSM configurations
- `generate_compliance_report.sh` — Automated GLI-19 / PCI-DSS compliance report generation
- `ansible/` — Ansible playbooks for HSM provisioning across fleet
- `rust-hsm-platform/` — Rust HSM abstraction library
- `monitor_certs.sh` / `monitor_tde_performance.sh` / `monitoring_dashboard.sh` — Operational monitoring
- `Makefile` — Test runner: `make test`, `make test-security`, `make security-scan`

## Technology Stack

- **Languages:** Python 3.11+, Bash, Rust, Ansible YAML
- **HSM:** YubiHSM 2 FIPS, AWS CloudHSM, AWS Nitro Enclaves
- **Secrets:** OpenBao (HashiCorp Vault fork) 2.5.2
- **Encryption:** LUKS2 (AES-XTS-512), PostgreSQL TDE (pg_tde), MariaDB TDE
- **Infrastructure:** Docker, Ansible

## Prerequisites

- `yubihsm-connector` running on `localhost:12345`
- OpenBao 2.5.2 with Transit secrets engine enabled
- `yubihsm-shell`, `cryptsetup`, `ansible` installed
- Python deps: `pip install -r requirements.txt`
- `VAULT_ADDR`, `VAULT_TOKEN` environment variables

## How to Run

```bash
# Install Python dependencies
pip install -r requirements.txt

# Initialise YubiHSM 2
python hsm_setup.py --init

# Run full test suite against connected HSM
make test

# Generate compliance report
bash generate_compliance_report.sh
```

## Security Notes

All scripts assume the YubiHSM 2 connector is accessible only on localhost. Never expose port 12345 externally. Key ceremonies for master key generation should follow the `ansible/` playbook with a quorum of operators present. TRNG entropy quality must score ≥ 7.99 bits/byte (test included in `make test-security`).

## Related

- See Chapter 20 in the book for HSM key hierarchy design and GLI-19 compliance validation.
- [The Backend of Luck →](https://thebackendofluck.com)

---

<sub>© Backend of Luck — Volume 2 · last updated 2026-04-16.</sub>
