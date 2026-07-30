#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# setup-pg18-hsm-test.sh
# Deploy PostgreSQL 18 via Docker and populate with casino test data.
# Used for YubiHSM 2 + OpenBao TDE testing (GDPR Art.32 / PCI DSS 3.5).
# Usage: bash setup-pg18-hsm-test.sh [--destroy]

set -euo pipefail

CONTAINER_NAME="pg18-hsm-test"
PG_USER="acmetocasino"
PG_DB="casino_hsm_test"
PG_PORT="5440"
EVIDENCE_DIR="${EVIDENCE_DIR:-/opt/yubihsm-evidence}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
pass() { echo "[PASS] $*"; }
fail() { echo "[FAIL] $*" >&2; exit 1; }

if [ "${1:-}" = "--destroy" ]; then
    log "Destroying test database..."
    sudo docker stop "${CONTAINER_NAME}" 2>/dev/null || true
    sudo docker rm "${CONTAINER_NAME}" 2>/dev/null || true
    log "Done."
    exit 0
fi

# Check if already running
if sudo docker inspect "${CONTAINER_NAME}" > /dev/null 2>&1; then
    log "Container ${CONTAINER_NAME} already exists. Use --destroy to remove."
    exit 0
fi

log "=== Deploying PostgreSQL 18 for HSM TDE test ==="

# Generate strong password
PG_PASS="$(openssl rand -hex 16)"
mkdir -p "${EVIDENCE_DIR}"
printf '%s\n' "${PG_PASS}" | sudo tee "${EVIDENCE_DIR}/pg18-test-password" > /dev/null
sudo chmod 600 "${EVIDENCE_DIR}/pg18-test-password"
log "Password stored in ${EVIDENCE_DIR}/pg18-test-password"

# Deploy container
log "Starting PostgreSQL 18 container..."
sudo docker run -d \
    --name "${CONTAINER_NAME}" \
    -e "POSTGRES_USER=${PG_USER}" \
    -e "POSTGRES_PASSWORD=${PG_PASS}" \
    -e "POSTGRES_DB=${PG_DB}" \
    -p "127.0.0.1:${PG_PORT}:5432" \
    --restart unless-stopped \
    --memory 1g \
    --security-opt no-new-privileges:true \
    postgres:18-alpine

log "Waiting for PostgreSQL to accept connections..."
READY=false
for _i in $(seq 1 30); do
    if sudo docker exec "${CONTAINER_NAME}" pg_isready -U "${PG_USER}" -d "${PG_DB}" > /dev/null 2>&1; then
        READY=true
        break
    fi
    sleep 2
done
[ "${READY}" = "true" ] || fail "PostgreSQL did not start within 60 seconds"
pass "PostgreSQL 18 accepting connections"

# Apply schema and seed data
SQL_FILE="${SCRIPT_DIR}/populate-test-database.sql"
if [ ! -f "${SQL_FILE}" ]; then
    fail "SQL file not found: ${SQL_FILE}"
fi

log "Copying and applying schema + seed data (this takes ~30s)..."
sudo docker cp "${SQL_FILE}" "${CONTAINER_NAME}:/tmp/populate.sql"
sudo docker exec -e "PGPASSWORD=${PG_PASS}" "${CONTAINER_NAME}" \
    psql -U "${PG_USER}" -d "${PG_DB}" -f /tmp/populate.sql 2>&1 | tail -10

# Verify
PLAYER_COUNT="$(sudo docker exec -e "PGPASSWORD=${PG_PASS}" "${CONTAINER_NAME}" \
    psql -U "${PG_USER}" -d "${PG_DB}" -t -c "SELECT COUNT(*) FROM players;" 2>/dev/null | tr -d ' \n')"

if [ "${PLAYER_COUNT}" -ge 50000 ]; then
    pass "PostgreSQL 18 populated: ${PLAYER_COUNT} players"
else
    fail "Unexpected player count: ${PLAYER_COUNT}"
fi

log ""
log "Connection details:"
log "  Container: ${CONTAINER_NAME}"
log "  Host:      127.0.0.1:${PG_PORT}"
log "  User:      ${PG_USER}"
log "  Database:  ${PG_DB}"
log "  Password:  (in ${EVIDENCE_DIR}/pg18-test-password)"
log ""
log "Run TDE test:"
log "  BAO_TOKEN=<token> PG_CONTAINER=${CONTAINER_NAME} bash test-tde-postgresql.sh"
