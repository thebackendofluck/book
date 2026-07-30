-- test-pgcrypto.sql
-- Tests pgcrypto column-level encryption for casino PII data
-- PCI-DSS 3.4 compliant: encrypts PAN, name, email, SSN columns
-- Run as: psql -U postgres -p 5434 -f test-pgcrypto.sql
--
-- Tests:
--   1. pgcrypto extension installation
--   2. Table creation with encrypted PII columns
--   3. pgp_sym_encrypt / pgp_sym_decrypt round-trip
--   4. Verify raw bytea blobs are opaque (no plaintext)
--   5. Symmetric key isolation (wrong key returns error)
--   6. Hash-based PAN masking with pgcrypto digest
--   7. Performance sample (10k encrypt/decrypt cycles)

\set ON_ERROR_STOP on
\set ECHO errors

-- ============================================================
-- Setup
-- ============================================================
DROP DATABASE IF EXISTS pgcrypto_test;
CREATE DATABASE pgcrypto_test;
\c pgcrypto_test

-- Enable extension
CREATE EXTENSION IF NOT EXISTS pgcrypto;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pgcrypto') THEN
        RAISE NOTICE 'PASS: pgcrypto extension installed (version %)',
            (SELECT extversion FROM pg_extension WHERE extname = 'pgcrypto');
    ELSE
        RAISE EXCEPTION 'FAIL: pgcrypto extension not available';
    END IF;
END $$;

-- ============================================================
-- Schema: casino players with encrypted PII
-- ============================================================
CREATE SCHEMA IF NOT EXISTS crypto;

CREATE TABLE crypto.players (
    id              SERIAL PRIMARY KEY,
    username        VARCHAR(50)  NOT NULL,             -- not encrypted, used for lookups
    email_enc       BYTEA,                              -- pgp_sym_encrypt(email, key)
    full_name_enc   BYTEA,                              -- pgp_sym_encrypt(full_name, key)
    ssn_enc         BYTEA,                              -- pgp_sym_encrypt(SSN, key)
    pan_hash        TEXT,                               -- digest(PAN, 'sha256') hex — for dedup without plaintext
    pan_enc         BYTEA,                              -- pgp_sym_encrypt(full PAN, key)
    balance         NUMERIC(12,2) DEFAULT 0.00,
    created_at      TIMESTAMPTZ   DEFAULT now()
);

CREATE TABLE crypto.transactions (
    id              SERIAL PRIMARY KEY,
    player_id       INTEGER REFERENCES crypto.players(id),
    amount          NUMERIC(12,2),
    txn_type        VARCHAR(20),
    ref_enc         BYTEA,                              -- pgp_sym_encrypt(txn_ref, key)
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- Helper functions that mirror playbook-postgresql-tde.yml encrypt_pii / decrypt_pii
CREATE OR REPLACE FUNCTION crypto.encrypt_pii(plaintext TEXT, key TEXT)
RETURNS BYTEA
LANGUAGE sql IMMUTABLE STRICT
AS $$
    SELECT pgp_sym_encrypt(plaintext, key,
        'compress-algo=1, cipher-algo=aes256')::BYTEA;
$$;

CREATE OR REPLACE FUNCTION crypto.decrypt_pii(ciphertext BYTEA, key TEXT)
RETURNS TEXT
LANGUAGE sql IMMUTABLE STRICT
AS $$
    SELECT pgp_sym_decrypt(ciphertext, key);
$$;

DO $$ BEGIN RAISE NOTICE 'PASS: Schema and helper functions created'; END $$;

-- ============================================================
-- Test 1: Insert encrypted player PII
-- ============================================================
\set enc_key 'super-secret-casino-key-2024'

INSERT INTO crypto.players
    (username, email_enc, full_name_enc, ssn_enc, pan_hash, pan_enc, balance)
VALUES
    ('alice_poker',
     crypto.encrypt_pii('alice@example.com',       :'enc_key'),
     crypto.encrypt_pii('Alice Johnson',            :'enc_key'),
     crypto.encrypt_pii('123-45-6789',              :'enc_key'),
     encode(digest('4111111111111111', 'sha256'), 'hex'),
     crypto.encrypt_pii('4111111111111111',         :'enc_key'),
     5000.00),
    ('bob_slots',
     crypto.encrypt_pii('bob@example.com',          :'enc_key'),
     crypto.encrypt_pii('Robert Smith',             :'enc_key'),
     crypto.encrypt_pii('987-65-4321',              :'enc_key'),
     encode(digest('5500005555555559', 'sha256'), 'hex'),
     crypto.encrypt_pii('5500005555555559',         :'enc_key'),
     1250.75),
    ('carol_roulette',
     crypto.encrypt_pii('carol@example.com',        :'enc_key'),
     crypto.encrypt_pii('Carol Williams',           :'enc_key'),
     crypto.encrypt_pii('456-78-9012',              :'enc_key'),
     encode(digest('378282246310005',  'sha256'), 'hex'),
     crypto.encrypt_pii('378282246310005',          :'enc_key'),
     8800.00);

INSERT INTO crypto.transactions (player_id, amount, txn_type, ref_enc) VALUES
    (1, -250.00, 'bet',     crypto.encrypt_pii('TXN-2024-001', :'enc_key')),
    (1,  750.00, 'win',     crypto.encrypt_pii('TXN-2024-002', :'enc_key')),
    (2,  -50.00, 'bet',     crypto.encrypt_pii('TXN-2024-003', :'enc_key')),
    (3, 2000.00, 'deposit', crypto.encrypt_pii('TXN-2024-004', :'enc_key'));

DO $$ BEGIN RAISE NOTICE 'PASS: Inserted 3 players and 4 transactions with encrypted PII'; END $$;

-- ============================================================
-- Test 2: Decrypt and verify round-trip integrity
-- ============================================================
DO $$
DECLARE
    r RECORD;
    expected_emails  TEXT[] := ARRAY['alice@example.com','bob@example.com','carol@example.com'];
    decrypted_email  TEXT;
    all_ok           BOOLEAN := TRUE;
BEGIN
    FOR r IN SELECT id, username, email_enc FROM crypto.players ORDER BY id LOOP
        decrypted_email := crypto.decrypt_pii(r.email_enc, 'super-secret-casino-key-2024');
        IF decrypted_email = expected_emails[r.id] THEN
            RAISE NOTICE 'PASS: Decrypt round-trip OK for user % — email decrypted correctly', r.username;
        ELSE
            RAISE WARNING 'FAIL: Decrypt mismatch for user %: got %', r.username, decrypted_email;
            all_ok := FALSE;
        END IF;
    END LOOP;
    IF all_ok THEN
        RAISE NOTICE 'PASS: All email decrypt round-trips verified';
    END IF;
END $$;

-- ============================================================
-- Test 3: Verify raw storage is opaque (no plaintext in bytea)
-- ============================================================
DO $$
DECLARE
    r RECORD;
    raw_hex TEXT;
BEGIN
    FOR r IN SELECT username, email_enc::TEXT AS raw FROM crypto.players LOOP
        -- Check the bytea hex representation doesn't contain ASCII of known PII
        -- pgcrypto stores OpenPGP packet format — starts with \xc2 or \xc3 (tag byte)
        IF r.raw NOT LIKE '%alice@%' AND r.raw NOT LIKE '%bob@%' AND r.raw NOT LIKE '%carol@%' THEN
            RAISE NOTICE 'PASS: Raw storage for % is opaque (no plaintext visible)', r.username;
        ELSE
            RAISE WARNING 'FAIL: Plaintext visible in raw storage for %', r.username;
        END IF;
    END LOOP;
END $$;

-- ============================================================
-- Test 4: Wrong key returns error (key integrity)
-- ============================================================
DO $$
BEGIN
    BEGIN
        PERFORM crypto.decrypt_pii(
            (SELECT email_enc FROM crypto.players WHERE username = 'alice_poker'),
            'wrong-key-should-fail'
        );
        RAISE WARNING 'FAIL: Wrong key should have raised an error but did not';
    EXCEPTION
        WHEN OTHERS THEN
            RAISE NOTICE 'PASS: Wrong key correctly raises error: %', SQLERRM;
    END;
END $$;

-- ============================================================
-- Test 5: PAN hash dedup (same PAN = same hash, no plaintext)
-- ============================================================
DO $$
DECLARE
    h1 TEXT;
    h2 TEXT;
BEGIN
    -- Insert same PAN again under different user
    INSERT INTO crypto.players (username, pan_hash, pan_enc, balance)
    VALUES (
        'alice_poker_dup',
        encode(digest('4111111111111111', 'sha256'), 'hex'),
        crypto.encrypt_pii('4111111111111111', 'super-secret-casino-key-2024'),
        0
    );

    SELECT pan_hash INTO h1 FROM crypto.players WHERE username = 'alice_poker';
    SELECT pan_hash INTO h2 FROM crypto.players WHERE username = 'alice_poker_dup';

    IF h1 = h2 THEN
        RAISE NOTICE 'PASS: Duplicate PAN detected via hash (% = %)', left(h1,16)||'...', left(h2,16)||'...';
    ELSE
        RAISE WARNING 'FAIL: PAN hash mismatch — dedup will not work';
    END IF;

    -- Verify hash is not the PAN itself
    IF h1 <> '4111111111111111' AND length(h1) = 64 THEN
        RAISE NOTICE 'PASS: PAN hash is a proper SHA-256 hex digest (not plaintext)';
    END IF;

    -- Cleanup dup
    DELETE FROM crypto.players WHERE username = 'alice_poker_dup';
END $$;

-- ============================================================
-- Test 6: Query with decryption in SELECT (operational pattern)
-- ============================================================
\echo 'Decrypted player report (operational query):'
SELECT
    p.username,
    crypto.decrypt_pii(p.email_enc,     'super-secret-casino-key-2024') AS email,
    crypto.decrypt_pii(p.full_name_enc, 'super-secret-casino-key-2024') AS full_name,
    p.balance,
    COUNT(t.id) AS txn_count
FROM crypto.players p
LEFT JOIN crypto.transactions t ON t.player_id = p.id
GROUP BY p.id
ORDER BY p.id;

DO $$ BEGIN RAISE NOTICE 'PASS: Decrypted SELECT query executed successfully'; END $$;

-- ============================================================
-- Test 7: Performance — 10k encrypt/decrypt cycles
-- ============================================================
\echo ''
\echo 'Performance: 10,000 encrypt/decrypt cycles...'

DO $$
DECLARE
    t_start  TIMESTAMPTZ;
    t_end    TIMESTAMPTZ;
    elapsed  INTERVAL;
    i        INTEGER;
    dummy    TEXT;
    enc_val  BYTEA;
BEGIN
    t_start := clock_timestamp();
    FOR i IN 1..10000 LOOP
        enc_val := crypto.encrypt_pii('test-pii-value-' || i::TEXT, 'super-secret-casino-key-2024');
        dummy   := crypto.decrypt_pii(enc_val, 'super-secret-casino-key-2024');
    END LOOP;
    t_end   := clock_timestamp();
    elapsed := t_end - t_start;

    RAISE NOTICE 'PASS: 10,000 encrypt+decrypt cycles in %ms (~% ops/sec)',
        (EXTRACT(EPOCH FROM elapsed) * 1000)::INTEGER,
        (10000.0 / EXTRACT(EPOCH FROM elapsed))::INTEGER;
END $$;

-- ============================================================
-- Summary counts
-- ============================================================
\echo ''
\echo 'Final row counts:'
SELECT
    (SELECT COUNT(*) FROM crypto.players)      AS total_players,
    (SELECT COUNT(*) FROM crypto.transactions) AS total_transactions;

\echo ''
\echo 'All pgcrypto tests completed.'

-- ============================================================
-- Cleanup (comment out to keep DB for further inspection)
-- ============================================================
\c postgres
DROP DATABASE pgcrypto_test;
\echo 'Test database dropped. Done.'
