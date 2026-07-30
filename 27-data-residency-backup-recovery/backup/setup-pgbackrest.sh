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
# pgBackRest One-Shot Setup Script
# =============================================================================
# Installs and configures pgBackRest with S3-compatible storage (Wasabi/MinIO),
# AES-256-CBC encryption, WAL archiving, stanza init, and systemd timers.
#
# Tested on: Ubuntu 24.04 LTS, PostgreSQL 16, pgBackRest 2.50
#
# Usage:
#   ./setup-pgbackrest.sh \
#     --s3-endpoint localhost:9000 \    # or s3.eu-central-1.wasabisys.com
#     --s3-bucket casino-backups \
#     --s3-key ACCESS_KEY \
#     --s3-secret SECRET_KEY \
#     --s3-region eu-central-1 \
#     --s3-ca-file /etc/pgbackrest/minio-ca.crt \   # omit for Wasabi
#     --stanza casino \
#     --pg-port 5434 \
#     --encrypt-pass "your-passphrase"  # omit to auto-generate
#
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
S3_ENDPOINT="s3.eu-central-1.wasabisys.com"
S3_BUCKET="casino-pgbackrest"
S3_KEY=""
S3_SECRET=""
S3_REGION="eu-central-1"
S3_PORT=""
S3_CA_FILE=""
S3_URI_STYLE="host"         # "path" for MinIO, "host" for Wasabi
STANZA="casino"
PG_PORT="5432"
PG_PATH="/var/lib/postgresql/16/main"
PG_USER="postgres"
ENCRYPT_PASS=""
RETENTION_FULL=7
RETENTION_DIFF=14
PROCESS_MAX=4

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --s3-endpoint) S3_ENDPOINT="$2"; shift 2 ;;
        --s3-bucket)   S3_BUCKET="$2";   shift 2 ;;
        --s3-key)      S3_KEY="$2";      shift 2 ;;
        --s3-secret)   S3_SECRET="$2";   shift 2 ;;
        --s3-region)   S3_REGION="$2";   shift 2 ;;
        --s3-port)     S3_PORT="$2";     shift 2 ;;
        --s3-ca-file)  S3_CA_FILE="$2";  shift 2 ;;
        --s3-uri-style) S3_URI_STYLE="$2"; shift 2 ;;
        --stanza)      STANZA="$2";      shift 2 ;;
        --pg-port)     PG_PORT="$2";     shift 2 ;;
        --pg-path)     PG_PATH="$2";     shift 2 ;;
        --encrypt-pass) ENCRYPT_PASS="$2"; shift 2 ;;
        *) echo "Unknown argument: $1"; exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
log() { echo "[$(date -u +%H:%M:%S)] $*"; }

check_root() {
    if [[ $EUID -ne 0 ]]; then
        echo "Run as root or with sudo"
        exit 1
    fi
}

# ---------------------------------------------------------------------------
# Step 1: Install pgBackRest
# ---------------------------------------------------------------------------
install_pgbackrest() {
    log "Installing pgBackRest..."
    apt-get update -q
    apt-get install -y pgbackrest
    log "pgBackRest version: $(pgbackrest version)"
}

# ---------------------------------------------------------------------------
# Step 2: Create directories
# ---------------------------------------------------------------------------
create_directories() {
    log "Creating directories..."
    mkdir -p /etc/pgbackrest
    mkdir -p /var/log/pgbackrest
    mkdir -p /var/spool/pgbackrest
    chown postgres:postgres /var/log/pgbackrest
    chown postgres:postgres /var/spool/pgbackrest
    chmod 750 /var/spool/pgbackrest
}

# ---------------------------------------------------------------------------
# Step 3: Generate or store encryption passphrase
# ---------------------------------------------------------------------------
setup_encryption() {
    if [[ -z "$ENCRYPT_PASS" ]]; then
        ENCRYPT_PASS=$(openssl rand -base64 32)
        log "Generated new encryption passphrase"
    else
        log "Using provided encryption passphrase"
    fi

    echo "$ENCRYPT_PASS" > /etc/pgbackrest/.cipher_pass
    chmod 600 /etc/pgbackrest/.cipher_pass
    chown postgres:postgres /etc/pgbackrest/.cipher_pass
    log "Passphrase stored at /etc/pgbackrest/.cipher_pass (keep this safe!)"
}

# ---------------------------------------------------------------------------
# Step 4: Write pgbackrest.conf
# ---------------------------------------------------------------------------
write_config() {
    log "Writing /etc/pgbackrest/pgbackrest.conf..."

    # Determine if using MinIO (local) or Wasabi (remote)
    S3_PORT_LINE=""
    if [[ -n "$S3_PORT" ]]; then
        S3_PORT_LINE="repo1-s3-port=${S3_PORT}"
    fi

    CA_FILE_LINE=""
    if [[ -n "$S3_CA_FILE" ]]; then
        CA_FILE_LINE="repo1-s3-ca-file=${S3_CA_FILE}"
    fi

    cat > /etc/pgbackrest/pgbackrest.conf << EOF
[global]
# S3-compatible repository (Wasabi or MinIO)
repo1-type=s3
repo1-path=/pgbackrest/${HOSTNAME}
repo1-s3-bucket=${S3_BUCKET}
repo1-s3-endpoint=${S3_ENDPOINT}
${S3_PORT_LINE}
repo1-s3-region=${S3_REGION}
repo1-s3-key=${S3_KEY}
repo1-s3-key-secret=${S3_SECRET}
repo1-s3-uri-style=${S3_URI_STYLE}
${CA_FILE_LINE}

# Encryption at rest (AES-256-CBC)
repo1-cipher-type=aes-256-cbc
repo1-cipher-pass=${ENCRYPT_PASS}

# Retention policy
repo1-retention-full=${RETENTION_FULL}
repo1-retention-diff=${RETENTION_DIFF}

# Performance
process-max=${PROCESS_MAX}
compress-type=zst
compress-level=3

# Logging
log-level-console=info
log-level-file=detail
log-path=/var/log/pgbackrest

[${STANZA}]
pg1-path=${PG_PATH}
pg1-port=${PG_PORT}
pg1-user=${PG_USER}
EOF

    # Remove empty lines from optional fields
    sed -i '/^$/d' /etc/pgbackrest/pgbackrest.conf
    chmod 640 /etc/pgbackrest/pgbackrest.conf
    chown postgres:postgres /etc/pgbackrest/pgbackrest.conf
    log "Config written"
}

# ---------------------------------------------------------------------------
# Step 5: Configure PostgreSQL for WAL archiving
# ---------------------------------------------------------------------------
configure_postgresql() {
    log "Configuring PostgreSQL WAL archiving..."

    local PG_CONF
    PG_CONF=$(sudo -u postgres psql -p "${PG_PORT}" -t -A -c 'SHOW config_file;' 2>/dev/null) || {
        log "Cannot connect to PostgreSQL on port ${PG_PORT}"
        exit 1
    }

    cp "${PG_CONF}" "${PG_CONF}.bak.$(date +%Y%m%d%H%M%S)"

    # Only update if not already set
    grep -q '^archive_mode' "$PG_CONF" || \
        sed -i 's/#archive_mode = off/archive_mode = on/' "$PG_CONF"
    sed -i 's/archive_mode = off/archive_mode = on/' "$PG_CONF"

    grep -q '^archive_command' "$PG_CONF" || \
        sed -i "s|#archive_command = ''|archive_command = 'pgbackrest --stanza=${STANZA} archive-push %p'|" "$PG_CONF"

    grep -q '^wal_level' "$PG_CONF" || \
        sed -i 's/#wal_level = replica/wal_level = replica/' "$PG_CONF"

    grep -q '^max_wal_senders' "$PG_CONF" || \
        sed -i 's/#max_wal_senders = 10/max_wal_senders = 10/' "$PG_CONF"

    log "Restarting PostgreSQL..."
    pg_ctlcluster "$(sudo -u postgres psql -p "${PG_PORT}" -t -A -c 'SHOW server_version;' | cut -d. -f1)" main restart || \
        systemctl restart postgresql

    sleep 5
    sudo -u postgres psql -p "${PG_PORT}" -c 'SHOW archive_mode; SHOW archive_command;' | grep -v '^$'
}

# ---------------------------------------------------------------------------
# Step 6: Create stanza and run initial backup
# ---------------------------------------------------------------------------
initialize_backups() {
    log "Creating pgBackRest stanza '${STANZA}'..."
    sudo -u postgres pgbackrest --stanza="${STANZA}" stanza-create

    log "Running stanza check (archives WAL)..."
    sudo -u postgres pgbackrest --stanza="${STANZA}" check

    log "Running initial full backup..."
    local BACKUP_START
    BACKUP_START=$(date +%s)
    sudo -u postgres pgbackrest --stanza="${STANZA}" --type=full backup
    local BACKUP_END
    BACKUP_END=$(date +%s)
    log "Full backup completed in $((BACKUP_END - BACKUP_START)) seconds"

    sudo -u postgres pgbackrest --stanza="${STANZA}" info
}

# ---------------------------------------------------------------------------
# Step 7: Install systemd timers
# ---------------------------------------------------------------------------
install_timers() {
    log "Installing systemd timers..."

    # Full backup service
    cat > /etc/systemd/system/pgbackrest-full.service << EOF
[Unit]
Description=pgBackRest Full Backup - ${STANZA}
After=postgresql.service

[Service]
Type=oneshot
User=postgres
ExecStart=/usr/bin/pgbackrest --stanza=${STANZA} --type=full backup
StandardOutput=journal
StandardError=journal
SyslogIdentifier=pgbackrest-full
EOF

    # Full backup timer (Sunday 03:00)
    cat > /etc/systemd/system/pgbackrest-full.timer << EOF
[Unit]
Description=pgBackRest Full Backup Timer

[Timer]
OnCalendar=Sun 03:00:00
RandomizedDelaySec=600
Persistent=true

[Install]
WantedBy=timers.target
EOF

    # Diff backup service
    cat > /etc/systemd/system/pgbackrest-diff.service << EOF
[Unit]
Description=pgBackRest Differential Backup - ${STANZA}
After=postgresql.service

[Service]
Type=oneshot
User=postgres
ExecStart=/usr/bin/pgbackrest --stanza=${STANZA} --type=diff backup
StandardOutput=journal
StandardError=journal
SyslogIdentifier=pgbackrest-diff
EOF

    # Diff backup timer (Mon-Sat 03:00)
    cat > /etc/systemd/system/pgbackrest-diff.timer << EOF
[Unit]
Description=pgBackRest Differential Backup Timer

[Timer]
OnCalendar=Mon-Sat 03:00:00
RandomizedDelaySec=300
Persistent=true

[Install]
WantedBy=timers.target
EOF

    # Verify service
    cat > /etc/systemd/system/pgbackrest-verify.service << EOF
[Unit]
Description=pgBackRest Backup Verification - ${STANZA}

[Service]
Type=oneshot
User=postgres
ExecStart=/usr/local/bin/backup-verify.sh
StandardOutput=journal
StandardError=journal
SyslogIdentifier=pgbackrest-verify
EOF

    # Verify timer (daily 05:00)
    cat > /etc/systemd/system/pgbackrest-verify.timer << EOF
[Unit]
Description=pgBackRest Verification Timer

[Timer]
OnCalendar=*-*-* 05:00:00
RandomizedDelaySec=300
Persistent=true

[Install]
WantedBy=timers.target
EOF

    systemctl daemon-reload
    systemctl enable pgbackrest-full.timer pgbackrest-diff.timer pgbackrest-verify.timer
    systemctl start pgbackrest-full.timer pgbackrest-diff.timer pgbackrest-verify.timer

    log "Timers enabled:"
    systemctl list-timers | grep pgbackrest
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    check_root

    log "=== pgBackRest Setup Starting ==="
    log "S3 endpoint: ${S3_ENDPOINT}"
    log "Bucket: ${S3_BUCKET}"
    log "Stanza: ${STANZA}"
    log "PostgreSQL port: ${PG_PORT}"

    install_pgbackrest
    create_directories
    setup_encryption
    write_config
    configure_postgresql
    initialize_backups
    install_timers

    log ""
    log "=== Setup Complete ==="
    log "Passphrase: /etc/pgbackrest/.cipher_pass (BACK THIS UP SECURELY)"
    log "Config:     /etc/pgbackrest/pgbackrest.conf"
    log "Logs:       /var/log/pgbackrest/"
    log ""
    log "Quick commands:"
    log "  pgbackrest --stanza=${STANZA} info              # List backups"
    log "  pgbackrest --stanza=${STANZA} --type=full backup  # Full backup"
    log "  pgbackrest --stanza=${STANZA} --delta restore    # Restore latest"
}

main "$@"
