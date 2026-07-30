-- =============================================================================
-- REGULATORY REQUIREMENT: NJ DGE (Division of Gaming Enforcement)
-- Regulation:  N.J.A.C. 13:69O-1.4(b) — Internet Gaming System Standards
-- Section:     §1.4(b)2 — Session logging; §1.4(e) — Regulator data access
-- Purpose:     Real-time audit view for DGE inspectors — every patron login/logout
--              session with dual-timezone timestamps for fraud/geolocation audits
-- Retention:   10 years minimum (N.J.A.C. 13:69O-1.4(l))
-- Audit Access: DGE must have continuous read-only access
-- Penalty:     License suspension or revocation (N.J.S.A. 5:12-130)
-- Note:        SESSION_IPV4 is currently static '000.000.000.000' — IP addresses
--              are not captured in this schema. DGE has been informed; a future
--              schema revision should capture real IPs for geolocation compliance.
-- Last Verified: March 2026
--
-- Also applicable to:
--   PA PGCB   — 58 Pa. Code §436a.7 (session activity logs, 10 years)
--   MI MGCB   — MGCB-RAG-21-01 §4.4 (session logging requirements)
--   Ontario AGCO — Registrar's Standards Standard 3.2 (session records)
--   MGA       — Gaming Compliance Directive §6 (session logging recommended)
--   UKGC      — LCCP SR Code 3.4.1 (transaction/session records)
--   Sweden    — Spelinspektionen FFFS 2019:1 (session data reporting)
--   Brazil    — Portaria SPA/MF No. 722/2024 (login events to SIGAP daily batch)
--
-- References:
--   N.J.A.C. 13:69O: https://www.njleg.state.nj.us/TitleSearch?TitleNum=13&ChapterNum=69O
--   NJ DGE Technical Standards: https://www.nj.gov/oag/ge/docs/TechStds/InternetGaming/
--   N.J.S.A. 5:12 (Casino Control Act): https://www.njleg.state.nj.us/TitleSearch?TitleNum=5&ChapterNum=12
--   UKGC LCCP: https://www.gamblingcommission.gov.uk/licensees-and-businesses/lccp
--   MGA Gaming Act: https://www.mga.org.mt/legislation/gaming-act/
--   Spellagen (2018:1138): https://www.riksdagen.se/sv/dokument-och-lagar/dokument/svensk-forfattningssamling/spellag-20181138_sfs-2018-1138/
--   AGCO iGO Standards: https://www.agco.ca/internet-gaming/standards-and-resources
--   Portaria SPA/MF 722/2024: https://www.in.gov.br/web/dou/-/portaria-spa/mf-n-722
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
