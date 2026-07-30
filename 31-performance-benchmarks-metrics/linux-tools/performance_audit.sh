#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 31, Performance Benchmarks and Metrics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# shellcheck disable=SC2002
#
# iGaming Performance Audit Script
# Based on Brendan Gregg's USE Methodology
#
# This script performs a comprehensive performance audit using the USE method:
# - Utilization: How busy is the resource?
# - Saturation: How much work is waiting?
# - Errors: Are there any error conditions?
#
# References:
# - "Systems Performance: Enterprise and the Cloud" (2nd Ed) - Brendan Gregg
# - "BPF Performance Tools" - Brendan Gregg
# - brendangregg.com/USE-method
#
# Usage: ./performance_audit.sh [--full|--quick|--help]
#

set -euo pipefail

# Colors for output
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly NC='\033[0m' # No Color

# Audit mode
AUDIT_MODE="${1:-quick}"

# Header
print_header() {
    echo -e "${BLUE}============================================${NC}"
    echo -e "${BLUE}  iGaming Performance Audit${NC}"
    echo -e "${BLUE}  Based on Brendan Gregg's USE Method${NC}"
    echo -e "${BLUE}============================================${NC}"
    echo ""
    echo "Date: $(date)"
    echo "Hostname: $(hostname)"
    echo "Kernel: $(uname -r)"

    # Check if running on AWS
    if curl -s --connect-timeout 1 http://169.254.169.254/latest/meta-data/ >/dev/null 2>&1; then
        echo "Instance Type: $(curl -s http://169.254.169.254/latest/meta-data/instance-type 2>/dev/null || echo 'Unknown')"
        echo "Instance ID: $(curl -s http://169.254.169.254/latest/meta-data/instance-id 2>/dev/null || echo 'Unknown')"
        echo "Availability Zone: $(curl -s http://169.254.169.254/latest/meta-data/placement/availability-zone 2>/dev/null || echo 'Unknown')"
    fi
    echo ""
}

# Section header
section() {
    echo -e "\n${YELLOW}=== $1 ===${NC}\n"
}

# Status indicators
ok() {
    echo -e "${GREEN}[OK]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

critical() {
    echo -e "${RED}[CRITICAL]${NC} $1"
}

info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

# CPU Analysis (USE Method)
analyze_cpu() {
    section "CPU Analysis (USE Method)"

    # Utilization
    info "CPU Utilization:"
    echo "Load averages (1, 5, 15 min):"
    uptime

    local cpu_count
    cpu_count=$(nproc)
    local load_1min
    load_1min=$(awk '{print $1}' /proc/loadavg)

    if command -v bc &>/dev/null; then
        local load_ratio
        load_ratio=$(echo "$load_1min / $cpu_count" | bc -l 2>/dev/null || echo "0")
        if (( $(echo "$load_ratio > 1.0" | bc -l 2>/dev/null || echo 0) )); then
            critical "System is CPU saturated (load > CPU count)"
        elif (( $(echo "$load_ratio > 0.7" | bc -l 2>/dev/null || echo 0) )); then
            warn "CPU utilization is high (>70%)"
        else
            ok "CPU load is healthy"
        fi
    fi

    echo ""

    # Per-CPU breakdown
    if command -v mpstat &>/dev/null; then
        info "Per-CPU Statistics (5 second sample):"
        mpstat -P ALL 1 5 | tail -20
    else
        warn "mpstat not available. Install sysstat package."
    fi

    # Saturation
    echo ""
    info "CPU Saturation (run queue):"
    if command -v vmstat &>/dev/null; then
        vmstat 1 5 | head -10
        echo "Key: 'r' column shows run queue (processes waiting for CPU)"
    fi

    # Errors
    echo ""
    info "CPU Errors/Issues:"
    if command -v dmesg &>/dev/null; then
        local cpu_errors
        cpu_errors=$(dmesg 2>/dev/null | grep -iE 'cpu|mce|thermal' | tail -5 || true)
        if [[ -n "$cpu_errors" ]]; then
            warn "Recent CPU-related messages in dmesg:"
            echo "$cpu_errors"
        else
            ok "No recent CPU errors in dmesg"
        fi
    fi
}

# Memory Analysis (USE Method)
analyze_memory() {
    section "Memory Analysis (USE Method)"

    # Utilization
    info "Memory Utilization:"
    free -h
    echo ""

    local mem_available
    local mem_total
    mem_available=$(awk '/MemAvailable/ {print $2}' /proc/meminfo)
    mem_total=$(awk '/MemTotal/ {print $2}' /proc/meminfo)

    if [[ -n "$mem_available" ]] && [[ -n "$mem_total" ]] && [[ "$mem_total" -gt 0 ]]; then
        local mem_used_pct
        mem_used_pct=$((100 - (mem_available * 100 / mem_total)))

        if [[ $mem_used_pct -gt 90 ]]; then
            critical "Memory utilization is critical (${mem_used_pct}% used)"
        elif [[ $mem_used_pct -gt 80 ]]; then
            warn "Memory utilization is high (${mem_used_pct}% used)"
        else
            ok "Memory utilization is healthy (${mem_used_pct}% used)"
        fi
    fi

    # Saturation (swap activity)
    echo ""
    info "Memory Saturation (swap activity):"
    if command -v vmstat &>/dev/null; then
        vmstat 1 5 | head -10
        echo "Key: 'si' and 'so' columns show swap in/out (should be 0)"
    fi

    local swap_used
    swap_used=$(awk '/SwapTotal/ {total=$2} /SwapFree/ {free=$2} END {print total-free}' /proc/meminfo)
    if [[ "$swap_used" -gt 0 ]]; then
        warn "Swap is in use: $((swap_used / 1024)) MB"
    else
        ok "No swap usage detected"
    fi

    # Page faults
    echo ""
    info "Page Fault Statistics:"
    if command -v sar &>/dev/null; then
        sar -B 1 3 2>/dev/null | tail -5 || echo "sar -B not available"
        echo "Key: 'majflt/s' shows major (disk) page faults"
    fi

    # Errors
    echo ""
    info "Memory Errors:"
    if command -v dmesg &>/dev/null; then
        local oom_messages
        oom_messages=$(dmesg 2>/dev/null | grep -i 'out of memory\|oom\|killed process' | tail -5 || true)
        if [[ -n "$oom_messages" ]]; then
            critical "OOM killer has been active:"
            echo "$oom_messages"
        else
            ok "No OOM events detected"
        fi
    fi
}

# Disk I/O Analysis (USE Method)
analyze_disk() {
    section "Disk I/O Analysis (USE Method)"

    # Utilization
    info "Disk Utilization:"
    if command -v iostat &>/dev/null; then
        iostat -xz 1 3 | tail -20
        echo ""
        echo "Key metrics:"
        echo "  - %util: Device utilization (>80% indicates saturation)"
        echo "  - await: Average I/O wait time in ms"
        echo "  - avgqu-sz: Average queue size"
    else
        warn "iostat not available. Install sysstat package."
        df -h
    fi

    # Check for high utilization
    if command -v iostat &>/dev/null; then
        local high_util
        high_util=$(iostat -xz 1 1 2>/dev/null | awk 'NR>6 && $NF+0 > 80 {print $1": "$NF"%"}' || true)
        if [[ -n "$high_util" ]]; then
            warn "High disk utilization detected:"
            echo "$high_util"
        else
            ok "Disk utilization is healthy"
        fi
    fi

    # Filesystem usage
    echo ""
    info "Filesystem Usage:"
    df -h | grep -E '^/dev|Filesystem'

    local full_fs
    full_fs=$(df -h 2>/dev/null | awk 'NR>1 && int($5) > 85 {print $6": "$5}' || true)
    if [[ -n "$full_fs" ]]; then
        warn "Filesystems with >85% usage:"
        echo "$full_fs"
    fi

    # I/O Scheduler
    echo ""
    info "I/O Schedulers:"
    for disk in /sys/block/*/queue/scheduler; do
        if [[ -f "$disk" ]]; then
            local device
            device=$(echo "$disk" | cut -d'/' -f4)
            echo "$device: $(cat "$disk" 2>/dev/null || echo 'unknown')"
        fi
    done

    # Errors
    echo ""
    info "Disk Errors:"
    if command -v dmesg &>/dev/null; then
        local disk_errors
        disk_errors=$(dmesg 2>/dev/null | grep -iE 'error|fail|i/o|reset|timeout' | grep -iE 'sd|nvme|block' | tail -5 || true)
        if [[ -n "$disk_errors" ]]; then
            warn "Recent disk-related errors:"
            echo "$disk_errors"
        else
            ok "No recent disk errors"
        fi
    fi
}

# Network Analysis (USE Method)
analyze_network() {
    section "Network Analysis (USE Method)"

    # Utilization
    info "Network Interface Statistics:"
    if command -v sar &>/dev/null; then
        sar -n DEV 1 3 2>/dev/null | tail -15 || echo "sar -n DEV not available"
    else
        cat /proc/net/dev
    fi

    # Socket statistics
    echo ""
    info "Socket Statistics:"
    if command -v ss &>/dev/null; then
        ss -s
    else
        netstat -s | head -20
    fi

    # Connection states
    echo ""
    info "TCP Connection States:"
    if command -v ss &>/dev/null; then
        ss -tan | awk 'NR>1 {state[$1]++} END {for (s in state) print s": "state[s]}'
    fi

    # Check for high TIME_WAIT
    local time_wait_count
    time_wait_count=$(ss -tan 2>/dev/null | grep -c TIME-WAIT || echo 0)
    if [[ "$time_wait_count" -gt 10000 ]]; then
        warn "High TIME_WAIT count: $time_wait_count (consider tcp_tw_reuse)"
    fi

    # Errors and drops
    echo ""
    info "Network Errors and Drops:"
    if command -v sar &>/dev/null; then
        sar -n EDEV 1 3 2>/dev/null | tail -10 || echo "sar -n EDEV not available"
    fi

    # Check interface drops
    if command -v netstat &>/dev/null; then
        local drops
        drops=$(netstat -i 2>/dev/null | awk 'NR>2 && ($4+$8) > 0 {print $1": RX-DRP="$4" TX-DRP="$8}' || true)
        if [[ -n "$drops" ]]; then
            warn "Interface drops detected:"
            echo "$drops"
        else
            ok "No interface drops"
        fi
    fi

    # Kernel parameters
    echo ""
    info "Key Network Kernel Parameters:"
    for param in net.core.somaxconn net.ipv4.tcp_max_syn_backlog net.core.netdev_max_backlog; do
        local value
        value=$(sysctl -n "$param" 2>/dev/null || echo "not available")
        echo "$param = $value"
    done
}

# Process Analysis
analyze_processes() {
    section "Process Analysis"

    info "Top CPU Consumers:"
    ps aux --sort=-%cpu | head -10

    echo ""
    info "Top Memory Consumers:"
    ps aux --sort=-%mem | head -10

    echo ""
    info "Process Count:"
    local proc_count
    proc_count=$(ps aux | wc -l)
    echo "Total processes: $proc_count"

    if command -v pstree &>/dev/null; then
        echo ""
        info "Process Tree (summary):"
        pstree -a | head -20
    fi
}

# Kernel Parameters Audit
analyze_kernel_params() {
    section "Kernel Parameters Audit"

    info "Recommended iGaming Settings Check:"

    # Define expected values
    declare -A expected_params=(
        ["net.core.somaxconn"]="65535"
        ["net.ipv4.tcp_max_syn_backlog"]="65535"
        ["vm.swappiness"]="10"
        ["fs.file-max"]="2097152"
        ["net.ipv4.tcp_tw_reuse"]="1"
        ["net.ipv4.tcp_fin_timeout"]="5"
    )

    for param in "${!expected_params[@]}"; do
        local current
        local expected
        current=$(sysctl -n "$param" 2>/dev/null || echo "not available")
        expected="${expected_params[$param]}"

        if [[ "$current" == "$expected" ]]; then
            ok "$param = $current"
        elif [[ "$current" == "not available" ]]; then
            info "$param = not available"
        else
            warn "$param = $current (recommended: $expected)"
        fi
    done

    echo ""
    info "File Descriptor Limits:"
    echo "Current open files: $(cat /proc/sys/fs/file-nr 2>/dev/null | awk '{print $1}')"
    echo "Max open files: $(sysctl -n fs.file-max 2>/dev/null)"
    ulimit -n
}

# Summary and Recommendations
print_summary() {
    section "Summary and Recommendations"

    echo "Based on Brendan Gregg's USE methodology, this audit checked:"
    echo ""
    echo "  1. CPU: Utilization, run queue saturation, errors"
    echo "  2. Memory: Usage, swap activity, page faults, OOM events"
    echo "  3. Disk: I/O utilization, latency, queue depth, errors"
    echo "  4. Network: Interface stats, drops, connection states"
    echo ""
    echo "For deeper analysis, consider:"
    echo "  - perf top -g (CPU profiling)"
    echo "  - perf record -ag -- sleep 30 (flame graph data)"
    echo "  - BPF tools: biolatency, tcpretrans, execsnoop"
    echo ""
    echo "References:"
    echo "  - 'Systems Performance' (2nd Ed) - Brendan Gregg"
    echo "  - 'BPF Performance Tools' - Brendan Gregg"
    echo "  - brendangregg.com/USE-method"
}

# Help
show_help() {
    echo "iGaming Performance Audit Script"
    echo ""
    echo "Usage: $0 [--full|--quick|--help]"
    echo ""
    echo "Options:"
    echo "  --quick    Quick audit (default)"
    echo "  --full     Full audit with extended sampling"
    echo "  --help     Show this help message"
    echo ""
    echo "Based on Brendan Gregg's USE methodology for systems performance analysis."
}

# Main
main() {
    case "$AUDIT_MODE" in
        --help|-h)
            show_help
            exit 0
            ;;
        --full)
            print_header
            analyze_cpu
            analyze_memory
            analyze_disk
            analyze_network
            analyze_processes
            analyze_kernel_params
            print_summary
            ;;
        --quick|*)
            print_header
            analyze_cpu
            analyze_memory
            analyze_disk
            analyze_network
            analyze_kernel_params
            print_summary
            ;;
    esac
}

main
