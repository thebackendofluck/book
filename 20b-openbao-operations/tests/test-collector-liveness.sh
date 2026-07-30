#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 20b, OpenBao Operations.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# Start the collector in the foreground, let it write once, kill it, and
# verify that after the hot-key TTL expires the liveness check correctly
# reports "missing". This is the unit test for the TTL-based liveness design.
#
# Requires a Redis instance reachable via REDIS_HOST:REDIS_PORT (defaults to
# 127.0.0.1:6382). Set up a throw-away container if you do not have one.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="$SCRIPT_DIR/.."

: "${REDIS_HOST:=127.0.0.1}"
: "${REDIS_PORT:=6382}"
: "${COLLECTOR_INTERVAL:=2}"
export REDIS_HOST REDIS_PORT COLLECTOR_INTERVAL

# Force a short TTL for the test
HOT_KEY_TTL=$(( COLLECTOR_INTERVAL * 4 ))

log() { printf '[test] %s\n' "$*" >&2; }

rc() {
  redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" "$@"
}

# sanity: redis reachable?
rc ping >/dev/null || { echo "redis not reachable at $REDIS_HOST:$REDIS_PORT"; exit 2; }

# Use a dedicated test key namespace so we never touch the production
# hsm:health / hsm:health_history keys on ha-redis.
TEST_KEY="hsm:health"
TEST_HISTORY="hsm:health_history"

# Clean slate -- only in a test Redis, refuse otherwise
if [[ "$REDIS_HOST" != "127.0.0.1" && "$REDIS_HOST" != "localhost" ]]; then
  log "FAIL: refusing to run against non-local Redis ($REDIS_HOST)"
  exit 2
fi
rc DEL "$TEST_KEY" "$TEST_HISTORY" >/dev/null

log "starting collector with INTERVAL=$COLLECTOR_INTERVAL"
python3 "$BASE/ops/hsm-collector.py" &
COLLECTOR_PID=$!
trap 'kill "$COLLECTOR_PID" 2>/dev/null || true' EXIT

# Wait for first write (generous upper bound)
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if rc EXISTS "$TEST_KEY" | grep -q 1; then
    break
  fi
  sleep 1
done

rc EXISTS "$TEST_KEY" | grep -q 1 || { log "collector never wrote $TEST_KEY"; exit 3; }
log "collector wrote $TEST_KEY, ttl=$(rc TTL "$TEST_KEY")"

log "killing collector"
kill "$COLLECTOR_PID"
wait "$COLLECTOR_PID" 2>/dev/null || true

log "waiting $((HOT_KEY_TTL + 1))s for the hot-key TTL to expire"
sleep "$((HOT_KEY_TTL + 1))"

if rc EXISTS "$TEST_KEY" | grep -q 0; then
  log "PASS: $TEST_KEY expired after collector death -- liveness detection works"
  exit 0
fi

log "FAIL: $TEST_KEY still present after TTL; check COLLECTOR_INTERVAL / HOT_KEY_TTL"
exit 1
