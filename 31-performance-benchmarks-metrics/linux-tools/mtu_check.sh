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
# MTU Path Discovery and Validation Script
# For AWS VPC Jumbo Frames (MTU 9001) Configuration
#
# This script helps validate MTU support across network paths
# before enabling jumbo frames in production.
#
# Usage:
#   ./mtu_check.sh <target-ip>           # Quick check
#   ./mtu_check.sh <target-ip> --full    # Full analysis
#   ./mtu_check.sh --scan <cidr>         # Scan subnet
#   ./mtu_check.sh --configure           # Generate sysctl config
#   ./mtu_check.sh --help                # Show help
#
# Author: iGaming Performance Team
# Reference: Chapter 19 - Performance Benchmarks and Metrics
#

set -euo pipefail

# Colors
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly CYAN='\033[0;36m'
readonly NC='\033[0m'

# MTU sizes to test
readonly MTU_STANDARD=1500
readonly MTU_JUMBO=9001
readonly PING_PAYLOAD_JUMBO=8973  # 9001 - 20 (IP) - 8 (ICMP)
readonly PING_PAYLOAD_STANDARD=1472  # 1500 - 20 (IP) - 8 (ICMP)

# Functions
print_header() {
    echo -e "${BLUE}================================================${NC}"
    echo -e "${BLUE}  MTU Path Discovery and Validation Tool${NC}"
    echo -e "${BLUE}  For AWS VPC Jumbo Frames Configuration${NC}"
    echo -e "${BLUE}================================================${NC}"
    echo ""
}

ok() { echo -e "${GREEN}[✓]${NC} $1"; }
fail() { echo -e "${RED}[✗]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
info() { echo -e "${CYAN}[i]${NC} $1"; }

show_help() {
    cat << 'EOF'
MTU Path Discovery and Validation Script

USAGE:
    ./mtu_check.sh <target-ip>              Quick MTU check to target
    ./mtu_check.sh <target-ip> --full       Full analysis with throughput test
    ./mtu_check.sh --scan <cidr>            Scan subnet for jumbo frame support
    ./mtu_check.sh --local                  Check local interface configuration
    ./mtu_check.sh --configure [bandwidth]  Generate optimized sysctl.conf
    ./mtu_check.sh --help                   Show this help

BANDWIDTH OPTIONS for --configure:
    10g     10 Gbps network (default)
    25g     25 Gbps network
    50g     50 Gbps network
    100g    100 Gbps network
    200g    200 Gbps network

EXAMPLES:
    # Check if jumbo frames work to a specific host
    ./mtu_check.sh 10.0.1.50

    # Full analysis including throughput comparison
    ./mtu_check.sh 10.0.1.50 --full

    # Scan entire subnet for jumbo frame support
    ./mtu_check.sh --scan 10.0.1.0/24

    # Generate sysctl.conf for 100Gbps network
    ./mtu_check.sh --configure 100g

    # Check local interface settings
    ./mtu_check.sh --local

EOF
}

# Get current interface info
get_interface_info() {
    local iface="${1:-eth0}"

    echo -e "\n${CYAN}=== Interface: $iface ===${NC}\n"

    # Current MTU
    local current_mtu
    current_mtu=$(ip link show "$iface" 2>/dev/null | grep -oP 'mtu \K\d+' || echo "unknown")
    echo "Current MTU: $current_mtu"

    # Link speed
    if command -v ethtool &>/dev/null; then
        local speed
        speed=$(ethtool "$iface" 2>/dev/null | grep -i "speed:" | awk '{print $2}' || echo "unknown")
        echo "Link Speed: $speed"

        # Ring buffer
        echo ""
        echo "Ring Buffer Settings:"
        ethtool -g "$iface" 2>/dev/null | grep -E "^(RX|TX|Current)" || echo "  Unable to read"
    fi

    # Driver info
    if command -v ethtool &>/dev/null; then
        echo ""
        echo "Driver Info:"
        ethtool -i "$iface" 2>/dev/null | grep -E "^(driver|version)" || echo "  Unable to read"
    fi

    # IP addresses
    echo ""
    echo "IP Addresses:"
    ip addr show "$iface" 2>/dev/null | grep -E "inet " | awk '{print "  " $2}'
}

# Check local configuration
check_local() {
    print_header
    echo -e "${CYAN}=== Local Network Configuration ===${NC}\n"

    # List all interfaces
    echo "Network Interfaces:"
    echo "-------------------"
    for iface in $(ip -o link show | awk -F': ' '{print $2}' | grep -v lo); do
        local mtu speed state
        mtu=$(ip link show "$iface" 2>/dev/null | grep -oP 'mtu \K\d+' || echo "?")
        state=$(ip link show "$iface" 2>/dev/null | grep -oP 'state \K\w+' || echo "?")

        if command -v ethtool &>/dev/null; then
            speed=$(ethtool "$iface" 2>/dev/null | grep -i "speed:" | awk '{print $2}' || echo "?")
        else
            speed="?"
        fi

        printf "  %-15s MTU: %-5s Speed: %-10s State: %s\n" "$iface" "$mtu" "$speed" "$state"
    done

    # Detailed info for primary interface
    local primary_iface
    primary_iface=$(ip route get 8.8.8.8 2>/dev/null | grep -oP 'dev \K\S+' || echo "eth0")
    get_interface_info "$primary_iface"

    # Current sysctl network settings
    echo -e "\n${CYAN}=== Key Kernel Parameters ===${NC}\n"

    local params=(
        "net.core.rmem_max"
        "net.core.wmem_max"
        "net.core.rmem_default"
        "net.core.wmem_default"
        "net.core.netdev_max_backlog"
        "net.core.somaxconn"
        "net.ipv4.tcp_rmem"
        "net.ipv4.tcp_wmem"
        "net.ipv4.tcp_max_syn_backlog"
        "net.ipv4.ip_local_port_range"
        "net.ipv4.tcp_tw_reuse"
        "net.ipv4.tcp_fin_timeout"
        "net.core.netdev_budget"
        "net.core.netdev_budget_usecs"
    )

    for param in "${params[@]}"; do
        local value
        value=$(sysctl -n "$param" 2>/dev/null || echo "not available")
        printf "  %-40s = %s\n" "$param" "$value"
    done

    # File descriptor limits
    echo -e "\n${CYAN}=== File Descriptor Limits ===${NC}\n"
    echo "  System-wide:"
    echo "    fs.file-max = $(sysctl -n fs.file-max 2>/dev/null || echo 'unknown')"
    echo "    fs.nr_open  = $(sysctl -n fs.nr_open 2>/dev/null || echo 'unknown')"
    echo "    Current open files: $(cat /proc/sys/fs/file-nr 2>/dev/null | awk '{print $1}' || echo 'unknown')"
    echo ""
    echo "  Per-process (current shell):"
    echo "    Soft limit: $(ulimit -Sn)"
    echo "    Hard limit: $(ulimit -Hn)"
}

# Test MTU to target
test_mtu() {
    local target="$1"
    # $2 is mtu_size (for documentation, not used in ping)
    local payload_size="$3"
    local timeout="${4:-2}"

    # Use ping with Don't Fragment flag (-M do means "do not fragment")
    # shellcheck disable=SC1010
    if ping -M "do" -s "$payload_size" -c 1 -W "$timeout" "$target" &>/dev/null; then
        return 0
    else
        return 1
    fi
}

# Quick MTU check
quick_check() {
    local target="$1"

    print_header
    echo -e "${CYAN}Target: $target${NC}\n"

    # Basic connectivity
    info "Testing basic connectivity..."
    if ! ping -c 1 -W 2 "$target" &>/dev/null; then
        fail "Cannot reach $target - check if host is up and accessible"
        return 1
    fi
    ok "Host is reachable"

    # Standard MTU test
    info "Testing standard MTU (1500)..."
    if test_mtu "$target" "$MTU_STANDARD" "$PING_PAYLOAD_STANDARD"; then
        ok "Standard MTU (1500) works"
    else
        fail "Standard MTU (1500) failed - network issue"
        return 1
    fi

    # Jumbo MTU test
    info "Testing jumbo frames MTU (9001)..."
    if test_mtu "$target" "$MTU_JUMBO" "$PING_PAYLOAD_JUMBO"; then
        ok "Jumbo frames (MTU 9001) SUPPORTED"
        echo ""
        echo -e "${GREEN}Result: You can safely enable jumbo frames to this target${NC}"
        echo ""
        echo "To enable jumbo frames:"
        echo "  sudo ip link set eth0 mtu 9001"
        return 0
    else
        warn "Jumbo frames (MTU 9001) NOT supported on this path"
        echo ""

        # Find actual path MTU
        info "Discovering actual path MTU..."
        discover_path_mtu "$target"
        return 1
    fi
}

# Discover path MTU using binary search
discover_path_mtu() {
    local target="$1"
    local low=1400
    local high=9001
    local best_mtu=$low

    while [[ $((high - low)) -gt 10 ]]; do
        local mid=$(( (low + high) / 2 ))
        local payload=$((mid - 28))  # Subtract IP + ICMP headers

        # shellcheck disable=SC1010
        if ping -M "do" -s "$payload" -c 1 -W 1 "$target" &>/dev/null; then
            best_mtu=$mid
            low=$mid
        else
            high=$mid
        fi
    done

    echo "  Discovered path MTU: approximately $best_mtu bytes"

    if [[ $best_mtu -lt 1500 ]]; then
        warn "Path MTU is below standard (1500) - possible tunnel or VPN"
    elif [[ $best_mtu -ge 8900 ]]; then
        ok "Path supports near-jumbo MTU ($best_mtu)"
    fi
}

# Full analysis with throughput test
full_analysis() {
    local target="$1"

    quick_check "$target"
    local mtu_result=$?

    echo -e "\n${CYAN}=== Extended Analysis ===${NC}\n"

    # Tracepath for MTU discovery
    if command -v tracepath &>/dev/null; then
        info "Running tracepath for hop-by-hop MTU analysis..."
        tracepath -n "$target" 2>/dev/null | head -20 || warn "tracepath failed"
    fi

    # Throughput test if iperf3 available
    if command -v iperf3 &>/dev/null; then
        echo ""
        info "Checking for iperf3 server on target..."
        if timeout 2 bash -c "echo >/dev/tcp/$target/5201" 2>/dev/null; then
            echo ""
            info "Running throughput test (10 seconds)..."
            iperf3 -c "$target" -t 10 -P 4 2>/dev/null || warn "iperf3 test failed"
        else
            warn "No iperf3 server running on $target:5201"
            echo "  To test throughput, run on target: iperf3 -s"
        fi
    else
        warn "iperf3 not installed - skipping throughput test"
        echo "  Install with: sudo yum install iperf3  (or apt-get)"
    fi

    # Network statistics
    echo ""
    info "Current network statistics for route to $target:"
    ip route get "$target" 2>/dev/null || true

    return $mtu_result
}

# Scan subnet for jumbo frame support
scan_subnet() {
    local cidr="$1"

    print_header
    echo -e "${CYAN}Scanning $cidr for jumbo frame support...${NC}\n"

    # Check if nmap or fping available for discovery
    local hosts=()

    if command -v nmap &>/dev/null; then
        info "Using nmap for host discovery..."
        mapfile -t hosts < <(nmap -sn "$cidr" 2>/dev/null | grep -oP '\d+\.\d+\.\d+\.\d+' || true)
    elif command -v fping &>/dev/null; then
        info "Using fping for host discovery..."
        mapfile -t hosts < <(fping -a -g "$cidr" 2>/dev/null || true)
    else
        warn "Neither nmap nor fping available - using ping sweep (slower)"
        # Extract network and do simple ping sweep
        local base
        base=$(echo "$cidr" | cut -d'/' -f1 | sed 's/\.[0-9]*$//')
        for i in {1..254}; do
            if ping -c 1 -W 1 "${base}.$i" &>/dev/null; then
                hosts+=("${base}.$i")
            fi
        done
    fi

    if [[ ${#hosts[@]} -eq 0 ]]; then
        warn "No hosts found in $cidr"
        return 1
    fi

    echo ""
    echo "Found ${#hosts[@]} hosts. Testing jumbo frame support..."
    echo ""
    printf "%-20s %-15s %-15s\n" "Host" "Standard MTU" "Jumbo Frames"
    printf "%-20s %-15s %-15s\n" "----" "------------" "------------"

    local jumbo_supported=0
    local jumbo_failed=0

    for host in "${hosts[@]}"; do
        local std_result jumbo_result

        if test_mtu "$host" "$MTU_STANDARD" "$PING_PAYLOAD_STANDARD" 1; then
            std_result="${GREEN}OK${NC}"
        else
            std_result="${RED}FAIL${NC}"
        fi

        if test_mtu "$host" "$MTU_JUMBO" "$PING_PAYLOAD_JUMBO" 1; then
            jumbo_result="${GREEN}SUPPORTED${NC}"
            ((jumbo_supported++))
        else
            jumbo_result="${YELLOW}NOT SUPPORTED${NC}"
            ((jumbo_failed++))
        fi

        printf "%-20s %-24b %-24b\n" "$host" "$std_result" "$jumbo_result"
    done

    echo ""
    echo "Summary:"
    echo "  Jumbo frames supported: $jumbo_supported hosts"
    echo "  Jumbo frames not supported: $jumbo_failed hosts"

    if [[ $jumbo_failed -gt 0 ]]; then
        warn "Some hosts do not support jumbo frames"
        echo "  These may be behind NAT, VPN, or using Internet Gateway"
    fi
}

# Generate optimized sysctl.conf
generate_sysctl_config() {
    local bandwidth="${1:-10g}"

    print_header
    echo -e "${CYAN}Generating optimized network configuration for $bandwidth network${NC}\n"

    # Set parameters based on bandwidth
    local rmem_max wmem_max rmem_default wmem_default
    local tcp_rmem tcp_wmem
    local netdev_max_backlog somaxconn
    local netdev_budget netdev_budget_usecs
    local file_max nr_open
    local nf_conntrack_max
    local recommended_ulimit

    case "$bandwidth" in
        10g)
            rmem_max=134217728        # 128MB
            wmem_max=134217728        # 128MB
            rmem_default=1048576      # 1MB
            wmem_default=1048576      # 1MB
            tcp_rmem="4096 1048576 134217728"
            tcp_wmem="4096 1048576 134217728"
            netdev_max_backlog=50000
            somaxconn=65535
            netdev_budget=600
            netdev_budget_usecs=8000
            file_max=2097152
            nr_open=2097152
            nf_conntrack_max=1000000
            recommended_ulimit=1048576
            ;;
        25g)
            rmem_max=268435456        # 256MB
            wmem_max=268435456        # 256MB
            rmem_default=2097152      # 2MB
            wmem_default=2097152      # 2MB
            tcp_rmem="4096 2097152 268435456"
            tcp_wmem="4096 2097152 268435456"
            netdev_max_backlog=100000
            somaxconn=65535
            netdev_budget=1200
            netdev_budget_usecs=6000
            file_max=4194304
            nr_open=4194304
            nf_conntrack_max=2000000
            recommended_ulimit=2097152
            ;;
        50g)
            rmem_max=536870912        # 512MB
            wmem_max=536870912        # 512MB
            rmem_default=4194304      # 4MB
            wmem_default=4194304      # 4MB
            tcp_rmem="4096 4194304 536870912"
            tcp_wmem="4096 4194304 536870912"
            netdev_max_backlog=200000
            somaxconn=65535
            netdev_budget=2400
            netdev_budget_usecs=4000
            file_max=8388608
            nr_open=8388608
            nf_conntrack_max=4000000
            recommended_ulimit=4194304
            ;;
        100g)
            rmem_max=1073741824       # 1GB
            wmem_max=1073741824       # 1GB
            rmem_default=8388608      # 8MB
            wmem_default=8388608      # 8MB
            tcp_rmem="4096 8388608 1073741824"
            tcp_wmem="4096 8388608 1073741824"
            netdev_max_backlog=300000
            somaxconn=65535
            netdev_budget=4800
            netdev_budget_usecs=2000
            file_max=16777216
            nr_open=16777216
            nf_conntrack_max=8000000
            recommended_ulimit=8388608
            ;;
        200g)
            rmem_max=2147483647       # 2GB (max)
            wmem_max=2147483647       # 2GB (max)
            rmem_default=16777216     # 16MB
            wmem_default=16777216     # 16MB
            tcp_rmem="4096 16777216 2147483647"
            tcp_wmem="4096 16777216 2147483647"
            netdev_max_backlog=500000
            somaxconn=65535
            netdev_budget=9600
            netdev_budget_usecs=1000
            file_max=33554432
            nr_open=33554432
            nf_conntrack_max=16000000
            recommended_ulimit=16777216
            ;;
        *)
            echo "Unknown bandwidth: $bandwidth"
            echo "Use: 10g, 25g, 50g, 100g, or 200g"
            return 1
            ;;
    esac

    # Generate sysctl.conf
    echo "# =============================================="
    echo "# /etc/sysctl.d/99-network-performance.conf"
    echo "# Optimized for $bandwidth network"
    echo "# Generated: $(date)"
    echo "# =============================================="
    echo ""
    cat << EOF
# ==============================================
# NETWORK BUFFER SIZES
# ==============================================
# Maximum receive/send buffer size (bytes)
net.core.rmem_max = $rmem_max
net.core.wmem_max = $wmem_max

# Default receive/send buffer size (bytes)
net.core.rmem_default = $rmem_default
net.core.wmem_default = $wmem_default

# Optional memory buffer
net.core.optmem_max = 134217728

# ==============================================
# TCP BUFFER SIZES
# ==============================================
# TCP receive buffer: min, default, max (bytes)
net.ipv4.tcp_rmem = $tcp_rmem

# TCP send buffer: min, default, max (bytes)
net.ipv4.tcp_wmem = $tcp_wmem

# TCP memory limits (pages): min, pressure, max
net.ipv4.tcp_mem = 786432 1048576 26777216

# ==============================================
# NETWORK QUEUE SETTINGS
# ==============================================
# Maximum backlog for incoming packets
net.core.netdev_max_backlog = $netdev_max_backlog

# Maximum listen queue size
net.core.somaxconn = $somaxconn

# Maximum SYN backlog
net.ipv4.tcp_max_syn_backlog = 65535

# NAPI budget (packets per softirq)
net.core.netdev_budget = $netdev_budget
net.core.netdev_budget_usecs = $netdev_budget_usecs

# ==============================================
# TCP CONNECTION SETTINGS
# ==============================================
# Local port range for outgoing connections
net.ipv4.ip_local_port_range = 1024 65535

# Reuse TIME_WAIT sockets for new outbound connections
net.ipv4.tcp_tw_reuse = 1

# Bound orphaned FIN_WAIT_2 sockets (TIME_WAIT is fixed at 60s in the kernel)
net.ipv4.tcp_fin_timeout = 5

# Maximum TIME_WAIT sockets
net.ipv4.tcp_max_tw_buckets = 2000000

# Keepalive settings
net.ipv4.tcp_keepalive_time = 300
net.ipv4.tcp_keepalive_intvl = 15
net.ipv4.tcp_keepalive_probes = 5

# ==============================================
# TCP PERFORMANCE OPTIMIZATIONS
# ==============================================
# Disable slow start after idle
net.ipv4.tcp_slow_start_after_idle = 0

# Enable TCP timestamps
net.ipv4.tcp_timestamps = 1

# Enable selective acknowledgments
net.ipv4.tcp_sack = 1

# Enable window scaling
net.ipv4.tcp_window_scaling = 1

# Enable MTU probing
net.ipv4.tcp_mtu_probing = 1

# Increase max orphan sockets
net.ipv4.tcp_max_orphans = 262144

# Enable TCP Fast Open
net.ipv4.tcp_fastopen = 3

# ==============================================
# CONNECTION TRACKING (if using iptables/nftables)
# ==============================================
net.netfilter.nf_conntrack_max = $nf_conntrack_max
net.netfilter.nf_conntrack_tcp_timeout_established = 600
net.netfilter.nf_conntrack_tcp_timeout_time_wait = 30
net.netfilter.nf_conntrack_tcp_timeout_close_wait = 15
net.netfilter.nf_conntrack_tcp_timeout_fin_wait = 30

# ==============================================
# FILE DESCRIPTOR LIMITS
# ==============================================
fs.file-max = $file_max
fs.nr_open = $nr_open
fs.aio-max-nr = 1048576

# ==============================================
# MEMORY SETTINGS
# ==============================================
# Reduce swappiness for network servers
vm.swappiness = 10

# Memory overcommit
vm.overcommit_memory = 1

# Max memory map areas
vm.max_map_count = 262144
EOF

    echo ""
    echo "# =============================================="
    echo "# /etc/security/limits.d/99-network.conf"
    echo "# =============================================="
    echo ""
    cat << EOF
# File descriptor limits for high-connection servers
# Optimized for $bandwidth network

*               soft    nofile          $recommended_ulimit
*               hard    nofile          $recommended_ulimit
root            soft    nofile          $recommended_ulimit
root            hard    nofile          $recommended_ulimit

# Process limits
*               soft    nproc           65535
*               hard    nproc           65535

# Memory lock (for applications using huge pages)
*               soft    memlock         unlimited
*               hard    memlock         unlimited
EOF

    echo ""
    echo -e "${CYAN}=== Installation Instructions ===${NC}"
    echo ""
    echo "1. Save sysctl configuration:"
    echo "   sudo tee /etc/sysctl.d/99-network-performance.conf << 'SYSCTL_EOF'"
    echo "   ... (copy content above) ..."
    echo "   SYSCTL_EOF"
    echo ""
    echo "2. Save limits configuration:"
    echo "   sudo tee /etc/security/limits.d/99-network.conf << 'LIMITS_EOF'"
    echo "   ... (copy content above) ..."
    echo "   LIMITS_EOF"
    echo ""
    echo "3. Apply sysctl changes:"
    echo "   sudo sysctl -p /etc/sysctl.d/99-network-performance.conf"
    echo ""
    echo "4. For limits.conf to take effect:"
    echo "   - Log out and log back in, or"
    echo "   - Reboot the system"
    echo ""
    echo "5. Verify changes:"
    echo "   sysctl net.core.rmem_max"
    echo "   ulimit -n"
}

# Main
main() {
    case "${1:-}" in
        --help|-h)
            show_help
            ;;
        --local|-l)
            check_local
            ;;
        --configure|-c)
            generate_sysctl_config "${2:-10g}"
            ;;
        --scan|-s)
            if [[ -z "${2:-}" ]]; then
                echo "Error: --scan requires a CIDR (e.g., 10.0.1.0/24)"
                exit 1
            fi
            scan_subnet "$2"
            ;;
        --full|-f)
            if [[ -z "${2:-}" ]]; then
                echo "Error: --full requires a target IP"
                exit 1
            fi
            full_analysis "$2"
            ;;
        "")
            show_help
            ;;
        *)
            if [[ "${2:-}" == "--full" || "${2:-}" == "-f" ]]; then
                full_analysis "$1"
            else
                quick_check "$1"
            fi
            ;;
    esac
}

main "$@"
