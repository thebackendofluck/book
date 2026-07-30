#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# Local encrypted pg_basebackup rotation for Tier 0 recovery.

set -euo pipefail

BACKUP_ROOT="${BACKUP_ROOT:-/var/backups/pg/local}"
PGHOST="${PGHOST:-/var/run/postgresql}"
PGPORT="${PGPORT:-5432}"
PGUSER="${PGUSER:-replicator}"
RETENTION_DAYS="${RETENTION_DAYS:-3}"
PASSPHRASE_FILE="${PASSPHRASE_FILE:-/etc/pg-backup/local-hot.gpg-passphrase}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
WORK_DIR="${BACKUP_ROOT}/.local-hot-${STAMP}"
ARCHIVE="${BACKUP_ROOT}/local-hot-${STAMP}.tar.zst"

log() {
  printf '[%s] local-hot-backup: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

die() {
  log "ERROR: $*"
  exit 1
}

command -v pg_basebackup >/dev/null || die "pg_basebackup not found"
command -v gpg >/dev/null || die "gpg not found"
command -v zstd >/dev/null || die "zstd not found"
[ -f "${PASSPHRASE_FILE}" ] || die "passphrase file not found: ${PASSPHRASE_FILE}"

mkdir -p "${BACKUP_ROOT}" "${WORK_DIR}"
chmod 700 "${BACKUP_ROOT}" "${WORK_DIR}"

cleanup() {
  rm -rf "${WORK_DIR}" "${ARCHIVE}" 2>/dev/null || true
}
trap cleanup EXIT

log "starting pg_basebackup from ${PGHOST}:${PGPORT}"
pg_basebackup \
  --host="${PGHOST}" \
  --port="${PGPORT}" \
  --username="${PGUSER}" \
  --pgdata="${WORK_DIR}/base" \
  --format=plain \
  --wal-method=stream \
  --checkpoint=fast \
  --write-recovery-conf \
  --progress

log "compressing base backup"
tar -C "${WORK_DIR}" -I 'zstd -9 -T0' -cf "${ARCHIVE}" base

log "encrypting archive"
gpg --batch \
  --yes \
  --passphrase-file "${PASSPHRASE_FILE}" \
  --symmetric \
  --cipher-algo AES256 \
  --output "${ARCHIVE}.gpg" \
  "${ARCHIVE}"

sha256sum "${ARCHIVE}.gpg" > "${ARCHIVE}.gpg.sha256"
chmod 600 "${ARCHIVE}.gpg" "${ARCHIVE}.gpg.sha256"

log "rotating local snapshots older than ${RETENTION_DAYS} days"
find "${BACKUP_ROOT}" -maxdepth 1 -type f \
  \( -name 'local-hot-*.tar.zst.gpg' -o -name 'local-hot-*.tar.zst.gpg.sha256' \) \
  -mtime "+${RETENTION_DAYS}" \
  -delete

log "completed ${ARCHIVE}.gpg"
