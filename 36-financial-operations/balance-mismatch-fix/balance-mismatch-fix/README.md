# Balance Mismatch Fix

Python utility for reconciling account balance mismatches in an online casino platform. This tool fetches failed Kafka message deliveries from Elasticsearch, parses them back into domain events (AccountsEvent, RoundPlayedEvent), and replays them to the correct Kafka topics to restore balance consistency.

## Architecture

- **Elasticsearch Integration**: Queries Kibana proxy for failed message push logs within a configurable date range
- **Event Parsing**: Reconstructs AccountsEvent and RoundPlayedEvent objects from log text using regex-based parsing
- **Kafka Replay**: Re-publishes parsed events to original Kafka topics in chronological order per user
- **Database Lookup**: Retrieves user metadata (internal/external IDs) from PostgreSQL via SQLAlchemy
- **Dry Run Mode**: Supports safe testing without actually sending messages

## Key Components

| File | Description |
|------|-------------|
| `main.py` | Entry point - orchestrates fetch, parse, filter, and replay pipeline |
| `service.py` | ElasticClient (scroll API), event parsers, Kafka replay logic, UserRepository |
| `models.py` | Pydantic models: AccountsEvent, RoundPlayedEvent, AppConfig, UserRecord |

## Configuration

| Env Variable | Description | Default |
|---|---|---|
| `FROM_DATE` | Start date (ISO format, inclusive) | `2021-03-01T00:00:00.000Z` |
| `TO_DATE` | End date (ISO format, inclusive) | `2021-03-30T00:00:00.000Z` |
| `USER_ID_LIST` | Comma-separated user IDs to process | (required) |
| `ELASTIC_HOST` | Elasticsearch host URL | `https://localhost:9200` |
| `KAFKA_HOST` | Kafka bootstrap servers | `localhost:9092` |
| `DB_URL` | PostgreSQL connection string (asyncpg format) | (required) |
| `DRY_RUN` | Skip sending if true | `false` |

## Tech Stack

- Python 3.12, FastAPI
- Apache Kafka via confluent-kafka (producer replay)
- Elasticsearch (log source via scroll API)
- PostgreSQL + SQLAlchemy 2.0 async (user metadata)
- httpx (HTTP client)
- Pydantic (event models and config)
- structlog (structured logging)

## Chapter Reference

This code accompanies **Chapter 36: Financial Operations**, illustrating how iGaming platforms handle balance reconciliation when Kafka message delivery failures cause account state divergence.
