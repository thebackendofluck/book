#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# shellcheck disable=SC2034
# mTLS Network Security Setup
# Complete mutual TLS implementation for multi-network gaming infrastructure
# Generates PKI hierarchy and per-network certificates with HSM integration
#
# Usage: ./network_setup.sh {setup|ca|network|client|interactive}

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="${SCRIPT_DIR}/config/mtls"
CERTS_DIR="${SCRIPT_DIR}/certs/mtls"
LOGS_DIR="${SCRIPT_DIR}/logs/mtls"

# HSM Configuration
HSM_CONNECTOR="${HSM_CONNECTOR:-http://localhost:12345}"
AUTH_KEY_ID=2
CA_KEY_ID=1000

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "${LOGS_DIR}/mtls_setup.log"; }
log_error() { echo -e "${RED}[ERROR]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "${LOGS_DIR}/mtls_setup.log"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "${LOGS_DIR}/mtls_setup.log"; }

check_prerequisites() {
    log_info "Checking prerequisites..."

    local required_tools=("openssl" "python3" "curl" "docker" "docker-compose" "jq")
    for tool in "${required_tools[@]}"; do
        if ! command -v "$tool" &> /dev/null; then
            log_error "$tool is required but not installed"
            exit 1
        fi
    done

    if ! curl -s "${HSM_CONNECTOR}/connector/status" > /dev/null; then
        log_error "HSM connector not accessible at ${HSM_CONNECTOR}"
        exit 1
    fi

    log_info "Prerequisites check completed"
}

# Generate root CA certificate using HSM-stored key
generate_ca_certificate() {
    log_info "Generating root CA certificate in HSM..."

    python3 << EOF
from yubihsm import YubiHsm
from yubihsm.defs import ALGORITHM, CAPABILITY
from yubihsm.objects import AsymmetricKey
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
import datetime

hsm = YubiHsm.connect('${HSM_CONNECTOR}')
session = hsm.create_session_derived(${AUTH_KEY_ID}, 'password')

ca_key = AsymmetricKey.generate(
    session=session,
    object_id=${CA_KEY_ID},
    label='mTLS-Root-CA',
    domains=1,
    capabilities=CAPABILITY.SIGN_PKCS | CAPABILITY.SIGN_PSS | CAPABILITY.DECRYPT_PKCS,
    algorithm=ALGORITHM.RSA_4096
)

subject = issuer = x509.Name([
    x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
    x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "State"),
    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Gaming Platform"),
    x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "Certificate Authority"),
    x509.NameAttribute(NameOID.COMMON_NAME, "Gaming Platform Root CA"),
])

cert = x509.CertificateBuilder().subject_name(
    subject
).issuer_name(issuer).public_key(
    ca_key.public_key
).serial_number(
    x509.random_serial_number()
).not_valid_before(
    datetime.datetime.utcnow()
).not_valid_after(
    datetime.datetime.utcnow() + datetime.timedelta(days=3650)
).add_extension(
    x509.BasicConstraints(ca=True, path_length=1), critical=True,
).add_extension(
    x509.KeyUsage(
        digital_signature=True, content_commitment=False, key_encipherment=False,
        data_encipherment=False, key_agreement=False, key_cert_sign=True,
        crl_sign=True, encipher_only=False, decipher_only=False
    ), critical=True,
).sign(ca_key, hashes.SHA256())

with open('${CERTS_DIR}/root-ca.crt', 'wb') as f:
    f.write(cert.public_bytes(serialization.Encoding.PEM))

print("Root CA certificate generated and saved")
session.close()
EOF

    log_info "Root CA certificate generated successfully"
}

# Generate intermediate CA for certificate hierarchy
generate_intermediate_ca() {
    log_info "Generating intermediate CA certificate..."

    openssl genrsa -out "${CERTS_DIR}/intermediate-ca.key" 4096

    cat > "${CONFIG_DIR}/intermediate-ca.conf" << EOF
[req]
distinguished_name = req_distinguished_name
req_extensions = v3_intermediate_ca
prompt = no

[req_distinguished_name]
C = US
ST = State
O = Gaming Platform
OU = Intermediate Certificate Authority
CN = Gaming Platform Intermediate CA

[v3_intermediate_ca]
basicConstraints = critical,CA:TRUE,pathlen:0
keyUsage = critical,keyCertSign,cRLSign
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid:always,issuer:always
EOF

    openssl req -new -key "${CERTS_DIR}/intermediate-ca.key" \
    -out "${CERTS_DIR}/intermediate-ca.csr" \
    -config "${CONFIG_DIR}/intermediate-ca.conf"

    # Sign with root CA (HSM-backed)
    python3 << EOF
from yubihsm import YubiHsm
from yubihsm.defs import OBJECT
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
import datetime

with open('${CERTS_DIR}/intermediate-ca.csr', 'rb') as f:
    csr = x509.load_pem_x509_csr(f.read())

hsm = YubiHsm.connect('${HSM_CONNECTOR}')
session = hsm.create_session_derived(${AUTH_KEY_ID}, 'password')

ca_key = session.get_object(${CA_KEY_ID}, OBJECT.ASYMMETRIC_KEY)
ca_cert = x509.load_pem_x509_certificate(open('${CERTS_DIR}/root-ca.crt', 'rb').read())

cert = x509.CertificateBuilder().subject_name(
    csr.subject
).issuer_name(ca_cert.subject).public_key(
    csr.public_key()
).serial_number(
    x509.random_serial_number()
).not_valid_before(
    datetime.datetime.utcnow()
).not_valid_after(
    datetime.datetime.utcnow() + datetime.timedelta(days=1825)
).add_extension(
    x509.BasicConstraints(ca=True, path_length=0), critical=True,
).add_extension(
    x509.KeyUsage(
        digital_signature=False, content_commitment=False, key_encipherment=False,
        data_encipherment=False, key_agreement=False, key_cert_sign=True,
        crl_sign=True, encipher_only=False, decipher_only=False
    ), critical=True,
).sign(ca_key, hashes.SHA256())

with open('${CERTS_DIR}/intermediate-ca.crt', 'wb') as f:
    f.write(cert.public_bytes(serialization.Encoding.PEM))

print("Intermediate CA certificate generated and saved")
session.close()
EOF

    log_info "Intermediate CA certificate generated successfully"
}

# Generate per-network server certificate
generate_server_certificate() {
    local network_name="$1"
    local domain="$2"

    log_info "Generating server certificate for network: $network_name ($domain)"

    openssl genrsa -out "${CERTS_DIR}/server-${network_name}.key" 2048

    cat > "${CONFIG_DIR}/server-${network_name}.conf" << EOF
[req]
distinguished_name = req_distinguished_name
req_extensions = v3_server
prompt = no

[req_distinguished_name]
C = US
ST = State
O = Gaming Platform
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
EOF

    openssl req -new -key "${CERTS_DIR}/server-${network_name}.key" \
    -out "${CERTS_DIR}/server-${network_name}.csr" \
    -config "${CONFIG_DIR}/server-${network_name}.conf"

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

# Generate per-network client certificate
generate_client_certificate() {
    local network_name="$1"
    local client_name="$2"

    log_info "Generating client certificate for $client_name in $network_name"

    openssl genrsa -out "${CERTS_DIR}/client-${client_name}.key" 2048

    cat > "${CONFIG_DIR}/client-${client_name}.conf" << EOF
[req]
distinguished_name = req_distinguished_name
req_extensions = v3_client
prompt = no

[req_distinguished_name]
C = US
ST = State
O = Gaming Platform
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

    openssl x509 -req -days 365 \
    -in "${CERTS_DIR}/client-${client_name}.csr" \
    -CA "${CERTS_DIR}/intermediate-ca.crt" \
    -CAkey "${CERTS_DIR}/intermediate-ca.key" \
    -CAcreateserial \
    -out "${CERTS_DIR}/client-${client_name}.crt" \
    -extfile "${CONFIG_DIR}/client-${client_name}.conf" \
    -extensions v3_client

    openssl pkcs12 -export \
    -in "${CERTS_DIR}/client-${client_name}.crt" \
    -inkey "${CERTS_DIR}/client-${client_name}.key" \
    -certfile "${CERTS_DIR}/intermediate-ca.crt" \
    -out "${CERTS_DIR}/client-${client_name}.p12" \
    -passout pass:""

    log_info "Client certificate generated for $client_name"
}

# Complete infrastructure setup
setup_mtls_infrastructure() {
    log_info "Starting mTLS infrastructure setup..."

    mkdir -p "${CONFIG_DIR}" "${CERTS_DIR}" "${LOGS_DIR}"

    generate_ca_certificate
    generate_intermediate_ca

    local networks=("network-1" "network-2" "network-3" "network-4" "network-5")
    for network in "${networks[@]}"; do
        generate_server_certificate "$network" "${network}.internal"
        generate_client_certificate "$network" "client-${network}"
    done

    log_info "mTLS infrastructure setup completed"
}

# Main execution
main() {
    check_prerequisites

    case "${1:-}" in
        "setup") setup_mtls_infrastructure ;;
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
        ;;
        "client")
            if [ $# -lt 3 ]; then
                log_error "Usage: $0 client <network_name> <client_name>"
                exit 1
            fi
            generate_client_certificate "$2" "$3"
        ;;
        *)
            echo "Usage: $0 {setup|ca|network|client}"
            echo ""
            echo "Examples:"
            echo "  $0 setup                          # Complete PKI setup"
            echo "  $0 ca                             # Generate CA certificates only"
            echo "  $0 network network-1 example.com  # Add network certificates"
            echo "  $0 client network-1 payment-svc   # Generate client cert"
            exit 1
        ;;
    esac
}

main "$@"
