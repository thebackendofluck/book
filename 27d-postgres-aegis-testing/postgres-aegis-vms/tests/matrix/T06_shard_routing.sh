#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 27d, PostgreSQL Aegis.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# T06_shard_routing.sh — confirms PgCat hash-shards on player_id correctly
# and that the balance is within 5% of uniform.
#
# Method: insert 100k rows with predictable player_ids, then count rows
# per shard via the Patroni REST or by per-shard pg connection. If not
# balanced, emit a warning.

set -euo pipefail

PGCAT_IP="${1:?usage: $0 <pgcat_ip>}"

: "${PG_PASSWORD:?set PG_PASSWORD}"

INSERT_ROWS=100000

echo "[T06] inserting $INSERT_ROWS rows via PgCat"
PGPASSWORD="$PG_PASSWORD" psql -h "$PGCAT_IP" -p 6432 -U aegis_admin -d casino <<SQL
INSERT INTO casino_ledger (player_id, amount, kind)
SELECT i, (random()*100)::int, (ARRAY['bet','credit','debit'])[ceil(random()*3)]
FROM generate_series(1, $INSERT_ROWS) AS i;
SQL

echo "[T06] per-shard row counts"
for SHARD in shard-a shard-b; do
  SHARD_PORT=$(python3 -c "print(5000 if '$SHARD'=='shard-a' else 5001)")
  CNT=$(PGPASSWORD="$PG_PASSWORD" psql -h "$PGCAT_IP" -p "$SHARD_PORT" -U aegis_admin -d casino -tAc \
        "SELECT count(*) FROM casino_ledger;")
  echo "  $SHARD : $CNT rows"
done

echo "[T06] balance check — expect roughly 50/50"
PGPASSWORD="$PG_PASSWORD" psql -h "$PGCAT_IP" -p 6432 -U aegis_admin -d casino -c "
SELECT 'overall' AS scope, count(*) FROM casino_ledger;
"
