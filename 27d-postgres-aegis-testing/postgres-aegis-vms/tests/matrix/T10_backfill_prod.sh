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

# T10_backfill_prod.sh — idempotent blue-green backfill on a production-scale
# table (up to 1M rows by default). Runs 8 parallel workers from the writer VM.

set -eu

WRITER="${1:?usage: $0 <writer_ip>}"
ROWS="${ROWS:-1000000}"
WORKERS="${WORKERS:-8}"
CHUNK=$((ROWS / WORKERS))
KEY="${KEY:-00112233445566778899aabbccddeeff}"

runssh() { ssh -o BatchMode=yes "ansible@$WRITER" "$@"; }

echo "[T10] seeding $ROWS rows"
runssh "sudo -u postgres psql -d casino -v ON_ERROR_STOP=1 -c \"
  TRUNCATE player_pii_pgcrypto RESTART IDENTITY;
  INSERT INTO player_pii_pgcrypto (name, doc_id, email)
  SELECT 'bf_' || i,
         pgp_sym_encrypt(lpad(i::text, 11, '0'), '${KEY}'),
         pgp_sym_encrypt('bf_' || i || '@example.test', '${KEY}')
  FROM generate_series(1, ${ROWS}) AS i;
  ALTER TABLE player_pii_pgcrypto
    ADD COLUMN IF NOT EXISTS doc_id_aegis bytea,
    ADD COLUMN IF NOT EXISTS email_aegis bytea,
    ADD COLUMN IF NOT EXISTS nonce_aegis bytea;
\""

echo "[T10] launching $WORKERS workers"
T0=$(date +%s)
for i in $(seq 0 $((WORKERS-1))); do
  LO=$((i * CHUNK + 1))
  HI=$(((i + 1) * CHUNK))
  [ "$i" = "$((WORKERS - 1))" ] && HI=$ROWS
  (
    runssh "sudo -u postgres psql -d casino -v ON_ERROR_STOP=1 -c \"
      UPDATE player_pii_pgcrypto
         SET doc_id_aegis = aegis_demo_encrypt(pgp_sym_decrypt(doc_id, '${KEY}'), '${KEY}', gen_random_bytes(24)),
             email_aegis  = aegis_demo_encrypt(pgp_sym_decrypt(email,  '${KEY}'), '${KEY}', gen_random_bytes(24)),
             nonce_aegis  = gen_random_bytes(24)
       WHERE id BETWEEN $LO AND $HI AND doc_id_aegis IS NULL;\""
  ) &
done
wait
T1=$(date +%s)
ELAPSED=$((T1 - T0))
DONE=$(runssh "sudo -u postgres psql -d casino -tAc 'SELECT count(*) FROM player_pii_pgcrypto WHERE doc_id_aegis IS NOT NULL;'")
RPS=$((DONE / (ELAPSED > 0 ? ELAPSED : 1)))

printf '[T10] %s/%s rows in %ss = %s rows/s (%s workers)\n' "$DONE" "$ROWS" "$ELAPSED" "$RPS" "$WORKERS"
