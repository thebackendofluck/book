#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# iptables-geoblock.sh
# =============================================================================
# On-premises (or bare-metal) geo-blocking using iptables with IP sets
# sourced from ipdeny.com country zone files.
#
# This is the network-layer block — it drops TCP connections from blocked
# countries before any application code runs. Useful for:
#   - Bare-metal or co-located servers without CDN
#   - Reducing DDoS attack surface from non-licensed jurisdictions
#   - Defense-in-depth behind CDN (CDN can be misconfigured; this doesn't)
#   - Compliance requirements in jurisdictions where "all reasonable steps"
#     to prevent access must be documented
#
# Architecture:
#   Internet → Router → iptables (THIS SCRIPT) → nginx → Application
#
# Approach:
#   Uses ipset (hash:net) for O(1) lookup performance.
#   Each country's CIDR ranges are loaded into a named ipset.
#   A single iptables rule matches the ipset and DROPs the packet.
#   This is dramatically faster than individual iptables rules per CIDR.
#
# Performance:
#   ipset with 200,000 CIDR entries: ~0.01ms lookup
#   200,000 individual iptables rules: ~2-5ms lookup (sequential scan)
#
# Usage:
#   # Install dependencies (Debian/Ubuntu):
#   apt-get install iptables ipset curl
#
#   # Apply geo-block rules:
#   sudo ./iptables-geoblock.sh --apply
#
#   # Check current blocked countries:
#   sudo ./iptables-geoblock.sh --list
#
#   # Remove all geo-block rules:
#   sudo ./iptables-geoblock.sh --flush
#
#   # Test if an IP would be blocked:
#   sudo ./iptables-geoblock.sh --test-ip 5.62.56.160
#
# Persistence:
#   Rules are flushed on reboot. For persistence:
#     - Debian/Ubuntu: apt-get install iptables-persistent
#       Then: netfilter-persistent save
#     - systemd: create a oneshot service that runs this script on boot
#     - Or use nftables (see comments at bottom of this file)
#
# IMPORTANT: Run as root. Test in a staging environment first.
# A misconfigured iptables rule can lock you out of the server.
# Always have out-of-band console access before modifying firewall rules.
# =============================================================================

set -euo pipefail

# ---- Colours ----
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# =============================================================================
# Configuration
# =============================================================================

# Country codes to block (ISO 3166-1 alpha-2, lowercase for ipdeny.com URLs)
# Update this list to match your license portfolio.
BLOCKED_COUNTRIES=(
    ae    # United Arab Emirates — Federal Law No. 6 of 2018
    sa    # Saudi Arabia — Royal Decree M/33 + Islamic law
    qa    # Qatar — Law No. 14 of 2014
    kw    # Kuwait — Law No. 31 of 1970
    bh    # Bahrain — Decree-Law No. 15 of 1976
    om    # Oman — Penal Code Article 263
    ye    # Yemen — Islamic Penal Code provisions
    ly    # Libya — Penal Code Chapter 4
    sd    # Sudan — Gambling Act 1974
    cn    # China — Criminal Law Article 303
    kp    # North Korea — complete prohibition
    kh    # Cambodia — Sub-Decree No. 176 of 2019
    dz    # Algeria — Ordinance No. 75-58
    ma    # Morocco — Dahir gambling code
    pk    # Pakistan — Prevention of Gambling Act 1977
    bd    # Bangladesh — Public Gambling Act 1867
    af    # Afghanistan — Penal Code Article 277
    iq    # Iraq — Penal Code No. 111 of 1969
    ir    # Iran — Islamic Penal Code Chapter 20
)

# Ports to apply geo-blocking to (typically 80 and 443)
BLOCKED_PORTS=(80 443)

# Source for country CIDR data
IPDENY_URL="https://www.ipdeny.com/ipblocks/data/aggregated"

# Local cache directory for CIDR zone files
ZONE_CACHE_DIR="/var/cache/geoip-zones"

# ipset name prefix
IPSET_PREFIX="geo_blocked"

# Combined ipset name (all blocked countries merged)
COMBINED_IPSET="${IPSET_PREFIX}_all"

# Log file
LOG_FILE="/var/log/igaming/iptables-geoblock.log"

# iptables chain for geo-blocking rules
GEOBLOCK_CHAIN="GEO_BLOCK"

# Block target: DROP (silently discard) or REJECT (sends TCP RST)
# DROP is recommended — it gives no information to the attacker.
# REJECT is more user-friendly but reveals the firewall's presence.
BLOCK_TARGET="DROP"

# =============================================================================
# Helper functions
# =============================================================================

log_info()  { echo -e "${BLUE}[INFO]${NC}  $(date -u +"%Y-%m-%dT%H:%M:%SZ") $*" | tee -a "$LOG_FILE"; }
log_ok()    { echo -e "${GREEN}[OK]${NC}    $(date -u +"%Y-%m-%dT%H:%M:%SZ") $*" | tee -a "$LOG_FILE"; }
log_fail()  { echo -e "${RED}[ERROR]${NC} $(date -u +"%Y-%m-%dT%H:%M:%SZ") $*" | tee -a "$LOG_FILE"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $(date -u +"%Y-%m-%dT%H:%M:%SZ") $*" | tee -a "$LOG_FILE"; }

require_root() {
    if [[ "$(id -u)" -ne 0 ]]; then
        log_fail "This script must be run as root."
        exit 1
    fi
}

check_deps() {
    local missing=()
    for cmd in iptables ipset curl; do
        command -v "$cmd" &>/dev/null || missing+=("$cmd")
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        log_fail "Missing required tools: ${missing[*]}"
        echo "Install with: apt-get install iptables ipset curl"
        exit 1
    fi
}

# =============================================================================
# Download country CIDR zone files from ipdeny.com
# ipdeny.com provides free, regularly updated IP block lists.
# Alternative: db-ip.com, MaxMind GeoLite2 (requires free account)
# =============================================================================
download_zone_files() {
    log_info "Downloading country zone files..."
    mkdir -p "$ZONE_CACHE_DIR"

    local total="${#BLOCKED_COUNTRIES[@]}"
    local count=0

    for country in "${BLOCKED_COUNTRIES[@]}"; do
        count=$((count + 1))
        local zone_url="${IPDENY_URL}/${country}-aggregated.zone"
        local zone_file="${ZONE_CACHE_DIR}/${country}.zone"

        # Skip download if file was updated in the last 24 hours
        if [[ -f "$zone_file" ]] && \
           [[ $(find "$zone_file" -mtime -1 -print 2>/dev/null | wc -l) -gt 0 ]]; then
            log_info "[${count}/${total}] ${country^^}: cached (less than 24h old)"
            continue
        fi

        if curl --silent --fail --max-time 30 --retry 3 \
            -o "$zone_file" "$zone_url" 2>/dev/null; then
            local cidr_count
            cidr_count=$(wc -l < "$zone_file")
            log_ok "[${count}/${total}] ${country^^}: downloaded ${cidr_count} CIDR ranges"
        else
            log_warn "[${count}/${total}] ${country^^}: download failed — using cached file if available"
        fi
    done
}

# =============================================================================
# Create ipset and load CIDR ranges
# =============================================================================
create_ipsets() {
    log_info "Creating ipset: ${COMBINED_IPSET}"

    # Destroy existing set (atomic swap: create new, swap, destroy old)
    local temp_ipset="${COMBINED_IPSET}_tmp"

    ipset create -exist "$temp_ipset" hash:net \
        family inet \
        hashsize 65536 \
        maxelem 1000000 \
        comment

    local total_cidrs=0

    for country in "${BLOCKED_COUNTRIES[@]}"; do
        local zone_file="${ZONE_CACHE_DIR}/${country}.zone"

        if [[ ! -f "$zone_file" ]]; then
            log_warn "Zone file not found: ${zone_file} — skipping ${country^^}"
            continue
        fi

        local count=0
        while IFS= read -r cidr; do
            # Skip blank lines and comments
            [[ -z "$cidr" || "$cidr" == \#* ]] && continue

            # Add to ipset with country comment (for auditability)
            ipset add -exist "$temp_ipset" "$cidr" comment "${country^^}"
            count=$((count + 1))
        done < "$zone_file"

        total_cidrs=$((total_cidrs + count))
        log_info "  ${country^^}: loaded ${count} CIDRs"
    done

    # Atomic swap: replace production set with new set
    if ipset list "$COMBINED_IPSET" &>/dev/null 2>&1; then
        ipset swap "$temp_ipset" "$COMBINED_IPSET"
        ipset destroy "$temp_ipset"
    else
        ipset rename "$temp_ipset" "$COMBINED_IPSET"
    fi

    log_ok "ipset ${COMBINED_IPSET} created with ${total_cidrs} total CIDR ranges"
}

# =============================================================================
# Create iptables chain and rules
# =============================================================================
create_iptables_rules() {
    log_info "Setting up iptables geo-block chain: ${GEOBLOCK_CHAIN}"

    # Create the geo-block chain (idempotent)
    iptables -N "$GEOBLOCK_CHAIN" 2>/dev/null || \
        iptables -F "$GEOBLOCK_CHAIN"  # Flush if chain already exists

    # Match ipset → LOG → DROP
    # Log with a prefix so these can be filtered in syslog/journald
    iptables -A "$GEOBLOCK_CHAIN" \
        -m set --match-set "$COMBINED_IPSET" src \
        -j LOG \
        --log-prefix "GEO_BLOCK: " \
        --log-level 6

    iptables -A "$GEOBLOCK_CHAIN" \
        -m set --match-set "$COMBINED_IPSET" src \
        -j "$BLOCK_TARGET"

    # Jump from INPUT chain to geo-block chain for the blocked ports
    for port in "${BLOCKED_PORTS[@]}"; do
        # Remove existing rule first (avoid duplicates)
        iptables -D INPUT -p tcp --dport "$port" -j "$GEOBLOCK_CHAIN" 2>/dev/null || true

        # Add the rule
        iptables -I INPUT 1 -p tcp --dport "$port" -j "$GEOBLOCK_CHAIN"
        log_ok "iptables: INPUT → ${GEOBLOCK_CHAIN} for tcp port ${port}"
    done
}

# =============================================================================
# Apply all geo-blocking rules
# =============================================================================
apply_rules() {
    require_root
    check_deps

    mkdir -p "$(dirname "$LOG_FILE")"
    log_info "Starting geo-block rule application"
    log_info "Blocked countries: ${BLOCKED_COUNTRIES[*]^^}"
    log_info "Blocked ports: ${BLOCKED_PORTS[*]}"

    download_zone_files
    create_ipsets
    create_iptables_rules

    log_ok "Geo-blocking rules applied successfully"
    log_info "To persist rules across reboots: netfilter-persistent save"
}

# =============================================================================
# Flush (remove) all geo-blocking rules
# =============================================================================
flush_rules() {
    require_root
    log_info "Removing geo-block rules..."

    # Remove INPUT chain jumps
    for port in "${BLOCKED_PORTS[@]}"; do
        iptables -D INPUT -p tcp --dport "$port" -j "$GEOBLOCK_CHAIN" 2>/dev/null || true
    done

    # Flush and delete the geo-block chain
    iptables -F "$GEOBLOCK_CHAIN" 2>/dev/null || true
    iptables -X "$GEOBLOCK_CHAIN" 2>/dev/null || true

    # Destroy ipset
    ipset destroy "$COMBINED_IPSET" 2>/dev/null || true

    log_ok "All geo-block rules removed"
}

# =============================================================================
# List current blocked countries and stats
# =============================================================================
list_rules() {
    require_root
    echo ""
    echo "=== Geo-Block Status ==="
    echo ""

    if iptables -L "$GEOBLOCK_CHAIN" -n &>/dev/null 2>&1; then
        echo "iptables chain '${GEOBLOCK_CHAIN}':"
        iptables -L "$GEOBLOCK_CHAIN" -n -v
    else
        echo "Chain '${GEOBLOCK_CHAIN}' does not exist (rules not applied)"
    fi

    echo ""
    if ipset list "$COMBINED_IPSET" &>/dev/null 2>&1; then
        local entry_count
        entry_count=$(ipset list "$COMBINED_IPSET" | grep -c "^[0-9]" || true)
        echo "ipset '${COMBINED_IPSET}': ${entry_count} CIDR entries"

        echo ""
        echo "Blocked countries:"
        ipset list "$COMBINED_IPSET" | grep "comment:" | \
            awk '{print $NF}' | sort | uniq -c | sort -rn | \
            awk '{printf "  %-5s %s CIDRs\n", $2, $1}'
    else
        echo "ipset '${COMBINED_IPSET}' does not exist (rules not applied)"
    fi
    echo ""
}

# =============================================================================
# Test whether a specific IP would be blocked
# =============================================================================
test_ip() {
    local ip="$1"
    require_root

    if ipset test "$COMBINED_IPSET" "$ip" &>/dev/null 2>&1; then
        echo -e "${RED}BLOCKED:${NC} IP ${ip} is in the geo-block list"
        # Show which country
        local entry
        entry=$(ipset list "$COMBINED_IPSET" | grep "^${ip}" | head -1 || true)
        if [[ -n "$entry" ]]; then
            echo "  Entry: ${entry}"
        fi
        return 1
    else
        echo -e "${GREEN}ALLOWED:${NC} IP ${ip} is NOT in the geo-block list"
        return 0
    fi
}

# =============================================================================
# Print nftables equivalent (modern alternative to iptables)
# nftables is the successor to iptables on Linux 5.2+.
# For new deployments, prefer nftables.
# =============================================================================
print_nftables_equivalent() {
    cat <<'NFTABLES'
# nftables equivalent configuration
# Place in /etc/nftables.conf

table inet geo_filter {
    # Define the geo-block set (populate with country CIDR ranges)
    set geo_blocked_countries {
        type ipv4_addr
        flags interval
        # Add CIDRs here or use 'nft add element' from a script
        # elements = { 5.62.56.0/24, ... }
    }

    chain input {
        type filter hook input priority 0; policy accept;

        # Log and drop geo-blocked IPs on ports 80 and 443
        tcp dport { 80, 443 } ip saddr @geo_blocked_countries \
            log prefix "GEO_BLOCK: " level info \
            drop
    }
}

# Load country CIDRs:
# while IFS= read -r cidr; do
#     nft add element inet geo_filter geo_blocked_countries "{ $cidr }"
# done < /var/cache/geoip-zones/cn.zone
NFTABLES
}

# =============================================================================
# Argument dispatch
# =============================================================================
usage() {
    cat <<EOF
Usage: $(basename "$0") [COMMAND]

Commands:
  --apply          Download zone files and apply geo-block rules
  --flush          Remove all geo-block rules
  --list           Show current blocked countries and stats
  --test-ip IP     Test whether an IP address would be blocked
  --nftables       Print equivalent nftables configuration
  --help           Show this message

EOF
}

case "${1:-}" in
    --apply)     apply_rules                     ;;
    --flush)     flush_rules                     ;;
    --list)      list_rules                      ;;
    --test-ip)   test_ip "${2:?'IP address required'}" ;;
    --nftables)  print_nftables_equivalent       ;;
    --help|-h)   usage                           ;;
    *)           usage; exit 2                   ;;
esac
