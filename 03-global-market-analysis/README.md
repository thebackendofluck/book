<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-01.jpg" alt="Volume 1" width="150" /></a>

# Chapter 03: Global Market Analysis

**📘 Part of Volume 1 — Markets, Regulation, Launch, and Business Foundations** · €34.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0HBRVQZQB) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 03 of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

> Tools for market sizing, competitive mapping, regulatory risk scoring, and multi-jurisdiction expansion planning.

## Overview

This chapter's scripts operationalize the market-entry framework from the book: calculating TAM/SAM/SOM for each region, scoring regulatory risk before committing to a jurisdiction, and mapping payment-method requirements per country. They complement the chapter's deep-dive into European, LatAm, Asia-Pacific, and North American market dynamics.

## Contents

- `market_analysis/`
  - `global_market_analyzer.py` — Pulls market-size data by region; produces region-level GGR breakdowns
  - `regulatory_predictor.py` — Scores regulatory-change probability and impact for a given jurisdiction
  - `technology_adoption.py` — Maps mobile/payment-method adoption curves by country
- `implementation/analysis/`
  - `tam_sam_som_calculator.py` — TAM → SAM → SOM funnel model parameterized by jurisdiction
  - `competitive_mapper.py` — Positions operators on a competitive-landscape grid
  - `market_risk_scorer.py` — Composite risk score combining regulation, FX, and political factors
- `implementation/payments/`
  - `payment_method_mapper.py` — Maps locally preferred payment rails per country (e.g. Pix for Brazil, Giropay for Germany)
- `implementation/planning/`
  - `expansion_roadmap.py` — Generates a sequenced multi-jurisdiction expansion roadmap based on risk/reward scoring

## Technology Stack

- **Language:** Python 3.11+
- **No external dependencies required** (pure stdlib + dataclasses)

## Prerequisites

- Python 3.11 or later
- No environment variables or external services required; all data is embedded as reference fixtures

## How to Run

```bash
cd scripts/chapter-03
python -m implementation.analysis.tam_sam_som_calculator
python -m market_analysis.global_market_analyzer
python -m implementation.planning.expansion_roadmap
```

## Related

- See Chapter 3 in the book for full market-by-market context and data sources.
- [The Backend of Luck →](https://thebackendofluck.com)

---

<sub>© Backend of Luck — Volume 1 · last updated 2026-04-16.</sub>
