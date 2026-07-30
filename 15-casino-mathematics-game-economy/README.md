<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-02.jpg" alt="Volume 2" width="150" /></a>

# Chapter 15: Casino Mathematics and Game Economy

**📗 Part of Volume 2 — Platform, Game, and Product Architecture** · €59.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0HBS2RGXR) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 15 of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

## Overview

Code covering casino game mathematics, jackpot feed systems, game provider integrations, promotional campaign engines, sportsbook financial ledgers, and a prize administration frontend.

## Contents

- `mathematics/` - Python casino mathematics engine: RTP calculation, house edge modeling, variance simulation, and paytable validation
- `feeder/` - Scala jackpot feed system:
  - `Feeder.scala` / `Feed.scala` - Real-time jackpot value feed from game providers
  - `ProgressiveJackpots.scala` / `ProgressiveFeed.scala` - Progressive jackpot aggregation across multiple suppliers
  - `GameJackpotDAO.scala` - Database access for jackpot state persistence
- `game-service/` - Scala game catalog management:
  - `GameFeedService.scala` - Ingesting game metadata from suppliers
  - `GameProvider.scala` - Provider abstraction layer
  - `GameVariant.scala` - Game variant definitions (RTP tiers, volatility levels)
- `promotions/` - Scala promotional campaign engine with bonus rules and qualification criteria
- `prize-admin/` - Angular frontend for prize campaign administration:
  - Campaign reoccurrence scheduling
  - Game qualifier configuration
  - Angular module with routing
- `sportsbook-ledger/` - Scala financial ledger for sportsbook operations
- `gli-12/` — GLI-12 v3.0 (2026) Progressive Gaming Devices and Systems
  - `jackpot-reserve-check.py` — Asserts every active progressive jackpot has its certified reserve floor funded; runs every 5 min on cron and exits non-zero on breach. Pages on-call compliance.

## Technology Stack

- **Mathematics engine:** Python (NumPy, SciPy)
- **Backend services:** Scala (Apache Pekko, Play Framework)
- **Frontend:** Angular, TypeScript
- **Build tools:** SBT, npm

## Key Concepts

- **RTP Validation** - Verifying that game return-to-player percentages match certified values
- **Progressive Jackpot Feeds** - Aggregating real-time jackpot values from multiple game suppliers into a unified feed
- **Game Variants** - Managing multiple RTP configurations for the same game across different jurisdictions

## Related

- See Chapter 15 in the book for full context on casino mathematics and game economy
