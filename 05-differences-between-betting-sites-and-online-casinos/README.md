<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-01.jpg" alt="Volume 1" width="150" /></a>

# Chapter 05: Differences Between Betting Sites and Online Casinos

**📘 Part of Volume 1 — Markets, Regulation, Launch, and Business Foundations** · €34.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0HBRVQZQB) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 05 of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

> Reference implementations contrasting the risk-management and financial models of sportsbooks versus RNG casino platforms.

## Overview

Chapter 5 draws the technical and commercial line between market-making (sportsbook) and house-edge (casino) businesses. These scripts model that distinction concretely: an odds compiler, a real-time liability monitor, a trading-risk engine, a hybrid P&L model, and a platform-architecture evaluator that scores build/buy decisions for each model type.

## Contents

- `implementation/risk-management/`
  - `odds_compiler.py` — Generates overround-adjusted odds for an event market
  - `trading_risk_engine.py` — Tracks open liability across markets and triggers hedge signals when exposure exceeds thresholds
  - `liability_monitor.py` — Real-time liability aggregation by event, market, and outcome
- `implementation/financial/`
  - `hybrid_platform_model.py` — P&L projection model for an operator running both sportsbook and casino verticals; shows margin, volatility, and cash-flow differences side by side
- `implementation/architecture/`
  - `platform_evaluator.py` — Scores build vs. buy vs. white-label options across 10 dimensions for betting and casino separately

## Technology Stack

- **Language:** Python 3.11+
- **No external dependencies required**

## Prerequisites

- Python 3.11 or later

## How to Run

```bash
cd scripts/chapter-05
python -m implementation.risk-management.odds_compiler
python -m implementation.risk-management.trading_risk_engine
python -m implementation.financial.hybrid_platform_model
```

## Related

- See Chapter 5 in the book for the full regulatory, financial, and architectural comparison between betting sites and online casinos.
- [The Backend of Luck →](https://thebackendofluck.com)

---

<sub>© Backend of Luck — Volume 1 · last updated 2026-04-16.</sub>
