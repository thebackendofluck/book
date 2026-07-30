# feeder -- Supplier Data Feed Service for Progressive Jackpots

Sanitised extracts from a production progressive jackpot aggregation service that
fetches real-time jackpot values from multiple game suppliers and generates
per-currency JSON feeds for casino frontend widgets.

## Architecture

```
Cron (every 10 minutes)
  |
  v
Feeder.scala (CLI entry point)
  |
  v
ProgressiveJackpots.scala (orchestrator)
  |  (loads currency rates from DB)
  |  (spawns per-currency workers)
  |
  +-- Currency Worker (GBP)
  |     +-- NetEnt (SOAP API)
  |     +-- SG Digital (REST + session auth)
  |     +-- Evolution (REST)
  |     +-- Blueprint (REST)
  |     +-- MGS (REST)
  |     +-- Stakelogic (REST)
  |
  +-- Currency Worker (EUR)
  |     +-- ... (same suppliers)
  |
  +-- ... (29 currencies total)
  |
  v
JSON files: GBP.json, EUR.json, GBP-UKGC.json, ...
  |
  v
S3 Uploader -> CDN -> Casino Frontend Widgets
```

## Key Files

| File | Purpose |
|------|---------|
| `Feeder.scala` | CLI entry point, config resolution, dynamic feed class loading |
| `Feed.scala` | Base trait for all feed types + FeedContext runtime context |
| `ProgressiveJackpots.scala` | Multi-currency, multi-supplier concurrent jackpot aggregation |
| `ProgressiveFeed.scala` | Trait for supplier-specific jackpot fetchers + Jackpot domain model |
| `GameJackpotDAO.scala` | Database mapping from supplier jackpot refs to platform games |
| `jackpots.conf` | Supplier configuration (6 suppliers, class names, API endpoints) |

## Patterns Demonstrated

- **Dynamic class loading**: Feed and supplier classes loaded via `Class.forName` from config
- **Two-level parallelism**: Currency-level threads + supplier-level threads for I/O-bound work
- **Jurisdiction-aware output**: Separate JSON files per jurisdiction (UKGC, MGA) for suppliers with regulatory pools
- **Supplier protocol abstraction**: Each supplier implements `ProgressiveFeed` regardless of API type (SOAP, REST, session-based)
- **Currency normalization**: Cross-currency jackpot values converted via `monthly_currencies` table
- **Cron-safe execution**: 9.8-minute timeout prevents overlap with 10-minute cron schedule
- **Graceful shutdown**: Thread pools drained on JVM shutdown hook
