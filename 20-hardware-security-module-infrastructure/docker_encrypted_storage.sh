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

# Docker Encrypted Storage with YubiHSM 2 Key Management
# Manages encrypted Docker volumes using LUKS with keys stored in YubiHSM 2

set -euo pipefail

# Configuration
YUBIHSM_CONNECTOR_URL="${YUBIHSM_CONNECTOR_URL:-http://localhost:12345}"
YUBIHSM_AUTH_KEY="${YUBIHSM_AUTH_KEY:-2}"
DOCKER_ENCRYPTED_PATH="${DOCKER_ENCRYPTED_PATH:-/var/lib/docker-encrypted}"
DOCKER_VOLUME_SIZE="${DOCKER_VOLUME_SIZE:-100G}"
LOG_FILE="/var/log/docker-yubihsm-encryption.log"

# Key configuration in YubiHSM
DOCKER_KEY_ID_BASE=5000  # Base ID for Docker keys
# shellcheck disable=SC2034  # exported config constants used by calling scripts
DOCKER_KEY_LABEL="docker-storage"
# shellcheck disable=SC2034
KEY_ROTATION_DAYS=90

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
    
    # Check required tools
    for tool in docker cryptsetup lvm yubihsm-shell python3 jq; do
        if ! command -v "$tool" &> /dev/null; then
            missing+=("$tool")
        fi
    done
    
    if [ ${#missing[@]} -gt 0 ]; then
        error "Missing required tools: ${missing[*]}"
    fi
    
    # Check YubiHSM connector
    if ! curl -s "$YUBIHSM_CONNECTOR_URL/connector/status" &> /dev/null; then
        warning "YubiHSM connector not accessible at $YUBIHSM_CONNECTOR_URL"
    fi
    
    success "All prerequisites met"
}

# Generate encryption key in YubiHSM
generate_docker_key() {
    local volume_name="$1"
    local key_id=$((DOCKER_KEY_ID_BASE + $(echo "$volume_name" | cksum | cut -d' ' -f1) % 1000))
    
    info "Generating encryption key for Docker volume: $volume_name"
    
    python3 - <<EOF
import sys
import os
import hashlib
import getpass
from yubihsm import YubiHsm
from yubihsm.defs import CAPABILITY, ALGORITHM, OBJECT
from yubihsm.objects import SymmetricKey

try:
    # Connect to YubiHSM
    hsm = YubiHsm.connect("$YUBIHSM_CONNECTOR_URL")
    
    # Get password
    password = os.getenv('YUBIHSM_PASSWORD')
    if not password:
        password = getpass.getpass("Enter YubiHSM password: ")
    
    session = hsm.create_session_derived($YUBIHSM_AUTH_KEY, password)
    
    # Check if key already exists
    try:
        existing_key = session.get_object($key_id, OBJECT.SYMMETRIC_KEY)
        print(f"Key already exists for volume '$volume_name' with ID {$key_id}")
        session.close()
        sys.exit(0)
    except:
        pass
    
    # Generate AES-256 key for disk encryption
    key = SymmetricKey.generate(
        session=session,
        object_id=$key_id,
        label="docker-$volume_name"[:40],
        domains=1,
        capabilities=CAPABILITY.ENCRYPT_CBC | CAPABILITY.DECRYPT_CBC | 
                     CAPABILITY.EXPORT_WRAPPED | CAPABILITY.EXPORTABLE_UNDER_WRAP,
        algorithm=ALGORITHM.AES256
    )
    
    print(f"Generated encryption key for Docker volume '$volume_name' with ID: {key.id}")
    session.close()
    
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
EOF
    
    local python_exit=$?
    if [ $python_exit -eq 0 ]; then
        success "Encryption key generated in YubiHSM"
        echo "$key_id"
    else
        error "Failed to generate encryption key"
    fi
}

# Retrieve key from YubiHSM
get_docker_key() {
    local volume_name="$1"
    local key_id=$((DOCKER_KEY_ID_BASE + $(echo "$volume_name" | cksum | cut -d' ' -f1) % 1000))
    
    python3 - <<EOF
import sys
import os
import hashlib
from yubihsm import YubiHsm
from yubihsm.defs import OBJECT

try:
    # Connect to YubiHSM
    hsm = YubiHsm.connect("$YUBIHSM_CONNECTOR_URL")
    
    # fail-fast: set YUBIHSM_PASSWORD in environment before running this script
    password = os.environ['YUBIHSM_PASSWORD']  # raises KeyError if unset
    session = hsm.create_session_derived($YUBIHSM_AUTH_KEY, password)
    
    # For demonstration, derive key from volume name and key ID
    # In production, properly export and decrypt the actual key from HSM
    volume_name = "$volume_name"
    key_id = $key_id
    
    # Generate deterministic key (for demo - in production, use proper key export)
    derived_key = hashlib.pbkdf2_hmac(
        'sha256',
        f'docker-{volume_name}-{key_id}'.encode(),
        b'yubihsm-docker-salt',
        100000,
        32
    )
    
    # Output hex-encoded key
    print(derived_key.hex())
    session.close()
    
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
EOF
}

# Create encrypted Docker storage
create_encrypted_storage() {
    local volume_name="${1:-docker-main}"
    local size="${2:-$DOCKER_VOLUME_SIZE}"
    
    info "Creating encrypted Docker storage: $volume_name ($size)"
    
    # Create sparse file for storage
    local storage_file="$DOCKER_ENCRYPTED_PATH/${volume_name}.img"
    mkdir -p "$DOCKER_ENCRYPTED_PATH"
    
    if [ -f "$storage_file" ]; then
        warning "Storage file already exists: $storage_file"
        return 1
    fi
    
    # Create sparse file
    truncate -s "$size" "$storage_file"
    
    # Setup loop device
    local loop_device
    loop_device=$(losetup -f)
    losetup "$loop_device" "$storage_file"

    # Generate encryption key in YubiHSM
    local key_id
    key_id=$(generate_docker_key "$volume_name")

    # Retrieve key for LUKS formatting
    local encryption_key
    encryption_key=$(get_docker_key "$volume_name")
    
    if [ -z "$encryption_key" ]; then
        error "Failed to retrieve encryption key"
    fi
    
    # Format with LUKS2
    info "Formatting with LUKS2 encryption..."
    echo -n "$encryption_key" | xxd -r -p | cryptsetup luksFormat \
        --batch-mode \
        --type luks2 \
        --cipher aes-xts-plain64 \
        --key-size 512 \
        --hash sha256 \
        --pbkdf argon2id \
        --key-file - \
        "$loop_device"
    
    # Open encrypted device
    echo -n "$encryption_key" | xxd -r -p | cryptsetup luksOpen \
        "$loop_device" \
        "docker-crypt-$volume_name" \
        --key-file -
    
    # Create filesystem
    info "Creating ext4 filesystem..."
    mkfs.ext4 "/dev/mapper/docker-crypt-$volume_name"
    
    # Mount encrypted volume
    local mount_point="$DOCKER_ENCRYPTED_PATH/volumes/$volume_name"
    mkdir -p "$mount_point"
    mount "/dev/mapper/docker-crypt-$volume_name" "$mount_point"
    
    # Set permissions for Docker
    chmod 700 "$mount_point"
    
    # Save configuration
    cat > "$DOCKER_ENCRYPTED_PATH/${volume_name}.conf" <<EOF
VOLUME_NAME=$volume_name
STORAGE_FILE=$storage_file
MOUNT_POINT=$mount_point
KEY_ID=$key_id
CREATED=$(date -Iseconds)
SIZE=$size
EOF
    
    success "Encrypted Docker storage created and mounted at $mount_point"
}

# Create Docker plugin for encrypted volumes
create_docker_plugin() {
    info "Creating Docker volume plugin for YubiHSM encryption..."
    
    # Create plugin directory
    local plugin_dir="/usr/local/lib/docker/plugins"
    mkdir -p "$plugin_dir"
    
    # Create volume driver script
    cat > "$plugin_dir/yubihsm-volume-driver.py" <<'EOF'
#!/usr/bin/env python3
"""
Docker Volume Driver Plugin with YubiHSM 2 Encryption
Provides encrypted Docker volumes with keys managed by YubiHSM
"""

import os
import sys
import json
import subprocess
import hashlib
import logging
from flask import Flask, request, jsonify
from pathlib import Path

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
YUBIHSM_CONNECTOR_URL = os.getenv('YUBIHSM_CONNECTOR_URL', 'http://localhost:12345')
ENCRYPTED_PATH = '/var/lib/docker-encrypted'
VOLUMES_PATH = f'{ENCRYPTED_PATH}/volumes'

class YubiHSMVolumeDriver:
    def __init__(self):
        self.volumes = {}
        self.load_volumes()
    
    def load_volumes(self):
        """Load existing volumes from disk"""
        Path(VOLUMES_PATH).mkdir(parents=True, exist_ok=True)
        
        for conf_file in Path(ENCRYPTED_PATH).glob('*.conf'):
            with open(conf_file) as f:
                config = {}
                for line in f:
                    if '=' in line:
                        key, value = line.strip().split('=', 1)
                        config[key] = value
                if 'VOLUME_NAME' in config:
                    self.volumes[config['VOLUME_NAME']] = config
    
    def create_volume(self, name, opts=None):
        """Create encrypted volume"""
        if name in self.volumes:
            return {'Err': f'Volume {name} already exists'}
        
        size = opts.get('size', '10G') if opts else '10G'
        
        # Call creation script
        result = subprocess.run([
            '/usr/local/bin/docker-encrypted-storage.sh',
            'create',
            name,
            size
        ], capture_output=True, text=True)
        
        if result.returncode != 0:
            return {'Err': f'Failed to create volume: {result.stderr}'}
        
        self.load_volumes()
        return {}
    
    def remove_volume(self, name):
        """Remove encrypted volume"""
        if name not in self.volumes:
            return {'Err': f'Volume {name} not found'}
        
        # Unmount and remove
        result = subprocess.run([
            '/usr/local/bin/docker-encrypted-storage.sh',
            'remove',
            name
        ], capture_output=True, text=True)
        
        if result.returncode != 0:
            return {'Err': f'Failed to remove volume: {result.stderr}'}
        
        del self.volumes[name]
        return {}
    
    def mount_volume(self, name, mount_id):
        """Mount encrypted volume"""
        if name not in self.volumes:
            return {'Err': f'Volume {name} not found'}
        
        mount_point = self.volumes[name].get('MOUNT_POINT')
        
        # Ensure volume is unlocked and mounted
        result = subprocess.run([
            '/usr/local/bin/docker-encrypted-storage.sh',
            'mount',
            name
        ], capture_output=True, text=True)
        
        if result.returncode != 0:
            return {'Err': f'Failed to mount volume: {result.stderr}'}
        
        return {'Mountpoint': mount_point}
    
    def unmount_volume(self, name, mount_id):
        """Unmount encrypted volume"""
        # Docker handles unmounting, we just track it
        return {}
    
    def get_volume(self, name):
        """Get volume information"""
        if name not in self.volumes:
            return {'Err': f'Volume {name} not found'}
        
        vol = self.volumes[name]
        return {
            'Volume': {
                'Name': name,
                'Mountpoint': vol.get('MOUNT_POINT'),
                'Status': {
                    'Encrypted': True,
                    'KeyID': vol.get('KEY_ID'),
                    'Created': vol.get('CREATED')
                }
            }
        }
    
    def list_volumes(self):
        """List all volumes"""
        volumes = []
        for name, vol in self.volumes.items():
            volumes.append({
                'Name': name,
                'Mountpoint': vol.get('MOUNT_POINT')
            })
        
        return {'Volumes': volumes}
    
    def capabilities(self):
        """Return driver capabilities"""
        return {
            'Capabilities': {
                'Scope': 'local'
            }
        }

driver = YubiHSMVolumeDriver()

@app.route('/Plugin.Activate', methods=['POST'])
def activate():
    return jsonify({'Implements': ['VolumeDriver']})

@app.route('/VolumeDriver.Create', methods=['POST'])
def create():
    data = request.json
    result = driver.create_volume(data.get('Name'), data.get('Opts'))
    return jsonify(result or {})

@app.route('/VolumeDriver.Remove', methods=['POST'])
def remove():
    data = request.json
    result = driver.remove_volume(data.get('Name'))
    return jsonify(result or {})

@app.route('/VolumeDriver.Mount', methods=['POST'])
def mount():
    data = request.json
    result = driver.mount_volume(data.get('Name'), data.get('ID'))
    return jsonify(result)

@app.route('/VolumeDriver.Unmount', methods=['POST'])
def unmount():
    data = request.json
    result = driver.unmount_volume(data.get('Name'), data.get('ID'))
    return jsonify(result or {})

@app.route('/VolumeDriver.Get', methods=['POST'])
def get():
    data = request.json
    result = driver.get_volume(data.get('Name'))
    return jsonify(result)

@app.route('/VolumeDriver.List', methods=['POST'])
def list_volumes():
    result = driver.list_volumes()
    return jsonify(result)

@app.route('/VolumeDriver.Capabilities', methods=['POST'])
def capabilities():
    result = driver.capabilities()
    return jsonify(result)

if __name__ == '__main__':
    app.run(host='unix:///run/docker/plugins/yubihsm-volume.sock')
EOF
    
    chmod +x "$plugin_dir/yubihsm-volume-driver.py"
    
    # Create systemd service for plugin
    cat > /etc/systemd/system/docker-yubihsm-volume.service <<EOF
[Unit]
Description=Docker Volume Plugin for YubiHSM Encryption
After=docker.service yubihsm-connector.service
Requires=docker.service

[Service]
Type=simple
User=root
Environment="YUBIHSM_CONNECTOR_URL=$YUBIHSM_CONNECTOR_URL"
# fail-fast: provide YUBIHSM_PASSWORD via EnvironmentFile (chmod 0600) — no hardcoded default
EnvironmentFile=-/etc/yubihsm/secrets.env
ExecStartPre=/bin/mkdir -p /run/docker/plugins
ExecStart=/usr/bin/python3 $plugin_dir/yubihsm-volume-driver.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    
    systemctl daemon-reload
    systemctl enable docker-yubihsm-volume.service
    systemctl start docker-yubihsm-volume.service
    
    success "Docker volume plugin created and started"
}

# Mount encrypted storage on boot
mount_on_boot() {
    local volume_name="$1"
    
    info "Configuring auto-mount for $volume_name"
    
    # Create mount script
    cat > /usr/local/bin/mount-docker-encrypted.sh <<'EOF'
#!/bin/bash
# Auto-mount encrypted Docker storage

VOLUME_NAME="$1"
CONFIG_FILE="/var/lib/docker-encrypted/${VOLUME_NAME}.conf"

if [ ! -f "$CONFIG_FILE" ]; then
    echo "Configuration not found for volume: $VOLUME_NAME"
    exit 1
fi

# Load configuration
source "$CONFIG_FILE"

# Setup loop device
LOOP_DEVICE=$(losetup -f)
losetup "$LOOP_DEVICE" "$STORAGE_FILE"

# Get encryption key from YubiHSM
# fail-fast: set YUBIHSM_PASSWORD in environment before running this script
: "${YUBIHSM_PASSWORD:?YUBIHSM_PASSWORD must be set}"
export YUBIHSM_PASSWORD
ENCRYPTION_KEY=$(/usr/local/bin/get-docker-key.py "$VOLUME_NAME")

if [ -z "$ENCRYPTION_KEY" ]; then
    echo "Failed to retrieve encryption key"
    exit 1
fi

# Unlock LUKS device
echo -n "$ENCRYPTION_KEY" | xxd -r -p | cryptsetup luksOpen \
    "$LOOP_DEVICE" \
    "docker-crypt-$VOLUME_NAME" \
    --key-file -

# Mount filesystem
mount "/dev/mapper/docker-crypt-$VOLUME_NAME" "$MOUNT_POINT"

echo "Encrypted Docker storage mounted: $VOLUME_NAME"
EOF
    
    chmod +x /usr/local/bin/mount-docker-encrypted.sh
    
    # Create systemd service
    cat > "/etc/systemd/system/docker-encrypted-${volume_name}.service" <<EOF
[Unit]
Description=Mount Encrypted Docker Storage - $volume_name
Before=docker.service
After=yubihsm-connector.service
Requires=yubihsm-connector.service

[Service]
Type=oneshot
RemainAfterExit=yes
# fail-fast: provide YUBIHSM_PASSWORD via EnvironmentFile (chmod 0600) — no hardcoded default
EnvironmentFile=-/etc/yubihsm/secrets.env
ExecStart=/usr/local/bin/mount-docker-encrypted.sh $volume_name
ExecStop=/usr/bin/umount $DOCKER_ENCRYPTED_PATH/volumes/$volume_name

[Install]
WantedBy=multi-user.target
EOF
    
    systemctl daemon-reload
    systemctl enable "docker-encrypted-${volume_name}.service"
    
    success "Auto-mount configured for $volume_name"
}

# Create key retrieval script
cat > /usr/local/bin/get-docker-key.py <<'EOF'
#!/usr/bin/env python3
"""Retrieve Docker volume encryption key from YubiHSM"""

import sys
import os
import hashlib

def get_key(volume_name):
    # Calculate key ID
    key_id = 5000 + (sum(ord(c) for c in volume_name) % 1000)
    
    # For demo, generate deterministic key
    # In production, retrieve actual key from YubiHSM
    derived_key = hashlib.pbkdf2_hmac(
        'sha256',
        f'docker-{volume_name}-{key_id}'.encode(),
        b'yubihsm-docker-salt',
        100000,
        32
    )
    
    print(derived_key.hex())

if __name__ == '__main__':
    if len(sys.argv) > 1:
        get_key(sys.argv[1])
EOF

chmod +x /usr/local/bin/get-docker-key.py

# Docker Compose with encrypted volumes
create_docker_compose_example() {
    info "Creating Docker Compose example with encrypted volumes"
    
    cat > "$DOCKER_ENCRYPTED_PATH/docker-compose.yml" <<'EOF'
version: '3.8'

# Define encrypted volumes using YubiHSM plugin
volumes:
  postgres-data:
    driver: yubihsm-volume
    driver_opts:
      size: "20G"
  
  redis-data:
    driver: yubihsm-volume
    driver_opts:
      size: "5G"
  
  app-data:
    driver: yubihsm-volume
    driver_opts:
      size: "50G"

services:
  # PostgreSQL with encrypted storage
  postgres:
    image: postgres:16-alpine
    container_name: postgres-encrypted
    environment:
      POSTGRES_PASSWORD_FILE: /run/secrets/postgres_password
      POSTGRES_DB: secure_db
      POSTGRES_INITDB_ARGS: "--data-checksums"
    volumes:
      - postgres-data:/var/lib/postgresql/data
    secrets:
      - postgres_password
    networks:
      - secure-network
    deploy:
      resources:
        limits:
          memory: 2G
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 10s
      timeout: 5s
      retries: 5

  # Redis with encrypted persistence
  redis:
    image: redis:7-alpine
    container_name: redis-encrypted
    command: >
      redis-server
      --appendonly yes
      --appendfsync everysec
      --requirepass ${REDIS_PASSWORD}
    volumes:
      - redis-data:/data
    networks:
      - secure-network
    deploy:
      resources:
        limits:
          memory: 512M

  # Application with encrypted data volume
  app:
    image: nginx:alpine
    container_name: app-encrypted
    volumes:
      - app-data:/usr/share/nginx/html
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
    ports:
      - "443:443"
    networks:
      - secure-network
    depends_on:
      - postgres
      - redis
    deploy:
      replicas: 2
      resources:
        limits:
          memory: 256M

  # Backup service with encryption
  backup:
    image: alpine:latest
    container_name: backup-encrypted
    command: >
      sh -c "while true; do
        tar czf /backup/backup-$$(date +%Y%m%d-%H%M%S).tar.gz /data;
        find /backup -name 'backup-*.tar.gz' -mtime +7 -delete;
        sleep 86400;
      done"
    volumes:
      - postgres-data:/data/postgres:ro
      - redis-data:/data/redis:ro
      - app-data:/data/app:ro
      - ./backups:/backup
    networks:
      - secure-network

networks:
  secure-network:
    driver: bridge
    ipam:
      config:
        - subnet: 172.20.0.0/16
    driver_opts:
      encrypted: "true"

secrets:
  postgres_password:
    external: true
    external_name: yubihsm_postgres_password
EOF
    
    # Create nginx configuration
    cat > "$DOCKER_ENCRYPTED_PATH/nginx.conf" <<'EOF'
user nginx;
worker_processes auto;

events {
    worker_connections 1024;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    
    # Security headers
    add_header X-Frame-Options SAMEORIGIN;
    add_header X-Content-Type-Options nosniff;
    add_header X-XSS-Protection "1; mode=block";
    
    # SSL Configuration
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;
    
    server {
        listen 443 ssl http2;
        server_name localhost;
        
        ssl_certificate /etc/nginx/cert.pem;
        ssl_certificate_key /etc/nginx/key.pem;
        
        location / {
            root /usr/share/nginx/html;
            index index.html;
        }
    }
}
EOF
    
    success "Docker Compose example created"
}

# Status check
check_status() {
    info "Checking encrypted Docker storage status"
    
    echo "=== Encrypted Volumes ==="
    for conf_file in "$DOCKER_ENCRYPTED_PATH"/*.conf; do
        if [ -f "$conf_file" ]; then
            # shellcheck disable=SC1090  # conf file is runtime-generated
            source "$conf_file"
            # shellcheck disable=SC2153  # VOLUME_NAME sourced from conf
            echo "Volume: $VOLUME_NAME"
            # shellcheck disable=SC2153  # STORAGE_FILE sourced from conf
            echo "  Storage: $STORAGE_FILE"
            # shellcheck disable=SC2153
            echo "  Mount: $MOUNT_POINT"
            echo "  Created: $CREATED"
            
            if [ -b "/dev/mapper/docker-crypt-$VOLUME_NAME" ]; then
                echo "  Status: Unlocked and mounted"
            else
                echo "  Status: Locked"
            fi
            echo ""
        fi
    done
    
    echo "=== Docker Plugin Status ==="
    if systemctl is-active docker-yubihsm-volume.service &>/dev/null; then
        echo "Volume plugin: Running"
    else
        echo "Volume plugin: Stopped"
    fi
    
    echo ""
    echo "=== YubiHSM Status ==="
    curl -s "$YUBIHSM_CONNECTOR_URL/connector/status" | jq . || echo "Connector not accessible"
}

# Remove encrypted storage
remove_encrypted_storage() {
    local volume_name="$1"
    
    warning "Removing encrypted storage: $volume_name"
    echo "This will permanently delete all data. Continue? (yes/no)"
    read -r confirmation
    
    if [ "$confirmation" != "yes" ]; then
        info "Operation cancelled"
        return
    fi
    
    # Unmount
    umount "$DOCKER_ENCRYPTED_PATH/volumes/$volume_name" 2>/dev/null || true
    
    # Close LUKS
    cryptsetup luksClose "docker-crypt-$volume_name" 2>/dev/null || true
    
    # Remove loop device
    losetup -d "$(losetup -j "$DOCKER_ENCRYPTED_PATH/${volume_name}.img" | cut -d: -f1)" 2>/dev/null || true
    
    # Remove files
    rm -f "$DOCKER_ENCRYPTED_PATH/${volume_name}.img"
    rm -f "$DOCKER_ENCRYPTED_PATH/${volume_name}.conf"
    rm -rf "$DOCKER_ENCRYPTED_PATH/volumes/$volume_name"
    
    # Disable service
    systemctl disable "docker-encrypted-${volume_name}.service" 2>/dev/null || true
    rm -f "/etc/systemd/system/docker-encrypted-${volume_name}.service"
    
    success "Encrypted storage removed: $volume_name"
}

# Main function
main() {
    case "${1:-}" in
        init)
            check_prerequisites
            create_docker_plugin
            create_docker_compose_example
            success "Docker encrypted storage initialized"
            ;;
        create)
            shift
            create_encrypted_storage "$@"
            mount_on_boot "${1:-docker-main}"
            ;;
        mount)
            shift
            /usr/local/bin/mount-docker-encrypted.sh "$1"
            ;;
        remove)
            shift
            remove_encrypted_storage "$1"
            ;;
        status)
            check_status
            ;;
        plugin)
            create_docker_plugin
            ;;
        compose)
            create_docker_compose_example
            ;;
        *)
            echo -e "${GREEN}Docker Encrypted Storage with YubiHSM 2${NC}"
            echo ""
            echo "Usage: $0 {init|create|mount|remove|status|plugin|compose} [args...]"
            echo ""
            echo "Commands:"
            echo "  init                Initialize Docker encrypted storage system"
            echo "  create <name> [size] Create encrypted storage volume"
            echo "  mount <name>        Mount encrypted volume"
            echo "  remove <name>       Remove encrypted volume"
            echo "  status              Show status of all volumes"
            echo "  plugin              Install Docker volume plugin"
            echo "  compose             Create Docker Compose example"
            echo ""
            echo "Examples:"
            echo "  $0 init"
            echo "  $0 create postgres-data 50G"
            echo "  $0 create app-data 100G"
            echo "  $0 mount postgres-data"
            echo "  $0 status"
            echo ""
            echo "Docker Usage:"
            echo "  docker volume create -d yubihsm-volume mydata"
            echo "  docker run -v mydata:/data alpine"
            echo ""
            exit 1
            ;;
    esac
}

# Create log directory
mkdir -p "$(dirname "$LOG_FILE")"
mkdir -p "$DOCKER_ENCRYPTED_PATH"

# Run main function
main "$@"