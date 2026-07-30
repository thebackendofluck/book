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

# Encrypted PostgreSQL backup to Wasabi S3
# Database: new_acmetocasino (Docker container: new-casino-db)
# DO NOT log credentials, keys, or passphrases
set -euo pipefail

# ── Configuration ────────────────────────────────────────────────────────────
DB_CONTAINER="new-casino-db"
DB_USER="casino_new"
DB_NAME="new_acmetocasino"
WASABI_PROFILE="wasabi-acmetocasino"          # named profile in ~/.wasabi/credentials
WASABI_ENDPOINT="https://s3.eu-central-1.wasabisys.com"
WASABI_BUCKET="acmetocasino"
S3_PREFIX="postgres/${DB_NAME}"
PASSPHRASE_FILE="${PASSPHRASE_FILE:-/root/.wasabi/encryption-passphrase}"
LOG="/var/log/wasabi-backup.log"
WORK_DIR="/opt/backups/wasabi-tmp"
PROMETHEUS_GW="${PROMETHEUS_PUSHGATEWAY_URL:-}"  # e.g. http://localhost:9091
RETENTION_DAILY=30
RETENTION_MONTHLY=365  # ~12 months in days

# ── Helpers ──────────────────────────────────────────────────────────────────
log() { echo "[$(date -Iseconds)] $*" >> "${LOG}"; }
die() { log "ERROR: $*"; push_metric "0"; exit 1; }

push_metric() {
    local success="$1"
    local size_bytes="${2:-0}"
    local duration_s="${3:-0}"
    if [ -n "${PROMETHEUS_GW}" ]; then
        cat <<PROM | curl -s --data-binary @- "${PROMETHEUS_GW}/metrics/job/wasabi_backup/instance/${DB_NAME}" >/dev/null 2>&1 || true
# HELP wasabi_backup_success 1 if last backup succeeded
# TYPE wasabi_backup_success gauge
wasabi_backup_success{db="${DB_NAME}"} ${success}
# HELP wasabi_backup_size_bytes Size of encrypted backup in bytes
# TYPE wasabi_backup_size_bytes gauge
wasabi_backup_size_bytes{db="${DB_NAME}"} ${size_bytes}
# HELP wasabi_backup_duration_seconds Duration of backup in seconds
# TYPE wasabi_backup_duration_seconds gauge
wasabi_backup_duration_seconds{db="${DB_NAME}"} ${duration_s}
# HELP wasabi_backup_timestamp_seconds Unix timestamp of last backup
# TYPE wasabi_backup_timestamp_seconds gauge
wasabi_backup_timestamp_seconds{db="${DB_NAME}"} $(date +%s)
PROM
    fi
}

# ── Pre-flight checks ────────────────────────────────────────────────────────
[ -f "${PASSPHRASE_FILE}" ] || die "Passphrase file not found: ${PASSPHRASE_FILE}"
[ "$(stat -c '%a' "${PASSPHRASE_FILE}")" = "600" ] || die "Passphrase file must be chmod 600"
command -v docker >/dev/null || die "docker not found"
command -v gpg >/dev/null || die "gpg not found"
command -v aws >/dev/null || die "aws CLI not found"

docker inspect "${DB_CONTAINER}" >/dev/null 2>&1 || die "Container ${DB_CONTAINER} not running"

START_TS=$(date +%s)
STAMP=$(date +%Y/%m/%d)
TIME_STAMP=$(date +%H%M%S)
DATE_PATH="${STAMP}"
BACKUP_BASENAME="backup-${TIME_STAMP}.dump.gz.gpg"
CHECKSUM_BASENAME="${BACKUP_BASENAME}.sha256"
S3_KEY="${S3_PREFIX}/${DATE_PATH}/${BACKUP_BASENAME}"
S3_CHECKSUM_KEY="${S3_PREFIX}/${DATE_PATH}/${CHECKSUM_BASENAME}"

mkdir -p "${WORK_DIR}"
chmod 700 "${WORK_DIR}"

WORK_FILE="${WORK_DIR}/${BACKUP_BASENAME}"
CHECKSUM_FILE="${WORK_DIR}/${CHECKSUM_BASENAME}"

log "=== Backup started: ${DB_NAME} → s3://${WASABI_BUCKET}/${S3_KEY} ==="

# ── Step 1: pg_dump | gzip | gpg encrypt ────────────────────────────────────
log "Running pg_dump | gzip | gpg..."

# Use process substitution to avoid writing decrypted data to disk
# pg_dump → gzip → gpg symmetric (passphrase from file, batch mode)
docker exec "${DB_CONTAINER}" pg_dump \
    -U "${DB_USER}" \
    -d "${DB_NAME}" \
    -Fc \
    2>/dev/null \
  | gzip -9 \
  | gpg --batch \
        --yes \
        --passphrase-file "${PASSPHRASE_FILE}" \
        --symmetric \
        --cipher-algo AES256 \
        --compress-algo none \
        --output "${WORK_FILE}" \
  || die "pg_dump/gzip/gpg pipeline failed"

[ -s "${WORK_FILE}" ] || die "Encrypted backup file is empty"

BACKUP_SIZE=$(stat -c '%s' "${WORK_FILE}")
BACKUP_SIZE_HUMAN=$(du -h "${WORK_FILE}" | cut -f1)
log "Encrypted backup size: ${BACKUP_SIZE_HUMAN} (${BACKUP_SIZE} bytes)"

# ── Step 2: SHA256 checksum ──────────────────────────────────────────────────
sha256sum "${WORK_FILE}" | awk '{print $1}' > "${CHECKSUM_FILE}"
CHECKSUM=$(cat "${CHECKSUM_FILE}")
log "SHA256: ${CHECKSUM}"

# ── Step 3: Write manifest (row counts for restore integrity check) ───────────
MANIFEST_BASENAME="manifest-${TIME_STAMP}.json"
MANIFEST_FILE="${WORK_DIR}/${MANIFEST_BASENAME}"
S3_MANIFEST_KEY="${S3_PREFIX}/${DATE_PATH}/${MANIFEST_BASENAME}"

log "Collecting row counts for manifest..."
PLAYERS=$(docker exec "${DB_CONTAINER}" psql -U "${DB_USER}" -d "${DB_NAME}" -tAq -c 'SELECT count(*) FROM players;' 2>/dev/null)
GAME_ROUNDS=$(docker exec "${DB_CONTAINER}" psql -U "${DB_USER}" -d "${DB_NAME}" -tAq -c 'SELECT count(*) FROM game_rounds;' 2>/dev/null)
WALLET_EVENTS=$(docker exec "${DB_CONTAINER}" psql -U "${DB_USER}" -d "${DB_NAME}" -tAq -c 'SELECT count(*) FROM wallet_events;' 2>/dev/null)

cat > "${MANIFEST_FILE}" << MANIFEST_EOF
{
  "db": "${DB_NAME}",
  "backup_key": "${S3_KEY}",
  "backup_size_bytes": ${BACKUP_SIZE},
  "sha256": "${CHECKSUM}",
  "timestamp": "$(date -Iseconds)",
  "row_counts": {
    "players": ${PLAYERS:-0},
    "game_rounds": ${GAME_ROUNDS:-0},
    "wallet_events": ${WALLET_EVENTS:-0}
  }
}
MANIFEST_EOF
log "Manifest: players=${PLAYERS}, game_rounds=${GAME_ROUNDS}, wallet_events=${WALLET_EVENTS}"

# ── Step 4: Upload to Wasabi ─────────────────────────────────────────────────
log "Uploading encrypted backup..."
aws --profile "${WASABI_PROFILE}" \
    --endpoint-url "${WASABI_ENDPOINT}" \
    s3 cp "${WORK_FILE}" "s3://${WASABI_BUCKET}/${S3_KEY}" \
    --storage-class STANDARD \
    --no-progress \
  || die "Upload of backup failed"

log "Uploading SHA256 checksum..."
aws --profile "${WASABI_PROFILE}" \
    --endpoint-url "${WASABI_ENDPOINT}" \
    s3 cp "${CHECKSUM_FILE}" "s3://${WASABI_BUCKET}/${S3_CHECKSUM_KEY}" \
    --no-progress \
  || die "Upload of checksum failed"

log "Uploading manifest..."
aws --profile "${WASABI_PROFILE}" \
    --endpoint-url "${WASABI_ENDPOINT}" \
    s3 cp "${MANIFEST_FILE}" "s3://${WASABI_BUCKET}/${S3_MANIFEST_KEY}" \
    --no-progress \
  || die "Upload of manifest failed"

# ── Step 5: Retention enforcement ────────────────────────────────────────────
log "Enforcing retention policy (${RETENTION_DAILY} daily, ${RETENTION_MONTHLY} monthly)..."

# List all backup keys under the prefix, sorted oldest-first
ALL_KEYS=$(aws --profile "${WASABI_PROFILE}" \
    --endpoint-url "${WASABI_ENDPOINT}" \
    s3 ls "s3://${WASABI_BUCKET}/${S3_PREFIX}/" \
    --recursive \
  | grep '\.dump\.gz\.gpg$' \
  | sort \
  | awk '{print $NF}' 2>/dev/null) || ALL_KEYS=""

CUTOFF_DAILY=$(date -d "-${RETENTION_DAILY} days" +%Y%m%d 2>/dev/null || date -v-"${RETENTION_DAILY}"d +%Y%m%d)
CUTOFF_MONTHLY=$(date -d "-${RETENTION_MONTHLY} days" +%Y%m%d 2>/dev/null || date -v-"${RETENTION_MONTHLY}"d +%Y%m%d)

DELETED=0
while IFS= read -r key; do
    [ -z "$key" ] && continue
    # Extract date from key path: postgres/new_acmetocasino/2026/04/12/backup-...
    KEY_DATE=$(echo "$key" | grep -oE '[0-9]{4}/[0-9]{2}/[0-9]{2}' | tr -d '/')
    [ -z "$KEY_DATE" ] && continue

    # Keep if within daily retention window
    if [ "$KEY_DATE" -ge "$CUTOFF_DAILY" ]; then
        continue
    fi

    # Keep first-of-month backups within monthly retention
    DAY_PART="${KEY_DATE: -2}"
    if [ "$DAY_PART" = "01" ] && [ "$KEY_DATE" -ge "$CUTOFF_MONTHLY" ]; then
        continue
    fi

    # Delete expired backup + sidecar files
    aws --profile "${WASABI_PROFILE}" \
        --endpoint-url "${WASABI_ENDPOINT}" \
        s3 rm "s3://${WASABI_BUCKET}/${key}" 2>/dev/null || true
    aws --profile "${WASABI_PROFILE}" \
        --endpoint-url "${WASABI_ENDPOINT}" \
        s3 rm "s3://${WASABI_BUCKET}/${key}.sha256" 2>/dev/null || true
    DELETED=$((DELETED + 1))
done <<< "${ALL_KEYS}"

log "Retention: deleted ${DELETED} expired backup(s)"

# ── Step 6: Cleanup temp files ───────────────────────────────────────────────
rm -f "${WORK_FILE}" "${CHECKSUM_FILE}" "${MANIFEST_FILE}"

# ── Done ─────────────────────────────────────────────────────────────────────
END_TS=$(date +%s)
DURATION=$((END_TS - START_TS))
log "=== Backup complete: ${BACKUP_SIZE_HUMAN} in ${DURATION}s ==="
log "S3 key: s3://${WASABI_BUCKET}/${S3_KEY}"

push_metric "1" "${BACKUP_SIZE}" "${DURATION}"

# Write status JSON for dashboard
STATUS_JSON="/var/www/acmetocasino.com/api-data/backup-status.json"
mkdir -p "$(dirname "${STATUS_JSON}")"
cat > "${STATUS_JSON}.tmp" << JSON_EOF
{
  "last_backup": "$(date -Iseconds)",
  "last_backup_size_bytes": ${BACKUP_SIZE},
  "last_backup_size_human": "${BACKUP_SIZE_HUMAN}",
  "last_backup_status": "success",
  "last_backup_duration_s": ${DURATION},
  "last_backup_s3_key": "${S3_KEY}",
  "sha256": "${CHECKSUM}",
  "wasabi_bucket": "${WASABI_BUCKET}",
  "wasabi_region": "eu-central-1",
  "retention_daily_days": ${RETENTION_DAILY},
  "retention_monthly_months": 12,
  "db": "${DB_NAME}"
}
JSON_EOF
mv "${STATUS_JSON}.tmp" "${STATUS_JSON}"

exit 0
