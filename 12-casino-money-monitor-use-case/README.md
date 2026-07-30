<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-02.jpg" alt="Volume 2" width="150" /></a>

# Chapter 12: Real-Time Cash Flow Management for Online Casinos

**📗 Part of Volume 2 — Platform, Game, and Product Architecture** · €59.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0HBS2RGXR) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 12 of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

## Overview

Production code for monitoring casino cash positions in real time, calculating financial exposure, and triggering alerts when bank balances approach critical thresholds.

## Contents

- `monitor_money/` - Node.js cash monitoring system:
  - `server.js` - Express API server for the monitoring dashboard
  - `bank_monitor.js` - Real-time bank balance polling across multiple institutions
  - `exposure_calculator.js` - Calculates current financial exposure from outstanding bets, pending withdrawals, and progressive jackpots
  - `alert_system.js` - Threshold-based alerting via Slack, PagerDuty, and SMS
  - `maintenance_mode.js` - Automated deposit suspension when liquidity drops below safety margins
  - `docker-compose.yml` - Local development stack
- `real-time-matrix/` - Scala streaming components:
  - `ChasingLossesStream.scala` - Kafka stream detecting players chasing losses in real time
  - `ComplaintsStream.scala` - Streaming complaint event processing
  - `MatrixScoreDAO.scala` - Data access for player risk matrix scores
  - `RtmxScoreMessageProducer.scala` - Kafka producer for risk score updates
  - `HomeController.scala` - Dashboard controller for the real-time matrix UI

## Technology Stack

- **Cash monitoring:** Node.js, Express, Docker
- **Streaming analytics:** Scala, Kafka Streams
- **Database:** PostgreSQL
- **Alerting:** Slack, PagerDuty integrations

## Key Concepts

- **Exposure Calculation** - Aggregating all outstanding liabilities (unsettled bets, queued payouts, jackpot reserves)
- **Maintenance Mode** - Automated circuit breaker that suspends deposits when liquidity is threatened
- **Real-Time Matrix** - Streaming risk scores combining financial and behavioral signals

## Related

- See Chapter 12 in the book for full context on casino money monitoring
