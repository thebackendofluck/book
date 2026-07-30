<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-01.jpg" alt="Volume 1" width="150" /></a>

# Chapter 04: Market Analysis and Industry Players

**📘 Part of Volume 1 — Markets, Regulation, Launch, and Business Foundations** · €34.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0HBRVQZQB) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 04 of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

> Python tooling for competitive intelligence, M&A due diligence, vendor evaluation, and market-positioning strategy.

## Overview

Chapter 4 maps how Bet365, Flutter, Entain, and Tier 2 operators have structured their technology and go-to-market strategies. These scripts provide the analytical machinery to benchmark competitors, evaluate acquisition targets, score vendors, and define a positioning strategy before entering a crowded market.

## Contents

- `implementation/analysis/`
  - `competitive_intelligence.py` — Gathers and scores competitor capabilities across technology, regulation, and product dimensions
- `implementation/due-diligence/`
  - `ma_evaluator.py` — M&A evaluation framework: scores acquisition targets on technology stack, license portfolio, and integration risk
- `implementation/vendor/`
  - `vendor_evaluator.py` — Weighted scoring model for platform vendors and game aggregators
- `implementation/strategy/`
  - `market_positioning.py` — Positions an operator on a differentiation/cost matrix relative to identified competitors

## Technology Stack

- **Language:** Python 3.11+
- **No external dependencies required**

## Prerequisites

- Python 3.11 or later
- Input parameters are passed inline or via simple config dicts within each script

## How to Run

```bash
cd scripts/chapter-04
python -m implementation.analysis.competitive_intelligence
python -m implementation.due-diligence.ma_evaluator
python -m implementation.vendor.vendor_evaluator
python -m implementation.strategy.market_positioning
```

## Related

- See Chapter 4 in the book for operator profiles, revenue benchmarks, and the technology strategies behind Flutter, Bet365, and Entain.
- [The Backend of Luck →](https://thebackendofluck.com)

---

<sub>© Backend of Luck — Volume 1 · last updated 2026-04-16.</sub>
