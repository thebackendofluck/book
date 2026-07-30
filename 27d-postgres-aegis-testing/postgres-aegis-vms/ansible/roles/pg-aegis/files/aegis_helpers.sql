-- aegis_helpers.sql — thin wrappers so book test scripts keep one API.
--
-- Real API (pg_aegis 0.1.0):
--   aegis_encrypt(text, key_name text) -> bytea
--   aegis_decrypt(bytea, key_name text) -> text
--   aegis_generate_key(key_name text) -> bool
--   aegis_version() -> text
--   pg_aegis_keys(key_name, key_version, encrypted_key, rotated_at) table
--
-- Wire format: [1B alg][4B key_version BE][16B nonce][ciphertext][16B tag]
-- Overhead: 37 bytes per cell.
--
-- IMPORTANT: Use LANGUAGE sql (not plpgsql) for these wrappers.
-- PL/pgSQL EXCEPTION clauses create subtransactions that require assigning
-- TransactionIDs — forbidden on hot-standby replicas. SQL-language functions
-- run without subtransactions and work correctly on replicas.

-- 3-arg shims: accept (plaintext, hex_key, nonce) for compose-demo script
-- compatibility; hex_key and nonce are accepted but ignored — real pg_aegis
-- manages the nonce internally inside its wire format.
CREATE OR REPLACE FUNCTION aegis_demo_encrypt(
    plaintext text,
    key_hex   text,
    nonce     bytea
) RETURNS bytea LANGUAGE sql AS $$
    SELECT aegis_encrypt(plaintext, 'player_pii_key')
$$;

CREATE OR REPLACE FUNCTION aegis_demo_decrypt(
    ciphertext bytea,
    key_hex    text,
    nonce      bytea
) RETURNS text LANGUAGE sql AS $$
    SELECT aegis_decrypt(ciphertext, 'player_pii_key')
$$;

-- Status view — report REAL details when pg_aegis is present.
CREATE OR REPLACE VIEW aegis_demo_status AS
SELECT
  current_setting('server_version', false) AS pg_version,
  (SELECT bool_or(extname = 'pg_aegis') FROM pg_extension) AS pg_aegis_loaded,
  (SELECT bool_or(extname = 'pgcrypto') FROM pg_extension) AS pgcrypto_loaded,
  (SELECT extversion FROM pg_extension WHERE extname = 'pg_aegis') AS aegis_version,
  now() AS checked_at;

-- Tables aligned to the real extension's expected shape.
-- When pg_aegis is on, `doc_id` contains the wire-format bytea
-- (alg + key_version + nonce + ciphertext + tag). No separate `nonce`
-- column is needed — the nonce travels inside the bytea.

CREATE TABLE IF NOT EXISTS player_pii_pgcrypto (
  id      bigserial PRIMARY KEY,
  name    text NOT NULL,
  doc_id  bytea NOT NULL,
  email   bytea NOT NULL,
  created timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS player_pii_aegis (
  id      bigserial PRIMARY KEY,
  name    text NOT NULL,
  doc_id  bytea NOT NULL,                         -- real pg_aegis wire format
  email   bytea NOT NULL,
  created timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS casino_ledger (
  id         bigserial PRIMARY KEY,
  player_id  bigint NOT NULL,
  amount     numeric(14,2) NOT NULL,
  kind       text NOT NULL,
  created    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_casino_ledger_player ON casino_ledger(player_id);

CREATE TABLE IF NOT EXISTS chaos_writes (
  id    bigserial PRIMARY KEY,
  ts    timestamptz NOT NULL DEFAULT now()
);
