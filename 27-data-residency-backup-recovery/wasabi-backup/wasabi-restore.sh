#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# Restore encrypted PostgreSQL backup from Wasabi S3
# Usage: wasabi-restore.sh <s3-key> [--dry-run] [--target-db <dbname>]
# Example: wasabi-restore.sh postgres/new_acmetocasino/2026/04/12/backup-030000.dump.gz.gpg
set -euo pipefail

# ── Configuration ────────────────────────────────────────────────────────────
DB_CONTAINER="new-casino-db"
DB_USER="casino_new"
DB_NAME_PROD="new_acmetocasino"
WASABI_PROFILE="wasabi-acmetocasino"          # named profile in ~/.wasabi/credentials
WASABI_ENDPOINT="https://s3.eu-central-1.wasabisys.com"
WASABI_BUCKET="acmetocasino"
PASSPHRASE_FILE="${PASSPHRASE_FILE:-/root/.wasabi/encryption-passphrase}"
WORK_DIR="/opt/backups/wasabi-restore-tmp"
LOG="/var/log/wasabi-backup.log"

# ── Argument parsing ─────────────────────────────────────────────────────────
DRY_RUN=0
TARGET_DB="new_acmetocasino_restore_test"
S3_KEY=""

usage() {
    echo "Usage: $0 <s3-backup-key> [--dry-run] [--target-db <dbname>]"
    echo ""
    echo "  <s3-backup-key>   Full S3 key, e.g.:"
    echo "                    postgres/new_acmetocasino/2026/04/12/backup-030000.dump.gz.gpg"
    echo "  --dry-run         Download and verify but do not restore"
    echo "  --target-db NAME  Target database (default: new_acmetocasino_restore_test)"
    echo "                    WARNING: never use '${DB_NAME_PROD}' without explicit confirmation"
    exit 1
}

[ $# -eq 0 ] && usage

S3_KEY="$1"
shift

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --target-db)
            shift
            TARGET_DB="${1:-}"
            [ -z "${TARGET_DB}" ] && { echo "ERROR: --target-db requires a name"; exit 1; }
            ;;
        *) echo "Unknown argument: $1"; usage ;;
    esac
    shift
done

# Safety guard: block direct restore to production DB
if [ "${TARGET_DB}" = "${DB_NAME_PROD}" ]; then
    echo "ERROR: Restoring to production database '${DB_NAME_PROD}' is blocked by default."
    echo "This script is designed for restore testing only."
    echo "If you truly need a production restore, edit the script to remove this guard."
    exit 1
fi

# ── Helpers ──────────────────────────────────────────────────────────────────
log() { echo "[$(date -Iseconds)] RESTORE: $*" | tee -a "${LOG}"; }
die() { log "ERROR: $*"; exit 1; }

# ── Pre-flight checks ────────────────────────────────────────────────────────
[ -z "${S3_KEY}" ] && usage
[ -f "${PASSPHRASE_FILE}" ] || die "Passphrase file not found: ${PASSPHRASE_FILE}"
command -v docker >/dev/null || die "docker not found"
command -v gpg >/dev/null || die "gpg not found"
command -v aws >/dev/null || die "aws CLI not found"
docker inspect "${DB_CONTAINER}" >/dev/null 2>&1 || die "Container ${DB_CONTAINER} not running"

START_TS=$(date +%s)

mkdir -p "${WORK_DIR}"
chmod 700 "${WORK_DIR}"

BACKUP_FILE="${WORK_DIR}/restore.dump.gz.gpg"
CHECKSUM_FILE="${WORK_DIR}/restore.dump.gz.gpg.sha256"
DECRYPTED_FILE="${WORK_DIR}/restore.dump.gz"

log "=== Restore started ==="
log "S3 key: s3://${WASABI_BUCKET}/${S3_KEY}"
log "Target DB: ${TARGET_DB}"
[ "${DRY_RUN}" -eq 1 ] && log "Mode: DRY-RUN (no actual restore)"

# ── Step 1: Download backup ──────────────────────────────────────────────────
log "Downloading encrypted backup..."
DL_START=$(date +%s)
aws --profile "${WASABI_PROFILE}" \
    --endpoint-url "${WASABI_ENDPOINT}" \
    s3 cp "s3://${WASABI_BUCKET}/${S3_KEY}" "${BACKUP_FILE}" \
    --no-progress \
  || die "Download failed for s3://${WASABI_BUCKET}/${S3_KEY}"
DL_END=$(date +%s)
DL_DURATION=$((DL_END - DL_START))

ENCRYPTED_SIZE=$(stat -c '%s' "${BACKUP_FILE}")
log "Download complete: ${ENCRYPTED_SIZE} bytes in ${DL_DURATION}s"

# ── Step 2: Download and verify SHA256 ──────────────────────────────────────
S3_CHECKSUM_KEY="${S3_KEY}.sha256"
log "Downloading SHA256 checksum..."
aws --profile "${WASABI_PROFILE}" \
    --endpoint-url "${WASABI_ENDPOINT}" \
    s3 cp "s3://${WASABI_BUCKET}/${S3_CHECKSUM_KEY}" "${CHECKSUM_FILE}" \
    --no-progress \
  || die "Download failed for checksum: s3://${WASABI_BUCKET}/${S3_CHECKSUM_KEY}"

EXPECTED_SHA=$(cat "${CHECKSUM_FILE}")
ACTUAL_SHA=$(sha256sum "${BACKUP_FILE}" | awk '{print $1}')

log "Expected SHA256: ${EXPECTED_SHA}"
log "Actual   SHA256: ${ACTUAL_SHA}"

if [ "${EXPECTED_SHA}" = "${ACTUAL_SHA}" ]; then
    log "SHA256 MATCH: integrity verified"
else
    die "SHA256 MISMATCH: backup may be corrupted or tampered"
fi

# ── Step 3: Download manifest ────────────────────────────────────────────────
BACKUP_BASENAME=$(basename "${S3_KEY}")
BACKUP_DIR=$(dirname "${S3_KEY}")
TIME_PART="${BACKUP_BASENAME#backup-}"
TIME_PART="${TIME_PART%.dump.gz.gpg}"
MANIFEST_KEY="${BACKUP_DIR}/manifest-${TIME_PART}.json"
MANIFEST_FILE="${WORK_DIR}/manifest.json"

log "Downloading manifest..."
if aws --profile "${WASABI_PROFILE}" \
    --endpoint-url "${WASABI_ENDPOINT}" \
    s3 cp "s3://${WASABI_BUCKET}/${MANIFEST_KEY}" "${MANIFEST_FILE}" \
    --no-progress 2>/dev/null; then
    MANIFEST_AVAILABLE=1
else
    log "Manifest not found, integrity comparison will be skipped"
    MANIFEST_AVAILABLE=0
fi

if [ "${MANIFEST_AVAILABLE}" -eq 1 ]; then
    EXPECTED_PLAYERS=$(python3 -c "import json; d=json.load(open('${MANIFEST_FILE}')); print(d['row_counts']['players'])" 2>/dev/null || echo "unknown")
    EXPECTED_ROUNDS=$(python3 -c "import json; d=json.load(open('${MANIFEST_FILE}')); print(d['row_counts']['game_rounds'])" 2>/dev/null || echo "unknown")
    EXPECTED_WALLET=$(python3 -c "import json; d=json.load(open('${MANIFEST_FILE}')); print(d['row_counts']['wallet_events'])" 2>/dev/null || echo "unknown")
    log "Manifest row counts: players=${EXPECTED_PLAYERS} game_rounds=${EXPECTED_ROUNDS} wallet_events=${EXPECTED_WALLET}"
fi

# ── Step 4: Decrypt ──────────────────────────────────────────────────────────
log "Decrypting backup..."
gpg --batch \
    --yes \
    --passphrase-file "${PASSPHRASE_FILE}" \
    --decrypt \
    --output "${DECRYPTED_FILE}" \
    "${BACKUP_FILE}" \
  || die "Decryption failed"

DECRYPTED_SIZE=$(stat -c '%s' "${DECRYPTED_FILE}")
log "Decrypted size: ${DECRYPTED_SIZE} bytes"

# ── Step 5: Verify gzip integrity ────────────────────────────────────────────
log "Verifying decompression integrity..."
gunzip -t "${DECRYPTED_FILE}" || die "Gzip integrity check failed"
log "Gzip integrity OK"

if [ "${DRY_RUN}" -eq 1 ]; then
    log "=== DRY-RUN complete — no database restore performed ==="
    log "Encrypted size: ${ENCRYPTED_SIZE} bytes"
    log "Decrypted size: ${DECRYPTED_SIZE} bytes"
    log "SHA256: MATCHED"
    rm -f "${BACKUP_FILE}" "${CHECKSUM_FILE}" "${DECRYPTED_FILE}" "${MANIFEST_FILE}" 2>/dev/null || true
    exit 0
fi

# ── Step 6: Create target database ──────────────────────────────────────────
log "Creating target database: ${TARGET_DB}..."
docker exec "${DB_CONTAINER}" psql -U "${DB_USER}" -d postgres \
    -c "DROP DATABASE IF EXISTS \"${TARGET_DB}\";" 2>/dev/null || true
docker exec "${DB_CONTAINER}" psql -U "${DB_USER}" -d postgres \
    -c "CREATE DATABASE \"${TARGET_DB}\";" \
  || die "Failed to create target database"

# ── Step 7: Restore ──────────────────────────────────────────────────────────
log "Restoring to ${TARGET_DB}..."
RESTORE_START=$(date +%s)

# Stream decrypted gzip into pg_restore inside the container
gunzip -c "${DECRYPTED_FILE}" \
  | docker exec -i "${DB_CONTAINER}" pg_restore \
      -U "${DB_USER}" \
      -d "${TARGET_DB}" \
      --no-owner \
      --no-acl \
      -Fc \
  || die "pg_restore failed"

RESTORE_END=$(date +%s)
RESTORE_DURATION=$((RESTORE_END - RESTORE_START))
log "Restore complete in ${RESTORE_DURATION}s"

# ── Step 8: Integrity verification ──────────────────────────────────────────
log "Running integrity queries..."
ACTUAL_PLAYERS=$(docker exec "${DB_CONTAINER}" psql -U "${DB_USER}" -d "${TARGET_DB}" \
    -tAq -c 'SELECT count(*) FROM players;' 2>/dev/null || echo "error")
ACTUAL_ROUNDS=$(docker exec "${DB_CONTAINER}" psql -U "${DB_USER}" -d "${TARGET_DB}" \
    -tAq -c 'SELECT count(*) FROM game_rounds;' 2>/dev/null || echo "error")
ACTUAL_WALLET=$(docker exec "${DB_CONTAINER}" psql -U "${DB_USER}" -d "${TARGET_DB}" \
    -tAq -c 'SELECT count(*) FROM wallet_events;' 2>/dev/null || echo "error")

log "Restored row counts: players=${ACTUAL_PLAYERS} game_rounds=${ACTUAL_ROUNDS} wallet_events=${ACTUAL_WALLET}"

INTEGRITY_OK=1
if [ "${MANIFEST_AVAILABLE}" -eq 1 ]; then
    if [ "${ACTUAL_PLAYERS}" = "${EXPECTED_PLAYERS}" ] && \
       [ "${ACTUAL_ROUNDS}" = "${EXPECTED_ROUNDS}" ] && \
       [ "${ACTUAL_WALLET}" = "${EXPECTED_WALLET}" ]; then
        log "INTEGRITY CHECK: PASSED — all row counts match manifest"
    else
        log "INTEGRITY CHECK: FAILED — row count mismatch"
        log "  players: expected=${EXPECTED_PLAYERS} actual=${ACTUAL_PLAYERS}"
        log "  game_rounds: expected=${EXPECTED_ROUNDS} actual=${ACTUAL_ROUNDS}"
        log "  wallet_events: expected=${EXPECTED_WALLET} actual=${ACTUAL_WALLET}"
        INTEGRITY_OK=0
    fi
fi

# ── Cleanup ──────────────────────────────────────────────────────────────────
rm -f "${BACKUP_FILE}" "${CHECKSUM_FILE}" "${DECRYPTED_FILE}" "${MANIFEST_FILE}" 2>/dev/null || true

# ── Summary ──────────────────────────────────────────────────────────────────
END_TS=$(date +%s)
TOTAL_DURATION=$((END_TS - START_TS))
log "=== Restore summary ==="
log "  Download: ${DL_DURATION}s"
log "  Restore:  ${RESTORE_DURATION}s"
log "  Total:    ${TOTAL_DURATION}s"
log "  Encrypted size: ${ENCRYPTED_SIZE} bytes"
log "  Decrypted size: ${DECRYPTED_SIZE} bytes"
log "  SHA256: MATCHED"
log "  Target DB: ${TARGET_DB}"
log "  Integrity: $([ "${INTEGRITY_OK}" -eq 1 ] && echo PASSED || echo FAILED)"

[ "${INTEGRITY_OK}" -eq 0 ] && exit 1
exit 0
