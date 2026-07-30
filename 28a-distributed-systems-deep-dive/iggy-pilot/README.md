# Apache Iggy Casino Streaming Pilot

This directory contains the Chapter 28a pilot scripts for running Apache Iggy as an internal low-latency stream beside the existing Kafka fraud and platform event backbone.

Iggy is used here for synthetic game telemetry, supplier callback replay labs, fraud feature experiments, and dashboard latency tests. It is not the authority for wallets, RNG outcomes, withdrawals, SIGAP reporting, or regulated fraud evidence.

## Topology

```text
games / bots / supplier callbacks
  -> Apache Iggy stream casino-ops / casino-events
  -> iggy_to_kafka_bridge.py
  -> Kafka topic game-events
  -> fraud, analytics, monitoring
```

## Files

- `docker-compose.yml` starts Iggy Server and the Iggy Web UI for an internal pilot.
- `.env.example` contains the minimum environment variables.
- `smoke-iggy-casino.sh` logs in, creates the stream/topic when needed, publishes a synthetic event, and reads it back.
- `iggy_to_kafka_bridge.py` polls Iggy over HTTP and forwards decoded events into Kafka.
- `requirements.txt` installs the bridge dependencies.

## Run

```bash
cp .env.example .env
docker compose up -d

IGGY_ROOT_USERNAME=iggy \
IGGY_ROOT_PASSWORD=change-me \
./smoke-iggy-casino.sh
```

For the bridge:

```bash
pip install -r requirements.txt

IGGY_ROOT_USERNAME=iggy \
IGGY_ROOT_PASSWORD=change-me \
KAFKA_BOOTSTRAP_SERVERS=localhost:9092 \
KAFKA_TOPIC=game-events \
python iggy_to_kafka_bridge.py
```

## Production Guardrails

- Keep DNS internal through pfSense host overrides.
- Put the HTTP API and Web UI behind the internal NPM front door.
- Do not publish Iggy TCP, QUIC, or WebSocket transports to the public edge.
- Keep Kafka as the regulated source of truth until retention, replay, schema governance, and monitoring are proven for the Iggy path.
- Store bridge offsets on persistent disk and monitor bridge lag before using it for any operational decision.
