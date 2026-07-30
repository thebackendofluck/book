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

# Obtain a TLS certificate for a public IP address using Let's Encrypt.
#
# Requirements:
#   - certbot >= 5.3  (introduces --ip-address flag)
#   - Port 80 publicly reachable from the internet (for http-01 challenge)
#   - A webroot directory served by your web server (or use --standalone)
#
# Let's Encrypt notes:
#   - IP certs MUST use the "shortlived" profile (6-day validity)
#   - Only http-01 and tls-alpn-01 challenges are supported (not dns-01)
#   - Only public (routable) IPs are supported; private RFC 1918 ranges fail
#   - Both IPv4 and IPv6 are supported
#   - No OCSP/CRL URLs in the certificate (short lifespan makes revocation moot)
#   - SAN field contains "IP Address:x.x.x.x" (not DNS:...)
#
# Usage:
#   ./obtain-ip-cert.sh <public-ip> [webroot-path]
#
# Examples:
#   ./obtain-ip-cert.sh 203.0.113.1
#   ./obtain-ip-cert.sh 203.0.113.1 /var/www/html
#   ./obtain-ip-cert.sh 2001:db8::1 /var/www/html

set -euo pipefail

IP="${1:?Usage: $0 <public-ip> [webroot-path]}"
WEBROOT="${2:-/var/www/acme-challenge}"
EMAIL="${CERTBOT_EMAIL:-ops@acmetocasino.com}"
CERTBOT_BIN="${CERTBOT_BIN:-certbot}"

# Validate certbot version supports IP certs (>= 5.3)
check_certbot_version() {
    local version
    version=$("$CERTBOT_BIN" --version 2>&1 | grep -oP '\d+\.\d+\.\d+' | head -1)
    local major minor
    major=$(echo "$version" | cut -d. -f1)
    minor=$(echo "$version" | cut -d. -f2)

    if [[ "$major" -lt 5 ]] || [[ "$major" -eq 5 && "$minor" -lt 3 ]]; then
        echo "ERROR: certbot $version detected. IP address certificates require certbot >= 5.3."
        echo "Upgrade: pip install --upgrade certbot"
        echo "Or use a venv: python3 -m venv /opt/certbot-venv && /opt/certbot-venv/bin/pip install certbot"
        exit 1
    fi
    echo "certbot $version — OK (>= 5.3 required for IP certs)"
}

# Ensure the webroot challenge directory exists
ensure_webroot() {
    mkdir -p "${WEBROOT}/.well-known/acme-challenge"
    echo "Webroot: ${WEBROOT}"
}

# Attempt certificate issuance
issue_cert() {
    echo "Requesting Let's Encrypt certificate for IP: ${IP}"
    echo "Challenge type: http-01 (webroot)"
    echo "Profile: shortlived (6-day validity — required for IP certs)"
    echo ""

    "$CERTBOT_BIN" certonly \
        --webroot \
        -w "${WEBROOT}" \
        --ip-address "${IP}" \
        --preferred-profile shortlived \
        --agree-tos \
        --email "${EMAIL}" \
        --no-eff-email \
        --non-interactive

    echo ""
    echo "Certificate issued successfully."
    echo "  fullchain : /etc/letsencrypt/live/${IP}/fullchain.pem"
    echo "  privkey   : /etc/letsencrypt/live/${IP}/privkey.pem"
    echo "  expires   : $(openssl x509 -in "/etc/letsencrypt/live/${IP}/fullchain.pem" -noout -enddate | cut -d= -f2)"
}

# Alternative: standalone mode (stops port 80 listener temporarily)
issue_cert_standalone() {
    echo "Using standalone mode (temporarily binds port 80)"
    echo "WARNING: This will fail if anything is already listening on port 80."
    echo ""

    "$CERTBOT_BIN" certonly \
        --standalone \
        --preferred-challenges http \
        --ip-address "${IP}" \
        --preferred-profile shortlived \
        --agree-tos \
        --email "${EMAIL}" \
        --no-eff-email \
        --non-interactive
}

# Alternative: lego client (Go-based, single binary)
issue_cert_lego() {
    local LEGO_BIN="${LEGO_BIN:-lego}"
    echo "Using lego client for IP: ${IP}"
    # lego treats the IP like a domain identifier
    "$LEGO_BIN" \
        --email "${EMAIL}" \
        --domains "${IP}" \
        --http \
        --http.webroot "${WEBROOT}" \
        --path /etc/lego \
        --profile shortlived \
        --server https://acme-v02.api.letsencrypt.org/directory \
        --accept-tos \
        run
}

main() {
    echo "=== Let's Encrypt IP Address Certificate ==="
    check_certbot_version
    ensure_webroot

    # Use standalone if webroot mode is explicitly overridden
    if [[ "${USE_STANDALONE:-false}" == "true" ]]; then
        issue_cert_standalone
    else
        issue_cert
    fi
}

main "$@"
