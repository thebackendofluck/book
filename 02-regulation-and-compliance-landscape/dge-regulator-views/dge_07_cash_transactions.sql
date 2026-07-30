-- =============================================================================
-- REGULATORY REQUIREMENT: NJ DGE (Division of Gaming Enforcement) + AML/BSA
-- Regulation:  N.J.A.C. 13:69O-1.4(c) — Internet Gaming System Standards;
--              31 U.S.C. §5313 (Bank Secrecy Act) — Currency Transaction Reports;
--              N.J.S.A. 5:12-101 — Casino Control Act AML provisions
-- Section:     §1.4(c)2 — Cash deposit/withdrawal logging; AML Reporting §5
-- Purpose:     Complete deposit and withdrawal audit trail per NJ jurisdiction.
--              DUAL FUNCTION: (1) DGE audit access for reconciliation;
--              (2) AML source data — transactions ≥ $10,000 trigger CTR filing
--              with FinCEN; structuring patterns identified from this view
-- Retention:   10 years minimum per N.J.A.C. 13:69O-1.4(l);
--              5 years minimum per BSA 31 U.S.C. §5314 for AML records
-- Audit Access: Continuous read-only DGE access required;
--              FinCEN may request records within 5-year BSA window
-- Penalty:     DGE: License suspension/revocation (N.J.S.A. 5:12-130);
--              BSA violations: civil penalty up to $10,000/day + criminal
--              prosecution up to $500,000/5 years imprisonment (31 U.S.C. §5322)
-- Note:        UNION ALL on deposits + withdrawals. Filter is jurisdiction_id = 'US-NJ'
--              — only NJ transactions are in scope for this DGE view.
-- Last Verified: March 2026
--
-- Also applicable to:
--   PA PGCB   — 58 Pa. Code §436a.5 (cash transaction records, 10 years)
--   MI MGCB   — MGCB-RAG-21-01 §4.9 (financial transaction logging)
--   Ontario AGCO — FINTRAC PCMLTFA (Canadian AML; deposits/withdrawals reportable)
--   MGA       — AML/CFT Implementing Procedures Part II (4AMLD/5AMLD applies;
--              6AMLD enters into force 10 July 2027 — plan migration now)
--   UKGC      — Money Laundering Regulations 2017 (MLR 2017) Reg. 40 (5-year
--              retention); LCCP SR Code 12.1 (financial crime)
--   Sweden    — Lag (2017:630) om åtgärder mot penningtvätt (AML Act)
--   Brazil    — COAF Circular 1/2024 (AML suspicious transaction reporting);
--              Portaria SPA/MF No. 722/2024 (wallet/deposit data to SIGAP)
--   Curacao   — LOK/CGA AML Policy (2024): MLRO required; FIU reporting under
--              LID; KYC identity checks before transactions exceeding NAf 4,000
--
-- References:
--   N.J.A.C. 13:69O: https://www.njleg.state.nj.us/TitleSearch?TitleNum=13&ChapterNum=69O
--   NJ DGE Technical Standards: https://www.nj.gov/oag/ge/docs/TechStds/InternetGaming/
--   N.J.S.A. 5:12 (Casino Control Act): https://www.njleg.state.nj.us/TitleSearch?TitleNum=5&ChapterNum=12
--   FinCEN BSA: https://www.fincen.gov/resources/statutes-and-regulations
--   UKGC LCCP: https://www.gamblingcommission.gov.uk/licensees-and-businesses/lccp
--   UKGC AML Guidance: https://www.gamblingcommission.gov.uk/guidance/anti-money-laundering
--   MGA Gaming Act: https://www.mga.org.mt/legislation/gaming-act/
--   Spellagen (2018:1138): https://www.riksdagen.se/sv/dokument-och-lagar/dokument/svensk-forfattningssamling/spellag-20181138_sfs-2018-1138/
--   AGCO iGO Standards: https://www.agco.ca/internet-gaming/standards-and-resources
--   5AMLD: https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32018L0843
--   6AMLD: https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32018L1673
--   Portaria SPA/MF 722/2024: https://www.in.gov.br/web/dou/-/portaria-spa/mf-n-722
--   COAF: https://www.gov.br/coaf/
--   CGA (Curacao Gaming Authority): https://curacaogamingauthority.com/
--   LOK (Landsverordening op de Kansspelen): https://curacaogamingauthority.com/legislation/
-- =============================================================================
-- NJ DGE View 07: CASH TRANSACTIONS
-- Complete log of patron cash deposits and withdrawals.
-- Two-part UNION ALL: deposits from user_payments, withdrawals from user_withdraws.
-- Both filtered to US-NJ jurisdiction only (WHERE jurisdiction_id = 'US-NJ').
--
-- Deposit source: casino_replica.user_payments
--   CASH_TRANSACTION_AMOUNT = actual amount only for SUCCEEDED/VOIDED; 0 otherwise
--   CASH_TRANSACTION_REQAMOUNT = requested amount (always set)
--
-- Withdrawal source: casino_replica.user_withdraws
--   CASH_TRANSACTION_AMOUNT = actual amount only for status 1 (ACCEPTED),
--                             3 (REVERSED), 8 (RETURNED); 0 otherwise
--   Withdrawal status codes are mapped to descriptive strings for DGE readability
--
-- Key design choices:
--   - UNION ALL used (not UNION) to avoid deduplication cost; deposits and withdrawals
--     have different ID spaces so no natural duplicates exist
--   - Payment method label and provider name sanitized with regexp_replace
-- Supported by indexes: idx_up_user_id, idx_up_jurisdiction_id,
--                        idx_uw_changedate, idx_uw_jurisdiction_id

CREATE OR REPLACE VIEW dge."vNJDGE07CASHTRANS" AS
-- Part 1: Deposits
SELECT
    'ACMETOCASINO'::varchar                               AS SKIN_NAME,
    up.user_id::varchar                                 AS PATRON_ACCOUNT_ID,
    up.id::varchar                                      AS CASH_TRANSACTION_ID,
    to_char(up.date_updated, 'YYYYMMDD HH24MISS.MS +00:00')::varchar
                                                        AS CASH_TRANSACTIONTIME_SYSTEM,
    concat(
        to_char(up.date_updated AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York', 'YYYYMMDD HH24MISS.MS '),
        to_char(up.date_updated AT TIME ZONE 'America/New_York' - up.date_updated AT TIME ZONE 'UTC', '-HH24:MI')
    )::varchar                                          AS CASH_TRANSACTIONTIME_EASTERN,
    CASE
        WHEN up.status IN ('SUCCEEDED', 'VOIDED')
        THEN cast(up.amount / 100 AS numeric)
        ELSE 0.00
    END                                                 AS CASH_TRANSACTION_AMOUNT,
    'DEPOSIT'::varchar                                  AS CASH_TRANSACTION_TYPE,
    up.status::varchar                                  AS CASH_TRANSACTION_DESCRIPTION,
    upper(regexp_replace(pm.label, '\W+', '', 'g'))::varchar
                                                        AS CASH_TRANSACTION_SOURCE_TYPE,
    upper(regexp_replace(pp."name", '\W+', '', 'g'))::varchar
                                                        AS CASH_TRANSACTION_PROVIDER,
    cast(up.amount / 100 AS numeric)                    AS CASH_TRANSACTION_REQAMOUNT,
    up.date_updated AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York'
                                                        AS CASH_TRANSACTIONTIME_EASTERN_INDEX
FROM casino_replica.user_payments up
JOIN casino_replica.payment_method pm  ON up.payment_method = pm."name"
JOIN casino_replica.payment_provider pp ON up.provider_id = pp.id
WHERE up.jurisdiction_id = 'US-NJ'

UNION ALL

-- Part 2: Withdrawals
SELECT
    'ACMETOCASINO'::varchar                               AS SKIN_NAME,
    uw.userid::varchar                                  AS PATRON_ACCOUNT_ID,
    uw.historyid::varchar                               AS CASH_TRANSACTION_ID,
    to_char(uw.changedate, 'YYYYMMDD HH24MISS.MS +00:00')::varchar
                                                        AS CASH_TRANSACTIONTIME_SYSTEM,
    concat(
        to_char(uw.changedate AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York', 'YYYYMMDD HH24MISS.MS '),
        to_char(uw.changedate AT TIME ZONE 'America/New_York' - uw.changedate AT TIME ZONE 'UTC', '-HH24:MI')
    )::varchar                                          AS CASH_TRANSACTIONTIME_EASTERN,
    CASE
        WHEN uw.status IN (1, 3, 8)
        THEN cast(uw.amount / 100 AS numeric)
        ELSE 0.00
    END                                                 AS CASH_TRANSACTION_AMOUNT,
    'WITHDRAWAL'::varchar                               AS CASH_TRANSACTION_TYPE,
    CASE uw.status
        WHEN 0 THEN 'PENDING'
        WHEN 1 THEN 'ACCEPTED'
        WHEN 2 THEN 'REJECTED'
        WHEN 3 THEN 'REVERSED'
        WHEN 4 THEN 'PROCESSING'
        WHEN 5 THEN 'FAILED'
        WHEN 6 THEN 'REVIEW'
        WHEN 7 THEN 'TIMED_OUT'
        WHEN 8 THEN 'RETURNED'
        WHEN 9 THEN 'BATCH_APPROVED'
    END::varchar                                        AS CASH_TRANSACTION_DESCRIPTION,
    upper(regexp_replace(pm.label, '\W+', '', 'g'))::varchar
                                                        AS CASH_TRANSACTION_SOURCE_TYPE,
    upper(regexp_replace(pp."name", '\W+', '', 'g'))::varchar
                                                        AS CASH_TRANSACTION_PROVIDER,
    cast(uw.amount / 100 AS numeric)                    AS CASH_TRANSACTION_REQAMOUNT,
    uw.changedate AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York'
                                                        AS CASH_TRANSACTIONTIME_EASTERN_INDEX
FROM casino_replica.user_withdraws uw
JOIN casino_replica.payment_method pm  ON uw.method = pm."name"
JOIN casino_replica.payment_provider pp ON uw.processor = pp.id
WHERE uw.jurisdiction_id = 'US-NJ';

GRANT SELECT ON "dge"."vNJDGE07CASHTRANS" TO dge_readonly_external;
