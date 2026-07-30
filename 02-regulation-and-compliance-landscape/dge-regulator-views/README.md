# NJ DGE Regulator Views

Database views implementing New Jersey Division of Gaming Enforcement (DGE) regulatory reporting requirements for AcmetoCasino's iGaming platform.

## Architecture

The views operate across a multi-schema PostgreSQL database architecture used in US multi-state iGaming deployments:

- **casino_core** -- Primary spoke database (state-specific)
- **casino_replica** -- Hub database replica (cross-state user data)
- **analytics_dw** -- Analytical/statistical data
- **dge** -- Dedicated schema for regulatory views (read-only external access)

## Views

| File | DGE View Name | Purpose | Key Data Sources |
|------|--------------|---------|-----------------|
| `dge_01_patrons.sql` | `vNJDGE01PATRONS` | Patron demographics, balances, exclusions, account status | daily_player_balances, user_lock_audit |
| `dge_02_patron_sessions.sql` | `vNJDGE02PATRONSSESSIONS` | Login/logout session tracking | temp_user_session_persistent |
| `dge_03_wallet_transfers.sql` | `vNJDGE03WALLETTRANSFERS` | Fund transfer log (TOWALLET/FROMWALLET) | user_account_history, user_accounts |
| `dge_04_casino_game_wagers.sql` | `vNJDGE04CASINOGAMEWAGERS` | Casino game wager details with coin-in/out/winloss | user_game_round, user_account_history |
| `dge_05_poker_game_wagers.sql` | `vNJDGE05POKERGAMEWAGERS` | Poker wagers (placeholder -- no P2P poker offered) | N/A |
| `dge_06_sports_wagers.sql` | `vNJDGE06SPORTSWAGERS` | Sports betting via Kambi OSP integration | feed_messages, players |
| `dge_07_cash_transactions.sql` | `vNJDGE07CASHTRANS` | Cash deposits and withdrawals | user_payments, user_withdraws |
| `dge_08_patron_game_limits.sql` | `vNJDGE08PATRONSGAMELIMS` | Gaming limit changes (deposit, time, spend, loss) | user_limit_change |
| `dge_09_pii_data.sql` | `vNJDGE09PII` | Personally identifiable information and KYC status | user_info, user_kyc_status |

## Installation Order

1. `dge_indexes.sql` -- Create performance indexes
2. `dge_persistent_sessions.sql` -- Set up session tracking infrastructure
3. `dge_01_patrons.sql` through `dge_09_pii_data.sql` -- Create views in order

## Key Technical Patterns

- **All timestamps dual-format:** System time (UTC with offset) and Eastern Time (with DST-aware offset)
- **Hub/spoke session tracking:** Triggers ensure only local-state sessions are persisted
- **Amounts in cents:** All monetary values stored as integers, divided by 100 in views
- **DISTINCT ON for latest records:** Used in PII view for most-recent KYC status and audit entries
- **Window functions:** LEAD() in gaming limits to calculate effective date ranges
- **JSONB extraction:** Sports wagers parse Kambi betslip JSON for event IDs and leagues

## Access Control

Views are granted to `dge_readonly_external`, a read-only role created specifically for DGE access. The regulator connects via IP-whitelisted VPN with SSL/TLS encryption.
