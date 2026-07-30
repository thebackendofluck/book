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

# cf-tunnel-setup.sh
# Create and configure a Cloudflare Tunnel for an iGaming platform
# Usage: sudo ./cf-tunnel-setup.sh <TUNNEL_NAME>
# Prerequisites: Cloudflare account with Zero Trust plan
#
# Chapter 23 — DevSecOps for iGaming

set -euo pipefail

TUNNEL_NAME="${1:?Usage: $0 <TUNNEL_NAME>}"
CF_DOMAIN="${CF_DOMAIN:-acmetocasino.com}"
LOG_FILE="/var/log/cloudflare-tunnel-setup.log"
CONFIG_DIR="/etc/cloudflared"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$LOG_FILE"; }

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: Run as root" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# 1. Install cloudflared
# ---------------------------------------------------------------------------
if ! command -v cloudflared &>/dev/null; then
    log "Installing cloudflared..."
    curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb \
        -o /tmp/cloudflared.deb
    dpkg -i /tmp/cloudflared.deb
    rm /tmp/cloudflared.deb
    log "cloudflared installed: $(cloudflared version)"
else
    log "cloudflared already installed: $(cloudflared version)"
fi

# ---------------------------------------------------------------------------
# 2. Authenticate (interactive -- opens browser)
# ---------------------------------------------------------------------------
if [[ ! -f "${HOME}/.cloudflared/cert.pem" ]]; then
    log "Authenticating with Cloudflare (browser will open)..."
    cloudflared tunnel login
fi

# ---------------------------------------------------------------------------
# 3. Create the tunnel
# ---------------------------------------------------------------------------
log "Creating tunnel: ${TUNNEL_NAME}..."
cloudflared tunnel create "${TUNNEL_NAME}"

# Get tunnel ID
TUNNEL_ID=$(cloudflared tunnel list --output json | python3 -c "
import json, sys
tunnels = json.load(sys.stdin)
for t in tunnels:
    if t['name'] == '${TUNNEL_NAME}':
        print(t['id'])
        break
")

if [[ -z "${TUNNEL_ID}" ]]; then
    echo "ERROR: Failed to get tunnel ID" >&2
    exit 1
fi

log "Tunnel created: ${TUNNEL_ID}"

# ---------------------------------------------------------------------------
# 4. Create configuration file
# ---------------------------------------------------------------------------
mkdir -p "${CONFIG_DIR}"

cat > "${CONFIG_DIR}/config.yml" <<CFCONFIG
# Cloudflare Tunnel configuration for iGaming platform
# Tunnel: ${TUNNEL_NAME} (${TUNNEL_ID})
# Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)

tunnel: ${TUNNEL_ID}
credentials-file: ${CONFIG_DIR}/${TUNNEL_ID}.json

# Metrics for Prometheus scraping
metrics: 0.0.0.0:2000

# Origin server configuration
originRequest:
  connectTimeout: 30s
  noTLSVerify: false
  # Keep connections alive for long-polling dashboards
  keepAliveTimeout: 90s
  # Disable chunked encoding for compatibility with legacy game APIs
  disableChunkedEncoding: false

# Ingress rules -- route hostnames to local services
ingress:
  # Backoffice admin panel
  - hostname: admin.${CF_DOMAIN}
    service: http://localhost:8080
    originRequest:
      connectTimeout: 10s
      # Backoffice uses websockets for real-time dashboard updates
      noTLSVerify: false

  # Monitoring -- Grafana
  - hostname: grafana.${CF_DOMAIN}
    service: http://localhost:3000
    originRequest:
      connectTimeout: 10s

  # Monitoring -- Prometheus
  - hostname: prometheus.${CF_DOMAIN}
    service: http://localhost:9090
    originRequest:
      connectTimeout: 10s

  # Database admin -- pgAdmin (restricted via Access policy)
  - hostname: dbadmin.${CF_DOMAIN}
    service: http://localhost:5050
    originRequest:
      connectTimeout: 30s

  # CI/CD -- Jenkins or GitLab
  - hostname: ci.${CF_DOMAIN}
    service: http://localhost:8081
    originRequest:
      connectTimeout: 60s

  # API documentation -- Swagger/ReDoc (public)
  - hostname: docs.${CF_DOMAIN}
    service: http://localhost:8888
    originRequest:
      connectTimeout: 10s

  # Catch-all rule (required -- returns 404 for unmatched routes)
  - service: http_status:404
CFCONFIG

log "Configuration written to ${CONFIG_DIR}/config.yml"

# ---------------------------------------------------------------------------
# 5. Copy tunnel credentials
# ---------------------------------------------------------------------------
CRED_SRC="${HOME}/.cloudflared/${TUNNEL_ID}.json"
if [[ -f "${CRED_SRC}" ]]; then
    cp "${CRED_SRC}" "${CONFIG_DIR}/${TUNNEL_ID}.json"
    chmod 600 "${CONFIG_DIR}/${TUNNEL_ID}.json"
    log "Credentials copied to ${CONFIG_DIR}/"
fi

# ---------------------------------------------------------------------------
# 6. Create DNS records for each hostname
# ---------------------------------------------------------------------------
HOSTNAMES=("admin" "grafana" "prometheus" "dbadmin" "ci" "docs")
for host in "${HOSTNAMES[@]}"; do
    log "Creating DNS record: ${host}.${CF_DOMAIN} -> tunnel ${TUNNEL_ID}"
    cloudflared tunnel route dns "${TUNNEL_NAME}" "${host}.${CF_DOMAIN}" || true
done

# ---------------------------------------------------------------------------
# 7. Install as systemd service
# ---------------------------------------------------------------------------
log "Installing as systemd service..."
cloudflared service install

# Override the default service to use our config
mkdir -p /etc/systemd/system/cloudflared.service.d
cat > /etc/systemd/system/cloudflared.service.d/override.conf <<OVERRIDE
[Service]
ExecStart=
ExecStart=/usr/bin/cloudflared tunnel --config ${CONFIG_DIR}/config.yml run
Restart=always
RestartSec=5
OVERRIDE

systemctl daemon-reload
systemctl enable cloudflared
systemctl start cloudflared

# ---------------------------------------------------------------------------
# 8. Verify tunnel is running
# ---------------------------------------------------------------------------
sleep 3
if systemctl is-active --quiet cloudflared; then
    log "Tunnel is ACTIVE"
    cloudflared tunnel info "${TUNNEL_NAME}"
else
    log "ERROR: Tunnel failed to start"
    journalctl -u cloudflared --no-pager -n 20
    exit 1
fi

log "Tunnel setup complete. Services available at:"
for host in "${HOSTNAMES[@]}"; do
    log "  https://${host}.${CF_DOMAIN}"
done
log ""
log "Next step: Configure Cloudflare Access policies for each hostname"
