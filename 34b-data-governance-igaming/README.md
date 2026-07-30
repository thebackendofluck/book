<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-04.jpg" alt="Volume 4" width="150" /></a>

# Chapter 34b: Data Governance for iGaming Platforms

**📕 Part of Volume 4 — Compliance, Player Safety, Data Residency, and Governance** · €64.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0HBS473SJ) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 34b of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

> GDPR/ePrivacy automation: consent lifecycle management, Data Subject Requests, cryptographic shredding, and PII mapping for multi-jurisdiction casino platforms.

## Overview

These scripts implement the data governance layer described in Chapter 34b, with a focus on operator obligations under GDPR, Brazil LGPD, and ePrivacy Directive. Two modules cover the full data subject rights lifecycle (`dsr/`) and cookie/marketing consent management (`consent/`). All code is Python 3.12 and designed to integrate with the Kafka governance event bus described in the chapter.

## Contents

- `dsr/dsr_router.py` — Routes incoming DSR requests (erasure, portability, restriction) to the correct jurisdiction handler
- `dsr/dsr_decision_engine.py` — Evaluates whether a request can be fulfilled against retention obligations and regulatory holds
- `dsr/dsr_export_service.py` — Generates GDPR Article 20 portability exports (JSON/CSV) with PII included
- `dsr/crypto_shredder.py` — Cryptographic erasure: rotates the player-specific encryption key to render stored data unreadable without deleting rows
- `dsr/retention_enforcer.py` — Enforces jurisdiction-specific retention windows (MGA 10y, KSA 5y, Brazil 7y) via scheduled purge jobs
- `dsr/pii_data_map.py` — Dynamic PII discovery across tables, JSON fields, and event payloads; produces a regulator-ready data map
- `dsr/dsr_dashboard_api.py` — FastAPI dashboard API for DSR queue status, SLA breach alerts, and audit trail
- `consent/consent_lifecycle.py` — Consent record management: capture, versioning, withdrawal, and propagation to downstream systems
- `consent/cookie_consent_config.py` — Cookie category definitions and Consent Management Platform (CMP) configuration builder
- `consent/terms_acceptance.py` — Terms and Privacy Policy acceptance tracking with version pinning
- `consent/transfer_impact_assessment.py` — Automated Transfer Impact Assessment (TIA) for cross-border data flows under GDPR Chapter V

## Technology Stack

- **Language:** Python 3.12
- **API:** FastAPI
- **Encryption:** Python `cryptography` library (Fernet/AES-256)
- **Storage:** PostgreSQL (with `pgaudit`), S3-compatible object store
- **Messaging:** Kafka (governance events topic)

## Prerequisites

- Python 3.12+: `pip install fastapi uvicorn cryptography kafka-python psycopg2-binary`
- PostgreSQL 14+ with `pgaudit` extension enabled
- Kafka cluster with `governance.events` topic

## How to Run

```bash
# Install dependencies
pip install fastapi uvicorn cryptography kafka-python psycopg2-binary

# Start DSR dashboard API
uvicorn dsr.dsr_dashboard_api:app --port 8090

# Run retention enforcement (dry-run)
python dsr/retention_enforcer.py --dry-run --jurisdiction MGA

# Crypto-shred a player (GDPR erasure)
python dsr/crypto_shredder.py --player-id <UUID>

# Generate PII data map for audit
python dsr/pii_data_map.py --output pii_map_$(date +%Y%m%d).json
```

## Compliance / Security Notes

Cryptographic shredding (`crypto_shredder.py`) is the preferred erasure method when row deletion would break referential integrity in financial tables — a common scenario in iGaming where game rounds reference player accounts. The approach satisfies GDPR Article 17 and UK ICO guidance on anonymisation. The `dsr_decision_engine.py` enforces regulatory holds: a player under AML investigation cannot be erased even upon request. The TIA module (`transfer_impact_assessment.py`) generates the documentation required before transferring EEA player data to third-country suppliers (game providers, PSPs). All DSR actions emit signed audit events to Kafka, providing the immutable log required by MGA Technical Standards §7.

## Related

- See Chapter 34b in the book for the full data governance architecture and multi-jurisdiction compliance framework.
- [The Backend of Luck →](https://thebackendofluck.com)

---

<sub>© Backend of Luck — Volume 3 · last updated 2026-04-16.</sub>
