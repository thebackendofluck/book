-- 20-seed.sql
-- Seeds 10k rows of synthetic PII into all three tables.
-- The larger 100k/1M seed is left to the reader via tests/T10_backfill.

\timing on

INSERT INTO player_pii_plain (name, doc_id, email)
SELECT
  'player_' || i,
  lpad((i * 7919)::text, 11, '0'),
  'player_' || i || '@example.test'
FROM generate_series(1, 10000) AS i;

INSERT INTO player_pii_pgcrypto (name, doc_id, email)
SELECT
  'player_' || i,
  pgp_sym_encrypt(lpad((i * 7919)::text, 11, '0'), '00112233445566778899aabbccddeeff'),
  pgp_sym_encrypt('player_' || i || '@example.test',  '00112233445566778899aabbccddeeff')
FROM generate_series(1, 10000) AS i;

INSERT INTO player_pii_aegis (name, doc_id, email, nonce)
SELECT
  'player_' || i,
  aegis_demo_encrypt(lpad((i * 7919)::text, 11, '0'),    '00112233445566778899aabbccddeeff', gen_random_bytes(24)),
  aegis_demo_encrypt('player_' || i || '@example.test', '00112233445566778899aabbccddeeff', gen_random_bytes(24)),
  gen_random_bytes(24)
FROM generate_series(1, 10000) AS i;

ANALYZE;

SELECT * FROM aegis_demo_status;
