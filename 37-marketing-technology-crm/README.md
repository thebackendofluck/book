<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-06.jpg" alt="Volume 6" width="150" /></a>

# Chapter 37: Marketing Technology and CRM Systems

**📓 Part of Volume 6 — Operations, Finance, Growth, and Case Studies** · €64.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0GZLM5J8M) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 37 of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

## Overview

Complete marketing technology stack for an online casino, from email delivery and affiliate tracking to VIP management, A/B testing, and privacy-compliant data synchronization with third-party platforms.

## Contents

- `mailer/` - Scala email delivery service (SBT project with Apache Pekko)
- `exacttarget-sync/` - Scala batch sync of player marketing preferences to Salesforce ExactTarget via SFTP CSV exports
- `suppression-sync/` - Gradle-based Scala service syncing suppression lists (unsubscribes, bounces) with SilverPop ESP
- `pixel-tracking/` - Scala event processor for marketing pixel/attribution tracking with template engine and SQL schema
- `optimove-tnt/` - Optimove CRM integration (SBT project) for campaign targeting
- `retention/` - Scala bonus allocation engine: retention bonus calculator, queue processor, and bonus type definitions
- `vip-rule-processor/` - Dockerized VIP tier rule engine for automated player segmentation
- `marketing/` - Python marketing platform components:
  - `customer_data_platform.py` - CDP aggregating player data across touchpoints
  - `affiliate_tracking.py` - Multi-touch attribution and affiliate commission calculation
  - `ab_testing.py` - Experimentation framework for bonus and UI variants
  - `personalization_engine.py` - Real-time content personalization based on player behavior
  - `privacy_compliance.py` - GDPR/CCPA consent management for marketing communications

## Technology Stack

- **Backend:** Scala (Apache Pekko, Play), Python
- **Build tools:** SBT, Gradle
- **ESPs:** Salesforce ExactTarget, SilverPop
- **CRM:** Optimove
- **Data transfer:** SFTP, Kafka, REST APIs
- **Infrastructure:** Docker

## Key Concepts

- **Suppression Sync** - Keeping opt-out lists synchronized across all email service providers
- **Multi-Touch Attribution** - Tracking player acquisition across affiliate, paid, and organic channels
- **VIP Rule Engine** - Automated tier progression based on wagering volume, deposit frequency, and game preferences

## Related

- See Chapter 37 in the book for full context on marketing technology and CRM systems
