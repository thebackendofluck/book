-- DGE Regulator Views: Persistent Session Tracking
-- Creates the infrastructure for tracking user sessions across
-- hub and spoke database architecture (multi-state operations).
--
-- In a multi-state US iGaming deployment, each state has a "spoke"
-- database and a central "hub" replicates session data. This trigger
-- system ensures only sessions originating from the local NJ spoke
-- are persisted for DGE reporting.
--
-- Two-trigger architecture to handle a race condition:
--   Trigger 1 (hub -> persistent): fires when a session arrives in the hub replica.
--     Checks if the user has an active spoke_session; if so, records it as local.
--   Trigger 2 (spoke -> persistent, reverse): fires when a spoke_session is created.
--     Backfills the most recent hub session in case replication arrived first.
--     ON CONFLICT DO NOTHING prevents duplicates.
--
-- Execute AFTER dge_indexes.sql and BEFORE creating the DGE views.

-- Persistent session storage table
CREATE TABLE IF NOT EXISTS casino_replica.temp_user_session_persistent
(
    userid            numeric(38)  NOT NULL,
    sessionid         varchar(36)  NOT NULL,
    created           timestamp    NOT NULL,
    lasttouch         timestamp,
    status            varchar(32),
    invalidation_time timestamp
);

-- Trigger function: when a session appears in the hub,
-- check if the user has an active spoke session.
-- If yes, this is a local session -- persist it for DGE.
-- If no, it originated from another state -- skip it.
CREATE OR REPLACE FUNCTION casino_replica.casino_replica_user_session_persistent()
    RETURNS TRIGGER
    LANGUAGE PLPGSQL
AS
$$
BEGIN
    IF (EXISTS(
        SELECT 1
        FROM casino_core.spoke_session
        WHERE casino_core.spoke_session.user_id = NEW.userid
    )) THEN
        INSERT INTO casino_replica.temp_user_session_persistent(
            userid, sessionid, created, lasttouch, status, invalidation_time
        ) VALUES (
            NEW.userid, NEW.sessionid, NEW.created,
            NEW.lasttouch, NEW.status, NEW.invalidation_time
        );
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS casino_replica_user_session_persistent_changed
    ON casino_replica.user_session;
CREATE TRIGGER casino_replica_user_session_persistent_changed
    AFTER INSERT
    ON casino_replica.user_session
    FOR EACH ROW
    EXECUTE PROCEDURE casino_replica.casino_replica_user_session_persistent();


-- Reverse trigger: when a spoke session is created,
-- backfill the most recent hub session for that user.
-- Handles race condition where spoke session arrives before hub replication.
CREATE OR REPLACE FUNCTION casino_core.casino_core_spoke_session_user_session_persistent()
    RETURNS TRIGGER
    LANGUAGE PLPGSQL
AS
$$
BEGIN
    INSERT INTO casino_replica.temp_user_session_persistent
        (userid, sessionid, created, lasttouch)
    SELECT hub.userid, hub.sessionid, hub.created, hub.lasttouch
    FROM casino_replica.user_session hub
    WHERE NEW.user_id = hub.userid
    ORDER BY hub.created DESC
    LIMIT 1
    ON CONFLICT DO NOTHING;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS casino_core_spoke_session_user_session_persistent_changed
    ON casino_core.spoke_session;
CREATE TRIGGER casino_core_spoke_session_user_session_persistent_changed
    AFTER INSERT
    ON casino_core.spoke_session
    FOR EACH ROW
    EXECUTE PROCEDURE casino_core.casino_core_spoke_session_user_session_persistent();


-- Cross-schema permissions for trigger execution
GRANT USAGE ON SCHEMA casino_replica TO casino_core;
GRANT INSERT ON casino_replica.temp_user_session_persistent TO casino_core;
GRANT SELECT ON casino_replica.user_session TO casino_core;
GRANT SELECT ON casino_replica.temp_user_session_persistent TO dge_readonly_external;
