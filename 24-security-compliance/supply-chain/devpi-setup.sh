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

# devpi-setup.sh — Install and configure devpi as a private PyPI repository
# Provides internal package hosting with upstream PyPI mirroring
# Usage: ./devpi-setup.sh [--port PORT] [--data-dir DIR]
set -euo pipefail

PORT="${1:-3141}"
DATA_DIR="${2:-/opt/devpi/data}"
DEVPI_USER="admin"
DEVPI_INDEX="internal"

echo "=== devpi Private PyPI Setup ==="
echo "Port: ${PORT}"
echo "Data directory: ${DATA_DIR}"

# Step 1: Install devpi server and client
echo "[1/6] Installing devpi-server and devpi-client..."
pip install --upgrade devpi-server devpi-client devpi-web

# Step 2: Initialize the server data directory
echo "[2/6] Initializing devpi server at ${DATA_DIR}..."
mkdir -p "${DATA_DIR}"
devpi-init --serverdir "${DATA_DIR}" 2>/dev/null || echo "Server directory already initialized"

# Step 3: Start devpi-server (background for setup, use systemd in production)
echo "[3/6] Starting devpi-server on port ${PORT}..."
devpi-server --serverdir "${DATA_DIR}" --port "${PORT}" --host 0.0.0.0 &
DEVPI_PID=$!
sleep 3

# Step 4: Configure devpi client and create internal user/index
echo "[4/6] Configuring devpi client..."
devpi use "http://localhost:${PORT}"
devpi login root --password=""

echo "[5/6] Creating admin user and internal index..."
devpi user -c "${DEVPI_USER}" password="changeme-in-production"
devpi login "${DEVPI_USER}" --password="changeme-in-production"

# Create internal index that mirrors PyPI (upstream)
devpi index -c "${DEVPI_INDEX}" \
  bases=root/pypi \
  volatile=True \
  acl_upload="${DEVPI_USER}"

echo "[6/6] Verifying setup..."
devpi use "${DEVPI_USER}/${DEVPI_INDEX}"
devpi getjson

# Stop the background server (use systemd unit in production)
kill "${DEVPI_PID}" 2>/dev/null

echo ""
echo "=== devpi Setup Complete ==="
echo ""
echo "To start the server:"
echo "  devpi-server --serverdir ${DATA_DIR} --port ${PORT} --host 0.0.0.0"
echo ""
echo "To configure pip to use this index:"
echo "  pip install --index-url http://localhost:${PORT}/${DEVPI_USER}/${DEVPI_INDEX}/+simple/ <package>"
echo ""
echo "Or set globally in ~/.pip/pip.conf:"
echo "  [global]"
echo "  index-url = http://localhost:${PORT}/${DEVPI_USER}/${DEVPI_INDEX}/+simple/"
echo ""
echo "To upload internal packages:"
echo "  devpi use http://localhost:${PORT}/${DEVPI_USER}/${DEVPI_INDEX}"
echo "  devpi login ${DEVPI_USER} --password=<password>"
echo "  devpi upload dist/*"
echo ""
echo "IMPORTANT: Change the default password before production use."
echo "IMPORTANT: Place behind a reverse proxy with TLS for production."
echo ""
echo "=== systemd Unit Example ==="
cat <<'SYSTEMD'
# /etc/systemd/system/devpi.service
[Unit]
Description=devpi Private PyPI Server
After=network.target

[Service]
Type=simple
User=devpi
Group=devpi
ExecStart=/usr/local/bin/devpi-server --serverdir /opt/devpi/data --port 3141 --host 0.0.0.0
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SYSTEMD
