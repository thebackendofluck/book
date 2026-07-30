<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-06.jpg" alt="Volume 6" width="150" /></a>

# Chapter 41: Case Study: Scaling for the World Cup

**📓 Part of Volume 6 — Operations, Finance, Growth, and Case Studies** · €64.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0GZLM5J8M) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 41 of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

> Infrastructure automation for a 15x traffic spike: predictive auto-scaling, multi-region capacity planning, and match-day operations for the 2022 FIFA World Cup.

## Overview

These scripts demonstrate how to scale an iGaming platform from 150,000 to 2.3 million concurrent users over 29 days. They cover predictive capacity modelling, EKS horizontal pod autoscaling, database read-replica burst provisioning, and real-time match-day operations runbooks — all validated against the actual tournament traffic profile.

## Contents

- `world_cup_scaling/` — Core scaling toolkit:
  - `capacity_planning.py` — Predictive capacity model using historical match data and betting volume forecasts
  - `auto_scaling.py` — EKS HPA and Karpenter node provisioner configuration with match-schedule-aware triggers
  - `database_scaling.py` — Aurora read-replica scaling and connection-pool burst management
  - `match_day_ops.py` — Real-time match-day runbook: pre-match warm-up, kickoff burst, half-time drain, post-match scale-down
- `implementation/` — Supporting load-testing harness (failover, load, scaling sub-dirs)
- `tests/` — `test_capacity_planning.py`, `test_chapter41_smoke_compile.py`

## Technology Stack

- **Cloud:** AWS EKS, Aurora PostgreSQL, ElastiCache Redis
- **Scaling:** Kubernetes HPA, Karpenter, AWS Auto Scaling Groups
- **Load testing:** Locust / k6 (via implementation harness)
- **Language:** Python 3.12

## Prerequisites

- AWS credentials with EKS and RDS permissions
- `kubectl` configured for the target cluster
- Python 3.10+ with `boto3`, `kubernetes` client

```bash
pip install -r tests/../implementation/requirements.txt 2>/dev/null || pip install boto3 kubernetes
```

## How to Run

```bash
# Run capacity forecast for a specific match
python world_cup_scaling/capacity_planning.py --match "ARG vs FRA" --kickoff "2022-12-18T15:00Z"

# Trigger pre-match warm-up sequence (30 min before kickoff)
python world_cup_scaling/match_day_ops.py --phase pre-match --match-id WC_FINAL_2022

# Run full test suite
pytest tests/ -v
```

## Operational Notes

- **Go/no-go gate:** capacity model must project headroom ≥ 40% above peak estimate before match-day warm-up is approved.
- **Rollback:** `match_day_ops.py --phase rollback` drains added nodes and resets HPA min-replicas to baseline within 15 minutes.
- **Staged rollout:** warm-up → 20% extra capacity → kickoff burst → continuous autoscale → post-match scale-down. Each phase requires explicit approval from on-call engineer.
- **Pitfall:** Aurora connection limits saturate before CPU — always scale connection pooling (PgBouncer) ahead of replica count.

## Related

- See Chapter 41 in the book for the full World Cup scaling case study.
- [The Backend of Luck →](https://thebackendofluck.com)

---

<sub>© Backend of Luck — Volume 4 · last updated 2026-04-16.</sub>
