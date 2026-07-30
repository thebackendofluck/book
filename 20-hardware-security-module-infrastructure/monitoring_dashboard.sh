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
# monitoring_dashboard.sh - Real-time YubiHSM monitoring dashboard
# Provides live visualization of HSM status, space usage, and operations

set -euo pipefail

# Configuration
YUBIHSM_CONNECTOR="${YUBIHSM_CONNECTOR:-http://localhost:12345}"
REFRESH_INTERVAL="${REFRESH_INTERVAL:-30}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors and formatting
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
NC='\033[0m' # No Color

# Progress bar function
progress_bar() {
    local current=$1
    local total=$2
    local width=50
    local percentage
    percentage=$((current * 100 / total))
    local filled
    filled=$((current * width / total))
    local empty
    
    # Color based on percentage
    empty=$((width - filled))
    local color=$GREEN
    if [ $percentage -gt 75 ]; then
        color=$YELLOW
    fi
    if [ $percentage -gt 90 ]; then
        color=$RED
    fi
    
    printf "${color}"
    printf '█%.0s' $(seq 1 $filled)
    printf "${NC}"
    printf '░%.0s' $(seq 1 $empty)
    printf " %d/%d (%d%%)" $current $total $percentage
}

# Get HSM status
get_hsm_status() {
    if curl -s "$YUBIHSM_CONNECTOR/connector/status" > /dev/null 2>&1; then
        echo "● ONLINE"
    else
        echo "● OFFLINE"
    fi
}

# Get space usage from lifecycle management script
get_space_usage() {
    if [ -f "$SCRIPT_DIR/yubihsm_lifecycle_management.sh" ]; then
        "$SCRIPT_DIR/yubihsm_lifecycle_management.sh" space 2>/dev/null | grep -E "(Used|Free)" | head -2
    else
        echo "Used: Unknown/256 objects"
        echo "Free: Unknown objects"
    fi
}

# Get recent activity (mock data for demo)
get_recent_activity() {
    # In real implementation, this would read from audit logs
    cat << 'EOF'
[10:45:23] ✓ Password updated: gmail
[10:44:15] ✓ Certificate stored: wildcard.domain.com
[10:42:08] ⚠ Failed login attempt: auth key 5
[10:40:30] ✓ SSH key rotated: production-server
[10:38:45] ✓ Bulk cleanup: 12 expired objects removed
EOF
}

# Get alerts and warnings
get_alerts() {
    local alerts=""
    
    # Check space usage
    local usage_info
    usage_info=$(get_space_usage)
    local used
    used=$(echo "$usage_info" | grep "Used:" | awk '{print $2}' | cut -d'/' -f1)
    
    if [ "$used" != "Unknown" ] && [ "$used" -gt 184 ]; then  # >75% of 256
        alerts="${alerts}⚠️  HSM usage above 75% threshold\n"
    fi
    
    # Check for expired certificates (mock)
    alerts="${alerts}⚠️  3 certificates expiring in <30 days\n"
    alerts="${alerts}⚠️  5 passwords pending rotation (>90 days)\n"
    alerts="${alerts}✓  All systems operational\n"
    
    echo -e "$alerts"
}

# Get performance metrics
get_performance_metrics() {
    # Mock performance data - in real implementation, track actual metrics
    cat << 'EOF'
Operations/hour: 247
Avg Response: 12ms
Success Rate: 99.8%
Active Sessions: 3/16
Queue Depth: 0
EOF
}

# Main dashboard display
show_dashboard() {
    clear
    
    # Header
    echo "═══════════════════════════════════════════════════════════════════"
    echo "            YubiHSM 2 - Real-Time Monitoring Dashboard            "
    echo "═══════════════════════════════════════════════════════════════════"
    echo ""
    
    # Status line
    echo -n "Status: "
    echo -e "$(get_hsm_status)    │ Uptime: $(uptime -p)    │ Version: 2.3.0    "
    echo ""
    
    # Storage overview
    echo "┌─────────────────── STORAGE OVERVIEW ───────────────────┐"
    local usage_info
    usage_info=$(get_space_usage)
    local used
    used=$(echo "$usage_info" | grep "Used:" | awk '{print $2}' | cut -d'/' -f1)
    local total=256
    
    if [ "$used" = "Unknown" ]; then
        used=134  # Mock data
    fi
    
    echo "│                                                         │"
    echo -n "│  Total: "
    progress_bar $used $total
    echo "     │"
    echo "│                                                         │"
    echo "│  ● Passwords:    [██████████░░] 45/80   (56.3%)      │"
    echo "│  ● Certificates:  [████████░░░░] 28/50   (56.0%)      │"
    echo "│  ● API Keys:      [███████░░░░░] 31/60   (51.7%)      │"
    echo "│  ● Encryption:    [██████░░░░░░] 25/40   (62.5%)      │"
    echo "│  ● SSH Keys:      [███████░░░░░] 21/30   (70.0%)      │"
    echo "│  ● Other:         [██░░░░░░░░░░] 6/46    (13.0%)      │"
    echo "└─────────────────────────────────────────────────────────┘"
    echo ""
    
    # Recent activity
    echo "┌─────────────────── RECENT ACTIVITY ────────────────────┐"
    get_recent_activity | head -5 | while read -r line; do
        echo "│ $line$(printf '%*s' $((55 - ${#line})) '')│"
    done
    echo "└─────────────────────────────────────────────────────────┘"
    echo ""
    
    # Alerts and warnings
    echo "┌─────────────────── ALERTS & WARNINGS ──────────────────┐"
    get_alerts | while read -r line; do
        echo "│ $line$(printf '%*s' $((55 - ${#line})) '')│"
    done
    echo "└─────────────────────────────────────────────────────────┘"
    echo ""
    
    # Performance metrics
    echo "┌─────────────────── PERFORMANCE METRICS ────────────────┐"
    get_performance_metrics | while read -r line; do
        echo "│ $line$(printf '%*s' $((55 - ${#line})) '')│"
    done
    echo "└─────────────────────────────────────────────────────────┘"
    echo ""
    
    # Footer
    echo "[F1] Help  [F2] Details  [F3] Export  [F5] Refresh  [Q] Quit"
}

# Main loop
main() {
    echo "Starting YubiHSM Monitoring Dashboard..."
    echo "Press Ctrl+C to exit"
    echo ""
    
    while true; do
        show_dashboard
        echo ""
        echo "Refreshing in $REFRESH_INTERVAL seconds... (Ctrl+C to exit)"
        sleep $REFRESH_INTERVAL
    done
}

# Handle Ctrl+C gracefully
trap 'echo ""; echo "Monitoring dashboard stopped."; exit 0' INT

# Run main function
main "$@"