# matrix-populator -- User Risk Scoring from Payment Patterns

Sanitised batch job that calculates user risk/behavior scores from payment patterns
for AML (Anti-Money Laundering) and fraud detection compliance.

## Architecture

```
matrix_score_type (DB table)
  |  (defines scoring rules: trigger, time window, condition, score value)
  v
Run.scala (batch entry point)
  |
  v
Populator.scala
  |  (generates Oracle SQL with analytic functions)
  v
user_payments + monthly_currencies + responsible_gaming_actions (source tables)
  |  (windowed aggregations: deposit count, total, declined count, etc.)
  v
user_matrix_score (output table)
  |  (user_id, score_type_id, timestamp, metric details)
  v
Platform Risk Dashboard (consumed by compliance team)
```

## Key Files

| File | Purpose |
|------|---------|
| `Run.scala` | CLI entry point with date range, environment, and score type filters |
| `Populator.scala` | Core scoring engine -- Oracle analytic SQL generation and execution |
| `Model.scala` | Domain model: MatrixScoreType, CalculateOn sealed trait, DB loader |
| `DB.scala` | Synchronous Slick wrapper for batch processing (60min timeout) |
| `application.conf` | Database connection settings (credentials from environment) |
| `build.sbt` | Slick + Oracle + SBT Native Packager |

## Scoring Dimensions

| Trigger | Metrics Computed | Example Condition |
|---------|-----------------|-------------------|
| deposit-confirmed | depositCount, depositTotal (normalized), depositingDays | depositCount > 20 AND depositTotal > 5000 |
| deposit-declined | depositsDeclined | depositsDeclined > 10 |
| deposit-limit-increased | depositLimitIncreases | depositLimitIncreases > 3 |

## Patterns Demonstrated

- **Oracle analytic functions**: `COUNT/SUM OVER(PARTITION BY ... RANGE BETWEEN INTERVAL ... PRECEDING AND CURRENT ROW)` for rolling window metrics
- **Currency normalization**: Joins with monthly_currencies table to convert all amounts to base currency for cross-currency comparison
- **Idempotent batch processing**: `NOT EXISTS` check prevents duplicate scoring
- **Configurable rules**: Scoring conditions stored in DB, no code changes needed for new rules
- **Regulatory scoping**: Country filter restricts scoring to regulated jurisdictions (UKGC)
