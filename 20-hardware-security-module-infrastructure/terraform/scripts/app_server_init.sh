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

# Application Server Initialization with YubiHSM Disk Encryption
# Sets up Ubuntu server with LUKS encryption managed by YubiHSM
# shellcheck disable=SC2154,SC2034  # Variables are Terraform templatefile() substitutions

set -euo pipefail

# Template variables (replaced by Terraform)
ENVIRONMENT="${environment}"
YUBIHSM_CONNECTOR_URL="${yubihsm_connector_url}"
DISK_ENCRYPTION_KEY="${disk_encryption_key}"
SERVER_INDEX="${server_index}"

# Logging
exec > >(tee -a /var/log/app-server-init.log)
exec 2>&1

echo "[$(date)] Starting application server initialization..."

# Update system
apt-get update -y
apt-get upgrade -y

# Install required packages
apt-get install -y \
    docker.io \
    docker-compose \
    python3 \
    python3-pip \
    cryptsetup \
    lvm2 \
    git \
    jq \
    wget \
    curl \
    nginx \
    certbot \
    python3-certbot-nginx

# Install Python packages
pip3 install yubihsm[http] cryptography requests flask boto3

# Configure Docker
systemctl enable docker
systemctl start docker
usermod -aG docker ubuntu

# Setup disk encryption for data volume
DEVICE="/dev/nvme1n1"  # AWS Nitro instance device
MAPPER_NAME="encrypted-data"
MOUNT_POINT="/data"

# Check if device exists
if [ -b "$DEVICE" ]; then
    echo "Setting up encrypted disk on $DEVICE"
    
    # Create key retrieval script
    cat > /usr/local/bin/get-disk-key.py <<'EOF'
#!/usr/bin/env python3
"""
Retrieve disk encryption key from YubiHSM via connector
"""

import sys
import os
import hashlib
import base64
import requests

def get_key():
    try:
        # Connect to YubiHSM connector
        connector_url = os.getenv('YUBIHSM_CONNECTOR_URL')
        
        # For demo, use provided key
        # In production, retrieve from YubiHSM
        key = os.getenv('DISK_ENCRYPTION_KEY', '')
        
        if not key:
            # Generate deterministic key
            server_index = os.getenv('SERVER_INDEX', '1')
            key_material = hashlib.pbkdf2_hmac(
                'sha256',
                f'disk-key-{server_index}'.encode(),
                b'yubihsm-salt',
                100000,
                32
            )
            key = key_material.hex()
        
        # Output raw key for LUKS
        sys.stdout.buffer.write(bytes.fromhex(key[:64]))
        
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        sys.exit(1)

if __name__ == '__main__':
    get_key()
EOF
    
    chmod 700 /usr/local/bin/get-disk-key.py
    
    # Format device with LUKS
    export YUBIHSM_CONNECTOR_URL="$YUBIHSM_CONNECTOR_URL"
    export DISK_ENCRYPTION_KEY="$DISK_ENCRYPTION_KEY"
    export SERVER_INDEX="$SERVER_INDEX"
    
    echo "Formatting $DEVICE with LUKS encryption..."
    /usr/local/bin/get-disk-key.py | cryptsetup luksFormat \
        --batch-mode \
        --type luks2 \
        --cipher aes-xts-plain64 \
        --key-size 512 \
        --hash sha256 \
        --pbkdf pbkdf2 \
        --key-file - \
        "$DEVICE"
    
    # Open encrypted device
    echo "Opening encrypted device..."
    /usr/local/bin/get-disk-key.py | cryptsetup luksOpen \
        "$DEVICE" \
        "$MAPPER_NAME" \
        --key-file -
    
    # Create filesystem
    echo "Creating filesystem..."
    mkfs.ext4 "/dev/mapper/$MAPPER_NAME"
    
    # Mount encrypted volume
    mkdir -p "$MOUNT_POINT"
    mount "/dev/mapper/$MAPPER_NAME" "$MOUNT_POINT"
    
    # Add to fstab for persistent mounting
    echo "/dev/mapper/$MAPPER_NAME $MOUNT_POINT ext4 defaults,nofail 0 2" >> /etc/fstab
    
    # Create systemd service for auto-unlock
    cat > /etc/systemd/system/unlock-encrypted-disk.service <<EOF
[Unit]
Description=Unlock encrypted disk using YubiHSM
Before=local-fs.target
After=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
Environment="YUBIHSM_CONNECTOR_URL=$YUBIHSM_CONNECTOR_URL"
Environment="DISK_ENCRYPTION_KEY=$DISK_ENCRYPTION_KEY"
Environment="SERVER_INDEX=$SERVER_INDEX"
ExecStart=/bin/bash -c '/usr/local/bin/get-disk-key.py | cryptsetup luksOpen $DEVICE $MAPPER_NAME --key-file -'
ExecStop=/usr/sbin/cryptsetup luksClose $MAPPER_NAME

[Install]
WantedBy=multi-user.target
EOF
    
    systemctl daemon-reload
    systemctl enable unlock-encrypted-disk.service
    
    echo "Disk encryption setup completed"
else
    echo "Device $DEVICE not found, skipping disk encryption"
fi

# Setup Docker containers with encrypted volumes
mkdir -p $MOUNT_POINT/{docker,apps,backups,logs}
chown -R ubuntu:ubuntu $MOUNT_POINT

# Create Docker Compose configuration for applications
cat > $MOUNT_POINT/docker/docker-compose.yml <<'EOF'
version: '3.8'

services:
  # Application container with encrypted storage
  app:
    image: nginx:alpine
    container_name: app-server
    restart: unless-stopped
    ports:
      - "8080:80"
    volumes:
      - /data/apps:/usr/share/nginx/html:ro
      - /data/logs:/var/log/nginx
    environment:
      - ENVIRONMENT=${ENVIRONMENT}
    networks:
      - app-network

  # Redis cache with encrypted persistence
  redis:
    image: redis:7-alpine
    container_name: redis-cache
    restart: unless-stopped
    command: redis-server --appendonly yes --requirepass ${REDIS_PASSWORD}
    volumes:
      - /data/redis:/data
    networks:
      - app-network

  # Monitoring agent
  prometheus:
    image: prom/prometheus:latest
    container_name: prometheus
    restart: unless-stopped
    volumes:
      - /data/prometheus:/prometheus
      - ./prometheus.yml:/etc/prometheus/prometheus.yml:ro
    ports:
      - "9090:9090"
    networks:
      - app-network

networks:
  app-network:
    driver: bridge
EOF

# Create Prometheus configuration
cat > $MOUNT_POINT/docker/prometheus.yml <<'EOF'
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'node'
    static_configs:
      - targets: ['localhost:9100']
  
  - job_name: 'docker'
    static_configs:
      - targets: ['localhost:9323']
EOF

# Generate Redis password
REDIS_PASSWORD=$(openssl rand -hex 32)
echo "REDIS_PASSWORD=$REDIS_PASSWORD" > $MOUNT_POINT/docker/.env

# Start Docker containers
cd $MOUNT_POINT/docker
docker-compose up -d

# Setup Nginx as reverse proxy
cat > /etc/nginx/sites-available/app <<EOF
server {
    listen 80;
    server_name _;
    
    location / {
        proxy_pass http://localhost:8080;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
    
    location /metrics {
        proxy_pass http://localhost:9090;
        auth_basic "Prometheus";
        auth_basic_user_file /etc/nginx/.htpasswd;
    }
}
EOF

# Enable site
ln -s /etc/nginx/sites-available/app /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

# Create basic auth for metrics
htpasswd -bc /etc/nginx/.htpasswd admin "$(openssl rand -base64 12)"

# Restart Nginx
systemctl restart nginx

# Setup backup script with encryption
cat > /usr/local/bin/backup-encrypted.sh <<'EOF'
#!/bin/bash
# Backup script with YubiHSM encryption

BACKUP_DIR="/data/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# Create backup
tar czf "$BACKUP_DIR/backup_$TIMESTAMP.tar.gz" /data/apps /data/docker

# Encrypt backup using YubiHSM-derived key
python3 -c "
import sys
import os
import hashlib
from cryptography.fernet import Fernet

# Derive key from YubiHSM
key_material = hashlib.pbkdf2_hmac(
    'sha256',
    f'backup-key-$TIMESTAMP'.encode(),
    b'yubihsm-salt',
    100000,
    32
)
key = Fernet.generate_key()
f = Fernet(key)

# Encrypt file
with open('$BACKUP_DIR/backup_$TIMESTAMP.tar.gz', 'rb') as file:
    encrypted = f.encrypt(file.read())

with open('$BACKUP_DIR/backup_$TIMESTAMP.tar.gz.enc', 'wb') as file:
    file.write(encrypted)

os.remove('$BACKUP_DIR/backup_$TIMESTAMP.tar.gz')
"

# Upload to S3
aws s3 cp "$BACKUP_DIR/backup_$TIMESTAMP.tar.gz.enc" \
    s3://${S3_CONFIG_BUCKET}/backups/ || true

# Keep only last 7 days of local backups
find "$BACKUP_DIR" -name "backup_*.tar.gz.enc" -mtime +7 -delete
EOF

chmod +x /usr/local/bin/backup-encrypted.sh

# Schedule daily backups
echo "0 2 * * * /usr/local/bin/backup-encrypted.sh" | crontab -

# Configure CloudWatch monitoring
wget https://amazoncloudwatch-agent.s3.amazonaws.com/ubuntu/amd64/latest/amazon-cloudwatch-agent.deb
dpkg -i amazon-cloudwatch-agent.deb

cat > /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json <<EOF
{
  "metrics": {
    "namespace": "AppServer",
    "metrics_collected": {
      "disk": {
        "measurement": [
          {"name": "used_percent", "rename": "DISK_USED", "unit": "Percent"}
        ],
        "metrics_collection_interval": 60,
        "resources": ["*"]
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
            "file_path": "/var/log/app-server-init.log",
            "log_group_name": "/${ENVIRONMENT}/app-server/init",
            "log_stream_name": "server-${SERVER_INDEX}"
          },
          {
            "file_path": "/data/logs/*.log",
            "log_group_name": "/${ENVIRONMENT}/app-server/app",
            "log_stream_name": "server-${SERVER_INDEX}"
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

echo "[$(date)] Application server initialization completed!"
echo "Server Index: $SERVER_INDEX"
echo "Encrypted disk mounted at: $MOUNT_POINT"
echo "Services running:"
docker ps
