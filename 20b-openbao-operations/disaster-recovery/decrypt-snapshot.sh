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

# decrypt-snapshot.sh -- reverse of the envelope encryption in snapshot-cron.sh.
#
# Reads a BAOSNAP1 file, unwraps the per-snapshot DEK through
# transit/decrypt/<key>, AES-GCM decrypts the body, and writes the cleartext
# raft snapshot ready for `bao operator raft snapshot restore`.
#
# This is step 3 of restore-from-snapshot-runbook.md. It is deliberately a
# standalone script and does NOT source lib/common.sh: common.sh refuses any
# BAO_ADDR that is not the loopback sandbox, and a restore runs against a real
# cluster. Do not "tidy" this by sourcing it -- that would make the script
# refuse to run at exactly the moment it is needed.
#
# Envelope layout written by snapshot-cron.sh:
#   magic     8 bytes            "BAOSNAP1"
#   wrap_len  4 bytes            big-endian uint32
#   wrap      wrap_len bytes     transit ciphertext of the DEK ("vault:vN:...")
#   nonce     12 bytes           AES-GCM nonce
#   ct        rest of the file   AES-256-GCM ciphertext, tag appended, no AAD
#
# Usage:
#   decrypt-snapshot.sh --in <file.snap.enc> --out <file.snap> \
#       [--key snapshot-key] [--manifest /var/lib/bao-snapshots/manifest.sha256] \
#       [--expect-sha256 <hex>]
#
# Required environment:
#   BAO_ADDR        the cluster that holds the wrapping key
#   BAO_TOKEN       or BAO_TOKEN_FILE -- needs `update transit/decrypt/<key>`
#
# Exit codes: 0 success, 1 usage/prerequisite, 2 integrity check failed,
#             3 transit unwrap failed, 4 AES-GCM decryption failed

set -euo pipefail

IN=""
OUT=""
TRANSIT_KEY="${TRANSIT_KEY:-snapshot-key}"
MANIFEST=""
EXPECT_SHA=""

# die <message> [exit-code]
die() { printf 'decrypt-snapshot: ERROR: %s\n' "$1" >&2; exit "${2:-1}"; }
log() { printf 'decrypt-snapshot: %s\n' "$*" >&2; }

usage() {
  sed -n '2,30p' "$0" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --in)            IN="${2:?--in needs a value}"; shift 2 ;;
    --out)           OUT="${2:?--out needs a value}"; shift 2 ;;
    --key)           TRANSIT_KEY="${2:?--key needs a value}"; shift 2 ;;
    --manifest)      MANIFEST="${2:?--manifest needs a value}"; shift 2 ;;
    --expect-sha256) EXPECT_SHA="${2:?--expect-sha256 needs a value}"; shift 2 ;;
    -h|--help)       usage ;;
    *)               die "unknown argument: $1" ;;
  esac
done

[[ -n "$IN"  ]] || die "--in is required"
[[ -n "$OUT" ]] || die "--out is required"
[[ -r "$IN"  ]] || die "input not readable: $IN"

for c in bao python3 sha256sum; do
  command -v "$c" >/dev/null 2>&1 || die "missing required command: $c"
done
python3 -c 'import cryptography' 2>/dev/null \
  || die "python3 cryptography module is required (pip install cryptography)"

# The output is a plaintext copy of every secret in the cluster. Create it 0600
# and keep it that way; the runbook shreds it after the restore.
umask 077

if [[ -z "${BAO_TOKEN:-}" && -n "${BAO_TOKEN_FILE:-}" ]]; then
  [[ -r "$BAO_TOKEN_FILE" ]] || die "BAO_TOKEN_FILE not readable: $BAO_TOKEN_FILE"
  BAO_TOKEN=$(< "$BAO_TOKEN_FILE")
  export BAO_TOKEN
fi
[[ -n "${BAO_TOKEN:-}" ]] || die "set BAO_TOKEN or BAO_TOKEN_FILE (needs update transit/decrypt/${TRANSIT_KEY})"
[[ -n "${BAO_ADDR:-}"  ]] || die "set BAO_ADDR to the cluster holding ${TRANSIT_KEY}"

#------------------------------------------------------------------------------
# 1. Integrity check against the manifest, before spending an HSM operation
#------------------------------------------------------------------------------
actual_sha=$(sha256sum "$IN" | awk '{print $1}')
log "sha256($(basename "$IN")) = ${actual_sha}"

if [[ -z "$EXPECT_SHA" && -n "$MANIFEST" ]]; then
  [[ -r "$MANIFEST" ]] || die "manifest not readable: $MANIFEST"
  # Manifest lines are: <iso-timestamp>  <sha256>  <basename>
  EXPECT_SHA=$(awk -v want="$(basename "$IN")" '$3 == want { sha = $2 } END { print sha }' "$MANIFEST")
  [[ -n "$EXPECT_SHA" ]] || die "no manifest entry for $(basename "$IN") in $MANIFEST" 2
fi

if [[ -n "$EXPECT_SHA" ]]; then
  if [[ "$actual_sha" != "$EXPECT_SHA" ]]; then
    die "sha256 mismatch: expected ${EXPECT_SHA}, got ${actual_sha}" 2
  fi
  log "integrity OK against expected sha256"
else
  log "WARNING: no --manifest or --expect-sha256 given; integrity not verified"
  log "         AES-GCM will still detect corruption, but you will find out"
  log "         after the unwrap rather than before it."
fi

#------------------------------------------------------------------------------
# 2. Read the header and extract the wrapped DEK
#------------------------------------------------------------------------------
read_header_py='
import struct, sys

with open(sys.argv[1], "rb") as fi:
    magic = fi.read(8)
    if magic != b"BAOSNAP1":
        sys.exit(f"bad magic {magic!r}: not a snapshot-cron.sh envelope")
    raw_len = fi.read(4)
    if len(raw_len) != 4:
        sys.exit("truncated header: missing wrap length")
    (wrap_len,) = struct.unpack(">I", raw_len)
    if not 0 < wrap_len <= 4096:
        sys.exit(f"implausible wrap length {wrap_len}")
    wrap = fi.read(wrap_len)
    if len(wrap) != wrap_len:
        sys.exit("truncated header: wrapped DEK short")
print(wrap.decode("ascii"))
'

wrapped_dek=$(python3 -c "$read_header_py" "$IN") \
  || die "could not parse BAOSNAP1 header of $IN" 2
log "header OK, wrapped DEK is ${wrapped_dek%%:*}:${wrapped_dek#vault:} (version prefix shown only)"

#------------------------------------------------------------------------------
# 3. Unwrap the DEK through transit and decrypt in one pipeline
#------------------------------------------------------------------------------
# The plaintext DEK travels only through the pipe into python3's stdin. It is
# never an argv element (world readable via /proc/<pid>/cmdline), never an
# environment variable, and never a shell variable that could end up in an
# error message or a trace. snapshot-cron.sh has to use the environment on the
# encrypt side because its heredoc occupies stdin; here the program is passed
# with -c, so stdin is free and can carry the key material instead.
decrypt_py='
import base64, struct, sys
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

src, dst = sys.argv[1:3]

dek_b64 = sys.stdin.read().strip()
if not dek_b64:
    sys.exit("no DEK on stdin: transit unwrap produced nothing")
dek = base64.b64decode(dek_b64)
if len(dek) != 32:
    sys.exit(f"unwrapped DEK is {len(dek)} bytes, expected 32 (AES-256)")

with open(src, "rb") as fi:
    if fi.read(8) != b"BAOSNAP1":
        sys.exit("bad magic")
    (wrap_len,) = struct.unpack(">I", fi.read(4))
    fi.seek(wrap_len, 1)
    nonce = fi.read(12)
    if len(nonce) != 12:
        sys.exit("truncated envelope: nonce short")
    ct = fi.read()

if not ct:
    sys.exit("truncated envelope: no ciphertext")

try:
    pt = AESGCM(dek).decrypt(nonce, ct, None)
except InvalidTag:
    sys.exit(
        "AES-GCM authentication failed. Either the file is corrupt or it was "
        "encrypted under a different DEK than the header claims. Do NOT restore "
        "this snapshot; try the previous one."
    )

with open(dst, "wb") as fo:
    fo.write(pt)
'

log "unwrapping DEK via transit/decrypt/${TRANSIT_KEY}"
# The pipeline runs as a statement, not inside a command substitution, so that
# PIPESTATUS below refers to it. A command substitution runs the pipeline in a
# subshell and leaves the parent's PIPESTATUS with a single element, which makes
# it impossible to tell a failed transit unwrap from a failed decryption.
set +e
"${BAO_BIN:-bao}" write -field=plaintext "transit/decrypt/${TRANSIT_KEY}" \
    "ciphertext=${wrapped_dek}" \
  | python3 -c "$decrypt_py" "$IN" "$OUT"
pipe_status=("${PIPESTATUS[@]}")
set -e

if [[ "${pipe_status[0]}" -ne 0 ]]; then
  rm -f "$OUT"
  die "transit/decrypt/${TRANSIT_KEY} failed. Check BAO_ADDR, BAO_TOKEN policy
  (needs 'update transit/decrypt/${TRANSIT_KEY}'), that the key still exists,
  and that min_decryption_version has not been advanced past the version that
  wrapped this DEK." 3
fi
if [[ "${pipe_status[1]}" -ne 0 ]]; then
  rm -f "$OUT"
  die "decryption failed (see message above)" 4
fi

#------------------------------------------------------------------------------
# 4. Sanity-check the output before the operator restores it
#------------------------------------------------------------------------------
# A raft snapshot is a gzipped tar. Checking the magic here turns "the restore
# failed for some reason" into "the decrypt produced something that is not a
# snapshot", which is a much shorter conversation at 3am.
if ! python3 -c 'import sys; sys.exit(0 if open(sys.argv[1],"rb").read(2) == b"\x1f\x8b" else 1)' "$OUT"; then
  die "decrypted output is not gzip-framed; this does not look like a raft snapshot" 4
fi

chmod 600 "$OUT"
plain_size=$(stat -c '%s' "$OUT" 2>/dev/null || stat -f '%z' "$OUT")
log "decrypted ${plain_size} bytes to ${OUT} (mode 0600)"
log "next: bao operator raft snapshot restore -force ${OUT}"
log "after the restore completes and the canary read passes: shred -u ${OUT}"
