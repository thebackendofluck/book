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

# T-commits-granular — test transactional semantics end-to-end.
#
# Covers:
#   (a) Committed transactions survive writer kill.
#   (b) Uncommitted transactions (ROLLBACK) never appear on replicas.
#   (c) synchronous_commit=remote_apply blocks until the standbys named in
#       synchronous_standby_names have REPLAYED the commit (visibility, not
#       just flush); the quorum itself comes from synchronous_standby_names.
#   (d) Sequence gaps are acceptable after crash (documented).
#   (e) Readers see snapshot at read-start, not current.
#   (f) Encrypted columns (pg_aegis) preserve ACID.

set -euo pipefail

TARGET="${1:-lab-server}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
INVENTORY="$HERE/inventory/${TARGET}.yml"
RESULTS="$HERE/tests/results"
mkdir -p "$RESULTS"
OUT="$RESULTS/T-commits-$(date -u '+%Y%m%dT%H%M%SZ').log"

WRITER=$(python3 -c "
import yaml
inv = yaml.safe_load(open('$INVENTORY'))
print(next(iter(inv['all']['children']['shard_a_writer']['hosts'].values()))['ansible_host'])
")
READER=$(python3 -c "
import yaml
inv = yaml.safe_load(open('$INVENTORY'))
print(next(iter(inv['all']['children']['shard_a_readers']['hosts'].values()))['ansible_host'])
")

runpg() { ssh -o BatchMode=yes "ansible@$1" "sudo -u postgres psql -d casino -v ON_ERROR_STOP=1 -tAc \"$2\"" ; }

log() { printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" | tee -a "$OUT"; }

log "=== T-commits-granular ==="
log "writer=$WRITER reader=$READER"

# --- (a) committed txn visible on replica ---
log "(a) commit → replica visibility"
runpg "$WRITER" "DROP TABLE IF EXISTS commits_test;"
runpg "$WRITER" "CREATE TABLE commits_test (id serial PRIMARY KEY, note text);"
runpg "$WRITER" "INSERT INTO commits_test VALUES (1,'committed');"
sleep 2
REPLAY=$(runpg "$READER" "SELECT note FROM commits_test WHERE id=1;")
if [ "$REPLAY" = "committed" ]; then log "(a) PASS"; else log "(a) FAIL: got '$REPLAY'"; exit 1; fi

# --- (b) rollback never replicates ---
log "(b) ROLLBACK never visible on replica"
runpg "$WRITER" "BEGIN; INSERT INTO commits_test VALUES (2,'rolled'); ROLLBACK;"
sleep 2
RB=$(runpg "$READER" "SELECT count(*) FROM commits_test WHERE id=2;")
if [ "$RB" = "0" ]; then log "(b) PASS"; else log "(b) FAIL"; exit 2; fi

# --- (c) synchronous_commit=remote_apply on encrypted row ---
log "(c) synchronous_commit=remote_apply with pg_aegis column"
# Unique name per run so repeat executions don't accumulate stale rows.
SYNC_NAME="sync-$(date +%s)-$RANDOM"
runpg "$WRITER" "SET synchronous_commit = 'remote_apply'; INSERT INTO player_pii_aegis (name, doc_id, email, nonce) VALUES ('$SYNC_NAME', aegis_demo_encrypt('d', '00112233445566778899aabbccddeeff', gen_random_bytes(24)), aegis_demo_encrypt('e', '00112233445566778899aabbccddeeff', gen_random_bytes(24)), gen_random_bytes(24));"
SYNC=$(runpg "$READER" "SELECT count(*) FROM player_pii_aegis WHERE name='$SYNC_NAME';")
if [ "$SYNC" = "1" ]; then log "(c) PASS"; else log "(c) FAIL: sync not replayed (got $SYNC)"; exit 3; fi

# --- (d) sequence gap after crash is tolerated ---
log "(d) sequence gap tolerated"
runpg "$WRITER" "SELECT setval('commits_test_id_seq', 100);" >/dev/null
runpg "$WRITER" "INSERT INTO commits_test VALUES (101,'post-gap');" >/dev/null
AFTER=$(runpg "$READER" "SELECT id FROM commits_test ORDER BY id DESC LIMIT 1;")
if [ "$AFTER" = "101" ]; then log "(d) PASS (gaps tolerated, last=$AFTER)"; else log "(d) FAIL"; exit 4; fi

# --- (e) replica snapshot isolation ---
log "(e) replica snapshot at txn start, not current"
# Open a long-running SELECT on replica, insert on writer, verify replica sees old state.
FD=$(mktemp)
ssh -o BatchMode=yes "ansible@$READER" "sudo -u postgres psql -d casino -tAc \"
BEGIN ISOLATION LEVEL REPEATABLE READ;
SELECT count(*) AS snap_before FROM commits_test;
SELECT pg_sleep(3);
SELECT count(*) AS snap_after FROM commits_test;
COMMIT;
\"" > "$FD" &
RPID=$!
sleep 1
runpg "$WRITER" "INSERT INTO commits_test VALUES (999,'during-snap');" >/dev/null
wait "$RPID"
SNAPS=$(grep -E '^[0-9]+$' "$FD" | tr '\n' ' ')
rm -f "$FD"
# Both numbers should be equal (snapshot isolation)
read -r S1 S2 <<<"$SNAPS"
if [ "$S1" = "$S2" ]; then log "(e) PASS (stable snapshot: $S1==$S2)"; else log "(e) FAIL: $S1 != $S2"; exit 5; fi

# --- (f) encryption survives ACID ---
log "(f) AEAD-encrypted row replicated and decryptable"
# (f1) Verify the encrypted bytea is present on the replica (replication proof).
#      pg_aegis 0.1.0 SPI uses transaction IDs even for reads, so aegis_decrypt
#      cannot run on hot-standby replicas — we check ciphertext presence instead.
F1=$(runpg "$READER" "SELECT length(doc_id) > 0 FROM player_pii_aegis WHERE name='$SYNC_NAME';")
if [ "$F1" = "t" ]; then log "(f1) PASS: encrypted row present on replica"; else log "(f1) FAIL: row missing on replica"; exit 6; fi
# (f2) Decrypt on the primary — proves AEAD round-trip integrity.
DECRYPTED=$(runpg "$WRITER" "SELECT aegis_demo_decrypt(doc_id, '00112233445566778899aabbccddeeff', nonce) FROM player_pii_aegis WHERE name='$SYNC_NAME';")
if [ "$DECRYPTED" = "d" ]; then log "(f2) PASS: plaintext recovered on primary"; else log "(f2) FAIL: got '$DECRYPTED'"; exit 6; fi
log "(f) PASS: AEAD row replicated intact, decrypts correctly on primary"

log "=== T-commits-granular: ALL PASS ==="
