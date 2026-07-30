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

# run-all.sh — orchestrator. Runs the non-destructive harnesses in order and
# prints a summary CSV path. Intended to be invoked from the demo Makefile
# (make bench-all) or manually inside the pgbench-runner container.

# shellcheck disable=SC1091
. "$(dirname "$0")/_lib.sh"

HERE="$(dirname "$0")"

log "=== postgres-aegis harness ==="
log "results dir: $RESULTS_DIR"

sh "$HERE/T01_baseline_oltp.sh" || log "T01 failed"
sh "$HERE/T02_tls_overhead.sh"  || log "T02 failed"
sh "$HERE/T06_aegis_vs_pgcrypto.sh" || log "T06 failed"
sh "$HERE/T10_backfill_100k.sh" || log "T10 failed"

log "=== summary ==="
cat "$RESULTS_DIR/summary.csv"
