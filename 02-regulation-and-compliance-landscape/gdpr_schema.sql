-- =============================================================================
-- REGULATORY REQUIREMENT: GDPR (EU) + UK GDPR + LGPD (Brazil) + PIPEDA (Canada)
-- Regulation:  GDPR (EU) 2016/679 — Art. 12, 15, 17, 20 (Data Subject Rights)
--              UK GDPR + Data Protection Act 2018 (post-Brexit equivalent)
--              LGPD Lei No. 13.709/2018 Art. 18 (Brazilian data subject rights)
--              PIPEDA S.C. 2000 c.5 Principle 9 (Canadian access rights)
-- Purpose:     Database schema for the Data Subject Access Request (DSAR) pipeline.
--              Operators must respond to DSARs within statutory deadlines:
--                EU/UK GDPR: 30 calendar days (extendable to 90 for complex requests)
--                LGPD (Brazil): 15 business days (ANPD guidance 2020)
--                PIPEDA (Canada): 30 calendar days
--              Missing a DSAR deadline is a direct compliance failure.
-- Retention:   GDPR Art. 5(2) accountability: DSAR records and audit logs must be
--              retained to demonstrate compliance — recommended minimum 3 years
-- Audit:       ICO (UK) / IDPC (Malta) / ANPD (Brazil) may request DSAR records
-- Penalty:     GDPR Art. 83(4): up to €10M or 2% global annual turnover for
--              procedural violations (Art. 12 failures, missed deadlines);
--              Art. 83(5): up to €20M or 4% for rights violations (Art. 15/17/20)
--              UK GDPR: equivalent fines in GBP (post-Brexit)
--              LGPD: up to 2% of Brazilian revenue, max R$50M per infraction
-- Jurisdictions: All EU/EEA operators (MGA Malta, Germany GGL, Sweden, Netherlands),
--              UK (UKGC), Brazil (SPA/MF), Canada (AGCO Ontario — PIPEDA)
-- Note:        Oracle compatibility: TEXT → CLOB, SEQUENCE → use Oracle SEQUENCE.
--              This schema uses PostgreSQL syntax (TEXT for CLOBs, SERIAL/SEQUENCE).
--
-- References:
--   GDPR Full Text: https://gdpr-info.eu/
--   Art. 15 (Right of Access): https://gdpr-info.eu/art-15-gdpr/
--   Art. 17 (Right to Erasure): https://gdpr-info.eu/art-17-gdpr/
--   Art. 20 (Data Portability): https://gdpr-info.eu/art-20-gdpr/
--   Art. 83 (Penalties): https://gdpr-info.eu/art-83-gdpr/
--   UK GDPR: https://www.legislation.gov.uk/uksi/2019/419/contents
--   MGA Player Protection Directive: https://www.mga.org.mt/legislation/subsidiary-legislation/
--   LGPD: https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/L13709.htm
--   AGCO iGO Standards: https://www.agco.ca/internet-gaming/standards-and-resources
-- =============================================================================
-- Chapter 02 - Regulation & Compliance Landscape
-- GDPR Data Subject Access Request: Database Schema
--
-- Three tables support the DSAR workflow:
--   1. GDPR_REQUEST              - Backoffice-initiated data access requests
--   2. GDPR_REQUEST_REQUIRED_EXTRACTS - Which data domains are requested
--   3. GDPR_REQUEST_EXTRACT_DATA      - Extracted CSV data stored as CLOBs
--
-- Flow: Backoffice agent creates a request -> service polls for 'pending' ->
-- extracts all 7 data domains -> stores CSVs -> marks 'completed'

CREATE SCHEMA IF NOT EXISTS BACKOFFICE;

DO $$ BEGIN CREATE ROLE gdpr_service_role; EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS BACKOFFICE.GDPR_REQUEST (
    id        INT NOT NULL,
    userId    INT NOT NULL,
    requested TIMESTAMP NOT NULL,
    status    VARCHAR(10),          -- 'pending', 'completed', 'failed'
    completed TIMESTAMP NULL,
    dataType  VARCHAR(5),           -- 'csv'
    allData   CHAR(1) NOT NULL,     -- 'Y' = extract all domains
    CONSTRAINT PK_GDPR_REQUEST PRIMARY KEY(id)
);

CREATE TABLE IF NOT EXISTS BACKOFFICE.GDPR_REQUEST_REQUIRED_EXTRACTS (
    requestId   INT NOT NULL,
    extractName VARCHAR(255) NOT NULL,
    CONSTRAINT PK_GDPR_REQUEST_REQUIRED_EXTRACTS PRIMARY KEY(requestId, extractName)
);

CREATE TABLE IF NOT EXISTS BACKOFFICE.GDPR_REQUEST_EXTRACT_DATA (
    requestId   INT NOT NULL,
    extractName VARCHAR(255) NOT NULL,
    data        TEXT NOT NULL,      -- CSV content for this extract domain (PostgreSQL: TEXT; Oracle: CLOB)
    CONSTRAINT PK_GDPR_REQUEST_EXTRACT_DATA PRIMARY KEY(requestId, extractName)
);

ALTER TABLE BACKOFFICE.GDPR_REQUEST_REQUIRED_EXTRACTS
DROP CONSTRAINT IF EXISTS FK_GRRE_REQUESTID;
ALTER TABLE BACKOFFICE.GDPR_REQUEST_REQUIRED_EXTRACTS
ADD CONSTRAINT FK_GRRE_REQUESTID
    FOREIGN KEY (requestId)
    REFERENCES BACKOFFICE.GDPR_REQUEST (id);

ALTER TABLE BACKOFFICE.GDPR_REQUEST_EXTRACT_DATA
DROP CONSTRAINT IF EXISTS FK_GRED_REQUESTID;
ALTER TABLE BACKOFFICE.GDPR_REQUEST_EXTRACT_DATA
ADD CONSTRAINT FK_GRED_REQUESTID
    FOREIGN KEY (requestId)
    REFERENCES BACKOFFICE.GDPR_REQUEST (id);

CREATE SEQUENCE IF NOT EXISTS BACKOFFICE.GDPR_REQUEST_SEQ;

-- Minimal privilege grants: service account gets only what it needs
GRANT SELECT, INSERT, UPDATE ON BACKOFFICE.GDPR_REQUEST TO gdpr_service_role;
GRANT SELECT, INSERT ON BACKOFFICE.GDPR_REQUEST_EXTRACT_DATA TO gdpr_service_role;
GRANT SELECT ON BACKOFFICE.GDPR_REQUEST_REQUIRED_EXTRACTS TO gdpr_service_role;
GRANT USAGE ON SEQUENCE BACKOFFICE.GDPR_REQUEST_SEQ TO gdpr_service_role;

-- Read-only access to source data tables
-- Note: casino_core schema must exist in the target deployment database.
-- These grants run only if the schema/tables already exist.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema = 'casino_core' AND table_name = 'users') THEN
        EXECUTE 'GRANT SELECT ON casino_core.users TO gdpr_service_role';
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema = 'casino_core' AND table_name = 'user_info') THEN
        EXECUTE 'GRANT SELECT ON casino_core.user_info TO gdpr_service_role';
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema = 'casino_core' AND table_name = 'user_account_history') THEN
        EXECUTE 'GRANT SELECT ON casino_core.user_account_history TO gdpr_service_role';
    END IF;
END $$;
-- (additional table grants for each data domain)

-- Sample account details extract query (one of 7 data domain queries):
--
-- SELECT
--     u.id AS "ID",
--     u.name AS "Username",
--     b.name AS "Brand",
--     DECODE(u.activated, 1, 'Y', 'N') AS "Activated",
--     i.firstname || ' ' || i.lastname AS "Name",
--     LOWER(TRIM(i.email)) AS "Email",
--     i.created AS "Signup",
--     i.lastlogin AS "Last Login",
--     i.dob AS "Date of Birth",
--     TRIM(i.address1) || ' ' || TRIM(i.address2) AS "Address",
--     TRIM(i.town) || ' / ' || NVL(TRIM(c.name), TRIM(i.country)) AS "City / Country"
-- FROM casino_core.users u
-- JOIN casino_core.user_info i ON u.id = i.userid
-- JOIN casino_core.brands b ON b.id = u.affiliateid
-- WHERE u.id = :UserID
