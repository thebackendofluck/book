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
# switch_to_backup_hsm.sh - Switch to backup YubiHSM in case of primary failure
# Performs automated failover with key synchronization

set -euo pipefail

# Configuration
PRIMARY_HSM="${PRIMARY_HSM:-http://localhost:12345}"
BACKUP_HSM="${BACKUP_HSM:-http://backup-hsm:12345}"
AUTH_KEY_ID="${AUTH_KEY_ID:-2}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"
}

# Check HSM connectivity
check_hsm_connectivity() {
    local hsm_url="$1"
    local hsm_name="$2"

    if curl -s --max-time 5 "$hsm_url/connector/status" > /dev/null 2>&1; then
        log_info "$hsm_name HSM is accessible"
        return 0
    else
        log_error "$hsm_name HSM is not accessible at $hsm_url"
        return 1
    fi
}

# Get list of critical objects from primary HSM
get_critical_objects() {
    # In real implementation, query HSM for critical objects
    # For demo, return mock critical object IDs
    cat << 'EOF'
100:TDE Master Key
2000:SSL Certificate
2001:API Key
8000:LUKS Root Key
8001:LUKS Data Key
EOF
}

# Sync object from primary to backup HSM
sync_object() {
    local object_id="$1"
    local object_label="$2"

    log_info "Syncing $object_label (ID: $object_id) to backup HSM..."

    # In real implementation:
    # 1. Export wrapped key from primary HSM
    # 2. Import wrapped key to backup HSM
    # 3. Verify import was successful

    # Mock implementation
    sleep 1
    log_info "✓ $object_label synced successfully"
}

# Update application configurations
update_app_configs() {
    log_info "Updating application configurations to use backup HSM..."

    # Update scripts that reference HSM connector URL
    local scripts_to_update=(
        "get_luks_key.py"
        "unlock_luks_volumes.sh"
        "get-volume-key.sh"
        "postgres_key_fetch.sh"
        "yubihsm_complete_storage.sh"
        "yubihsm_lifecycle_management.sh"
    )

    for script in "${scripts_to_update[@]}"; do
        if [ -f "$SCRIPT_DIR/$script" ]; then
            log_info "Updating $script..."
            # In real implementation, update YUBIHSM_CONNECTOR variable
            sed -i "s|$PRIMARY_HSM|$BACKUP_HSM|g" "$SCRIPT_DIR/$script" 2>/dev/null || true
        fi
    done

    # Update systemd services
    log_info "Restarting systemd services..."
    # systemctl restart yubihsm-connector postgresql docker

    log_info "✓ Application configurations updated"
}

# Verify backup HSM functionality
verify_backup_hsm() {
    log_info "Verifying backup HSM functionality..."

    # Test key retrieval
    if [ -f "$SCRIPT_DIR/get_luks_key.py" ]; then
        local test_key
        test_key=$("$SCRIPT_DIR/get_luks_key.py" "$BACKUP_HSM" "$AUTH_KEY_ID" "password" "100" 2>/dev/null)
        if [ -n "$test_key" ]; then
            log_info "✓ Key retrieval test passed"
        else
            log_error "✗ Key retrieval test failed"
            return 1
        fi
    fi

    # Test storage operations
    if [ -f "$SCRIPT_DIR/yubihsm_complete_storage.sh" ]; then
        # Try a simple list operation
        if "$SCRIPT_DIR/yubihsm_complete_storage.sh" list > /dev/null 2>&1; then
            log_info "✓ Storage operations test passed"
        else
            log_error "✗ Storage operations test failed"
            return 1
        fi
    fi

    log_info "✓ Backup HSM verification completed"
    return 0
}

# Perform failover
perform_failover() {
    log_info "Starting failover to backup HSM..."
    log_info "Primary HSM: $PRIMARY_HSM"
    log_info "Backup HSM: $BACKUP_HSM"
    echo ""

    # Step 1: Verify backup HSM is accessible
    log_info "Step 1: Checking backup HSM connectivity..."
    if ! check_hsm_connectivity "$BACKUP_HSM" "Backup"; then
        log_error "Cannot proceed with failover - backup HSM is not accessible"
        exit 1
    fi

    # Step 2: Sync critical objects
    log_info "Step 2: Syncing critical objects..."
    local critical_objects
    critical_objects=$(get_critical_objects)

    while read -r object_info; do
        if [ -z "$object_info" ]; then
            continue
        fi

        local object_id

        object_id=$(echo "$object_info" | cut -d: -f1)
        local object_label
        object_label=$(echo "$object_info" | cut -d: -f2-)

        if ! sync_object "$object_id" "$object_label"; then
            log_error "Failed to sync $object_label - aborting failover"
            exit 1
        fi
    done <<< "$critical_objects"

    # Step 3: Update configurations
    log_info "Step 3: Updating application configurations..."
    update_app_configs

    # Step 4: Verify functionality
    log_info "Step 4: Verifying backup HSM functionality..."
    if ! verify_backup_hsm; then
        log_error "Backup HSM verification failed - manual intervention required"
        exit 1
    fi

    # Step 5: Complete failover
    log_info "Step 5: Failover completed successfully"
    log_info "All systems now using backup HSM: $BACKUP_HSM"

    # Send notification
    log_info "Sending failover notification..."
    # In real implementation: send email/SMS/Slack notification

    echo ""
    log_info "FAILOVER COMPLETED SUCCESSFULLY"
    log_info "Primary HSM: $PRIMARY_HSM (failed)"
    log_info "Active HSM: $BACKUP_HSM (active)"
    log_info "Timestamp: $(date)"
}

# Check if failover is needed
check_failover_needed() {
    log_info "Checking if failover is needed..."

    if check_hsm_connectivity "$PRIMARY_HSM" "Primary"; then
        log_info "Primary HSM is still accessible - no failover needed"
        return 1
    else
        log_warn "Primary HSM is not accessible - initiating failover"
        return 0
    fi
}

# Main execution
main() {
    log_info "YubiHSM Failover Script"
    echo ""

    if [ "${FORCE_FAILOVER:-false}" = "true" ]; then
        log_warn "Forced failover requested - proceeding without primary check"
        perform_failover
    elif check_failover_needed; then
        perform_failover
    else
        log_info "No action needed - primary HSM is operational"
        exit 0
    fi
}

# Run main function
main "$@"