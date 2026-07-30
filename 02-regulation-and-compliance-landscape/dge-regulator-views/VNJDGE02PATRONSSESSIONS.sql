-- =============================================================================
-- FILE NOTE: This UPPERCASE file (VNJDGE02PATRONSSESSIONS.sql) uses Oracle naming conventions
-- (ALL-CAPS, VNJDGE* prefix) but contains PostgreSQL-compatible SQL.
-- Both this file and its lowercase counterpart are functionally identical.
-- The UPPERCASE naming is a legacy convention from the platform's Oracle origins.
-- See the lowercase counterpart in this directory for the full regulatory header.
-- NOTE: These views are PostgreSQL only; Oracle would require different syntax
-- (e.g., no DISTINCT ON, different timezone functions, no CREATE OR REPLACE VIEW).
-- =============================================================================
-- NJ DGE View 02: PATRON SESSIONS
-- Tracks all patron login/logout sessions with system and Eastern Time timestamps.
-- Session ID is constructed as userid-epoch to guarantee uniqueness across all sessions.
--
-- Source: casino_replica.temp_user_session_persistent
--   Only contains sessions that originated from the local NJ spoke database
--   (filtered at insert time by the trigger in dge_persistent_sessions.sql).
--
-- Key design choices:
--   - SESSION_IPV4 is static '000.000.000.000' (IP not captured in this schema)
--   - LOGOUTTIME is NULL for active/unexpired sessions (invalidation_time IS NULL)
--   - Both timestamps dual-format: UTC offset string + DST-aware Eastern offset
-- Supported by indexes: idx_tempuser_session_userid, idx_tempuser_session_created

CREATE OR REPLACE VIEW "dge"."vNJDGE02PATRONSSESSIONS" AS
SELECT
    'ACMETOCASINO' AS SKIN_NAME,
    hub_user_session.userid::text AS PATRON_ACCOUNT_ID,
    CONCAT(
        hub_user_session.userid, '-',
        extract(epoch FROM hub_user_session.created)
    ) AS SESSION_ID,
    TO_CHAR(hub_user_session.created, 'YYYYMMDD HH24MISS.MS +00:00')
        AS LOGINTIME_SYSTEM,
    TO_CHAR(hub_user_session.invalidation_time, 'YYYYMMDD HH24MISS.MS +00:00')
        AS LOGOUTTIME_SYSTEM,
    CONCAT(
        TO_CHAR(hub_user_session.created AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York', 'YYYYMMDD HH24MISS.MS '),
        TO_CHAR(hub_user_session.created AT TIME ZONE 'America/New_York' - hub_user_session.created AT TIME ZONE 'UTC', '-HH24:MI')
    ) AS LOGINTIME_EASTERN,
    CONCAT(
        TO_CHAR(hub_user_session.invalidation_time AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York', 'YYYYMMDD HH24MISS.MS '),
        TO_CHAR(hub_user_session.invalidation_time AT TIME ZONE 'America/New_York' - hub_user_session.invalidation_time AT TIME ZONE 'UTC', '-HH24:MI')
    ) AS LOGOUTTIME_EASTERN,
    NULL AS GEOLOCATION_TRANSACTION_ID,
    '000.000.000.000' AS SESSION_IPV4,
    NULL AS SESSION_IPV6,
    NULL AS SESSION_LATITUDE,
    NULL AS SESSION_LONGITUDE,
    hub_user_session.created AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York'
        AS LOGINTIME_EASTERN_INDEX
FROM casino_replica.temp_user_session_persistent hub_user_session;

GRANT SELECT ON "dge"."vNJDGE02PATRONSSESSIONS" TO dge_readonly_external;
