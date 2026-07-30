<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-02.jpg" alt="Volume 2" width="150" /></a>

# Chapter 14: Mobile-First Architecture for iGaming

**📗 Part of Volume 2 — Platform, Game, and Product Architecture** · €59.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0HBS2RGXR) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 14 of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

> PWA, React Native, Flutter, and WebView shell implementations for real-money gaming on mobile.

## Overview

Scripts and configurations for building mobile-first iGaming clients: PWA service workers, offline state sync, push notifications, CDN asset delivery, and Lighthouse CI performance budgets. Covers both the PWA and native (React Native / Flutter) paths described in the chapter.

## Contents

- `sw.js` / `serviceWorkerRegistration.ts` — Service worker with cache-first strategy and offline fallback
- `offlineSyncManager.ts` — Deterministic conflict-free state synchronisation for offline gaming sessions
- `webSocketManager.ts` — WebSocket connection manager with reconnect logic and session replay
- `game-loader.ts` — Lazy-loading game bundles with CDN origin fallback
- `brand-config.ts` — Per-brand configuration for WebView hybrid shells
- `networkOptimiser.js` / `cdn-asset-resolver.js` — Adaptive asset loading for 2G/3G networks
- `push-notification-manager.ts` — Push notification manager with responsible gaming rate limits
- `cdn-origin-mobile.conf` — NGINX origin config for mobile CDN edge
- `generate-image-variants.sh` — Generates WebP/AVIF variants for responsive image delivery
- `lighthouse-budget.json` / `lighthouse-ci.yml` — Lighthouse CI performance budgets and CI pipeline
- `mobile/` — React Native / Flutter components: offline sync, push, network optimisation, security manager
- `implementation/` — Subdirs: `pwa/`, `native/`, `storage/`, `geofencing/`

## Technology Stack

- **Languages:** TypeScript, JavaScript, Dart (Flutter), Shell
- **Frameworks:** React Native, Flutter (BLoC + Hive), PWA Service Workers
- **CI:** Lighthouse CI (`.lighthouserc`)
- **CDN/Web:** NGINX, WebP/AVIF image pipeline

## Prerequisites

- Node.js ≥ 20, npm or yarn
- Flutter SDK ≥ 3.x (for `mobile/flutter/`)
- `npx lighthouse-ci` for performance budget checks
- NGINX for serving `cdn-origin-mobile.conf`

## How to Run

```bash
# Generate responsive image variants
bash generate-image-variants.sh

# Run Lighthouse CI performance audit
npx lhci autorun --config=lighthouse-ci.yml

# TypeScript services (compile + run)
npm install && npx tsc
```

## Security Notes

The `mobile/security_manager.ts` enforces certificate pinning, root/jailbreak detection, and screenshot prevention for regulated markets. Review before shipping to production.

## Related

- See Chapter 14 in the book for full context on the PWA vs native decision framework.
- [The Backend of Luck →](https://thebackendofluck.com)

---

<sub>© Backend of Luck — Volume 2 · last updated 2026-04-16.</sub>
