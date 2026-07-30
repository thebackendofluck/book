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
# MariaDB Transparent Data Encryption Integration with YubiHSM 2
# Provides secure key management for MariaDB TDE using native implementation and YubiHSM 2 FIPS
# Updated for MariaDB 10.11+ with native TDE support

set -euo pipefail

# Configuration
YUBIHSM_CONNECTOR_URL="${YUBIHSM_CONNECTOR_URL:-http://localhost:12345}"
YUBIHSM_AUTH_KEY="${YUBIHSM_AUTH_KEY:-2}"
MARIADB_VERSION="${MARIADB_VERSION:-10.11}"
MARIADB_DATA_DIR="${MARIADB_DATA_DIR:-/var/lib/mysql}"
TDE_KEY_COMMAND_PATH="/opt/yubihsm/mariadb_key_fetch.sh"
TDE_CONFIG_PATH="/etc/mysql/tde"
LOG_FILE="/var/log/mysql/yubihsm-tde.log"

# TDE Key configuration
TDE_KEY_ID=200
TDE_KEY_LABEL="mariadb-tde-master"
TDE_KEY_LENGTH=32  # 256 bits

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
    for tool in yubihsm-shell python3 mysql mariadb-admin; do
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
    
    # Check MariaDB installation and version
    if ! mysql --version | grep -q "MariaDB"; then
        error "MariaDB not properly installed"
    fi
    
    local mariadb_version
    mariadb_version=$(mysql --version | grep -oP '\d+\.\d+')
    if ! awk "BEGIN {exit !($mariadb_version >= 10.11)}"; then
        error "MariaDB 10.11+ required for native TDE. Current version: $mariadb_version"
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

# Create key fetch script for MariaDB
create_key_fetch_script() {
    info "Creating key fetch script for MariaDB..."
    
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
MariaDB TDE Key Fetch Script
Retrieves the TDE master key from YubiHSM 2 for MariaDB
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
            pwd_file = '/etc/mysql/yubihsm.pwd'
            if os.path.exists(pwd_file):
                with open(pwd_file, 'r') as f:
                    password = f.read().strip()
            else:
                # Hard fail: never silently fall back to a guessable password
                print("Error: YUBIHSM_PASSWORD not set and /etc/mysql/yubihsm.pwd not present", file=sys.stderr)
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
            f'mariadb-tde-{TDE_KEY_ID}'.encode(),
            b'yubihsm-salt',
            100000,
            32
        )

        # Return hex-encoded 32-byte key as required by MariaDB TDE
        print(derived_key.hex())

        session.close()
        return 0

    except Exception as e:
        # Log error to stderr (MariaDB will capture this)
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
    chown mysql:mysql "$TDE_KEY_COMMAND_PATH"
    
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

# Initialize MariaDB with TDE
init_mariadb_tde() {
    info "Initializing MariaDB with TDE..."
    
    # Stop MariaDB if running
    systemctl stop mariadb 2>/dev/null || true
    
    # Backup existing data directory
    if [ -d "$MARIADB_DATA_DIR" ]; then
        warning "Existing MariaDB data directory found"
        echo "This will create a new TDE-enabled instance. Backup existing data? (yes/no)"
        read -r response
        
        if [ "$response" = "yes" ]; then
            backup_dir="${MARIADB_DATA_DIR}.backup.$(date +%Y%m%d-%H%M%S)"
            mv "$MARIADB_DATA_DIR" "$backup_dir"
            success "Backed up existing data to $backup_dir"
        fi
    fi
    
    # Create TDE configuration directory
    mkdir -p "$TDE_CONFIG_PATH"
    
    # Generate encryption keys file
    generate_keys_file
    
    # Configure MariaDB
    configure_mariadb
    
    # Initialize MariaDB with TDE
    info "Initializing MariaDB with TDE..."
    
    # Start MariaDB
    systemctl start mariadb
    
    if systemctl is-active mariadb &>/dev/null; then
        success "MariaDB started with TDE enabled"
    else
        error "Failed to start MariaDB"
    fi
}

# Generate MariaDB encryption keys file
generate_keys_file() {
    info "Generating MariaDB encryption keys file..."
    
    local keys_file="$TDE_CONFIG_PATH/keys.enc"
    local key_data
    
    # Get the master key
    key_data=$($TDE_KEY_COMMAND_PATH)
    
    if [ -z "$key_data" ]; then
        error "Failed to retrieve TDE master key"
    fi
    
    # Create keys file with MariaDB format
    cat > "$keys_file" <<EOF
1;${key_data};AES_256;ENCRYPTED
EOF
    
    # Set permissions
    chown mysql:mysql "$keys_file"
    chmod 600 "$keys_file"
    
    success "MariaDB encryption keys file created"
}

# Configure MariaDB for TDE
configure_mariadb() {
    info "Configuring MariaDB for TDE..."
    
    local my_cnf="/etc/mysql/mariadb.conf.d/50-server.cnf"
    
    # Backup original configuration
    cp "$my_cnf" "$my_cnf.backup"
    
    # Add TDE configuration
    cat >> "$my_cnf" <<EOF

# YubiHSM TDE Configuration
plugin_load_add = file_key_management
file_key_management_filename = $TDE_CONFIG_PATH/keys.enc
file_key_management_filekey = FILE:$TDE_KEY_COMMAND_PATH
file_key_management_encryption_algorithm = AES_CTR

# InnoDB encryption
innodb_encrypt_tables = ON
innodb_encrypt_log = ON
innodb_encrypt_temporary_tables = ON
innodb_encryption_rotate_key_age = 1
innodb_encryption_rotation_iops = 100

# Aria encryption
aria_encrypt_tables = ON

# Security settings
ssl = on
ssl_cert_file = '/etc/mysql/server.crt'
ssl_key_file = '/etc/mysql/server.key'

# Audit settings
log_warnings = 2
general_log = ON
general_log_file = /var/log/mysql/mysql.log
EOF
    
    # Generate SSL certificates if not present
    if [ ! -f "/etc/mysql/server.crt" ]; then
        info "Generating SSL certificates..."
        openssl req -new -x509 -days 365 -nodes \
        -keyout "/etc/mysql/server.key" \
        -out "/etc/mysql/server.crt" \
        -subj "/C=US/ST=State/L=City/O=Organization/CN=mariadb"
        
        chown mysql:mysql "/etc/mysql/server."*
        chmod 600 "/etc/mysql/server.key"
    fi
    
    success "MariaDB configuration updated"
}

# Verify TDE status
verify_tde() {
    info "Verifying TDE status..."
    
    # Check if MariaDB is running
    if ! systemctl is-active mariadb &>/dev/null; then
        error "MariaDB is not running"
    fi
    
    # Check TDE status using information_schema
    local tde_status
    tde_status=$(mysql -u root -e "SELECT * FROM information_schema.INNODB_TABLESPACES_ENCRYPTION;" 2>/dev/null || echo "")
    
    if [ -n "$tde_status" ]; then
        success "TDE is enabled"
        
        # Display encryption details
        mysql -u root -e "SHOW VARIABLES LIKE 'innodb_encrypt%';"
        
        # Test database connection
        if mysql -u root -e "SELECT VERSION();" &>/dev/null; then
            success "Database connection successful"
        else
            warning "Database connection failed"
        fi
    else
        error "TDE is not enabled"
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
        label="mariadb-tde-new",
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
    
    # Update keys file
    local new_key_data
    new_key_data=$($TDE_KEY_COMMAND_PATH)
    local keys_file="$TDE_CONFIG_PATH/keys.enc"
    
    # Add new key version
    echo "2;${new_key_data};AES_256;ENCRYPTED" >> "$keys_file"
    
    # Perform key rotation
    info "Performing key rotation..."
    
    # Restart MariaDB
    systemctl restart mariadb
    
    if systemctl is-active mariadb &>/dev/null; then
        success "TDE key rotation completed successfully"
        
        # Clean up old key from YubiHSM
        info "Removing old key from YubiHSM..."
        # Implementation would go here
    else
        error "Failed to start MariaDB after key rotation"
    fi
}

# Create systemd service for YubiHSM connector
create_connector_service() {
    info "Creating systemd service for YubiHSM connector..."
    
    cat > /etc/systemd/system/yubihsm-connector.service <<EOF
[Unit]
Description=YubiHSM Connector Service
Before=mariadb.service
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
    local backup_dir="/var/backups/mariadb-tde"
    local backup_file
    backup_file="$backup_dir/tde-backup-$(date +%Y%m%d-%H%M%S).tar.gz"
    
    info "Backing up TDE configuration..."
    
    mkdir -p "$backup_dir"
    
    # Create backup
    tar czf "$backup_file" \
    "$TDE_KEY_COMMAND_PATH" \
    "$TDE_CONFIG_PATH" \
    "/etc/mysql/mariadb.conf.d/"*.cnf \
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
            generate_tde_key
            create_key_fetch_script
            init_mariadb_tde
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
            echo -e "${GREEN}MariaDB TDE Integration with YubiHSM 2${NC}"
            echo ""
            echo "Usage: $0 {init|verify|rotate|backup|connector}"
            echo ""
            echo "Commands:"
            echo "  init       Initialize MariaDB with TDE"
            echo "  verify     Verify TDE status"
            echo "  rotate     Rotate TDE master key"
            echo "  backup     Backup TDE configuration"
            echo "  connector  Setup YubiHSM connector service"
            echo ""
            echo "Environment Variables:"
            echo "  YUBIHSM_CONNECTOR_URL   YubiHSM connector URL"
            echo "  YUBIHSM_AUTH_KEY       Authentication key ID"
            echo "  MARIADB_VERSION        MariaDB version"
            echo "  MARIADB_DATA_DIR       MariaDB data directory"
            exit 1
        ;;
    esac
}

# Create log directory
mkdir -p "$(dirname "$LOG_FILE")"

# Run main function
main "$@"