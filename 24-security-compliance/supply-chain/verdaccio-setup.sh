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

# verdaccio-setup.sh — Install and configure Verdaccio as a private npm registry
# Provides internal package hosting with upstream npmjs.org mirroring
# Usage: ./verdaccio-setup.sh [--port PORT] [--storage-dir DIR]
set -euo pipefail

PORT="${1:-4873}"
STORAGE_DIR="${2:-/opt/verdaccio/storage}"
CONFIG_DIR="/opt/verdaccio/conf"
CONFIG_FILE="${CONFIG_DIR}/config.yaml"

echo "=== Verdaccio Private npm Registry Setup ==="
echo "Port: ${PORT}"
echo "Storage: ${STORAGE_DIR}"

# Step 1: Install Verdaccio globally
echo "[1/5] Installing Verdaccio..."
npm install -g verdaccio

# Step 2: Create directories
echo "[2/5] Creating directories..."
mkdir -p "${STORAGE_DIR}" "${CONFIG_DIR}"

# Step 3: Write configuration
echo "[3/5] Writing configuration to ${CONFIG_FILE}..."
cat > "${CONFIG_FILE}" <<YAML
# Verdaccio configuration for iGaming private registry
storage: ${STORAGE_DIR}
auth:
  htpasswd:
    file: ${CONFIG_DIR}/htpasswd
    # Maximum number of users allowed to register
    # Set to -1 to disable registration (manage users via CLI)
    max_users: -1

uplinks:
  npmjs:
    url: https://registry.npmjs.org/
    timeout: 30s
    maxage: 10m
    fail_timeout: 5m
    max_fails: 3
    cache: true

packages:
  # Scoped packages for your organization
  '@igaming/*':
    access: \$authenticated
    publish: \$authenticated
    unpublish: \$authenticated

  '@casino/*':
    access: \$authenticated
    publish: \$authenticated
    unpublish: \$authenticated

  '@platform/*':
    access: \$authenticated
    publish: \$authenticated
    unpublish: \$authenticated

  # Public packages — proxy to npmjs
  '**':
    access: \$authenticated
    publish: \$authenticated
    proxy: npmjs

server:
  keepAliveTimeout: 60

middlewares:
  audit:
    enabled: true

listen: 0.0.0.0:${PORT}

logs:
  type: stdout
  format: pretty
  level: warn

# Security settings
security:
  api:
    jwt:
      sign:
        expiresIn: 60d
  web:
    sign:
      expiresIn: 7d
YAML

# Step 4: Create initial admin user
echo "[4/5] Creating admin user..."
# Install htpasswd utility
npm install -g htpasswd-cli 2>/dev/null || pip install htpasswd 2>/dev/null || true
# Create htpasswd file with admin user (change password in production)
ADMIN_HASH=$(node -e "const crypto = require('crypto'); console.log('{SHA}' + crypto.createHash('sha1').update('changeme-in-production').digest('base64'))")
echo "admin:${ADMIN_HASH}" > "${CONFIG_DIR}/htpasswd"

# Step 5: Verify installation
echo "[5/5] Verifying installation..."
verdaccio --version

echo ""
echo "=== Verdaccio Setup Complete ==="
echo ""
echo "To start the server:"
echo "  verdaccio --config ${CONFIG_FILE}"
echo ""
echo "To configure npm to use this registry:"
echo "  npm set registry http://localhost:${PORT}/"
echo "  npm adduser --registry http://localhost:${PORT}/"
echo ""
echo "To publish internal packages:"
echo "  npm publish --registry http://localhost:${PORT}/"
echo ""
echo "To scope only internal packages (recommended):"
echo "  npm config set @igaming:registry http://localhost:${PORT}/"
echo "  npm config set @casino:registry http://localhost:${PORT}/"
echo ""
echo "IMPORTANT: Change the default password before production use."
echo "IMPORTANT: Place behind a reverse proxy with TLS for production."
echo ""
echo "=== Docker Alternative ==="
cat <<'DOCKER'
# docker-compose.yml
version: '3.8'
services:
  verdaccio:
    image: verdaccio/verdaccio:5
    container_name: verdaccio
    ports:
      - "4873:4873"
    volumes:
      - ./conf:/verdaccio/conf
      - ./storage:/verdaccio/storage
      - ./plugins:/verdaccio/plugins
    environment:
      - VERDACCIO_PORT=4873
    restart: unless-stopped
DOCKER

echo ""
echo "=== systemd Unit Example ==="
cat <<'SYSTEMD'
# /etc/systemd/system/verdaccio.service
[Unit]
Description=Verdaccio Private npm Registry
After=network.target

[Service]
Type=simple
User=verdaccio
Group=verdaccio
ExecStart=/usr/local/bin/verdaccio --config /opt/verdaccio/conf/config.yaml
Restart=always
RestartSec=5
Environment=NODE_ENV=production

[Install]
WantedBy=multi-user.target
SYSTEMD
