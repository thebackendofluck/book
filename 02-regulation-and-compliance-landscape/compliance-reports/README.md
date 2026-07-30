# Compliance Reports

Regulatory compliance reporting queries for AcmetoCasino's multi-jurisdiction platform. These SQL reports are required by the UKGC and similar regulatory bodies.

## Reports

### Dormant Accounts with Balance (`dormant_accounts_with_balance.sql`)
Identifies player accounts that have not shown activity for 6 or 18 months but still maintain positive balances. Regulators require operators to:
- Contact dormant account holders after 6 months
- Begin escheatment procedures after 18 months (jurisdiction-dependent)
- Report aggregate dormant fund totals

The query uses a CTE to identify active players from `daily_player_stats`, then finds accounts NOT in that set with `balance > 0`. Results are grouped by `global_id` and currency to handle multi-account players.

### Top Winners (`top_winners.sql`)
Identifies the top 100 most profitable players within a jurisdiction for AML and responsible gaming oversight. The net cash formula:

```
Net Cash = (Cash Stakes - Cash Returns/Refunds) - Bonus Conversions - (Credit Adjustments - Debit Adjustments)
```

Uses a two-pass approach: the CTE calculates global net cash across all brands, then the outer query breaks down per-brand activity with registration metadata.

## Database Dependencies

- **casino_core schema:** `users`, `user_accounts`, `user_info`, `countries`, `brands`
- **analytics_dw schema:** `daily_player_stats`, `daily_player_revenue`

## Scheduling

| Report | Frequency | Typical Deadline |
|--------|-----------|-----------------|
| Dormant accounts (6-month) | Monthly | 10th of following month |
| Dormant accounts (18-month) | Monthly | 10th of following month |
| Top winners | Quarterly | 30 days after quarter end |
