#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 45, Secure Infrastructure Decommissioning.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

#
# Force Destruction Script for Secure Data Destruction System
# Destroys everything regardless of power state or connectivity
#

set -euo pipefail

# Simulation by default, like terraform_obliterator.sh and aws_nuke_enhanced.py
# in this directory. This script acts on the host that runs it: it stops every
# service, deletes every user account, flushes the firewall and shreds the logs.
# Nothing here is scoped to a cloud account you can rebuild, so it does not run
# for real unless the operator sets both variables below by hand.
DRY_RUN="${DRY_RUN:-true}"
I_HAVE_WRITTEN_AUTHORISATION="${I_HAVE_WRITTEN_AUTHORISATION:-no}"

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="/var/log/force_destruction.log"
EMERGENCY_FLAG="/tmp/emergency_destruction_active"
OFFLINE_QUEUE="/var/spool/sdds/offline_destruction.queue"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging function
log() {
    local level="$1"
    local message="$2"
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[$timestamp] [$level] $message" >> "$LOG_FILE"
    echo -e "${BLUE}[$timestamp]${NC} ${level:0:1}$message"
}

# Error handling
error_exit() {
    local message="$1"
    log "ERROR" "$message"
    echo -e "${RED}ERROR: $message${NC}" >&2
    exit 1
}

# Check if running as root
check_root() {
    if [[ $EUID -ne 0 ]]; then
        error_exit "This script must be run as root (sudo)"
    fi
}

# Parse command line arguments
parse_args() {
    EMERGENCY_MODE=false
    OVERRIDE_CHECKS=false
    DESTROY_OFFLINE=false
    TARGET_SYSTEMS="all"

    while [[ $# -gt 0 ]]; do
        case $1 in
            --emergency)
                EMERGENCY_MODE=true
                log "INFO" "Emergency mode activated"
                ;;
            --override-all-checks)
                OVERRIDE_CHECKS=true
                log "WARNING" "All safety checks overridden"
                ;;
            --destroy-offline-systems)
                DESTROY_OFFLINE=true
                log "WARNING" "Offline system destruction enabled"
                ;;
            --target)
                TARGET_SYSTEMS="$2"
                shift
                ;;
            --help)
                show_help
                exit 0
                ;;
            *)
                error_exit "Unknown option: $1"
                ;;
        esac
        shift
    done
}

show_help() {
    cat << EOF
Force Destruction Script for SDDS

USAGE:
    sudo ./force_destruction.sh [OPTIONS]

OPTIONS:
    --emergency                Enable emergency destruction mode
    --override-all-checks      Override all safety checks
    --destroy-offline-systems  Destroy systems even when powered off
    --target SYSTEMS          Target specific systems (default: all)
    --help                     Show this help message

EXAMPLES:
    # Show what would happen. This is the default and makes no changes.
    ./force_destruction.sh --emergency --destroy-offline-systems

    # Report on specific systems
    ./force_destruction.sh --target "aws,servers" --emergency

WARNING:
    This script destroys the host it runs on: every service stopped, every user
    account deleted, the firewall flushed, the logs shredded and the bootloader
    rewritten. It runs in simulation unless you deliberately set both
    DRY_RUN=false and I_HAVE_WRITTEN_AUTHORISATION=yes, and it will still ask
    for typed confirmation. Use only with legal authorisation in writing, on a
    machine you intend to lose.
EOF
}

# Create emergency flag
create_emergency_flag() {
    log "INFO" "Creating emergency destruction flag"
    mkdir -p "$(dirname "$EMERGENCY_FLAG")"
    {
        date '+%Y-%m-%d %H:%M:%S'
        echo "EMERGENCY_MODE=$EMERGENCY_MODE"
        echo "OVERRIDE_CHECKS=$OVERRIDE_CHECKS"
        echo "DESTROY_OFFLINE=$DESTROY_OFFLINE"
    } > "$EMERGENCY_FLAG"
}

# Schedule offline destruction
schedule_offline_destruction() {
    if [[ "$DESTROY_OFFLINE" == "true" ]]; then
        log "INFO" "Scheduling offline system destruction"
        mkdir -p "$(dirname "$OFFLINE_QUEUE")"

        # Find all known systems
        find_systems_to_destroy

        # Create destruction scripts for each system
        for system in "${SYSTEMS_TO_DESTROY[@]}"; do
            create_offline_destruction_script "$system"
        done
    fi
}

# Find systems to destroy
find_systems_to_destroy() {
    SYSTEMS_TO_DESTROY=()

    # From Ansible inventory
    if [[ -f "config/ansible_inventory.json" ]]; then
        mapfile -t ansible_systems < <(jq -r '.hosts | keys[]' config/ansible_inventory.json 2>/dev/null || echo "")
        SYSTEMS_TO_DESTROY+=("${ansible_systems[@]}")
    fi

    # From Terraform state
    if [[ -d "infrastructure/" ]]; then
        mapfile -t tf_systems < <(find infrastructure/ -name "*.tfstate" -exec jq -r '.resources[].instances[].attributes.public_ip // .resources[].instances[].attributes.private_ip // empty' {} \; 2>/dev/null | sort | uniq)
        SYSTEMS_TO_DESTROY+=("${tf_systems[@]}")
    fi

    # From configuration files
    if [[ -f "config/system_inventory.json" ]]; then
        mapfile -t config_systems < <(jq -r '.systems[]' config/system_inventory.json 2>/dev/null || echo "")
        SYSTEMS_TO_DESTROY+=("${config_systems[@]}")
    fi

    # Remove duplicates
    mapfile -t SYSTEMS_TO_DESTROY < <(printf '%s\n' "${SYSTEMS_TO_DESTROY[@]}" | sort | uniq)

    log "INFO" "Found ${#SYSTEMS_TO_DESTROY[@]} systems to destroy"
}

# Create offline destruction script for a system
create_offline_destruction_script() {
    local system="$1"
    local script_path="$OFFLINE_QUEUE/destroy_$system.sh"

    log "INFO" "Creating offline destruction script for $system"

    cat > "$script_path" << EOF
#!/bin/bash
#
# Offline Destruction Script for $system
# Generated by force_destruction.sh on $(date)
#

set -euo pipefail

SYSTEM="$system"
LOG_FILE="/var/log/sdds_offline_\$SYSTEM.log"

# Logging function
log() {
    local level="\$1"
    local message="\$2"
    local timestamp=\$(date '+%Y-%m-%d %H:%M:%S')
    echo "[\$timestamp] [\$level] \$message" >> "\$LOG_FILE"
    echo "[\$timestamp] [\$level] \$message"
}

log "INFO" "Starting offline destruction for \$SYSTEM"

# Configure BIOS/UEFI for destruction on boot
configure_bios_destruction() {
    log "INFO" "Configuring BIOS/UEFI for destruction on boot"

    # This would configure the system to run destruction on next boot
    # Implementation depends on hardware/BIOS interface

    # For now, create a systemd service that runs on boot
    cat > /etc/systemd/system/sdds-offline-destruction.service << SERVICEEOF
[Unit]
Description=SDDS Offline Destruction Service
After=network.target

[Service]
Type=oneshot
ExecStart=$SCRIPT_DIR/offline_crypto_wipe.sh --all-disks --force
RemainAfterExit=no

[Install]
WantedBy=multi-user.target
SERVICEEOF

    systemctl enable sdds-offline-destruction.service
    log "INFO" "BIOS/UEFI destruction configuration complete"
}

# Set up hardware triggers
setup_hardware_triggers() {
    log "INFO" "Setting up hardware destruction triggers"

    # Configure watchdog timer for destruction if system hangs
    # Configure TPM/HSM for destruction triggers
    # Set up power management triggers

    log "INFO" "Hardware triggers configured"
}

# Main destruction sequence
main() {
    log "WARNING" "=== STARTING OFFLINE DESTRUCTION FOR \$SYSTEM ==="

    # Step 1: Configure for destruction on boot
    configure_bios_destruction

    # Step 2: Set up hardware triggers
    setup_hardware_triggers

    # Step 3: Schedule immediate destruction if possible
    if [[ -f "$SCRIPT_DIR/crypto_wipe.sh" ]]; then
        log "INFO" "Running immediate cryptographic wipe"
        bash "$SCRIPT_DIR/crypto_wipe.sh" --all-disks --force --no-verification
    fi

    # Step 4: Self-destruct if capable
    if [[ -f "$SCRIPT_DIR/hardware_selfdestruct.sh" ]]; then
        log "WARNING" "Initiating hardware self-destruction"
        bash "$SCRIPT_DIR/hardware_selfdestruct.sh" --force
    fi

    log "WARNING" "=== OFFLINE DESTRUCTION SEQUENCE COMPLETE ==="
}

# Run main function
main "\$@"
EOF

    chmod +x "$script_path"
    log "INFO" "Offline destruction script created: $script_path"
}

# Force immediate destruction of online systems
force_online_destruction() {
    log "WARNING" "Starting force destruction of online systems"

    # Stop all running services
    log "INFO" "Stopping all services"
    systemctl list-units --type=service --state=running --no-legend | awk '{print $1}' | xargs -r systemctl stop 2>/dev/null || true

    # Kill all user processes
    log "INFO" "Terminating all user processes"
    who | awk '{print $1}' | sort -u | xargs -r -I {} pkill -9 -u {} 2>/dev/null || true

    # Cryptographic disk wipe
    if [[ -f "$SCRIPT_DIR/crypto_wipe.sh" ]]; then
        log "WARNING" "Starting cryptographic disk wipe"
        bash "$SCRIPT_DIR/crypto_wipe.sh" --all-disks --force --method=aes256
    else
        log "ERROR" "Cryptographic wipe script not found"
    fi

    # Destroy network configuration
    log "INFO" "Destroying network configuration"
    ifconfig | grep -oP '^[^ ]+' | xargs -I {} ifconfig {} down 2>/dev/null || true
    iptables -F 2>/dev/null || true
    iptables -X 2>/dev/null || true
    iptables -t nat -F 2>/dev/null || true
    iptables -t nat -X 2>/dev/null || true

    # Remove all users and groups
    log "INFO" "Removing all users and groups"
    while IFS=: read -r user _; do
        if [[ "$user" != "root" && "$user" != "daemon" ]]; then
            userdel -r "$user" 2>/dev/null || true
        fi
    done < /etc/passwd

    # Overwrite system logs
    log "INFO" "Overwriting system logs"
    find /var/log -type f -exec shred -u -n 1 {} \; 2>/dev/null || true

    # Self-destruct hardware if possible
    if [[ -f "$SCRIPT_DIR/hardware_selfdestruct.sh" ]]; then
        log "WARNING" "Initiating hardware self-destruction"
        bash "$SCRIPT_DIR/hardware_selfdestruct.sh" --force
    fi
}

# Configure system for destruction on next boot
configure_boot_destruction() {
    log "INFO" "Configuring system for destruction on next boot"

    # Create initrd with destruction capabilities
    # Modify grub configuration
    # Set up kernel parameters for destruction mode

    cat >> /etc/default/grub << EOF
# SDDS Force Destruction Configuration
GRUB_CMDLINE_LINUX_DEFAULT="\$GRUB_CMDLINE_LINUX_DEFAULT sdds_force_destroy=1"
EOF

    update-grub 2>/dev/null || true

    # Create destruction init script
    cat > /etc/init.d/sdds_force_destroy << EOF
#!/bin/bash
### BEGIN INIT INFO
# Provides:          sdds_force_destroy
# Required-Start:    \$all
# Required-Stop:
# Default-Start:     S
# Default-Stop:
# Short-Description: SDDS Force Destruction
### END INIT INFO

case "\$1" in
    start)
        # Deliberately does NOT pass --override-all-checks. A boot service that
        # re-invokes a host-destroying script with its safety gate disabled
        # survives every reboot with nothing left to stop it.
        logger -t sdds "Force destruction requested on boot; operator confirmation required"
        bash $SCRIPT_DIR/force_destruction.sh --emergency
        ;;
    *)
        echo "Usage: \$0 {start}"
        exit 1
        ;;
esac
EOF

    chmod +x /etc/init.d/sdds_force_destroy
    update-rc.d sdds_force_destroy defaults 2>/dev/null || true

    log "INFO" "Boot destruction configuration complete"
}

# Main execution
main() {
    log "WARNING" "=== FORCE DESTRUCTION SCRIPT STARTED ==="
    log "INFO" "Emergency Mode: $EMERGENCY_MODE"
    log "INFO" "Override Checks: $OVERRIDE_CHECKS"
    log "INFO" "Destroy Offline: $DESTROY_OFFLINE"
    log "INFO" "Target Systems: $TARGET_SYSTEMS"

    if [[ "$DRY_RUN" != "false" || "$I_HAVE_WRITTEN_AUTHORISATION" != "yes" ]]; then
        echo -e "${YELLOW}[SIMULATED] No changes made. This run only reports what would happen.${NC}"
        echo "  would stop every systemd service and kill every logged-in session"
        echo "  would delete every user account except root and daemon"
        echo "  would flush the iptables filter and nat tables"
        echo "  would shred every file under /var/log"
        echo "  would append destructive kernel arguments and run update-grub"
        echo
        echo "To run this for real you must set BOTH of the following, deliberately:"
        echo "  DRY_RUN=false I_HAVE_WRITTEN_AUTHORISATION=yes $0 ..."
        echo "Do that only on a machine you intend to destroy, with authorisation in writing."
        log "INFO" "Simulated run, no destructive action taken"
        exit 0
    fi

    # --override-all-checks no longer skips this prompt. A flag that removes the
    # last confirmation is exactly what a reader copies out of the help text.
    echo -e "${YELLOW}WARNING: This will destroy ALL systems and data irreversibly!${NC}"
    echo -e "${YELLOW}Are you absolutely sure? Type 'YES_DESTROY_EVERYTHING' to continue:${NC}"
    read -r confirmation
    if [[ "$confirmation" != "YES_DESTROY_EVERYTHING" ]]; then
        log "INFO" "Destruction cancelled by user"
        echo "Destruction cancelled."
        exit 0
    fi

    # Create emergency flag
    create_emergency_flag

    # Schedule offline destruction if requested
    schedule_offline_destruction

    # Configure for destruction on boot
    configure_boot_destruction

    # Force destruction of this system immediately
    force_online_destruction

    log "WARNING" "=== FORCE DESTRUCTION SEQUENCE COMPLETE ==="
    echo -e "${GREEN}Force destruction sequence initiated.${NC}"
    echo -e "${YELLOW}System will be completely destroyed on next boot if not already destroyed.${NC}"
}

# Run the script
check_root
parse_args "$@"
main

exit 0