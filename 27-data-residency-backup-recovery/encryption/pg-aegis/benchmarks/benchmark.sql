-- benchmark.sql — compare pgcrypto AES-256-CBC vs pg_aegis AEGIS-128L
-- Run with:  psql -d postgres -f benchmark.sql
-- Requirements: pgcrypto + pg_aegis extensions; master key GUC set.

\timing on
\set ROWS 100000
\set PGP_PASS 'correct-horse-battery-staple'
\set AEGIS_KEY 'bench_key'

-- Ensure extensions and bootstrap.
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_aegis;
SELECT aegis_generate_key(:'AEGIS_KEY');

-- Generate source plaintexts (32–256 byte range to simulate PII fields).
DROP TABLE IF EXISTS bench_src;
CREATE UNLOGGED TABLE bench_src AS
SELECT i AS id,
       repeat(md5(i::text), 1 + (i % 8)) AS payload
FROM generate_series(1, :ROWS) i;
ANALYZE bench_src;

SELECT avg(length(payload))::int AS avg_plain_len FROM bench_src;

-- ----- INSERT benchmark: pgcrypto -----
DROP TABLE IF EXISTS bench_pgp;
CREATE UNLOGGED TABLE bench_pgp (id INT PRIMARY KEY, v BYTEA);

\echo '=== pgcrypto pgp_sym_encrypt INSERT ==='
INSERT INTO bench_pgp(id, v)
SELECT id, pgp_sym_encrypt(payload, :'PGP_PASS') FROM bench_src;

-- ----- INSERT benchmark: pg_aegis -----
DROP TABLE IF EXISTS bench_aegis;
CREATE UNLOGGED TABLE bench_aegis (id INT PRIMARY KEY, v BYTEA);

\echo '=== pg_aegis aegis_encrypt INSERT ==='
INSERT INTO bench_aegis(id, v)
SELECT id, aegis_encrypt(payload, :'AEGIS_KEY') FROM bench_src;

-- ----- SELECT (decrypt) benchmark: pgcrypto -----
\echo '=== pgcrypto pgp_sym_decrypt SELECT ==='
SELECT count(*), avg(length(pt))::int AS avg_len FROM (
    SELECT pgp_sym_decrypt(v, :'PGP_PASS') AS pt FROM bench_pgp
) s;

-- ----- SELECT (decrypt) benchmark: pg_aegis -----
\echo '=== pg_aegis aegis_decrypt SELECT ==='
SELECT count(*), avg(length(pt))::int AS avg_len FROM (
    SELECT aegis_decrypt(v, :'AEGIS_KEY') AS pt FROM bench_aegis
) s;

-- ----- Storage overhead -----
\echo '=== Storage overhead ==='
SELECT 'pgcrypto'  AS impl,
       pg_size_pretty(pg_total_relation_size('bench_pgp'))   AS tbl_size,
       avg(length(v))::int AS avg_ct_bytes
FROM bench_pgp
UNION ALL
SELECT 'pg_aegis',
       pg_size_pretty(pg_total_relation_size('bench_aegis')),
       avg(length(v))::int
FROM bench_aegis;

-- Cleanup (comment out to keep tables).
-- DROP TABLE bench_src, bench_pgp, bench_aegis;
