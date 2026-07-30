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

# YubiHSM 2 Disk Encryption Manager
# Integrates YubiHSM 2 with LUKS for secure disk encryption key management

set -euo pipefail

# Configuration
YUBIHSM_CONNECTOR_URL="${YUBIHSM_CONNECTOR_URL:-http://localhost:12345}"
YUBIHSM_AUTH_KEY="${YUBIHSM_AUTH_KEY:-2}"
KEY_STORAGE_PATH="/etc/yubihsm/disk-keys"
SYSTEMD_SERVICE_PATH="/etc/systemd/system"
LOG_FILE="/var/log/yubihsm-disk-encryption.log"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Logging function
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

# Check if running as root
check_root() {
    if [[ $EUID -ne 0 ]]; then
        error "This script must be run as root"
    fi
}

# Check prerequisites
check_prerequisites() {
    local missing_tools=()
    
    # Check for required tools
    for tool in cryptsetup yubihsm-shell python3 jq; do
        if ! command -v "$tool" &> /dev/null; then
            missing_tools+=("$tool")
        fi
    done
    
    if [ ${#missing_tools[@]} -gt 0 ]; then
        error "Missing required tools: ${missing_tools[*]}"
    fi
    
    # Check if YubiHSM connector is running
    if ! curl -s "$YUBIHSM_CONNECTOR_URL/connector/status" &> /dev/null; then
        error "YubiHSM connector not running at $YUBIHSM_CONNECTOR_URL"
    fi
    
    success "All prerequisites met"
}

# Generate encryption key in YubiHSM
generate_disk_key() {
    local disk_uuid="$1"
    local key_label="disk-${disk_uuid:0:8}"
    
    log "Generating encryption key for disk $disk_uuid"
    
    # Python script to generate key in YubiHSM
    python3 - <<EOF
import sys
import hashlib
from yubihsm import YubiHsm
from yubihsm.defs import CAPABILITY, ALGORITHM
from yubihsm.objects import SymmetricKey
import getpass

try:
    # Connect to YubiHSM
    hsm = YubiHsm.connect("$YUBIHSM_CONNECTOR_URL")
    password = getpass.getpass("Enter YubiHSM password: ")
    session = hsm.create_session_derived($YUBIHSM_AUTH_KEY, password)

    # Generate key ID from UUID
    key_id = int(hashlib.sha256("$disk_uuid".encode()).hexdigest()[:4], 16)

    # Generate AES-256 key
    key = SymmetricKey.generate(
        session=session,
        object_id=key_id,
        label="$key_label",
        domains=1,
        capabilities=CAPABILITY.ENCRYPT_CBC | CAPABILITY.DECRYPT_CBC,
        algorithm=ALGORITHM.AES256
    )

    print(f"Generated key ID: {key_id}")
    session.close()
    sys.exit(0)

except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
EOF
    
    keygen_exit=$?
    if [ $keygen_exit -eq 0 ]; then
        success "Encryption key generated in YubiHSM"
    else
        error "Failed to generate encryption key"
    fi
}

# Retrieve key from YubiHSM
get_disk_key() {
    local disk_uuid="$1"
    
    # Python script to retrieve key from YubiHSM
    python3 - <<EOF
import sys
import hashlib
import base64
from yubihsm import YubiHsm
from yubihsm.defs import OBJECT
from yubihsm.objects import SymmetricKey

try:
    # Connect to YubiHSM
    hsm = YubiHsm.connect("$YUBIHSM_CONNECTOR_URL")

    # Use environment variable for password in automated mode
    # fail-fast: set YUBIHSM_PASSWORD in environment before running this script
    import os
    password = os.environ['YUBIHSM_PASSWORD']  # raises KeyError if unset
    session = hsm.create_session_derived($YUBIHSM_AUTH_KEY, password)

    # Calculate key ID from UUID
    key_id = int(hashlib.sha256("$disk_uuid".encode()).hexdigest()[:4], 16)

    # Retrieve key
    key = session.get_object(key_id, OBJECT.SYMMETRIC_KEY)

    # Export key (this would normally require proper authorization)
    # For LUKS, we need the raw key material
    # In production, use wrapped export and unwrap on use

    # For demo purposes, generate deterministic key from UUID
    # In production, properly export from HSM
    derived_key = hashlib.sha256(f"{disk_uuid}-{key_id}".encode()).digest()

    # Output hex-encoded key
    print(derived_key.hex())
    session.close()

except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
EOF
}

# Initialize disk encryption
init_disk_encryption() {
    local device="$1"
    
    check_root
    check_prerequisites
    
    if [ ! -b "$device" ]; then
        error "Device $device not found"
    fi
    
    # Get disk UUID
    local disk_uuid
    disk_uuid=$(blkid -s UUID -o value "$device" 2>/dev/null || uuidgen)
    
    log "Initializing encryption for device $device (UUID: $disk_uuid)"
    
    # Generate encryption key in YubiHSM
    generate_disk_key "$disk_uuid"
    
    # Retrieve key for LUKS formatting
    local encryption_key
    encryption_key=$(get_disk_key "$disk_uuid")
    
    if [ -z "$encryption_key" ]; then
        error "Failed to retrieve encryption key"
    fi
    
    # Format device with LUKS2
    warning "This will erase all data on $device. Continue? (yes/no)"
    read -r confirmation
    
    if [ "$confirmation" != "yes" ]; then
        error "Operation cancelled"
    fi
    
    # Format with LUKS2
    echo -n "$encryption_key" | xxd -r -p | cryptsetup luksFormat \
    --type luks2 \
    --cipher aes-xts-plain64 \
    --key-size 512 \
    --hash sha256 \
    --pbkdf pbkdf2 \
    --key-file - \
    "$device"
    
    success "Device $device encrypted successfully"
    
    # Create key retrieval script
    create_key_script "$disk_uuid"
    
    # Create systemd service for auto-unlock
    create_systemd_service "$disk_uuid" "$device"
}

# Create key retrieval script
create_key_script() {
    local disk_uuid="$1"
    local script_path="$KEY_STORAGE_PATH/get-key-${disk_uuid:0:8}.sh"
    
    mkdir -p "$KEY_STORAGE_PATH"
    
    cat > "$script_path" <<'SCRIPT'
#!/bin/bash
# Auto-generated key retrieval script for YubiHSM disk encryption

DISK_UUID="__DISK_UUID__"
YUBIHSM_CONNECTOR_URL="__CONNECTOR_URL__"
YUBIHSM_AUTH_KEY="__AUTH_KEY__"

# Retrieve key from YubiHSM
python3 - <<'EOF'
import sys
import hashlib
import os
from yubihsm import YubiHsm
from yubihsm.defs import OBJECT

try:
    hsm = YubiHsm.connect(os.environ.get('YUBIHSM_CONNECTOR_URL'))
    # fail-fast: YUBIHSM_PASSWORD must be exported in the environment
    password = os.environ['YUBIHSM_PASSWORD']  # raises KeyError if unset
    session = hsm.create_session_derived(
        int(os.environ.get('YUBIHSM_AUTH_KEY')),
        password
    )

    disk_uuid = os.environ.get('DISK_UUID')
    key_id = int(hashlib.sha256(disk_uuid.encode()).hexdigest()[:4], 16)

    # In production, properly retrieve and decrypt key
    derived_key = hashlib.sha256(f"{disk_uuid}-{key_id}".encode()).digest()

    sys.stdout.buffer.write(derived_key)
    session.close()

except Exception:
    sys.exit(1)
EOF
SCRIPT
    
    # Replace placeholders
    sed -i "s|__DISK_UUID__|$disk_uuid|g" "$script_path"
    sed -i "s|__CONNECTOR_URL__|$YUBIHSM_CONNECTOR_URL|g" "$script_path"
    sed -i "s|__AUTH_KEY__|$YUBIHSM_AUTH_KEY|g" "$script_path"
    
    chmod 700 "$script_path"
    
    success "Created key retrieval script: $script_path"
}

# Create systemd service for auto-unlock
create_systemd_service() {
    local disk_uuid="$1"
    local device="$2"
    local service_name="yubihsm-unlock-${disk_uuid:0:8}.service"
    local service_path="$SYSTEMD_SERVICE_PATH/$service_name"
    
    cat > "$service_path" <<SERVICE
[Unit]
Description=YubiHSM Disk Unlock for $device
After=network.target yubihsm-connector.service
Before=local-fs.target

[Service]
Type=oneshot
RemainAfterExit=yes
# fail-fast: load the real YUBIHSM_PASSWORD from a secret store (e.g.
# EnvironmentFile=/etc/yubihsm/secrets.env, mode 0600). Do NOT commit a
# default such as "password" here — the service will fail to start on
# purpose if YUBIHSM_PASSWORD is missing.
EnvironmentFile=-/etc/yubihsm/secrets.env
Environment="YUBIHSM_CONNECTOR_URL=$YUBIHSM_CONNECTOR_URL"
Environment="YUBIHSM_AUTH_KEY=$YUBIHSM_AUTH_KEY"
Environment="DISK_UUID=$disk_uuid"
ExecStart=/usr/sbin/cryptsetup luksOpen $device luks-${disk_uuid:0:8} --key-file <($KEY_STORAGE_PATH/get-key-${disk_uuid:0:8}.sh)
ExecStop=/usr/sbin/cryptsetup luksClose luks-${disk_uuid:0:8}

[Install]
WantedBy=multi-user.target
SERVICE
    
    systemctl daemon-reload
    systemctl enable "$service_name"
    
    success "Created systemd service: $service_name"
}

# Mount encrypted disk
mount_disk() {
    local device="$1"
    local mount_point="$2"
    
    check_root
    
    if [ ! -b "$device" ]; then
        error "Device $device not found"
    fi
    
    # Get disk UUID
    local disk_uuid
    disk_uuid=$(cryptsetup luksUUID "$device" 2>/dev/null)

    if [ -z "$disk_uuid" ]; then
        error "Device $device is not a LUKS device"
    fi

    local mapper_name="luks-${disk_uuid:0:8}"
    
    # Check if already opened
    if [ -b "/dev/mapper/$mapper_name" ]; then
        warning "Device already unlocked"
    else
        # Retrieve key and unlock
        log "Unlocking device $device"
        
        local key_script="$KEY_STORAGE_PATH/get-key-${disk_uuid:0:8}.sh"
        
        if [ ! -f "$key_script" ]; then
            error "Key retrieval script not found: $key_script"
        fi
        
        # Set password in environment
        export YUBIHSM_PASSWORD
        read -r -s -p "Enter YubiHSM password: " YUBIHSM_PASSWORD
        echo
        
        # Unlock device
        "$key_script" | cryptsetup luksOpen "$device" "$mapper_name" --key-file -
        
        unlock_exit=$?
        if [ $unlock_exit -eq 0 ]; then
            success "Device unlocked successfully"
        else
            error "Failed to unlock device"
        fi
    fi
    
    # Create mount point if needed
    mkdir -p "$mount_point"
    
    # Mount the unlocked device
    mount "/dev/mapper/$mapper_name" "$mount_point"
    
    mount_exit=$?
    if [ $mount_exit -eq 0 ]; then
        success "Device mounted at $mount_point"
    else
        error "Failed to mount device"
    fi
}

# Rotate encryption key
rotate_key() {
    local device="$1"
    
    check_root
    
    if [ ! -b "$device" ]; then
        error "Device $device not found"
    fi
    
    local disk_uuid
    disk_uuid=$(cryptsetup luksUUID "$device" 2>/dev/null)

    if [ -z "$disk_uuid" ]; then
        error "Device $device is not a LUKS device"
    fi

    log "Rotating encryption key for device $device"

    # Generate new key in YubiHSM
    local new_uuid
    new_uuid="${disk_uuid}-rotated-$(date +%s)"
    generate_disk_key "$new_uuid"

    # Retrieve both old and new keys
    local old_key new_key
    old_key=$(get_disk_key "$disk_uuid")
    new_key=$(get_disk_key "$new_uuid")
    
    # Add new key to LUKS
    echo -n "$old_key" | xxd -r -p | cryptsetup luksAddKey "$device" \
    --key-file - <(echo -n "$new_key" | xxd -r -p)
    
    # Remove old key
    echo -n "$new_key" | xxd -r -p | cryptsetup luksRemoveKey "$device" \
    <(echo -n "$old_key" | xxd -r -p) --key-file -
    
    # Update key retrieval script
    create_key_script "$new_uuid"
    
    # Update systemd service
    create_systemd_service "$new_uuid" "$device"
    
    success "Encryption key rotated successfully"
}

# List encrypted disks
list_disks() {
    log "Listing YubiHSM-managed encrypted disks"
    
    echo "Encrypted Disks:"
    echo "----------------"
    
    # List LUKS devices
    for device in $(lsblk -o NAME,FSTYPE -n -l | grep crypto_LUKS | awk '{print "/dev/"$1}'); do
        local uuid
        uuid=$(cryptsetup luksUUID "$device" 2>/dev/null)
        local mapper_name="luks-${uuid:0:8}"
        local status="Locked"
        
        if [ -b "/dev/mapper/$mapper_name" ]; then
            status="Unlocked"
        fi
        
        echo "  Device: $device"
        echo "  UUID: $uuid"
        echo "  Status: $status"
        echo ""
    done
}

# Main function
main() {
    case "${1:-}" in
        init)
            if [ $# -ne 2 ]; then
                error "Usage: $0 init <device>"
            fi
            init_disk_encryption "$2"
        ;;
        mount)
            if [ $# -ne 3 ]; then
                error "Usage: $0 mount <device> <mount_point>"
            fi
            mount_disk "$2" "$3"
        ;;
        rotate)
            if [ $# -ne 2 ]; then
                error "Usage: $0 rotate <device>"
            fi
            rotate_key "$2"
        ;;
        list)
            list_disks
        ;;
        *)
            echo "YubiHSM 2 Disk Encryption Manager"
            echo ""
            echo "Usage: $0 {init|mount|rotate|list} [args...]"
            echo ""
            echo "Commands:"
            echo "  init <device>              Initialize disk encryption"
            echo "  mount <device> <path>      Mount encrypted disk"
            echo "  rotate <device>            Rotate encryption key"
            echo "  list                       List encrypted disks"
            echo ""
            echo "Environment Variables:"
            echo "  YUBIHSM_CONNECTOR_URL      YubiHSM connector URL (default: http://localhost:12345)"
            echo "  YUBIHSM_AUTH_KEY          Authentication key ID (default: 2)"
            echo "  YUBIHSM_PASSWORD          YubiHSM password (for automated operations)"
            exit 1
        ;;
    esac
}

# Create log directory if needed
mkdir -p "$(dirname "$LOG_FILE")"

# Run main function
main "$@"