#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 35b, Cash-Flow Integrity Incident Response.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# Cash-flow incident snapshot: preserves DB rows, redis keys, recent logs, RNG seed audit.
# Usage: snapshot_state.sh --window-minutes 60 --output /var/forensics/incident-<ts>
set -euo pipefail

WINDOW_MINUTES=60
OUTPUT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --window-minutes) WINDOW_MINUTES="$2"; shift 2;;
    --output) OUTPUT="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done
[[ -n "$OUTPUT" ]] || { echo "--output required" >&2; exit 2; }

mkdir -p "$OUTPUT"/{db,redis,logs,rng,meta}
TS=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

echo "[$TS] snapshot starting -> $OUTPUT (window: ${WINDOW_MINUTES}m)" | tee "$OUTPUT/meta/start.txt"

# Postgres rows in window — game_rounds, wallet_events, audit_cashflow_violations
PG_USER="${PG_USER:-casino_ro}"
PG_DB="${PG_DB:-casino}"
PG_HOST="${PG_HOST:-127.0.0.1}"
psql -h "$PG_HOST" -U "$PG_USER" -d "$PG_DB" --csv -c "
  SELECT * FROM game_rounds
  WHERE settled_at >= NOW() - INTERVAL '${WINDOW_MINUTES} minutes';" \
  > "$OUTPUT/db/game_rounds.csv" 2> "$OUTPUT/db/game_rounds.err" || true

psql -h "$PG_HOST" -U "$PG_USER" -d "$PG_DB" --csv -c "
  SELECT * FROM wallet_events
  WHERE created_at >= NOW() - INTERVAL '${WINDOW_MINUTES} minutes';" \
  > "$OUTPUT/db/wallet_events.csv" 2> "$OUTPUT/db/wallet_events.err" || true

psql -h "$PG_HOST" -U "$PG_USER" -d "$PG_DB" --csv -c "
  SELECT * FROM audit_cashflow_violations
  WHERE detected_at >= NOW() - INTERVAL '${WINDOW_MINUTES} minutes';" \
  > "$OUTPUT/db/cashflow_violations.csv" 2> "$OUTPUT/db/cashflow_violations.err" || true

# Redis: dump pub/sub channels + recent ledger keys
REDIS_CLI="${REDIS_CLI:-redis-cli}"
$REDIS_CLI INFO replication > "$OUTPUT/redis/replication.txt" 2>&1 || true
$REDIS_CLI PUBSUB CHANNELS '*' > "$OUTPUT/redis/channels.txt" 2>&1 || true
$REDIS_CLI --scan --pattern 'wallet:*' | head -1000 > "$OUTPUT/redis/wallet_keys.txt" 2>&1 || true

# Logs: app + nginx + ingress in window
journalctl --since "${WINDOW_MINUTES} minutes ago" -u 'casino-*' \
  > "$OUTPUT/logs/casino-services.log" 2>&1 || true
journalctl --since "${WINDOW_MINUTES} minutes ago" -u nginx \
  > "$OUTPUT/logs/nginx.log" 2>&1 || true

# RNG seed audit (if HSM available)
if command -v yubihsm-shell >/dev/null; then
  yubihsm-shell --action get-logs --connector http://127.0.0.1:12345 \
    > "$OUTPUT/rng/hsm_audit.log" 2>&1 || true
fi

# Manifest + checksums for chain of custody
(cd "$OUTPUT" && find . -type f -exec sha256sum {} \;) > "$OUTPUT/meta/sha256sums.txt"
echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] snapshot complete; tarball:" | tee -a "$OUTPUT/meta/start.txt"

TARBALL="${OUTPUT}.tar.gz"
tar czf "$TARBALL" -C "$(dirname "$OUTPUT")" "$(basename "$OUTPUT")"
sha256sum "$TARBALL" > "${TARBALL}.sha256"
echo "$TARBALL"
echo "${TARBALL}.sha256"
