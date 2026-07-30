# AcmeToCasino — Data Analytics

Code and documentation from the AcmeToCasino analytics infrastructure,
as referenced in Chapter 34 (Data Analytics).

## Files

- **logging_config.py** — Structured JSON logging with per-request correlation IDs.
  Every log entry includes timestamp, level, service name, module, action, correlation_id,
  and player_id. Supports both JSON output (production) and human-readable console
  format (development).

- **dashboard-architecture.md** — Documents the 17-tab dashboard structure, all API
  endpoints consumed, WebSocket integration pattern, and frontend architecture decisions.

## How This Maps to Chapter 34

The chapter covers data analytics pipelines for iGambling platforms:

1. **Structured Logging** — JSON-formatted log entries with correlation IDs enable
   log aggregation (ELK, Datadog) and request tracing across the modular monolith.
   The `ContextVar`-based correlation ID propagates automatically through async handlers.

2. **Dashboard as Analytics Consumer** — The 17-tab dashboard demonstrates how
   operational data (health, metrics) and business data (player stats, game rounds,
   wallet events) converge into a single analytics view.

3. **Real-Time vs. Batch** — WebSocket streaming provides real-time event feeds,
   while the `/stats` endpoint aggregates data with SQL (batch-style). Both patterns
   coexist in the same platform.

4. **Metric-Driven Decisions** — Prometheus metrics (defined in the Chapter 30 scripts)
   feed into the dashboard's Analytics and Infrastructure tabs, connecting raw metrics
   to business intelligence.

5. **Audit Trail** — The correlation ID system, combined with the RNG audit hashes
   from the GAL module, creates a complete chain of evidence from player action to
   game outcome to financial settlement.
