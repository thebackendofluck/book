#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 23, DevSecOps for iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# tailscale-server-setup.sh
# Install and configure Tailscale on an iGaming production server
# Tested on Ubuntu 22.04 LTS and 24.04 LTS
# Usage: sudo ./tailscale-server-setup.sh <AUTH_KEY> <ROLE>
# Roles: api-server, db-gateway, monitoring, backoffice
#
# Chapter 23 — DevSecOps for iGaming

set -euo pipefail

AUTH_KEY="${1:?Usage: $0 <AUTH_KEY> <ROLE>}"
ROLE="${2:?Usage: $0 <AUTH_KEY> <ROLE>}"
LOG_FILE="/var/log/tailscale-setup.log"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$LOG_FILE"; }

# ---------------------------------------------------------------------------
# 1. Validate environment
# ---------------------------------------------------------------------------
if [[ $EUID -ne 0 ]]; then
    echo "ERROR: This script must be run as root" >&2
    exit 1
fi

log "Starting Tailscale setup for role: ${ROLE}"

# ---------------------------------------------------------------------------
# 2. Install Tailscale
# ---------------------------------------------------------------------------
if ! command -v tailscale &>/dev/null; then
    log "Installing Tailscale..."
    curl -fsSL https://tailscale.com/install.sh | sh
    log "Tailscale installed: $(tailscale version)"
else
    log "Tailscale already installed: $(tailscale version)"
fi

# ---------------------------------------------------------------------------
# 3. Enable IP forwarding (required for subnet routing)
# ---------------------------------------------------------------------------
log "Enabling IP forwarding..."
cat > /etc/sysctl.d/99-tailscale.conf <<SYSCTL
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
SYSCTL
sysctl -p /etc/sysctl.d/99-tailscale.conf

# ---------------------------------------------------------------------------
# 4. Configure firewall rules for Tailscale
# ---------------------------------------------------------------------------
if command -v ufw &>/dev/null; then
    log "Configuring UFW for Tailscale..."
    ufw allow in on tailscale0
    ufw allow 41641/udp comment "Tailscale direct connections"
    ufw reload
fi

# ---------------------------------------------------------------------------
# 5. Determine subnet routes and tags based on role
# ---------------------------------------------------------------------------
declare -a TS_ARGS=()
TS_ARGS+=(--authkey="${AUTH_KEY}")
TS_ARGS+=(--hostname="$(hostname)-ts")
TS_ARGS+=(--accept-dns=true)
TS_ARGS+=(--accept-routes=true)
TS_ARGS+=(--ssh)                 # Enable Tailscale SSH

case "${ROLE}" in
    api-server)
        # shellcheck disable=SC2054  # comma is part of tag list value, not array separator
        TS_ARGS+=(--advertise-tags=tag:api,tag:production)
        log "Role: API server -- no subnet routing needed"
        ;;
    db-gateway)
        # shellcheck disable=SC2054
        TS_ARGS+=(--advertise-tags=tag:database,tag:production)
        TS_ARGS+=(--advertise-routes=10.0.2.0/24)  # Database subnet
        log "Role: Database gateway -- advertising 10.0.2.0/24"
        ;;
    monitoring)
        # shellcheck disable=SC2054
        TS_ARGS+=(--advertise-tags=tag:monitoring,tag:production)
        TS_ARGS+=(--advertise-routes=10.0.3.0/24)  # Monitoring subnet
        log "Role: Monitoring -- advertising 10.0.3.0/24"
        ;;
    backoffice)
        # shellcheck disable=SC2054
        TS_ARGS+=(--advertise-tags=tag:backoffice,tag:production)
        log "Role: Backoffice -- no subnet routing needed"
        ;;
    *)
        log "ERROR: Unknown role '${ROLE}'. Use: api-server, db-gateway, monitoring, backoffice"
        exit 1
        ;;
esac

# ---------------------------------------------------------------------------
# 6. Start Tailscale and authenticate
# ---------------------------------------------------------------------------
log "Starting Tailscale with role-specific configuration..."
systemctl enable --now tailscaled
tailscale up "${TS_ARGS[@]}"

# ---------------------------------------------------------------------------
# 7. Verify connectivity
# ---------------------------------------------------------------------------
sleep 3
TS_IP=$(tailscale ip -4)
TS_STATUS=$(tailscale status --json | python3 -c "
import json, sys
s = json.load(sys.stdin)
print(f\"Connected as {s['Self']['HostName']} ({s['Self']['TailscaleIPs'][0]})\")
")

log "Tailscale active: ${TS_STATUS}"
log "Tailscale IPv4: ${TS_IP}"
log "Setup complete. Verify with: tailscale status"
