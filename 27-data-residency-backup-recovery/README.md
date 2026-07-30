<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-04.jpg" alt="Volume 4" width="150" /></a>

# Chapter 27: Data Residency and Backup/Recovery

**📕 Part of Volume 4 — Compliance, Player Safety, Data Residency, and Governance** · €64.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0HBS473SJ) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 27 of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

## Overview

Production tooling for data residency compliance, encryption-at-rest and in-transit, mTLS certificate management, and cost-optimized backup strategies across regulated gambling jurisdictions.

## Contents

- `backup/` - Wasabi jurisdiction-aware backup tooling:
  - `wasabi-jurisdiction-config.py` - Maps each jurisdiction to its legally required Wasabi region; includes retention days and full regulatory citations
  - `wasabi-backup.sh` - Jurisdiction-aware upload to Wasabi with mandatory region guardrail; supports cross-region replica where permitted
  - `wasabi-restore-test.sh` - Automated DR drill: download, decrypt, restore to staging, validate, measure against jurisdiction RTO target
  - `WASABI-JURISDICTION-GUIDE.md` - Regulatory citations and operational runbook for each configured jurisdiction
- `data_residency/` - Python data residency management:
  - `jurisdiction_data_router.py` - Routes player data to jurisdiction-compliant storage locations
  - `backup_cost_optimizer.py` - Optimizes backup storage tiers based on retention requirements and access patterns
  - `compliance_views.sql` - SQL views proving data residency compliance for auditors
- `encryption/` - Encryption implementations:
  - `disk/` - LUKS disk encryption setup, Docker encrypted storage, and key retrieval from vault
  - `tde/` - Transparent Data Encryption for MariaDB and PostgreSQL with key rotation and performance monitoring
  - `mtls/` - Mutual TLS certificate generation, network setup, and certificate expiry monitoring

## Technology Stack

- **Offsite archive:** Wasabi (S3-compatible) via AWS CLI v2
- **Data routing:** Python
- **Database encryption:** MariaDB TDE, PostgreSQL TDE
- **Disk encryption:** LUKS
- **Certificate management:** OpenSSL, Bash
- **Key management:** HashiCorp Vault integration
- **SQL:** PostgreSQL, MariaDB

## Usage

```bash
# Upload an encrypted backup archive to the jurisdiction-correct Wasabi bucket
JURISDICTION=agco_on WASABI_REGION=ca-central-1 ... ./backup/wasabi-backup.sh upload /path/to/backup.enc

# Run a monthly DR drill (download → decrypt → restore → validate → RTO check)
JURISDICTION=ukgc STAGING_PGHOST=staging-db.internal ... ./backup/wasabi-restore-test.sh full-drill

# Validate that configured region matches jurisdiction requirements
JURISDICTION=dge_nj WASABI_REGION=us-east-1 ... ./backup/wasabi-backup.sh check-region

# Generate mTLS certificates for inter-service communication
cd encryption/mtls && bash generate_certs.sh

# Set up TDE on PostgreSQL
cd encryption/tde && bash postgresql_tde.sh

# Monitor certificate expiry
cd encryption/mtls && bash monitor_certs.sh
```

## Key Concepts

- **Jurisdiction Routing** - Ensuring player data from NJ stays in NJ, EU data stays in EU, etc.
- **TDE Key Rotation** - Rotating database encryption keys without downtime
- **Backup Tier Optimization** - Hot/warm/cold storage tiers aligned with regulatory retention periods

## Related

- See Chapter 27 in the book for full context on data residency and backup/recovery strategies
