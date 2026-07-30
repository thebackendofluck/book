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

# shellcheck disable=SC2155,SC2162,SC2181
# LUKS Disk Encryption with HSM Key Management
# Integrates hardware security modules with LUKS2 for secure disk encryption
#
# Usage: ./disk_encryption.sh {init|mount|rotate|list} [args...]

set -euo pipefail

# Configuration
HSM_CONNECTOR_URL="${HSM_CONNECTOR_URL:-http://localhost:12345}"
HSM_AUTH_KEY="${HSM_AUTH_KEY:-2}"
KEY_STORAGE_PATH="/etc/hsm/disk-keys"
SYSTEMD_SERVICE_PATH="/etc/systemd/system"
LOG_FILE="/var/log/hsm-disk-encryption.log"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"; }
error() { echo -e "${RED}[ERROR]${NC} $1" >&2; log "ERROR: $1"; exit 1; }
success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; log "SUCCESS: $1"; }
warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; log "WARNING: $1"; }

check_root() {
    if [[ $EUID -ne 0 ]]; then
        error "This script must be run as root"
    fi
}

check_prerequisites() {
    local missing_tools=()
    for tool in cryptsetup yubihsm-shell python3 jq; do
        if ! command -v "$tool" &> /dev/null; then
            missing_tools+=("$tool")
        fi
    done

    if [ ${#missing_tools[@]} -gt 0 ]; then
        error "Missing required tools: ${missing_tools[*]}"
    fi

    if ! curl -s "$HSM_CONNECTOR_URL/connector/status" &> /dev/null; then
        error "HSM connector not running at $HSM_CONNECTOR_URL"
    fi

    success "All prerequisites met"
}

# Generate encryption key in HSM
generate_disk_key() {
    local disk_uuid="$1"
    local key_label="disk-${disk_uuid:0:8}"

    log "Generating encryption key for disk $disk_uuid"

    python3 - <<EOF
import sys
import hashlib
from yubihsm import YubiHsm
from yubihsm.defs import CAPABILITY, ALGORITHM
from yubihsm.objects import SymmetricKey
import getpass

try:
    hsm = YubiHsm.connect("$HSM_CONNECTOR_URL")
    password = getpass.getpass("Enter HSM password: ")
    session = hsm.create_session_derived($HSM_AUTH_KEY, password)

    key_id = int(hashlib.sha256("$disk_uuid".encode()).hexdigest()[:4], 16)

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

    if [ $? -eq 0 ]; then
        success "Encryption key generated in HSM"
    else
        error "Failed to generate encryption key"
    fi
}

# Retrieve key from HSM
get_disk_key() {
    local disk_uuid="$1"

    python3 - <<EOF
import sys
import hashlib
import os
from yubihsm import YubiHsm
from yubihsm.defs import OBJECT

try:
    hsm = YubiHsm.connect("$HSM_CONNECTOR_URL")
    # fail-fast: set HSM_PASSWORD in environment before running this script
    password = os.environ['HSM_PASSWORD']  # raises KeyError if unset
    session = hsm.create_session_derived($HSM_AUTH_KEY, password)

    key_id = int(hashlib.sha256("$disk_uuid".encode()).hexdigest()[:4], 16)
    key = session.get_object(key_id, OBJECT.SYMMETRIC_KEY)

    # In production, use proper wrapped export from HSM
    derived_key = hashlib.sha256(f"{disk_uuid}-{key_id}".encode()).digest()
    print(derived_key.hex())
    session.close()

except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
EOF
}

# Initialize disk encryption with LUKS2
init_disk_encryption() {
    local device="$1"

    check_root
    check_prerequisites

    if [ ! -b "$device" ]; then
        error "Device $device not found"
    fi

    local disk_uuid=$(blkid -s UUID -o value "$device" 2>/dev/null || uuidgen)
    log "Initializing encryption for device $device (UUID: $disk_uuid)"

    generate_disk_key "$disk_uuid"
    local encryption_key=$(get_disk_key "$disk_uuid")

    if [ -z "$encryption_key" ]; then
        error "Failed to retrieve encryption key"
    fi

    warning "This will erase all data on $device. Continue? (yes/no)"
    read -r confirmation
    if [ "$confirmation" != "yes" ]; then
        error "Operation cancelled"
    fi

    # Format with LUKS2 using AES-XTS (industry standard for disk encryption)
    echo -n "$encryption_key" | xxd -r -p | cryptsetup luksFormat \
    --type luks2 \
    --cipher aes-xts-plain64 \
    --key-size 512 \
    --hash sha256 \
    --pbkdf pbkdf2 \
    --key-file - \
    "$device"

    success "Device $device encrypted successfully"

    create_key_script "$disk_uuid"
    create_systemd_service "$disk_uuid" "$device"
}

# Create key retrieval script for automated unlock
create_key_script() {
    local disk_uuid="$1"
    local script_path="$KEY_STORAGE_PATH/get-key-${disk_uuid:0:8}.sh"

    mkdir -p "$KEY_STORAGE_PATH"

    cat > "$script_path" <<'SCRIPT'
#!/bin/bash
# Key retrieval script for HSM-backed disk encryption

DISK_UUID="__DISK_UUID__"
HSM_CONNECTOR_URL="__CONNECTOR_URL__"
HSM_AUTH_KEY="__AUTH_KEY__"

python3 - <<'EOF'
import sys
import hashlib
import os
from yubihsm import YubiHsm
from yubihsm.defs import OBJECT

try:
    hsm = YubiHsm.connect(os.environ.get('HSM_CONNECTOR_URL'))
    # fail-fast: HSM_PASSWORD must be exported in the environment
    password = os.environ['HSM_PASSWORD']  # raises KeyError if unset
    session = hsm.create_session_derived(
        int(os.environ.get('HSM_AUTH_KEY')),
        password
    )

    disk_uuid = os.environ.get('DISK_UUID')
    key_id = int(hashlib.sha256(disk_uuid.encode()).hexdigest()[:4], 16)

    # In production, properly retrieve and decrypt key from HSM
    derived_key = hashlib.sha256(f"{disk_uuid}-{key_id}".encode()).digest()

    sys.stdout.buffer.write(derived_key)
    session.close()

except Exception:
    sys.exit(1)
EOF
SCRIPT

    sed -i "s|__DISK_UUID__|$disk_uuid|g" "$script_path"
    sed -i "s|__CONNECTOR_URL__|$HSM_CONNECTOR_URL|g" "$script_path"
    sed -i "s|__AUTH_KEY__|$HSM_AUTH_KEY|g" "$script_path"

    chmod 700 "$script_path"
    success "Created key retrieval script: $script_path"
}

# Create systemd service for auto-unlock at boot
create_systemd_service() {
    local disk_uuid="$1"
    local device="$2"
    local service_name="hsm-unlock-${disk_uuid:0:8}.service"
    local service_path="$SYSTEMD_SERVICE_PATH/$service_name"

    cat > "$service_path" <<SERVICE
[Unit]
Description=HSM Disk Unlock for $device
After=network.target hsm-connector.service
Before=local-fs.target

[Service]
Type=oneshot
RemainAfterExit=yes
Environment="HSM_CONNECTOR_URL=$HSM_CONNECTOR_URL"
Environment="HSM_AUTH_KEY=$HSM_AUTH_KEY"
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

    local disk_uuid=$(cryptsetup luksUUID "$device" 2>/dev/null)
    if [ -z "$disk_uuid" ]; then
        error "Device $device is not a LUKS device"
    fi

    local mapper_name="luks-${disk_uuid:0:8}"

    if [ -b "/dev/mapper/$mapper_name" ]; then
        warning "Device already unlocked"
    else
        log "Unlocking device $device"
        local key_script="$KEY_STORAGE_PATH/get-key-${disk_uuid:0:8}.sh"
        if [ ! -f "$key_script" ]; then
            error "Key retrieval script not found: $key_script"
        fi

        export HSM_PASSWORD
        read -s -p "Enter HSM password: " HSM_PASSWORD
        echo

        "$key_script" | cryptsetup luksOpen "$device" "$mapper_name" --key-file -
        if [ $? -eq 0 ]; then
            success "Device unlocked successfully"
        else
            error "Failed to unlock device"
        fi
    fi

    mkdir -p "$mount_point"
    mount "/dev/mapper/$mapper_name" "$mount_point"

    if [ $? -eq 0 ]; then
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

    local disk_uuid=$(cryptsetup luksUUID "$device" 2>/dev/null)
    if [ -z "$disk_uuid" ]; then
        error "Device $device is not a LUKS device"
    fi

    log "Rotating encryption key for device $device"

    local new_uuid="${disk_uuid}-rotated-$(date +%s)"
    generate_disk_key "$new_uuid"

    local old_key=$(get_disk_key "$disk_uuid")
    local new_key=$(get_disk_key "$new_uuid")

    echo -n "$old_key" | xxd -r -p | cryptsetup luksAddKey "$device" \
    --key-file - <(echo -n "$new_key" | xxd -r -p)

    echo -n "$new_key" | xxd -r -p | cryptsetup luksRemoveKey "$device" \
    <(echo -n "$old_key" | xxd -r -p) --key-file -

    create_key_script "$new_uuid"
    create_systemd_service "$new_uuid" "$device"
    success "Encryption key rotated successfully"
}

# List encrypted disks
list_disks() {
    log "Listing HSM-managed encrypted disks"

    echo "Encrypted Disks:"
    echo "----------------"
    for device in $(lsblk -o NAME,FSTYPE -n -l | grep crypto_LUKS | awk '{print "/dev/"$1}'); do
        local uuid=$(cryptsetup luksUUID "$device" 2>/dev/null)
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
            if [ $# -ne 2 ]; then error "Usage: $0 init <device>"; fi
            init_disk_encryption "$2"
        ;;
        mount)
            if [ $# -ne 3 ]; then error "Usage: $0 mount <device> <mount_point>"; fi
            mount_disk "$2" "$3"
        ;;
        rotate)
            if [ $# -ne 2 ]; then error "Usage: $0 rotate <device>"; fi
            rotate_key "$2"
        ;;
        list) list_disks ;;
        *)
            echo "LUKS Disk Encryption with HSM Key Management"
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
            echo "  HSM_CONNECTOR_URL   HSM connector URL (default: http://localhost:12345)"
            echo "  HSM_AUTH_KEY        Authentication key ID (default: 2)"
            echo "  HSM_PASSWORD        HSM password (for automated operations)"
            exit 1
        ;;
    esac
}

mkdir -p "$(dirname "$LOG_FILE")"
main "$@"
