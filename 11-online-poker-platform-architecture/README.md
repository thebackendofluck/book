<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-02.jpg" alt="Volume 2" width="150" /></a>

# Chapter 11: Online Poker Platform Architecture

**📗 Part of Volume 2 — Platform, Game, and Product Architecture** · €59.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0HBS2RGXR) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 11 of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

## Overview

Reference implementations for an online poker platform covering the game server, anti-cheat detection, real-time streaming, payment processing, and WebSocket-based client connectivity.

## Contents

- `poker/` - Core poker platform in Python:
  - `game_server.py` - Game state machine, hand management, and table logic
  - `security.py` - Anti-collusion detection and bot prevention
  - `payment.py` - Poker-specific rake calculation and cashout processing
  - `monitoring.py` - Table health, player session, and latency monitoring
  - `schema.sql` - Database schema for hands, players, tournaments, and ledger
  - `websocket_client.js` - Browser-side WebSocket client for real-time table updates
- `online-poker-system/` - Node.js supplementary services:
  - `anti_cheat_engine.js` - Statistical analysis for detecting collusion patterns
  - `machine_ban_manager.js` - Device fingerprinting and multi-account detection
  - `streaming_manager.js` - Live poker streaming with hole-card delay logic

## Technology Stack

- **Game server:** Python
- **Supplementary services:** Node.js
- **Real-time communication:** WebSocket
- **Database:** PostgreSQL (SQL schema)
- **Monitoring:** Prometheus-compatible metrics

## Key Concepts

- **State Machine** - Poker hand progression (preflop, flop, turn, river) as a distributed state machine
- **Anti-Collusion** - Statistical models detecting coordinated play between accounts
- **Hole-Card Delay** - Streaming architecture that prevents information leakage to spectators

## Related

- See Chapter 11 in the book for full context on poker platform architecture
