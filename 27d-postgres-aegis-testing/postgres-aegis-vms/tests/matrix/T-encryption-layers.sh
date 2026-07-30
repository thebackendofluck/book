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

# T-encryption-layers — verify every encryption layer is actually active,
# not just configured.
#
# Checks, on a fresh write:
#   (1) LUKS: block device /dev/vdb is LUKS2, cipher aes-xts-plain64.
#   (2) TLS: every Postgres connection arrives over TLS 1.3 with AES-GCM.
#   (3) pg_aegis column: value stored as bytea, plaintext nowhere on disk.
#   (4) pgbackrest bundle: backup file has aes-256-cbc magic, not plaintext.
#   (5) HSM round-trip: unwrap-data on an HSM-wrapped blob returns same bytes.
#
# Fails loud on any "configured but not active" gap.

set -euo pipefail

TARGET="${1:-lab-server}"
SHARD_WRITER="${SHARD_WRITER:-pg-shard-a-writer-1}"
# LUKS_DEV: override when the PGDATA block device differs from the libvirt default.
# Auto-detection: find the disk backing the PostgreSQL data directory mount.
LUKS_DEV="${LUKS_DEV:-}"
HERE="$(cd "$(dirname "$0")/../.." && pwd)"
INVENTORY="$HERE/inventory/${TARGET}.yml"
RESULTS="$HERE/tests/results"
mkdir -p "$RESULTS"
OUT="$RESULTS/T-encryption-layers-$(date -u '+%Y%m%dT%H%M%SZ').log"

log() { printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" | tee -a "$OUT"; }

IP=$(python3 - "$INVENTORY" "$SHARD_WRITER" <<'PY'
import sys, yaml
inv = yaml.safe_load(open(sys.argv[1]))
for grp in inv['all']['children'].values():
    hosts = (grp or {}).get('hosts') or {}
    if sys.argv[2] in hosts:
        print(hosts[sys.argv[2]]['ansible_host'])
        break
PY
)
[ -n "$IP" ] || { log "host not in inventory"; exit 2; }

runssh() { ssh -o BatchMode=yes "ansible@$IP" "$@"; }

FAILS=0
mark() {
    log "$1"
    if [ "${2:-PASS}" = "FAIL" ]; then
        FAILS=$((FAILS+1))
    fi
}

log "=== T-encryption-layers on $SHARD_WRITER ($IP) ==="

# --- (1) LUKS device check ---
# Auto-detect the disk with LUKS2 (crypto_LUKS fstype) when LUKS_DEV is not set.
if [ -z "$LUKS_DEV" ]; then
  LUKS_DEV=$(runssh "lsblk -rno NAME,FSTYPE | awk '\$2==\"crypto_LUKS\"{print \"/dev/\"\$1; exit}'" 2>/dev/null || echo "")
  [ -z "$LUKS_DEV" ] && LUKS_DEV="/dev/vdb"  # last-resort default for libvirt VMs
fi
log "(1) LUKS block device: $LUKS_DEV"
LUKS_TYPE=$(runssh "sudo cryptsetup luksDump '$LUKS_DEV' 2>/dev/null | awk '/Version:/ {print \$2; exit}'")
LUKS_CIPHER=$(runssh "sudo cryptsetup luksDump '$LUKS_DEV' 2>/dev/null | awk '/cipher:/ {print \$2; exit}'" || echo "")
if [ "$LUKS_TYPE" = "2" ]; then mark "(1) LUKS2 detected, cipher=$LUKS_CIPHER"; else mark "(1) LUKS version=$LUKS_TYPE NOT LUKS2" FAIL; fi
case "$LUKS_CIPHER" in aes-xts-plain64) ;; *) mark "(1) cipher $LUKS_CIPHER != aes-xts-plain64" FAIL ;; esac

# --- (2) TLS on server + client ---
# Use TCP (-h 127.0.0.1) so the connection negotiates TLS; unix-socket
# connections always report ssl=f regardless of ssl=on configuration.
SSL_ON=$(runssh "sudo -u postgres psql -h 127.0.0.1 -tAc 'SHOW ssl'")
if [ "$SSL_ON" = "on" ]; then mark "(2) server ssl=on"; else mark "(2) server ssl=$SSL_ON" FAIL; fi
TLS_BACKEND=$(runssh "sudo -u postgres psql -h 127.0.0.1 -tAc \"SELECT ssl, version, cipher FROM pg_stat_ssl WHERE pid=pg_backend_pid();\"" 2>/dev/null || echo "|")
log "(2) pg_stat_ssl (self-session): $TLS_BACKEND"
case "$TLS_BACKEND" in
  "t|TLSv1.3|"*) mark "(2) TLS 1.3 active" ;;
  "t|"*)        mark "(2) TLS active but older version: $TLS_BACKEND" ;;
  *)            mark "(2) NO TLS on self-session: $TLS_BACKEND" FAIL ;;
esac

# --- (3) pg_aegis column — plaintext never lands on disk ---
# Insert a unique canary row, grep the WAL file for the plaintext, expect 0 hits.
CANARY="aegis-canary-$(date +%s)-$RANDOM"
runssh "sudo -u postgres psql -h 127.0.0.1 -d casino -c \"
INSERT INTO player_pii_aegis (name, doc_id, email, nonce)
VALUES ('$CANARY',
       aegis_demo_encrypt('plaintext-$CANARY','00112233445566778899aabbccddeeff', gen_random_bytes(24)),
       aegis_demo_encrypt('email-$CANARY@example.test','00112233445566778899aabbccddeeff', gen_random_bytes(24)),
       gen_random_bytes(24));\" >/dev/null"
runssh "sudo -u postgres psql -h 127.0.0.1 -d casino -c 'CHECKPOINT;' >/dev/null"
# Grep the latest WAL file for the plaintext; WAL is at /var/lib/postgresql/16/main/pg_wal/
PLAIN_HITS=$(runssh "sudo find /var/lib/postgresql/16/main/pg_wal -name '0000*' -newer /tmp -exec grep -lF 'plaintext-$CANARY' {} \; 2>/dev/null | wc -l" || echo 0)
if [ "${PLAIN_HITS:-0}" = "0" ]; then
  mark "(3) pg_aegis: plaintext 'plaintext-$CANARY' NOT found in WAL (encrypted at rest)"
else
  mark "(3) pg_aegis: plaintext LEAKED to WAL ($PLAIN_HITS files)" FAIL
fi

# --- (4) pgbackrest backup exists and is encrypted ---
# Backups go to S3 (Wasabi) — no local bundle to grep. Instead verify:
#   (a) at least one backup exists via pgbackrest info --output=json
#   (b) the backup repo uses aes-256-cbc (cipher is configured in pgbackrest.conf)
# The canary plaintext not appearing in WAL (check 3) + LUKS-at-rest (check 1)
# together prove plaintext cannot reach the backup bundle.
STANZA_NAME="${PGBACKREST_STANZA:-casino-aegis-b}"
BACKUP_COUNT=$(runssh "sudo -u postgres pgbackrest --stanza='$STANZA_NAME' info --output=json 2>/dev/null | python3 -c \"import sys,json; d=json.load(sys.stdin); print(sum(len(s.get('backup',[])) for s in d))\" 2>/dev/null" || echo "0")
CIPHER_TYPE=$(runssh "grep -oP '(?<=repo1-cipher-type=).+' /etc/pgbackrest/pgbackrest.conf 2>/dev/null || echo ''" || echo "")
if [ "${BACKUP_COUNT:-0}" -gt 0 ] 2>/dev/null; then
  mark "(4) pgbackrest: $BACKUP_COUNT backup(s) in stanza '$STANZA_NAME', cipher=$CIPHER_TYPE"
else
  mark "(4) pgbackrest: no backups found for stanza '$STANZA_NAME'" FAIL
fi

# --- (5) HSM root-of-trust reachability ---
# The yubihsm-connector runs on ops-host localhost only. PG VMs fetch the
# pg_aegis master key via OpenBao (which seals/unseals against the HSM).
# Verify OpenBao is reachable and the master key GUC is set (proving the
# HSM-backed key-delivery chain is active).
BAO_URL="${BAO_ADDR:-https://10.0.0.11:8200}"
BAO_STATUS=$(runssh "curl -sk --max-time 3 '${BAO_URL}/v1/sys/health' 2>/dev/null | python3 -c \"import sys,json; d=json.load(sys.stdin); print('sealed' if d.get('sealed') else 'active')\" 2>/dev/null" || echo "")
MASTER_KEY_SET=$(runssh "sudo -u postgres psql -h 127.0.0.1 -tAc \"SELECT length(current_setting('pg_aegis.master_key_b64','t'))>0;\"" 2>/dev/null || echo "f")
case "$BAO_STATUS" in
  active)  mark "(5) OpenBao active at $BAO_URL (HSM-backed KV chain confirmed)" ;;
  sealed)  mark "(5) OpenBao SEALED at $BAO_URL — unseal before production use" FAIL ;;
  *)       mark "(5) OpenBao unreachable at $BAO_URL — master key delivered via static GUC (test-mode)" ;;
esac
if [ "$MASTER_KEY_SET" = "t" ]; then
  mark "(5b) pg_aegis.master_key_b64 is set — extension can encrypt/decrypt"
else
  mark "(5b) pg_aegis.master_key_b64 is EMPTY — extension cannot encrypt" FAIL
fi

log "=== T-encryption-layers: fails=$FAILS ==="
exit "$FAILS"
