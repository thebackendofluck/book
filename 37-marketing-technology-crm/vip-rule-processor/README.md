# VIP Rule Processor v2.0

A real-time VIP tier classification microservice for iGaming platforms.
Evaluates player activity (bets, deposits, withdrawals) against configurable
rules to assign VIP tiers with associated benefits.

Built on the Typelevel Scala stack: cats-effect 3, fs2, doobie, http4s, fs2-kafka.

## Architecture Overview

```
                    +------------------+
                    |  Kafka: Txn      |
                    |  Topic           |
                    +--------+---------+
                             |
                             v
                    +------------------+        +------------------+
                    | Transactions     |------->| PostgreSQL 15    |
                    | Event Processor  |        |                  |
                    +--------+---------+        | - rules          |
                             |                  | - bets           |
                             v                  | - deposits       |
                    +------------------+        | - withdrawals    |
                    | EvaluateUser     |<-------| - user_status    |
                    | Status           |        | - scheduler      |
                    +--------+---------+        +------------------+
                             |
                    +--------v---------+
                    |  Rule Processor  |
                    |  (weighted       |
                    |   scoring)       |
                    +--------+---------+
                             |
                    +--------v---------+
                    | Kafka: VIP       |
                    | Events Topic     |------> CRM / Notifications
                    +------------------+

    Midnight Scheduler:
    +----------+     +------------------+     +------------------+
    | Scheduler|---->| Kafka: Commands  |---->| Recalculation    |
    | Flow     |     | Topic            |     | Cmd Processor    |
    +----------+     +------------------+     +------------------+
```

## How VIP Tiers Work

### The 2D Boundary Model

Each VIP rule defines a rectangular region in two dimensions:
- **X axis**: 30-day deposit volume (in cents)
- **Y axis**: 30-day bet volume (handle, in cents)

The 12 default tiers use qualifiers:
- **POT** (Potential): approaching full VIP status
- **ND** (No Deposit): low deposits but high bet volume
- **NW** (No Wager): high deposits but low bet volume

### v2.0 Weighted Scoring Algorithm

1. **Base score**: tier level x 1000
2. **Volume score**: (deposits + weighted bets) / 100,000
3. **Net deposit bonus**: rewards positive net deposits
4. **Frequency multiplier**: 1.0x to 1.5x based on active days
5. **Game-type weights**: live casino 1.5x, table games 1.3x

### Responsible Gambling Integration

Self-excluded players are **never** promoted regardless of activity.

## Event Flow

### Real-time (Transaction-triggered)

1. Player places bet or makes deposit
2. Transaction event arrives on Kafka
3. Processor persists and triggers VIP evaluation
4. If tier changed: publishes UserVipRuleUpdated event

### Batch (Scheduler-triggered)

1. SchedulerFlow ticks at configured interval
2. Streams user IDs from PostgreSQL
3. Publishes RecalculateCommand per user
4. RecalculationCommandProcessor runs evaluation

## Configuration

| Setting | Env Variable | Default |
|---------|-------------|---------|
| `db.connection-uri` | `DATABASE_URI` | `jdbc:postgresql://localhost:5432/viprules` |
| `kafka.bootstrap-servers` | `KAFKA_SERVERS` | `localhost:9092` |
| `scheduler.enabled` | `SCHEDULER_ENABLED` | `true` |
| `scheduler.clock-interval` | `CLOCK_INTERVAL` | `600s` |
| `http.port` | `HTTP_PORT` | `8080` |

## Local Development

```bash
# Start infrastructure
docker compose up -d db kafka zookeeper

# Run the application
sbt run

# Health check
curl http://localhost:8080/health
```

## Technology Stack

| Component | v1.0 | v2.0 |
|-----------|------|------|
| Scala | 2.13.4 | 2.13.12 |
| cats-effect | 2.x | 3.5.x |
| fs2 | 2.x | 3.9.x |
| DB access | Quill | Doobie 1.x |
| HTTP | http4s 0.21 | http4s 0.23 (Ember) |
| JDK | 11 | 17 |
| PostgreSQL | 13 | 15 |
| Aggregation | In-memory SUM | DB-side SUM |
