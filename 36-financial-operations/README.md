<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-06.jpg" alt="Volume 6" width="150" /></a>

# Chapter 36: Financial Operations

**📓 Part of Volume 6 — Operations, Finance, Growth, and Case Studies** · €64.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0GZLM5J8M) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 36 of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

## Overview

Complete payment and financial operations stack for an online casino, from payment gateway integrations and cashier UIs to withdrawal processing, balance reconciliation, and revenue analytics.

## Contents

- `payments/` - Scala payment processing core:
  - `DepositFlow.scala` - End-to-end deposit workflow with provider routing
  - `GatewayProxy.scala` - Payment gateway abstraction layer
  - `PaymentProviderRegistry.scala` / `PaymentProviderTrait.scala` - Provider plugin architecture
  - `KafkaMessaging.scala` - Event-driven payment state notifications
  - `PaymentSettings.scala` - Jurisdiction-specific payment configuration
- `payments-techmojo/` - Scala deposit processing service with controller, consumer, and DAO layers
- `payments-openapi/` - OpenAPI specification for payment APIs with TypeScript types and client generation
- `adyen-admin-py/` - Python package for Adyen payment gateway administration (skin management, credentials). Replaces the Ruby gem.
- `adyen-admin/` - Ruby gem (reference, kept for historical context)
- `adyen-skins/` - Adyen HPP skin customization for branded checkout pages
- `payment-gateway-ui/` - Angular payment gateway UI:
  - Apple Pay integration, PXP card components
  - Deposit method selection and payment constants
- `cashier-launcher/` - Angular cashier launcher widget (embedded payment modal)
- `withdraw-processor/` - Scala automated withdrawal processing with fraud checks and platform integration
- `balance-mismatch-fix/` - Scala tool for detecting and resolving wallet balance discrepancies
- `financial_operations/` - Python revenue analytics and reporting

## Technology Stack

- **Payment backend:** Scala (Play Framework, Apache Pekko)
- **Payment admin:** Python (httpx, click, pydantic)
- **Frontend:** Angular, TypeScript
- **API specification:** OpenAPI 3.0
- **Payment providers:** Adyen, PXP Financial, Apple Pay
- **Messaging:** Apache Kafka
- **Build tools:** SBT, npm, Bundler

## Key Concepts

- **Provider Registry Pattern** - Pluggable payment provider architecture supporting multiple gateways per jurisdiction
- **Balance Reconciliation** - Automated detection and resolution of wallet balance mismatches between platform and provider
- **Withdrawal Automation** - Rules-based auto-approval with fraud scoring and manual review escalation

## Related

- See Chapter 36 in the book for full context on financial operations
