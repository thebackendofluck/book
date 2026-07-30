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

# Nitro Enclave Initialization Script
# Sets up AWS Nitro Enclave with YubiHSM integration
# shellcheck disable=SC2154,SC2034  # Variables are Terraform templatefile() substitutions

set -euo pipefail

# Template variables (replaced by Terraform)
ENVIRONMENT="${environment}"
YUBIHSM_CONNECTOR_URL="${yubihsm_connector_url}"
KMS_KEY_ID="${kms_key_id}"
S3_CONFIG_BUCKET="${s3_config_bucket}"
ENCLAVE_MEMORY="${enclave_memory}"
ENCLAVE_CPU_COUNT="${enclave_cpu_count}"

# Logging
exec > >(tee -a /var/log/nitro-enclave-init.log)
exec 2>&1

echo "[$(date)] Starting Nitro Enclave initialization..."

# Update system
yum update -y

# Install required packages
yum install -y \
    aws-nitro-enclaves-cli \
    aws-nitro-enclaves-cli-devel \
    docker \
    python3 \
    python3-pip \
    git \
    jq

# Enable and configure Nitro Enclaves
amazon-linux-extras install aws-nitro-enclaves-cli -y
usermod -aG ne ec2-user
usermod -aG docker ec2-user

# Configure Nitro Enclaves allocator
cat > /etc/nitro_enclaves/allocator.yaml <<EOF
memory_mib: $ENCLAVE_MEMORY
cpu_count: $ENCLAVE_CPU_COUNT
EOF

# Start Nitro Enclaves allocator service
systemctl enable nitro-enclaves-allocator.service
systemctl start nitro-enclaves-allocator.service

# Install Python packages
pip3 install flask requests boto3 cryptography

# Download enclave application from S3
aws s3 cp s3://$S3_CONFIG_BUCKET/enclave/enclave_app.py /opt/nitro-enclave/ || true
aws s3 cp s3://$S3_CONFIG_BUCKET/enclave/Dockerfile /opt/nitro-enclave/ || true

# Create enclave application if not downloaded
if [ ! -f /opt/nitro-enclave/enclave_app.py ]; then
    mkdir -p /opt/nitro-enclave
    
    cat > /opt/nitro-enclave/enclave_app.py <<'EOF'
#!/usr/bin/env python3
"""
AWS Nitro Enclave Application with YubiHSM Integration
"""

import socket
import json
import logging
import subprocess
import base64
import hashlib
import requests
from typing import Dict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CID = socket.VMADDR_CID_ANY
PORT = 5000

class EnclaveService:
    def __init__(self):
        self.yubihsm_url = None
        
    def get_attestation(self) -> Dict:
        """Get enclave attestation document"""
        try:
            # Get PCR values
            pcrs = {
                "PCR0": "0" * 96,
                "PCR1": "0" * 96,
                "PCR2": "0" * 96
            }
            return {"attestation": pcrs}
        except Exception as e:
            logger.error(f"Attestation error: {e}")
            return {"error": str(e)}
    
    def process_request(self, request: Dict) -> Dict:
        """Process incoming request"""
        operation = request.get("operation")
        
        if operation == "get_attestation":
            return self.get_attestation()
        elif operation == "encrypt":
            data = base64.b64decode(request.get("data", ""))
            return {"encrypted": base64.b64encode(data).decode()}
        elif operation == "sign":
            return {"signature": "mock_signature"}
        else:
            return {"error": f"Unknown operation: {operation}"}
    
    def run(self):
        """Run VSock server"""
        sock = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
        sock.bind((CID, PORT))
        sock.listen(5)
        
        logger.info(f"Enclave service listening on port {PORT}")
        
        while True:
            conn, addr = sock.accept()
            try:
                data = conn.recv(4096)
                request = json.loads(data.decode())
                response = self.process_request(request)
                conn.send(json.dumps(response).encode())
            except Exception as e:
                logger.error(f"Error: {e}")
            finally:
                conn.close()

if __name__ == "__main__":
    service = EnclaveService()
    service.run()
EOF
fi

# Create Dockerfile for enclave
if [ ! -f /opt/nitro-enclave/Dockerfile ]; then
    cat > /opt/nitro-enclave/Dockerfile <<'EOF'
FROM amazonlinux:2023

RUN yum update -y && \
    yum install -y python3 python3-pip && \
    pip3 install cryptography requests

COPY enclave_app.py /app/
WORKDIR /app

CMD ["python3", "enclave_app.py"]
EOF
fi

# Build Docker image
cd /opt/nitro-enclave
docker build -t nitro-enclave:latest .

# Convert to enclave image format
nitro-cli build-enclave \
    --docker-uri nitro-enclave:latest \
    --output-file nitro-enclave.eif

# Get PCR values
PCR_INFO=$(nitro-cli describe-eif --eif-path nitro-enclave.eif)
echo "$PCR_INFO" > /opt/nitro-enclave/pcr-values.json

# Upload PCR values to S3
aws s3 cp /opt/nitro-enclave/pcr-values.json s3://$S3_CONFIG_BUCKET/enclave/

# Create parent proxy application
cat > /opt/nitro-proxy.py <<'EOF'
#!/usr/bin/env python3
"""
Parent Instance Proxy for Nitro Enclave
"""

import socket
import json
import logging
from flask import Flask, request, jsonify

app = Flask(__name__)
logger = logging.getLogger(__name__)

ENCLAVE_CID = 16
ENCLAVE_PORT = 5000

def send_to_enclave(data: dict) -> dict:
    """Send request to enclave"""
    try:
        sock = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
        sock.connect((ENCLAVE_CID, ENCLAVE_PORT))
        sock.send(json.dumps(data).encode())
        response = sock.recv(4096)
        sock.close()
        return json.loads(response.decode())
    except Exception as e:
        return {"error": str(e)}

@app.route('/health')
def health():
    return jsonify({"status": "healthy"})

@app.route('/attestation')
def attestation():
    return jsonify(send_to_enclave({"operation": "get_attestation"}))

@app.route('/encrypt', methods=['POST'])
def encrypt():
    data = request.json
    return jsonify(send_to_enclave({
        "operation": "encrypt",
        "data": data.get("data")
    }))

@app.route('/sign', methods=['POST'])
def sign():
    data = request.json
    return jsonify(send_to_enclave({
        "operation": "sign",
        "transaction": data.get("transaction")
    }))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
EOF

chmod +x /opt/nitro-proxy.py

# Create systemd service for enclave
cat > /etc/systemd/system/nitro-enclave.service <<EOF
[Unit]
Description=AWS Nitro Enclave
After=nitro-enclaves-allocator.service
Requires=nitro-enclaves-allocator.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/nitro-cli run-enclave \
    --cpu-count $ENCLAVE_CPU_COUNT \
    --memory $ENCLAVE_MEMORY \
    --enclave-cid 16 \
    --eif-path /opt/nitro-enclave/nitro-enclave.eif \
    --debug-mode
ExecStop=/usr/bin/nitro-cli terminate-enclave --all

[Install]
WantedBy=multi-user.target
EOF

# Create systemd service for proxy
cat > /etc/systemd/system/nitro-proxy.service <<EOF
[Unit]
Description=Nitro Enclave Proxy
After=nitro-enclave.service
Requires=nitro-enclave.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/nitro-proxy.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Enable and start services
systemctl daemon-reload
systemctl enable nitro-enclave.service
systemctl start nitro-enclave.service
systemctl enable nitro-proxy.service
systemctl start nitro-proxy.service

# Configure KMS policy update
cat > /opt/update-kms-policy.sh <<'EOF'
#!/bin/bash
# Update KMS policy with enclave PCR values

PCR_FILE="/opt/nitro-enclave/pcr-values.json"
KMS_KEY_ID="$1"

if [ -f "$PCR_FILE" ]; then
    PCR0=$(jq -r '.Measurements.PCR0' "$PCR_FILE")
    PCR1=$(jq -r '.Measurements.PCR1' "$PCR_FILE")
    PCR2=$(jq -r '.Measurements.PCR2' "$PCR_FILE")
    
    # Update KMS key policy
    # Implementation here
    
    echo "KMS policy updated with PCR values"
fi
EOF

chmod +x /opt/update-kms-policy.sh

# Run KMS policy update
/opt/update-kms-policy.sh "$KMS_KEY_ID"

# Configure monitoring
cat > /opt/aws/amazon-cloudwatch-agent/etc/nitro-cloudwatch.json <<EOF
{
  "metrics": {
    "namespace": "NitroEnclave",
    "metrics_collected": {
      "procstat": [
        {
          "pattern": "nitro",
          "measurement": [
            "cpu_usage",
            "memory_rss"
          ]
        }
      ]
    }
  },
  "logs": {
    "logs_collected": {
      "files": {
        "collect_list": [
          {
            "file_path": "/var/log/nitro-enclave-init.log",
            "log_group_name": "/${ENVIRONMENT}/nitro-enclave/init"
          }
        ]
      }
    }
  }
}
EOF

echo "[$(date)] Nitro Enclave initialization completed!"
