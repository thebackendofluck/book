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

# mTLS Configuration for Vaultwarden with YubiHSM 2
# Implements mutual TLS authentication with certificates stored in YubiHSM
# shellcheck disable=SC2034  # Config and color constants

set -euo pipefail

# Configuration
YUBIHSM_CONNECTOR_URL="${YUBIHSM_CONNECTOR_URL:-http://localhost:12345}"
YUBIHSM_AUTH_KEY="${YUBIHSM_AUTH_KEY:-2}"
VAULTWARDEN_DOMAIN="${VAULTWARDEN_DOMAIN:-vault.example.com}"
CERT_PATH="${CERT_PATH:-/etc/vaultwarden/certs}"
CA_PATH="${CA_PATH:-/etc/vaultwarden/ca}"
LOG_FILE="/var/log/vaultwarden-mtls.log"

# Certificate configuration
CA_VALIDITY_DAYS=3650  # 10 years for CA
CERT_VALIDITY_DAYS=365  # 1 year for certificates
KEY_SIZE=4096
CERT_COUNTRY="US"
CERT_STATE="California"
CERT_CITY="San Francisco"
CERT_ORG="Enterprise Security"

# YubiHSM key IDs for certificates
CA_KEY_ID=1000
SERVER_KEY_ID=1001
CLIENT_KEY_BASE_ID=2000

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Logging
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
    log "ERROR: $1"
    exit 1
}

success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
    log "SUCCESS: $1"
}

info() {
    echo -e "${BLUE}[INFO]${NC} $1"
    log "INFO: $1"
}

# Generate CA certificate in YubiHSM
generate_ca_in_hsm() {
    info "Generating CA certificate in YubiHSM..."
    
    python3 - <<EOF
import sys
import os
import getpass
from yubihsm import YubiHsm
from yubihsm.defs import CAPABILITY, ALGORITHM, OBJECT
from yubihsm.objects import AsymmetricKey
from cryptography import x509
from cryptography.x509.oid import NameOID, ExtensionOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.backends import default_backend
import datetime

try:
    # Connect to YubiHSM
    hsm = YubiHsm.connect("$YUBIHSM_CONNECTOR_URL")
    password = os.getenv('YUBIHSM_PASSWORD', getpass.getpass("Enter YubiHSM password: "))
    session = hsm.create_session_derived($YUBIHSM_AUTH_KEY, password)

    # Check if CA key already exists
    try:
        ca_key = session.get_object($CA_KEY_ID, OBJECT.ASYMMETRIC_KEY)
        print(f"CA key already exists with ID {$CA_KEY_ID}")
    except:
        # Generate RSA key pair for CA
        ca_key = AsymmetricKey.generate(
            session=session,
            object_id=$CA_KEY_ID,
            label="mTLS-CA-Key",
            domains=1,
            capabilities=CAPABILITY.SIGN_PKCS | CAPABILITY.SIGN_PSS |
                        CAPABILITY.SIGN_ATTESTATION_CERTIFICATE,
            algorithm=ALGORITHM.RSA_4096
        )
        print(f"Generated CA key with ID: {ca_key.id}")

    # Get public key
    public_key = ca_key.get_public_key()

    # Create CA certificate
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "$CERT_COUNTRY"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "$CERT_STATE"),
        x509.NameAttribute(NameOID.LOCALITY_NAME, "$CERT_CITY"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "$CERT_ORG"),
        x509.NameAttribute(NameOID.COMMON_NAME, "YubiHSM mTLS CA"),
    ])

    cert = x509.CertificateBuilder().subject_name(
        subject
    ).issuer_name(
        issuer
    ).public_key(
        public_key
    ).serial_number(
        x509.random_serial_number()
    ).not_valid_before(
        datetime.datetime.utcnow()
    ).not_valid_after(
        datetime.datetime.utcnow() + datetime.timedelta(days=$CA_VALIDITY_DAYS)
    ).add_extension(
        x509.BasicConstraints(ca=True, path_length=None),
        critical=True,
    ).add_extension(
        x509.KeyUsage(
            digital_signature=True,
            key_cert_sign=True,
            crl_sign=True,
            key_encipherment=False,
            content_commitment=False,
            data_encipherment=False,
            key_agreement=False,
            encipher_only=False,
            decipher_only=False,
        ),
        critical=True,
    ).sign(ca_key, hashes.SHA256(), backend=default_backend())

    # Save CA certificate
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    with open("$CA_PATH/ca.crt", "wb") as f:
        f.write(cert_pem)

    print("CA certificate generated and saved")
    session.close()

except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
EOF
    
    ca_keygen_exit=$?
    if [ $ca_keygen_exit -eq 0 ]; then
        success "CA certificate generated in YubiHSM"
    else
        error "Failed to generate CA certificate"
    fi
}

# Generate server certificate for Vaultwarden
generate_server_cert() {
    info "Generating server certificate for Vaultwarden..."
    
    # Create certificate configuration
    cat > /tmp/server.conf <<EOF
[req]
distinguished_name = req_distinguished_name
req_extensions = v3_req
prompt = no

[req_distinguished_name]
C = $CERT_COUNTRY
ST = $CERT_STATE
L = $CERT_CITY
O = $CERT_ORG
CN = $VAULTWARDEN_DOMAIN

[v3_req]
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = @alt_names

[alt_names]
DNS.1 = $VAULTWARDEN_DOMAIN
DNS.2 = *.$VAULTWARDEN_DOMAIN
IP.1 = 127.0.0.1
EOF
    
    # Generate server key in YubiHSM and certificate
    python3 - <<EOF
import sys
import os
from yubihsm import YubiHsm
from yubihsm.defs import CAPABILITY, ALGORITHM, OBJECT
from yubihsm.objects import AsymmetricKey
from cryptography import x509
from cryptography.x509.oid import NameOID, ExtensionOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.backends import default_backend
import datetime
import ipaddress

try:
    # Connect to YubiHSM
    hsm = YubiHsm.connect("$YUBIHSM_CONNECTOR_URL")
    # fail-fast: set YUBIHSM_PASSWORD in environment before running this script
    password = os.environ['YUBIHSM_PASSWORD']  # raises KeyError if unset
    session = hsm.create_session_derived($YUBIHSM_AUTH_KEY, password)

    # Generate server key
    try:
        server_key = session.get_object($SERVER_KEY_ID, OBJECT.ASYMMETRIC_KEY)
        print(f"Server key already exists with ID {$SERVER_KEY_ID}")
    except:
        server_key = AsymmetricKey.generate(
            session=session,
            object_id=$SERVER_KEY_ID,
            label="vaultwarden-server",
            domains=1,
            capabilities=CAPABILITY.SIGN_PKCS | CAPABILITY.DECRYPT_PKCS,
            algorithm=ALGORITHM.RSA_4096
        )
        print(f"Generated server key with ID: {server_key.id}")

    # Get CA key
    ca_key = session.get_object($CA_KEY_ID, OBJECT.ASYMMETRIC_KEY)

    # Create server certificate
    subject = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "$CERT_COUNTRY"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "$CERT_STATE"),
        x509.NameAttribute(NameOID.LOCALITY_NAME, "$CERT_CITY"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "$CERT_ORG"),
        x509.NameAttribute(NameOID.COMMON_NAME, "$VAULTWARDEN_DOMAIN"),
    ])

    # Load CA certificate
    with open("$CA_PATH/ca.crt", "rb") as f:
        ca_cert = x509.load_pem_x509_certificate(f.read(), default_backend())

    cert = x509.CertificateBuilder().subject_name(
        subject
    ).issuer_name(
        ca_cert.issuer
    ).public_key(
        server_key.get_public_key()
    ).serial_number(
        x509.random_serial_number()
    ).not_valid_before(
        datetime.datetime.utcnow()
    ).not_valid_after(
        datetime.datetime.utcnow() + datetime.timedelta(days=$CERT_VALIDITY_DAYS)
    ).add_extension(
        x509.SubjectAlternativeName([
            x509.DNSName("$VAULTWARDEN_DOMAIN"),
            x509.DNSName("*.$VAULTWARDEN_DOMAIN"),
            x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
        ]),
        critical=False,
    ).add_extension(
        x509.KeyUsage(
            digital_signature=True,
            key_encipherment=True,
            key_cert_sign=False,
            crl_sign=False,
            content_commitment=False,
            data_encipherment=False,
            key_agreement=False,
            encipher_only=False,
            decipher_only=False,
        ),
        critical=True,
    ).add_extension(
        x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
        critical=True,
    ).sign(ca_key, hashes.SHA256(), backend=default_backend())

    # Save server certificate
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    with open("$CERT_PATH/server.crt", "wb") as f:
        f.write(cert_pem)

    # Export server private key (encrypted)
    # In production, the key should remain in HSM
    # This is for demonstration purposes
    key_pem = server_key.get_public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )

    print("Server certificate generated")
    session.close()

except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
EOF
    
    success "Server certificate generated"
}

# Generate client certificate for Terraform
generate_client_cert() {
    local client_name="${1:-terraform}"
    local client_id
    client_id=$((CLIENT_KEY_BASE_ID + $(echo "$client_name" | cksum | cut -d' ' -f1) % 1000))
    
    info "Generating client certificate for: $client_name"
    
    python3 - <<EOF
import sys
import os
from yubihsm import YubiHsm
from yubihsm.defs import CAPABILITY, ALGORITHM, OBJECT
from yubihsm.objects import AsymmetricKey
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import rsa
import datetime

try:
    # Connect to YubiHSM
    hsm = YubiHsm.connect("$YUBIHSM_CONNECTOR_URL")
    # fail-fast: set YUBIHSM_PASSWORD in environment before running this script
    password = os.environ['YUBIHSM_PASSWORD']  # raises KeyError if unset
    session = hsm.create_session_derived($YUBIHSM_AUTH_KEY, password)

    # Generate client key (stored locally for Terraform use)
    client_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=4096,
        backend=default_backend()
    )

    # Get CA key from HSM
    ca_key = session.get_object($CA_KEY_ID, OBJECT.ASYMMETRIC_KEY)

    # Create client certificate
    subject = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "$CERT_COUNTRY"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "$CERT_STATE"),
        x509.NameAttribute(NameOID.LOCALITY_NAME, "$CERT_CITY"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "$CERT_ORG"),
        x509.NameAttribute(NameOID.COMMON_NAME, "$client_name"),
    ])

    # Load CA certificate
    with open("$CA_PATH/ca.crt", "rb") as f:
        ca_cert = x509.load_pem_x509_certificate(f.read(), default_backend())

    cert = x509.CertificateBuilder().subject_name(
        subject
    ).issuer_name(
        ca_cert.issuer
    ).public_key(
        client_key.public_key()
    ).serial_number(
        x509.random_serial_number()
    ).not_valid_before(
        datetime.datetime.utcnow()
    ).not_valid_after(
        datetime.datetime.utcnow() + datetime.timedelta(days=$CERT_VALIDITY_DAYS)
    ).add_extension(
        x509.KeyUsage(
            digital_signature=True,
            key_encipherment=True,
            key_cert_sign=False,
            crl_sign=False,
            content_commitment=True,
            data_encipherment=False,
            key_agreement=False,
            encipher_only=False,
            decipher_only=False,
        ),
        critical=True,
    ).add_extension(
        x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH]),
        critical=True,
    ).add_extension(
        x509.SubjectKeyIdentifier.from_public_key(client_key.public_key()),
        critical=False,
    ).sign(ca_key, hashes.SHA256(), backend=default_backend())

    # Save client certificate
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    with open("$CERT_PATH/client-$client_name.crt", "wb") as f:
        f.write(cert_pem)

    # Save client private key (encrypted with passphrase)
    passphrase = os.getenv('CLIENT_KEY_PASSPHRASE', 'changeme').encode()
    key_pem = client_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.BestAvailableEncryption(passphrase)
    )
    with open("$CERT_PATH/client-$client_name.key", "wb") as f:
        f.write(key_pem)

    # Create PKCS12 bundle for easier import
    from cryptography.hazmat.primitives.serialization import pkcs12
    p12 = pkcs12.serialize_key_and_certificates(
        name=b"$client_name",
        key=client_key,
        cert=cert,
        cas=[ca_cert],
        encryption_algorithm=serialization.BestAvailableEncryption(passphrase)
    )
    with open("$CERT_PATH/client-$client_name.p12", "wb") as f:
        f.write(p12)

    print(f"Client certificate generated for $client_name")
    print(f"Certificate: $CERT_PATH/client-$client_name.crt")
    print(f"Private Key: $CERT_PATH/client-$client_name.key")
    print(f"PKCS12 Bundle: $CERT_PATH/client-$client_name.p12")

    session.close()

except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
EOF
    
    success "Client certificate generated for $client_name"
}

# Configure Nginx for mTLS
configure_nginx_mtls() {
    info "Configuring Nginx for mTLS..."
    
    cat > /etc/nginx/sites-available/vaultwarden-mtls <<'EOF'
# Vaultwarden with mTLS Configuration
server {
    listen 443 ssl http2;
    server_name VAULTWARDEN_DOMAIN;

    # SSL Configuration
    ssl_certificate /etc/vaultwarden/certs/server.crt;
    ssl_certificate_key /etc/vaultwarden/certs/server.key;

    # mTLS Configuration - Client Certificate Verification
    ssl_client_certificate /etc/vaultwarden/ca/ca.crt;
    ssl_verify_client on;
    ssl_verify_depth 2;

    # Strong SSL Configuration
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers 'ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384';
    ssl_prefer_server_ciphers off;
    ssl_session_timeout 1d;
    ssl_session_cache shared:MozSSL:10m;
    ssl_session_tickets off;
    ssl_stapling on;
    ssl_stapling_verify on;

    # Security Headers
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    # Client certificate information headers
    proxy_set_header X-Client-Cert $ssl_client_cert;
    proxy_set_header X-Client-DN $ssl_client_s_dn;
    proxy_set_header X-Client-Verify $ssl_client_verify;

    # Rate limiting based on client certificate
    limit_req_zone $ssl_client_s_dn zone=client_cert_limit:10m rate=10r/s;
    limit_req zone=client_cert_limit burst=20 nodelay;

    # Logging with client certificate info
    access_log /var/log/nginx/vaultwarden-mtls-access.log combined;
    error_log /var/log/nginx/vaultwarden-mtls-error.log warn;

    # Main location
    location / {
        # Additional verification based on certificate CN
        if ($ssl_client_s_dn !~ "CN=(terraform|vault|admin)") {
            return 403;
        }

        proxy_pass http://vaultwarden:80;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Pass client certificate info to backend
        proxy_set_header X-Client-Certificate $ssl_client_escaped_cert;
        proxy_set_header X-Client-Certificate-CN $ssl_client_s_dn_cn;
    }

    # Admin panel - require specific client certificate
    location /admin {
        if ($ssl_client_s_dn_cn != "admin") {
            return 403;
        }

        proxy_pass http://vaultwarden:80;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # API endpoint for Terraform/Vault
    location /api/keys {
        if ($ssl_client_s_dn_cn !~ "^(terraform|vault)$") {
            return 403;
        }

        proxy_pass http://vaultwarden:80;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Client-Certificate-CN $ssl_client_s_dn_cn;
    }
}

# Redirect HTTP to HTTPS
server {
    listen 80;
    server_name VAULTWARDEN_DOMAIN;
    return 301 https://$server_name$request_uri;
}
EOF
    
    # Replace domain placeholder
    sed -i "s/VAULTWARDEN_DOMAIN/$VAULTWARDEN_DOMAIN/g" /etc/nginx/sites-available/vaultwarden-mtls
    
    # Enable site
    ln -sf /etc/nginx/sites-available/vaultwarden-mtls /etc/nginx/sites-enabled/
    
    # Test configuration
    nginx -t
    nginx_exit=$?
    if [ $nginx_exit -eq 0 ]; then
        systemctl reload nginx
        success "Nginx configured for mTLS"
    else
        error "Nginx configuration test failed"
    fi
}

# Create HAProxy configuration for mTLS load balancing
create_haproxy_mtls() {
    info "Creating HAProxy configuration for mTLS..."
    
    cat > /etc/haproxy/haproxy-mtls.cfg <<'EOF'
global
    log /dev/log local0
    log /dev/log local1 notice
    chroot /var/lib/haproxy
    stats socket /run/haproxy/admin.sock mode 660 level admin
    stats timeout 30s
    user haproxy
    group haproxy
    daemon

    # mTLS Configuration
    ssl-default-bind-ciphers ECDHE+AESGCM:ECDHE+AES256:ECDHE+AES128
    ssl-default-bind-options no-sslv3 no-tlsv10 no-tlsv11 no-tls-tickets
    ssl-default-server-ciphers ECDHE+AESGCM:ECDHE+AES256:ECDHE+AES128
    ssl-default-server-options no-sslv3 no-tlsv10 no-tlsv11 no-tls-tickets

    # Performance tuning
    tune.ssl.default-dh-param 2048
    tune.ssl.cachesize 100000
    tune.ssl.lifetime 600

defaults
    log     global
    mode    http
    option  httplog
    option  dontlognull
    timeout connect 5000
    timeout client  50000
    timeout server  50000
    errorfile 400 /etc/haproxy/errors/400.http
    errorfile 403 /etc/haproxy/errors/403.http
    errorfile 408 /etc/haproxy/errors/408.http
    errorfile 500 /etc/haproxy/errors/500.http
    errorfile 502 /etc/haproxy/errors/502.http
    errorfile 503 /etc/haproxy/errors/503.http
    errorfile 504 /etc/haproxy/errors/504.http

frontend vaultwarden_mtls
    bind *:443 ssl crt /etc/vaultwarden/certs/server.pem ca-file /etc/vaultwarden/ca/ca.crt verify required
    mode http
    option forwardfor

    # ACL based on client certificate
    acl valid_client_cert ssl_c_used
    acl terraform_client ssl_c_s_dn(cn) -i terraform
    acl vault_client ssl_c_s_dn(cn) -i vault
    acl admin_client ssl_c_s_dn(cn) -i admin

    # Deny if no valid client certificate
    http-request deny if !valid_client_cert

    # Route based on client certificate
    use_backend vaultwarden_api if terraform_client || vault_client
    use_backend vaultwarden_admin if admin_client
    default_backend vaultwarden_web

backend vaultwarden_web
    mode http
    balance roundrobin
    option httpchk GET /alive
    server vaultwarden1 vaultwarden:80 check

backend vaultwarden_api
    mode http
    balance roundrobin
    server vaultwarden1 vaultwarden:80 check

backend vaultwarden_admin
    mode http
    balance roundrobin
    server vaultwarden1 vaultwarden:80 check

listen stats
    bind *:8404
    stats enable
    stats uri /stats
    stats refresh 30s
    stats admin if TRUE
EOF
    
    success "HAProxy mTLS configuration created"
}

# Update Terraform provider for mTLS
create_terraform_mtls_provider() {
    info "Creating Terraform provider configuration for mTLS..."
    
    cat > terraform/provider_mtls.tf <<'EOF'
# Terraform Provider Configuration with mTLS

terraform {
  required_providers {
    http = {
      source  = "hashicorp/http"
      version = "~> 3.4"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }
}

# Load client certificates
locals {
  client_cert = file("${path.module}/certs/client-terraform.crt")
  client_key  = file("${path.module}/certs/client-terraform.key")
  ca_cert     = file("${path.module}/certs/ca.crt")
}

# Data source for retrieving keys from Vaultwarden with mTLS
data "http" "vaultwarden_keys" {
  url = "${var.vaultwarden_url}/api/keys/retrieve"

  method = "POST"

  request_headers = {
    Content-Type  = "application/json"
    Authorization = "Bearer ${var.vaultwarden_api_key}"
  }

  request_body = jsonencode({
    key_type = "infrastructure"
    key_ids  = var.required_keys
  })

  # mTLS configuration
  ca_cert_pem     = local.ca_cert
  client_cert_pem = local.client_cert
  client_key_pem  = local.client_key

  # Optional: Skip server certificate verification (not recommended for production)
  insecure = false
}

# Parse response
locals {
  vaultwarden_response = jsondecode(data.http.vaultwarden_keys.response_body)
  encryption_keys      = local.vaultwarden_response.keys
}

# Vault Provider with mTLS (if using HashiCorp Vault)
provider "vault" {
  address = var.vault_address

  # mTLS authentication
  auth_login {
    path = "auth/cert/login"

    parameters = {
      name = "terraform"
    }
  }

  # Client certificate configuration
  client_auth {
    cert_file = "${path.module}/certs/client-terraform.crt"
    key_file  = "${path.module}/certs/client-terraform.key"
  }

  # CA certificate for server verification
  ca_cert_file = "${path.module}/certs/ca.crt"

  # Skip TLS verification (not recommended)
  skip_tls_verify = false
}

# Example: Using retrieved keys
resource "aws_kms_key" "example" {
  description = "KMS key with Vaultwarden-managed key material"

  # Use key from Vaultwarden
  key_material_base64 = local.encryption_keys["master_key"]

  tags = {
    Name      = "vaultwarden-managed-key"
    ManagedBy = "YubiHSM"
  }
}
EOF
    
    success "Terraform mTLS provider configuration created"
}

# Python script for mTLS client
create_mtls_client_script() {
    info "Creating Python mTLS client script..."
    
    cat > scripts/mtls_client.py <<'EOF'
#!/usr/bin/env python3
"""
mTLS Client for Vaultwarden API
Demonstrates secure communication with mutual TLS authentication
"""

import ssl
import json
import requests
from pathlib import Path
import urllib3

class VaultwardenMTLSClient:
    def __init__(self, base_url, client_cert, client_key, ca_cert, api_key=None):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key

        # Configure mTLS
        self.cert = (client_cert, client_key)
        self.verify = ca_cert

        # Create session with mTLS
        self.session = requests.Session()
        self.session.cert = self.cert
        self.session.verify = self.verify

        # Add default headers
        if self.api_key:
            self.session.headers.update({
                'Authorization': f'Bearer {self.api_key}'
            })
        self.session.headers.update({
            'Content-Type': 'application/json'
        })

        # Disable SSL warnings if needed
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def retrieve_keys(self, key_type='infrastructure', key_ids=None):
        """Retrieve encryption keys from Vaultwarden"""
        endpoint = f'{self.base_url}/api/keys/retrieve'

        payload = {
            'key_type': key_type,
            'key_ids': key_ids or []
        }

        try:
            response = self.session.post(endpoint, json=payload)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.SSLError as e:
            print(f"SSL Error: {e}")
            return None
        except requests.exceptions.RequestException as e:
            print(f"Request failed: {e}")
            return None

    def store_key(self, key_name, key_value, key_type='generic'):
        """Store a key in Vaultwarden"""
        endpoint = f'{self.base_url}/api/keys/store'

        payload = {
            'key_name': key_name,
            'key_value': key_value,
            'key_type': key_type
        }

        try:
            response = self.session.post(endpoint, json=payload)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Failed to store key: {e}")
            return None

    def rotate_key(self, key_name):
        """Rotate a key in Vaultwarden"""
        endpoint = f'{self.base_url}/api/keys/rotate'

        payload = {'key_name': key_name}

        try:
            response = self.session.post(endpoint, json=payload)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Failed to rotate key: {e}")
            return None

    def verify_connection(self):
        """Verify mTLS connection"""
        endpoint = f'{self.base_url}/api/health'

        try:
            response = self.session.get(endpoint)
            if response.status_code == 200:
                print("✓ mTLS connection successful")
                cert_info = self.session.get(f'{self.base_url}/api/cert-info').json()
                print(f"  Client CN: {cert_info.get('client_cn', 'N/A')}")
                print(f"  Server CN: {cert_info.get('server_cn', 'N/A')}")
                return True
            else:
                print("✗ mTLS connection failed")
                return False
        except Exception as e:
            print(f"✗ Connection error: {e}")
            return False

def main():
    """Example usage of mTLS client"""
    import argparse

    parser = argparse.ArgumentParser(description='Vaultwarden mTLS Client')
    parser.add_argument('--url', required=True, help='Vaultwarden URL')
    parser.add_argument('--cert', required=True, help='Client certificate path')
    parser.add_argument('--key', required=True, help='Client key path')
    parser.add_argument('--ca', required=True, help='CA certificate path')
    parser.add_argument('--api-key', help='API key (optional)')
    parser.add_argument('--action', choices=['verify', 'retrieve', 'store', 'rotate'],
                       default='verify', help='Action to perform')
    parser.add_argument('--key-name', help='Key name for operations')
    parser.add_argument('--key-value', help='Key value for store operation')

    args = parser.parse_args()

    # Create client
    client = VaultwardenMTLSClient(
        base_url=args.url,
        client_cert=args.cert,
        client_key=args.key,
        ca_cert=args.ca,
        api_key=args.api_key
    )

    # Perform action
    if args.action == 'verify':
        client.verify_connection()

    elif args.action == 'retrieve':
        keys = client.retrieve_keys()
        if keys:
            print(f"Retrieved keys: {json.dumps(keys, indent=2)}")

    elif args.action == 'store' and args.key_name and args.key_value:
        result = client.store_key(args.key_name, args.key_value)
        if result:
            print(f"Key stored: {result}")

    elif args.action == 'rotate' and args.key_name:
        result = client.rotate_key(args.key_name)
        if result:
            print(f"Key rotated: {result}")

if __name__ == '__main__':
    main()
EOF
    
    chmod +x scripts/mtls_client.py
    success "mTLS client script created"
}

# Create certificate validation webhook
create_cert_validation_webhook() {
    info "Creating certificate validation webhook..."
    
    cat > /usr/local/bin/cert_validator.py <<'EOF'
#!/usr/bin/env python3
"""
Certificate Validation Webhook for Vaultwarden
Validates client certificates against YubiHSM-stored CA
"""

from flask import Flask, request, jsonify
import ssl
import base64
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from datetime import datetime

app = Flask(__name__)

# Load CA certificate
with open('/etc/vaultwarden/ca/ca.crt', 'rb') as f:
    CA_CERT = x509.load_pem_x509_certificate(f.read(), default_backend())

# Certificate revocation list (CRL)
REVOKED_SERIALS = set()

@app.route('/validate', methods=['POST'])
def validate_certificate():
    """Validate client certificate"""
    data = request.json
    cert_pem = data.get('certificate', '')

    try:
        # Decode certificate
        cert_data = base64.b64decode(cert_pem)
        cert = x509.load_pem_x509_certificate(cert_data, default_backend())

        # Check expiration
        if datetime.utcnow() > cert.not_valid_after:
            return jsonify({'valid': False, 'reason': 'Certificate expired'}), 403

        if datetime.utcnow() < cert.not_valid_before:
            return jsonify({'valid': False, 'reason': 'Certificate not yet valid'}), 403

        # Check revocation
        if cert.serial_number in REVOKED_SERIALS:
            return jsonify({'valid': False, 'reason': 'Certificate revoked'}), 403

        # Verify signature
        try:
            CA_CERT.public_key().verify(
                cert.signature,
                cert.tbs_certificate_bytes,
                cert.signature_algorithm_parameters
            )
        except:
            return jsonify({'valid': False, 'reason': 'Invalid signature'}), 403

        # Extract CN
        cn = None
        for attribute in cert.subject:
            if attribute.oid == x509.oid.NameOID.COMMON_NAME:
                cn = attribute.value
                break

        # Check allowed CNs
        allowed_cns = ['terraform', 'vault', 'admin']
        if cn not in allowed_cns:
            return jsonify({'valid': False, 'reason': f'Unauthorized CN: {cn}'}), 403

        return jsonify({
            'valid': True,
            'cn': cn,
            'serial': str(cert.serial_number),
            'expires': cert.not_valid_after.isoformat()
        })

    except Exception as e:
        return jsonify({'valid': False, 'reason': str(e)}), 500

@app.route('/revoke', methods=['POST'])
def revoke_certificate():
    """Add certificate to revocation list"""
    data = request.json
    serial = data.get('serial')

    if serial:
        REVOKED_SERIALS.add(int(serial))
        return jsonify({'revoked': True, 'serial': serial})

    return jsonify({'error': 'No serial provided'}), 400

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8443, ssl_context=(
        '/etc/vaultwarden/certs/server.crt',
        '/etc/vaultwarden/certs/server.key'
    ))
EOF
    
    chmod +x /usr/local/bin/cert_validator.py
    success "Certificate validation webhook created"
}

# Setup complete mTLS infrastructure
setup_mtls() {
    info "Setting up complete mTLS infrastructure..."
    
    # Create directories
    mkdir -p "$CERT_PATH" "$CA_PATH"
    chmod 700 "$CERT_PATH" "$CA_PATH"
    
    # Generate certificates
    generate_ca_in_hsm
    generate_server_cert
    generate_client_cert "terraform"
    generate_client_cert "vault"
    generate_client_cert "admin"
    
    # Configure services
    configure_nginx_mtls
    create_haproxy_mtls
    create_terraform_mtls_provider
    create_mtls_client_script
    create_cert_validation_webhook
    
    success "mTLS infrastructure setup complete"
}

# Test mTLS connection
test_mtls() {
    info "Testing mTLS connection..."
    
    # Test with curl
    curl -v \
    --cacert "$CA_PATH/ca.crt" \
    --cert "$CERT_PATH/client-terraform.crt" \
    --key "$CERT_PATH/client-terraform.key" \
    "https://$VAULTWARDEN_DOMAIN/api/health"
    
    # Test with Python client
    python3 scripts/mtls_client.py \
    --url "https://$VAULTWARDEN_DOMAIN" \
    --cert "$CERT_PATH/client-terraform.crt" \
    --key "$CERT_PATH/client-terraform.key" \
    --ca "$CA_PATH/ca.crt" \
    --action verify
}

# Main function
main() {
    case "${1:-}" in
        setup)
            setup_mtls
        ;;
        ca)
            generate_ca_in_hsm
        ;;
        server)
            generate_server_cert
        ;;
        client)
            generate_client_cert "${2:-terraform}"
        ;;
        nginx)
            configure_nginx_mtls
        ;;
        test)
            test_mtls
        ;;
        *)
            echo -e "${GREEN}Vaultwarden mTLS Configuration with YubiHSM 2${NC}"
            echo ""
            echo "Usage: $0 {setup|ca|server|client|nginx|test} [args...]"
            echo ""
            echo "Commands:"
            echo "  setup         Complete mTLS setup"
            echo "  ca            Generate CA certificate in YubiHSM"
            echo "  server        Generate server certificate"
            echo "  client <name> Generate client certificate"
            echo "  nginx         Configure Nginx for mTLS"
            echo "  test          Test mTLS connection"
            echo ""
            echo "Examples:"
            echo "  $0 setup"
            echo "  $0 client terraform"
            echo "  $0 test"
            exit 1
        ;;
    esac
}

# Create log directory
mkdir -p "$(dirname "$LOG_FILE")"

# Run main function
main "$@"