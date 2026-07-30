#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 23b, DevSecOps Pipeline Implementation.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# Prevent overlapping MV refreshes with flock
exec 200>/tmp/refresh-stats.lock
flock -n 200 || { echo "Already running"; exit 0; }

docker exec new-casino-db psql -U casino_new -d new_acmetocasino -c "
SET statement_timeout = '60s';
REFRESH MATERIALIZED VIEW CONCURRENTLY mv_dashboard_stats;
" > /dev/null 2>&1

docker exec new-casino-db psql -U casino_new -d new_acmetocasino -c "
SET statement_timeout = '60s';
REFRESH MATERIALIZED VIEW CONCURRENTLY mv_player_balances;
" > /dev/null 2>&1
