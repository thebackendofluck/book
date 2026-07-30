-- =============================================================================
-- REGULATORY REQUIREMENT: National Self-Exclusion Registries
--   (UK GamStop + Sweden Spelpaus)
-- Regulation:
--   UK GamStop: UKGC LCCP SR Code 3.5.1 — operators must participate in the
--              national multi-operator self-exclusion scheme (GamStop);
--              UKGC RTS 14.1 — technical integration requirements;
--              Social Responsibility Code 3.5.1 — mandatory for all online operators
--   Sweden Spelpaus: Spellagen (SFS 2018:1138) §6 kap. 6 § — mandatory integration
--              with Spelpaus national self-exclusion register before player activation;
--              Spelinspektionen FFFS 2019:1 — technical integration specification
-- Purpose:     Test data and verification queries for the national exclusion
--              integration. The system must:
--                (1) Query GamStop before activating any UK player account
--                (2) Query Spelpaus (via SSN) before activating any Swedish player
--                (3) Apply a NATIONAL_EXCLUSION lock if the player is registered
--                (4) NOT permit play until the exclusion period expires
--              Test cases cover excluded and non-excluded players for each registry.
-- API Endpoints:
--   GamStop: batch.stage.gamstop.io/v2 (staging); production endpoint requires
--             operator licence with GamStop
--   Spelpaus: testapi.spelpaus.se (test); production: api.spelpaus.se
-- Penalty:    UKGC: One of the most heavily penalised areas — operators have
--              received fines of £500K to £19M for admitting GamStop-registered
--              players; licence at risk.
--              Sweden Spelinspektionen: Licence suspension; fines up to
--              10% of annual turnover for Spelpaus failures.
-- WARNING 2026 — Spelpaus upgrade: Spelinspektionen has proposed stronger
--              verification using Actor IDs and API keys (expected August 2026).
--              Current SSN-based regex matching may be insufficient after upgrade.
--              Monitor: https://www.spelinspektionen.se for consultation updates.
-- Also applicable (other national registries NOT yet in this codebase — GAPS):
--   Netherlands — CRUKS: kansspelautoriteit.nl/cruks (check on registration AND
--              each login; mandatory for all licensed NL operators; licence renewal
--              from Jan 2026 includes CRUKS integration re-test)
--   Germany   — OASIS: unified self-exclusion register for all German states
--   Denmark   — ROFUS: Registret Over Frivilligt Udelukkede Spillere
--   Italy     — ADM registro delle autoesclusioni
--
-- References:
--   UKGC LCCP: https://www.gamblingcommission.gov.uk/licensees-and-businesses/lccp
--   UKGC Remote Technical Standards: https://www.gamblingcommission.gov.uk/standards/remote-technical-standards
--   Spellagen (2018:1138): https://www.riksdagen.se/sv/dokument-och-lagar/dokument/svensk-forfattningssamling/spellag-20181138_sfs-2018-1138/
--   Spelinspektionen: https://www.spelinspektionen.se/en/
--   KSA (Kansspelautoriteit): https://kansspelautoriteit.nl/
--   CRUKS: https://kansspelautoriteit.nl/cruks/
-- =============================================================================
-- National Exclusion Tool - Test Data Setup
-- Sanitized for book publication.
--
-- CONVERTED FROM ORACLE TO POSTGRESQL:
--   REGEXP_LIKE(col, pattern)  →  col ~ 'pattern'   (PostgreSQL POSIX regex)
--   TO_DATE('...','YYYY-MM-DD')  →  standard ISO date literal (no function needed in PG)
--
-- Stub tables are created here so the script is fully self-contained.
-- In production these schemas/tables are pre-existing.

-- ============================================================
-- STUB SCHEMAS AND TABLES
-- ============================================================

CREATE SCHEMA IF NOT EXISTS platform;

CREATE TABLE IF NOT EXISTS platform.USERS (
    ID                      BIGINT PRIMARY KEY,
    NAME                    VARCHAR(200),
    ENABLED                 SMALLINT DEFAULT 1,
    EXCLUDE_FROM_MARKETING  SMALLINT DEFAULT 0
);

CREATE TABLE IF NOT EXISTS platform.USER_INFO (
    USERID      BIGINT NOT NULL,
    FIRSTNAME   VARCHAR(100),
    LASTNAME    VARCHAR(100),
    DOB         DATE,
    EMAIL       VARCHAR(200),
    POSTALCODE  VARCHAR(20),
    COUNTRY     CHAR(2),
    SSN         VARCHAR(20),
    PHONE       VARCHAR(30)
);

CREATE TABLE IF NOT EXISTS platform.USER_LOCK (
    ID          BIGSERIAL PRIMARY KEY,
    USER_ID     BIGINT NOT NULL,
    LOCK_TYPE_ID VARCHAR(50),
    STATUS      VARCHAR(30)
);

CREATE TABLE IF NOT EXISTS platform.USER_TASKS (
    ID          BIGSERIAL PRIMARY KEY,
    USER_ID     BIGINT NOT NULL,
    TASK_TYPE   VARCHAR(50),
    STATUS      VARCHAR(30)
);

CREATE TABLE IF NOT EXISTS platform.SYSTEM_SETTINGS (
    SETTING_NAME  VARCHAR(200) PRIMARY KEY,
    SETTING_VALUE TEXT
);

-- ============================================================
-- CLEANUP
-- ============================================================

DELETE FROM platform.USER_TASKS WHERE USER_ID BETWEEN 1000 AND 1003;
DELETE FROM platform.USER_LOCK  WHERE USER_ID BETWEEN 1000 AND 1003;
DELETE FROM platform.USER_INFO  WHERE USERID  BETWEEN 1000 AND 1003;
DELETE FROM platform.USERS      WHERE ID      BETWEEN 1000 AND 1003;

-- Registry API endpoints (test/staging)
INSERT INTO platform.SYSTEM_SETTINGS (SETTING_NAME, SETTING_VALUE)
  VALUES ('spelpaus-batch-service-url', 'https://testapi.spelpaus.se')
  ON CONFLICT (SETTING_NAME) DO UPDATE SET SETTING_VALUE = EXCLUDED.SETTING_VALUE;

INSERT INTO platform.SYSTEM_SETTINGS (SETTING_NAME, SETTING_VALUE)
  VALUES ('gamstop-batch-service-url', 'https://batch.stage.gamstop.io/v2')
  ON CONFLICT (SETTING_NAME) DO UPDATE SET SETTING_VALUE = EXCLUDED.SETTING_VALUE;

-- ============================================================
-- TEST USERS
-- ============================================================

-- Swedish user: expected to be EXCLUDED by Spelpaus
-- SSN 071114-4006 hashes to a known blocked ID in test registry
INSERT INTO platform.USERS (ID, NAME, ENABLED, EXCLUDE_FROM_MARKETING)
  VALUES (1000, 'Blocked1 Tester', 1, 0);
INSERT INTO platform.USER_INFO (USERID, FIRSTNAME, LASTNAME, DOB, EMAIL, COUNTRY, SSN)
  VALUES (1000, 'Blocked1', 'Tester',
          '1807-11-14',
          'test-se-blocked@example.com', 'SE', '071114-4006');

-- Swedish user: expected to be NOT excluded
INSERT INTO platform.USERS (ID, NAME, ENABLED, EXCLUDE_FROM_MARKETING)
  VALUES (1001, 'Unblocked1 Tester', 1, 0);
INSERT INTO platform.USER_INFO (USERID, FIRSTNAME, LASTNAME, DOB, EMAIL, COUNTRY, SSN)
  VALUES (1001, 'Unblocked1', 'Tester',
          '1990-02-04',
          'test-se-clean@example.com', 'SE', '123456-2222');

-- UK user: expected to be EXCLUDED by Gamstop
INSERT INTO platform.USERS (ID, NAME, ENABLED, EXCLUDE_FROM_MARKETING)
  VALUES (1002, 'Jane Smith', 1, 0);
INSERT INTO platform.USER_INFO (USERID, FIRSTNAME, LASTNAME, DOB, EMAIL, POSTALCODE, COUNTRY, PHONE)
  VALUES (1002, 'Jane', 'Smith',
          '1945-05-29',
          'test-gb-blocked@example.com', 'PE166RY', 'GB', '07700900000');

-- UK user: expected to be NOT excluded
INSERT INTO platform.USERS (ID, NAME, ENABLED, EXCLUDE_FROM_MARKETING)
  VALUES (1003, 'John Doe', 1, 0);
INSERT INTO platform.USER_INFO (USERID, FIRSTNAME, LASTNAME, DOB, EMAIL, POSTALCODE, COUNTRY, PHONE)
  VALUES (1003, 'John', 'Doe',
          '1970-01-01',
          'test-gb-clean@example.com', 'HP11AA', 'GB', '07700900004');

-- ============================================================
-- VERIFICATION QUERIES
-- ============================================================

-- Swedish users eligible for Spelpaus exclusion check
-- (have a valid SSN, not already nationally excluded)
-- Oracle: REGEXP_LIKE(ui.SSN, '^[0-9]{6}-[0-9]{4}$')
-- PostgreSQL: ui.SSN ~ '^[0-9]{6}-[0-9]{4}$'
SELECT ui.USERID, ui.SSN, ui.DOB
FROM platform.USER_INFO ui
LEFT JOIN (
  SELECT DISTINCT ul.USER_ID, 1 AS NATIONAL_EXCLUDED
  FROM platform.USER_LOCK ul
  WHERE ul.LOCK_TYPE_ID = 'NATIONAL_EXCLUSION'
    AND ul.STATUS NOT IN ('CANCELLED','COMPLETED')
) ul ON ul.USER_ID = ui.USERID
WHERE ui.COUNTRY = 'SE'
  AND ui.SSN IS NOT NULL AND ui.DOB IS NOT NULL
  AND ui.SSN ~ '^[0-9]{6}-[0-9]{4}$'
  AND ul.NATIONAL_EXCLUDED IS NULL;

-- UK users eligible for Gamstop exclusion check
SELECT ui.USERID, ui.FIRSTNAME, ui.LASTNAME, ui.DOB, ui.EMAIL, ui.POSTALCODE, ui.PHONE
FROM platform.USER_INFO ui
LEFT JOIN (
  SELECT DISTINCT ul.USER_ID, 1 AS NATIONAL_EXCLUDED
  FROM platform.USER_LOCK ul
  WHERE ul.LOCK_TYPE_ID = 'NATIONAL_EXCLUSION'
    AND ul.STATUS NOT IN ('CANCELLED','COMPLETED')
) ul ON ul.USER_ID = ui.USERID
WHERE ui.COUNTRY = 'GB'
  AND ui.FIRSTNAME IS NOT NULL AND ui.LASTNAME IS NOT NULL
  AND ui.DOB IS NOT NULL AND ui.POSTALCODE IS NOT NULL
  AND ul.NATIONAL_EXCLUDED IS NULL;
