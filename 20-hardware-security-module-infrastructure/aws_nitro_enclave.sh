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

# AWS Nitro Enclaves Integration with YubiHSM 2
# Secure key management for AWS Nitro Enclaves using YubiHSM 2 FIPS

set -euo pipefail

# Configuration
YUBIHSM_CONNECTOR_URL="${YUBIHSM_CONNECTOR_URL:-http://localhost:12345}"
YUBIHSM_AUTH_KEY="${YUBIHSM_AUTH_KEY:-2}"
ENCLAVE_IMAGE_NAME="${ENCLAVE_IMAGE_NAME:-yubihsm-enclave}"
ENCLAVE_CID="${ENCLAVE_CID:-16}"
ENCLAVE_MEMORY="${ENCLAVE_MEMORY:-2048}"
ENCLAVE_CPU_COUNT="${ENCLAVE_CPU_COUNT:-2}"
AWS_REGION="${AWS_REGION:-us-east-1}"
KMS_KEY_ALIAS="${KMS_KEY_ALIAS:-alias/nitro-enclave-yubihsm}"
LOG_FILE="/var/log/nitro-enclave-yubihsm.log"

# Colors for output
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

warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
    log "WARNING: $1"
}

info() {
    echo -e "${BLUE}[INFO]${NC} $1"
    log "INFO: $1"
}

# Check prerequisites
check_prerequisites() {
    local missing=()
    
    # Check for root
    if [[ $EUID -ne 0 ]]; then
        error "This script must be run as root"
    fi
    
    # Check for required tools
    for tool in nitro-cli aws docker yubihsm-shell python3 jq; do
        if ! command -v "$tool" &> /dev/null; then
            missing+=("$tool")
        fi
    done
    
    if [ ${#missing[@]} -gt 0 ]; then
        error "Missing required tools: ${missing[*]}"
    fi
    
    # Check if running on Nitro-enabled instance
    if ! nitro-cli describe-enclaves &> /dev/null; then
        error "This instance does not support AWS Nitro Enclaves"
    fi
    
    # Check YubiHSM connector
    if ! curl -s "$YUBIHSM_CONNECTOR_URL/connector/status" &> /dev/null; then
        warning "YubiHSM connector not running locally. Will use remote connector."
    fi
    
    success "All prerequisites met"
}

# Create Nitro Enclave application
create_enclave_app() {
    info "Creating Nitro Enclave application..."
    
    local app_dir="/opt/nitro-enclave-app"
    mkdir -p "$app_dir"
    
    # Create the main enclave application
    cat > "$app_dir/enclave_app.py" <<'EOF'
#!/usr/bin/env python3
"""
AWS Nitro Enclave YubiHSM 2 Key Manager
Secure key management service running inside AWS Nitro Enclave
"""

import socket
import json
import sys
import os
import hashlib
import base64
import logging
from typing import Dict, Optional, Tuple
import subprocess
import hmac

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# VSock configuration
CID = socket.VMADDR_CID_ANY
PORT = 5000

class EnclaveKeyManager:
    """Key management service for Nitro Enclave"""
    
    def __init__(self):
        self.attestation_doc = None
        self.session_keys = {}
        
    def get_attestation_document(self) -> Dict:
        """Generate enclave attestation document"""
        try:
            # Get attestation document from Nitro Secure Module
            result = subprocess.run(
                ["/usr/bin/nitro-cli", "describe-enclaves"],
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                enclave_info = json.loads(result.stdout)
                return {
                    "pcr0": enclave_info[0]["PCR0"],
                    "pcr1": enclave_info[0]["PCR1"],
                    "pcr2": enclave_info[0]["PCR2"],
                    "enclave_id": enclave_info[0]["EnclaveID"]
                }
            else:
                logger.error(f"Failed to get attestation: {result.stderr}")
                return None
                
        except Exception as e:
            logger.error(f"Attestation error: {e}")
            return None
    
    def derive_key_from_hsm(self, key_id: int, context: str) -> Optional[bytes]:
        """Derive a key from YubiHSM 2 based on context"""
        try:
            # In production, this would connect to YubiHSM via secure channel
            # For now, derive deterministically for demonstration
            derived = hashlib.pbkdf2_hmac(
                'sha256',
                f'yubihsm-key-{key_id}'.encode(),
                context.encode(),
                100000,
                32
            )
            return derived
            
        except Exception as e:
            logger.error(f"Key derivation error: {e}")
            return None
    
    def encrypt_for_kms(self, data: bytes, key_id: str) -> Dict:
        """Encrypt data for AWS KMS with attestation"""
        try:
            attestation = self.get_attestation_document()
            if not attestation:
                return {"error": "Failed to get attestation"}
            
            # Encrypt data using derived key
            key = self.derive_key_from_hsm(100, key_id)
            if not key:
                return {"error": "Failed to derive key"}
            
            # Simple XOR encryption for demo (use proper encryption in production)
            encrypted = bytes(a ^ b for a, b in zip(data, key * (len(data) // 32 + 1)))
            
            return {
                "encrypted_data": base64.b64encode(encrypted).decode(),
                "attestation": attestation,
                "key_id": key_id
            }
            
        except Exception as e:
            logger.error(f"Encryption error: {e}")
            return {"error": str(e)}
    
    def sign_transaction(self, transaction: Dict) -> Dict:
        """Sign a transaction using YubiHSM 2"""
        try:
            # Serialize transaction
            tx_data = json.dumps(transaction, sort_keys=True).encode()
            tx_hash = hashlib.sha256(tx_data).digest()
            
            # Get signing key from HSM
            signing_key = self.derive_key_from_hsm(200, "signing")
            if not signing_key:
                return {"error": "Failed to get signing key"}
            
            # Create HMAC signature (in production, use proper ECDSA)
            signature = hmac.new(signing_key, tx_hash, hashlib.sha256).digest()
            
            return {
                "transaction": transaction,
                "signature": base64.b64encode(signature).decode(),
                "hash": base64.b64encode(tx_hash).decode()
            }
            
        except Exception as e:
            logger.error(f"Signing error: {e}")
            return {"error": str(e)}
    
    def handle_request(self, request: Dict) -> Dict:
        """Handle incoming requests"""
        try:
            operation = request.get("operation")
            
            if operation == "get_attestation":
                attestation = self.get_attestation_document()
                return {"attestation": attestation} if attestation else {"error": "Failed"}
                
            elif operation == "encrypt":
                data = base64.b64decode(request.get("data", ""))
                key_id = request.get("key_id", "default")
                return self.encrypt_for_kms(data, key_id)
                
            elif operation == "sign":
                transaction = request.get("transaction", {})
                return self.sign_transaction(transaction)
                
            elif operation == "derive_key":
                key_id = request.get("key_id", 100)
                context = request.get("context", "default")
                key = self.derive_key_from_hsm(key_id, context)
                if key:
                    return {"key": base64.b64encode(key).decode()}
                else:
                    return {"error": "Key derivation failed"}
                    
            else:
                return {"error": f"Unknown operation: {operation}"}
                
        except Exception as e:
            logger.error(f"Request handling error: {e}")
            return {"error": str(e)}
    
    def start_server(self):
        """Start VSock server inside enclave"""
        try:
            # Create VSock socket
            sock = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
            sock.bind((CID, PORT))
            sock.listen(5)
            
            logger.info(f"Enclave key manager listening on port {PORT}")
            
            while True:
                conn, addr = sock.accept()
                logger.info(f"Connection from {addr}")
                
                try:
                    # Receive request
                    data = conn.recv(4096)
                    request = json.loads(data.decode())
                    logger.info(f"Request: {request.get('operation')}")
                    
                    # Process request
                    response = self.handle_request(request)
                    
                    # Send response
                    conn.send(json.dumps(response).encode())
                    
                except Exception as e:
                    logger.error(f"Connection error: {e}")
                    error_response = {"error": str(e)}
                    conn.send(json.dumps(error_response).encode())
                finally:
                    conn.close()
                    
        except Exception as e:
            logger.error(f"Server error: {e}")
            sys.exit(1)

def main():
    """Main entry point"""
    logger.info("Starting AWS Nitro Enclave YubiHSM Key Manager")
    
    manager = EnclaveKeyManager()
    manager.start_server()

if __name__ == "__main__":
    main()
EOF
    
    chmod +x "$app_dir/enclave_app.py"
    success "Created enclave application"
}

# Create Dockerfile for enclave
create_enclave_dockerfile() {
    info "Creating Dockerfile for Nitro Enclave..."
    
    local docker_dir="/opt/nitro-enclave-docker"
    mkdir -p "$docker_dir"
    
    cat > "$docker_dir/Dockerfile" <<'EOF'
FROM amazonlinux:2023

# Install required packages
RUN yum update -y && \
    yum install -y python3 python3-pip openssl && \
    yum clean all

# Install Python packages
RUN pip3 install cryptography

# Create enclave user
RUN useradd -m -s /bin/bash enclave

# Copy enclave application
COPY enclave_app.py /home/enclave/
RUN chmod +x /home/enclave/enclave_app.py && \
    chown enclave:enclave /home/enclave/enclave_app.py

# Switch to enclave user
USER enclave
WORKDIR /home/enclave

# Run the enclave application
CMD ["/usr/bin/python3", "/home/enclave/enclave_app.py"]
EOF
    
    # Copy application to Docker context
    cp /opt/nitro-enclave-app/enclave_app.py "$docker_dir/"
    
    success "Created Dockerfile for enclave"
}

# Build enclave image
build_enclave_image() {
    info "Building Nitro Enclave image..."
    
    local docker_dir="/opt/nitro-enclave-docker"
    
    # Build Docker image
    cd "$docker_dir"
    if ! docker build -t "$ENCLAVE_IMAGE_NAME" .; then
        error "Failed to build Docker image"
    fi

    # Convert to enclave image format
    if ! nitro-cli build-enclave \
        --docker-uri "$ENCLAVE_IMAGE_NAME:latest" \
        --output-file "${ENCLAVE_IMAGE_NAME}.eif"; then
        error "Failed to build enclave image"
    fi

    # Get PCR values for KMS policy
    local pcr_info
    pcr_info=$(nitro-cli describe-eif --eif-path "${ENCLAVE_IMAGE_NAME}.eif")
    echo "$pcr_info" > "${ENCLAVE_IMAGE_NAME}.pcrs"
    
    info "PCR values for KMS policy:"
    echo "$pcr_info" | jq '.Measurements'
    
    success "Built enclave image: ${ENCLAVE_IMAGE_NAME}.eif"
}

# Create proxy application for parent instance
create_parent_proxy() {
    info "Creating parent instance proxy..."
    
    cat > /opt/nitro-proxy.py <<'EOF'
#!/usr/bin/env python3
"""
Parent Instance Proxy for Nitro Enclave
Forwards requests between external clients and enclave
"""

import socket
import json
import sys
import argparse
from flask import Flask, request, jsonify
import logging

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Enclave configuration
ENCLAVE_CID = 16
ENCLAVE_PORT = 5000

def send_to_enclave(data: dict) -> dict:
    """Send request to enclave and get response"""
    try:
        # Create VSock socket
        sock = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
        sock.connect((ENCLAVE_CID, ENCLAVE_PORT))
        
        # Send request
        sock.send(json.dumps(data).encode())
        
        # Receive response
        response = sock.recv(4096)
        sock.close()
        
        return json.loads(response.decode())
        
    except Exception as e:
        logger.error(f"Enclave communication error: {e}")
        return {"error": str(e)}

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({"status": "healthy"})

@app.route('/attestation', methods=['GET'])
def get_attestation():
    """Get enclave attestation document"""
    response = send_to_enclave({"operation": "get_attestation"})
    return jsonify(response)

@app.route('/encrypt', methods=['POST'])
def encrypt_data():
    """Encrypt data using enclave"""
    data = request.json
    response = send_to_enclave({
        "operation": "encrypt",
        "data": data.get("data"),
        "key_id": data.get("key_id", "default")
    })
    return jsonify(response)

@app.route('/sign', methods=['POST'])
def sign_transaction():
    """Sign transaction using enclave"""
    data = request.json
    response = send_to_enclave({
        "operation": "sign",
        "transaction": data.get("transaction")
    })
    return jsonify(response)

@app.route('/derive', methods=['POST'])
def derive_key():
    """Derive key using enclave"""
    data = request.json
    response = send_to_enclave({
        "operation": "derive_key",
        "key_id": data.get("key_id", 100),
        "context": data.get("context", "default")
    })
    return jsonify(response)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
EOF
    
    chmod +x /opt/nitro-proxy.py
    
    # Install Flask
    pip3 install flask
    
    success "Created parent instance proxy"
}

# Run Nitro Enclave
run_enclave() {
    info "Starting Nitro Enclave..."
    
    # Terminate any existing enclave
    nitro-cli terminate-enclave --all &> /dev/null || true
    
    # Run new enclave
    local enclave_info
    enclave_info=$(nitro-cli run-enclave \
        --cpu-count "$ENCLAVE_CPU_COUNT" \
        --memory "$ENCLAVE_MEMORY" \
        --enclave-cid "$ENCLAVE_CID" \
        --eif-path "/opt/nitro-enclave-docker/${ENCLAVE_IMAGE_NAME}.eif" \
        --debug-mode) || error "Failed to start enclave"

    # Extract enclave ID
    local enclave_id
    enclave_id=$(echo "$enclave_info" | jq -r '.EnclaveID')
    echo "$enclave_id" > /var/run/nitro-enclave.id
    
    info "Enclave started with ID: $enclave_id"
    
    # Verify enclave is running
    sleep 2
    nitro-cli describe-enclaves
    
    success "Nitro Enclave is running"
}

# Configure AWS KMS for enclave
configure_kms() {
    info "Configuring AWS KMS for Nitro Enclave..."
    
    # Read PCR values
    local pcr_file="/opt/nitro-enclave-docker/${ENCLAVE_IMAGE_NAME}.pcrs"
    if [ ! -f "$pcr_file" ]; then
        error "PCR file not found. Build enclave first."
    fi
    
    local pcr0 pcr1 pcr2
    pcr0=$(jq -r '.Measurements.PCR0' "$pcr_file")
    pcr1=$(jq -r '.Measurements.PCR1' "$pcr_file")
    pcr2=$(jq -r '.Measurements.PCR2' "$pcr_file")

    # Create KMS key if not exists
    local key_id
    key_id=$(aws kms describe-key --key-id "$KMS_KEY_ALIAS" \
        --region "$AWS_REGION" 2>/dev/null | jq -r '.KeyMetadata.KeyId')
    
    if [ -z "$key_id" ] || [ "$key_id" == "null" ]; then
        info "Creating new KMS key..."
        key_id=$(aws kms create-key \
            --description "YubiHSM Nitro Enclave Key" \
            --region "$AWS_REGION" \
            --query 'KeyMetadata.KeyId' \
            --output text)
        
        # Create alias
        aws kms create-alias \
            --alias-name "$KMS_KEY_ALIAS" \
            --target-key-id "$key_id" \
            --region "$AWS_REGION"
    fi
    
    info "KMS Key ID: $key_id"
    
    # Create KMS key policy for enclave
    cat > /tmp/kms-policy.json <<EOF
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "Enable IAM User Permissions",
            "Effect": "Allow",
            "Principal": {
                "AWS": "arn:aws:iam::$(aws sts get-caller-identity --query Account --output text):root"
            },
            "Action": "kms:*",
            "Resource": "*"
        },
        {
            "Sid": "Allow Nitro Enclave to use the key",
            "Effect": "Allow",
            "Principal": {
                "AWS": "*"
            },
            "Action": [
                "kms:Decrypt",
                "kms:GenerateDataKey"
            ],
            "Resource": "*",
            "Condition": {
                "StringEqualsIgnoreCase": {
                    "kms:RecipientAttestation:PCR0": "$pcr0",
                    "kms:RecipientAttestation:PCR1": "$pcr1",
                    "kms:RecipientAttestation:PCR2": "$pcr2"
                }
            }
        }
    ]
}
EOF
    
    # Apply KMS key policy
    aws kms put-key-policy \
        --key-id "$key_id" \
        --policy-name default \
        --policy file:///tmp/kms-policy.json \
        --region "$AWS_REGION"
    
    kms_exit=$?
    if [ $kms_exit -eq 0 ]; then
        success "KMS key configured for Nitro Enclave"
    else
        error "Failed to configure KMS key policy"
    fi
    
    # Save configuration
    cat > /etc/nitro-enclave-kms.conf <<EOF
KMS_KEY_ID=$key_id
KMS_KEY_ALIAS=$KMS_KEY_ALIAS
AWS_REGION=$AWS_REGION
PCR0=$pcr0
PCR1=$pcr1
PCR2=$pcr2
EOF
    
    info "KMS configuration saved to /etc/nitro-enclave-kms.conf"
}

# Create systemd service
create_systemd_service() {
    info "Creating systemd services..."
    
    # Service for enclave
    cat > /etc/systemd/system/nitro-enclave.service <<EOF
[Unit]
Description=AWS Nitro Enclave with YubiHSM
After=network.target docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/bin/nitro-enclave-manager.sh start
ExecStop=/usr/local/bin/nitro-enclave-manager.sh stop
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
    
    # Service for proxy
    cat > /etc/systemd/system/nitro-proxy.service <<EOF
[Unit]
Description=Nitro Enclave Proxy Service
After=nitro-enclave.service
Requires=nitro-enclave.service

[Service]
Type=simple
User=ec2-user
ExecStart=/usr/bin/python3 /opt/nitro-proxy.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
    
    # Create manager script
    cat > /usr/local/bin/nitro-enclave-manager.sh <<'SCRIPT'
#!/bin/bash
case "$1" in
    start)
        nitro-cli run-enclave \
            --cpu-count 2 \
            --memory 2048 \
            --enclave-cid 16 \
            --eif-path "/opt/nitro-enclave-docker/yubihsm-enclave.eif" \
            --debug-mode
        ;;
    stop)
        nitro-cli terminate-enclave --all
        ;;
    *)
        echo "Usage: $0 {start|stop}"
        exit 1
        ;;
esac
SCRIPT
    
    chmod +x /usr/local/bin/nitro-enclave-manager.sh
    
    # Enable services
    systemctl daemon-reload
    systemctl enable nitro-enclave.service
    systemctl enable nitro-proxy.service
    
    success "Systemd services created"
}

# Test enclave functionality
test_enclave() {
    info "Testing Nitro Enclave functionality..."
    
    # Start proxy if not running
    if ! pgrep -f nitro-proxy.py > /dev/null; then
        python3 /opt/nitro-proxy.py &
        sleep 2
    fi
    
    # Test attestation
    info "Testing attestation..."
    curl -s http://localhost:8080/attestation | jq .
    
    # Test encryption
    info "Testing encryption..."
    curl -s -X POST http://localhost:8080/encrypt \
        -H "Content-Type: application/json" \
        -d '{"data": "SGVsbG8gV29ybGQ=", "key_id": "test"}' | jq .
    
    # Test signing
    info "Testing transaction signing..."
    curl -s -X POST http://localhost:8080/sign \
        -H "Content-Type: application/json" \
        -d '{"transaction": {"from": "alice", "to": "bob", "amount": 100}}' | jq .
    
    success "Enclave tests completed"
}

# Stop enclave
stop_enclave() {
    info "Stopping Nitro Enclave..."
    
    # Terminate enclave
    nitro-cli terminate-enclave --all
    
    # Stop proxy
    pkill -f nitro-proxy.py || true
    
    success "Nitro Enclave stopped"
}

# Main function
main() {
    case "${1:-}" in
        init)
            check_prerequisites
            create_enclave_app
            create_enclave_dockerfile
            create_parent_proxy
            success "Nitro Enclave environment initialized"
            ;;
        build)
            build_enclave_image
            ;;
        run)
            run_enclave
            ;;
        configure-kms)
            configure_kms
            ;;
        start)
            run_enclave
            python3 /opt/nitro-proxy.py &
            success "Enclave and proxy started"
            ;;
        stop)
            stop_enclave
            ;;
        test)
            test_enclave
            ;;
        service)
            create_systemd_service
            ;;
        status)
            nitro-cli describe-enclaves
            ;;
        *)
            echo -e "${GREEN}AWS Nitro Enclave YubiHSM Integration${NC}"
            echo ""
            echo "Usage: $0 {init|build|run|configure-kms|start|stop|test|service|status}"
            echo ""
            echo "Commands:"
            echo "  init           Initialize enclave environment"
            echo "  build          Build enclave image"
            echo "  run            Run the enclave"
            echo "  configure-kms  Configure AWS KMS for enclave"
            echo "  start          Start enclave and proxy"
            echo "  stop           Stop enclave and proxy"
            echo "  test           Test enclave functionality"
            echo "  service        Create systemd services"
            echo "  status         Show enclave status"
            echo ""
            echo "Environment Variables:"
            echo "  YUBIHSM_CONNECTOR_URL  YubiHSM connector URL"
            echo "  ENCLAVE_IMAGE_NAME     Enclave image name"
            echo "  ENCLAVE_CID            Enclave CID (default: 16)"
            echo "  ENCLAVE_MEMORY         Enclave memory in MB"
            echo "  AWS_REGION             AWS region"
            echo "  KMS_KEY_ALIAS          KMS key alias"
            exit 1
            ;;
    esac
}

# Create log directory
mkdir -p "$(dirname "$LOG_FILE")"

# Run main function
main "$@"
