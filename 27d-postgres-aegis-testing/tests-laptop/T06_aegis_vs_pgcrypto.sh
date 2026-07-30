#!/bin/sh
# Companion code for "The Backend of Luck" - Chapter 27d, PostgreSQL Aegis.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# T06 — pg_aegis column-level AEAD vs pgcrypto pgp_sym_encrypt.
#
# Measures: INSERT throughput on two tables (pgcrypto vs aegis/fallback) and
# SELECT+decrypt throughput. Honest fallback: when pg_aegis isn't loaded the
# "aegis" table still runs, but via pgcrypto under the hood — so both numbers
# will match. Readers see the mechanism even if they can't see the speedup.

# shellcheck disable=SC1091
. "$(dirname "$0")/_lib.sh"

SCENARIO="T06_aegis_vs_pgcrypto"
ROWS="${ROWS:-50000}"

wait_ready

AEGIS_LOADED=$(psql_q "SELECT pg_aegis_loaded FROM aegis_demo_status;")
log "pg_aegis_loaded=$AEGIS_LOADED"
record "$SCENARIO" "pg_aegis_loaded" "$AEGIS_LOADED"

log "truncating target tables"
psql -c "TRUNCATE player_pii_pgcrypto, player_pii_aegis RESTART IDENTITY;" >/dev/null

time_ms() {
  # Emits elapsed milliseconds for a statement, via psql --set and EXTRACT.
  # Uses a heredoc with 'SQL' quoted so the outer shell never touches the SQL.
  _n=$1
  _stmt=$2
  psql -v ON_ERROR_STOP=1 -tAq -v n="$_n" -v stmt="$_stmt" <<'SQL' | tr -d '[:space:]'
SELECT EXTRACT(MILLISECONDS FROM elapsed)::int::text AS ms
FROM (
  SELECT clock_timestamp() AS t0,
         (SELECT clock_timestamp() FROM generate_series(1, 1) LIMIT 1) AS t1
) s,
LATERAL (
  SELECT clock_timestamp() - s.t0 AS elapsed
) e;
SQL
}

KEY_HEX="00112233445566778899aabbccddeeff"

sql_insert_pgcrypto() {
cat <<SQL
DO \$block\$
DECLARE t0 timestamptz := clock_timestamp();
BEGIN
  INSERT INTO player_pii_pgcrypto (name, doc_id, email)
  SELECT 'p_' || i,
         pgp_sym_encrypt(lpad((i * 7919)::text, 11, '0'), '${KEY_HEX}'),
         pgp_sym_encrypt('p_' || i || '@example.test',   '${KEY_HEX}')
  FROM generate_series(1, ${ROWS}) AS i;
  RAISE NOTICE 'ELAPSED_MS: %', EXTRACT(MILLISECONDS FROM clock_timestamp() - t0)::bigint;
END
\$block\$;
SQL
}

sql_insert_aegis() {
cat <<SQL
DO \$block\$
DECLARE t0 timestamptz := clock_timestamp();
BEGIN
  INSERT INTO player_pii_aegis (name, doc_id, email, nonce)
  SELECT 'p_' || i,
         aegis_demo_encrypt(lpad((i * 7919)::text, 11, '0'), '${KEY_HEX}', gen_random_bytes(24)),
         aegis_demo_encrypt('p_' || i || '@example.test',   '${KEY_HEX}', gen_random_bytes(24)),
         gen_random_bytes(24)
  FROM generate_series(1, ${ROWS}) AS i;
  RAISE NOTICE 'ELAPSED_MS: %', EXTRACT(MILLISECONDS FROM clock_timestamp() - t0)::bigint;
END
\$block\$;
SQL
}

sql_select_pgcrypto() {
cat <<SQL
DO \$block\$
DECLARE t0 timestamptz := clock_timestamp();
BEGIN
  PERFORM pgp_sym_decrypt(doc_id, '${KEY_HEX}')
  FROM player_pii_pgcrypto
  WHERE id % 10 = 0;
  RAISE NOTICE 'ELAPSED_MS: %', EXTRACT(MILLISECONDS FROM clock_timestamp() - t0)::bigint;
END
\$block\$;
SQL
}

sql_select_aegis() {
cat <<SQL
DO \$block\$
DECLARE t0 timestamptz := clock_timestamp();
BEGIN
  PERFORM aegis_demo_decrypt(doc_id, '${KEY_HEX}', nonce)
  FROM player_pii_aegis
  WHERE id % 10 = 0;
  RAISE NOTICE 'ELAPSED_MS: %', EXTRACT(MILLISECONDS FROM clock_timestamp() - t0)::bigint;
END
\$block\$;
SQL
}

measure_insert_pgcrypto() { sql_insert_pgcrypto | psql -v ON_ERROR_STOP=1 2>&1 | awk '/ELAPSED_MS:/ {print $NF}'; }
measure_insert_aegis()    { sql_insert_aegis    | psql -v ON_ERROR_STOP=1 2>&1 | awk '/ELAPSED_MS:/ {print $NF}'; }
measure_select_pgcrypto() { sql_select_pgcrypto | psql -v ON_ERROR_STOP=1 2>&1 | awk '/ELAPSED_MS:/ {print $NF}'; }
measure_select_aegis()    { sql_select_aegis    | psql -v ON_ERROR_STOP=1 2>&1 | awk '/ELAPSED_MS:/ {print $NF}'; }

log "INSERT $ROWS rows into player_pii_pgcrypto"
PGCRYPTO_INSERT_MS=$(measure_insert_pgcrypto | head -1)
record "$SCENARIO" "pgcrypto_insert_ms" "${PGCRYPTO_INSERT_MS:-0}"

log "INSERT $ROWS rows into player_pii_aegis"
AEGIS_INSERT_MS=$(measure_insert_aegis | head -1)
record "$SCENARIO" "aegis_insert_ms" "${AEGIS_INSERT_MS:-0}"

log "SELECT+decrypt sample from pgcrypto"
PGCRYPTO_SELECT_MS=$(measure_select_pgcrypto | head -1)
record "$SCENARIO" "pgcrypto_select_ms" "${PGCRYPTO_SELECT_MS:-0}"

log "SELECT+decrypt sample from aegis"
AEGIS_SELECT_MS=$(measure_select_aegis | head -1)
record "$SCENARIO" "aegis_select_ms" "${AEGIS_SELECT_MS:-0}"

P_SIZE=$(psql_q "SELECT pg_total_relation_size('player_pii_pgcrypto');")
A_SIZE=$(psql_q "SELECT pg_total_relation_size('player_pii_aegis');")
record "$SCENARIO" "pgcrypto_bytes" "$P_SIZE"
record "$SCENARIO" "aegis_bytes"    "$A_SIZE"

printf '\n=== T06 summary ===\n'
printf 'pg_aegis_loaded      : %s\n'  "$AEGIS_LOADED"
printf 'pgcrypto insert (ms) : %s\n'  "${PGCRYPTO_INSERT_MS:-?}"
printf 'aegis    insert (ms) : %s\n'  "${AEGIS_INSERT_MS:-?}"
printf 'pgcrypto select (ms) : %s\n'  "${PGCRYPTO_SELECT_MS:-?}"
printf 'aegis    select (ms) : %s\n'  "${AEGIS_SELECT_MS:-?}"
printf 'pgcrypto bytes        : %s\n' "${P_SIZE:-?}"
printf 'aegis    bytes        : %s\n' "${A_SIZE:-?}"
