#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 18, Real-Time Clock Module Implementation.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# Generate mTLS Certificates for HSM API Gateway
# Creates CA, server certificate, and client certificates for mutual TLS authentication.
#
# This script supports the API Gateway (api_gateway.py) by generating the full
# certificate chain needed for mTLS between game servers and the HSM gateway.
#
# Environment Variables:
#   CERT_DIR        - Output directory (default: /etc/ssl/api_gateway)
#   CA_CN           - CA common name (default: "HSM API Gateway CA")
#   SERVER_CN       - Server common name (default: "api-gateway.local")
#   CLIENT_CNS      - Space-separated client CN list (default: "client1 client2")
#   CERT_VALIDITY   - Certificate validity in days (default: 365)
#   KEY_SIZE        - RSA key size (default: 2048)

set -e

# Configuration
CERT_DIR=${CERT_DIR:-/etc/ssl/api_gateway}
CA_CN=${CA_CN:-"HSM API Gateway CA"}
SERVER_CN=${SERVER_CN:-"api-gateway.local"}
CLIENT_CNS=${CLIENT_CNS:-"client1 client2"}
CERT_VALIDITY=${CERT_VALIDITY:-365}
KEY_SIZE=${KEY_SIZE:-2048}

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_dependencies() {
    log_info "Checking dependencies..."
    local deps=("openssl" "mkdir" "chmod")
    for dep in "${deps[@]}"; do
        if ! command -v "$dep" &> /dev/null; then
            log_error "$dep is not installed"
            exit 1
        fi
    done
    log_info "All dependencies available"
}

create_cert_directory() {
    log_info "Creating certificate directory: $CERT_DIR"
    mkdir -p "$CERT_DIR"
    chmod 700 "$CERT_DIR"
}

generate_ca() {
    log_info "Generating Certificate Authority (CA)..."

    openssl genrsa -out "$CERT_DIR/ca.key" "$KEY_SIZE"

    openssl req -new -x509 -days "$CERT_VALIDITY" -key "$CERT_DIR/ca.key" \
    -sha256 -extensions v3_ca -subj "/C=US/ST=State/L=City/O=Organization/CN=$CA_CN" \
    -out "$CERT_DIR/ca.crt"

    chmod 600 "$CERT_DIR/ca.key"
    chmod 644 "$CERT_DIR/ca.crt"

    log_info "CA certificate created: $CERT_DIR/ca.crt"
}

generate_server_cert() {
    log_info "Generating server certificate for $SERVER_CN..."

    openssl genrsa -out "$CERT_DIR/server.key" "$KEY_SIZE"

    openssl req -new -key "$CERT_DIR/server.key" \
    -subj "/C=US/ST=State/L=City/O=Organization/CN=$SERVER_CN" \
    -out "$CERT_DIR/server.csr"

    cat > "$CERT_DIR/server.ext" << EOF
authorityKeyIdentifier=keyid,issuer
basicConstraints=CA:FALSE
keyUsage = digitalSignature, nonRepudiation, keyEncipherment, dataEncipherment
extendedKeyUsage = serverAuth
subjectAltName = @alt_names

[alt_names]
DNS.1 = $SERVER_CN
DNS.2 = localhost
IP.1 = 127.0.0.1
EOF

    openssl x509 -req -days "$CERT_VALIDITY" -in "$CERT_DIR/server.csr" \
    -CA "$CERT_DIR/ca.crt" -CAkey "$CERT_DIR/ca.key" -CAcreateserial \
    -sha256 -extfile "$CERT_DIR/server.ext" \
    -out "$CERT_DIR/server.crt"

    chmod 600 "$CERT_DIR/server.key"
    chmod 644 "$CERT_DIR/server.crt"

    log_info "Server certificate created: $CERT_DIR/server.crt"
}

generate_client_cert() {
    local client_cn=$1
    log_info "Generating client certificate for $client_cn..."

    openssl genrsa -out "$CERT_DIR/${client_cn}.key" "$KEY_SIZE"

    openssl req -new -key "$CERT_DIR/${client_cn}.key" \
    -subj "/C=US/ST=State/L=City/O=Organization/CN=$client_cn" \
    -out "$CERT_DIR/${client_cn}.csr"

    cat > "$CERT_DIR/${client_cn}.ext" << EOF
authorityKeyIdentifier=keyid,issuer
basicConstraints=CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = clientAuth
subjectAltName = email:${client_cn}@example.com
EOF

    openssl x509 -req -days "$CERT_VALIDITY" -in "$CERT_DIR/${client_cn}.csr" \
    -CA "$CERT_DIR/ca.crt" -CAkey "$CERT_DIR/ca.key" -CAcreateserial \
    -sha256 -extfile "$CERT_DIR/${client_cn}.ext" \
    -out "$CERT_DIR/${client_cn}.crt"

    # Create PKCS#12 bundle for client
    openssl pkcs12 -export -out "$CERT_DIR/${client_cn}.p12" \
    -inkey "$CERT_DIR/${client_cn}.key" \
    -in "$CERT_DIR/${client_cn}.crt" \
    -certfile "$CERT_DIR/ca.crt" \
    -passout pass:"${client_cn}123"

    chmod 600 "$CERT_DIR/${client_cn}.key"
    chmod 644 "$CERT_DIR/${client_cn}.crt"
    chmod 600 "$CERT_DIR/${client_cn}.p12"

    log_info "Client certificate created: $CERT_DIR/${client_cn}.crt"
    log_info "Client PKCS#12 bundle created: $CERT_DIR/${client_cn}.p12"
}

verify_certificates() {
    log_info "Verifying generated certificates..."

    openssl x509 -in "$CERT_DIR/ca.crt" -text -noout | head -5
    openssl verify -CAfile "$CERT_DIR/ca.crt" "$CERT_DIR/server.crt"

    for client_cn in $CLIENT_CNS; do
        openssl verify -CAfile "$CERT_DIR/ca.crt" "$CERT_DIR/${client_cn}.crt"
    done

    log_info "All certificates verified successfully"
}

print_summary() {
    echo ""
    echo "=============================================="
    log_info "mTLS Certificate Generation Complete!"
    echo "=============================================="
    echo ""
    echo "Certificate Directory: $CERT_DIR"
    echo ""
    echo "Generated Files:"
    echo "  CA Certificate:     ca.crt"
    echo "  Server Certificate: server.crt"
    echo "  Server Private Key: server.key"
    echo ""

    for client_cn in $CLIENT_CNS; do
        echo "  Client $client_cn:"
        echo "    Certificate:    ${client_cn}.crt"
        echo "    Private Key:    ${client_cn}.key"
        echo "    PKCS#12 Bundle: ${client_cn}.p12"
    done

    echo ""
    echo "Next Steps:"
    echo "1. Configure API Gateway: export SSL_CERT_FILE=$CERT_DIR/server.crt"
    echo "2. Start API Gateway: python3 api_gateway.py"
    echo "3. Distribute client certificates to authorized game servers"
    echo ""
    echo "Security Notes:"
    echo "- Store private keys securely (HSM recommended)"
    echo "- Rotate certificates before expiry ($CERT_VALIDITY days)"
    echo "- Monitor certificate expiry dates"
    echo ""
}

main() {
    echo "=============================================="
    echo "  HSM API Gateway mTLS Certificate Generation"
    echo "=============================================="
    echo ""

    check_dependencies
    create_cert_directory
    generate_ca
    generate_server_cert

    for client_cn in $CLIENT_CNS; do
        generate_client_cert "$client_cn"
    done

    verify_certificates
    print_summary
}

main "$@"
