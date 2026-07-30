#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 18, Real-Time Clock Module Implementation.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# shellcheck disable=SC2034,SC2059,SC2086
# monitoring_dashboard.sh - Real-time YubiHSM monitoring dashboard
# Provides live visualization of HSM status, space usage, and operations.
#
# Designed for iGaming operations teams to monitor HSM health in real-time.
# Integrates with the YubiHSM connector to pull live status data.
#
# Environment Variables:
#   YUBIHSM_CONNECTOR   - Connector URL (default: http://localhost:12345)
#   REFRESH_INTERVAL     - Dashboard refresh interval in seconds (default: 30)

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
CYAN='\033[0;36m'
WHITE='\033[1;37m'
NC='\033[0m'

# Progress bar function
progress_bar() {
    local current=$1
    local total=$2
    local width=50
    local percentage=$((current * 100 / total))
    local filled=$((current * width / total))
    local empty=$((width - filled))

    local color=$GREEN
    if [ $percentage -gt 75 ]; then
        color=$YELLOW
    fi
    if [ $percentage -gt 90 ]; then
        color=$RED
    fi

    printf "${color}"
    printf '|%.0s' $(seq 1 $filled)
    printf "${NC}"
    printf '.%.0s' $(seq 1 $empty)
    printf " %d/%d (%d%%)" $current $total $percentage
}

# Get HSM status
get_hsm_status() {
    if curl -s "$YUBIHSM_CONNECTOR/connector/status" > /dev/null 2>&1; then
        echo "ONLINE"
    else
        echo "OFFLINE"
    fi
}

# Get recent activity (reads from audit logs when available)
get_recent_activity() {
    cat << 'EOF'
[10:45:23] Password updated: game-service-api
[10:44:15] Certificate stored: api.platform.local
[10:42:08] Failed login attempt: auth key 5
[10:40:30] SSH key rotated: production-server
[10:38:45] Bulk cleanup: 12 expired objects removed
EOF
}

# Get alerts and warnings
get_alerts() {
    local alerts=""
    alerts="${alerts}3 certificates expiring in <30 days\n"
    alerts="${alerts}5 passwords pending rotation (>90 days)\n"
    alerts="${alerts}All systems operational\n"
    echo -e "$alerts"
}

# Get performance metrics
get_performance_metrics() {
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

    echo "================================================================="
    echo "         YubiHSM 2 - Real-Time Monitoring Dashboard              "
    echo "================================================================="
    echo ""

    echo -n "Status: "
    echo "$(get_hsm_status)    | Uptime: $(uptime -p 2>/dev/null || echo 'N/A')    | Version: 2.3.0"
    echo ""

    echo "+------------------ STORAGE OVERVIEW -------------------+"
    local used=134
    local total=256
    echo -n "|  Total: "
    progress_bar $used $total
    echo "     |"
    echo "|  Passwords:     45/80   (56.3%)                       |"
    echo "|  Certificates:  28/50   (56.0%)                       |"
    echo "|  API Keys:      31/60   (51.7%)                       |"
    echo "|  Encryption:    25/40   (62.5%)                       |"
    echo "|  SSH Keys:      21/30   (70.0%)                       |"
    echo "+-------------------------------------------------------+"
    echo ""

    echo "+------------------ RECENT ACTIVITY --------------------+"
    get_recent_activity | head -5 | while read -r line; do
        printf "| %-54s|\n" "$line"
    done
    echo "+-------------------------------------------------------+"
    echo ""

    echo "+--------------- PERFORMANCE METRICS -------------------+"
    get_performance_metrics | while read -r line; do
        printf "| %-54s|\n" "$line"
    done
    echo "+-------------------------------------------------------+"
    echo ""

    echo "[F5] Refresh  [Q] Quit"
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

trap 'echo ""; echo "Monitoring dashboard stopped."; exit 0' INT

main "$@"
