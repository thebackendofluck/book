<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-02.jpg" alt="Volume 2" width="150" /></a>

# Chapter 46: Building a Brazilian Betting Platform: Architecture, Compliance, and Implementation

**📗 Part of Volume 2 — Platform, Game, and Product Architecture** · €59.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0HBS2RGXR) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 46 of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

> Reference implementation for a Brazilian fixed-odds betting platform: CPF/KYC, permitted payment rails, SIGAP regulatory files, the SIGAP Impediments API, and a sports betting engine.

## Overview

Brazil's regulated betting market is architecturally distinct from European and US jurisdictions: CPF anchors player identity, account-to-account rails such as PIX are prominent, and operators submit prescribed regulatory files to SIGAP. These scripts demonstrate Brazil-specific controls, including the official SIGAP Impediments query used to block an ineligible CPF. That query does not identify the source of an individual deposit or trace benefit money through PIX.

## Contents

- `brazilian-betting-platform/` — Core platform:
  - `cpf_kyc_service.py` — CPF validation, biometric check, identity lookup, and SIGAP impediment query
  - `brazil_launch_checklist.py` — Automated go/no-go checklist covering all 47 SPA-MF licensing requirements
  - `cloudflare/` — Edge worker configuration for Brazil geolocation enforcement and PIX webhook routing
  - `CHANGELOG.md` — Platform version history
- `sports/` — TypeScript sports betting engine:
  - `odds.ts` — Real-time odds calculation (fixed-odds model)
  - `betslip.ts` — Bet acceptance, validation, and limit enforcement
  - `bets.ts` — Bet persistence and state machine
  - `cashout.ts` — In-play partial and full cashout
  - `settlement.ts` — Match result ingestion and automatic settlement
- `applied/` — Integration tests and parity checks (`test_applied.py`)
- `kafka-broker-api-versions.sh` — Verifies Kafka broker API compatibility for SIGAP event pipeline

## Technology Stack

- **Language:** Python 3.12, TypeScript
- **Payments:** PIX (Banco Central do Brasil API), TED fallback
- **Regulatory reporting:** SIGAP file preparation and API delivery; Kafka is internal transport only
- **Identity and impediments:** CPF validation plus the official SIGAP Impediments API v2
- **Edge:** Cloudflare Workers (geolocation enforcement)
- **Messaging:** Apache Kafka

## Prerequisites

- Python 3.10+, Node.js 20+
- PIX credentials (SPA-MF sandbox or production)
- Internal Kafka cluster for the example event pipeline
- `RECEITA_FEDERAL_API_KEY`, `SIGAP_ACCESS_TOKEN`, `PIX_CLIENT_ID`, `PIX_CLIENT_SECRET` env vars

```bash
pip install requests cryptography pydantic
npm install  # in sports/ directory
```

## How to Run

```bash
# Run launch checklist
python brazilian-betting-platform/brazil_launch_checklist.py

# Run integration tests
pytest applied/test_applied.py -v

# Verify Kafka broker API versions for SIGAP
bash kafka-broker-api-versions.sh
```

## Operational Notes

- **Go/no-go gate:** `brazil_launch_checklist.py` must report 47/47 checks passed before submitting for SPA-MF technical certification.
- **SIGAP delivery:** operational events may use Kafka internally, but the regulator receives prescribed signed files according to the current SIGAP manual, generally with reference date no older than D-2.
- **Credit card ban:** `betslip.ts` rejects all credit card payment method codes — this is a hard regulatory requirement since January 2025.
- **Withdrawal routing:** PIX withdrawals must return to the same CPF-linked bank account that made the deposit; `payments` logic enforces this at the data layer.
- **Fail closed:** when a mandatory impediment consultation is unavailable, betting must not be released until the CPF is validated.

## Related

- See Chapter 46 in the book for the full Brazilian market architecture and regulatory deep-dive.
- [The Backend of Luck →](https://thebackendofluck.com)

---

<sub>© Backend of Luck — Volume 4 · regulatory review 2026-07-23.</sub>
