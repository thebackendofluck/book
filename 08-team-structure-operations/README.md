<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-01.jpg" alt="Volume 1" width="150" /></a>

# Chapter 08: Team Structure and Operations

**📘 Part of Volume 1 — Markets, Regulation, Launch, and Business Foundations** · €34.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0HBRVQZQB) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 08 of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

## Overview

Code and configuration samples demonstrating how casino platform teams organize their codebases, manage backoffice tooling across generations, and enforce ownership boundaries in a monorepo.

## Contents

- `backoffice-main/` - Primary backoffice application (Nx monorepo):
  - `fe-main-app.ts` - Angular frontend entry point
  - `player-service-main.ts` / `search-service-main.ts` - Backend services for player management
  - `getPlayerInfoQuery.ts` / `playerSearchQuery.ts` - GraphQL query definitions
  - `nx.json` / `package.json` - Nx workspace configuration
- `backoffice-nextgen/` - Next-generation backoffice with client/server split:
  - `client/` - Angular components for complaints handling and UKGC returns
  - `server/` - Express API with auth, customer management, and workflow engine
- `backoffice/` - Progressive Web App backoffice with Redis caching and Docker Compose setup
- `cli-scripts/` - Developer CLI tools and CMS integration scripts
- `team-structure/` - Team organization artifacts:
  - `CODEOWNERS` - GitHub CODEOWNERS file mapping directories to responsible teams
  - `module-boundaries.ts` - Nx module boundary enforcement rules
  - `nx-workspace.json` / `nx-project-graph.json` - Workspace dependency graph

## Technology Stack

- **Frontend:** Angular, TypeScript, PWA
- **Backend:** Node.js, Express
- **Monorepo:** Nx
- **Caching:** Redis
- **Infrastructure:** Docker Compose
- **Code ownership:** GitHub CODEOWNERS

## Key Concepts

- **CODEOWNERS** - Enforcing code review by domain experts (payments team reviews payments code, etc.)
- **Module Boundaries** - Nx-enforced dependency rules preventing unauthorized cross-team imports
- **Backoffice Generations** - Evolution from monolithic PHP to Angular SPA to PWA-based tooling

## Related

- See Chapter 8 in the book for full context on team structure and operations
