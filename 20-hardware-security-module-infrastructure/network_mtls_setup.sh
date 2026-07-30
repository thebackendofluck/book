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

# shellcheck disable=SC2034  # Config and color constants
# network_mtls_setup.sh - Complete mTLS implementation for network security
# Provides mutual TLS authentication between multiple networks using YubiHSM 2

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="${SCRIPT_DIR}/config/mtls"
CERTS_DIR="${SCRIPT_DIR}/certs/mtls"
LOGS_DIR="${SCRIPT_DIR}/logs/mtls"

# YubiHSM Configuration
YUBIHSM_CONNECTOR="http://localhost:12345"
AUTH_KEY_ID=2
CA_KEY_ID=1000
INTERMEDIATE_KEY_ID=1100

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() {
    echo -e "${GREEN}[INFO]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "${LOGS_DIR}/mtls_setup.log"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "${LOGS_DIR}/mtls_setup.log"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "${LOGS_DIR}/mtls_setup.log"
}

# Prerequisites check
check_prerequisites() {
    log_info "Checking prerequisites..."
    
    # Check required tools
    local required_tools=("openssl" "python3" "curl" "docker" "docker-compose" "jq")
    for tool in "${required_tools[@]}"; do
        if ! command -v "$tool" &> /dev/null; then
            log_error "$tool is not required but not installed"
            exit 1
        fi
    done
    
    # Check YubiHSM connectivity
    if ! curl -s "${YUBIHSM_CONNECTOR}/connector/status" > /dev/null; then
        log_error "YubiHSM connector not accessible at ${YUBIHSM_CONNECTOR}"
        exit 1
    fi
    
    # Check Python yubihsm library
    if ! python3 -c "import yubihsm" 2>/dev/null; then
        log_error "yubihsm Python library not installed"
        exit 1
    fi
    
    log_info "Prerequisites check completed"
}

# Generate CA certificate in YubiHSM
generate_ca_certificate() {
    log_info "Generating root CA certificate in YubiHSM..."
    
    python3 << EOF
from yubihsm import YubiHsm
from yubihsm.defs import ALGORITHM, CAPABILITY, OBJECT
from yubihsm.objects import AsymmetricKey
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import datetime

# Connect to HSM
hsm = YubiHsm.connect('${YUBIHSM_CONNECTOR}')
session = hsm.create_session_derived(${AUTH_KEY_ID}, 'password')

# Generate RSA key pair for CA
ca_key = AsymmetricKey.generate(
    session=session,
    object_id=${CA_KEY_ID},
    label='mTLS-Root-CA',
    domains=1,
    capabilities=CAPABILITY.SIGN_PKCS | CAPABILITY.SIGN_PSS | CAPABILITY.DECRYPT_PKCS,
    algorithm=ALGORITHM.RSA_4096
)

# Create CA certificate
subject = issuer = x509.Name([
    x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
    x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "California"),
    x509.NameAttribute(NameOID.LOCALITY_NAME, "San Francisco"),
    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Network Security Inc"),
    x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "Certificate Authority"),
    x509.NameAttribute(NameOID.COMMON_NAME, "Network Security Root CA"),
])

cert = x509.CertificateBuilder().subject_name(
    subject
).issuer_name(
    issuer
).public_key(
    ca_key.public_key
).serial_number(
    x509.random_serial_number()
).not_valid_before(
    datetime.datetime.utcnow()
).not_valid_after(
    datetime.datetime.utcnow() + datetime.timedelta(days=3650)
).add_extension(
    x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key),
    critical=False,
).add_extension(
    x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key),
    critical=False,
).add_extension(
    x509.BasicConstraints(ca=True, path_length=1),
    critical=True,
).add_extension(
    x509.KeyUsage(
        digital_signature=True,
        content_commitment=False,
        key_encipherment=False,
        data_encipherment=False,
        key_agreement=False,
        key_cert_sign=True,
        crl_sign=True,
        encipher_only=False,
        decipher_only=False
    ),
    critical=True,
).sign(ca_key, hashes.SHA256())

# Save certificate
with open('${CERTS_DIR}/root-ca.crt', 'wb') as f:
    f.write(cert.public_bytes(serialization.Encoding.PEM))

print("Root CA certificate generated and saved")
session.close()
EOF
    
    log_info "Root CA certificate generated successfully"
}

# Generate intermediate CA
generate_intermediate_ca() {
    log_info "Generating intermediate CA certificate..."
    
    # Generate intermediate key pair
    openssl genrsa -out "${CERTS_DIR}/intermediate-ca.key" 4096
    
    # Create certificate signing request
    cat > "${CONFIG_DIR}/intermediate-ca.conf" << EOF
[req]
distinguished_name = req_distinguished_name
req_extensions = v3_intermediate_ca
prompt = no

[req_distinguished_name]
C = US
ST = California
L = San Francisco
O = Network Security Inc
OU = Intermediate Certificate Authority
CN = Network Security Intermediate CA

[v3_intermediate_ca]
basicConstraints = critical,CA:TRUE,pathlen:0
keyUsage = critical,keyCertSign,cRLSign
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid:always,issuer:always
EOF
    
    openssl req -new -key "${CERTS_DIR}/intermediate-ca.key" \
    -out "${CERTS_DIR}/intermediate-ca.csr" \
    -config "${CONFIG_DIR}/intermediate-ca.conf"
    
    # Sign intermediate certificate with root CA
    python3 << EOF
from yubihsm import YubiHsm
from yubihsm.defs import OBJECT
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
import datetime

# Load intermediate CSR
with open('${CERTS_DIR}/intermediate-ca.csr', 'rb') as f:
    csr = x509.load_pem_x509_csr(f.read())

# Connect to HSM and sign
hsm = YubiHsm.connect('${YUBIHSM_CONNECTOR}')
session = hsm.create_session_derived(${AUTH_KEY_ID}, 'password')

ca_key = session.get_object(${CA_KEY_ID}, OBJECT.ASYMMETRIC_KEY)
ca_cert_data = open('${CERTS_DIR}/root-ca.crt', 'rb').read()
ca_cert = x509.load_pem_x509_certificate(ca_cert_data)

# Create intermediate certificate
cert = x509.CertificateBuilder().subject_name(
    csr.subject
).issuer_name(
    ca_cert.subject
).public_key(
    csr.public_key()
).serial_number(
    x509.random_serial_number()
).not_valid_before(
    datetime.datetime.utcnow()
).not_valid_after(
    datetime.datetime.utcnow() + datetime.timedelta(days=1825)
).add_extension(
    x509.SubjectKeyIdentifier.from_public_key(csr.public_key()),
    critical=False,
).add_extension(
    x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key),
    critical=False,
).add_extension(
    x509.BasicConstraints(ca=True, path_length=0),
    critical=True,
).add_extension(
    x509.KeyUsage(
        digital_signature=False,
        content_commitment=False,
        key_encipherment=False,
        data_encipherment=False,
        key_agreement=False,
        key_cert_sign=True,
        crl_sign=True,
        encipher_only=False,
        decipher_only=False
    ),
    critical=True,
).sign(ca_key, hashes.SHA256())

# Save certificate
with open('${CERTS_DIR}/intermediate-ca.crt', 'wb') as f:
    f.write(cert.public_bytes(serialization.Encoding.PEM))

print("Intermediate CA certificate generated and saved")
session.close()
EOF
    
    log_info "Intermediate CA certificate generated successfully"
}

# Generate server certificate for network
generate_server_certificate() {
    local network_name="$1"
    local domain="$2"
    
    log_info "Generating server certificate for network: $network_name ($domain)"
    
    # Generate private key
    openssl genrsa -out "${CERTS_DIR}/server-${network_name}.key" 2048
    
    # Create certificate signing request
    cat > "${CONFIG_DIR}/server-${network_name}.conf" << EOF
[req]
distinguished_name = req_distinguished_name
req_extensions = v3_server
prompt = no

[req_distinguished_name]
C = US
ST = California
L = San Francisco
O = Network Security Inc
OU = Network Gateway
CN = $domain

[v3_server]
basicConstraints = CA:FALSE
keyUsage = nonRepudiation,digitalSignature,keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = @alt_names

[alt_names]
DNS.1 = $domain
DNS.2 = *.${domain}
IP.1 = 10.0.${network_name#network-}.1
EOF
    
    openssl req -new -key "${CERTS_DIR}/server-${network_name}.key" \
    -out "${CERTS_DIR}/server-${network_name}.csr" \
    -config "${CONFIG_DIR}/server-${network_name}.conf"
    
    # Sign server certificate
    openssl x509 -req -days 365 \
    -in "${CERTS_DIR}/server-${network_name}.csr" \
    -CA "${CERTS_DIR}/intermediate-ca.crt" \
    -CAkey "${CERTS_DIR}/intermediate-ca.key" \
    -CAcreateserial \
    -out "${CERTS_DIR}/server-${network_name}.crt" \
    -extfile "${CONFIG_DIR}/server-${network_name}.conf" \
    -extensions v3_server
    
    log_info "Server certificate generated for $network_name"
}

# Generate client certificate for network
generate_client_certificate() {
    local network_name="$1"
    local client_name="$2"
    
    log_info "Generating client certificate for $client_name in $network_name"
    
    # Generate private key
    openssl genrsa -out "${CERTS_DIR}/client-${client_name}.key" 2048
    
    # Create certificate signing request
    cat > "${CONFIG_DIR}/client-${client_name}.conf" << EOF
[req]
distinguished_name = req_distinguished_name
req_extensions = v3_client
prompt = no

[req_distinguished_name]
C = US
ST = California
L = San Francisco
O = Network Security Inc
OU = Network Client
CN = $client_name

[v3_client]
basicConstraints = CA:FALSE
keyUsage = nonRepudiation,digitalSignature,keyEncipherment
extendedKeyUsage = clientAuth
EOF
    
    openssl req -new -key "${CERTS_DIR}/client-${client_name}.key" \
    -out "${CERTS_DIR}/client-${client_name}.csr" \
    -config "${CONFIG_DIR}/client-${client_name}.conf"
    
    # Sign client certificate
    openssl x509 -req -days 365 \
    -in "${CERTS_DIR}/client-${client_name}.csr" \
    -CA "${CERTS_DIR}/intermediate-ca.crt" \
    -CAkey "${CERTS_DIR}/intermediate-ca.key" \
    -CAcreateserial \
    -out "${CERTS_DIR}/client-${client_name}.crt" \
    -extfile "${CONFIG_DIR}/client-${client_name}.conf" \
    -extensions v3_client
    
    # Create PKCS#12 bundle for client
    openssl pkcs12 -export \
    -in "${CERTS_DIR}/client-${client_name}.crt" \
    -inkey "${CERTS_DIR}/client-${client_name}.key" \
    -certfile "${CERTS_DIR}/intermediate-ca.crt" \
    -out "${CERTS_DIR}/client-${client_name}.p12" \
    -passout pass:""
    
    log_info "Client certificate generated for $client_name"
}

# Create Docker Compose configuration
create_docker_compose() {
    local network_name="$1"
    local network_id="${network_name#network-}"
    
    log_info "Creating Docker Compose configuration for $network_name"
    
    cat > "${CONFIG_DIR}/docker-compose-${network_name}.yml" << EOF
version: '3.8'

services:
  mtls-gateway-${network_name}:
    image: nginx:alpine
    container_name: mtls-gateway-${network_name}
    ports:
      - "8443:443"
    volumes:
      - ${CERTS_DIR}/server-${network_name}.crt:/etc/ssl/certs/server.crt:ro
      - ${CERTS_DIR}/server-${network_name}.key:/etc/ssl/private/server.key:ro
      - ${CERTS_DIR}/root-ca.crt:/etc/ssl/certs/ca.crt:ro
      - ./nginx-${network_name}.conf:/etc/nginx/nginx.conf:ro
    networks:
      - ${network_name}
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "https://localhost/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  mtls-monitor-${network_name}:
    image: prom/prometheus:latest
    container_name: mtls-monitor-${network_name}
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus-${network_name}.yml:/etc/prometheus/prometheus.yml:ro
      - prometheus_data:/prometheus
    networks:
      - ${network_name}
    restart: unless-stopped

networks:
  ${network_name}:
    driver: bridge
    ipam:
      config:
        - subnet: 172.${network_id}.0.0/16

volumes:
  prometheus_data:
EOF
    
    # Create Nginx configuration
    cat > "${CONFIG_DIR}/nginx-${network_name}.conf" << EOF
events {
    worker_connections 1024;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;

    # Logging
    log_format main '\$remote_addr - \$remote_user [\$time_local] "\$request" '
                    '\$status \$body_bytes_sent "\$http_referer" '
                    '"\$http_user_agent" "\$http_x_forwarded_for"';

    access_log /var/log/nginx/access.log main;
    error_log /var/log/nginx/error.log;

    # SSL/TLS configuration
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-RSA-AES256-GCM-SHA512:DHE-RSA-AES256-GCM-SHA512:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;

    # Client certificate verification
    ssl_verify_client on;
    ssl_verify_depth 2;
    ssl_client_certificate /etc/ssl/certs/ca.crt;

    server {
        listen 443 ssl http2;
        server_name ${network_name}.internal;

        ssl_certificate /etc/ssl/certs/server.crt;
        ssl_certificate_key /etc/ssl/private/server.key;

        # Security headers
        add_header X-Frame-Options DENY;
        add_header X-Content-Type-Options nosniff;
        add_header X-XSS-Protection "1; mode=block";
        add_header Strict-Transport-Security "max-age=31536000; includeSubDomains";

        # Health check endpoint
        location /health {
            access_log off;
            return 200 "healthy\n";
            add_header Content-Type text/plain;
        }

        # Main application (placeholder)
        location / {
            proxy_pass http://app:8080;
            proxy_set_header Host \$host;
            proxy_set_header X-Real-IP \$remote_addr;
            proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto \$scheme;
            proxy_set_header X-SSL-Client-Cert \$ssl_client_cert;
        }
    }
}
EOF
    
    log_info "Docker Compose configuration created for $network_name"
}

# Main setup function
setup_mtls_infrastructure() {
    log_info "Starting mTLS infrastructure setup..."
    
    # Create directory structure
    mkdir -p "${CONFIG_DIR}" "${CERTS_DIR}" "${LOGS_DIR}"
    
    # Generate PKI
    generate_ca_certificate
    generate_intermediate_ca
    
    # Generate certificates for 5 networks
    local networks=("network-1" "network-2" "network-3" "network-4" "network-5")
    for network in "${networks[@]}"; do
        generate_server_certificate "$network" "${network}.internal"
        generate_client_certificate "$network" "client-${network}"
        create_docker_compose "$network"
    done
    
    log_info "mTLS infrastructure setup completed"
}

# Interactive menu
show_menu() {
    echo
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║              mTLS Network Security Setup                    ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo
    echo "Available Operations:"
    echo "  1. Complete Infrastructure Setup"
    echo "  2. Generate CA Certificates"
    echo "  3. Add New Network"
    echo "  4. Generate Client Certificate"
    echo "  5. Deploy to Environment"
    echo "  6. View Network Topology"
    echo "  7. Test Connectivity"
    echo "  8. Rotate Certificates"
    echo "  9. Backup Configuration"
    echo " 10. View Logs and Status"
    echo " 11. Troubleshooting Tools"
    echo "  0. Exit"
    echo
    echo -n "Select option: "
}

# Main execution
main() {
    # Check prerequisites
    check_prerequisites
    
    # Parse command line arguments
    case "${1:-}" in
        "setup")
            setup_mtls_infrastructure
        ;;
        "ca")
            generate_ca_certificate
            generate_intermediate_ca
        ;;
        "network")
            if [ $# -lt 3 ]; then
                log_error "Usage: $0 network <network_name> <domain>"
                exit 1
            fi
            generate_server_certificate "$2" "$3"
            generate_client_certificate "$2" "client-$2"
            create_docker_compose "$2"
        ;;
        "client")
            if [ $# -lt 3 ]; then
                log_error "Usage: $0 client <network_name> <client_name>"
                exit 1
            fi
            generate_client_certificate "$2" "$3"
        ;;
        "deploy")
            if [ $# -lt 2 ]; then
                log_error "Usage: $0 deploy <environment> [network_name]"
                exit 1
            fi
            deploy_to_environment "$2" "${3:-}"
        ;;
        "interactive")
            while true; do
                show_menu
                read -r choice
                case $choice in
                    1)
                        setup_mtls_infrastructure
                    ;;
                    2)
                        generate_ca_certificate
                        generate_intermediate_ca
                    ;;
                    3)
                        echo -n "Enter network name: "
                        read -r network_name
                        echo -n "Enter domain: "
                        read -r domain
                        generate_server_certificate "$network_name" "$domain"
                        generate_client_certificate "$network_name" "client-$network_name"
                        create_docker_compose "$network_name"
                    ;;
                    4)
                        echo -n "Enter network name: "
                        read -r network_name
                        echo -n "Enter client name: "
                        read -r client_name
                        generate_client_certificate "$network_name" "$client_name"
                    ;;
                    5)
                        echo -n "Enter environment (dev/staging/prod): "
                        read -r env
                        echo -n "Enter network name (optional): "
                        read -r network
                        deploy_to_environment "$env" "$network"
                    ;;
                    6)
                        view_network_topology
                    ;;
                    7)
                        test_connectivity
                    ;;
                    8)
                        rotate_certificates
                    ;;
                    9)
                        backup_configuration
                    ;;
                    10)
                        view_logs_and_status
                    ;;
                    11)
                        troubleshooting_tools
                    ;;
                    0)
                        log_info "Exiting mTLS setup"
                        exit 0
                    ;;
                    *)
                        log_error "Invalid option"
                    ;;
                esac
                echo
                echo "Press Enter to continue..."
                read -r
            done
        ;;
        *)
            echo "Usage: $0 {setup|ca|network|client|deploy|interactive}"
            echo
            echo "Examples:"
            echo "  $0 setup                    # Complete setup"
            echo "  $0 ca                       # Generate CA certificates"
            echo "  $0 network network-1 example.com  # Add network"
            echo "  $0 client network-1 client1 # Generate client cert"
            echo "  $0 deploy prod network-1    # Deploy to production"
            echo "  $0 interactive              # Interactive menu"
            exit 1
        ;;
    esac
}

main "$@"