<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-06.jpg" alt="Volume 6" width="150" /></a>

# Chapter 40: Case Study: Launching in a New Regulated Market

**📓 Part of Volume 6 — Operations, Finance, Growth, and Case Studies** · €64.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0GZLM5J8M) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 40 of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

## Overview

Code supporting the launch of an online casino in a new regulated market (Ontario, Canada case study), including geo-verification, regulatory compliance checks, i18n/l10n, and US market reporting.

## Contents

- `market_launch/` - Python market entry toolkit:
  - `market_entry.py` - Market readiness checklist automation and launch sequencing
  - `geo_verification.py` - Geolocation verification ensuring players are within jurisdiction boundaries
  - `regulatory_compliance.py` - Jurisdiction-specific compliance rule engine
  - `multi_language.py` - Internationalization framework for content, currency, and date localization
  - `responsible_gaming.py` - Market-specific responsible gaming configuration (limits, messaging, cool-off periods)
- `us-reporting/` - Scala reporting suite for US regulated markets:
  - `MainReportingSuite.scala` - Entry point for the full reporting pipeline
  - `KambiSupplier.scala` / `ReportingSupplier.scala` - Supplier-specific data adapters
  - `WsrReports.scala` - Weekly Summary Report generation for regulators
  - `VarianceCheck.scala` - Automated variance detection between operator and supplier figures
  - `ReportDefaults.scala` - Standard report configuration and formatting

## Technology Stack

- **Market launch tools:** Python
- **Regulatory reporting:** Scala (SBT)
- **Geolocation:** GeoComply-compatible verification
- **Configuration:** HOCON (application.conf)

## Key Concepts

- **Geo-Fencing** - Verifying player physical location at registration, login, and periodically during sessions
- **Variance Checking** - Automated reconciliation between operator records and supplier data before regulator submission
- **Market-Specific Configuration** - Each jurisdiction requires different responsible gaming limits, tax rates, and reporting formats

## Related

- See Chapter 40 in the book for the full case study on launching in Ontario
