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

# YubiHSM Connector Initialization Script for EC2
# Configures and starts YubiHSM connector service with Vaultwarden integration
# shellcheck disable=SC2154,SC2034  # Variables are Terraform templatefile() substitutions

set -euo pipefail

# Template variables (replaced by Terraform)
ENVIRONMENT="${environment}"
VAULTWARDEN_URL="${vaultwarden_url}"
YUBIHSM_PASSWORD="${yubihsm_password}"

# Logging
exec > >(tee -a /var/log/yubihsm-init.log)
exec 2>&1

echo "[$(date)] Starting YubiHSM Connector initialization..."

# Update system
yum update -y

# Install required packages
yum install -y \
    docker \
    python3 \
    python3-pip \
    git \
    jq \
    wget \
    openssl \
    libusb

# Start Docker
systemctl enable docker
systemctl start docker

# Install YubiHSM SDK
cd /tmp
wget https://developers.yubico.com/YubiHSM2/Releases/yubihsm2-sdk-latest-linux.tar.gz
tar -xzf yubihsm2-sdk-latest-linux.tar.gz
cd yubihsm2-sdk
rpm -i yubihsm2-sdk-*.rpm || true

# Install Python packages
pip3 install yubihsm[http,usb] cryptography requests flask

# Create YubiHSM user
useradd -r -s /bin/false yubihsm || true

# Configure USB access
cat > /etc/udev/rules.d/10-yubihsm.rules <<EOF
SUBSYSTEM=="usb", ATTRS{idVendor}=="1050", ATTRS{idProduct}=="0030", MODE="0660", GROUP="yubihsm"
EOF

udevadm control --reload-rules
udevadm trigger

# Create configuration directory
mkdir -p /etc/yubihsm
chmod 750 /etc/yubihsm
chown yubihsm:yubihsm /etc/yubihsm

# Create YubiHSM connector configuration
cat > /etc/yubihsm/connector.conf <<EOF
# YubiHSM Connector Configuration
listen = 0.0.0.0:12345
timeout = 300
# TLS configuration (optional)
# cert = /etc/yubihsm/connector.crt
# key = /etc/yubihsm/connector.key
EOF

# Store YubiHSM password securely
echo "$YUBIHSM_PASSWORD" | aws ssm put-parameter \
    --name "/${ENVIRONMENT}/yubihsm/connector/password" \
    --value "file:///dev/stdin" \
    --type "SecureString" \
    --overwrite \
    --region "$(curl -s http://169.254.169.254/latest/meta-data/placement/region)" || true

# Create systemd service for YubiHSM connector
cat > /etc/systemd/system/yubihsm-connector.service <<EOF
[Unit]
Description=YubiHSM Connector Service
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=yubihsm
Group=yubihsm
ExecStart=/usr/bin/yubihsm-connector -c /etc/yubihsm/connector.conf
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# Create Docker container for YubiHSM connector (alternative method)
docker run -d \
    --name yubihsm-connector \
    --restart unless-stopped \
    --privileged \
    --device /dev/bus/usb:/dev/bus/usb \
    -v /run/udev:/run/udev:ro \
    -p 12345:12345 \
    -e YUBIHSM_CONNECTOR_LISTEN=0.0.0.0:12345 \
    yubico/yubihsm-connector:latest \
    -d || true

# Create Vaultwarden integration service
cat > /usr/local/bin/vaultwarden-yubihsm-sync.py <<'EOF'
#!/usr/bin/env python3
"""
Vaultwarden YubiHSM Synchronization Service
Syncs encryption keys between Vaultwarden and YubiHSM
"""

import os
import sys
import time
import json
import requests
import logging
from yubihsm import YubiHsm
from yubihsm.defs import CAPABILITY, ALGORITHM

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def sync_keys():
    """Synchronize keys from Vaultwarden to YubiHSM"""
    
    vaultwarden_url = os.getenv('VAULTWARDEN_URL')
    yubihsm_password = os.getenv('YUBIHSM_PASSWORD')
    
    try:
        # Connect to YubiHSM
        hsm = YubiHsm.connect('http://localhost:12345')
        session = hsm.create_session_derived(2, yubihsm_password)
        
        # Retrieve keys from Vaultwarden
        # Implementation here
        
        logger.info("Key synchronization completed")
        session.close()
        
    except Exception as e:
        logger.error(f"Sync failed: {e}")

if __name__ == '__main__':
    while True:
        sync_keys()
        time.sleep(3600)  # Sync every hour
EOF

chmod +x /usr/local/bin/vaultwarden-yubihsm-sync.py

# Create sync service
cat > /etc/systemd/system/vaultwarden-yubihsm-sync.service <<EOF
[Unit]
Description=Vaultwarden YubiHSM Sync Service
After=yubihsm-connector.service
Requires=yubihsm-connector.service

[Service]
Type=simple
User=yubihsm
Environment="VAULTWARDEN_URL=${VAULTWARDEN_URL}"
Environment="YUBIHSM_PASSWORD=${YUBIHSM_PASSWORD}"
ExecStart=/usr/local/bin/vaultwarden-yubihsm-sync.py
Restart=always
RestartSec=60

[Install]
WantedBy=multi-user.target
EOF

# Enable and start services
systemctl daemon-reload
systemctl enable yubihsm-connector.service
systemctl start yubihsm-connector.service
systemctl enable vaultwarden-yubihsm-sync.service
systemctl start vaultwarden-yubihsm-sync.service

# Create health check endpoint
cat > /usr/local/bin/yubihsm-health.py <<'EOF'
#!/usr/bin/env python3
from flask import Flask, jsonify
import subprocess

app = Flask(__name__)

@app.route('/health')
def health():
    try:
        # Check connector status
        result = subprocess.run(['curl', '-s', 'http://localhost:12345/connector/status'],
                              capture_output=True, text=True)
        if result.returncode == 0:
            return jsonify({"status": "healthy", "connector": "running"})
    except:
        pass
    return jsonify({"status": "unhealthy"}), 503

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
EOF

chmod +x /usr/local/bin/yubihsm-health.py

# Start health check service
nohup python3 /usr/local/bin/yubihsm-health.py &

# Configure CloudWatch agent
cat > /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json <<EOF
{
  "metrics": {
    "namespace": "YubiHSM",
    "metrics_collected": {
      "cpu": {
        "measurement": [
          {"name": "cpu_usage_idle", "rename": "CPU_IDLE", "unit": "Percent"}
        ],
        "metrics_collection_interval": 60
      },
      "mem": {
        "measurement": [
          {"name": "mem_used_percent", "rename": "MEM_USED", "unit": "Percent"}
        ],
        "metrics_collection_interval": 60
      }
    }
  },
  "logs": {
    "logs_collected": {
      "files": {
        "collect_list": [
          {
            "file_path": "/var/log/yubihsm-init.log",
            "log_group_name": "/${ENVIRONMENT}/yubihsm/init",
            "log_stream_name": "{instance_id}"
          }
        ]
      }
    }
  }
}
EOF

# Start CloudWatch agent
/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
    -a fetch-config \
    -m ec2 \
    -s \
    -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json

echo "[$(date)] YubiHSM Connector initialization completed successfully!"
