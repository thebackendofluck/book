-- 10-schema.sql
-- Initializes the demo schema used by book chapter 27d tests.
-- pgcrypto ships with every Postgres image; pg_aegis is optional.
--
-- Honesty mode: when pg_aegis is missing, the aegis_* helper functions
-- fall back to pgcrypto so book readers can still compare workloads —
-- they just won't see the 4.3x INSERT win from the article.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

DO $$
BEGIN
  BEGIN
    EXECUTE 'CREATE EXTENSION IF NOT EXISTS pg_aegis';
    RAISE NOTICE '[aegis-demo] pg_aegis available - real AEGIS-128L benchmark mode';
  EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE '[aegis-demo] pg_aegis NOT available - falling back to pgcrypto (honest demo)';
  END;
END$$;

-- Plain baseline
CREATE TABLE IF NOT EXISTS player_pii_plain (
  id      bigserial PRIMARY KEY,
  name    text NOT NULL,
  doc_id  text NOT NULL,
  email   text NOT NULL,
  created timestamptz NOT NULL DEFAULT now()
);

-- pgcrypto column-encrypted
CREATE TABLE IF NOT EXISTS player_pii_pgcrypto (
  id      bigserial PRIMARY KEY,
  name    text NOT NULL,
  doc_id  bytea NOT NULL,                                 -- pgp_sym_encrypt
  email   bytea NOT NULL,
  created timestamptz NOT NULL DEFAULT now()
);

-- "Aegis" — either real pg_aegis or pgcrypto fallback (same shape).
CREATE TABLE IF NOT EXISTS player_pii_aegis (
  id      bigserial PRIMARY KEY,
  name    text NOT NULL,
  doc_id  bytea NOT NULL,
  email   bytea NOT NULL,
  nonce   bytea NOT NULL,                                 -- 24B per-row nonce
  created timestamptz NOT NULL DEFAULT now()
);

-- Helper functions with a single interface; route to pg_aegis when present,
-- otherwise to pgcrypto. Readers learn the API; honest about what it does.

CREATE OR REPLACE FUNCTION aegis_demo_encrypt(plaintext text, key_hex text, nonce bytea)
RETURNS bytea LANGUAGE plpgsql AS $$
DECLARE
  result bytea;
BEGIN
  BEGIN
    -- Real AEAD path; only exists when pg_aegis extension is installed.
    EXECUTE 'SELECT aegis_encrypt($1::bytea, decode($2, $3), $4)'
      INTO result
      USING plaintext::bytea, key_hex, 'hex', nonce;
    RETURN result;
  EXCEPTION WHEN undefined_function THEN
    -- Fallback: pgcrypto pgp_sym_encrypt. Not AEAD, but lets the demo run.
    RETURN pgp_sym_encrypt(plaintext, key_hex);
  END;
END$$;

CREATE OR REPLACE FUNCTION aegis_demo_decrypt(ciphertext bytea, key_hex text, nonce bytea)
RETURNS text LANGUAGE plpgsql AS $$
DECLARE
  result text;
BEGIN
  BEGIN
    EXECUTE 'SELECT convert_from(aegis_decrypt($1, decode($2, $3), $4), $5)'
      INTO result
      USING ciphertext, key_hex, 'hex', nonce, 'UTF8';
    RETURN result;
  EXCEPTION WHEN undefined_function THEN
    RETURN pgp_sym_decrypt(ciphertext, key_hex);
  END;
END$$;

-- Identify which path the demo is on.
CREATE OR REPLACE VIEW aegis_demo_status AS
SELECT
  current_setting('server_version', false)                                    AS pg_version,
  (SELECT bool_or(extname = 'pg_aegis') FROM pg_extension)                    AS pg_aegis_loaded,
  (SELECT bool_or(extname = 'pgcrypto') FROM pg_extension)                    AS pgcrypto_loaded,
  '2026-04-22'::date                                                          AS demo_schema_version;
