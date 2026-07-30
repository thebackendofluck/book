#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 31, Performance Benchmarks and Metrics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# shellcheck disable=SC2012,SC2015,SC2034,SC2155
# =============================================================================
# Linux Kernel Tuning for Casino / Gambling Workloads
# =============================================================================
# Optimizes kernel parameters for high-throughput, low-latency casino platforms:
#   - Network stack (high WebSocket concurrency, TCP optimization)
#   - Memory management (huge pages, swappiness for game servers)
#   - Filesystem (async I/O for logging, journaling)
#   - Process scheduling (real-time priorities for RNG/game servers)
#   - Security hardening (required by GLI-33 / gambling regulations)
#
# IMPORTANT: Run on dedicated casino server nodes only.
# Test in staging before applying to production.
#
# Usage:
#   sudo ./kernel_tuning.sh --apply          # Apply all tunings
#   sudo ./kernel_tuning.sh --check          # Check current values
#   sudo ./kernel_tuning.sh --rollback       # Restore defaults
#   sudo ./kernel_tuning.sh --apply --profile high-concurrency
#
# Profiles:
#   default          - Balanced for general casino workloads
#   high-concurrency - Optimized for 100K+ concurrent WebSocket connections
#   game-server      - Low-latency for game round processing
#   database         - Optimized for PostgreSQL casino databases
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_DIR="/etc/sysctl.d/casino-backup"
SYSCTL_CONF="/etc/sysctl.d/99-casino-tuning.conf"
LIMITS_CONF="/etc/security/limits.d/99-casino.conf"
LOG_FILE="/var/log/casino-kernel-tuning.log"

# ---------------------------------------------------------------------------
# Argument Parsing
# ---------------------------------------------------------------------------
ACTION="check"
PROFILE="default"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --apply)    ACTION="apply"; shift;;
        --check)    ACTION="check"; shift;;
        --rollback) ACTION="rollback"; shift;;
        --profile)  PROFILE="$2"; shift 2;;
        --help)
            echo "Usage: sudo $0 [--apply|--check|--rollback] [--profile <profile>]"
            echo "Profiles: default, high-concurrency, game-server, database"
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1;;
    esac
done

# Must run as root for apply/rollback
if [[ "$ACTION" != "check" ]] && [[ $EUID -ne 0 ]]; then
    echo "ERROR: Must run as root for --apply/--rollback"
    exit 1
fi

log() {
    local msg="$(date -u +'%Y-%m-%d %H:%M:%S') [$1] $2"
    echo "$msg"
    [[ "$ACTION" == "apply" ]] && echo "$msg" >> "$LOG_FILE" 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# Profile-Specific Values
# ---------------------------------------------------------------------------
# Default values (overridden per profile)
FILE_MAX=2097152                  # 2M open files system-wide
NR_OPEN=1048576                   # 1M per process
SOMAXCONN=65535                   # Socket listen backlog
TCP_MAX_SYN_BACKLOG=65535
NETDEV_MAX_BACKLOG=65535
TCP_KEEPALIVE_TIME=60             # Seconds before first keepalive probe
TCP_KEEPALIVE_INTVL=10
TCP_KEEPALIVE_PROBES=6
TCP_FIN_TIMEOUT=5                 # Reclaim orphaned FIN_WAIT_2 sockets fast (see Chapter 28a)
TCP_TW_REUSE=1
IP_LOCAL_PORT_RANGE="1024 65535"
RMEM_MAX=16777216                 # 16MB receive buffer
WMEM_MAX=16777216                 # 16MB send buffer
TCP_RMEM="4096 87380 16777216"
TCP_WMEM="4096 65536 16777216"
SWAPPINESS=10
DIRTY_RATIO=20
DIRTY_BG_RATIO=5
HUGEPAGES=0
VM_OVERCOMMIT=0                   # Conservative for gambling (data integrity)
AIO_MAX=1048576

case "$PROFILE" in
    high-concurrency)
        # For WebSocket gateway nodes handling 100K+ concurrent connections
        FILE_MAX=4194304
        NR_OPEN=2097152
        SOMAXCONN=131072
        TCP_MAX_SYN_BACKLOG=131072
        NETDEV_MAX_BACKLOG=131072
        RMEM_MAX=33554432         # 32MB
        WMEM_MAX=33554432
        TCP_RMEM="4096 87380 33554432"
        TCP_WMEM="4096 65536 33554432"
        TCP_KEEPALIVE_TIME=30
        log "INFO" "Profile: high-concurrency (100K+ WebSocket connections)"
        ;;
    game-server)
        # For game server nodes (slots, table games, live dealer)
        SWAPPINESS=1              # Minimize swapping for latency
        HUGEPAGES=512             # 1GB huge pages for JVM game servers
        TCP_KEEPALIVE_TIME=30
        log "INFO" "Profile: game-server (low-latency game processing)"
        ;;
    database)
        # For PostgreSQL database nodes
        SWAPPINESS=5
        HUGEPAGES=2048            # 4GB huge pages for shared_buffers
        DIRTY_RATIO=40
        DIRTY_BG_RATIO=10
        VM_OVERCOMMIT=2           # Strict overcommit for database safety
        log "INFO" "Profile: database (PostgreSQL optimized)"
        ;;
    default)
        log "INFO" "Profile: default (balanced casino workload)"
        ;;
    *)
        echo "Unknown profile: $PROFILE"
        exit 1
        ;;
esac

# ---------------------------------------------------------------------------
# Check Current Values
# ---------------------------------------------------------------------------
check_current() {
    echo "============================================================"
    echo "  Current Kernel Parameters (Casino Tuning Check)"
    echo "  Profile: $PROFILE"
    echo "============================================================"
    echo ""

    printf "%-45s %-20s %-20s %-6s\n" "Parameter" "Current" "Recommended" "Status"
    echo "-------------------------------------------------------------------------------------------------------------"

    check_param() {
        local param="$1"
        local recommended="$2"
        local current
        current=$(sysctl -n "$param" 2>/dev/null | tr '\t' ' ' || echo "N/A")
        local status="OK"

        # Simple numeric comparison for single values
        if [[ "$current" =~ ^[0-9]+$ ]] && [[ "$recommended" =~ ^[0-9]+$ ]]; then
            if [[ "$current" -lt "$recommended" ]]; then
                status="LOW"
            fi
        elif [[ "$current" != "$recommended" ]]; then
            status="DIFF"
        fi

        local color=""
        local nc=""
        if [[ -t 1 ]]; then
            case "$status" in
                OK)   color="\033[0;32m"; nc="\033[0m";;
                LOW)  color="\033[0;31m"; nc="\033[0m";;
                DIFF) color="\033[1;33m"; nc="\033[0m";;
            esac
        fi

        printf "%-45s %-20s %-20s ${color}%-6s${nc}\n" "$param" "$current" "$recommended" "$status"
    }

    echo ""
    echo "--- File Descriptors ---"
    check_param "fs.file-max" "$FILE_MAX"
    check_param "fs.nr_open" "$NR_OPEN"
    check_param "fs.aio-max-nr" "$AIO_MAX"

    echo ""
    echo "--- Network Stack ---"
    check_param "net.core.somaxconn" "$SOMAXCONN"
    check_param "net.core.netdev_max_backlog" "$NETDEV_MAX_BACKLOG"
    check_param "net.core.rmem_max" "$RMEM_MAX"
    check_param "net.core.wmem_max" "$WMEM_MAX"
    check_param "net.ipv4.tcp_max_syn_backlog" "$TCP_MAX_SYN_BACKLOG"
    check_param "net.ipv4.tcp_keepalive_time" "$TCP_KEEPALIVE_TIME"
    check_param "net.ipv4.tcp_keepalive_intvl" "$TCP_KEEPALIVE_INTVL"
    check_param "net.ipv4.tcp_keepalive_probes" "$TCP_KEEPALIVE_PROBES"
    check_param "net.ipv4.tcp_fin_timeout" "$TCP_FIN_TIMEOUT"
    check_param "net.ipv4.tcp_tw_reuse" "$TCP_TW_REUSE"
    check_param "net.ipv4.ip_local_port_range" "$IP_LOCAL_PORT_RANGE"

    echo ""
    echo "--- Memory Management ---"
    check_param "vm.swappiness" "$SWAPPINESS"
    check_param "vm.dirty_ratio" "$DIRTY_RATIO"
    check_param "vm.dirty_background_ratio" "$DIRTY_BG_RATIO"
    check_param "vm.overcommit_memory" "$VM_OVERCOMMIT"
    if [[ "$HUGEPAGES" -gt 0 ]]; then
        check_param "vm.nr_hugepages" "$HUGEPAGES"
    fi

    echo ""
    echo "--- Security (Gambling Compliance) ---"
    check_param "kernel.randomize_va_space" "2"
    check_param "net.ipv4.conf.all.rp_filter" "1"
    check_param "net.ipv4.conf.default.rp_filter" "1"
    check_param "net.ipv4.conf.all.accept_redirects" "0"
    check_param "net.ipv4.conf.all.send_redirects" "0"
    check_param "net.ipv4.icmp_echo_ignore_broadcasts" "1"
    check_param "kernel.dmesg_restrict" "1"
    check_param "kernel.kptr_restrict" "2"

    echo ""
    echo "--- Current Ulimits ---"
    echo "  Open files (soft): $(ulimit -Sn 2>/dev/null || echo 'N/A')"
    echo "  Open files (hard): $(ulimit -Hn 2>/dev/null || echo 'N/A')"
    echo "  Max processes:     $(ulimit -u 2>/dev/null || echo 'N/A')"
    echo ""
}

# ---------------------------------------------------------------------------
# Apply Tuning
# ---------------------------------------------------------------------------
apply_tuning() {
    log "INFO" "Applying kernel tuning (profile: $PROFILE)..."

    # Backup current settings
    mkdir -p "$BACKUP_DIR"
    sysctl -a > "$BACKUP_DIR/sysctl_backup_$(date +%Y%m%d_%H%M%S).conf" 2>/dev/null

    # Generate sysctl.conf
    cat > "$SYSCTL_CONF" << EOF
# =============================================================================
# Casino Platform Kernel Tuning
# Profile: $PROFILE
# Generated: $(date -u +'%Y-%m-%d %H:%M:%S UTC')
# =============================================================================

# --- File Descriptors ---
fs.file-max = $FILE_MAX
fs.nr_open = $NR_OPEN
fs.aio-max-nr = $AIO_MAX

# --- Network: Core ---
net.core.somaxconn = $SOMAXCONN
net.core.netdev_max_backlog = $NETDEV_MAX_BACKLOG
net.core.rmem_max = $RMEM_MAX
net.core.wmem_max = $WMEM_MAX
net.core.rmem_default = 262144
net.core.wmem_default = 262144
net.core.optmem_max = 2048000

# --- Network: TCP ---
net.ipv4.tcp_max_syn_backlog = $TCP_MAX_SYN_BACKLOG
net.ipv4.tcp_rmem = $TCP_RMEM
net.ipv4.tcp_wmem = $TCP_WMEM
net.ipv4.tcp_keepalive_time = $TCP_KEEPALIVE_TIME
net.ipv4.tcp_keepalive_intvl = $TCP_KEEPALIVE_INTVL
net.ipv4.tcp_keepalive_probes = $TCP_KEEPALIVE_PROBES
net.ipv4.tcp_fin_timeout = $TCP_FIN_TIMEOUT
net.ipv4.tcp_tw_reuse = $TCP_TW_REUSE
net.ipv4.ip_local_port_range = $IP_LOCAL_PORT_RANGE
net.ipv4.tcp_max_tw_buckets = 2000000
net.ipv4.tcp_slow_start_after_idle = 0
net.ipv4.tcp_mtu_probing = 1
net.ipv4.tcp_fastopen = 3
net.ipv4.tcp_congestion_control = bbr
net.ipv4.tcp_notsent_lowat = 16384
net.ipv4.tcp_max_orphans = 262144
net.ipv4.tcp_syncookies = 1

# --- Memory ---
vm.swappiness = $SWAPPINESS
vm.dirty_ratio = $DIRTY_RATIO
vm.dirty_background_ratio = $DIRTY_BG_RATIO
vm.overcommit_memory = $VM_OVERCOMMIT
vm.max_map_count = 262144
vm.min_free_kbytes = 65536

# --- Huge Pages ---
$(if [[ "$HUGEPAGES" -gt 0 ]]; then echo "vm.nr_hugepages = $HUGEPAGES"; else echo "# vm.nr_hugepages = 0 (not configured for this profile)"; fi)

# --- Security (GLI-33 / Gambling Compliance) ---
# ASLR (Address Space Layout Randomization) - required
kernel.randomize_va_space = 2

# Reverse path filtering - anti-spoofing
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1

# Disable ICMP redirects (prevent MITM)
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.default.send_redirects = 0
net.ipv6.conf.all.accept_redirects = 0

# Ignore broadcast ICMP (smurf attack prevention)
net.ipv4.icmp_echo_ignore_broadcasts = 1
net.ipv4.icmp_ignore_bogus_error_responses = 1

# Restrict kernel log access
kernel.dmesg_restrict = 1
kernel.kptr_restrict = 2

# Disable source routing
net.ipv4.conf.all.accept_source_route = 0
net.ipv4.conf.default.accept_source_route = 0

# SYN flood protection
net.ipv4.tcp_syncookies = 1
net.ipv4.tcp_max_syn_backlog = $TCP_MAX_SYN_BACKLOG

# Disable IPv6 if not needed (reduces attack surface)
# net.ipv6.conf.all.disable_ipv6 = 1
# net.ipv6.conf.default.disable_ipv6 = 1
EOF

    # Apply sysctl
    sysctl -p "$SYSCTL_CONF"
    log "INFO" "sysctl settings applied from $SYSCTL_CONF"

    # Configure ulimits for casino service users
    cat > "$LIMITS_CONF" << EOF
# Casino platform process limits
# Generated: $(date -u +'%Y-%m-%d %H:%M:%S UTC')

# Casino application users
casino-api    soft    nofile    $NR_OPEN
casino-api    hard    nofile    $NR_OPEN
casino-api    soft    nproc     65535
casino-api    hard    nproc     131072
casino-api    soft    memlock   unlimited
casino-api    hard    memlock   unlimited
casino-api    soft    core      unlimited
casino-api    hard    core      unlimited

casino-game   soft    nofile    $NR_OPEN
casino-game   hard    nofile    $NR_OPEN
casino-game   soft    nproc     65535
casino-game   hard    nproc     131072
casino-game   soft    memlock   unlimited
casino-game   hard    memlock   unlimited
casino-game   soft    rtprio    99
casino-game   hard    rtprio    99

# Database user
postgres      soft    nofile    1048576
postgres      hard    nofile    1048576
postgres      soft    nproc     65535
postgres      hard    nproc     65535

# Redis user
redis         soft    nofile    1048576
redis         hard    nofile    1048576

# All users baseline
*             soft    nofile    65535
*             hard    nofile    1048576
EOF

    log "INFO" "ulimits configured in $LIMITS_CONF"

    # Enable BBR congestion control if available
    if modprobe tcp_bbr 2>/dev/null; then
        log "INFO" "TCP BBR congestion control enabled"
    else
        log "WARN" "TCP BBR not available — using default congestion control"
    fi

    # Disable transparent huge pages (can cause latency spikes in game servers)
    if [[ "$PROFILE" == "game-server" ]]; then
        echo never > /sys/kernel/mm/transparent_hugepage/enabled 2>/dev/null || true
        echo never > /sys/kernel/mm/transparent_hugepage/defrag 2>/dev/null || true
        log "INFO" "Transparent huge pages disabled (game-server profile)"
    fi

    log "INFO" "Kernel tuning applied successfully"
    echo ""
    echo "NOTE: Some settings may require a reboot. Run with --check to verify."
}

# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------
rollback_tuning() {
    log "INFO" "Rolling back kernel tuning..."

    if [[ -d "$BACKUP_DIR" ]]; then
        local latest_backup
        latest_backup=$(ls -t "$BACKUP_DIR"/sysctl_backup_*.conf 2>/dev/null | head -1)
        if [[ -n "$latest_backup" ]]; then
            sysctl -p "$latest_backup" 2>/dev/null
            log "INFO" "Restored settings from: $latest_backup"
        fi
    fi

    rm -f "$SYSCTL_CONF" "$LIMITS_CONF"
    sysctl --system 2>/dev/null
    log "INFO" "Rollback complete. Reboot recommended."
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
case "$ACTION" in
    check)    check_current;;
    apply)    apply_tuning; check_current;;
    rollback) rollback_tuning;;
esac
