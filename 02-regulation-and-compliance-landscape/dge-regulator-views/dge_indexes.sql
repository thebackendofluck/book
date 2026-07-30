-- DGE Regulator Views: Performance Indexes
-- These indexes support the 9 NJ DGE regulatory views.
-- Execute BEFORE creating the views and the persistent session triggers.
--
-- Naming convention: idx_<table_abbreviation>_<column(s)>

-- -------------------------------------------------------------------------
-- dge_01_patrons: patron_gaming_date_index on daily_player_balances
-- Supports the balance-change detection subquery in the PATRONS view.
-- -------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_dpb_to_date
    ON analytics_dw.daily_player_balances USING btree (to_date);

-- Composite index for the balance range lookup (from_date/to_date window join)
CREATE INDEX IF NOT EXISTS idx_dpb_userid_from_to_date
    ON analytics_dw.daily_player_balances USING btree (user_id, from_date, to_date);

-- -------------------------------------------------------------------------
-- dge_02_patron_sessions: session lookup on temp_user_session_persistent
-- Supports session ID construction and time-window joins in views 02-06.
-- -------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_tempuser_session_userid
    ON casino_replica.temp_user_session_persistent USING btree (userid);

CREATE INDEX IF NOT EXISTS idx_tempuser_session_created
    ON casino_replica.temp_user_session_persistent USING btree (created);

-- Composite for the time-window join used in WALLETTRANSFERS, CASINOGAMEWAGERS, SPORTSWAGERS
CREATE INDEX IF NOT EXISTS idx_tempuser_session_userid_created_invalidation
    ON casino_replica.temp_user_session_persistent USING btree (userid, created, invalidation_time);

-- -------------------------------------------------------------------------
-- dge_03_wallet_transfers: transaction lookup on user_account_history
-- -------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_uah_userid
    ON casino_core.user_account_history USING btree (userid);

-- TRANSFERTIME_EASTERN_INDEX: already exists as "idx_uah_change_date"

-- Supporting index for wallet transfer session matching (comments = combination_ref)
CREATE INDEX IF NOT EXISTS idx_uah_comments
    ON casino_core.user_account_history USING btree (comments);

-- -------------------------------------------------------------------------
-- dge_04_casino_game_wagers: round and user lookup on user_game_round
-- -------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_ugr_userid
    ON casino_core.user_game_round USING btree (user_id);

-- CASINO_GAME_STARTTIME_EASTERN_INDEX
CREATE INDEX IF NOT EXISTS idx_ugr_started_at
    ON casino_core.user_game_round USING btree (started_at);

-- round_id used in correlated subqueries for coin-in/coin-out calculation
CREATE INDEX IF NOT EXISTS idx_ugr_round_id
    ON casino_core.user_game_round USING btree (round_id);

-- Composite: coin-in/coin-out correlated subqueries filter by comments = round_id and changetype
CREATE INDEX IF NOT EXISTS idx_uah_comments_changetype
    ON casino_core.user_account_history USING btree (comments, changetype);

-- -------------------------------------------------------------------------
-- dge_06_sports_wagers: player and timestamp lookup on feed_messages
-- -------------------------------------------------------------------------
-- PATRON_ACCOUNT_ID lookup via players.external_id
CREATE INDEX IF NOT EXISTS idx_players_external_id
    ON casino_core.players USING btree (external_id);

-- TRANSACTIONTIME_EASTERN_INDEX
CREATE INDEX IF NOT EXISTS idx_fm_update_date
    ON analytics_dw.feed_messages USING btree (update_date);

-- DISTINCT ON (combination_ref) ordering in wallet transfers and sports wagers
CREATE INDEX IF NOT EXISTS idx_fm_combination_ref
    ON analytics_dw.feed_messages USING btree (combination_ref DESC);

-- -------------------------------------------------------------------------
-- dge_07_cash_transactions: deposit and withdrawal lookups
-- -------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_up_user_id
    ON casino_core.user_payments USING btree (user_id);

-- Jurisdiction filter applied in WHERE clause
CREATE INDEX IF NOT EXISTS idx_up_jurisdiction_id
    ON casino_replica.user_payments USING btree (jurisdiction_id);

-- CASH_TRANSACTIONTIME_EASTERN_INDEX on user_withdraws
CREATE INDEX IF NOT EXISTS idx_uw_changedate
    ON casino_core.user_withdraws USING btree (changedate);

CREATE INDEX IF NOT EXISTS idx_uw_jurisdiction_id
    ON casino_replica.user_withdraws USING btree (jurisdiction_id);

-- -------------------------------------------------------------------------
-- dge_08_patron_game_limits: limit change lookups
-- PATRON_ACCOUNT_ID: already exists as "idx_ulc_user_id_1"
-- -------------------------------------------------------------------------

-- LIMIT_TRANSACTTIME_EASTERN_INDEX
CREATE INDEX IF NOT EXISTS idx_ulc_requested
    ON casino_core.user_limit_change USING btree (requested);

-- status filter (WHERE status = 'APPLIED' in CTE)
CREATE INDEX IF NOT EXISTS idx_ulc_status
    ON casino_core.user_limit_change USING btree (status);

-- -------------------------------------------------------------------------
-- dge_09_pii_data: PII and KYC audit lookups
-- PATRON_ACCOUNT_ID: already exists as "users_pk"
-- -------------------------------------------------------------------------

-- LAST_UPDATE_TIME_EASTERN_INDEX on user_information_field_audit
CREATE INDEX IF NOT EXISTS idx_ifa_changed_on
    ON casino_core.user_information_field_audit USING btree (changed_on);

-- DISTINCT ON (user_id) ordering in PII view subqueries
CREATE INDEX IF NOT EXISTS idx_ifa_user_id_changed_on
    ON casino_replica.user_information_field_audit USING btree (user_id, changed_on DESC);

CREATE INDEX IF NOT EXISTS idx_uks_user_id_updated_on
    ON casino_replica.user_kyc_status USING btree (user_id, updated_on DESC);

-- -------------------------------------------------------------------------
-- user_lock_audit: lock lifecycle queries used in PATRONS view
-- -------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_ula_lock_id
    ON casino_replica.user_lock_audit USING btree (lock_id);

CREATE INDEX IF NOT EXISTS idx_ula_timestamp
    ON casino_replica.user_lock_audit USING btree (timestamp);
