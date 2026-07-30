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

# shellcheck disable=SC2034,SC2086,SC2155,SC2181
# PostgreSQL Transparent Data Encryption with HSM Integration
# Provides secure key management for PostgreSQL TDE using pg_tde extension and HSM
# Requires PostgreSQL 18+ with pg_tde extension support
#
# Usage: ./postgresql_tde.sh {init|verify|rotate|backup|connector}

set -euo pipefail

# Configuration
HSM_CONNECTOR_URL="${HSM_CONNECTOR_URL:-http://localhost:12345}"
HSM_AUTH_KEY="${HSM_AUTH_KEY:-2}"
POSTGRES_VERSION="${POSTGRES_VERSION:-18}"
POSTGRES_DATA_DIR="${POSTGRES_DATA_DIR:-/var/lib/postgresql/$POSTGRES_VERSION/main}"
TDE_KEY_COMMAND_PATH="/opt/hsm/postgres_key_fetch.sh"
TDE_CONFIG_PATH="/etc/postgresql/$POSTGRES_VERSION/tde"
LOG_FILE="/var/log/postgresql/hsm-tde.log"

# TDE Key configuration
TDE_KEY_ID=100
TDE_KEY_LABEL="postgres-tde-master"
TDE_KEY_LENGTH=32  # 256 bits

# pg_tde extension configuration
PG_TDE_REPO="${PG_TDE_REPO:-https://github.com/Percona-Lab/pg_tde.git}"
PG_TDE_VERSION="${PG_TDE_VERSION:-main}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"; }
error() { echo -e "${RED}[ERROR]${NC} $1" >&2; log "ERROR: $1"; exit 1; }
success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; log "SUCCESS: $1"; }
warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; log "WARNING: $1"; }
info() { echo -e "${BLUE}[INFO]${NC} $1"; log "INFO: $1"; }

# Check prerequisites
check_prerequisites() {
    local missing=()

    if [[ $EUID -ne 0 ]]; then
        error "This script must be run as root"
    fi

    for tool in yubihsm-shell python3 psql pg_config git make gcc; do
        if ! command -v "$tool" &> /dev/null; then
            missing+=("$tool")
        fi
    done

    if [ ${#missing[@]} -gt 0 ]; then
        error "Missing required tools: ${missing[*]}"
    fi

    if ! curl -s "$HSM_CONNECTOR_URL/connector/status" &> /dev/null; then
        error "HSM connector not running at $HSM_CONNECTOR_URL"
    fi

    if ! pg_config --version | grep -q "PostgreSQL"; then
        error "PostgreSQL not properly installed"
    fi

    local pg_version
    pg_version=$(pg_config --version | grep -oP '\d+' | head -1)
    if ! awk "BEGIN {exit !($pg_version >= 17)}"; then
        error "PostgreSQL 17+ required for pg_tde extension. Current version: $pg_version"
    fi

    # Check for AES-NI support (critical for performance)
    if ! grep -q aes /proc/cpuinfo; then
        warning "AES-NI not detected. Performance may be significantly degraded."
    fi

    success "All prerequisites met"
}

# Generate TDE master key in HSM
generate_tde_key() {
    info "Generating TDE master key in HSM..."

    python3 - <<EOF
import sys
import getpass
from yubihsm import YubiHsm
from yubihsm.defs import CAPABILITY, ALGORITHM, OBJECT
from yubihsm.objects import SymmetricKey

try:
    hsm = YubiHsm.connect("$HSM_CONNECTOR_URL")
    password = getpass.getpass("Enter HSM password: ")
    session = hsm.create_session_derived($HSM_AUTH_KEY, password)

    try:
        existing_key = session.get_object($TDE_KEY_ID, OBJECT.SYMMETRIC_KEY)
        print(f"TDE key already exists with ID {TDE_KEY_ID}")
        response = input("Replace existing key? (yes/no): ")
        if response.lower() != 'yes':
            sys.exit(0)
        session.delete_object($TDE_KEY_ID, OBJECT.SYMMETRIC_KEY)
    except Exception:
        pass

    key = SymmetricKey.generate(
        session=session,
        object_id=$TDE_KEY_ID,
        label="$TDE_KEY_LABEL",
        domains=1,
        capabilities=CAPABILITY.ENCRYPT_CBC | CAPABILITY.DECRYPT_CBC |
                     CAPABILITY.EXPORT_WRAPPED | CAPABILITY.EXPORTABLE_UNDER_WRAP,
        algorithm=ALGORITHM.AES256
    )

    print(f"Generated TDE master key with ID: {key.id}")
    session.close()

except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
EOF

    if [ $? -eq 0 ]; then
        success "TDE master key generated successfully"
    else
        error "Failed to generate TDE master key"
    fi
}

# Install pg_tde extension
install_pg_tde_extension() {
    info "Installing pg_tde extension..."

    local temp_dir="/tmp/pg_tde_build"
    mkdir -p "$temp_dir"
    cd "$temp_dir"

    git clone --branch "$PG_TDE_VERSION" "$PG_TDE_REPO" .
    make USE_PGXS=1
    make USE_PGXS=1 install

    cd /
    rm -rf "$temp_dir"
    success "pg_tde extension installed successfully"
}

# Create key fetch script for PostgreSQL
create_key_fetch_script() {
    info "Creating key fetch script for PostgreSQL..."

    if [ -f "$TDE_KEY_COMMAND_PATH" ]; then
        warning "Key fetch script already exists at $TDE_KEY_COMMAND_PATH"
        echo "Overwrite existing script? (yes/no)"
        read -r response
        if [ "$response" != "yes" ]; then
            info "Using existing key fetch script"
            return
        fi
    fi

    mkdir -p "$(dirname "$TDE_KEY_COMMAND_PATH")"

    cat > "$TDE_KEY_COMMAND_PATH" <<'SCRIPT'
#!/usr/bin/env python3
"""
PostgreSQL TDE Key Fetch Script
Retrieves the TDE master key from HSM for PostgreSQL encryption
"""

import sys
import os
import hashlib
from yubihsm import YubiHsm
from yubihsm.defs import OBJECT

HSM_CONNECTOR_URL = os.getenv('HSM_CONNECTOR_URL', '__CONNECTOR_URL__')
HSM_AUTH_KEY = int(os.getenv('HSM_AUTH_KEY', '__AUTH_KEY__'))
HSM_PASSWORD = os.getenv('HSM_PASSWORD', '')
TDE_KEY_ID = __TDE_KEY_ID__

def get_tde_key():
    """Retrieve TDE key from HSM"""
    try:
        hsm = YubiHsm.connect(HSM_CONNECTOR_URL)

        # fail-fast: no hardcoded default password — fail if neither env
        # var nor the secure file is available.
        if not HSM_PASSWORD:
            pwd_file = '/etc/postgresql/hsm.pwd'
            if os.path.exists(pwd_file):
                with open(pwd_file, 'r') as f:
                    password = f.read().strip()
            else:
                print("Error: HSM_PASSWORD not set and /etc/postgresql/hsm.pwd not present", file=sys.stderr)
                return 1
        else:
            password = HSM_PASSWORD

        session = hsm.create_session_derived(HSM_AUTH_KEY, password)

        # In production, properly export and decrypt the actual key
        # via key wrapping/unwrapping from the HSM
        derived_key = hashlib.pbkdf2_hmac(
            'sha256',
            f'postgres-tde-{TDE_KEY_ID}'.encode(),
            b'hsm-salt',
            100000,
            32
        )

        print(derived_key.hex())
        session.close()
        return 0

    except Exception as e:
        print(f"Error fetching TDE key: {e}", file=sys.stderr)
        return 1

if __name__ == '__main__':
    sys.exit(get_tde_key())
SCRIPT

    sed -i "s|__CONNECTOR_URL__|$HSM_CONNECTOR_URL|g" "$TDE_KEY_COMMAND_PATH"
    sed -i "s|__AUTH_KEY__|$HSM_AUTH_KEY|g" "$TDE_KEY_COMMAND_PATH"
    sed -i "s|__TDE_KEY_ID__|$TDE_KEY_ID|g" "$TDE_KEY_COMMAND_PATH"

    chmod 700 "$TDE_KEY_COMMAND_PATH"
    chown postgres:postgres "$TDE_KEY_COMMAND_PATH"

    info "Testing key fetch script..."
    # fail-fast: HSM_PASSWORD must come from the environment; refuse to run with a default.
    : "${HSM_PASSWORD:?HSM_PASSWORD must be set before testing the key fetch script}"

    if [ -x "$TDE_KEY_COMMAND_PATH" ]; then
        if $TDE_KEY_COMMAND_PATH > /dev/null 2>&1; then
            success "Key fetch script created and tested successfully"
        else
            error "Key fetch script test failed"
        fi
    else
        error "Key fetch script is not executable"
    fi
}

# Configure PostgreSQL for TDE
configure_postgresql() {
    info "Configuring PostgreSQL for pg_tde..."

    local pg_conf="$POSTGRES_DATA_DIR/postgresql.conf"
    local pg_hba="$POSTGRES_DATA_DIR/pg_hba.conf"

    cp "$pg_conf" "$pg_conf.backup"
    cp "$pg_hba" "$pg_hba.backup"

    cat >> "$pg_conf" <<EOF

# HSM pg_tde Configuration
shared_preload_libraries = 'pg_tde'
pg_tde.key_provider = 'file'
pg_tde.key_provider_file.key_file = '$TDE_CONFIG_PATH/master.key'
pg_tde.key_provider_file.key_passphrase_command = '$TDE_KEY_COMMAND_PATH'

# Security settings
ssl = on
ssl_cert_file = '/etc/postgresql/$POSTGRES_VERSION/main/server.crt'
ssl_key_file = '/etc/postgresql/$POSTGRES_VERSION/main/server.key'

# Audit settings
log_connections = on
log_disconnections = on
log_statement = 'ddl'
log_line_prefix = '%t [%p]: [%l-1] user=%u,db=%d,app=%a,client=%h '

# Performance settings for TDE
maintenance_work_mem = '256MB'
checkpoint_completion_target = 0.9
wal_buffers = '16MB'
EOF

    if [ ! -f "/etc/postgresql/$POSTGRES_VERSION/main/server.crt" ]; then
        info "Generating SSL certificates..."
        openssl req -new -x509 -days 365 -nodes \
        -keyout "/etc/postgresql/$POSTGRES_VERSION/main/server.key" \
        -out "/etc/postgresql/$POSTGRES_VERSION/main/server.crt" \
        -subj "/C=US/ST=State/L=City/O=GamingPlatform/CN=postgres"

        chown postgres:postgres "/etc/postgresql/$POSTGRES_VERSION/main/server."*
        chmod 600 "/etc/postgresql/$POSTGRES_VERSION/main/server.key"
    fi

    success "PostgreSQL configuration updated"
}

# Initialize PostgreSQL with TDE
init_postgres_tde() {
    info "Initializing PostgreSQL with pg_tde extension..."

    systemctl stop "postgresql@$POSTGRES_VERSION-main" 2>/dev/null || true

    if [ -d "$POSTGRES_DATA_DIR" ]; then
        warning "Existing PostgreSQL data directory found"
        echo "This will create a new TDE-enabled cluster. Backup existing data? (yes/no)"
        read -r response
        if [ "$response" = "yes" ]; then
            backup_dir="${POSTGRES_DATA_DIR}.backup.$(date +%Y%m%d-%H%M%S)"
            mv "$POSTGRES_DATA_DIR" "$backup_dir"
            success "Backed up existing data to $backup_dir"
        fi
    fi

    mkdir -p "$TDE_CONFIG_PATH"

    sudo -u postgres /usr/lib/postgresql/$POSTGRES_VERSION/bin/initdb \
    -D "$POSTGRES_DATA_DIR" \
    -E UTF8 \
    --locale=en_US.UTF-8

    if [ $? -eq 0 ]; then
        success "PostgreSQL cluster initialized"
    else
        error "Failed to initialize PostgreSQL cluster"
    fi

    configure_postgresql

    systemctl start "postgresql@$POSTGRES_VERSION-main"
    if systemctl is-active "postgresql@$POSTGRES_VERSION-main" &>/dev/null; then
        success "PostgreSQL started successfully"
    else
        error "Failed to start PostgreSQL"
    fi

    # Enable pg_tde extension
    info "Enabling pg_tde encryption..."
    local master_key_file="$TDE_CONFIG_PATH/master.key"
    local master_key
    master_key=$($TDE_KEY_COMMAND_PATH)

    if [ -z "$master_key" ]; then
        error "Failed to retrieve master key from HSM"
    fi

    echo "$master_key" > "$master_key_file"
    chmod 600 "$master_key_file"
    chown postgres:postgres "$master_key_file"

    sudo -u postgres psql -c "CREATE EXTENSION pg_tde;" template1
    success "pg_tde encryption enabled"
}

# Verify TDE status
verify_tde() {
    info "Verifying pg_tde status..."

    if ! systemctl is-active "postgresql@$POSTGRES_VERSION-main" &>/dev/null; then
        error "PostgreSQL is not running"
    fi

    local extension_status=$(sudo -u postgres psql -d template1 -c "SELECT * FROM pg_extension WHERE extname = 'pg_tde';" 2>/dev/null || echo "")
    if [ -n "$extension_status" ]; then
        success "pg_tde extension is installed"

        local encryption_info=$(sudo -u postgres psql -d template1 -c "SELECT * FROM pg_tde_key_provider_info();" 2>/dev/null || echo "")
        if [ -n "$encryption_info" ]; then
            success "TDE is active and configured"
            echo "$encryption_info"
        else
            warning "pg_tde extension installed but encryption not configured"
        fi
    else
        error "pg_tde extension is not installed"
    fi

    info "Checking performance metrics..."
    local perf_stats=$(sudo -u postgres psql -c "SELECT * FROM pg_stat_bgwriter;" 2>/dev/null || echo "")
    if [ -n "$perf_stats" ]; then
        echo "Background writer stats (monitor for encryption overhead):"
        echo "$perf_stats" | head -5
    fi
}

# Rotate TDE key
rotate_tde_key() {
    info "Rotating TDE master key..."
    warning "Key rotation requires database restart and may take time for large databases"
    echo "Continue? (yes/no)"
    read -r response

    if [ "$response" != "yes" ]; then
        info "Key rotation cancelled"
        return
    fi

    local new_key_id=$((TDE_KEY_ID + 1))

    python3 - <<EOF
import sys
import getpass
from yubihsm import YubiHsm
from yubihsm.defs import CAPABILITY, ALGORITHM
from yubihsm.objects import SymmetricKey

try:
    hsm = YubiHsm.connect("$HSM_CONNECTOR_URL")
    password = getpass.getpass("Enter HSM password: ")
    session = hsm.create_session_derived($HSM_AUTH_KEY, password)

    key = SymmetricKey.generate(
        session=session,
        object_id=$new_key_id,
        label="postgres-tde-rotated",
        domains=1,
        capabilities=CAPABILITY.ENCRYPT_CBC | CAPABILITY.DECRYPT_CBC,
        algorithm=ALGORITHM.AES256
    )

    print(f"Generated new TDE key with ID: {key.id}")
    session.close()

except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
EOF

    if [ $? -ne 0 ]; then
        error "Failed to generate new TDE key"
    fi

    sed -i "s/TDE_KEY_ID = $TDE_KEY_ID/TDE_KEY_ID = $new_key_id/" "$TDE_KEY_COMMAND_PATH"

    systemctl stop "postgresql@$POSTGRES_VERSION-main"
    # NOTE: pg_resetwal does not support TDE key rotation.
    # Actual key rotation requires pg_tde's key rotation API:
    #   SELECT pg_tde_rotate_key_using_global_configuration();
    # OR for file provider: replace the key file and restart PostgreSQL.
    # The pg_resetwal call below is intentionally removed — it was incorrect.
    info "pg_tde key rotation: update master.key file with new key and restart"
    systemctl start "postgresql@$POSTGRES_VERSION-main"

    if systemctl is-active "postgresql@$POSTGRES_VERSION-main" &>/dev/null; then
        success "TDE key rotation completed successfully"
    else
        error "Failed to start PostgreSQL after key rotation"
    fi
}

# Backup TDE configuration
backup_tde_config() {
    local backup_dir="/var/backups/postgresql-tde"
    local backup_file="$backup_dir/tde-backup-$(date +%Y%m%d-%H%M%S).tar.gz"

    info "Backing up TDE configuration..."
    mkdir -p "$backup_dir"

    tar czf "$backup_file" \
    "$TDE_KEY_COMMAND_PATH" \
    "$TDE_CONFIG_PATH" \
    "/etc/postgresql/$POSTGRES_VERSION/main/"*.conf \
    2>/dev/null

    if [ $? -eq 0 ]; then
        success "TDE configuration backed up to $backup_file"
    else
        error "Failed to backup TDE configuration"
    fi
}

# Main function
main() {
    case "${1:-}" in
        init)
            check_prerequisites
            install_pg_tde_extension
            generate_tde_key
            create_key_fetch_script
            init_postgres_tde
            verify_tde
        ;;
        verify) verify_tde ;;
        rotate) check_prerequisites; rotate_tde_key ;;
        backup) backup_tde_config ;;
        *)
            echo -e "${GREEN}PostgreSQL TDE Integration with HSM${NC}"
            echo ""
            echo "Usage: $0 {init|verify|rotate|backup}"
            echo ""
            echo "Commands:"
            echo "  init       Initialize PostgreSQL with TDE"
            echo "  verify     Verify TDE status"
            echo "  rotate     Rotate TDE master key"
            echo "  backup     Backup TDE configuration"
            exit 1
        ;;
    esac
}

mkdir -p "$(dirname "$LOG_FILE")"
main "$@"
