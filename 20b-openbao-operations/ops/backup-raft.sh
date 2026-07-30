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

# Take a raft snapshot of the sandbox and write it to a dated file with a
# SHA-256 manifest entry. Prunes snapshots older than 14 days.
#
# Note: the sandbox uses the file backend by default (simpler for tests). If
# you have started a raft-backed sandbox variant, this script still works; it
# simply exits cleanly if the backend does not support raft snapshots.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"

ensure_sandbox
load_root_token

BACKUP_DIR="${SANDBOX_DIR}/backups"
mkdir -p "$BACKUP_DIR"

# Detect storage type from seal-status (file backend returns "raft" when using
# the integrated storage; for the file backend, snapshots are not supported).
storage=$(curl -fsS "${BAO_ADDR}/v1/sys/seal-status" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("storage_type","unknown"))')
if [[ "$storage" != "raft" ]]; then
  log "storage_type=$storage -- raft snapshots only supported on raft storage, exiting cleanly"
  exit 0
fi

SNAP="$BACKUP_DIR/snapshot-$(date -u +%Y-%m-%d-%H%M).snap"
log "taking raft snapshot -> $SNAP"
"$BAO_BIN" operator raft snapshot save "$SNAP"

[[ -s "$SNAP" ]] || die "snapshot file is empty"

log "recording sha256 manifest"
(cd "$BACKUP_DIR" && sha256sum "$(basename "$SNAP")") >> "$BACKUP_DIR/manifest.sha256"

log "pruning snapshots older than 14 days"
find "$BACKUP_DIR" -name 'snapshot-*.snap' -mtime +14 -print -delete

log "snapshot complete: $SNAP ($(stat -c%s "$SNAP") bytes)"
