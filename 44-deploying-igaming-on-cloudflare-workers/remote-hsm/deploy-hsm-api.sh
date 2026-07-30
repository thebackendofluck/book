#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 44, Deploying iGaming Platforms on Cloudflare Workers.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# Deploy HSM Proxy API on ops-host (10.0.0.11).
#
# Prerequisites on ops-host:
#   apt install python3.11 python3.11-venv nginx
#   systemctl status yubihsm-connector   # must be running
#   bao status                           # OpenBao must be unsealed
#
# Run from your workstation:
#   bash deploy-hsm-api.sh
#
# Or on ops-host directly:
#   bash deploy-hsm-api.sh --local

set -euo pipefail

# ─── Config ───────────────────────────────────────────────────────────────────

OPS_HOST="${OPS_HOST:-ops-server}"               # SSH alias in ~/.ssh/config
OPS_IP="${OPS_IP:-10.0.0.11}"
APP_DIR="/opt/hsm-proxy-api"
APP_USER="hsmapi"                                     # Dedicated service user
SERVICE_NAME="hsm-proxy-api"
NGINX_CONF_SRC="$(cd "$(dirname "$0")" && pwd)/nginx-hsm-api.conf"
API_SRC="$(cd "$(dirname "$0")" && pwd)/hsm-proxy-api.py"
VENV_DIR="${APP_DIR}/venv"
LOG_DIR="/var/log/hsm-proxy-api"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}[deploy]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC}  $*"; }
err()  { echo -e "${RED}[error]${NC} $*" >&2; }

# ─── Determine execution context ─────────────────────────────────────────────

if [[ "${1:-}" == "--local" ]]; then
    # Running directly on ops-host
    RUN() { bash -c "$*"; }
    COPY() { cp "$1" "$2"; }
    log "Running in local mode on $(hostname)"
else
    # Running from workstation — SSH into ops-host
    log "Deploying to ${OPS_HOST} (${OPS_IP}) via SSH"
    RUN() { ssh -o BatchMode=yes -o ConnectTimeout=10 "${OPS_HOST}" "$*"; }
    COPY() { scp -o BatchMode=yes "$1" "${OPS_HOST}:$2"; }
fi

# ─── Step 1: Verify prerequisites ────────────────────────────────────────────

log "Step 1: Verifying prerequisites"

RUN "systemctl is-active yubihsm-connector > /dev/null 2>&1 || (echo 'yubihsm-connector not running — start with: systemctl start yubihsm-connector' && exit 1)"
RUN "command -v bao > /dev/null 2>&1 && bao status | grep -q 'Sealed.*false' || echo 'WARNING: OpenBao may be sealed or unavailable — HSM ops will fail'"
RUN "python3.11 --version > /dev/null 2>&1 || (echo 'python3.11 required — apt install python3.11 python3.11-venv' && exit 1)"

log "Prerequisites OK"

# ─── Step 2: Create service user ─────────────────────────────────────────────

log "Step 2: Creating service user ${APP_USER}"
RUN "id ${APP_USER} > /dev/null 2>&1 || useradd --system --no-create-home --shell /bin/false ${APP_USER}"

# ─── Step 3: Create application directory ────────────────────────────────────

log "Step 3: Creating application directory ${APP_DIR}"
RUN "mkdir -p ${APP_DIR} ${LOG_DIR}"
RUN "chown ${APP_USER}:${APP_USER} ${APP_DIR} ${LOG_DIR}"

# ─── Step 4: Copy application files ──────────────────────────────────────────

log "Step 4: Copying application files"
COPY "${API_SRC}" "${APP_DIR}/hsm-proxy-api.py"
RUN "chown ${APP_USER}:${APP_USER} ${APP_DIR}/hsm-proxy-api.py"
RUN "chmod 640 ${APP_DIR}/hsm-proxy-api.py"

# ─── Step 5: Create Python venv and install dependencies ─────────────────────

log "Step 5: Installing Python dependencies"
RUN "python3.11 -m venv ${VENV_DIR}"
RUN "${VENV_DIR}/bin/pip install --quiet --upgrade pip"
RUN "${VENV_DIR}/bin/pip install --quiet \
    'fastapi==0.111.0' \
    'uvicorn[standard]==0.29.0' \
    'httpx==0.27.0' \
    'pydantic==2.7.1' \
    'slowapi==0.1.9'"
RUN "chown -R ${APP_USER}:${APP_USER} ${VENV_DIR}"

# ─── Step 6: Generate API key if not already set ─────────────────────────────

log "Step 6: Configuring environment"
RUN "
if [ ! -f /etc/hsm-proxy-api/env ]; then
    mkdir -p /etc/hsm-proxy-api
    chmod 700 /etc/hsm-proxy-api

    # Generate a new API key if none exists
    HSM_API_KEY=\$(openssl rand -hex 32)
    echo \"HSM_API_KEY=\${HSM_API_KEY}\" > /etc/hsm-proxy-api/env
    echo \"BAO_ADDR=http://127.0.0.1:8200\" >> /etc/hsm-proxy-api/env
    echo \"BAO_TRANSIT_MOUNT=transit\" >> /etc/hsm-proxy-api/env
    # BAO_TOKEN is written by the nightly AppRole renewal script
    echo \"BAO_TOKEN=\" >> /etc/hsm-proxy-api/env
    echo \"YH_CONNECTOR_URL=http://127.0.0.1:12345\" >> /etc/hsm-proxy-api/env

    chmod 600 /etc/hsm-proxy-api/env
    chown root:${APP_USER} /etc/hsm-proxy-api/env
    echo ''
    echo '================================================='
    echo 'NEW API KEY GENERATED (copy this into Workers Secret):'
    grep HSM_API_KEY /etc/hsm-proxy-api/env
    echo '================================================='
    echo ''
    echo 'Run: npx wrangler secret put HSM_API_KEY'
    echo '     npx wrangler secret put HSM_API_URL'
    echo '     (HSM_API_URL = https://hsm-api.acmetocasino.com)'
    echo ''
else
    echo 'env file already exists — skipping key generation'
fi
"

# ─── Step 7: Install systemd service ─────────────────────────────────────────

log "Step 7: Installing systemd service"
RUN "cat > /etc/systemd/system/${SERVICE_NAME}.service << 'EOF'
[Unit]
Description=AcmeToCasino HSM Proxy API
Documentation=https://github.com/acmetocasino/egambling-book
After=network.target yubihsm-connector.service
Requires=yubihsm-connector.service

[Service]
Type=exec
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=/etc/hsm-proxy-api/env
ExecStart=${VENV_DIR}/bin/python ${APP_DIR}/hsm-proxy-api.py
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}

# Hardening
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=${LOG_DIR}
PrivateTmp=yes
PrivateDevices=yes

# Resource limits — HSM has ~max 100 ops/s
LimitNOFILE=1024
CPUQuota=20%
MemoryMax=256M

[Install]
WantedBy=multi-user.target
EOF"

RUN "systemctl daemon-reload"
RUN "systemctl enable ${SERVICE_NAME}"

# ─── Step 8: Install nginx config ────────────────────────────────────────────

log "Step 8: Installing nginx configuration"
COPY "${NGINX_CONF_SRC}" "/etc/nginx/sites-available/hsm-api"

RUN "
# Add rate limit zones to nginx.conf if not present
if ! grep -q 'zone=hsm_per_ip' /etc/nginx/nginx.conf; then
    sed -i '/http {/a\\    limit_req_zone \$binary_remote_addr zone=hsm_per_ip:10m rate=50r/s;\n    limit_req_zone \$http_x_api_key zone=hsm_per_key:10m rate=200r/m;' /etc/nginx/nginx.conf
fi
"

RUN "
[ -L /etc/nginx/sites-enabled/hsm-api ] || ln -s /etc/nginx/sites-available/hsm-api /etc/nginx/sites-enabled/hsm-api
nginx -t
"

# ─── Step 9: Start services ───────────────────────────────────────────────────

log "Step 9: Starting services"
RUN "systemctl start ${SERVICE_NAME}"
RUN "systemctl reload nginx"

# Wait for API to be ready
RUN "
for i in \$(seq 1 10); do
    if curl -sf http://127.0.0.1:8000/hsm/health > /dev/null; then
        echo 'API is up'
        break
    fi
    echo \"Waiting for API... (\$i/10)\"
    sleep 2
done
"

# ─── Step 10: Smoke test ──────────────────────────────────────────────────────

log "Step 10: Running smoke test"
RUN "
API_KEY=\$(grep HSM_API_KEY /etc/hsm-proxy-api/env | cut -d= -f2)
curl -sf -H \"X-API-Key: \${API_KEY}\" http://127.0.0.1:8000/hsm/health | python3 -m json.tool
"

log "Deployment complete!"
warn "Next steps:"
warn "  1. Copy the HSM_API_KEY from /etc/hsm-proxy-api/env"
warn "  2. npx wrangler secret put HSM_API_KEY"
warn "  3. npx wrangler secret put HSM_API_URL  # https://hsm-api.acmetocasino.com"
warn "  4. Update Cloudflare IP allowlist in nginx-hsm-api.conf quarterly"
warn "  5. Configure BAO_TOKEN renewal: see /opt/bao-appole-renew.sh"
