<div align="center">

<a href="../README.md"><img src="../assets/covers/volume-02.jpg" alt="Volume 2" width="150" /></a>

# Chapter 28a: Distributed Systems Deep Dive

**📗 Part of Volume 2 — Platform, Game, and Product Architecture** · €59.90

[The Backend of Luck](../README.md) · [Buy this volume on Amazon](https://www.amazon.com/dp/B0HBS2RGXR) · [PDF and EPUB](https://leanpub.com/the-backend-of-luck) · [Chapter map](../README.md#chapter-map)

</div>

---

> Companion code for Chapter 28a of *The Backend of Luck*. The chapter itself
> explains the why and the trade-offs; the files here are what you run.
> Example operator throughout the series is the fictional **AcmeToCasino**.

---

> Applied code for TCP/IP tuning, Kafka integration, CQRS patterns, and distributed consistency in casino platforms.

## Overview

These scripts demonstrate the production-level distributed systems patterns covered in Chapter 28a. The focus is on the operational details that matter at scale: kernel-level network tuning, idempotent event processing on Kafka, Redis pub/sub for real-time session state, and an Apache Iggy pilot used as an internal low-latency stream in front of the regulated Kafka backbone. A Cloudflare Workers schema (`worker-d1-schema.sql`) shows how idempotency keys are persisted at the edge.

## Contents

- `applied/idempotency.py` — Python idempotency key manager for deduplicating payment and game-round events
- `applied/redis_subscriber.py` — Redis pub/sub subscriber for distributed session event propagation
- `applied/worker-idempotency.ts` — Cloudflare Workers idempotency handler (TypeScript, D1 storage)
- `applied/worker-d1-schema.sql` — D1 schema for idempotency key persistence at the edge
- `applied/test_applied.py` — pytest suite covering idempotency and subscriber behaviour
- `iggy-pilot/docker-compose.yml` — local Apache Iggy + Web UI pilot stack
- `iggy-pilot/smoke-iggy-casino.sh` — stream/topic creation, synthetic publish, and consume smoke test
- `iggy-pilot/iggy_to_kafka_bridge.py` — Iggy HTTP consumer that forwards events into Kafka with an offset file
- `iggy-pilot/requirements.txt` — Python dependencies for the bridge
- `gli-21/` — GLI-21 v2.2 Client-Server Systems
  - `client-server-boundary-test.py` — Smoke test for the four GLI-21 boundary controls: honest path, replay protection (idempotency), outcome tampering (server-authoritative state), JWT swap (cross-player auth). Deploy-blocking when any scenario fails.

## Technology Stack

- **Language:** Python 3.12, TypeScript (Cloudflare Workers)
- **Messaging:** Redis pub/sub, Apache Iggy, Kafka
- **Edge storage:** Cloudflare D1 (SQLite)
- **Testing:** pytest

## Prerequisites

- Python 3.12+, `redis-py`, `pytest`, `requests`, `kafka-python`
- Node.js / Wrangler CLI for Workers deployment
- Running Redis instance (local or remote)
- Docker Compose for the Iggy pilot
- Kafka bootstrap endpoint for the bridge script

## How to Run

```bash
# Install Python deps
pip install redis pytest

# Run tests
cd applied && pytest test_applied.py -v

# Deploy Workers handler (requires Wrangler)
wrangler deploy worker-idempotency.ts

# Run the Iggy pilot smoke test
cd ../iggy-pilot
cp .env.example .env
docker compose up -d
IGGY_ROOT_USERNAME=iggy IGGY_ROOT_PASSWORD=change-me ./smoke-iggy-casino.sh

# Bridge Iggy telemetry into Kafka
pip install -r requirements.txt
IGGY_ROOT_USERNAME=iggy IGGY_ROOT_PASSWORD=change-me \
KAFKA_BOOTSTRAP_SERVERS=localhost:9092 \
python iggy_to_kafka_bridge.py
```

## Compliance / Security Notes

Idempotent event processing is a regulatory requirement in any jurisdiction that mandates full game-round audit trails (MGA Technical Standards §5, NJ DGE §10). Every bet-placed and withdrawal event must be processed exactly once; duplicate processing constitutes a financial integrity violation. The D1 edge schema supports sub-50 ms idempotency checks without a round-trip to the core database.

The Iggy pilot is intentionally not a source of truth. It is suitable for synthetic casino telemetry, replay labs, and low-latency dashboard experiments. Kafka remains the regulated backbone for fraud, SIGAP, retention, replay, and audit evidence until Iggy has equivalent operational proof.

## Related

- See Chapter 28a in the book for full context on TCP/IP tuning and CAP theorem trade-offs.
- [The Backend of Luck →](https://thebackendofluck.com)

---

<sub>© Backend of Luck — Volume 3 · last updated 2026-04-16.</sub>
