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

# T13 — online resize of an encrypted PGDATA disk, including the end-to-end
# invariant check that data is still readable + decryptable after the grow.

set -euo pipefail

TARGET="${1:-lab-server}"
HOST="${HOST:-pg-shard-a-reader-3}"
NEW_GB="${NEW_GB:-150}"

HERE="$(cd "$(dirname "$0")/.." && pwd)"
INVENTORY="$HERE/inventory/${TARGET}.yml"
RESULTS="$HERE/tests/results"
mkdir -p "$RESULTS"
OUT="$RESULTS/T13-$(date -u '+%Y%m%dT%H%M%SZ').log"

log() { printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" | tee -a "$OUT"; }

IP=$(python3 - "$INVENTORY" "$HOST" <<'PY'
import sys, yaml
inv = yaml.safe_load(open(sys.argv[1]))
for grp in inv['all']['children'].values():
    hosts = (grp or {}).get('hosts') or {}
    if sys.argv[2] in hosts:
        print(hosts[sys.argv[2]]['ansible_host'])
        break
PY
)
[ -n "$IP" ] || { log "host $HOST not in inventory"; exit 2; }

runssh() { ssh -o BatchMode=yes "ansible@$IP" "$@"; }

log "=== T13 online resize test ==="
log "host=$HOST ip=$IP new_size_gb=$NEW_GB target=$TARGET"

# --- 1. Capture a known-good encrypted row BEFORE resize ---
KNOWN_ID=$(runssh "sudo -u postgres psql -d casino -tAc \"
INSERT INTO player_pii_aegis (name, doc_id, email, nonce)
VALUES ('T13-canary', aegis_demo_encrypt('canary-doc','00112233445566778899aabbccddeeff', gen_random_bytes(24)),
       aegis_demo_encrypt('canary@example.test','00112233445566778899aabbccddeeff', gen_random_bytes(24)),
       gen_random_bytes(24))
RETURNING id;\"")
log "canary row id=$KNOWN_ID"

# --- 2. Write continuous workload in background ---
GEN_LOG=$(mktemp)
(
  for i in $(seq 1 3000); do
    runssh "sudo -u postgres psql -d casino -c \"
      INSERT INTO player_pii_aegis (name, doc_id, email, nonce)
      VALUES ('load-$i', aegis_demo_encrypt('d-$i','00112233445566778899aabbccddeeff', gen_random_bytes(24)),
             aegis_demo_encrypt('e-$i','00112233445566778899aabbccddeeff', gen_random_bytes(24)),
             gen_random_bytes(24));\" >/dev/null 2>&1" \
      && echo "OK" || echo "FAIL"
  done
) >"$GEN_LOG" &
GEN_PID=$!
trap 'kill $GEN_PID 2>/dev/null || true' EXIT
sleep 3

# --- 3. Perform the online resize ---
log "triggering resize via scripts/resize-encrypted-disk.sh"
t0=$(date +%s)
TARGET="$TARGET" bash "$HERE/scripts/resize-encrypted-disk.sh" "$HOST" "$NEW_GB"
t1=$(date +%s)
log "resize took $((t1 - t0)) s"

# --- 4. Stop writes + collect availability stats ---
sleep 2
kill "$GEN_PID" 2>/dev/null || true
wait "$GEN_PID" 2>/dev/null || true
OK=$(grep -c OK "$GEN_LOG" || true)
FAIL=$(grep -c FAIL "$GEN_LOG" || true)
log "writes during resize: OK=$OK FAIL=$FAIL"
rm -f "$GEN_LOG"

# --- 5. Verify canary row still decrypts correctly ---
DECRYPTED=$(runssh "sudo -u postgres psql -d casino -tAc \"
SELECT convert_from(aegis_demo_decrypt(doc_id, '00112233445566778899aabbccddeeff', nonce), 'UTF8')
FROM player_pii_aegis WHERE id=$KNOWN_ID;\"")
if [ "$DECRYPTED" = "canary-doc" ]; then
  log "canary decrypted OK — data integrity preserved across resize"
  RC=0
else
  log "FAIL: canary decrypt returned '$DECRYPTED'"
  RC=1
fi

# --- 6. Confirm new capacity visible to PG ---
NEW_SIZE=$(runssh "sudo -u postgres psql -d casino -tAc \"SELECT pg_size_pretty(pg_tablespace_size('pg_default'))\"")
log "post-resize pg_tablespace size: $NEW_SIZE"

# --- 7. Emit CSV row ---
cat >>"$RESULTS/T13.csv" <<EOF
$(date -u '+%Y-%m-%dT%H:%M:%SZ'),$TARGET,$HOST,resize_seconds,$((t1 - t0))
$(date -u '+%Y-%m-%dT%H:%M:%SZ'),$TARGET,$HOST,writes_ok,$OK
$(date -u '+%Y-%m-%dT%H:%M:%SZ'),$TARGET,$HOST,writes_fail,$FAIL
$(date -u '+%Y-%m-%dT%H:%M:%SZ'),$TARGET,$HOST,canary_ok,$([ $RC -eq 0 ] && echo 1 || echo 0)
EOF

log "=== T13 done (rc=$RC) ==="
exit "$RC"
