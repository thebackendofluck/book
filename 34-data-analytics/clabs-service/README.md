# clabs-service -- CompetitionLabs Gamification Integration

Sanitised extracts from a production Kafka-to-RabbitMQ bridge service that integrates
an iGaming platform with CompetitionLabs for leaderboards, achievements, and tournaments.

## Architecture

```
Platform Kafka Topics                     CompetitionLabs
  transaction  ─┐                           REST API
  users        ─┼─> EventsConsumer ─> Transformer ─> RabbitMQ ─> CL Webhooks
  flags_updates─┘                                       |
                                                        v
                                               CL Awards/Rewards
                                                        |
                                                        v
                                              Kafka (user_awards)
                                                        |
                                                        v
                                              Platform Bonus System
```

## Key Files

| File | Purpose |
|------|---------|
| `CLabsAppServer.scala` | Application entry point, module composition, consumer lifecycle |
| `CompetitionLabsApi.scala` | HTTP client for CL REST API (members, events, competitions, rewards) |
| `EventsConsumer.scala` | Generic Kafka-to-RabbitMQ bridge with Alppekko Kafka + AMQP |
| `CLEvent.scala` | Domain model for player activity events (bet, win, deposit, login) |
| `BrandSettings.scala` | Per-brand CL space configuration with RabbitMQ routing |
| `application.conf` | Kafka consumer config, CL API settings, Kamon monitoring |
| `build.sbt` | Pekko HTTP + Pekko Connectors Kafka + Alppekko AMQP + Macwire DI |

## Patterns Demonstrated

- **Kafka-to-RabbitMQ bridge**: Generic `EventsConsumer[T, R]` consuming from Kafka, transforming, publishing to RabbitMQ
- **Per-brand isolation**: Each brand has independent CL space, API key, and RabbitMQ queue
- **Cake pattern modules**: ConfigModule, PekkoModule, KafkaModule, RabbitModule composed at startup
- **Committable partitioned source**: Parallel partition processing with batch committing
- **RestartSource with backoff**: Automatic failure recovery (2s-10s exponential backoff)
- **Game catalog sync**: Scheduled synchronization of platform games as CL products
- **Bi-directional integration**: Platform events -> CL -> award events -> Platform bonus system
