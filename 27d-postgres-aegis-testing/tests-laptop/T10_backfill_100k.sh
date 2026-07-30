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

# T10 — Back-fill migration sketch.
# Reduced from the article's 100M to 100K rows so the laptop demo finishes
# in under 2 minutes. Same pattern: idempotent UPDATE bounded by id ranges.

# shellcheck disable=SC1091
. "$(dirname "$0")/_lib.sh"

SCENARIO="T10_backfill_100k"
ROWS="${ROWS:-100000}"
WORKERS="${WORKERS:-4}"
KEY="00112233445566778899aabbccddeeff"

wait_ready

log "preparing source + target tables"
psql -c "TRUNCATE player_pii_pgcrypto RESTART IDENTITY;" >/dev/null
psql -c "
  INSERT INTO player_pii_pgcrypto (name, doc_id, email)
  SELECT 'bf_' || i,
         pgp_sym_encrypt(lpad(i::text, 11, '0'), '${KEY}'),
         pgp_sym_encrypt('bf_' || i || '@example.test', '${KEY}')
  FROM generate_series(1, ${ROWS}) AS i;" >/dev/null

# Add nullable aegis columns on the same table to exercise the migration pattern.
psql -c "ALTER TABLE player_pii_pgcrypto
          ADD COLUMN IF NOT EXISTS doc_id_aegis bytea,
          ADD COLUMN IF NOT EXISTS email_aegis  bytea,
          ADD COLUMN IF NOT EXISTS nonce_aegis  bytea;" >/dev/null

CHUNK=$((ROWS / WORKERS))

log "launching $WORKERS workers"
T0=$(date +%s)
pids=""
i=0
while [ "$i" -lt "$WORKERS" ]; do
  LO=$((i * CHUNK + 1))
  HI=$(((i + 1) * CHUNK))
  [ "$i" = $((WORKERS - 1)) ] && HI=$ROWS
  (
    psql -v ON_ERROR_STOP=1 -c "
      UPDATE player_pii_pgcrypto
         SET doc_id_aegis = aegis_demo_encrypt(
                              pgp_sym_decrypt(doc_id, '${KEY}'),
                              '${KEY}', gen_random_bytes(24)),
             email_aegis  = aegis_demo_encrypt(
                              pgp_sym_decrypt(email,  '${KEY}'),
                              '${KEY}', gen_random_bytes(24)),
             nonce_aegis  = gen_random_bytes(24)
       WHERE id BETWEEN $LO AND $HI
         AND doc_id_aegis IS NULL;"
  ) &
  pids="$pids $!"
  i=$((i + 1))
done

for p in $pids; do wait "$p"; done
T1=$(date +%s)
ELAPSED=$((T1 - T0))

DONE=$(psql_q "SELECT count(*) FROM player_pii_pgcrypto WHERE doc_id_aegis IS NOT NULL;")
RPS=$(( DONE / (ELAPSED > 0 ? ELAPSED : 1) ))

record "$SCENARIO" "rows"            "$DONE"
record "$SCENARIO" "elapsed_s"       "$ELAPSED"
record "$SCENARIO" "rows_per_second" "$RPS"
record "$SCENARIO" "workers"         "$WORKERS"

printf '\n=== T10 summary ===\n'
printf 'rows         : %s / %s\n' "$DONE" "$ROWS"
printf 'elapsed      : %s s\n' "$ELAPSED"
printf 'rows/second  : %s\n' "$RPS"
printf 'workers      : %s\n' "$WORKERS"
