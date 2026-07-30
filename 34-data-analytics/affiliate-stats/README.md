# Affiliate Stats -- Kafka Streams Aggregation (Scala/Play)

Production Scala service that aggregates hourly affiliate statistics from
Kafka transaction events and persists them to PostgreSQL.

**Stack:** Scala 2.12, Play Framework, Kafka Streams 3.x, Apache Pekko, Slick, PostgreSQL

## Data Flow

```
Kafka "transaction" topic
        |
   HourlyStatsStream  (Kafka Streams -- 1-hour tumbling window)
        |
   groupByKey(userId) -> windowedBy(1h) -> aggregate(betCount) -> suppress
        |
Kafka "hourly_stats" topic
        |
   HourlyStatsConsumer (Pekko Kafka consumer)
        |
   PostgreSQL  (affiliate_stats.hourly_stats table)
```

## Files

| File | Purpose |
|------|---------|
| `HourlyStatsStream.scala` | Kafka Streams topology -- tumbling window aggregation |
| `HourlyStatsConsumer.scala` | Consumer that writes aggregated stats to PostgreSQL |
| `EventStream.scala` | Base class for all Kafka Streams topologies |
| `ConsumedMessagesDAO.scala` | Slick database access layer for hourly stats |
| `schema.sql` | PostgreSQL schema and evolution scripts |
| `HourlyStatsStreamSpec.scala` | Topology unit tests with TopologyTestDriver |

## Key Patterns

- **Tumbling windows with suppress**: Events are grouped by userId into 1-hour
  windows. `suppress(untilWindowCloses)` ensures only final aggregates are
  emitted, avoiding partial results.
- **Debit counting**: Only `debit` events (bets) increment the bet counter;
  credits and deposits are ignored.
- **Static group membership**: `GROUP_INSTANCE_ID_CONFIG` avoids unnecessary
  rebalances during rolling deployments.
- **Configurable window size**: Window duration is read from system settings,
  allowing runtime tuning without redeployment.
