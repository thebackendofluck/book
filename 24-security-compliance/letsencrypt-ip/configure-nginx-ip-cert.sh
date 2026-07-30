#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# Configure nginx to use a Let's Encrypt IP address certificate.
#
# This script creates two nginx server blocks:
#   1. Port 80  — serves ACME challenge files for cert renewal (default_server catch-all)
#   2. Port 443 — TLS endpoint for direct IP access using the issued cert
#
# The TLS endpoint is suitable for:
#   - Internal health checks / admin APIs accessed by IP
#   - Game supplier callbacks that POST to your IP directly
#   - Regulatory reporting endpoints reached via VPN to your IP
#   - Disaster recovery access when DNS is unavailable
#
# Usage:
#   ./configure-nginx-ip-cert.sh <public-ip> [nginx-sites-available-dir]
#
# Example:
#   ./configure-nginx-ip-cert.sh 203.0.113.1

set -euo pipefail

IP="${1:?Usage: $0 <public-ip> [nginx-sites-dir]}"
NGINX_SITES="${2:-/etc/nginx/sites-available}"
NGINX_ENABLED="${NGINX_SITES%available}enabled"
WEBROOT="/var/www/acme-challenge"
CERT_PATH="/etc/letsencrypt/live/${IP}"

check_cert_exists() {
    if [[ ! -f "${CERT_PATH}/fullchain.pem" ]]; then
        echo "ERROR: Certificate not found at ${CERT_PATH}/fullchain.pem"
        echo "Run obtain-ip-cert.sh first."
        exit 1
    fi
    echo "Certificate found: ${CERT_PATH}/fullchain.pem"
    echo "Expires: $(openssl x509 -in "${CERT_PATH}/fullchain.pem" -noout -enddate | cut -d= -f2)"
}

write_acme_challenge_config() {
    local CONFIG="${NGINX_SITES}/ip-acme-challenge"
    cat > "${CONFIG}" << EOF
# ACME challenge endpoint for IP address certificate renewal.
# This catch-all on port 80 serves the http-01 challenge files
# written by certbot --webroot. All other traffic is redirected
# to the primary domain over HTTPS.
server {
    listen 80 default_server;
    server_name _;

    # Serve ACME challenge tokens written by certbot
    location /.well-known/acme-challenge/ {
        root ${WEBROOT};
        try_files \$uri =404;
    }

    # Redirect everything else to the primary domain
    location / {
        return 301 https://new.acmetocasino.com\$request_uri;
    }
}
EOF
    echo "Written: ${CONFIG}"
    ln -sf "${CONFIG}" "${NGINX_ENABLED}/ip-acme-challenge"
}

write_tls_config() {
    local CONFIG="${NGINX_SITES}/ip-tls"
    cat > "${CONFIG}" << EOF
# TLS endpoint for direct IP access (${IP}).
# Certificate: Let's Encrypt shortlived profile.
# Renewed automatically every ~5 days via the certbot renew cron job.
#
# Use cases in iGaming:
#   - Game supplier callbacks (some suppliers POST to IP directly)
#   - Regulatory reporting endpoints reached via VPN
#   - Internal health checks from monitoring systems that use IP
#   - Disaster recovery access when DNS is unavailable
server {
    listen 443 ssl;
    server_name _;

    ssl_certificate     ${CERT_PATH}/fullchain.pem;
    ssl_certificate_key ${CERT_PATH}/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    ssl_session_timeout 1d;
    ssl_session_cache   shared:SSL_IP:10m;

    # Health check for load balancers / monitoring
    location /health {
        access_log off;
        return 200 'OK';
        add_header Content-Type text/plain;
    }

    # Supplier callback endpoint (POST only)
    location /callback/ {
        limit_except POST { deny all; }
        proxy_pass http://127.0.0.1:8080/callback/;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    }

    # Default: redirect to domain
    location / {
        return 301 https://new.acmetocasino.com\$request_uri;
    }
}
EOF
    echo "Written: ${CONFIG}"
    ln -sf "${CONFIG}" "${NGINX_ENABLED}/ip-tls"
}

reload_nginx() {
    echo "Testing nginx configuration..."
    nginx -t
    echo "Reloading nginx..."
    systemctl reload nginx
    echo "nginx reloaded."
}

verify_cert_served() {
    echo ""
    echo "Verifying certificate on ${IP}:443..."
    openssl s_client -connect "${IP}:443" </dev/null 2>&1 | \
        grep -E 'subject|issuer|Verify|IP Address|notAfter' | head -8
}

main() {
    echo "=== Configure nginx for IP TLS (${IP}) ==="
    check_cert_exists
    mkdir -p "${WEBROOT}/.well-known/acme-challenge"
    write_acme_challenge_config
    write_tls_config
    reload_nginx
    verify_cert_served
    echo ""
    echo "Done. Direct IP TLS is now active:"
    echo "  https://${IP}/health  — should return 200 OK"
}

main "$@"
