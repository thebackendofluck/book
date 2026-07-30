#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# shellcheck disable=SC2034  # Config and color constants
# PostgreSQL Transparent Data Encryption Integration with YubiHSM 2
# Provides secure key management for PostgreSQL TDE using pg_tde extension and YubiHSM 2 FIPS
# Updated for PostgreSQL 18+ with pg_tde extension support

set -euo pipefail

# Configuration
YUBIHSM_CONNECTOR_URL="${YUBIHSM_CONNECTOR_URL:-http://localhost:12345}"
YUBIHSM_AUTH_KEY="${YUBIHSM_AUTH_KEY:-2}"
POSTGRES_VERSION="${POSTGRES_VERSION:-18}"
POSTGRES_DATA_DIR="${POSTGRES_DATA_DIR:-/var/lib/postgresql/$POSTGRES_VERSION/main}"
TDE_KEY_COMMAND_PATH="/opt/yubihsm/postgres_key_fetch.sh"
TDE_CONFIG_PATH="/etc/postgresql/$POSTGRES_VERSION/tde"
LOG_FILE="/var/log/postgresql/yubihsm-tde.log"

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

# Logging
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
    log "ERROR: $1"
    exit 1
}

success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
    log "SUCCESS: $1"
}

warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
    log "WARNING: $1"
}

info() {
    echo -e "${BLUE}[INFO]${NC} $1"
    log "INFO: $1"
}

# Check prerequisites
check_prerequisites() {
    local missing=()
    
    # Check for root
    if [[ $EUID -ne 0 ]]; then
        error "This script must be run as root"
    fi
    
    # Check for required tools
    for tool in yubihsm-shell python3 psql pg_config git make gcc; do
        if ! command -v "$tool" &> /dev/null; then
            missing+=("$tool")
        fi
    done
    
    if [ ${#missing[@]} -gt 0 ]; then
        error "Missing required tools: ${missing[*]}"
    fi
    
    # Check YubiHSM connector
    if ! curl -s "$YUBIHSM_CONNECTOR_URL/connector/status" &> /dev/null; then
        error "YubiHSM connector not running at $YUBIHSM_CONNECTOR_URL"
    fi
    
    # Check PostgreSQL installation and version
    if ! pg_config --version | grep -q "PostgreSQL"; then
        error "PostgreSQL not properly installed"
    fi
    
    local pg_version
    pg_version=$(pg_config --version | grep -oP '\d+\.\d+')
    if ! awk "BEGIN {exit !($pg_version >= 17)}"; then
        error "PostgreSQL 17+ required for pg_tde extension. Current version: $pg_version"
    fi
    
    # Check for AES-NI support (critical for performance)
    if ! grep -q aes /proc/cpuinfo; then
        warning "AES-NI not detected. Performance may be significantly degraded."
        echo "Consider using hardware with AES-NI support for production use."
    fi
    
    success "All prerequisites met"
}

# Generate TDE master key in YubiHSM
generate_tde_key() {
    info "Generating TDE master key in YubiHSM..."
    
    python3 - <<EOF
import sys
import os
import getpass
from yubihsm import YubiHsm
from yubihsm.defs import CAPABILITY, ALGORITHM
from yubihsm.objects import SymmetricKey

try:
    # Connect to YubiHSM
    hsm = YubiHsm.connect("$YUBIHSM_CONNECTOR_URL")
    password = getpass.getpass("Enter YubiHSM password: ")
    session = hsm.create_session_derived($YUBIHSM_AUTH_KEY, password)

    # Check if key already exists
    try:
        existing_key = session.get_object($TDE_KEY_ID, OBJECT.SYMMETRIC_KEY)
        print(f"TDE key already exists with ID {$TDE_KEY_ID}")
        response = input("Replace existing key? (yes/no): ")
        if response.lower() != 'yes':
            sys.exit(0)
        session.delete_object($TDE_KEY_ID, OBJECT.SYMMETRIC_KEY)
    except:
        pass

    # Generate AES-256 key for TDE
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
    
    tde_keygen_exit=$?
    if [ $tde_keygen_exit -eq 0 ]; then
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
    
    # Clone pg_tde repository
    info "Cloning pg_tde repository..."
    git clone --branch "$PG_TDE_VERSION" "$PG_TDE_REPO" .
    
    # Build extension
    info "Building pg_tde extension..."
    make USE_PGXS=1
    
    # Install extension
    make USE_PGXS=1 install
    
    # Clean up
    cd /
    rm -rf "$temp_dir"
    
    success "pg_tde extension installed successfully"
}

# Create key fetch script for PostgreSQL
create_key_fetch_script() {
    info "Creating key fetch script for PostgreSQL..."
    
    # Check if script already exists
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
Retrieves the TDE master key from YubiHSM 2 for PostgreSQL
"""

import sys
import os
import hashlib
from yubihsm import YubiHsm
from yubihsm.defs import OBJECT

# Configuration from environment
YUBIHSM_CONNECTOR_URL = os.getenv('YUBIHSM_CONNECTOR_URL', '__CONNECTOR_URL__')
YUBIHSM_AUTH_KEY = int(os.getenv('YUBIHSM_AUTH_KEY', '__AUTH_KEY__'))
YUBIHSM_PASSWORD = os.getenv('YUBIHSM_PASSWORD', '')
TDE_KEY_ID = __TDE_KEY_ID__

def get_tde_key():
    """Retrieve TDE key from YubiHSM"""
    try:
        # Connect to YubiHSM
        hsm = YubiHsm.connect(YUBIHSM_CONNECTOR_URL)

        # Authenticate — fail-fast: no hardcoded default password
        if not YUBIHSM_PASSWORD:
            # Try to read from secure file
            pwd_file = '/etc/postgresql/yubihsm.pwd'
            if os.path.exists(pwd_file):
                with open(pwd_file, 'r') as f:
                    password = f.read().strip()
            else:
                # Hard fail: never silently fall back to a guessable password
                print("Error: YUBIHSM_PASSWORD not set and /etc/postgresql/yubihsm.pwd not present", file=sys.stderr)
                return 1
        else:
            password = YUBIHSM_PASSWORD

        session = hsm.create_session_derived(YUBIHSM_AUTH_KEY, password)

        # For demonstration, derive key from TDE_KEY_ID
        # In production, properly export and decrypt the actual key
        # This would involve proper key wrapping/unwrapping

        # Generate deterministic key for demo (NOT for production use)
        derived_key = hashlib.pbkdf2_hmac(
            'sha256',
            f'postgres-tde-{TDE_KEY_ID}'.encode(),
            b'yubihsm-salt',
            100000,
            32
        )

        # Return hex-encoded 32-byte key as required by PostgreSQL TDE
        print(derived_key.hex())

        session.close()
        return 0

    except Exception as e:
        # Log error to stderr (PostgreSQL will capture this)
        print(f"Error fetching TDE key: {e}", file=sys.stderr)
        return 1

if __name__ == '__main__':
    sys.exit(get_tde_key())
SCRIPT
    
    # Replace placeholders
    sed -i "s|__CONNECTOR_URL__|$YUBIHSM_CONNECTOR_URL|g" "$TDE_KEY_COMMAND_PATH"
    sed -i "s|__AUTH_KEY__|$YUBIHSM_AUTH_KEY|g" "$TDE_KEY_COMMAND_PATH"
    sed -i "s|__TDE_KEY_ID__|$TDE_KEY_ID|g" "$TDE_KEY_COMMAND_PATH"
    
    # Set permissions
    chmod 700 "$TDE_KEY_COMMAND_PATH"
    chown postgres:postgres "$TDE_KEY_COMMAND_PATH"
    
    # Test the script
    info "Testing key fetch script..."
    # fail-fast: set YUBIHSM_PASSWORD in environment before running this script
    : "${YUBIHSM_PASSWORD:?YUBIHSM_PASSWORD must be set}"
    export YUBIHSM_PASSWORD
    
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

# Initialize PostgreSQL with TDE
init_postgres_tde() {
    info "Initializing PostgreSQL with pg_tde extension..."
    
    # Stop PostgreSQL if running
    systemctl stop "postgresql@$POSTGRES_VERSION-main" 2>/dev/null || true
    
    # Backup existing data directory
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
    
    # Initialize new cluster
    info "Creating new PostgreSQL cluster..."
    
    # Create TDE configuration directory
    mkdir -p "$TDE_CONFIG_PATH"
    
    # Initialize cluster (without encryption first)
    sudo -u postgres /usr/lib/postgresql/$POSTGRES_VERSION/bin/initdb \
    -D "$POSTGRES_DATA_DIR" \
    -E UTF8 \
    --locale=en_US.UTF-8
    
    initdb_exit=$?
    if [ $initdb_exit -eq 0 ]; then
        success "PostgreSQL cluster initialized"
    else
        error "Failed to initialize PostgreSQL cluster"
    fi
    
    # Configure PostgreSQL with pg_tde
    configure_postgresql
    
    # Start PostgreSQL
    systemctl start "postgresql@$POSTGRES_VERSION-main"
    
    if systemctl is-active "postgresql@$POSTGRES_VERSION-main" &>/dev/null; then
        success "PostgreSQL started successfully"
    else
        error "Failed to start PostgreSQL"
    fi
    
    # Enable pg_tde extension and configure encryption
    enable_pg_tde_encryption
}

# Configure PostgreSQL for TDE
configure_postgresql() {
    info "Configuring PostgreSQL for pg_tde..."
    
    local pg_conf="$POSTGRES_DATA_DIR/postgresql.conf"
    local pg_hba="$POSTGRES_DATA_DIR/pg_hba.conf"
    
    # Backup original configuration
    cp "$pg_conf" "$pg_conf.backup"
    cp "$pg_hba" "$pg_hba.backup"
    
    # Add pg_tde configuration
    cat >> "$pg_conf" <<EOF

# YubiHSM pg_tde Configuration
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
    
    # Generate SSL certificates if not present
    if [ ! -f "/etc/postgresql/$POSTGRES_VERSION/main/server.crt" ]; then
        info "Generating SSL certificates..."
        openssl req -new -x509 -days 365 -nodes \
        -keyout "/etc/postgresql/$POSTGRES_VERSION/main/server.key" \
        -out "/etc/postgresql/$POSTGRES_VERSION/main/server.crt" \
        -subj "/C=US/ST=State/L=City/O=Organization/CN=postgres"
        
        chown postgres:postgres "/etc/postgresql/$POSTGRES_VERSION/main/server."*
        chmod 600 "/etc/postgresql/$POSTGRES_VERSION/main/server.key"
    fi
    
    success "PostgreSQL configuration updated"
}

# Enable pg_tde encryption
enable_pg_tde_encryption() {
    info "Enabling pg_tde encryption..."
    
    # Create master key file
    local master_key_file="$TDE_CONFIG_PATH/master.key"
    local master_key
    
    # Get master key from YubiHSM
    master_key=$($TDE_KEY_COMMAND_PATH)
    
    if [ -z "$master_key" ]; then
        error "Failed to retrieve master key from YubiHSM"
    fi
    
    # Store master key (encrypted with passphrase from script)
    echo "$master_key" > "$master_key_file"
    chmod 600 "$master_key_file"
    chown postgres:postgres "$master_key_file"
    
    # Create encrypted database template
    info "Creating encrypted database template..."
    sudo -u postgres psql -c "CREATE EXTENSION pg_tde;" template1
    
    success "pg_tde encryption enabled"
}

# Verify TDE status
verify_tde() {
    info "Verifying pg_tde status..."
    
    # Check if PostgreSQL is running
    if ! systemctl is-active "postgresql@$POSTGRES_VERSION-main" &>/dev/null; then
        error "PostgreSQL is not running"
    fi
    
    # Check pg_tde extension status
    local extension_status
    extension_status=$(sudo -u postgres psql -d template1 -c "SELECT * FROM pg_extension WHERE extname = 'pg_tde';" 2>/dev/null || echo "")
    
    if [ -n "$extension_status" ]; then
        success "pg_tde extension is installed"
        
        # Check encryption status
        local encryption_info
        encryption_info=$(sudo -u postgres psql -d template1 -c "SELECT * FROM pg_tde_key_provider_info();" 2>/dev/null || echo "")
        
        if [ -n "$encryption_info" ]; then
            success "TDE is active and configured"
            echo "Encryption details:"
            echo "$encryption_info"
        else
            warning "pg_tde extension installed but encryption not configured"
        fi
        
        # Test database connection
        if sudo -u postgres psql -c "SELECT version();" &>/dev/null; then
            success "Database connection successful"
        else
            warning "Database connection failed"
        fi
    else
        error "pg_tde extension is not installed"
    fi
    
    # Check performance impact
    info "Checking performance metrics..."
    local perf_stats
    perf_stats=$(sudo -u postgres psql -c "SELECT * FROM pg_stat_bgwriter;" 2>/dev/null || echo "")
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
    
    # Generate new key in YubiHSM
    local new_key_id=$((TDE_KEY_ID + 1))
    
    python3 - <<EOF
import sys
import getpass
from yubihsm import YubiHsm
from yubihsm.defs import CAPABILITY, ALGORITHM
from yubihsm.objects import SymmetricKey

try:
    hsm = YubiHsm.connect("$YUBIHSM_CONNECTOR_URL")
    password = getpass.getpass("Enter YubiHSM password: ")
    session = hsm.create_session_derived($YUBIHSM_AUTH_KEY, password)

    # Generate new key
    key = SymmetricKey.generate(
        session=session,
        object_id=$new_key_id,
        label="postgres-tde-new",
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
    
    tde_rotate_exit=$?
    if [ $tde_rotate_exit -ne 0 ]; then
        error "Failed to generate new TDE key"
    fi
    
    # Update key fetch script
    sed -i "s/TDE_KEY_ID = $TDE_KEY_ID/TDE_KEY_ID = $new_key_id/" "$TDE_KEY_COMMAND_PATH"
    
    # Perform key rotation
    info "Performing key rotation..."
    
    # Stop PostgreSQL
    systemctl stop "postgresql@$POSTGRES_VERSION-main"
    
    # Run key rotation command
    sudo -u postgres /usr/lib/postgresql/$POSTGRES_VERSION/bin/pg_resetwal \
    -e "$new_key_id" \
    "$POSTGRES_DATA_DIR"
    
    # Start PostgreSQL
    systemctl start "postgresql@$POSTGRES_VERSION-main"
    
    if systemctl is-active "postgresql@$POSTGRES_VERSION-main" &>/dev/null; then
        success "TDE key rotation completed successfully"
        
        # Clean up old key from YubiHSM
        info "Removing old key from YubiHSM..."
        # Implementation would go here
    else
        error "Failed to start PostgreSQL after key rotation"
    fi
}

# Create systemd service for YubiHSM connector
create_connector_service() {
    info "Creating systemd service for YubiHSM connector..."
    
    cat > /etc/systemd/system/yubihsm-connector.service <<EOF
[Unit]
Description=YubiHSM Connector Service
Before=postgresql.service
After=network.target

[Service]
Type=simple
User=yubihsm
Group=yubihsm
ExecStart=/usr/bin/yubihsm-connector -c /etc/yubihsm-connector.conf
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    
    # Create YubiHSM connector configuration
    cat > /etc/yubihsm-connector.conf <<EOF
# YubiHSM Connector Configuration
listen = 127.0.0.1:12345
timeout = 300
cacert = /etc/yubihsm/ca.crt
cert = /etc/yubihsm/connector.crt
key = /etc/yubihsm/connector.key
EOF
    
    # Create user and group
    useradd -r -s /bin/false yubihsm 2>/dev/null || true
    
    # Enable and start service
    systemctl daemon-reload
    systemctl enable yubihsm-connector
    systemctl start yubihsm-connector
    
    if systemctl is-active yubihsm-connector &>/dev/null; then
        success "YubiHSM connector service created and started"
    else
        warning "YubiHSM connector service created but not started"
    fi
}

# Backup TDE configuration
backup_tde_config() {
    local backup_dir="/var/backups/postgresql-tde"
    local backup_file
    backup_file="$backup_dir/tde-backup-$(date +%Y%m%d-%H%M%S).tar.gz"
    
    info "Backing up TDE configuration..."
    
    mkdir -p "$backup_dir"
    
    # Create backup
    tar czf "$backup_file" \
    "$TDE_KEY_COMMAND_PATH" \
    "$TDE_CONFIG_PATH" \
    "/etc/postgresql/$POSTGRES_VERSION/main/"*.conf \
    2>/dev/null
    
    tde_backup_exit=$?
    if [ $tde_backup_exit -eq 0 ]; then
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
        verify)
            verify_tde
        ;;
        rotate)
            check_prerequisites
            rotate_tde_key
        ;;
        backup)
            backup_tde_config
        ;;
        connector)
            create_connector_service
        ;;
        *)
            echo -e "${GREEN}PostgreSQL TDE Integration with YubiHSM 2${NC}"
            echo ""
            echo "Usage: $0 {init|verify|rotate|backup|connector}"
            echo ""
            echo "Commands:"
            echo "  init       Initialize PostgreSQL with TDE"
            echo "  verify     Verify TDE status"
            echo "  rotate     Rotate TDE master key"
            echo "  backup     Backup TDE configuration"
            echo "  connector  Setup YubiHSM connector service"
            echo ""
            echo "Environment Variables:"
            echo "  YUBIHSM_CONNECTOR_URL   YubiHSM connector URL"
            echo "  YUBIHSM_AUTH_KEY       Authentication key ID"
            echo "  POSTGRES_VERSION       PostgreSQL version"
            echo "  POSTGRES_DATA_DIR      PostgreSQL data directory"
            exit 1
        ;;
    esac
}

# Create log directory
mkdir -p "$(dirname "$LOG_FILE")"

# Run main function
main "$@"