#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 22, Internal Docker Registry.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Chapter 37: Internal Docker Registry - Setup Script
# =============================================================================
# Initializes the registry with TLS certificates and authentication
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGISTRY_HOST="${REGISTRY_HOST:-registry.local}"
ADMIN_USER="${ADMIN_USER:-admin}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:?set ADMIN_PASSWORD}"

echo "=== iGaming Registry Setup ==="
echo "Host: ${REGISTRY_HOST}"
echo ""

# Create directories
mkdir -p "${SCRIPT_DIR}/certs"
mkdir -p "${SCRIPT_DIR}/auth"

# Generate TLS certificates
echo "Generating TLS certificates..."
openssl req -newkey rsa:4096 -nodes -sha256 \
    -keyout "${SCRIPT_DIR}/certs/registry.key" \
    -x509 -days 365 \
    -out "${SCRIPT_DIR}/certs/registry.crt" \
    -subj "/C=GB/ST=London/L=London/O=iGaming Corp/CN=${REGISTRY_HOST}" \
    -addext "subjectAltName=DNS:${REGISTRY_HOST},DNS:localhost,IP:127.0.0.1"

chmod 600 "${SCRIPT_DIR}/certs/registry.key"
chmod 644 "${SCRIPT_DIR}/certs/registry.crt"

echo "  - Certificate: ${SCRIPT_DIR}/certs/registry.crt"
echo "  - Key: ${SCRIPT_DIR}/certs/registry.key"

# Generate htpasswd authentication
echo ""
echo "Generating authentication..."
if command -v htpasswd &> /dev/null; then
    htpasswd -Bbn "${ADMIN_USER}" "${ADMIN_PASSWORD}" > "${SCRIPT_DIR}/auth/htpasswd"
else
    # Fallback using Docker
    docker run --rm --entrypoint htpasswd \
        httpd:2 -Bbn "${ADMIN_USER}" "${ADMIN_PASSWORD}" > "${SCRIPT_DIR}/auth/htpasswd"
fi

chmod 600 "${SCRIPT_DIR}/auth/htpasswd"
echo "  - Admin user: ${ADMIN_USER}"
echo "  - htpasswd: ${SCRIPT_DIR}/auth/htpasswd"

# Configure Docker daemon to trust the registry
echo ""
echo "Configuring Docker daemon..."
DOCKER_CERT_DIR="/etc/docker/certs.d/${REGISTRY_HOST}:5000"

if [[ $EUID -eq 0 ]]; then
    mkdir -p "${DOCKER_CERT_DIR}"
    cp "${SCRIPT_DIR}/certs/registry.crt" "${DOCKER_CERT_DIR}/ca.crt"
    echo "  - Installed CA certificate to ${DOCKER_CERT_DIR}"
else
    echo "  - Run with sudo to configure Docker daemon:"
    echo "    sudo mkdir -p ${DOCKER_CERT_DIR}"
    echo "    sudo cp ${SCRIPT_DIR}/certs/registry.crt ${DOCKER_CERT_DIR}/ca.crt"
fi

# Create Prometheus configuration
echo ""
echo "Creating Prometheus configuration..."
cat > "${SCRIPT_DIR}/prometheus.yml" << 'EOF'
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'registry'
    static_configs:
      - targets: ['registry:5001']
    metrics_path: /metrics

  - job_name: 'prometheus'
    static_configs:
      - targets: ['localhost:9090']
EOF

echo "  - prometheus.yml created"

# Add to /etc/hosts if needed
echo ""
echo "To access the registry locally, add to /etc/hosts:"
echo "  127.0.0.1 ${REGISTRY_HOST}"
echo ""

# Start instructions
echo "=== Setup Complete ==="
echo ""
echo "To start the registry:"
echo "  cd ${SCRIPT_DIR}"
echo "  docker-compose up -d"
echo ""
echo "To test the registry:"
echo "  docker login ${REGISTRY_HOST}:5000 -u ${ADMIN_USER}"
echo "  docker tag nginx:latest ${REGISTRY_HOST}:5000/nginx:latest"
echo "  docker push ${REGISTRY_HOST}:5000/nginx:latest"
echo ""
echo "Access points:"
echo "  - Registry API: https://${REGISTRY_HOST}:5000/v2/_catalog"
echo "  - Registry UI:  http://localhost:8080"
echo "  - Trivy API:    http://localhost:8081"
echo "  - Prometheus:   http://localhost:9090"
echo "  - Grafana:      http://localhost:3000 (admin/registry_grafana)"
