<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-04.jpg" alt="Volume 4" width="150" /></a>

# Chapter 26: Responsible Gaming and Player Protection Systems

**📕 Part of Volume 4 — Compliance, Player Safety, Data Residency, and Governance** · €64.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0HBS473SJ) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 26 of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

## Overview

Production implementations of player protection systems including self-exclusion registries, national exclusion list integrations (GAMSTOP, NJ DGE), geo-blocking, and AI-driven addiction detection.

## Contents

- `excludify/` - Scala exclusion matching service:
  - `ImportService.scala` / `NJImportService.scala` - Ingesting exclusion lists from multiple jurisdictions
  - `MatchingService.scala` / `NJMatchingService.scala` - Fuzzy matching players against exclusion registries
  - `DownloadService.scala` - Scheduled downloads from regulator SFTP endpoints
  - `NJ_DGE_report.xsd` - XML schema for New Jersey DGE exclusion reporting
- `national-exclusion/` - GAMSTOP (UK) integration:
  - `GamstopProcessor.scala` - Real-time GAMSTOP API checks during registration and login
  - `NationalExclusionProcessor.scala` - Generic processor supporting multiple national schemes
  - `Hash.java` - One-way hashing for privacy-preserving exclusion matching
- `global-id/` - Cross-brand player identity management:
  - `GlobalIdController.scala` - API for matching players across operator brands
  - `MatchRules.scala` / `PropagationRules.scala` - Rules for propagating exclusions and flags across brands
  - `FlagType.scala` / `FlagFilters.scala` - Player risk flag taxonomy
- `lookup-service/` - Geo-IP and jurisdiction lookup:
  - `GeoIpDatabase.scala` - MaxMind-based geolocation for jurisdiction enforcement
  - `BlockedCountryService.scala` - Country-level access restrictions
- `acme-import/` - Registration-time self-exclusion matcher (NJ DGE + PA DAP) with parallel asyncio matchers
- `responsible_gaming/` - Python responsible gaming toolkit:
  - `behavior_monitor.py` - Real-time session monitoring (duration, velocity, loss patterns)
  - `addiction_detection.py` - ML-based early warning system for problem gambling indicators
  - `self_exclusion.py` - Self-exclusion workflow with cooling-off periods

## Technology Stack

- **Backend:** Scala, Python, Java
- **Geo-IP:** MaxMind GeoIP2
- **Integrations:** GAMSTOP API, NJ DGE SFTP, PA Gaming Control Board
- **ML:** scikit-learn (addiction detection models)

## Key Concepts

- **Multi-Jurisdiction Exclusion** - Matching players against exclusion lists from UK, NJ, PA, and other jurisdictions simultaneously
- **Cross-Brand Propagation** - When a player self-excludes on one brand, the exclusion propagates to all brands under the same operator
- **Privacy-Preserving Matching** - Using hashed identifiers to check exclusion status without exposing PII

## Related

- See Chapter 26 in the book for full context on responsible gaming and player protection
