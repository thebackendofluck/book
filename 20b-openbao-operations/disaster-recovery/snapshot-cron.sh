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

# snapshot-cron.sh -- production-grade raft snapshot automation for OpenBao.
#
# Runs once per day from a systemd timer at 03:00. Each invocation:
#   1. Takes a raft snapshot via `bao operator raft snapshot save`.
#   2. Encrypts the snapshot at rest using HSM-wrapped envelope encryption
#      (a fresh AES-256 DEK per snapshot, wrapped by transit/encrypt/snapshot-key).
#   3. Uploads the encrypted snapshot to Wasabi object storage, together with a
#      per-snapshot sha256 sidecar and the rolling manifest, so the integrity
#      reference exists remotely and not only on the host being backed up.
#      Wasabi credentials live in OpenBao at `secret/project/wasabi/credentials`.
#   4. Checks that the bucket is versioned and object-locked, and says exactly
#      what to configure if it is not.
#   5. Maintains a 14-day daily / 8-week weekly / 12-month monthly retention,
#      deleting with a separate credential from the one that uploads.
#   6. Logs every step to /var/log/bao-snapshot.log AND syslog (tag bao-snapshot).
#
# Sister script (RESTORE side): scripts/chapter-20b/disaster-recovery/
#   force-single-node-recovery.sh recovers from quorum loss using peers.json.
#   This script is the BACKUP side -- the snapshots it produces are what
#   the restore-from-snapshot-runbook.md procedure consumes.
#
# Exit codes: 0 success, 1 snapshot/encrypt failure, 2 upload failure,
#             3 retention prune failure (snapshot already uploaded so still 0-equivalent for paging)
#             4 snapshot uploaded but the bucket is not immutable and
#               REQUIRE_OBJECT_LOCK=1 was set
#
# Required environment (set in the systemd unit):
#   BAO_ADDR        e.g. https://127.0.0.1:8200
#   BAO_TOKEN_FILE  path to a file holding a token with the `snapshot-taker` policy
#                   (capabilities: read sys/storage/raft/snapshot, update transit/encrypt/snapshot-key,
#                    read secret/project/wasabi/credentials)
#   SNAPSHOT_DIR    local staging dir (default /var/lib/bao-snapshots)
#   WASABI_BUCKET   target bucket name (default: bao-snapshots-<hostname>)
#   WASABI_REGION   default us-east-1
#   WASABI_ENDPOINT default https://s3.wasabisys.com
#
# Optional environment:
#   WASABI_PRUNE_SECRET_PATH  separate credential used ONLY for remote deletion
#                             (default secret/project/wasabi/prune-credentials).
#                             If unreadable, remote pruning is skipped rather
#                             than performed with the upload credential.
#   REQUIRE_OBJECT_LOCK=1     treat a bucket without versioning + object lock as
#                             a failure (exit 4) instead of a warning.
#
#------------------------------------------------------------------------------
# THREAT MODEL -- read this before trusting these backups
#------------------------------------------------------------------------------
# A backup that the backed-up host can delete is not a backup; it is a copy.
# This script runs on the OpenBao host, so whatever it can do, an attacker who
# owns that host can do. Three properties decide whether these snapshots
# survive that:
#
#   1. Separate credentials for write and delete. The upload credential should
#      carry s3:PutObject and nothing that removes data. Deletion is a distinct
#      credential, read from a distinct OpenBao path, used only in
#      prune_remote(). This script previously pruned with the same credential it
#      uploaded with, which meant one compromised host could delete every
#      remote snapshot in a single loop.
#   2. Bucket versioning. Without it, a PutObject to an existing key silently
#      replaces the only copy, so overwriting beats deleting as an attack.
#   3. Object lock with a default retention rule, in COMPLIANCE mode. This is
#      the only one of the three that holds against a credential that has been
#      granted deletion rights by mistake, because in COMPLIANCE mode not even
#      the account root can shorten the retention. check_remote_immutability()
#      below tests for 2 and 3 and prints the exact remediation.
#
# Properties 2 and 3 cannot be configured from here: they are bucket-level
# settings that must be applied once, out of band, by an identity this host does
# not hold. That is the point. If this script could enable object lock it could
# also disable it.

set -euo pipefail

LOG_FILE="/var/log/bao-snapshot.log"
LOG_TAG="bao-snapshot"
SNAPSHOT_DIR="${SNAPSHOT_DIR:-/var/lib/bao-snapshots}"
WASABI_BUCKET="${WASABI_BUCKET:-bao-snapshots-$(hostname -s)}"
WASABI_REGION="${WASABI_REGION:-us-east-1}"
WASABI_ENDPOINT="${WASABI_ENDPOINT:-https://s3.wasabisys.com}"
BAO_ADDR="${BAO_ADDR:-https://127.0.0.1:8200}"
BAO_TOKEN_FILE="${BAO_TOKEN_FILE:-/etc/bao-snapshot/token}"
TRANSIT_KEY="${TRANSIT_KEY:-snapshot-key}"
WASABI_SECRET_PATH="${WASABI_SECRET_PATH:-secret/project/wasabi/credentials}"
WASABI_PRUNE_SECRET_PATH="${WASABI_PRUNE_SECRET_PATH:-secret/project/wasabi/prune-credentials}"
REQUIRE_OBJECT_LOCK="${REQUIRE_OBJECT_LOCK:-0}"
IMMUTABILITY_OK=0

mkdir -p "$SNAPSHOT_DIR"
mkdir -p "$(dirname "$LOG_FILE")"
touch "$LOG_FILE"

log() {
  local level="$1"; shift
  local msg="$*"
  local line
  line=$(printf '%s [%s] [%s] %s' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$level" "$LOG_TAG" "$msg")
  printf '%s\n' "$line" | tee -a "$LOG_FILE"
  if command -v logger >/dev/null 2>&1; then
    logger -t "$LOG_TAG" -p "user.${level}" -- "$msg" || true
  fi
}

require_cmd() {
  for c in "$@"; do
    if ! command -v "$c" >/dev/null 2>&1; then
      log err "missing required command: $c"
      exit 1
    fi
  done
}

require_cmd bao curl python3 sha256sum aws

if [[ ! -r "$BAO_TOKEN_FILE" ]]; then
  log err "BAO_TOKEN_FILE not readable: $BAO_TOKEN_FILE"
  exit 1
fi

export BAO_ADDR
BAO_TOKEN=$(< "$BAO_TOKEN_FILE")
export BAO_TOKEN

now_utc=$(date -u +%Y-%m-%dT%H%MZ)
host_short=$(hostname -s)
snap_basename="snapshot-${host_short}-${now_utc}.snap"
snap_path="${SNAPSHOT_DIR}/${snap_basename}"
enc_path="${snap_path}.enc"
manifest="${SNAPSHOT_DIR}/manifest.sha256"

#------------------------------------------------------------------------------
# 1. Take the snapshot
#------------------------------------------------------------------------------
log info "starting snapshot to ${snap_path}"
if ! bao operator raft snapshot save "$snap_path" >/dev/null 2>&1; then
  log err "snapshot save failed"
  exit 1
fi

snap_size=$(stat -c '%s' "$snap_path" 2>/dev/null || stat -f '%z' "$snap_path")
if [[ "$snap_size" -lt 1024 ]]; then
  log err "snapshot suspiciously small: ${snap_size} bytes -- aborting"
  rm -f "$snap_path"
  exit 1
fi
log info "snapshot taken size=${snap_size} bytes"

#------------------------------------------------------------------------------
# 2. Envelope-encrypt with HSM-wrapped DEK via transit/encrypt
#------------------------------------------------------------------------------
log info "generating HSM-wrapped DEK via transit/datakey/plaintext/${TRANSIT_KEY}"
dk_resp=$(bao write -format=json "transit/datakey/plaintext/${TRANSIT_KEY}" 2>/dev/null) || {
  log err "transit datakey generation failed"
  rm -f "$snap_path"
  exit 1
}

dek_b64=$(printf '%s' "$dk_resp" | python3 -c 'import json,sys;print(json.load(sys.stdin)["data"]["plaintext"])')
wrapped_dek=$(printf '%s' "$dk_resp" | python3 -c 'import json,sys;print(json.load(sys.stdin)["data"]["ciphertext"])')

log info "encrypting snapshot with envelope DEK (HSM-wrapped via ${TRANSIT_KEY})"
# The plaintext DEK is passed through the child's environment, not as an
# argument. argv is world readable through /proc/<pid>/cmdline, and this
# process lives for as long as it takes to encrypt a multi-megabyte snapshot,
# so passing the DEK there let any local user read it and decrypt the backup
# directly, bypassing the point of wrapping it through transit.
# /proc/<pid>/environ is readable only by the same user and root, so this is a
# real improvement rather than a complete fix. stdin would be better still and
# is not available here because the heredoc uses it.
if ! DEK_B64="$dek_b64" python3 - "$snap_path" "$enc_path" "$wrapped_dek" <<'PY'
import base64, os, sys
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

src, dst, wrapped = sys.argv[1:]
dek_b64 = os.environ["DEK_B64"]
dek = base64.b64decode(dek_b64)
nonce = os.urandom(12)
aes = AESGCM(dek)
with open(src, "rb") as fi:
    pt = fi.read()
ct = aes.encrypt(nonce, pt, None)

# Header layout:
#   magic     8 bytes  "BAOSNAP1"
#   wrap_len  4 bytes  big-endian uint32
#   wrap      wrap_len bytes  (vault:vN:... ciphertext of the DEK)
#   nonce     12 bytes
#   ct        rest
import struct
wrap_b = wrapped.encode()
with open(dst, "wb") as fo:
    fo.write(b"BAOSNAP1")
    fo.write(struct.pack(">I", len(wrap_b)))
    fo.write(wrap_b)
    fo.write(nonce)
    fo.write(ct)
PY
then
  log err "envelope encryption failed"
  rm -f "$snap_path" "$enc_path"
  exit 1
fi

# Wipe the plaintext snapshot and the in-memory DEK reference.
shred -u "$snap_path" 2>/dev/null || rm -f "$snap_path"
unset dek_b64

enc_size=$(stat -c '%s' "$enc_path" 2>/dev/null || stat -f '%z' "$enc_path")
sha=$(sha256sum "$enc_path" | awk '{print $1}')
printf '%s  %s  %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$sha" "${snap_basename}.enc" >> "$manifest"
log info "encrypted snapshot ready size=${enc_size} sha256=${sha}"

#------------------------------------------------------------------------------
# 3. Upload to Wasabi
#------------------------------------------------------------------------------
log info "fetching Wasabi credentials from ${WASABI_SECRET_PATH}"
wasabi_json=$(bao kv get -format=json "$WASABI_SECRET_PATH" 2>/dev/null) || {
  log err "could not read Wasabi credentials from BAO"
  exit 2
}
AWS_ACCESS_KEY_ID=$(printf '%s' "$wasabi_json" | python3 -c 'import json,sys;print(json.load(sys.stdin)["data"]["data"]["access_key"])')
AWS_SECRET_ACCESS_KEY=$(printf '%s' "$wasabi_json" | python3 -c 'import json,sys;print(json.load(sys.stdin)["data"]["data"]["secret_key"])')
export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY

# Choose layout: daily/, weekly/ (Sun), monthly/ (1st)
day_of_week=$(date -u +%u)   # 1..7, Mon=1
day_of_month=$(date -u +%d)
prefix="daily"
if [[ "$day_of_month" == "01" ]]; then
  prefix="monthly"
elif [[ "$day_of_week" == "7" ]]; then
  prefix="weekly"
fi

wasabi() {
  aws --endpoint-url "$WASABI_ENDPOINT" --region "$WASABI_REGION" "$@"
}

#------------------------------------------------------------------------------
# 3a. Is the remote copy actually protected?
#------------------------------------------------------------------------------
# Checked every run, not once at setup time, because these are settings someone
# can turn off later and the whole value of the remote copy depends on them.
check_remote_immutability() {
  local versioning lock_enabled lock_mode ok=1

  versioning=$(wasabi s3api get-bucket-versioning --bucket "$WASABI_BUCKET" 2>/dev/null \
    | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("Status",""))
except Exception: print("")' 2>/dev/null || echo "")

  if [[ "$versioning" != "Enabled" ]]; then
    ok=0
    log err "bucket versioning is NOT enabled on ${WASABI_BUCKET}"
    log err "  without it, a PutObject to an existing key destroys the only copy."
    log err "  fix: aws --endpoint-url ${WASABI_ENDPOINT} s3api put-bucket-versioning \\"
    log err "         --bucket ${WASABI_BUCKET} --versioning-configuration Status=Enabled"
  fi

  local lock_json
  lock_json=$(wasabi s3api get-object-lock-configuration --bucket "$WASABI_BUCKET" 2>/dev/null || echo "")
  lock_enabled=$(printf '%s' "$lock_json" | python3 -c 'import json,sys
try: print(json.load(sys.stdin)["ObjectLockConfiguration"].get("ObjectLockEnabled",""))
except Exception: print("")' 2>/dev/null || echo "")
  lock_mode=$(printf '%s' "$lock_json" | python3 -c 'import json,sys
try: print(json.load(sys.stdin)["ObjectLockConfiguration"]["Rule"]["DefaultRetention"].get("Mode",""))
except Exception: print("")' 2>/dev/null || echo "")

  if [[ "$lock_enabled" != "Enabled" || -z "$lock_mode" ]]; then
    ok=0
    log err "object lock with a default retention rule is NOT configured on ${WASABI_BUCKET}"
    log err "  this host holds credentials for this bucket. Without object lock, an"
    log err "  attacker who owns this host can delete or overwrite every remote"
    log err "  snapshot, and the backups protect against disk failure only -- not"
    log err "  against ransomware, and not against a malicious operator."
    log err "  Object lock must be enabled at bucket CREATION (it cannot be added"
    log err "  to an existing bucket), then a default retention rule applied:"
    log err "    aws --endpoint-url ${WASABI_ENDPOINT} s3api create-bucket \\"
    log err "      --bucket <new-bucket> --object-lock-enabled-for-bucket"
    log err "    aws --endpoint-url ${WASABI_ENDPOINT} s3api put-object-lock-configuration \\"
    log err "      --bucket <new-bucket> --object-lock-configuration \\"
    log err "      'ObjectLockEnabled=Enabled,Rule={DefaultRetention={Mode=COMPLIANCE,Days=395}}'"
    log err "  Use COMPLIANCE, not GOVERNANCE: GOVERNANCE can be bypassed by any"
    log err "  identity holding s3:BypassGovernanceRetention, which defeats the point."
  elif [[ "$lock_mode" != "COMPLIANCE" ]]; then
    ok=0
    log err "object lock default retention is in ${lock_mode} mode, not COMPLIANCE"
    log err "  GOVERNANCE retention can be bypassed with s3:BypassGovernanceRetention."
  fi

  if (( ok == 1 )); then
    log info "remote immutability OK: versioning=Enabled object-lock=COMPLIANCE"
    IMMUTABILITY_OK=1
  fi
  return 0
}

check_remote_immutability

s3_key="${prefix}/${snap_basename}.enc"
log info "uploading to s3://${WASABI_BUCKET}/${s3_key}"
if ! wasabi s3 cp "$enc_path" "s3://${WASABI_BUCKET}/${s3_key}" \
     --no-progress >/dev/null 2>&1; then
  log err "wasabi upload failed -- snapshot retained locally at ${enc_path}"
  exit 2
fi
log info "upload OK s3://${WASABI_BUCKET}/${s3_key}"

#------------------------------------------------------------------------------
# 3b. Upload the integrity reference alongside the data
#------------------------------------------------------------------------------
# The manifest used to exist only at ${SNAPSHOT_DIR}/manifest.sha256 on this
# host. That is the one machine whose loss is the reason the remote copy exists,
# and restore-from-snapshot-runbook.md tells the operator to verify the
# downloaded snapshot against the manifest -- a step that was impossible in the
# scenario it was written for.
#
# Two copies go up: a per-snapshot sidecar (one immutable object per snapshot,
# so it inherits the same object-lock retention as the snapshot it describes)
# and the rolling manifest (convenient, but a mutable single object, so treat
# the sidecar as authoritative).
sidecar="${enc_path}.sha256"
printf '%s  %s\n' "$sha" "${snap_basename}.enc" > "$sidecar"
if ! wasabi s3 cp "$sidecar" "s3://${WASABI_BUCKET}/${s3_key}.sha256" \
     --no-progress >/dev/null 2>&1; then
  log err "sha256 sidecar upload failed -- the remote snapshot has no integrity reference"
  exit 2
fi
log info "sidecar OK s3://${WASABI_BUCKET}/${s3_key}.sha256"

if ! wasabi s3 cp "$manifest" "s3://${WASABI_BUCKET}/manifest/manifest.sha256" \
     --no-progress >/dev/null 2>&1; then
  log warning "rolling manifest upload failed (per-snapshot sidecar is uploaded, so restores are still verifiable)"
else
  log info "manifest OK s3://${WASABI_BUCKET}/manifest/manifest.sha256"
fi

#------------------------------------------------------------------------------
# 4. Retention: 14 daily / 8 weekly / 12 monthly (local + remote)
#------------------------------------------------------------------------------
prune_local() {
  local keep="$1"
  # shellcheck disable=SC2012
  ls -1t "$SNAPSHOT_DIR"/snapshot-*.snap.enc 2>/dev/null | tail -n +"$((keep + 1))" | while read -r f; do
    log info "pruning local: $f"
    rm -f "$f"
  done
}

# Remote deletion uses a DIFFERENT credential from the upload, read from a
# different OpenBao path. The upload credential should hold s3:PutObject and no
# delete permission at all, so that compromising this host yields the ability to
# add backups, not to remove them.
#
# Returns 1 if the prune credential is unavailable. In that case remote pruning
# is skipped rather than falling back to the upload credential -- the fallback is
# precisely the behaviour being removed. Remote retention should then be enforced
# by a bucket lifecycle rule, which is server-side and needs no credential on
# this host at all:
#   aws s3api put-bucket-lifecycle-configuration --bucket <bucket> \
#     --lifecycle-configuration file://lifecycle.json
# Note that with object lock in COMPLIANCE mode, a lifecycle expiration cannot
# delete an object before its retention date expires, which is the intended
# interaction: retention wins over retention policy.
load_prune_credentials() {
  local prune_json
  prune_json=$(bao kv get -format=json "$WASABI_PRUNE_SECRET_PATH" 2>/dev/null) || return 1
  PRUNE_ACCESS_KEY=$(printf '%s' "$prune_json" \
    | python3 -c 'import json,sys;print(json.load(sys.stdin)["data"]["data"]["access_key"])' 2>/dev/null) || return 1
  PRUNE_SECRET_KEY=$(printf '%s' "$prune_json" \
    | python3 -c 'import json,sys;print(json.load(sys.stdin)["data"]["data"]["secret_key"])' 2>/dev/null) || return 1
  [[ -n "$PRUNE_ACCESS_KEY" && -n "$PRUNE_SECRET_KEY" ]] || return 1
  return 0
}

prune_remote() {
  local prefix="$1" keep="$2" keys n i
  # List with the upload credential (read is fine), delete with the pruner.
  mapfile -t keys < <(wasabi s3 ls "s3://${WASABI_BUCKET}/${prefix}/" 2>/dev/null \
       | awk '{print $4}' | grep -v '\.sha256$' | sort -r)
  n=${#keys[@]}
  if (( n <= keep )); then
    log info "retention[$prefix]: ${n} kept (limit ${keep})"
    return 0
  fi
  for ((i = keep; i < n; i++)); do
    log info "pruning remote: ${prefix}/${keys[$i]}"
    # Subshell so the delete credential never leaks into the rest of the run.
    (
      export AWS_ACCESS_KEY_ID="$PRUNE_ACCESS_KEY"
      export AWS_SECRET_ACCESS_KEY="$PRUNE_SECRET_KEY"
      wasabi s3 rm "s3://${WASABI_BUCKET}/${prefix}/${keys[$i]}" >/dev/null 2>&1
      wasabi s3 rm "s3://${WASABI_BUCKET}/${prefix}/${keys[$i]}.sha256" >/dev/null 2>&1
    ) || log warning "could not prune ${prefix}/${keys[$i]} (object lock retention may still hold it, which is correct)"
  done
  return 0
}

prune_status=0
prune_local 14 || prune_status=1

if load_prune_credentials; then
  log info "remote retention using dedicated prune credential from ${WASABI_PRUNE_SECRET_PATH}"
  prune_remote daily 14   || prune_status=1
  prune_remote weekly 8   || prune_status=1
  prune_remote monthly 12 || prune_status=1
else
  log warning "no prune credential at ${WASABI_PRUNE_SECRET_PATH} -- SKIPPING remote prune"
  log warning "  remote snapshots will accumulate. This is deliberate: pruning with the"
  log warning "  upload credential would give this host the power to delete every backup."
  log warning "  Enforce remote retention with a bucket lifecycle rule instead, or provision"
  log warning "  a delete-only credential at ${WASABI_PRUNE_SECRET_PATH}."
fi

if (( prune_status != 0 )); then
  log warning "retention prune encountered errors -- snapshot itself succeeded"
  exit 3
fi

if (( IMMUTABILITY_OK == 0 )); then
  log warning "snapshot uploaded, but the remote copy is NOT immutable (see errors above)"
  if [[ "$REQUIRE_OBJECT_LOCK" == "1" ]]; then
    log err "REQUIRE_OBJECT_LOCK=1 and the bucket is not protected -- failing the run"
    exit 4
  fi
fi

log info "snapshot+upload+prune complete; next run in 24h"
exit 0
