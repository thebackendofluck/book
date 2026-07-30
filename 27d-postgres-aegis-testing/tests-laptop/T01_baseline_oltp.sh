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

# T01 — OLTP baseline on the plain (no-crypto) table.
#
# Hypothesis: establish the physical ceiling in TPS / p99 for this host.
# Every subsequent test subtracts overhead against this.

# shellcheck disable=SC1091
. "$(dirname "$0")/_lib.sh"

SCENARIO="T01_baseline_oltp"
SCALE="${SCALE:-10}"
CLIENTS="${CLIENTS:-32}"
JOBS="${JOBS:-4}"
DURATION="${DURATION:-30}"

log "wait for database"
wait_ready

log "initialising pgbench (scale=$SCALE)"
pgbench -i -q -s "$SCALE" "$PGDATABASE"

log "running: $CLIENTS clients, $JOBS jobs, ${DURATION}s"
OUT="$RESULTS_DIR/${SCENARIO}.txt"
pgbench -c "$CLIENTS" -j "$JOBS" -T "$DURATION" -M prepared \
  --latency-limit=500 -P 5 "$PGDATABASE" | tee "$OUT"

TPS=$(grep 'tps = ' "$OUT" | head -1 | awk '{print $3}')
LAT_AVG=$(grep 'latency average' "$OUT" | awk '{print $4}')
record "$SCENARIO" "tps"                "${TPS:-0}"
record "$SCENARIO" "latency_avg_ms"     "${LAT_AVG:-0}"
record "$SCENARIO" "clients"            "$CLIENTS"
record "$SCENARIO" "duration_s"         "$DURATION"
log "T01 done: ${TPS:-?} TPS"
