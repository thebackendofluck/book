<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-01.jpg" alt="Volume 1" width="150" /></a>

# Chapter 02: Regulation and Compliance Landscape

**📘 Part of Volume 1 — Markets, Regulation, Launch, and Business Foundations** · €34.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0HBRVQZQB) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 02 of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

## Overview

Compliance-oriented code covering GDPR data handling, regulator reporting views, and audit report generation for multi-jurisdictional online gambling operations.

## Contents

- `gdpr_schema.sql` - Database schema for GDPR data subject requests and consent tracking
- `gdpr_request_processor.ts` - TypeScript service handling data access, portability, and erasure requests
- `gdpr_data_extractor.ts` - Extracts player data across systems for Subject Access Requests (SARs)
- `compliance-reports/` - SQL queries for regulatory reporting: dormant accounts with balances, top winners analysis
- `dge-regulator-views/` - New Jersey Division of Gaming Enforcement (DGE) mandated database views covering patrons, sessions, wallet transfers, casino/poker/sports wagers, cash transactions, game limits, and PII

## Technology Stack

- **Database:** SQL (PostgreSQL/MariaDB) with regulatory-mandated view schemas
- **Backend:** TypeScript (Node.js)
- **Compliance:** GDPR (EU), DGE (New Jersey, US)

## Key Concepts

- **Regulator Views** - Pre-defined database views that regulators can query directly during audits
- **GDPR Right to Erasure** - Pseudonymization approach that preserves financial audit trails while removing PII
- **Dormant Account Reporting** - Identifying accounts with remaining balances for regulatory compliance

## Related

- See Chapter 2 in the book for full context on regulation and compliance
