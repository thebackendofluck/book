# Sportsbook Ledger

Scala service that consumes bet messages from a sportsbook provider's Bet Message Collector (BMC) feed and persists them to a PostgreSQL database. This provides the casino platform with a local ledger of all sportsbook betting activity for reconciliation, reporting, and regulatory compliance.

## Architecture

- **BMC Feed Consumer**: Polls the sportsbook provider's REST API for new bet settlement messages using mTLS client certificates
- **Database Persistence**: Stores bet data in PostgreSQL using Slick with JSON column support (slick-pg)
- **Health Monitoring**: Pekko HTTP health check endpoint
- **Metrics**: Kamon instrumentation with Datadog integration for monitoring feed lag, batch sizes, and errors
- **Jurisdiction Filtering**: Configurable filtering by US state jurisdiction (e.g., Michigan, Pennsylvania)

## Key Components

| File | Description |
|------|-------------|
| `Main.scala` | Entry point - initializes context, starts HTTP server, runs feed polling loop |
| `BmcConnector.scala` | HTTP client for sportsbook provider BMC API with mTLS |
| `FeedService.scala` | Feed message processing and deduplication logic |
| `BmcFeedDao.scala` | Database access for feed message tracking |
| `BetsDao.scala` | Database access for bet storage |
| `Bet.scala` | Bet domain model |
| `LedgerContext.scala` | Application context wiring (cake pattern) |
| `Metrics.scala` | Kamon metrics helpers |
| `JsonPostgresProfile.scala` | Slick profile extension for PostgreSQL JSON columns |

## Configuration

| Env Variable | Description | Default |
|---|---|---|
| `BMC_URL` | Sportsbook provider BMC feed URL | (provider API endpoint) |
| `PLATFORM_DB_URL` | PostgreSQL JDBC URL | `jdbc:postgresql://localhost:5432/Sandbox` |
| `PLATFORM_DB_USER` | Database user | `postgres` |
| `PLATFORM_DB_PASSWORD` | Database password | `passwd` |
| `CERT_NAME` | Path to mTLS client certificate (PKCS12) | `/opt/sportsbook-dev.p12` |
| `BMC_SCHEDULER_RATE` | Polling interval in seconds | `30` |
| `BMC_BATCH_SIZE` | Messages per fetch batch | `1000` |
| `JURISDICTION_FILTERING` | Enable jurisdiction filter | `true` |
| `JURISDICTION` | Comma-separated jurisdictions | `US_MICHIGAN,US_PENNSYLVANIA` |

## Running Locally

```bash
sbt compile
sbt docker:publishLocal
docker-compose up
```

## Tech Stack

- Scala 2.12, Pekko HTTP + Pekko Streams
- Slick + slick-pg (PostgreSQL with JSON)
- Dispatch (HTTP client)
- Kamon + Datadog (metrics)
- json4s + spray-json (serialization)
- Docker (sbt-native-packager)

## Chapter Reference

This code accompanies **Chapter 15: Casino Mathematics and Game Economy**, demonstrating how iGaming platforms integrate with third-party sportsbook providers to maintain a local bet ledger for financial reconciliation and regulatory reporting.
