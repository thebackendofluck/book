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

# Read-side queries used by the HSM Security dashboard panel. Safe to run
# repeatedly; touches Redis only with GET / ZRANGE / TTL (no writes).
#
# Default target is the ha-redis container on ops-host (127.0.0.1:6382),
# which is where the production collector at /opt/hsm-collector.py writes
# the `hsm:health` hot key and the `hsm:health_history` sorted set.

set -euo pipefail

: "${REDIS_HOST:=127.0.0.1}"
: "${REDIS_PORT:=6382}"

rc() {
  redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" "$@"
}

jq_or_cat() {
  if command -v jq >/dev/null 2>&1; then jq .; else cat; fi
}

echo "=== hsm:health (hot key, TTL-bounded liveness) ==="
status=$(rc GET hsm:health)
ttl=$(rc TTL hsm:health)
if [[ -z "$status" ]]; then
  echo "(missing -- collector may be down; TTL=$ttl)"
else
  printf '%s\n' "$status" | jq_or_cat
  echo "TTL remaining: $ttl seconds"
fi

echo
echo "=== hsm:health_history ring-buffer size ==="
rc ZCARD hsm:health_history

echo
echo "=== last 10 history entries (newest first) ==="
rc ZREVRANGE hsm:health_history 0 9 | while IFS= read -r line; do
  printf '%s\n' "$line" | jq_or_cat 2>/dev/null || printf '%s\n' "$line"
done

echo
echo "=== availability over the last hour ==="
now_ms=$(( $(date +%s%3N) ))
one_hour_ago=$(( now_ms - 3600 * 1000 ))
rc ZRANGEBYSCORE hsm:health_history "$one_hour_ago" "$now_ms" \
  | python3 -c '
import json, sys
healthy = 0
degraded = 0
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        obj = json.loads(line)
    except ValueError:
        continue
    if obj.get("status") == "healthy":
        healthy += 1
    else:
        degraded += 1
total = healthy + degraded
rate = (healthy / total * 100) if total else 0.0
print(f"healthy={healthy} degraded={degraded} availability={rate:.2f}%")
'
