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

# shellcheck disable=SC2034,SC2086,SC2155
# monitor_tde_performance.sh - Monitor database TDE performance metrics
# Tracks encryption/decryption overhead and HSM key retrieval latency
#
# Usage: ./monitor_tde_performance.sh
# Environment: MONITOR_INTERVAL, LOG_FILE

set -euo pipefail

# Configuration
MONITOR_INTERVAL="${MONITOR_INTERVAL:-60}"  # seconds
LOG_FILE="${LOG_FILE:-/var/log/tde_performance.log}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"; }

# Get database TDE metrics (replace with actual database queries in production)
get_postgres_metrics() {
    cat << 'EOF'
{
  "connections": 45,
  "active_queries": 12,
  "tde_operations": 1250,
  "avg_query_time": 45.2,
  "cache_hit_ratio": 0.94
}
EOF
}

# Get HSM key retrieval metrics
get_hsm_metrics() {
    cat << 'EOF'
{
  "key_retrievals": 1250,
  "avg_retrieval_time": 12.5,
  "max_retrieval_time": 45.8,
  "failed_retrievals": 0,
  "hsm_load": 0.23
}
EOF
}

# Calculate TDE performance metrics
calculate_performance() {
    local postgres_data=$(get_postgres_metrics)
    local hsm_data=$(get_hsm_metrics)

    local connections=$(echo "$postgres_data" | jq -r '.connections')
    local tde_ops=$(echo "$postgres_data" | jq -r '.tde_operations')
    local avg_query=$(echo "$postgres_data" | jq -r '.avg_query_time')
    local key_retrievals=$(echo "$hsm_data" | jq -r '.key_retrievals')
    local avg_retrieval=$(echo "$hsm_data" | jq -r '.avg_retrieval_time')
    local failed_ops=$(echo "$hsm_data" | jq -r '.failed_retrievals')

    local tde_ops_per_sec=$(echo "scale=2; $tde_ops / $MONITOR_INTERVAL" | bc 2>/dev/null || echo "0")
    local key_ops_per_sec=$(echo "scale=2; $key_retrievals / $MONITOR_INTERVAL" | bc 2>/dev/null || echo "0")
    local efficiency=$(echo "scale=3; $tde_ops / ($key_retrievals + 1)" | bc 2>/dev/null || echo "0")

    cat << EOF
{
  "timestamp": "$(date -Iseconds)",
  "tde_operations_per_second": $tde_ops_per_sec,
  "key_retrievals_per_second": $key_ops_per_sec,
  "avg_query_time_ms": $avg_query,
  "avg_key_retrieval_ms": $avg_retrieval,
  "cache_efficiency": $efficiency,
  "failed_operations": $failed_ops,
  "active_connections": $connections
}
EOF
}

# Check performance thresholds
check_thresholds() {
    local metrics="$1"

    local avg_query=$(echo "$metrics" | jq -r '.avg_query_time_ms')
    local avg_retrieval=$(echo "$metrics" | jq -r '.avg_key_retrieval_ms')
    local failed_ops=$(echo "$metrics" | jq -r '.failed_operations')
    local alerts=""

    # Query time threshold: <100ms for acceptable TDE overhead
    if (( $(echo "$avg_query > 100" | bc -l 2>/dev/null || echo "0") )); then
        alerts="${alerts}WARNING: High query time (${avg_query}ms)\n"
    fi

    # Key retrieval threshold: <50ms for HSM responsiveness
    if (( $(echo "$avg_retrieval > 50" | bc -l 2>/dev/null || echo "0") )); then
        alerts="${alerts}WARNING: Slow key retrieval (${avg_retrieval}ms)\n"
    fi

    if [ "$failed_ops" -gt 0 ]; then
        alerts="${alerts}ERROR: $failed_ops failed TDE operations\n"
    fi

    if [ -n "$alerts" ]; then
        echo -e "$alerts"
        return 1
    fi
    return 0
}

# Log and display metrics
log_metrics() {
    local metrics="$1"
    echo "$metrics" >> "$LOG_FILE"
    if [ -f "$LOG_FILE" ]; then
        tail -n 1000 "$LOG_FILE" > "${LOG_FILE}.tmp" && mv "${LOG_FILE}.tmp" "$LOG_FILE"
    fi
}

display_metrics() {
    local metrics="$1"
    echo "=== Database TDE Performance Monitor ==="
    echo ""
    echo "$metrics" | jq -r '
        "TDE Operations/sec: \(.tde_operations_per_second)",
        "Key Retrievals/sec: \(.key_retrievals_per_second)",
        "Avg Query Time: \(.avg_query_time_ms)ms",
        "Avg Key Retrieval: \(.avg_key_retrieval_ms)ms",
        "Cache Efficiency: \(.cache_efficiency)",
        "Failed Operations: \(.failed_operations)",
        "Active Connections: \(.active_connections)"
    '
    echo ""
    echo "Last updated: $(date)"
}

# Main monitoring loop
main() {
    log_info "Starting TDE performance monitoring..."
    log_info "Monitor interval: ${MONITOR_INTERVAL} seconds"
    log_info "Log file: $LOG_FILE"

    while true; do
        local metrics=$(calculate_performance)
        if ! check_thresholds "$metrics" > /dev/null; then
            check_thresholds "$metrics"
        fi
        log_metrics "$metrics"
        display_metrics "$metrics"
        echo "Next update in ${MONITOR_INTERVAL} seconds... (Ctrl+C to exit)"
        sleep $MONITOR_INTERVAL
        clear
    done
}

trap 'echo ""; log_info "TDE performance monitoring stopped."; exit 0' INT
main "$@"
