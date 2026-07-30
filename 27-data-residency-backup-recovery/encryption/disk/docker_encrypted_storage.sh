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

# shellcheck disable=SC1090,SC2034,SC2086,SC2153,SC2155,SC2181,SC2231,SC2259
# Docker Encrypted Storage with HSM Key Management
# Manages encrypted Docker volumes using LUKS with keys stored in HSM
#
# Usage: ./docker_encrypted_storage.sh {init|create|mount|remove|status|plugin|compose}

set -euo pipefail

# Configuration
HSM_CONNECTOR_URL="${HSM_CONNECTOR_URL:-http://localhost:12345}"
HSM_AUTH_KEY="${HSM_AUTH_KEY:-2}"
DOCKER_ENCRYPTED_PATH="${DOCKER_ENCRYPTED_PATH:-/var/lib/docker-encrypted}"
DOCKER_VOLUME_SIZE="${DOCKER_VOLUME_SIZE:-100G}"
LOG_FILE="/var/log/docker-hsm-encryption.log"

# Key configuration in HSM
DOCKER_KEY_ID_BASE=5000
DOCKER_KEY_LABEL="docker-storage"
KEY_ROTATION_DAYS=90

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"; }
error() { echo -e "${RED}[ERROR]${NC} $1" >&2; log "ERROR: $1"; exit 1; }
success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; log "SUCCESS: $1"; }
warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; log "WARNING: $1"; }
info() { echo -e "${BLUE}[INFO]${NC} $1"; log "INFO: $1"; }

check_prerequisites() {
    local missing=()
    if [[ $EUID -ne 0 ]]; then
        error "This script must be run as root"
    fi
    for tool in docker cryptsetup lvm yubihsm-shell python3 jq; do
        if ! command -v "$tool" &> /dev/null; then
            missing+=("$tool")
        fi
    done
    if [ ${#missing[@]} -gt 0 ]; then
        error "Missing required tools: ${missing[*]}"
    fi
    if ! curl -s "$HSM_CONNECTOR_URL/connector/status" &> /dev/null; then
        warning "HSM connector not accessible at $HSM_CONNECTOR_URL"
    fi
    success "All prerequisites met"
}

# Generate encryption key in HSM
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
    hsm = YubiHsm.connect("$HSM_CONNECTOR_URL")
    password = os.getenv('HSM_PASSWORD')
    if not password:
        password = getpass.getpass("Enter HSM password: ")
    session = hsm.create_session_derived($HSM_AUTH_KEY, password)

    try:
        existing_key = session.get_object($key_id, OBJECT.SYMMETRIC_KEY)
        print(f"Key already exists for volume '$volume_name' with ID {$key_id}")
        session.close()
        sys.exit(0)
    except:
        pass

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

    if [ $? -eq 0 ]; then
        success "Encryption key generated in HSM"
        echo "$key_id"
    else
        error "Failed to generate encryption key"
    fi
}

# Retrieve key from HSM
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
    hsm = YubiHsm.connect("$HSM_CONNECTOR_URL")
    # fail-fast: set HSM_PASSWORD in environment before running this script
    password = os.environ['HSM_PASSWORD']  # raises KeyError if unset
    session = hsm.create_session_derived($HSM_AUTH_KEY, password)

    volume_name = "$volume_name"
    key_id = $key_id

    # In production, use proper wrapped key export from HSM
    derived_key = hashlib.pbkdf2_hmac(
        'sha256',
        f'docker-{volume_name}-{key_id}'.encode(),
        b'hsm-docker-salt',
        100000,
        32
    )

    print(derived_key.hex())
    session.close()

except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
EOF
}

# Create encrypted Docker storage volume
create_encrypted_storage() {
    local volume_name="${1:-docker-main}"
    local size="${2:-$DOCKER_VOLUME_SIZE}"

    info "Creating encrypted Docker storage: $volume_name ($size)"

    local storage_file="$DOCKER_ENCRYPTED_PATH/${volume_name}.img"
    mkdir -p "$DOCKER_ENCRYPTED_PATH"

    if [ -f "$storage_file" ]; then
        warning "Storage file already exists: $storage_file"
        return 1
    fi

    truncate -s "$size" "$storage_file"

    local loop_device=$(losetup -f)
    losetup "$loop_device" "$storage_file"

    local key_id=$(generate_docker_key "$volume_name")
    local encryption_key=$(get_docker_key "$volume_name")

    if [ -z "$encryption_key" ]; then
        error "Failed to retrieve encryption key"
    fi

    info "Formatting with LUKS2 encryption..."
    echo -n "$encryption_key" | xxd -r -p | cryptsetup luksFormat \
        --type luks2 \
        --cipher aes-xts-plain64 \
        --key-size 512 \
        --hash sha256 \
        --pbkdf argon2id \
        --key-file - \
        "$loop_device" <<< "YES"

    echo -n "$encryption_key" | xxd -r -p | cryptsetup luksOpen \
        "$loop_device" \
        "docker-crypt-$volume_name" \
        --key-file -

    info "Creating ext4 filesystem..."
    mkfs.ext4 "/dev/mapper/docker-crypt-$volume_name"

    local mount_point="$DOCKER_ENCRYPTED_PATH/volumes/$volume_name"
    mkdir -p "$mount_point"
    mount "/dev/mapper/docker-crypt-$volume_name" "$mount_point"
    chmod 700 "$mount_point"

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

# Docker Compose example with encrypted volumes
create_docker_compose_example() {
    info "Creating Docker Compose example with encrypted volumes"

    cat > $DOCKER_ENCRYPTED_PATH/docker-compose.yml <<'EOF'
version: '3.8'

volumes:
  postgres-data:
    driver: hsm-volume
    driver_opts:
      size: "20G"
  redis-data:
    driver: hsm-volume
    driver_opts:
      size: "5G"
  app-data:
    driver: hsm-volume
    driver_opts:
      size: "50G"

services:
  postgres:
    image: postgres:18-alpine
    container_name: postgres-encrypted
    environment:
      POSTGRES_PASSWORD_FILE: /run/secrets/postgres_password
      POSTGRES_DB: platform_db
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

  redis:
    image: redis:8-alpine
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

  app:
    image: nginx:alpine
    container_name: app-encrypted
    volumes:
      - app-data:/usr/share/nginx/html
    ports:
      - "443:443"
    networks:
      - secure-network
    depends_on:
      - postgres
      - redis

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
EOF

    success "Docker Compose example created"
}

# Status check
check_status() {
    info "Checking encrypted Docker storage status"

    echo "=== Encrypted Volumes ==="
    for conf_file in $DOCKER_ENCRYPTED_PATH/*.conf; do
        if [ -f "$conf_file" ]; then
            source "$conf_file"
            echo "Volume: $VOLUME_NAME"
            echo "  Storage: $STORAGE_FILE"
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

    echo "=== HSM Status ==="
    curl -s "$HSM_CONNECTOR_URL/connector/status" | jq . || echo "Connector not accessible"
}

# Main function
main() {
    case "${1:-}" in
        init)
            check_prerequisites
            create_docker_compose_example
            success "Docker encrypted storage initialized"
            ;;
        create)
            shift
            create_encrypted_storage "$@"
            ;;
        status) check_status ;;
        compose) create_docker_compose_example ;;
        *)
            echo "Docker Encrypted Storage with HSM Key Management"
            echo ""
            echo "Usage: $0 {init|create|status|compose} [args...]"
            echo ""
            echo "Commands:"
            echo "  init                 Initialize Docker encrypted storage system"
            echo "  create <name> [size] Create encrypted storage volume"
            echo "  status               Show status of all volumes"
            echo "  compose              Create Docker Compose example"
            echo ""
            echo "Examples:"
            echo "  $0 init"
            echo "  $0 create postgres-data 50G"
            echo "  $0 status"
            exit 1
            ;;
    esac
}

mkdir -p "$(dirname "$LOG_FILE")"
mkdir -p "$DOCKER_ENCRYPTED_PATH"
main "$@"
