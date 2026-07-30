#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 23, DevSecOps for iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

set -euo pipefail

OUT_DIR="${OUT_DIR:-out/pg-slow}"
STAMP="$(date -u '+%Y%m%dT%H%M%SZ')"
mkdir -p "$OUT_DIR"

export PGOPTIONS="${PGOPTIONS:-"-c statement_timeout=5000 -c default_transaction_read_only=on"}"

psql -v ON_ERROR_STOP=1 -c "
SELECT round(mean_exec_time::numeric, 2) AS mean_ms,
       calls,
       left(regexp_replace(query, '\\s+', ' ', 'g'), 200) AS query
FROM pg_stat_statements
ORDER BY mean_exec_time DESC
LIMIT 20;
" > "${OUT_DIR}/top-mean-${STAMP}.txt"

psql -v ON_ERROR_STOP=1 -c "
SELECT round(total_exec_time::numeric, 2) AS total_ms,
       calls,
       left(regexp_replace(query, '\\s+', ' ', 'g'), 200) AS query
FROM pg_stat_statements
ORDER BY total_exec_time DESC
LIMIT 20;
" > "${OUT_DIR}/top-total-${STAMP}.txt"

psql -v ON_ERROR_STOP=1 -c "
SELECT pid,
       usename,
       now() - query_start AS age,
       left(regexp_replace(query, '\\s+', ' ', 'g'), 200) AS query
FROM pg_stat_activity
WHERE state <> 'idle'
  AND query_start IS NOT NULL
  AND now() - query_start > interval '5 seconds'
ORDER BY age DESC;
" > "${OUT_DIR}/long-running-${STAMP}.txt"
