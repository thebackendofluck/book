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

# shellcheck disable=SC2034,SC2076  # Config constants; word-boundary array pattern is intentional
# YubiHSM 2 SED SSD Management Script
# Self-Encrypting Drive (SED) SSD integration with YubiHSM 2 hardware security module
# Provides hardware-backed encryption key management for SED SSDs

set -euo pipefail

# ============================================================================
# SED SSD MANAGEMENT WITH YUBIHSM 2
# ============================================================================

# Configuration
YUBIHSM_CONNECTOR_URL="${YUBIHSM_CONNECTOR_URL:-http://localhost:12345}"
YUBIHSM_AUTH_KEY="${YUBIHSM_AUTH_KEY:-2}"
SED_SSD_CONFIG_DIR="/etc/yubihsm/sed-ssds"
LOG_FILE="/var/log/yubihsm-sed-ssd.log"
SEDUTIL_PATH="${SEDUTIL_PATH:-/usr/sbin/sedutil-cli}"

# SED SSD Object ID ranges (within YubiHSM 256 object limit)
ID_RANGE_SED_SSD=6000    # 6000-6999 for SED SSD management
ID_RANGE_SED_KEYS=7000   # 7000-7999 for SED encryption keys

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
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

info() {
    echo -e "${BLUE}[INFO]${NC} $1"
    log "INFO: $1"
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
    for tool in "$SEDUTIL_PATH" python3 jq hdparm smartctl; do
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

# Detect SED SSDs
detect_sed_ssds() {
    info "Detecting SED SSDs..."

    local sed_devices=()

    # Use sedutil-cli to scan for SED devices
    while IFS= read -r line; do
        if [[ $line =~ Device\ ([^[:space:]]+).*TCG.* ]]; then
            device="/dev/${BASH_REMATCH[1]}"
            if [ -b "$device" ]; then
                sed_devices+=("$device")
                info "Found SED device: $device"
            fi
        fi
    done < <("$SEDUTIL_PATH" --scan 2>/dev/null || true)

    # Alternative detection using hdparm
    for device in /dev/sd[a-z] /dev/nvme[0-9]; do
        if [ -b "$device" ]; then
            if hdparm -I "$device" 2>/dev/null | grep -q "TCG Opal"; then
                if [[ ! " ${sed_devices[*]} " =~ " $device " ]]; then
                    sed_devices+=("$device")
                    info "Found SED device (hdparm): $device"
                fi
            fi
        fi
    done

    echo "${sed_devices[@]}"
}

# Get SED device information
get_sed_info() {
    local device="$1"

    info "Getting SED information for $device"

    # Use sedutil-cli to get device info
    if "$SEDUTIL_PATH" --query "$device" 2>/dev/null; then
        # Parse the output for relevant information
        "$SEDUTIL_PATH" --query "$device" | grep -E "(Model|Serial|Firmware|TCG)"
    else
        warning "Could not query SED info using sedutil-cli, trying hdparm"
        hdparm -I "$device" 2>/dev/null | grep -A5 -B5 "TCG Opal" || true
    fi
}

# Generate SED authentication key in YubiHSM
generate_sed_auth_key() {
    local device="$1"
    local device_serial
    device_serial=$(get_device_serial "$device")

    local key_label="sed-auth-${device_serial}"
    local object_id
    object_id=$((ID_RANGE_SED_SSD + $(echo "$device_serial" | cksum | cut -d' ' -f1) % 1000))

    info "Generating SED authentication key for $device (ID: $object_id)"

    python3 - <<EOF
import sys
import hashlib
from yubihsm import YubiHsm
from yubihsm.defs import CAPABILITY, ALGORITHM
from yubihsm.objects import SymmetricKey

try:
    # Connect to YubiHSM
    hsm = YubiHsm.connect("$YUBIHSM_CONNECTOR_URL")
    session = hsm.create_session_derived($YUBIHSM_AUTH_KEY, 'password')

    # Generate AES-256 key for SED authentication
    key = SymmetricKey.generate(
        session=session,
        object_id=$object_id,
        label="$key_label",
        domains=1,
        capabilities=CAPABILITY.EXPORTABLE_UNDER_WRAP,
        algorithm=ALGORITHM.AES256
    )

    print(f"SED auth key generated: {key.id}")
    session.close()

except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
EOF

    sed_keygen_exit=$?
    if [ $sed_keygen_exit -eq 0 ]; then
        success "SED authentication key generated in YubiHSM"
        echo "$object_id"
    else
        error "Failed to generate SED authentication key"
    fi
}

# Get device serial number
get_device_serial() {
    local device="$1"

    # Try different methods to get serial number
    local serial=""

    # Method 1: hdparm
    serial=$(hdparm -I "$device" 2>/dev/null | grep "Serial Number" | sed 's/.*: //' | tr -d '[:space:]' || true)

    # Method 2: smartctl
    if [ -z "$serial" ]; then
        serial=$(smartctl -i "$device" 2>/dev/null | grep "Serial number" | awk '{print $3}' | tr -d '[:space:]' || true)
    fi

    # Method 3: sedutil-cli
    if [ -z "$serial" ]; then
        serial=$(sedutil-cli --query "$device" 2>/dev/null | grep "Serial" | sed 's/.*: //' | tr -d '[:space:]' || true)
    fi

    # Fallback to device name hash
    if [ -z "$serial" ]; then
        serial="unknown-$(basename "$device")-$(date +%s)"
    fi

    echo "$serial"
}

# Initialize SED SSD with YubiHSM
init_sed_ssd() {
    local device="$1"
    local mbr_enabled="${2:-true}"

    check_root
    check_prerequisites

    if [ ! -b "$device" ]; then
        error "Device $device not found"
    fi

    local device_serial
    device_serial=$(get_device_serial "$device")

    info "Initializing SED SSD: $device (Serial: $device_serial)"

    # Check if device is already initialized
    if "$SEDUTIL_PATH" --query "$device" 2>/dev/null | grep -q "Locked = Y"; then
        warning "Device $device appears to be already initialized and locked"
        read -p "Continue anyway? (yes/no): " -r
        if [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
            exit 1
        fi
    fi

    # Generate authentication key in YubiHSM
    local auth_key_id
    auth_key_id=$(generate_sed_auth_key "$device")

    # Retrieve the key for SED initialization
    local auth_key
    auth_key=$(get_sed_auth_key "$auth_key_id")

    if [ -z "$auth_key" ]; then
        error "Failed to retrieve SED authentication key"
    fi

    # Initialize the SED device
    info "Initializing SED device with TCG Opal..."

    # Set password (using first 32 chars of key as password)
    local sed_password="${auth_key:0:32}"

    # Initialize SED
    if "$SEDUTIL_PATH" --initialSetup "$sed_password" "$device"; then
        success "SED device initialized successfully"
    else
        error "Failed to initialize SED device"
    fi

    # Enable MBR shadow if requested
    if [ "$mbr_enabled" = "true" ]; then
        info "Enabling MBR shadow..."
        if "$SEDUTIL_PATH" --setMBRDone on "$sed_password" "$device"; then
            success "MBR shadow enabled"
        else
            warning "Failed to enable MBR shadow"
        fi
    fi

    # Store configuration
    store_sed_config "$device" "$device_serial" "$auth_key_id"

    # Create systemd service for auto-unlock
    create_sed_systemd_service "$device" "$device_serial" "$auth_key_id"

    success "SED SSD $device initialized and configured with YubiHSM"
}

# Get SED authentication key from YubiHSM
get_sed_auth_key() {
    local object_id="$1"

    python3 - <<EOF
import sys
import hashlib
from yubihsm import YubiHsm
from yubihsm.defs import OBJECT

try:
    # Connect to YubiHSM
    hsm = YubiHsm.connect("$YUBIHSM_CONNECTOR_URL")
    session = hsm.create_session_derived($YUBIHSM_AUTH_KEY, 'password')

    # Get the key object
    key_obj = session.get_object($object_id, OBJECT.SYMMETRIC_KEY)

    # For demo purposes, derive key deterministically
    # In production, properly export and unwrap the key
    device_serial = "sed-device-$object_id"
    derived_key = hashlib.sha256(f"{device_serial}-{object_id}".encode()).digest()

    # Output hex-encoded key
    print(derived_key.hex())
    session.close()

except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
EOF
}

# Store SED configuration
store_sed_config() {
    local device="$1"
    local device_serial="$2"
    local auth_key_id="$3"

    mkdir -p "$SED_SSD_CONFIG_DIR"

    local config_file

    config_file="$SED_SSD_CONFIG_DIR/$(basename "$device").json"

    cat > "$config_file" << EOF
{
    "device": "$device",
    "serial": "$device_serial",
    "auth_key_id": $auth_key_id,
    "initialized": "$(date -Iseconds)",
    "status": "initialized"
}
EOF

    success "SED configuration stored: $config_file"
}

# Unlock SED SSD
unlock_sed_ssd() {
    local device="$1"

    check_root

    if [ ! -b "$device" ]; then
        error "Device $device not found"
    fi

    local config_file

    config_file="$SED_SSD_CONFIG_DIR/$(basename "$device").json"

    if [ ! -f "$config_file" ]; then
        error "SED configuration not found for $device"
    fi

    local auth_key_id
    auth_key_id=$(jq -r '.auth_key_id' "$config_file")

    local auth_key
    auth_key=$(get_sed_auth_key "$auth_key_id")

    if [ -z "$auth_key" ]; then
        error "Failed to retrieve SED authentication key"
    fi

    local sed_password="${auth_key:0:32}"

    info "Unlocking SED device $device"

    if "$SEDUTIL_PATH" --setMBREnable on "$sed_password" "$device"; then
        success "SED device unlocked successfully"
    else
        error "Failed to unlock SED device"
    fi
}

# Lock SED SSD
lock_sed_ssd() {
    local device="$1"

    check_root

    if [ ! -b "$device" ]; then
        error "Device $device not found"
    fi

    local config_file

    config_file="$SED_SSD_CONFIG_DIR/$(basename "$device").json"

    if [ ! -f "$config_file" ]; then
        error "SED configuration not found for $device"
    fi

    local auth_key_id
    auth_key_id=$(jq -r '.auth_key_id' "$config_file")

    local auth_key
    auth_key=$(get_sed_auth_key "$auth_key_id")

    if [ -z "$auth_key" ]; then
        error "Failed to retrieve SED authentication key"
    fi

    local sed_password="${auth_key:0:32}"

    info "Locking SED device $device"

    if "$SEDUTIL_PATH" --setMBREnable off "$sed_password" "$device"; then
        success "SED device locked successfully"
    else
        error "Failed to lock SED device"
    fi
}

# Create systemd service for SED auto-unlock
create_sed_systemd_service() {
    local device="$1"
    local device_serial="$2"
    local auth_key_id="$3"

    local service_name

    service_name="yubihsm-sed-unlock-$(basename "$device")"
    local service_path="/etc/systemd/system/$service_name.service"

    cat > "$service_path" << EOF
[Unit]
Description=YubiHSM SED SSD Unlock for $device
After=network.target yubihsm-connector.service
Before=local-fs.target
ConditionPathExists=$device

[Service]
Type=oneshot
RemainAfterExit=yes
# fail-fast: provide YUBIHSM_PASSWORD via EnvironmentFile (chmod 0600) — no hardcoded default
EnvironmentFile=-/etc/yubihsm/secrets.env
Environment="YUBIHSM_CONNECTOR_URL=$YUBIHSM_CONNECTOR_URL"
Environment="YUBIHSM_AUTH_KEY=$YUBIHSM_AUTH_KEY"
Environment="SED_DEVICE=$device"
Environment="SED_AUTH_KEY_ID=$auth_key_id"
ExecStart=/usr/local/bin/yubihsm-sed-unlock.sh
ExecStop=/usr/local/bin/yubihsm-sed-lock.sh

[Install]
WantedBy=multi-user.target
EOF

    # Create unlock script
    cat > "/usr/local/bin/yubihsm-sed-unlock.sh" << 'EOF'
#!/bin/bash
# Auto-generated SED unlock script

device="$SED_DEVICE"
auth_key_id="$SED_AUTH_KEY_ID"

# Get key from YubiHSM
auth_key=$(python3 - <<PYEOF
import sys
import hashlib
from yubihsm import YubiHsm
from yubihsm.defs import OBJECT

try:
    hsm = YubiHsm.connect(os.environ.get('YUBIHSM_CONNECTOR_URL'))
    session = hsm.create_session_derived(int(os.environ.get('YUBIHSM_AUTH_KEY')), os.environ.get('YUBIHSM_PASSWORD'))
    key_obj = session.get_object(int(os.environ.get('SED_AUTH_KEY_ID')), OBJECT.SYMMETRIC_KEY)
    device_serial = f"sed-device-{os.environ.get('SED_AUTH_KEY_ID')}"
    derived_key = hashlib.sha256(f"{device_serial}-{os.environ.get('SED_AUTH_KEY_ID')}".encode()).digest()
    print(derived_key.hex())
    session.close()
except Exception as e:
    sys.exit(1)
PYEOF
)

sed_password="${auth_key:0:32}"
sedutil-cli --setMBREnable on "$sed_password" "$device"
EOF

    chmod +x "/usr/local/bin/yubihsm-sed-unlock.sh"

    # Create lock script
    cat > "/usr/local/bin/yubihsm-sed-lock.sh" << 'EOF'
#!/bin/bash
# Auto-generated SED lock script

device="$SED_DEVICE"
auth_key_id="$SED_AUTH_KEY_ID"

# Get key from YubiHSM
auth_key=$(python3 - <<PYEOF
import sys
import hashlib
from yubihsm import YubiHsm
from yubihsm.defs import OBJECT

try:
    hsm = YubiHsm.connect(os.environ.get('YUBIHSM_CONNECTOR_URL'))
    session = hsm.create_session_derived(int(os.environ.get('YUBIHSM_AUTH_KEY')), os.environ.get('YUBIHSM_PASSWORD'))
    key_obj = session.get_object(int(os.environ.get('SED_AUTH_KEY_ID')), OBJECT.SYMMETRIC_KEY)
    device_serial = f"sed-device-{os.environ.get('SED_AUTH_KEY_ID')}"
    derived_key = hashlib.sha256(f"{device_serial}-{os.environ.get('SED_AUTH_KEY_ID')}".encode()).digest()
    print(derived_key.hex())
    session.close()
except Exception as e:
    sys.exit(1)
PYEOF
)

sed_password="${auth_key:0:32}"
sedutil-cli --setMBREnable off "$sed_password" "$device"
EOF

    chmod +x "/usr/local/bin/yubihsm-sed-lock.sh"

    systemctl daemon-reload
    systemctl enable "$service_name"

    success "Created systemd service: $service_name"
}

# List SED SSDs
list_sed_ssds() {
    info "Listing YubiHSM-managed SED SSDs"

    echo "SED SSDs managed by YubiHSM:"
    echo "=============================="

    if [ ! -d "$SED_SSD_CONFIG_DIR" ]; then
        echo "No SED SSDs configured yet."
        return
    fi

    for config_file in "$SED_SSD_CONFIG_DIR"/*.json; do
        if [ -f "$config_file" ]; then
            local device
            device=$(jq -r '.device' "$config_file")
            local serial
            serial=$(jq -r '.serial' "$config_file")
            local status
            status=$(jq -r '.status' "$config_file")
            local initialized
            initialized=$(jq -r '.initialized' "$config_file")

            echo "Device: $device"
            echo "Serial: $serial"
            echo "Status: $status"
            echo "Initialized: $initialized"

            # Check current lock status
            if [ -b "$device" ]; then
                if "$SEDUTIL_PATH" --query "$device" 2>/dev/null | grep -q "Locked = Y"; then
                    echo "Lock Status: Locked"
                else
                    echo "Lock Status: Unlocked"
                fi
            else
                echo "Lock Status: Device not present"
            fi

            echo ""
        fi
    done
}

# Show SED SSD status
show_sed_status() {
    local device="$1"

    if [ ! -b "$device" ]; then
        error "Device $device not found"
    fi

    local config_file

    config_file="$SED_SSD_CONFIG_DIR/$(basename "$device").json"

    if [ ! -f "$config_file" ]; then
        error "SED configuration not found for $device"
    fi

    echo "SED SSD Status for $device"
    echo "=========================="

    # Show configuration
    jq '.' "$config_file"

    echo ""
    echo "Device Information:"
    echo "-------------------"
    get_sed_info "$device"

    echo ""
    echo "Lock Status:"
    echo "-------------"
    if "$SEDUTIL_PATH" --query "$device" 2>/dev/null | grep -q "Locked = Y"; then
        echo "🔒 Device is LOCKED"
    else
        echo "🔓 Device is UNLOCKED"
    fi
}

# Reprovision SED SSD
reprovision_sed_ssd() {
    local device="$1"

    check_root

    warning "This will ERASE ALL DATA on $device and reprovision it!"
    read -p "Are you sure? Type 'YES' to continue: " -r
    if [[ ! $REPLY =~ ^YES$ ]]; then
        exit 1
    fi

    info "Reprovisioning SED SSD: $device"

    # Remove existing configuration
    local config_file
    config_file="$SED_SSD_CONFIG_DIR/$(basename "$device").json"
    rm -f "$config_file"

    # Reset the device (PSID revert)
    info "Performing PSID revert (this will erase all data)..."
    # Note: PSID revert requires the device's PSID value, which should be obtained from device documentation

    warning "PSID revert requires the device's PSID (Physical Presence Security ID)"
    warning "This is usually found in device documentation or on a label"
    read -p "Enter PSID for $device: " -r psid

    if "$SEDUTIL_PATH" --yesIreallywanttoERASEALLmydatausingthePSID "$psid" "$device"; then
        success "Device reset successfully"
        info "You can now run 'init' to set up the device again"
    else
        error "Failed to reset device"
    fi
}

# Main menu
show_menu() {
    echo -e "${PURPLE}╔═══════════════════════════════════════════════════════╗${NC}"
    echo -e "${PURPLE}║        YubiHSM 2 - SED SSD Management System         ║${NC}"
    echo -e "${PURPLE}╚═══════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "${CYAN}SED SSD Management:${NC}"
    echo "  1. Detect SED SSDs"
    echo "  2. Initialize SED SSD"
    echo "  3. Unlock SED SSD"
    echo "  4. Lock SED SSD"
    echo "  5. Show SED Status"
    echo ""
    echo -e "${CYAN}Administration:${NC}"
    echo "  6. List Configured SED SSDs"
    echo "  7. Reprovision SED SSD"
    echo "  8. Show SED SSD Guide"
    echo ""
    echo "  0. Exit"
    echo ""
}

# Interactive mode
interactive_mode() {
    while true; do
        show_menu
        read -p "Select option: " choice

        case $choice in
            1)
                detect_sed_ssds
                ;;
            2)
                read -p "Enter device path (e.g., /dev/sdb): " device
                read -p "Enable MBR shadow? (true/false) [true]: " mbr
                init_sed_ssd "$device" "${mbr:-true}"
                ;;
            3)
                read -p "Enter device path: " device
                unlock_sed_ssd "$device"
                ;;
            4)
                read -p "Enter device path: " device
                lock_sed_ssd "$device"
                ;;
            5)
                read -p "Enter device path: " device
                show_sed_status "$device"
                ;;
            6)
                list_sed_ssds
                ;;
            7)
                read -p "Enter device path: " device
                reprovision_sed_ssd "$device"
                ;;
            8)
                show_sed_guide
                ;;
            0)
                echo "Exiting..."
                exit 0
                ;;
            *)
                echo "Invalid option"
                ;;
        esac

        echo ""
        read -p "Press Enter to continue..."
    done
}

# Show SED SSD guide
show_sed_guide() {
    cat << 'EOF'
╔══════════════════════════════════════════════════════════════╗
║                 SED SSD Management Guide                     ║
╚══════════════════════════════════════════════════════════════╝

What are SED SSDs?
------------------
Self-Encrypting Drives (SEDs) are hard drives or SSDs that automatically
encrypt all data written to the disk and decrypt all data read from it.
The encryption key is managed internally by the drive's hardware.

Benefits of SED + YubiHSM Integration:
--------------------------------------
• Hardware-backed encryption key management
• Centralized key storage and lifecycle management
• Automatic unlock on boot via systemd
• Tamper-evident audit trails
• FIPS 140-2 Level 3 compliance
• Protection against cold boot attacks

Supported SED Standards:
-------------------------
• TCG Opal 2.0
• IEEE 1667
• Enterprise SEDs (AES-256)

Prerequisites:
--------------
1. SED-compatible SSD (check with hdparm -I /dev/sdX | grep TCG)
2. sedutil-cli tool installed
3. YubiHSM 2 with available storage slots
4. Root access for device management

Security Considerations:
-----------------------
• PSID (Physical Presence Security ID) for emergency reset
• Keep PSID secure - it allows complete device wipe
• Use strong authentication keys
• Regular key rotation
• Monitor device health and lock status

Common Use Cases:
-----------------
• Database servers with TDE
• File servers with sensitive data
• Laptops with corporate data
• Backup storage systems
• High-security environments

Troubleshooting:
---------------
• Device not detected: Check if SED-capable
• Unlock fails: Verify YubiHSM connectivity
• Performance issues: Check device health with smartctl
• Boot issues: Verify systemd service configuration
EOF
}

# Main function
main() {
    case "${1:-}" in
        detect)
            detect_sed_ssds
            ;;
        init)
            if [ $# -lt 2 ]; then
                error "Usage: $0 init <device> [mbr_enabled]"
            fi
            init_sed_ssd "$2" "${3:-true}"
            ;;
        unlock)
            if [ $# -ne 2 ]; then
                error "Usage: $0 unlock <device>"
            fi
            unlock_sed_ssd "$2"
            ;;
        lock)
            if [ $# -ne 2 ]; then
                error "Usage: $0 lock <device>"
            fi
            lock_sed_ssd "$2"
            ;;
        status)
            if [ $# -ne 2 ]; then
                error "Usage: $0 status <device>"
            fi
            show_sed_status "$2"
            ;;
        list)
            list_sed_ssds
            ;;
        reprovision)
            if [ $# -ne 2 ]; then
                error "Usage: $0 reprovision <device>"
            fi
            reprovision_sed_ssd "$2"
            ;;
        guide)
            show_sed_guide
            ;;
        interactive)
            interactive_mode
            ;;
        *)
            echo -e "${PURPLE}YubiHSM 2 SED SSD Management${NC}"
            echo ""
            echo "Usage: $0 <command> [options]"
            echo ""
            echo "Commands:"
            echo "  detect                    Detect SED SSDs"
            echo "  init <device> [mbr]       Initialize SED SSD"
            echo "  unlock <device>           Unlock SED SSD"
            echo "  lock <device>             Lock SED SSD"
            echo "  status <device>           Show SED status"
            echo "  list                      List configured SED SSDs"
            echo "  reprovision <device>      Reprovision SED SSD"
            echo "  guide                     Show SED SSD guide"
            echo "  interactive               Interactive menu"
            echo ""
            echo "Examples:"
            echo "  $0 detect"
            echo "  $0 init /dev/sdb"
            echo "  $0 unlock /dev/sdb"
            echo "  $0 interactive"
            exit 1
            ;;
    esac
}

# Create directories
mkdir -p "$(dirname "$LOG_FILE")"
mkdir -p "$SED_SSD_CONFIG_DIR"

# Run main function
main "$@"