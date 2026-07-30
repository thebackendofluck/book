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

# _lib.sh — shared helpers for postgres-aegis harness scripts.
# POSIX sh so it runs inside the alpine postgres image without bash.

set -eu

: "${PGHOST:=pg-primary}"
: "${PGPORT:=5432}"
: "${PGUSER:=demo}"
: "${PGPASSWORD:=demo-pw-not-a-secret}"
: "${PGDATABASE:=casino}"

export PGHOST PGPORT PGUSER PGPASSWORD PGDATABASE

RESULTS_DIR="${RESULTS_DIR:-/results}"
mkdir -p "$RESULTS_DIR"

ts() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }

log() { printf '[%s] %s\n' "$(ts)" "$*" >&2; }

record() {
  # Appends a single line (scenario,metric,value) to the global results CSV.
  mkdir -p "$RESULTS_DIR"
  if [ ! -f "$RESULTS_DIR/summary.csv" ]; then
    printf 'timestamp,scenario,metric,value\n' > "$RESULTS_DIR/summary.csv"
  fi
  printf '%s,%s,%s,%s\n' "$(ts)" "$1" "$2" "$3" >> "$RESULTS_DIR/summary.csv"
}

psql_q() {
  # Single-value psql query, no headers, no newlines around it.
  psql -tAq -c "$1"
}

wait_ready() {
  i=0
  while [ "$i" -lt 30 ]; do
    if pg_isready -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" >/dev/null 2>&1; then
      return 0
    fi
    i=$((i + 1))
    sleep 2
  done
  log "database never became ready"
  return 1
}
