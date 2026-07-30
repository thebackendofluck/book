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

# Generate mTLS Certificates for API Gateway
# Creates CA, server certificate, and client certificates

set -e

# Configuration
CERT_DIR=${CERT_DIR:-/etc/ssl/api_gateway}
CA_CN=${CA_CN:-"YubiHSM API Gateway CA"}
SERVER_CN=${SERVER_CN:-"api-gateway.local"}
CLIENT_CNS=${CLIENT_CNS:-"client1 client2"}  # Space-separated list
CERT_VALIDITY=${CERT_VALIDITY:-365}
KEY_SIZE=${KEY_SIZE:-2048}

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

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
    log_info "✓ All dependencies available"
}

create_cert_directory() {
    log_info "Creating certificate directory: $CERT_DIR"
    mkdir -p "$CERT_DIR"
    chmod 700 "$CERT_DIR"
}

generate_ca() {
    log_info "Generating Certificate Authority (CA)..."
    
    # Generate CA private key
    openssl genrsa -out "$CERT_DIR/ca.key" "$KEY_SIZE"
    
    # Generate CA certificate
    openssl req -new -x509 -days "$CERT_VALIDITY" -key "$CERT_DIR/ca.key" \
    -sha256 -extensions v3_ca -subj "/C=US/ST=State/L=City/O=Organization/CN=$CA_CN" \
    -out "$CERT_DIR/ca.crt"
    
    # Set permissions
    chmod 600 "$CERT_DIR/ca.key"
    chmod 644 "$CERT_DIR/ca.crt"
    
    log_info "✓ CA certificate created: $CERT_DIR/ca.crt"
}

generate_server_cert() {
    log_info "Generating server certificate for $SERVER_CN..."
    
    # Generate server private key
    openssl genrsa -out "$CERT_DIR/server.key" "$KEY_SIZE"
    
    # Generate certificate signing request
    openssl req -new -key "$CERT_DIR/server.key" \
    -subj "/C=US/ST=State/L=City/O=Organization/CN=$SERVER_CN" \
    -out "$CERT_DIR/server.csr"
    
    # Create server certificate extensions
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
    
    # Sign server certificate
    openssl x509 -req -days "$CERT_VALIDITY" -in "$CERT_DIR/server.csr" \
    -CA "$CERT_DIR/ca.crt" -CAkey "$CERT_DIR/ca.key" -CAcreateserial \
    -sha256 -extfile "$CERT_DIR/server.ext" \
    -out "$CERT_DIR/server.crt"
    
    # Set permissions
    chmod 600 "$CERT_DIR/server.key"
    chmod 644 "$CERT_DIR/server.crt"
    
    log_info "✓ Server certificate created: $CERT_DIR/server.crt"
}

generate_client_cert() {
    local client_cn=$1
    log_info "Generating client certificate for $client_cn..."
    
    # Generate client private key
    openssl genrsa -out "$CERT_DIR/${client_cn}.key" "$KEY_SIZE"
    
    # Generate certificate signing request
    openssl req -new -key "$CERT_DIR/${client_cn}.key" \
    -subj "/C=US/ST=State/L=City/O=Organization/CN=$client_cn" \
    -out "$CERT_DIR/${client_cn}.csr"
    
    # Create client certificate extensions
    cat > "$CERT_DIR/${client_cn}.ext" << EOF
authorityKeyIdentifier=keyid,issuer
basicConstraints=CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = clientAuth
subjectAltName = email:${client_cn}@example.com
EOF
    
    # Sign client certificate
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
    
    # Set permissions
    chmod 600 "$CERT_DIR/${client_cn}.key"
    chmod 644 "$CERT_DIR/${client_cn}.crt"
    chmod 600 "$CERT_DIR/${client_cn}.p12"
    
    log_info "✓ Client certificate created: $CERT_DIR/${client_cn}.crt"
    log_info "✓ Client PKCS#12 bundle created: $CERT_DIR/${client_cn}.p12"
}

create_ca_bundle() {
    log_info "Creating CA certificate bundle..."
    
    # Combine CA and any intermediate certificates if needed
    cp "$CERT_DIR/ca.crt" "$CERT_DIR/ca-bundle.crt"
    
    log_info "✓ CA bundle created: $CERT_DIR/ca-bundle.crt"
}

verify_certificates() {
    log_info "Verifying generated certificates..."
    
    # Verify CA certificate
    openssl x509 -in "$CERT_DIR/ca.crt" -text -noout | head -5
    
    # Verify server certificate
    openssl verify -CAfile "$CERT_DIR/ca.crt" "$CERT_DIR/server.crt"
    
    # Verify client certificates
    for client_cn in $CLIENT_CNS; do
        openssl verify -CAfile "$CERT_DIR/ca.crt" "$CERT_DIR/${client_cn}.crt"
    done
    
    log_info "✓ All certificates verified successfully"
}

create_config_file() {
    log_info "Creating configuration file..."
    
    cat > "$CERT_DIR/config.env" << EOF
# API Gateway mTLS Configuration
# Generated on $(date)

# Certificate locations
SSL_CERT_FILE=$CERT_DIR/server.crt
SSL_KEY_FILE=$CERT_DIR/server.key
SSL_CA_CERTS=$CERT_DIR/ca-bundle.crt

# API settings
API_HOST=0.0.0.0
API_PORT=8443

# HSM settings
HSM_CONNECTOR_URL=http://localhost:12345
HSM_AUTH_KEY_ID=2
# Replace __SET_FROM_SECRET_MANAGER__ before sourcing this file. The placeholder
# is intentionally invalid so accidental use fails loudly instead of authenticating
# with the well-known default 'password'.
HSM_PASSWORD=__SET_FROM_SECRET_MANAGER__

# Client certificates
EOF
    
    for client_cn in $CLIENT_CNS; do
        {
            echo "CLIENT_${client_cn^^}_CERT=$CERT_DIR/${client_cn}.crt"
            echo "CLIENT_${client_cn^^}_KEY=$CERT_DIR/${client_cn}.key"
            echo "CLIENT_${client_cn^^}_P12=$CERT_DIR/${client_cn}.p12"
        } >> "$CERT_DIR/config.env"
    done
    
    log_info "✓ Configuration file created: $CERT_DIR/config.env"
}

create_test_script() {
    log_info "Creating test script..."
    
    cat > "$CERT_DIR/test_mtls.sh" << EOF
#!/bin/bash
# Test mTLS connection to API Gateway

echo "Testing mTLS connection to API Gateway..."
echo "=========================================="

# Test with first client certificate
FIRST_CLIENT=\$(echo "$CLIENT_CNS" | awk '{print \$1}')
echo "Testing with client: \$FIRST_CLIENT"

curl -v --cert "$CERT_DIR/\${FIRST_CLIENT}.crt" \
     --key "$CERT_DIR/\${FIRST_CLIENT}.key" \
     --cacert "$CERT_DIR/ca.crt" \
     https://localhost:8443/health

echo ""
echo "Testing without client certificate (should fail)..."
curl -k https://localhost:8443/health || echo "✓ Correctly rejected without client certificate"

echo ""
echo "Testing with invalid client certificate (should fail)..."
openssl req -new -newkey rsa:2048 -nodes -keyout /tmp/invalid.key -out /tmp/invalid.csr -subj "/CN=invalid-client" 2>/dev/null
openssl x509 -req -days 1 -in /tmp/invalid.csr -signkey /tmp/invalid.key -out /tmp/invalid.crt 2>/dev/null
curl --cert /tmp/invalid.crt --key /tmp/invalid.key --cacert "$CERT_DIR/ca.crt" https://localhost:8443/health 2>/dev/null || echo "✓ Correctly rejected invalid certificate"

echo ""
echo "mTLS test complete!"
EOF
    
    chmod +x "$CERT_DIR/test_mtls.sh"
    log_info "✓ Test script created: $CERT_DIR/test_mtls.sh"
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
    echo "  CA Certificate: ca.crt"
    echo "  CA Private Key: ca.key"
    echo "  Server Certificate: server.crt"
    echo "  Server Private Key: server.key"
    echo "  CA Bundle: ca-bundle.crt"
    echo "  Configuration: config.env"
    echo ""
    
    for client_cn in $CLIENT_CNS; do
        echo "  Client $client_cn:"
        echo "    Certificate: ${client_cn}.crt"
        echo "    Private Key: ${client_cn}.key"
        echo "    PKCS#12 Bundle: ${client_cn}.p12"
    done
    
    echo ""
    echo "Next Steps:"
    echo "1. Review and backup certificates: cp -r $CERT_DIR /secure/backup/"
    echo "2. Configure API Gateway: source $CERT_DIR/config.env"
    echo "3. Start API Gateway: python3 api_gateway.py"
    echo "4. Test mTLS: $CERT_DIR/test_mtls.sh"
    echo "5. Distribute client certificates to authorized users"
    echo ""
    echo "Security Notes:"
    echo "- Store private keys securely (HSM recommended)"
    echo "- Rotate certificates regularly"
    echo "- Monitor certificate expiry dates"
    echo "- Use strong passphrases for PKCS#12 bundles"
    echo ""
}

main() {
    echo "=============================================="
    echo "  API Gateway mTLS Certificate Generation"
    echo "=============================================="
    echo ""
    
    check_dependencies
    create_cert_directory
    generate_ca
    generate_server_cert
    
    for client_cn in $CLIENT_CNS; do
        generate_client_cert "$client_cn"
    done
    
    create_ca_bundle
    verify_certificates
    create_config_file
    create_test_script
    print_summary
}

# Run main function
main "$@"