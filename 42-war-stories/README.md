<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-06.jpg" alt="Volume 6" width="150" /></a>

# Chapter 42: War Stories: When Everything Goes Wrong

**📓 Part of Volume 6 — Operations, Finance, Growth, and Case Studies** · €64.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0GZLM5J8M) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 42 of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

> Reference implementations of the failure patterns from Chapter 42: RNG seeding bugs, payment cascade collapse, GDPR deletion disasters, and smart-contract exploits.

## Overview

These scripts are not templates — they are forensic reconstructions. Each file models the failure mode described in the war story, paired with the circuit-breaker or recovery pattern that contained the damage. Read alongside Chapter 42 to understand what the detection logs looked like and why each fix worked.

## Contents

- `war_stories/` — Core failure reconstructions:
  - `rng_examples.py` — Reproducible RNG seeding bug (weak entropy source) and corrected implementation
  - `payment_processor.py` — Payment cascade simulator: 7-provider chain failure with and without circuit breakers
  - `data_recovery.py` — GDPR mass-deletion recovery: WAL log replay and point-in-time restore sequence
  - `vulnerable_contract.sol` — Solidity smart contract with the reentrancy vulnerability from the war story
- `implementation/` — Production-hardened patterns: `access/`, `circuit-breaker/`, `monitoring/`, `payment-queue/`, `security/`
- `incident-logs/` — `log_analysis_patterns.py`, `sample_access_logs.txt` — log parsing for incident reconstruction

## Technology Stack

- **Language:** Python 3.12, Solidity 0.8.x
- **Resilience:** Circuit breaker pattern (custom Python implementation)
- **Database recovery:** PostgreSQL WAL / point-in-time restore
- **Monitoring:** Prometheus metrics, structured JSON logging

## Prerequisites

- Python 3.10+
- For Solidity: `solc` 0.8.x or Hardhat / Foundry
- PostgreSQL with WAL archiving enabled (for `data_recovery.py`)

## How to Run

```bash
# Reproduce RNG failure and verify fix
python war_stories/rng_examples.py

# Simulate payment cascade (safe — no real payments)
python war_stories/payment_processor.py --scenario cascade

# Parse incident logs
python incident-logs/log_analysis_patterns.py --log incident-logs/sample_access_logs.txt
```

## Operational Notes

- **Go/no-go gate for payment providers:** circuit breaker must report ≥ 2 healthy providers before enabling withdrawals.
- **GDPR deletion:** never run bulk-delete scripts without a point-in-time snapshot taken within the last 30 minutes — `data_recovery.py` documents the 3-month recovery cost of skipping this.
- **Rollback:** all `implementation/circuit-breaker` patterns include a force-open flag for emergency bypass with mandatory incident ticket.

## Related

- See Chapter 42 in the book for the full war stories narrative.
- [The Backend of Luck →](https://thebackendofluck.com)

---

<sub>© Backend of Luck — Volume 4 · last updated 2026-04-16.</sub>
