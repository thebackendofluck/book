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

# SED SSD Monitoring and Compliance Script
# Comprehensive monitoring, health checks, and compliance reporting for SED SSDs

set -euo pipefail

# ============================================================================
# SED SSD MONITORING AND COMPLIANCE
# ============================================================================

# Configuration
YUBIHSM_CONNECTOR_URL="${YUBIHSM_CONNECTOR_URL:-http://localhost:12345}"
YUBIHSM_AUTH_KEY="${YUBIHSM_AUTH_KEY:-2}"
LOG_FILE="/var/log/sed-ssd-monitoring.log"
REPORT_DIR="/var/log/sed-ssd-reports"
CONFIG_DIR="/etc/yubihsm/sed-ssds"

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

# Check prerequisites
check_prerequisites() {
    local missing_tools=()
    
    for tool in sedutil-cli smartctl jq curl awk bc; do
        if ! command -v "$tool" &> /dev/null; then
            missing_tools+=("$tool")
        fi
    done
    
    if [ ${#missing_tools[@]} -gt 0 ]; then
        error "Missing required tools: ${missing_tools[*]}"
    fi
    
    success "All prerequisites met"
}

# Get SED device information
get_sed_device_info() {
    local device="$1"
    local info_file
    info_file="/tmp/sed_info_$(basename "$device").json"
    
    # Get basic device info
    local model="Unknown"
    local serial="Unknown"
    local firmware="Unknown"
    local sed_standard="Unknown"
    
    # Try sedutil-cli
    if sedutil-cli --query "$device" &>/dev/null; then
        # Parse sedutil output for model/serial
        model=$(sedutil-cli --query "$device" 2>/dev/null | grep "Model" | sed 's/.*: //' | tr -d '\n' || echo "Unknown")
        serial=$(sedutil-cli --query "$device" 2>/dev/null | grep "Serial" | sed 's/.*: //' | tr -d '\n' || echo "Unknown")
        sed_standard="TCG Opal 2.0"
    fi
    
    # Try hdparm as fallback
    if [ "$model" = "Unknown" ]; then
        model=$(hdparm -I "$device" 2>/dev/null | grep "Model Number" | sed 's/.*: //' | tr -d '\n' || echo "Unknown")
        serial=$(hdparm -I "$device" 2>/dev/null | grep "Serial Number" | sed 's/.*: //' | tr -d '\n' || echo "Unknown")
    fi
    
    # Get SMART info
    local health_status="Unknown"
    local temperature="Unknown"
    local power_on_hours="Unknown"
    
    if smartctl -a "$device" &>/dev/null; then
        if smartctl -H "$device" | grep -q "PASSED"; then
            health_status="PASSED"
        else
            health_status="FAILED"
        fi
        
        temperature=$(smartctl -a "$device" | grep "Temperature" | head -1 | awk '{print $10}' | tr -d '\n' || echo "Unknown")
        power_on_hours=$(smartctl -a "$device" | grep "Power_On_Hours" | awk '{print $10}' | tr -d '\n' || echo "Unknown")
    fi
    
    # Get SED lock status
    local lock_status="Unknown"
    if sedutil-cli --query "$device" 2>/dev/null | grep -q "Locked = Y"; then
        lock_status="Locked"
        elif sedutil-cli --query "$device" 2>/dev/null | grep -q "Locked = N"; then
        lock_status="Unlocked"
    fi
    
    # Create JSON output
    cat > "$info_file" << EOF
{
    "device": "$device",
    "model": "$model",
    "serial": "$serial",
    "firmware": "$firmware",
    "sed_standard": "$sed_standard",
    "health_status": "$health_status",
    "temperature": "$temperature",
    "power_on_hours": "$power_on_hours",
    "lock_status": "$lock_status",
    "timestamp": "$(date -Iseconds)"
}
EOF
    
    echo "$info_file"
}

# Monitor SED device health
monitor_sed_health() {
    local device="$1"
    
    info "Monitoring health of SED device: $device"
    
    local info_file
    info_file=$(get_sed_device_info "$device")
    
    # Parse health information
    local health_status
    health_status=$(jq -r '.health_status' "$info_file")
    
    local temperature
    temperature=$(jq -r '.temperature' "$info_file")
    
    local lock_status
    lock_status=$(jq -r '.lock_status' "$info_file")
    
    # Health assessment
    local overall_health="GOOD"
    local issues=()
    
    if [ "$health_status" = "FAILED" ]; then
        overall_health="CRITICAL"
        issues+=("SMART health check failed")
    fi
    
    if [ "$temperature" != "Unknown" ] && [ "$temperature" -gt 60 ]; then
        overall_health="WARNING"
        issues+=("High temperature: ${temperature}°C")
    fi
    
    if [ "$lock_status" = "Unknown" ]; then
        overall_health="WARNING"
        issues+=("Unable to determine lock status")
    fi
    
    # Output health report
    echo "SED Device Health Report for $device"
    echo "=================================="
    echo "Overall Health: $overall_health"
    echo "SMART Status: $health_status"
    echo "Temperature: $temperature°C"
    echo "Lock Status: $lock_status"
    
    if [ ${#issues[@]} -gt 0 ]; then
        echo ""
        echo "Issues Found:"
        for issue in "${issues[@]}"; do
            echo "  - $issue"
        done
    fi
    
    # Cleanup
    rm -f "$info_file"
    
    # Return health status for scripting
    case $overall_health in
        "GOOD")
            return 0
        ;;
        "WARNING")
            return 1
        ;;
        "CRITICAL")
            return 2
        ;;
        *)
            return 3
        ;;
    esac
}

# Generate compliance report
generate_compliance_report() {
    local output_file="${1:-/tmp/sed-compliance-report-$(date +%Y%m%d-%H%M%S).json}"
    
    info "Generating SED SSD compliance report: $output_file"
    
    mkdir -p "$(dirname "$output_file")"
    
    # Initialize report structure
    local report
    report='{
        "report_type": "SED SSD Compliance Report",
        "generated_at": "'"$(date -Iseconds)"'",
        "system_info": {},
        "devices": [],
        "compliance_status": {},
        "recommendations": []
    }'
    
    # System information
    report=$(echo "$report" | jq \
        --arg hostname "$(hostname)" \
        --arg kernel "$(uname -r)" \
        --arg arch "$(uname -m)" \
        --arg connector "$YUBIHSM_CONNECTOR_URL" \
        '.system_info = {"hostname": $hostname, "kernel": $kernel, "architecture": $arch, "yubihsm_connector": $connector}')
    
    # Check YubiHSM connectivity
    local yubihsm_status="NOT CONNECTED"
    if curl -s "$YUBIHSM_CONNECTOR_URL/connector/status" &>/dev/null; then
        yubihsm_status="CONNECTED"
    fi
    
    # Scan for SED devices
    local sed_devices=()
    while IFS= read -r line; do
        if [[ $line =~ Device\ ([^[:space:]]+).*TCG.* ]]; then
            device="/dev/${BASH_REMATCH[1]}"
            if [ -b "$device" ]; then
                sed_devices+=("$device")
            fi
        fi
    done < <(sedutil-cli --scan 2>/dev/null || true)
    
    # Analyze each SED device
    local total_devices=${#sed_devices[@]}
    local compliant_devices=0
    local non_compliant_devices=0
    
    for device in "${sed_devices[@]}"; do
        info "Analyzing device: $device"
        
        local info_file
        info_file=$(get_sed_device_info "$device")
        
        # Compliance checks
        local device_compliant=true
        local compliance_issues=()
        
        # Check 1: SED-capable device
        if ! jq -e '.sed_standard != "Unknown"' "$info_file" &>/dev/null; then
            device_compliant=false
            compliance_issues+=("Device is not SED-capable")
        fi
        
        # Check 2: Health status
        if [ "$(jq -r '.health_status' "$info_file")" = "FAILED" ]; then
            device_compliant=false
            compliance_issues+=("SMART health check failed")
        fi
        
        # Check 3: YubiHSM integration
        local config_file
        config_file="$CONFIG_DIR/$(basename "$device").json"
        if [ ! -f "$config_file" ]; then
            device_compliant=false
            compliance_issues+=("No YubiHSM configuration found")
        else
            # Check if key exists in YubiHSM
            local key_id
            key_id=$(jq -r '.auth_key_id' "$config_file" 2>/dev/null || echo "null")
            if [ "$key_id" != "null" ]; then
                # Test key retrieval (simplified check)
                if ! python3 -c "
import sys
from yubihsm import YubiHsm
try:
    hsm = YubiHsm.connect('$YUBIHSM_CONNECTOR_URL')
    session = hsm.create_session_derived($YUBIHSM_AUTH_KEY, 'password')
    key = session.get_object($key_id, 2)  # SYMMETRIC_KEY
    session.close()
    print('OK')
except:
    print('FAILED')
                " 2>/dev/null | grep -q "OK"; then
                    device_compliant=false
                    compliance_issues+=("YubiHSM key not accessible")
                fi
            fi
        fi
        
        # Check 4: Lock status (should be unlocked for active use, but configurable)
        local lock_status
        lock_status=$(jq -r '.lock_status' "$info_file")
        if [ "$lock_status" = "Unknown" ]; then
            compliance_issues+=("Unable to determine lock status")
        fi
        
        # Update compliance counters
        if [ "$device_compliant" = true ]; then
            ((compliant_devices++))
        else
            ((non_compliant_devices++))
        fi
        
        # Add device to report
        local device_info
        device_info=$(cat "$info_file")
        device_info=$(echo "$device_info" | jq ".compliant = $device_compliant")
        device_info=$(echo "$device_info" | jq ".compliance_issues = $(printf '%s\n' "${compliance_issues[@]}" | jq -R . | jq -s .)")
        
        report=$(echo "$report" | jq ".devices += [$device_info]")
        
        # Cleanup
        rm -f "$info_file"
    done
    
    # Overall compliance status
    local overall_compliance="NON_COMPLIANT"
    local compliance_percentage=0
    
    if [ "$total_devices" -gt 0 ]; then
        compliance_percentage=$((compliant_devices * 100 / total_devices))
        if [ "$compliance_percentage" -ge 80 ]; then
            overall_compliance="COMPLIANT"
            elif [ "$compliance_percentage" -ge 50 ]; then
            overall_compliance="PARTIALLY_COMPLIANT"
        fi
    fi
    
    report=$(echo "$report" | jq ".compliance_status = {
        \"overall_status\": \"$overall_compliance\",
        \"total_devices\": $total_devices,
        \"compliant_devices\": $compliant_devices,
        \"non_compliant_devices\": $non_compliant_devices,
        \"compliance_percentage\": $compliance_percentage,
        \"yubihsm_status\": \"$yubihsm_status\"
    }")
    
    # Generate recommendations
    local recommendations=()
    
    if [ "$total_devices" -eq 0 ]; then
        recommendations+=("No SED devices detected - consider deploying SED SSDs for enhanced security")
    fi
    
    if [ "$yubihsm_status" != "CONNECTED" ]; then
        recommendations+=("YubiHSM connector is not accessible - ensure YubiHSM service is running")
    fi
    
    if [ "$compliance_percentage" -lt 100 ] && [ "$total_devices" -gt 0 ]; then
        recommendations+=("Not all SED devices are compliant - review device configurations")
    fi
    
    if [ $non_compliant_devices -gt 0 ]; then
        recommendations+=("Address compliance issues on $non_compliant_devices non-compliant devices")
    fi
    
    report=$(echo "$report" | jq ".recommendations = $(printf '%s\n' "${recommendations[@]}" | jq -R . | jq -s .)")
    
    # Write report
    echo "$report" | jq '.' > "$output_file"
    
    success "Compliance report generated: $output_file"
    
    # Print summary
    echo ""
    echo "Compliance Summary:"
    echo "==================="
    echo "Overall Status: $overall_compliance"
    echo "Total Devices: $total_devices"
    echo "Compliant: $compliant_devices"
    echo "Non-Compliant: $non_compliant_devices"
    echo "Compliance Rate: ${compliance_percentage}%"
    echo "YubiHSM Status: $yubihsm_status"
    
    if [ ${#recommendations[@]} -gt 0 ]; then
        echo ""
        echo "Recommendations:"
        for rec in "${recommendations[@]}"; do
            echo "  • $rec"
        done
    fi
}

# Monitor all SED devices
monitor_all_devices() {
    info "Monitoring all SED devices..."
    
    local sed_devices=()
    while IFS= read -r line; do
        if [[ $line =~ Device\ ([^[:space:]]+).*TCG.* ]]; then
            device="/dev/${BASH_REMATCH[1]}"
            if [ -b "$device" ]; then
                sed_devices+=("$device")
            fi
        fi
    done < <(sedutil-cli --scan 2>/dev/null || true)
    
    if [ ${#sed_devices[@]} -eq 0 ]; then
        warning "No SED devices found"
        return
    fi
    
    echo "Found ${#sed_devices[@]} SED device(s)"
    echo ""
    
    local healthy_devices=0
    local warning_devices=0
    local critical_devices=0
    
    for device in "${sed_devices[@]}"; do
        echo "Monitoring: $device"
        echo "-------------------"
        
        if monitor_sed_health "$device"; then
            case $? in
                0) ((healthy_devices++)) ;;
                1) ((warning_devices++)) ;;
                2) ((critical_devices++)) ;;
            esac
        else
            ((critical_devices++))
        fi
        
        echo ""
    done
    
    echo "Summary:"
    echo "========"
    echo "Healthy devices: $healthy_devices"
    echo "Warning devices: $warning_devices"
    echo "Critical devices: $critical_devices"
    echo "Total devices: ${#sed_devices[@]}"
}

# Show dashboard
show_dashboard() {
    echo -e "${PURPLE}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${PURPLE}║         YubiHSM 2 SED SSD Monitoring Dashboard              ║${NC}"
    echo -e "${PURPLE}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    
    # System status
    echo -e "${CYAN}System Status:${NC}"
    echo "=============="
    
    # YubiHSM status
    if curl -s "$YUBIHSM_CONNECTOR_URL/connector/status" &>/dev/null; then
        echo -e "YubiHSM Connector: ${GREEN}● CONNECTED${NC}"
    else
        echo -e "YubiHSM Connector: ${RED}● DISCONNECTED${NC}"
    fi
    
    # SED devices count
    local sed_count=0
    while IFS= read -r line; do
        if [[ $line =~ Device\ ([^[:space:]]+).*TCG.* ]]; then
            device="/dev/${BASH_REMATCH[1]}"
            if [ -b "$device" ]; then
                ((sed_count++))
            fi
        fi
    done < <(sedutil-cli --scan 2>/dev/null || true)
    
    echo "SED Devices Detected: $sed_count"
    echo ""
    
    # Device status
    if [ $sed_count -gt 0 ]; then
        echo -e "${CYAN}Device Status:${NC}"
        echo "=============="
        
        while IFS= read -r line; do
            if [[ $line =~ Device\ ([^[:space:]]+).*TCG.* ]]; then
                device="/dev/${BASH_REMATCH[1]}"
                if [ -b "$device" ]; then
                    local info_file
                    info_file=$(get_sed_device_info "$device")
                    
                    local model serial health lock
                    model=$(jq -r '.model' "$info_file")
                    serial=$(jq -r '.serial' "$info_file")
                    health=$(jq -r '.health_status' "$info_file")
                    lock=$(jq -r '.lock_status' "$info_file")
                    
                    # Health indicator
                    local health_color=$GREEN
                    if [ "$health" = "FAILED" ]; then
                        health_color=$RED
                    fi
                    
                    echo -e "Device: $device"
                    echo -e "  Model: $model"
                    echo -e "  Serial: $serial"
                    echo -e "  Health: ${health_color}$health${NC}"
                    echo -e "  Status: $lock"
                    echo ""
                fi
            fi
        done < <(sedutil-cli --scan 2>/dev/null || true)
    fi
    
    # Recent activity
    if [ -f "$LOG_FILE" ]; then
        echo -e "${CYAN}Recent Activity:${NC}"
        echo "================="
        tail -10 "$LOG_FILE" | while read -r line; do
            echo "  $line"
        done
        echo ""
    fi
    
    echo -e "${YELLOW}Press Ctrl+C to exit, or run with --continuous for live monitoring${NC}"
}

# Continuous monitoring mode
continuous_monitoring() {
    local interval="${1:-300}"  # Default 5 minutes
    
    info "Starting continuous monitoring (interval: ${interval}s)"
    
    while true; do
        clear
        show_dashboard
        
        # Generate compliance report every hour
        local current_time
        current_time=$(date +%M)
        if [ "$current_time" = "00" ]; then
            generate_compliance_report "$REPORT_DIR/compliance-$(date +%Y%m%d-%H%M%S).json" >/dev/null 2>&1
        fi
        
        sleep "$interval"
    done
}

# Main function
main() {
    mkdir -p "$REPORT_DIR"
    
    case "${1:-}" in
        health)
            if [ $# -lt 2 ]; then
                error "Usage: $0 health <device>"
            fi
            monitor_sed_health "$2"
        ;;
        monitor)
            monitor_all_devices
        ;;
        compliance)
            local output_file="${2:-$REPORT_DIR/compliance-$(date +%Y%m%d-%H%M%S).json}"
            generate_compliance_report "$output_file"
        ;;
        dashboard)
            show_dashboard
        ;;
        continuous)
            local interval="${2:-300}"
            continuous_monitoring "$interval"
        ;;
        *)
            echo -e "${PURPLE}SED SSD Monitoring and Compliance${NC}"
            echo ""
            echo "Usage: $0 <command> [options]"
            echo ""
            echo "Commands:"
            echo "  health <device>        Check health of specific SED device"
            echo "  monitor                 Monitor all SED devices"
            echo "  compliance [file]       Generate compliance report"
            echo "  dashboard               Show monitoring dashboard"
            echo "  continuous [interval]   Continuous monitoring mode"
            echo ""
            echo "Examples:"
            echo "  $0 monitor"
            echo "  $0 health /dev/sdb"
            echo "  $0 compliance /tmp/report.json"
            echo "  $0 dashboard"
            echo "  $0 continuous 600  # Monitor every 10 minutes"
            exit 1
        ;;
    esac
}

# Create log directory
mkdir -p "$(dirname "$LOG_FILE")"

# Run prerequisites check
check_prerequisites

# Run main function
main "$@"