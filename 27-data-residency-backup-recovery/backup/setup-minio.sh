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

# =============================================================================
# MinIO Local S3 Setup for pgBackRest Testing
# =============================================================================
# Deploys MinIO via Docker with TLS (self-signed cert), creates the backup
# bucket, installs the mc client, and prints credentials.
#
# This is the LOCAL TEST target — for production use Wasabi:
#   s3.eu-central-1.wasabisys.com
#
# Usage:
#   sudo ./setup-minio.sh [--bucket pgbackrest] [--port 9000] [--data-dir /data/minio]
# =============================================================================

set -euo pipefail

BUCKET="${BUCKET:-pgbackrest}"
PORT="${PORT:-9000}"
CONSOLE_PORT="${CONSOLE_PORT:-9002}"
DATA_DIR="${DATA_DIR:-/data/minio}"
CERT_DIR="${CERT_DIR:-/etc/minio/certs}"
MINIO_USER="${MINIO_USER:-minioadmin}"
MINIO_PASS="${MINIO_PASS:-$(openssl rand -base64 16)}"
CONTAINER_NAME="minio"

log() { echo "[$(date -u +%H:%M:%S)] $*"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --bucket)      BUCKET="$2";       shift 2 ;;
        --port)        PORT="$2";         shift 2 ;;
        --data-dir)    DATA_DIR="$2";     shift 2 ;;
        --minio-user)  MINIO_USER="$2";   shift 2 ;;
        --minio-pass)  MINIO_PASS="$2";   shift 2 ;;
        *) echo "Unknown: $1"; exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# 1. Check Docker
# ---------------------------------------------------------------------------
if ! command -v docker &>/dev/null; then
    log "Docker not found. Installing..."
    curl -fsSL https://get.docker.com | bash
fi

# ---------------------------------------------------------------------------
# 2. Generate TLS cert (pgBackRest requires HTTPS for S3)
# ---------------------------------------------------------------------------
log "Generating self-signed TLS certificate for MinIO..."
mkdir -p "${CERT_DIR}"

if [[ ! -f "${CERT_DIR}/public.crt" ]]; then
    openssl req -new -newkey rsa:2048 -days 3650 -nodes -x509 \
        -subj "/CN=localhost" \
        -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" \
        -keyout "${CERT_DIR}/private.key" \
        -out "${CERT_DIR}/public.crt" 2>/dev/null
    log "Certificate generated at ${CERT_DIR}/public.crt"
else
    log "Certificate already exists at ${CERT_DIR}/public.crt"
fi

# ---------------------------------------------------------------------------
# 3. Start MinIO
# ---------------------------------------------------------------------------
mkdir -p "${DATA_DIR}"
docker rm -f "${CONTAINER_NAME}" 2>/dev/null || true

log "Starting MinIO container (port ${PORT}, TLS)..."
docker run -d --name "${CONTAINER_NAME}" \
    --restart unless-stopped \
    -p "${PORT}:9000" \
    -p "${CONSOLE_PORT}:9001" \
    -e "MINIO_ROOT_USER=${MINIO_USER}" \
    -e "MINIO_ROOT_PASSWORD=${MINIO_PASS}" \
    -v "${DATA_DIR}:/data" \
    -v "${CERT_DIR}:/root/.minio/certs:ro" \
    minio/minio server /data --console-address ':9001'

sleep 5
curl -sk "https://localhost:${PORT}/minio/health/live" && log "MinIO is healthy" || {
    log "MinIO health check failed"
    docker logs "${CONTAINER_NAME}" | tail -20
    exit 1
}

# ---------------------------------------------------------------------------
# 4. Install mc and create bucket
# ---------------------------------------------------------------------------
if ! command -v mc &>/dev/null; then
    log "Installing mc (MinIO client)..."
    curl -sL https://dl.min.io/client/mc/release/linux-amd64/mc -o /usr/local/bin/mc
    chmod +x /usr/local/bin/mc
fi

log "Creating bucket: ${BUCKET}"
mc alias set local "https://localhost:${PORT}" "${MINIO_USER}" "${MINIO_PASS}" --insecure >/dev/null
mc mb --insecure "local/${BUCKET}" 2>/dev/null || log "Bucket already exists"

# ---------------------------------------------------------------------------
# 5. Save pgBackRest CA cert
# ---------------------------------------------------------------------------
PGBACKREST_CA="/etc/pgbackrest/minio-ca.crt"
mkdir -p /etc/pgbackrest
cp "${CERT_DIR}/public.crt" "${PGBACKREST_CA}"
if id postgres &>/dev/null; then
    chown postgres:postgres "${PGBACKREST_CA}"
fi
log "CA cert copied to ${PGBACKREST_CA}"

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
log ""
log "=== MinIO Setup Complete ==="
log "  Endpoint:     https://localhost:${PORT}"
log "  Console:      http://localhost:${CONSOLE_PORT}"
log "  Bucket:       ${BUCKET}"
log "  Access key:   ${MINIO_USER}"
log "  Secret key:   ${MINIO_PASS}"
log "  CA cert:      ${PGBACKREST_CA}"
log ""
log "pgBackRest config for this MinIO instance:"
log "  repo1-s3-endpoint=localhost"
log "  repo1-s3-port=${PORT}"
log "  repo1-s3-bucket=${BUCKET}"
log "  repo1-s3-key=${MINIO_USER}"
log "  repo1-s3-key-secret=${MINIO_PASS}"
log "  repo1-s3-region=us-east-1"
log "  repo1-s3-uri-style=path"
log "  repo1-s3-ca-file=${PGBACKREST_CA}"
