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

# T02 — TLS in-transit overhead.
# Re-runs T01 with PGSSLMODE=verify-full. If TLS isn't enabled on the server,
# exits with a skip message (honest) instead of faking a run.

# shellcheck disable=SC1091
. "$(dirname "$0")/_lib.sh"

SCENARIO="T02_tls_overhead"

wait_ready

SSL_USED=$(PGSSLMODE=require psql -tAq -c "SHOW ssl;" 2>/dev/null || echo "off")
case "$SSL_USED" in
  on*)
    log "TLS is on — proceeding"
    ;;
  *)
    log "SKIP: server has ssl=off. Enable SSL in docker-compose (see demo README) to run T02."
    record "$SCENARIO" "status" "skipped_tls_off"
    exit 0
    ;;
esac

SCALE="${SCALE:-10}"
CLIENTS="${CLIENTS:-32}"
JOBS="${JOBS:-4}"
DURATION="${DURATION:-30}"

OUT="$RESULTS_DIR/${SCENARIO}.txt"
PGSSLMODE=verify-full pgbench -c "$CLIENTS" -j "$JOBS" -T "$DURATION" -M prepared \
  -P 5 "$PGDATABASE" | tee "$OUT"

TPS=$(grep 'tps = ' "$OUT" | head -1 | awk '{print $3}')
LAT_AVG=$(grep 'latency average' "$OUT" | awk '{print $4}')
record "$SCENARIO" "tps"            "${TPS:-0}"
record "$SCENARIO" "latency_avg_ms" "${LAT_AVG:-0}"
log "T02 done: ${TPS:-?} TPS"
