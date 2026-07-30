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

# T05_aegis_vs_pgcrypto_prod.sh — runs inside a writer VM, measures
# INSERT/SELECT/storage for pgcrypto vs pg_aegis on real production-size data
# (1M rows). Honest fallback when pg_aegis isn't loaded.

set -eu

KEY_HEX="${KEY_HEX:-00112233445566778899aabbccddeeff}"
ROWS="${ROWS:-1000000}"

loaded=$(sudo -u postgres psql -tAc -d casino "SELECT bool_or(extname='pg_aegis') FROM pg_extension;")
echo "[T05] pg_aegis_loaded=$loaded rows=$ROWS"

sudo -u postgres psql -d casino -c "
TRUNCATE TABLE player_pii_pgcrypto, player_pii_aegis RESTART IDENTITY;
" >/dev/null

echo "[T05] INSERT pgcrypto..."
T0=$(date +%s)
sudo -u postgres psql -d casino -v ON_ERROR_STOP=1 -c "
INSERT INTO player_pii_pgcrypto (name, doc_id, email)
SELECT 'p_' || i,
       pgp_sym_encrypt(lpad((i * 7919)::text, 11, '0'), '${KEY_HEX}'),
       pgp_sym_encrypt('p_' || i || '@example.test', '${KEY_HEX}')
FROM generate_series(1, ${ROWS}) AS i;" >/dev/null
T1=$(date +%s)
PGCRYPTO_S=$((T1-T0))

echo "[T05] INSERT aegis..."
T0=$(date +%s)
sudo -u postgres psql -d casino -v ON_ERROR_STOP=1 -c "
INSERT INTO player_pii_aegis (name, doc_id, email, nonce)
SELECT 'p_' || i,
       aegis_demo_encrypt(lpad((i * 7919)::text, 11, '0'), '${KEY_HEX}', gen_random_bytes(24)),
       aegis_demo_encrypt('p_' || i || '@example.test', '${KEY_HEX}', gen_random_bytes(24)),
       gen_random_bytes(24)
FROM generate_series(1, ${ROWS}) AS i;" >/dev/null
T1=$(date +%s)
AEGIS_S=$((T1-T0))

PGC_BYTES=$(sudo -u postgres psql -tAc -d casino "SELECT pg_total_relation_size('player_pii_pgcrypto');")
AEG_BYTES=$(sudo -u postgres psql -tAc -d casino "SELECT pg_total_relation_size('player_pii_aegis');")

echo "T05 summary (prod):"
echo "  pg_aegis_loaded : $loaded"
echo "  rows           : $ROWS"
echo "  pgcrypto INSERT: ${PGCRYPTO_S}s"
echo "  aegis    INSERT: ${AEGIS_S}s"
echo "  pgcrypto bytes : $PGC_BYTES"
echo "  aegis    bytes : $AEG_BYTES"
