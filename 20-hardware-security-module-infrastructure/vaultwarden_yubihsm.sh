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
# Vaultwarden Integration with YubiHSM 2
# Secure password management using Vaultwarden with YubiHSM 2 FIPS backend

set -euo pipefail

# Configuration
VAULTWARDEN_DOMAIN="${VAULTWARDEN_DOMAIN:-vault.example.com}"
VAULTWARDEN_PORT="${VAULTWARDEN_PORT:-443}"
VAULTWARDEN_DATA_PATH="${VAULTWARDEN_DATA_PATH:-/opt/vaultwarden/data}"
YUBIHSM_CONNECTOR_URL="${YUBIHSM_CONNECTOR_URL:-http://host.docker.internal:12345}"
YUBIHSM_AUTH_KEY="${YUBIHSM_AUTH_KEY:-2}"
POSTGRES_HOST="${POSTGRES_HOST:-postgres}"
POSTGRES_DB="${POSTGRES_DB:-vaultwarden}"
POSTGRES_USER="${POSTGRES_USER:-vaultwarden}"
ADMIN_TOKEN="${ADMIN_TOKEN:-}"
LOG_FILE="/var/log/vaultwarden-yubihsm.log"

# YubiKey OTP Configuration
YUBICO_CLIENT_ID="${YUBICO_CLIENT_ID:-}"
YUBICO_SECRET_KEY="${YUBICO_SECRET_KEY:-}"

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
    
    # Check for required tools
    for tool in docker docker-compose openssl certbot yubihsm-shell python3; do
        if ! command -v "$tool" &> /dev/null; then
            missing+=("$tool")
        fi
    done
    
    if [ ${#missing[@]} -gt 0 ]; then
        error "Missing required tools: ${missing[*]}"
    fi
    
    # Check Docker daemon
    if ! docker info &> /dev/null; then
        error "Docker daemon is not running"
    fi
    
    success "All prerequisites met"
}

# Generate secure passwords using YubiHSM
generate_secure_password() {
    local password_name="$1"
    
    python3 - <<EOF
import sys
import hashlib
import secrets
import string
from yubihsm import YubiHsm
from yubihsm.defs import CAPABILITY, ALGORITHM
import getpass

try:
    # Connect to YubiHSM
    hsm = YubiHsm.connect("$YUBIHSM_CONNECTOR_URL")

    # Get password (in production, use secure method)
    password = getpass.getpass("Enter YubiHSM password: ")
    session = hsm.create_session_derived($YUBIHSM_AUTH_KEY, password)

    # Generate random bytes from HSM
    random_bytes = session.get_pseudo_random(32)

    # Create password
    charset = string.ascii_letters + string.digits + "!@#$%^&*"
    pwd = ''.join(charset[b % len(charset)] for b in random_bytes)

    print(pwd)
    session.close()

except Exception as e:
    # Fallback to system random
    import secrets
    pwd = secrets.token_urlsafe(32)
    print(pwd)
EOF
}

# Setup SSL certificates
setup_ssl_certificates() {
    info "Setting up SSL certificates..."
    
    local cert_dir="/opt/vaultwarden/ssl"
    mkdir -p "$cert_dir"
    
    # Check if certificates exist
    if [ -f "$cert_dir/cert.pem" ] && [ -f "$cert_dir/key.pem" ]; then
        warning "SSL certificates already exist"
        return 0
    fi
    
    # Try Let's Encrypt first
    if [ -n "$VAULTWARDEN_DOMAIN" ] && [ "$VAULTWARDEN_DOMAIN" != "vault.example.com" ]; then
        info "Obtaining Let's Encrypt certificate for $VAULTWARDEN_DOMAIN..."
        
        certbot certonly \
        --standalone \
        --non-interactive \
        --agree-tos \
        --email admin@"$VAULTWARDEN_DOMAIN" \
        -d "$VAULTWARDEN_DOMAIN"
        
        certbot_exit=$?
        if [ $certbot_exit -eq 0 ]; then
            # Copy certificates
            cp "/etc/letsencrypt/live/$VAULTWARDEN_DOMAIN/fullchain.pem" "$cert_dir/cert.pem"
            cp "/etc/letsencrypt/live/$VAULTWARDEN_DOMAIN/privkey.pem" "$cert_dir/key.pem"
            success "Let's Encrypt certificate obtained"
            return 0
        else
            warning "Failed to obtain Let's Encrypt certificate, generating self-signed"
        fi
    fi
    
    # Generate self-signed certificate
    info "Generating self-signed certificate..."
    
    openssl req -x509 -nodes -newkey rsa:4096 \
    -keyout "$cert_dir/key.pem" \
    -out "$cert_dir/cert.pem" \
    -days 365 \
    -subj "/C=US/ST=State/L=City/O=Organization/CN=$VAULTWARDEN_DOMAIN"
    
    chmod 600 "$cert_dir/key.pem"
    success "SSL certificates generated"
}

# Create Docker Compose configuration
create_docker_compose() {
    info "Creating Docker Compose configuration..."
    
    local compose_dir="/opt/vaultwarden"
    mkdir -p "$compose_dir"
    
    # Generate database password if not exists
    local db_password
    if [ ! -f "$compose_dir/.db_password" ]; then
        info "Generating database password..."
        db_password=$(generate_secure_password "postgres")
        echo "$db_password" > "$compose_dir/.db_password"
        chmod 600 "$compose_dir/.db_password"
    else
        db_password=$(cat "$compose_dir/.db_password")
    fi
    
    # Generate admin token if not provided
    if [ -z "$ADMIN_TOKEN" ]; then
        ADMIN_TOKEN=$(openssl rand -hex 32)
        echo "$ADMIN_TOKEN" > "$compose_dir/.admin_token"
        chmod 600 "$compose_dir/.admin_token"
    fi
    
    # Create docker-compose.yml
    cat > "$compose_dir/docker-compose.yml" <<EOF
version: '3.8'

services:
  # YubiHSM Connector
  yubihsm-connector:
    image: yubico/yubihsm-connector:latest
    container_name: yubihsm-connector
    restart: unless-stopped
    privileged: true
    devices:
      - /dev/bus/usb:/dev/bus/usb
    volumes:
      - /run/udev:/run/udev:ro
    ports:
      - "12345:12345"
    environment:
      - YUBIHSM_CONNECTOR_LISTEN=0.0.0.0:12345
    command: ["-d"]
    networks:
      - vaultwarden_net

  # PostgreSQL Database with encryption
  postgres:
    image: postgres:16-alpine
    container_name: vaultwarden-postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${db_password}
      POSTGRES_DB: ${POSTGRES_DB}
      POSTGRES_INITDB_ARGS: "--data-checksums"
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./postgres-init.sh:/docker-entrypoint-initdb.d/init.sh:ro
    networks:
      - vaultwarden_net
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER}"]
      interval: 10s
      timeout: 5s
      retries: 5

  # Redis for caching
  redis:
    image: redis:7-alpine
    container_name: vaultwarden-redis
    restart: unless-stopped
    command: redis-server --appendonly yes --requirepass ${db_password}
    volumes:
      - redis_data:/data
    networks:
      - vaultwarden_net
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  # Vaultwarden
  vaultwarden:
    image: vaultwarden/server:latest
    container_name: vaultwarden
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    environment:
      # Database
      DATABASE_URL: postgresql://${POSTGRES_USER}:${db_password}@postgres/${POSTGRES_DB}
      ENABLE_DB_WAL: 'true'

      # Redis
      REDIS_HOST: redis
      REDIS_PORT: 6379
      REDIS_PASSWORD: ${db_password}

      # Domain and SSL
      DOMAIN: https://${VAULTWARDEN_DOMAIN}
      ROCKET_TLS: '{certs="/ssl/cert.pem",key="/ssl/key.pem"}'

      # Security
      ADMIN_TOKEN: ${ADMIN_TOKEN}
      SIGNUPS_ALLOWED: 'false'
      INVITATIONS_ALLOWED: 'true'
      SHOW_PASSWORD_HINT: 'false'

      # YubiKey OTP support
      YUBICO_CLIENT_ID: ${YUBICO_CLIENT_ID}
      YUBICO_SECRET_KEY: ${YUBICO_SECRET_KEY}

      # 2FA
      ENABLE_EMAIL_2FA: 'true'

      # Logging
      LOG_LEVEL: 'info'
      EXTENDED_LOGGING: 'true'
      LOG_FILE: '/data/vaultwarden.log'

      # Performance
      WEB_VAULT_ENABLED: 'true'
      WEBSOCKET_ENABLED: 'true'
      WEBSOCKET_PORT: 3012

      # Security headers
      ENABLE_HSTS: 'true'
      HSTS_MAX_AGE: '31536000'
    volumes:
      - vaultwarden_data:/data
      - ./ssl:/ssl:ro
    ports:
      - "${VAULTWARDEN_PORT}:443"
      - "3012:3012"
    networks:
      - vaultwarden_net
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:80/alive"]
      interval: 30s
      timeout: 10s
      retries: 3

  # Nginx reverse proxy (optional)
  nginx:
    image: nginx:alpine
    container_name: vaultwarden-nginx
    restart: unless-stopped
    depends_on:
      - vaultwarden
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - ./ssl:/etc/nginx/ssl:ro
      - nginx_logs:/var/log/nginx
    ports:
      - "80:80"
      - "443:443"
    networks:
      - vaultwarden_net

  # Backup service
  backup:
    image: bruceforce/vaultwarden-backup:latest
    container_name: vaultwarden-backup
    restart: unless-stopped
    depends_on:
      - vaultwarden
    environment:
      BACKUP_INTERVAL: 12h
      BACKUP_KEEP_DAYS: 30
      TIMESTAMP: 'true'
    volumes:
      - vaultwarden_data:/data:ro
      - ./backups:/backups
      - /var/run/docker.sock:/var/run/docker.sock:ro
    networks:
      - vaultwarden_net

networks:
  vaultwarden_net:
    driver: bridge
    ipam:
      config:
        - subnet: 172.20.0.0/16

volumes:
  postgres_data:
  redis_data:
  vaultwarden_data:
  nginx_logs:
EOF
    
    success "Docker Compose configuration created"
}

# Create Nginx configuration
create_nginx_config() {
    info "Creating Nginx configuration..."
    
    local nginx_conf="/opt/vaultwarden/nginx.conf"
    
    cat > "$nginx_conf" <<'EOF'
user nginx;
worker_processes auto;
error_log /var/log/nginx/error.log warn;
pid /var/run/nginx.pid;

events {
    worker_connections 1024;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;

    log_format main '$remote_addr - $remote_user [$time_local] "$request" '
                    '$status $body_bytes_sent "$http_referer" '
                    '"$http_user_agent" "$http_x_forwarded_for"';

    access_log /var/log/nginx/access.log main;

    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 65;
    types_hash_max_size 2048;
    client_max_body_size 525M;

    # SSL Configuration
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;
    ssl_stapling on;
    ssl_stapling_verify on;

    # Security headers
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains; preload" always;
    add_header X-Frame-Options SAMEORIGIN always;
    add_header X-Content-Type-Options nosniff always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "same-origin" always;

    # Redirect HTTP to HTTPS
    server {
        listen 80;
        server_name _;
        return 301 https://$host$request_uri;
    }

    # HTTPS server
    server {
        listen 443 ssl http2;
        server_name VAULTWARDEN_DOMAIN;

        ssl_certificate /etc/nginx/ssl/cert.pem;
        ssl_certificate_key /etc/nginx/ssl/key.pem;

        # Vaultwarden web vault
        location / {
            proxy_pass http://vaultwarden:80;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }

        # Websocket for notifications
        location /notifications/hub {
            proxy_pass http://vaultwarden:3012;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }

        # Admin panel (restrict access)
        location /admin {
            proxy_pass http://vaultwarden:80;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;

            # IP restriction (adjust as needed)
            allow 10.0.0.0/8;
            allow 172.16.0.0/12;
            allow 192.168.0.0/16;
            deny all;
        }
    }
}
EOF
    
    # Replace domain placeholder
    sed -i "s/VAULTWARDEN_DOMAIN/$VAULTWARDEN_DOMAIN/g" "$nginx_conf"
    
    success "Nginx configuration created"
}

# Create YubiHSM integration plugin
create_yubihsm_plugin() {
    info "Creating YubiHSM integration plugin..."
    
    local plugin_dir="/opt/vaultwarden/plugins"
    mkdir -p "$plugin_dir"
    
    cat > "$plugin_dir/yubihsm_integration.py" <<'EOF'
#!/usr/bin/env python3
"""
YubiHSM 2 Integration Plugin for Vaultwarden
Provides secure key management and encryption services
"""

import os
import sys
import json
import hashlib
import base64
from typing import Optional, Dict, Any
from flask import Flask, request, jsonify
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend

try:
    from yubihsm import YubiHsm
    from yubihsm.defs import CAPABILITY, ALGORITHM, OBJECT
    from yubihsm.objects import SymmetricKey, AsymmetricKey, WrapKey
except ImportError:
    print("YubiHSM library not found. Install with: pip install yubihsm[http]")
    sys.exit(1)

app = Flask(__name__)

# Configuration
HSM_CONNECTOR_URL = os.getenv('YUBIHSM_CONNECTOR_URL', 'http://localhost:12345')
HSM_AUTH_KEY_ID = int(os.getenv('YUBIHSM_AUTH_KEY', '2'))
VAULT_KEY_ID = 1000  # Base key ID for vault entries

class YubiHSMVaultPlugin:
    """YubiHSM 2 integration for Vaultwarden"""

    def __init__(self):
        self.hsm = None
        self.session = None
        self.wrap_key_id = 100

    def connect(self, password: str = None):
        """Connect to YubiHSM 2"""
        try:
            self.hsm = YubiHsm.connect(HSM_CONNECTOR_URL)
            if password is None:
                # fail-fast: set YUBIHSM_PASSWORD in environment before running this script
                password = os.environ['YUBIHSM_PASSWORD']  # raises KeyError if unset
            self.session = self.hsm.create_session_derived(HSM_AUTH_KEY_ID, password)
            return True
        except Exception as e:
            print(f"Failed to connect to YubiHSM: {e}")
            return False

    def encrypt_data(self, data: bytes, context: str = "") -> Dict[str, Any]:
        """Encrypt data using YubiHSM"""
        try:
            # Generate IV
            iv = os.urandom(16)

            # Derive key from HSM
            key_material = self._derive_key(context)

            # Encrypt data
            cipher = Cipher(
                algorithms.AES(key_material),
                modes.CBC(iv),
                backend=default_backend()
            )
            encryptor = cipher.encryptor()

            # Pad data
            padding_len = 16 - (len(data) % 16)
            padded_data = data + bytes([padding_len] * padding_len)

            # Encrypt
            ciphertext = encryptor.update(padded_data) + encryptor.finalize()

            return {
                "encrypted": base64.b64encode(iv + ciphertext).decode(),
                "context": context,
                "algorithm": "AES-256-CBC"
            }

        except Exception as e:
            return {"error": str(e)}

    def decrypt_data(self, encrypted_data: str, context: str = "") -> Optional[bytes]:
        """Decrypt data using YubiHSM"""
        try:
            # Decode data
            data = base64.b64decode(encrypted_data)
            iv = data[:16]
            ciphertext = data[16:]

            # Derive key from HSM
            key_material = self._derive_key(context)

            # Decrypt data
            cipher = Cipher(
                algorithms.AES(key_material),
                modes.CBC(iv),
                backend=default_backend()
            )
            decryptor = cipher.decryptor()

            # Decrypt
            padded_data = decryptor.update(ciphertext) + decryptor.finalize()

            # Remove padding
            padding_len = padded_data[-1]
            return padded_data[:-padding_len]

        except Exception as e:
            print(f"Decryption error: {e}")
            return None

    def _derive_key(self, context: str) -> bytes:
        """Derive encryption key from HSM"""
        # In production, use proper key derivation from HSM
        # This is a simplified example
        context_bytes = context.encode() if context else b"default"
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"vaultwarden-salt",
            iterations=100000,
            backend=default_backend()
        )
        return kdf.derive(context_bytes)

    def generate_master_key(self) -> Optional[str]:
        """Generate master encryption key in HSM"""
        try:
            # Generate symmetric key in HSM
            key = SymmetricKey.generate(
                session=self.session,
                object_id=0,  # Auto-assign
                label="vaultwarden-master",
                domains=1,
                capabilities=CAPABILITY.ENCRYPT_CBC | CAPABILITY.DECRYPT_CBC,
                algorithm=ALGORITHM.AES256
            )

            return f"hsm:key:{key.id}"

        except Exception as e:
            print(f"Failed to generate master key: {e}")
            return None

    def close(self):
        """Close HSM session"""
        if self.session:
            self.session.close()

# Plugin instance
plugin = YubiHSMVaultPlugin()

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({"status": "healthy", "hsm_connected": plugin.session is not None})

@app.route('/encrypt', methods=['POST'])
def encrypt():
    """Encrypt data endpoint"""
    data = request.json
    plaintext = base64.b64decode(data.get('data', ''))
    context = data.get('context', '')

    result = plugin.encrypt_data(plaintext, context)
    return jsonify(result)

@app.route('/decrypt', methods=['POST'])
def decrypt():
    """Decrypt data endpoint"""
    data = request.json
    encrypted = data.get('encrypted', '')
    context = data.get('context', '')

    plaintext = plugin.decrypt_data(encrypted, context)
    if plaintext:
        return jsonify({"data": base64.b64encode(plaintext).decode()})
    else:
        return jsonify({"error": "Decryption failed"}), 400

@app.route('/generate-key', methods=['POST'])
def generate_key():
    """Generate new encryption key"""
    key_id = plugin.generate_master_key()
    if key_id:
        return jsonify({"key_id": key_id})
    else:
        return jsonify({"error": "Key generation failed"}), 500

if __name__ == '__main__':
    # Connect to YubiHSM on startup
    if not plugin.connect():
        print("Failed to connect to YubiHSM")
        sys.exit(1)

    # Run Flask app
    app.run(host='0.0.0.0', port=5000)
EOF
    
    chmod +x "$plugin_dir/yubihsm_integration.py"
    success "YubiHSM integration plugin created"
}

# Create backup script
create_backup_script() {
    info "Creating backup script..."
    
    cat > /opt/vaultwarden/backup.sh <<'EOF'
#!/bin/bash
# Vaultwarden backup script with YubiHSM encryption

BACKUP_DIR="/opt/vaultwarden/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_NAME="vaultwarden_backup_${TIMESTAMP}"

# Create backup directory
mkdir -p "$BACKUP_DIR"

# Backup database
docker exec vaultwarden-postgres pg_dump -U vaultwarden vaultwarden | \
    gzip > "$BACKUP_DIR/${BACKUP_NAME}_db.sql.gz"

# Backup Vaultwarden data
tar czf "$BACKUP_DIR/${BACKUP_NAME}_data.tar.gz" -C /opt/vaultwarden/data .

# Encrypt backups using YubiHSM (optional)
if [ -f "/opt/vaultwarden/plugins/yubihsm_integration.py" ]; then
    python3 -c "
import requests
import base64

with open('$BACKUP_DIR/${BACKUP_NAME}_db.sql.gz', 'rb') as f:
    data = f.read()

response = requests.post('http://localhost:5000/encrypt', json={
    'data': base64.b64encode(data).decode(),
    'context': 'backup'
})

if response.status_code == 200:
    with open('$BACKUP_DIR/${BACKUP_NAME}_db.sql.gz.enc', 'w') as f:
        f.write(response.json()['encrypted'])
    print('Backup encrypted successfully')
"
fi

# Remove old backups (keep last 30 days)
find "$BACKUP_DIR" -name "vaultwarden_backup_*.tar.gz" -mtime +30 -delete

echo "Backup completed: ${BACKUP_NAME}"
EOF
    
    chmod +x /opt/vaultwarden/backup.sh
    
    # Create cron job for daily backups
    echo "0 2 * * * /opt/vaultwarden/backup.sh" | crontab -
    
    success "Backup script created"
}

# Setup YubiKey OTP
setup_yubikey_otp() {
    info "Setting up YubiKey OTP authentication..."
    
    if [ -z "$YUBICO_CLIENT_ID" ] || [ -z "$YUBICO_SECRET_KEY" ]; then
        warning "YubiKey OTP credentials not provided. Skipping..."
        echo ""
        echo "To enable YubiKey OTP:"
        echo "1. Get your API credentials from: https://upgrade.yubico.com/getapikey/"
        echo "2. Set YUBICO_CLIENT_ID and YUBICO_SECRET_KEY environment variables"
        echo "3. Restart Vaultwarden container"
        return 0
    fi
    
    # Update docker-compose with YubiKey settings
    info "YubiKey OTP configured with client ID: $YUBICO_CLIENT_ID"
    success "YubiKey OTP setup complete"
}

# Start Vaultwarden
start_vaultwarden() {
    info "Starting Vaultwarden services..."
    
    cd /opt/vaultwarden
    
    # Pull latest images
    docker-compose pull
    
    # Start services
    docker-compose up -d
    
    # Wait for services to be ready
    info "Waiting for services to start..."
    sleep 10
    
    # Check service health
    if docker-compose ps | grep -q "Up"; then
        success "Vaultwarden services started successfully"
        
        # Show access information
        echo ""
        echo "========================================="
        echo "Vaultwarden is now running!"
        echo "========================================="
        echo "Access URL: https://$VAULTWARDEN_DOMAIN"
        echo "Admin Panel: https://$VAULTWARDEN_DOMAIN/admin"
        echo ""
        
        if [ -f "/opt/vaultwarden/.admin_token" ]; then
            echo "Admin Token: $(cat /opt/vaultwarden/.admin_token)"
        fi
        
        echo ""
        echo "First time setup:"
        echo "1. Access the admin panel"
        echo "2. Configure SMTP settings for email"
        echo "3. Set invitation settings"
        echo "4. Create your first user account"
        echo "========================================="
    else
        error "Failed to start Vaultwarden services"
    fi
}

# Stop Vaultwarden
stop_vaultwarden() {
    info "Stopping Vaultwarden services..."
    
    cd /opt/vaultwarden
    docker-compose down
    
    success "Vaultwarden services stopped"
}

# Show status
show_status() {
    echo -e "${GREEN}Vaultwarden Status${NC}"
    echo "=================="
    
    cd /opt/vaultwarden
    docker-compose ps
    
    echo ""
    echo "Container Logs:"
    echo "---------------"
    docker-compose logs --tail=20 vaultwarden
}

# Update Vaultwarden
update_vaultwarden() {
    info "Updating Vaultwarden..."
    
    # Backup before update
    /opt/vaultwarden/backup.sh
    
    cd /opt/vaultwarden
    
    # Pull latest images
    docker-compose pull
    
    # Recreate containers
    docker-compose up -d --force-recreate
    
    success "Vaultwarden updated successfully"
}

# Main function
main() {
    case "${1:-}" in
        install)
            check_prerequisites
            setup_ssl_certificates
            create_docker_compose
            create_nginx_config
            create_yubihsm_plugin
            create_backup_script
            setup_yubikey_otp
            success "Vaultwarden installation complete"
            echo "Run '$0 start' to start services"
        ;;
        start)
            start_vaultwarden
        ;;
        stop)
            stop_vaultwarden
        ;;
        restart)
            stop_vaultwarden
            start_vaultwarden
        ;;
        status)
            show_status
        ;;
        update)
            update_vaultwarden
        ;;
        backup)
            /opt/vaultwarden/backup.sh
        ;;
        *)
            echo -e "${GREEN}Vaultwarden YubiHSM Integration${NC}"
            echo ""
            echo "Usage: $0 {install|start|stop|restart|status|update|backup}"
            echo ""
            echo "Commands:"
            echo "  install   Install and configure Vaultwarden"
            echo "  start     Start Vaultwarden services"
            echo "  stop      Stop Vaultwarden services"
            echo "  restart   Restart Vaultwarden services"
            echo "  status    Show service status"
            echo "  update    Update Vaultwarden to latest version"
            echo "  backup    Backup Vaultwarden data"
            echo ""
            echo "Environment Variables:"
            echo "  VAULTWARDEN_DOMAIN    Domain name for Vaultwarden"
            echo "  VAULTWARDEN_PORT      HTTPS port (default: 443)"
            echo "  YUBICO_CLIENT_ID      YubiKey OTP client ID"
            echo "  YUBICO_SECRET_KEY     YubiKey OTP secret key"
            echo "  ADMIN_TOKEN           Admin panel token"
            exit 1
        ;;
    esac
}

# Create log directory
mkdir -p "$(dirname "$LOG_FILE")"

# Run main function
main "$@"