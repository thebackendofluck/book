<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-01.jpg" alt="Volume 1" width="150" /></a>

# Chapter 01: The Online Casino Ecosystem

**📘 Part of Volume 1 — Markets, Regulation, Launch, and Business Foundations** · €34.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0HBRVQZQB) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 01 of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

## Overview

Production code samples illustrating the core architectural patterns of an online casino platform, from the Scala-based backend gateway to legacy PHP backoffice and on-premises staging infrastructure.

## Contents

- `platform-core/` - Scala source demonstrating gateway pattern, multi-brand architecture, package structure, and supplier abstraction layer
- `backoffice/` - Backoffice admin panel code showing the evolution from legacy PHP to modern Angular/TypeScript frontends
- `stage-system/` - On-premises staging environment configurations: Apache httpd vhosts, Tomcat init scripts, MySQL backup config, and cron jobs

## Technology Stack

- **Backend:** Scala (Play Framework / Apache Pekko patterns)
- **Legacy backoffice:** PHP
- **Modern backoffice:** Angular, TypeScript, Vue.js
- **Infrastructure:** Apache httpd, Tomcat, MySQL, cron
- **OS:** Linux (CentOS/RHEL for staging)

## Key Concepts

- **Gateway Pattern** - Single entry point routing requests to game suppliers and internal services
- **Multi-Brand Architecture** - One platform serving multiple casino brands with shared infrastructure
- **Supplier Abstraction** - Decoupling game provider APIs behind a unified interface

## Related

- See Chapter 1 in the book for full context on the online casino ecosystem
