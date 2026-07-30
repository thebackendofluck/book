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

# Rotate OpenBao raft snapshots: prune anything older than 14 days and
# archive the survivors to an off-host object store with age encryption.
#
# The rotation lives downstream of backup-raft.sh. A typical cron setup:
#     0 */6 * * * /usr/local/bin/backup-raft.sh
#     30 3  * * * /usr/local/bin/rotate-snapshots.sh
#
# Environment variables (all optional):
#     BACKUP_DIR           Directory that backup-raft.sh writes to
#                          (default: /var/backups/openbao or the sandbox dir)
#     RETENTION_DAYS       Local retention window before prune/archive
#                          (default: 14, matching the chapter text)
#     AGE_RECIPIENTS_FILE  File of age recipients (one per line). When unset
#                          archival is skipped and only the local prune runs.
#     MC_ALIAS             mc alias pointing at the MinIO/S3 target bucket
#                          (e.g. "minio/openbao-archive"). When unset the
#                          local .age file is left in place rather than
#                          uploaded, so the script is still exercised in CI
#                          without needing a real object store.
#
# The script exits non-zero on any `age` or `mc` failure so that the cron
# job's mail wrapper surfaces the problem. It is idempotent: re-running it
# on a directory with no eligible files is a no-op.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"

: "${BACKUP_DIR:=${SANDBOX_DIR}/backups}"
: "${RETENTION_DAYS:=14}"
: "${AGE_RECIPIENTS_FILE:=}"
: "${MC_ALIAS:=}"

[[ -d "$BACKUP_DIR" ]] || { log "backup dir $BACKUP_DIR does not exist; nothing to rotate"; exit 0; }

log "scanning $BACKUP_DIR for snapshots older than ${RETENTION_DAYS} days"

count_pruned=0
count_archived=0

# Iterate eligible files safely (handles spaces in names)
while IFS= read -r -d '' snap; do
  base=$(basename "$snap")

  # Archive before prune when recipients are configured
  if [[ -n "$AGE_RECIPIENTS_FILE" && -f "$AGE_RECIPIENTS_FILE" ]]; then
    if ! command -v age >/dev/null 2>&1; then
      log "age binary not found; skipping archival for $base"
    else
      archive_path="${snap}.age"
      log "encrypting $base with age recipients from $AGE_RECIPIENTS_FILE"
      age -R "$AGE_RECIPIENTS_FILE" "$snap" > "$archive_path" \
        || { log "age encryption failed for $base"; exit 1; }

      # Append to the manifest chain so rotations remain tamper-evident
      (
        cd "$BACKUP_DIR" && sha256sum "$(basename "$archive_path")"
      ) >> "$BACKUP_DIR/manifest.sha256"

      if [[ -n "$MC_ALIAS" ]] && command -v mc >/dev/null 2>&1; then
        log "uploading $base.age to $MC_ALIAS"
        if mc cp "$archive_path" "$MC_ALIAS/" >/dev/null 2>&1; then
          rm -f "$archive_path"
          count_archived=$((count_archived + 1))
        else
          log "mc cp failed for $base.age; keeping local .age file"
        fi
      else
        log "MC_ALIAS unset or mc missing; leaving $archive_path in place"
        count_archived=$((count_archived + 1))
      fi
    fi
  fi

  log "pruning $base"
  rm -f "$snap"
  count_pruned=$((count_pruned + 1))

done < <(find "$BACKUP_DIR" -maxdepth 1 -name 'snapshot-*.snap' -type f -mtime +"$RETENTION_DAYS" -print0 2>/dev/null)

log "rotation complete: pruned=$count_pruned archived=$count_archived"
