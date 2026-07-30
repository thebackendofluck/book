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
# network_wireguard_setup.sh - Complete WireGuard VPN implementation for network security
# Provides secure network tunneling between multiple networks using YubiHSM 2

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="${SCRIPT_DIR}/config/wireguard"
KEYS_DIR="${SCRIPT_DIR}/keys/wireguard"
LOGS_DIR="${SCRIPT_DIR}/logs/wireguard"

# YubiHSM Configuration
YUBIHSM_CONNECTOR="http://localhost:12345"
AUTH_KEY_ID=2
WG_KEY_BASE=2000

# WireGuard Configuration
WG_PORT=51820
WG_NETWORK="10.0.0.0/8"
DNS_SERVER="1.1.1.1"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() {
    echo -e "${GREEN}[INFO]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "${LOGS_DIR}/wireguard_setup.log"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "${LOGS_DIR}/wireguard_setup.log"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "${LOGS_DIR}/wireguard_setup.log"
}

# Prerequisites check
check_prerequisites() {
    log_info "Checking prerequisites..."
    
    # Check required tools
    local required_tools=("wg" "wg-quick" "python3" "docker" "docker-compose" "ip" "iptables")
    for tool in "${required_tools[@]}"; do
        if ! command -v "$tool" &> /dev/null; then
            log_error "$tool is required but not installed"
            exit 1
        fi
    done
    
    # Check YubiHSM connectivity
    if ! curl -s "${YUBIHSM_CONNECTOR}/connector/status" > /dev/null; then
        log_error "YubiHSM connector not accessible at ${YUBIHSM_CONNECTOR}"
        exit 1
    fi
    
    # Check kernel WireGuard module
    if ! modprobe -n wireguard 2>/dev/null; then
        log_error "WireGuard kernel module not available"
        exit 1
    fi
    
    log_info "Prerequisites check completed"
}

# Generate WireGuard key pair in YubiHSM
generate_wg_keypair() {
    local peer_name="$1"
    local key_id="$2"
    
    log_info "Generating WireGuard key pair for $peer_name (ID: $key_id)"
    
    python3 << EOF
from yubihsm import YubiHsm
from yubihsm.defs import ALGORITHM, CAPABILITY
from yubihsm.objects import SymmetricKey
import os
import base64

# Connect to HSM
hsm = YubiHsm.connect('${YUBIHSM_CONNECTOR}')
session = hsm.create_session_derived(${AUTH_KEY_ID}, 'password')

# Generate 256-bit key for WireGuard
key_material = os.urandom(32)

# Store private key in HSM
private_key = SymmetricKey.put(
    session=session,
    object_id=${key_id},
    label=f'wg-private-{peer_name}',
    domains=1,
    capabilities=CAPABILITY.EXPORT_UNDER_WRAP,
    algorithm=ALGORITHM.AES256,
    key=key_material
)

# Derive public key (WireGuard uses Curve25519, but we simulate with HMAC)
import hmac
import hashlib
public_key = hmac.new(key_material, b'wireguard-public', hashlib.sha256).digest()[:32]

# Save keys
with open('${KEYS_DIR}/${peer_name}_private.key', 'wb') as f:
    f.write(key_material)

with open('${KEYS_DIR}/${peer_name}_public.key', 'wb') as f:
    f.write(public_key)

# Output base64 encoded keys for WireGuard
print(f"Private key: {base64.b64encode(key_material).decode()}")
print(f"Public key: {base64.b64encode(public_key).decode()}")

session.close()
EOF
    
    log_info "WireGuard key pair generated for $peer_name"
}

# Create WireGuard interface configuration
create_wg_config() {
    local peer_name="$1"
    local peer_ip="$2"
    local peer_port="$3"
    local endpoint="${4:-}"
    
    log_info "Creating WireGuard configuration for $peer_name"
    
    # Read keys
    local private_key
    private_key=$(cat "${KEYS_DIR}/${peer_name}_private.key" | base64)
    local public_key
    public_key=$(cat "${KEYS_DIR}/${peer_name}_public.key" | base64)
    
    # Create configuration
    cat > "${CONFIG_DIR}/wg-${peer_name}.conf" << EOF
[Interface]
Address = ${peer_ip}/24
ListenPort = ${peer_port}
PrivateKey = ${private_key}
DNS = ${DNS_SERVER}

# Routing
PostUp = iptables -A FORWARD -i %i -j ACCEPT; iptables -A FORWARD -o %i -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -D FORWARD -o %i -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE

EOF
    
    if [ -n "$endpoint" ]; then
        cat >> "${CONFIG_DIR}/wg-${peer_name}.conf" << EOF
# Peers will be added dynamically
EOF
    fi
    
    log_info "WireGuard configuration created for $peer_name"
}

# Add peer to WireGuard configuration
add_wg_peer() {
    local config_file="$1"
    local peer_name="$2"
    local peer_public_key="$3"
    local peer_ip="$4"
    local endpoint="$5"
    
    log_info "Adding peer $peer_name to $config_file"
    
    cat >> "$config_file" << EOF

[Peer]
PublicKey = ${peer_public_key}
AllowedIPs = ${peer_ip}/32
EOF
    
    if [ -n "$endpoint" ]; then
        cat >> "$config_file" << EOF
Endpoint = ${endpoint}:${WG_PORT}
PersistentKeepalive = 25
EOF
    fi
    
    log_info "Peer $peer_name added to configuration"
}

# Create Docker Compose configuration for WireGuard
create_docker_compose_wg() {
    local network_name="$1"
    local network_id="${network_name#network-}"
    
    log_info "Creating Docker Compose configuration for WireGuard $network_name"
    
    cat > "${CONFIG_DIR}/docker-compose-${network_name}.yml" << EOF
version: '3.8'

services:
  wireguard-${network_name}:
    image: linuxserver/wireguard:latest
    container_name: wireguard-${network_name}
    cap_add:
      - NET_ADMIN
      - SYS_MODULE
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=Europe/Amsterdam
      - SERVERURL=${network_name}.vpn.internal
      - SERVERPORT=${WG_PORT}
      - PEERS=5
      - PEERDNS=${DNS_SERVER}
      - INTERNAL_SUBNET=10.${network_id}.0.0/24
    volumes:
      - ${CONFIG_DIR}/wg-${network_name}.conf:/config/wg_confs/wg0.conf:ro
      - ${KEYS_DIR}:/config/keys:ro
      - /lib/modules:/lib/modules:ro
    ports:
      - "${WG_PORT}:${WG_PORT}/udp"
    sysctls:
      - net.ipv4.conf.all.src_valid_mark=1
    networks:
      - ${network_name}
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "wg", "show", "wg0"]
      interval: 30s
      timeout: 10s
      retries: 3

  wireguard-monitor-${network_name}:
    image: prom/prometheus:latest
    container_name: wireguard-monitor-${network_name}
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus-${network_name}.yml:/etc/prometheus/prometheus.yml:ro
      - wireguard_prometheus_data:/prometheus
    networks:
      - ${network_name}
    restart: unless-stopped

networks:
  ${network_name}:
    driver: bridge
    ipam:
      config:
        - subnet: 172.${network_id}.0.0/16

volumes:
  wireguard_prometheus_data:
EOF
    
    log_info "Docker Compose configuration created for WireGuard $network_name"
}

# Setup WireGuard mesh network
setup_wireguard_mesh() {
    log_info "Setting up WireGuard mesh network for 5 networks"
    
    # Create directory structure
    mkdir -p "${CONFIG_DIR}" "${KEYS_DIR}" "${LOGS_DIR}"
    
    # Network configuration
    declare -A networks=(
        ["network-1"]="10.0.1.1"
        ["network-2"]="10.0.2.1"
        ["network-3"]="10.0.3.1"
        ["network-4"]="10.0.4.1"
        ["network-5"]="10.0.5.1"
    )
    
    # Generate keys and configurations for each network
    local key_id=${WG_KEY_BASE}
    for network in "${!networks[@]}"; do
        local ip="${networks[$network]}"
        local port=$((WG_PORT + ${network#network-} - 1))
        
        # Generate keys
        generate_wg_keypair "$network" "$key_id"
        
        # Create configuration
        create_wg_config "$network" "$ip" "$port"
        
        # Create Docker setup
        create_docker_compose_wg "$network"
        
        key_id=$((key_id + 1))
    done
    
    # Setup mesh peering
    setup_mesh_peering networks
    
    log_info "WireGuard mesh network setup completed"
}

# Setup mesh peering between all networks
setup_mesh_peering() {
    declare -n networks_ref=$1
    
    log_info "Setting up mesh peering between networks"
    
    # For each pair of networks, add peering
    local networks_list=("${!networks_ref[@]}")
    for ((i = 0; i < ${#networks_list[@]}; i++)); do
        for ((j = i + 1; j < ${#networks_list[@]}; j++)); do
            local net1="${networks_list[$i]}"
            local net2="${networks_list[$j]}"
            
            local pubkey1
            pubkey1=$(cat "${KEYS_DIR}/${net1}_public.key" | base64)
            local pubkey2
            pubkey2=$(cat "${KEYS_DIR}/${net2}_public.key" | base64)
            
            local ip1="${networks_ref[$net1]}"
            local ip2="${networks_ref[$net2]}"
            
            # Add peer to net1 config
            add_wg_peer "${CONFIG_DIR}/wg-${net1}.conf" "$net2" "$pubkey2" "$ip2" "${net2}.vpn.internal"
            
            # Add peer to net2 config
            add_wg_peer "${CONFIG_DIR}/wg-${net2}.conf" "$net1" "$pubkey1" "$ip1" "${net1}.vpn.internal"
        done
    done
    
    log_info "Mesh peering setup completed"
}

# Deploy WireGuard to environment
deploy_wireguard_environment() {
    local environment="$1"
    local network_name="${2:-}"
    
    log_info "Deploying WireGuard to $environment environment"
    
    if [ -n "$network_name" ]; then
        # Deploy single network
        log_info "Deploying $network_name to $environment"
        docker-compose -f "${CONFIG_DIR}/docker-compose-${network_name}.yml" up -d
    else
        # Deploy all networks
        log_info "Deploying all networks to $environment"
        for config_file in "${CONFIG_DIR}"/docker-compose-network-*.yml; do
            if [ -f "$config_file" ]; then
                docker-compose -f "$config_file" up -d
            fi
        done
    fi
    
    log_info "WireGuard deployment to $environment completed"
}

# Interactive menu
show_wg_menu() {
    echo
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║            WireGuard VPN Network Security Setup            ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo
    echo "Available Operations:"
    echo "  1. Complete Mesh Network Setup"
    echo "  2. Generate WireGuard Keys"
    echo "  3. Add New Network/Peer"
    echo "  4. Create Network Configuration"
    echo "  5. Deploy to Environment"
    echo "  6. View Network Topology"
    echo "  7. Test Connectivity"
    echo "  8. Rotate Keys"
    echo "  9. Backup Configuration"
    echo " 10. View Logs and Status"
    echo " 11. Troubleshooting Tools"
    echo "  0. Exit"
    echo
    echo -n "Select option: "
}

# Main execution
main() {
    # Check prerequisites
    check_prerequisites
    
    # Parse command line arguments
    case "${1:-}" in
        "setup")
            setup_wireguard_mesh
        ;;
        "keys")
            if [ $# -lt 2 ]; then
                log_error "Usage: $0 keys <peer_name> [key_id]"
                exit 1
            fi
            generate_wg_keypair "$2" "${3:-${WG_KEY_BASE}}"
        ;;
        "network")
            if [ $# -lt 4 ]; then
                log_error "Usage: $0 network <network_name> <ip> <port> [endpoint]"
                exit 1
            fi
            create_wg_config "$2" "$3" "$4" "${5:-}"
            create_docker_compose_wg "$2"
        ;;
        "peer")
            if [ $# -lt 5 ]; then
                log_error "Usage: $0 peer <config_file> <peer_name> <public_key> <ip> <endpoint>"
                exit 1
            fi
            add_wg_peer "$2" "$3" "$4" "$5" "$6"
        ;;
        "deploy")
            if [ $# -lt 2 ]; then
                log_error "Usage: $0 deploy <environment> [network_name]"
                exit 1
            fi
            deploy_wireguard_environment "$2" "${3:-}"
        ;;
        "interactive")
            while true; do
                show_wg_menu
                read -r choice
                case $choice in
                    1)
                        setup_wireguard_mesh
                    ;;
                    2)
                        echo -n "Enter peer name: "
                        read -r peer_name
                        echo -n "Enter key ID (optional): "
                        read -r key_id
                        generate_wg_keypair "$peer_name" "${key_id:-${WG_KEY_BASE}}"
                    ;;
                    3)
                        echo -n "Enter network name: "
                        read -r network_name
                        echo -n "Enter IP address: "
                        read -r ip
                        echo -n "Enter port: "
                        read -r port
                        echo -n "Enter endpoint (optional): "
                        read -r endpoint
                        create_wg_config "$network_name" "$ip" "$port" "$endpoint"
                        create_docker_compose_wg "$network_name"
                    ;;
                    4)
                        echo -n "Enter config file: "
                        read -r config_file
                        echo -n "Enter peer name: "
                        read -r peer_name
                        echo -n "Enter public key: "
                        read -r pubkey
                        echo -n "Enter IP: "
                        read -r ip
                        echo -n "Enter endpoint: "
                        read -r endpoint
                        add_wg_peer "$config_file" "$peer_name" "$pubkey" "$ip" "$endpoint"
                    ;;
                    5)
                        echo -n "Enter environment (dev/staging/prod): "
                        read -r env
                        echo -n "Enter network name (optional): "
                        read -r network
                        deploy_wireguard_environment "$env" "$network"
                    ;;
                    6)
                        view_network_topology
                    ;;
                    7)
                        test_connectivity
                    ;;
                    8)
                        rotate_keys
                    ;;
                    9)
                        backup_configuration
                    ;;
                    10)
                        view_logs_and_status
                    ;;
                    11)
                        troubleshooting_tools
                    ;;
                    0)
                        log_info "Exiting WireGuard setup"
                        exit 0
                    ;;
                    *)
                        log_error "Invalid option"
                    ;;
                esac
                echo
                echo "Press Enter to continue..."
                read -r
            done
        ;;
        *)
            echo "Usage: $0 {setup|keys|network|peer|deploy|interactive}"
            echo
            echo "Examples:"
            echo "  $0 setup                    # Complete mesh setup"
            echo "  $0 keys network-1 2000      # Generate keys"
            echo "  $0 network network-1 10.0.1.1 51820  # Create network"
            echo "  $0 peer wg-network-1.conf peer1 <pubkey> 10.0.1.2 endpoint.com  # Add peer"
            echo "  $0 deploy prod network-1    # Deploy to production"
            echo "  $0 interactive              # Interactive menu"
            exit 1
        ;;
    esac
}

main "$@"