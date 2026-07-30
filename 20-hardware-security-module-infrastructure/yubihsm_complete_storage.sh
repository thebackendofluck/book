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
# YubiHSM 2 Complete Storage Guide
# What can be stored and how to manage it securely

set -euo pipefail

# ============================================================================
# YubiHSM 2 STORAGE CAPABILITIES GUIDE
# ============================================================================

cat << 'EOF' > /tmp/yubihsm2_storage_guide.md
# 🔐 YubiHSM 2 Complete Storage Guide

## 📊 Storage Capacity & Object Types

### Maximum Storage Capacity
- **Total Objects**: Up to 256 objects
- **Key Storage**: ~3,500 cryptographic keys (depending on type)
- **Sessions**: 16 concurrent sessions
- **Domains**: 16 logical domains for separation

### What Can Be Stored

| Object Type | Max Count | Use Case | Size |
|-------------|-----------|----------|------|
| **Asymmetric Keys** | ~127 | RSA, ECC private keys | 2048-4096 bits |
| **Symmetric Keys** | ~255 | AES encryption keys | 128-256 bits |
| **HMAC Keys** | ~255 | Message authentication | 128-512 bits |
| **Wrap Keys** | ~127 | Key export/import | 128-256 bits |
| **Opaque Objects** | ~255 | Certificates, passwords | Up to 2048 bytes |
| **Authentication Keys** | ~255 | HSM access control | Variable |
| **Template Objects** | ~127 | Object templates | Variable |

## 🔑 What You CAN Store

### ✅ 1. Cryptographic Keys
- **RSA Private Keys** (2048, 3072, 4096 bits)
- **ECC Private Keys** (P-224, P-256, P-384, P-521, secp256k1)
- **AES Keys** (128, 192, 256 bits)
- **HMAC Keys** (SHA-1, SHA-256, SHA-384, SHA-512)
- **Wrap Keys** for secure key export/import

### ✅ 2. Certificates (as Opaque Objects)
- X.509 certificates (up to 2048 bytes)
- Certificate chains
- CA certificates
- Code signing certificates
- TLS/SSL certificates

### ✅ 3. Passwords & Secrets (as Opaque Objects)
- Application passwords (encrypted)
- API keys
- Database credentials
- Service account passwords
- OAuth tokens

### ✅ 4. Authentication Credentials
- YubiHSM authentication keys
- M of N authorization keys
- Derivation passwords

## ❌ What You CANNOT Store

### Direct Storage Limitations
- **Plain text files** larger than 2048 bytes
- **Binary data** exceeding object size limits
- **Complete databases**
- **Large documents** or files
- **Unencrypted sensitive data** (must be wrapped)

## 🚀 Implementation Examples

EOF

# ============================================================================
# IMPLEMENTATION SCRIPT
# ============================================================================

# Configuration
YUBIHSM_CONNECTOR_URL="${YUBIHSM_CONNECTOR_URL:-http://localhost:12345}"
YUBIHSM_AUTH_KEY="${YUBIHSM_AUTH_KEY:-2}"
LOG_FILE="/var/log/yubihsm-storage.log"

# Object ID ranges for organization
ID_RANGE_PASSWORDS=1000    # 1000-1999 for passwords
ID_RANGE_CERTS=2000        # 2000-2999 for certificates  
ID_RANGE_KEYS=3000         # 3000-3999 for encryption keys
ID_RANGE_SIGNING=4000      # 4000-4999 for signing keys
ID_RANGE_SSH=5000          # 5000-5999 for SSH keys

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

info() {
    echo -e "${BLUE}[INFO]${NC} $1"
    log "INFO: $1"
}

success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
    log "SUCCESS: $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
    log "ERROR: $1"
    exit 1
}

# ============================================================================
# PASSWORD STORAGE FUNCTIONS
# ============================================================================

store_password() {
    local name="$1"
    local password="$2"
    local object_id
    object_id=$((ID_RANGE_PASSWORDS + $(echo "$name" | cksum | cut -d' ' -f1) % 1000))
    
    info "Storing password: $name (ID: $object_id)"
    
    python3 - <<EOF
import sys
import os
import getpass
from yubihsm import YubiHsm
from yubihsm.defs import CAPABILITY, ALGORITHM, OBJECT
from yubihsm.objects import Opaque
import hashlib
import json
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import secrets

try:
    # Connect to YubiHSM
    hsm = YubiHsm.connect("$YUBIHSM_CONNECTOR_URL")
    password = os.getenv('YUBIHSM_PASSWORD', getpass.getpass("YubiHSM password: "))
    session = hsm.create_session_derived($YUBIHSM_AUTH_KEY, password)
    
    # Create password object
    password_data = {
        "name": "$name",
        "password": "$password",
        "created": "$(date -Iseconds)",
        "type": "password"
    }
    
    # Convert to bytes
    password_bytes = json.dumps(password_data).encode()
    
    # Store as opaque object
    opaque = Opaque.put(
        session=session,
        object_id=$object_id,
        label=f"pwd-$name"[:40],
        domains=1,
        capabilities=CAPABILITY.EXPORTABLE_UNDER_WRAP,
        algorithm=ALGORITHM.OPAQUE_DATA,
        data=password_bytes
    )
    
    print(f"Password stored with ID: {opaque.id}")
    session.close()
    
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
EOF
    
    pwd_store_exit=$?
    if [ $pwd_store_exit -eq 0 ]; then
        success "Password stored successfully"
    else
        error "Failed to store password"
    fi
}

retrieve_password() {
    local name="$1"
    local object_id
    object_id=$((ID_RANGE_PASSWORDS + $(echo "$name" | cksum | cut -d' ' -f1) % 1000))
    
    info "Retrieving password: $name (ID: $object_id)"
    
    python3 - <<EOF
import sys
import os
from yubihsm import YubiHsm
from yubihsm.defs import OBJECT
import json

try:
    # Connect to YubiHSM
    hsm = YubiHsm.connect("$YUBIHSM_CONNECTOR_URL")
    # fail-fast: set YUBIHSM_PASSWORD in environment before running this script
    password = os.environ['YUBIHSM_PASSWORD']  # raises KeyError if unset
    session = hsm.create_session_derived($YUBIHSM_AUTH_KEY, password)
    
    # Retrieve opaque object
    opaque = session.get_object($object_id, OBJECT.OPAQUE)
    data = opaque.get()
    
    # Parse password data
    password_data = json.loads(data.decode())
    print(f"Name: {password_data['name']}")
    print(f"Password: {password_data['password']}")
    print(f"Created: {password_data['created']}")
    
    session.close()
    
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
EOF
}

# ============================================================================
# CERTIFICATE STORAGE FUNCTIONS
# ============================================================================

store_certificate() {
    local cert_file="$1"
    local cert_name
    cert_name="$(basename $cert_file .crt)"
    local object_id
    object_id=$((ID_RANGE_CERTS + $(echo "$cert_name" | cksum | cut -d' ' -f1) % 1000))
    
    info "Storing certificate: $cert_name (ID: $object_id)"
    
    python3 - <<EOF
import sys
import os
from yubihsm import YubiHsm
from yubihsm.defs import CAPABILITY, ALGORITHM, OBJECT
from yubihsm.objects import Opaque
from cryptography import x509
from cryptography.hazmat.backends import default_backend

try:
    # Connect to YubiHSM
    hsm = YubiHsm.connect("$YUBIHSM_CONNECTOR_URL")
    # fail-fast: set YUBIHSM_PASSWORD in environment before running this script
    password = os.environ['YUBIHSM_PASSWORD']  # raises KeyError if unset
    session = hsm.create_session_derived($YUBIHSM_AUTH_KEY, password)
    
    # Read certificate
    with open("$cert_file", "rb") as f:
        cert_data = f.read()
    
    # Parse to verify it's valid
    cert = x509.load_pem_x509_certificate(cert_data, default_backend())
    
    # Store as opaque object
    opaque = Opaque.put(
        session=session,
        object_id=$object_id,
        label=f"cert-$cert_name"[:40],
        domains=1,
        capabilities=CAPABILITY.EXPORTABLE_UNDER_WRAP,
        algorithm=ALGORITHM.OPAQUE_X509_CERTIFICATE,
        data=cert_data
    )
    
    print(f"Certificate stored with ID: {opaque.id}")
    print(f"Subject: {cert.subject}")
    print(f"Issuer: {cert.issuer}")
    print(f"Valid until: {cert.not_valid_after}")
    
    session.close()
    
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
EOF
    
    cert_store_exit=$?
    if [ $cert_store_exit -eq 0 ]; then
        success "Certificate stored successfully"
    else
        error "Failed to store certificate"
    fi
}

# ============================================================================
# SSH KEY STORAGE FUNCTIONS
# ============================================================================

store_ssh_key() {
    local key_name="$1"
    local key_file="${2:-~/.ssh/id_rsa}"
    local object_id
    object_id=$((ID_RANGE_SSH + $(echo "$key_name" | cksum | cut -d' ' -f1) % 1000))
    
    info "Storing SSH key: $key_name (ID: $object_id)"
    
    python3 - <<EOF
import sys
import os
from yubihsm import YubiHsm
from yubihsm.defs import CAPABILITY, ALGORITHM, OBJECT
from yubihsm.objects import AsymmetricKey
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend

try:
    # Connect to YubiHSM
    hsm = YubiHsm.connect("$YUBIHSM_CONNECTOR_URL")
    # fail-fast: set YUBIHSM_PASSWORD in environment before running this script
    password = os.environ['YUBIHSM_PASSWORD']  # raises KeyError if unset
    session = hsm.create_session_derived($YUBIHSM_AUTH_KEY, password)
    
    # Read SSH private key
    with open("$key_file", "rb") as f:
        key_data = f.read()
    
    # Parse private key
    private_key = serialization.load_pem_private_key(
        key_data, 
        password=None,
        backend=default_backend()
    )
    
    # Import into YubiHSM (for RSA keys)
    if hasattr(private_key, 'private_numbers'):
        # RSA key
        key = AsymmetricKey.put_rsa(
            session=session,
            object_id=$object_id,
            label=f"ssh-$key_name"[:40],
            domains=1,
            capabilities=CAPABILITY.SIGN_SSH_CERTIFICATE | CAPABILITY.SIGN_PKCS,
            p=private_key.private_numbers().p,
            q=private_key.private_numbers().q
        )
        print(f"SSH RSA key stored with ID: {key.id}")
    else:
        # Store as opaque for other key types
        opaque = Opaque.put(
            session=session,
            object_id=$object_id,
            label=f"ssh-$key_name"[:40],
            domains=1,
            capabilities=CAPABILITY.EXPORTABLE_UNDER_WRAP,
            algorithm=ALGORITHM.OPAQUE_DATA,
            data=key_data
        )
        print(f"SSH key stored as opaque with ID: {opaque.id}")
    
    session.close()
    
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
EOF
}

# ============================================================================
# API KEY / TOKEN STORAGE
# ============================================================================

store_api_key() {
    local service="$1"
    local api_key="$2"
    local object_id
    object_id=$((ID_RANGE_PASSWORDS + 500 + $(echo "$service" | cksum | cut -d' ' -f1) % 500))
    
    info "Storing API key for: $service"
    
    python3 - <<EOF
import sys
import os
import json
from yubihsm import YubiHsm
from yubihsm.defs import CAPABILITY, ALGORITHM, OBJECT
from yubihsm.objects import Opaque

try:
    # Connect to YubiHSM
    hsm = YubiHsm.connect("$YUBIHSM_CONNECTOR_URL")
    # fail-fast: set YUBIHSM_PASSWORD in environment before running this script
    password = os.environ['YUBIHSM_PASSWORD']  # raises KeyError if unset
    session = hsm.create_session_derived($YUBIHSM_AUTH_KEY, password)
    
    # Create API key object
    api_data = {
        "service": "$service",
        "api_key": "$api_key",
        "created": "$(date -Iseconds)",
        "type": "api_key"
    }
    
    # Store as opaque object
    opaque = Opaque.put(
        session=session,
        object_id=$object_id,
        label=f"api-$service"[:40],
        domains=1,
        capabilities=CAPABILITY.EXPORTABLE_UNDER_WRAP,
        algorithm=ALGORITHM.OPAQUE_DATA,
        data=json.dumps(api_data).encode()
    )
    
    print(f"API key stored with ID: {opaque.id}")
    session.close()
    
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
EOF
}

# ============================================================================
# DATABASE CREDENTIALS STORAGE
# ============================================================================

store_db_credentials() {
    local db_name="$1"
    local username="$2"
    local password="$3"
    local host="${4:-localhost}"
    local port="${5:-5432}"
    local object_id
    object_id=$((ID_RANGE_PASSWORDS + 200 + $(echo "$db_name" | cksum | cut -d' ' -f1) % 300))
    
    info "Storing database credentials for: $db_name"
    
    python3 - <<EOF
import sys
import os
import json
from yubihsm import YubiHsm
from yubihsm.defs import CAPABILITY, ALGORITHM, OBJECT
from yubihsm.objects import Opaque

try:
    # Connect to YubiHSM
    hsm = YubiHsm.connect("$YUBIHSM_CONNECTOR_URL")
    # fail-fast: set YUBIHSM_PASSWORD in environment before running this script
    password = os.environ['YUBIHSM_PASSWORD']  # raises KeyError if unset
    session = hsm.create_session_derived($YUBIHSM_AUTH_KEY, password)
    
    # Create database credentials object
    db_data = {
        "database": "$db_name",
        "username": "$username",
        "password": "$password",
        "host": "$host",
        "port": $port,
        "connection_string": f"postgresql://$username:$password@$host:$port/$db_name",
        "created": "$(date -Iseconds)",
        "type": "database"
    }
    
    # Store as opaque object
    opaque = Opaque.put(
        session=session,
        object_id=$object_id,
        label=f"db-$db_name"[:40],
        domains=1,
        capabilities=CAPABILITY.EXPORTABLE_UNDER_WRAP,
        algorithm=ALGORITHM.OPAQUE_DATA,
        data=json.dumps(db_data).encode()
    )
    
    print(f"Database credentials stored with ID: {opaque.id}")
    session.close()
    
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
EOF
}

# ============================================================================
# SIGNING KEY STORAGE
# ============================================================================

generate_signing_key() {
    local key_name="$1"
    local key_type="${2:-RSA4096}"  # RSA2048, RSA3072, RSA4096, ECP256, ECP384
    local object_id
    object_id=$((ID_RANGE_SIGNING + $(echo "$key_name" | cksum | cut -d' ' -f1) % 1000))
    
    info "Generating signing key: $key_name (Type: $key_type)"
    
    python3 - <<EOF
import sys
import os
from yubihsm import YubiHsm
from yubihsm.defs import CAPABILITY, ALGORITHM, OBJECT
from yubihsm.objects import AsymmetricKey

try:
    # Connect to YubiHSM
    hsm = YubiHsm.connect("$YUBIHSM_CONNECTOR_URL")
    # fail-fast: set YUBIHSM_PASSWORD in environment before running this script
    password = os.environ['YUBIHSM_PASSWORD']  # raises KeyError if unset
    session = hsm.create_session_derived($YUBIHSM_AUTH_KEY, password)
    
    # Map key type to algorithm
    algorithms = {
        "RSA2048": ALGORITHM.RSA_2048,
        "RSA3072": ALGORITHM.RSA_3072,
        "RSA4096": ALGORITHM.RSA_4096,
        "ECP256": ALGORITHM.EC_P256,
        "ECP384": ALGORITHM.EC_P384
    }
    
    algorithm = algorithms.get("$key_type", ALGORITHM.RSA_4096)
    
    # Generate signing key
    key = AsymmetricKey.generate(
        session=session,
        object_id=$object_id,
        label=f"sign-$key_name"[:40],
        domains=1,
        capabilities=CAPABILITY.SIGN_PKCS | CAPABILITY.SIGN_PSS | 
                     CAPABILITY.SIGN_ATTESTATION_CERTIFICATE,
        algorithm=algorithm
    )
    
    print(f"Signing key generated with ID: {key.id}")
    print(f"Algorithm: $key_type")
    
    # Get public key for verification
    public_key = key.get_public_key()
    
    session.close()
    
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
EOF
}

# ============================================================================
# ENCRYPTION KEY STORAGE
# ============================================================================

generate_encryption_key() {
    local key_name="$1"
    local key_size="${2:-256}"  # 128, 192, or 256 bits
    local object_id
    object_id=$((ID_RANGE_KEYS + $(echo "$key_name" | cksum | cut -d' ' -f1) % 1000))
    
    info "Generating AES encryption key: $key_name ($key_size bits)"
    
    python3 - <<EOF
import sys
import os
from yubihsm import YubiHsm
from yubihsm.defs import CAPABILITY, ALGORITHM, OBJECT
from yubihsm.objects import SymmetricKey

try:
    # Connect to YubiHSM
    hsm = YubiHsm.connect("$YUBIHSM_CONNECTOR_URL")
    # fail-fast: set YUBIHSM_PASSWORD in environment before running this script
    password = os.environ['YUBIHSM_PASSWORD']  # raises KeyError if unset
    session = hsm.create_session_derived($YUBIHSM_AUTH_KEY, password)
    
    # Map key size to algorithm
    algorithms = {
        "128": ALGORITHM.AES128,
        "192": ALGORITHM.AES192,
        "256": ALGORITHM.AES256
    }
    
    algorithm = algorithms.get("$key_size", ALGORITHM.AES256)
    
    # Generate encryption key
    key = SymmetricKey.generate(
        session=session,
        object_id=$object_id,
        label=f"enc-$key_name"[:40],
        domains=1,
        capabilities=CAPABILITY.ENCRYPT_CBC | CAPABILITY.DECRYPT_CBC |
                     CAPABILITY.ENCRYPT_ECB | CAPABILITY.DECRYPT_ECB,
        algorithm=algorithm
    )
    
    print(f"Encryption key generated with ID: {key.id}")
    print(f"Algorithm: AES-{$key_size}")
    
    session.close()
    
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
EOF
}

# ============================================================================
# LIST STORED OBJECTS
# ============================================================================

list_stored_objects() {
    info "Listing all stored objects in YubiHSM..."
    
    python3 - <<EOF
import sys
import os
from yubihsm import YubiHsm
from yubihsm.defs import OBJECT
from tabulate import tabulate

try:
    # Connect to YubiHSM
    hsm = YubiHsm.connect("$YUBIHSM_CONNECTOR_URL")
    # fail-fast: set YUBIHSM_PASSWORD in environment before running this script
    password = os.environ['YUBIHSM_PASSWORD']  # raises KeyError if unset
    session = hsm.create_session_derived($YUBIHSM_AUTH_KEY, password)
    
    # List all objects
    objects = session.list_objects()
    
    # Categorize objects
    table_data = []
    for obj in objects:
        # Determine category based on ID range
        if 1000 <= obj.id < 2000:
            category = "Password/Secret"
        elif 2000 <= obj.id < 3000:
            category = "Certificate"
        elif 3000 <= obj.id < 4000:
            category = "Encryption Key"
        elif 4000 <= obj.id < 5000:
            category = "Signing Key"
        elif 5000 <= obj.id < 6000:
            category = "SSH Key"
        else:
            category = "Other"
        
        table_data.append([
            obj.id,
            obj.object_type.name,
            obj.label,
            category,
            obj.domains,
            obj.algorithm.name if hasattr(obj, 'algorithm') else 'N/A'
        ])
    
    # Print table
    headers = ["ID", "Type", "Label", "Category", "Domains", "Algorithm"]
    print(tabulate(table_data, headers=headers, tablefmt="grid"))
    print(f"\nTotal objects: {len(objects)}")
    print(f"Free slots: {256 - len(objects)}")
    
    session.close()
    
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
EOF
}

# ============================================================================
# BACKUP AND RESTORE
# ============================================================================

backup_objects() {
    local backup_dir="${1:-/backup/yubihsm}"
    mkdir -p "$backup_dir"
    
    info "Backing up YubiHSM objects to: $backup_dir"
    
    python3 - <<EOF
import sys
import os
import json
from yubihsm import YubiHsm
from yubihsm.defs import CAPABILITY, ALGORITHM, OBJECT
from yubihsm.objects import WrapKey
import base64
from datetime import datetime

try:
    # Connect to YubiHSM
    hsm = YubiHsm.connect("$YUBIHSM_CONNECTOR_URL")
    # fail-fast: set YUBIHSM_PASSWORD in environment before running this script
    password = os.environ['YUBIHSM_PASSWORD']  # raises KeyError if unset
    session = hsm.create_session_derived($YUBIHSM_AUTH_KEY, password)
    
    # Create or get wrap key for backup
    try:
        wrap_key = session.get_object(999, OBJECT.WRAP_KEY)
    except:
        wrap_key = WrapKey.generate(
            session=session,
            object_id=999,
            label="backup-wrap-key",
            domains=0xFFFF,  # All domains
            capabilities=CAPABILITY.EXPORT_WRAPPED | CAPABILITY.IMPORT_WRAPPED,
            algorithm=ALGORITHM.AES256_CCM_WRAP
        )
    
    # List and export objects
    objects = session.list_objects()
    backup_data = {
        "timestamp": datetime.now().isoformat(),
        "objects": []
    }
    
    for obj in objects:
        if obj.id == 999:  # Skip wrap key itself
            continue
            
        try:
            # Export wrapped object
            wrapped = obj.export_wrapped(wrap_key)
            
            backup_data["objects"].append({
                "id": obj.id,
                "type": obj.object_type.name,
                "label": obj.label,
                "wrapped_data": base64.b64encode(wrapped).decode(),
                "domains": obj.domains,
                "algorithm": obj.algorithm.name if hasattr(obj, 'algorithm') else None
            })
            print(f"Backed up: {obj.label} (ID: {obj.id})")
            
        except Exception as e:
            print(f"Failed to backup {obj.label}: {e}")
    
    # Save backup
    backup_file = f"$backup_dir/yubihsm_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(backup_file, "w") as f:
        json.dump(backup_data, f, indent=2)
    
    print(f"\nBackup saved to: {backup_file}")
    print(f"Total objects backed up: {len(backup_data['objects'])}")
    
    session.close()
    
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
EOF
}

# ============================================================================
# USAGE STATISTICS
# ============================================================================

show_usage_stats() {
    info "YubiHSM Storage Usage Statistics"
    
    python3 - <<EOF
import sys
import os
from yubihsm import YubiHsm

try:
    # Connect to YubiHSM
    hsm = YubiHsm.connect("$YUBIHSM_CONNECTOR_URL")
    # fail-fast: set YUBIHSM_PASSWORD in environment before running this script
    password = os.environ['YUBIHSM_PASSWORD']  # raises KeyError if unset
    session = hsm.create_session_derived($YUBIHSM_AUTH_KEY, password)
    
    # Get device info
    info = session.get_device_info()
    
    print("\n=== YubiHSM 2 Device Information ===")
    print(f"Serial Number: {info.serial}")
    print(f"Version: {info.version}")
    print(f"Log Entries: {info.log_used}/{info.log_size}")
    
    # List objects and calculate usage
    objects = session.list_objects()
    
    # Count by type
    type_counts = {}
    for obj in objects:
        obj_type = obj.object_type.name
        type_counts[obj_type] = type_counts.get(obj_type, 0) + 1
    
    print("\n=== Storage Usage ===")
    print(f"Total Objects: {len(objects)}/256")
    print(f"Usage: {(len(objects)/256)*100:.1f}%")
    print(f"Free Slots: {256 - len(objects)}")
    
    print("\n=== Objects by Type ===")
    for obj_type, count in sorted(type_counts.items()):
        print(f"{obj_type}: {count}")
    
    # Estimate storage by category
    passwords = sum(1 for o in objects if 1000 <= o.id < 2000)
    certs = sum(1 for o in objects if 2000 <= o.id < 3000)
    enc_keys = sum(1 for o in objects if 3000 <= o.id < 4000)
    sign_keys = sum(1 for o in objects if 4000 <= o.id < 5000)
    ssh_keys = sum(1 for o in objects if 5000 <= o.id < 6000)
    
    print("\n=== Objects by Category ===")
    print(f"Passwords/Secrets: {passwords}")
    print(f"Certificates: {certs}")
    print(f"Encryption Keys: {enc_keys}")
    print(f"Signing Keys: {sign_keys}")
    print(f"SSH Keys: {ssh_keys}")
    print(f"Other: {len(objects) - passwords - certs - enc_keys - sign_keys - ssh_keys}")
    
    session.close()
    
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
EOF
}

# ============================================================================
# MAIN MENU
# ============================================================================

show_menu() {
    echo -e "${GREEN}╔═══════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║     YubiHSM 2 - Secure Storage Management System     ║${NC}"
    echo -e "${GREEN}╚═══════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "${BLUE}Password & Secret Management:${NC}"
    echo "  1. Store Password"
    echo "  2. Retrieve Password"
    echo "  3. Store API Key"
    echo "  4. Store Database Credentials"
    echo ""
    echo -e "${BLUE}Certificate Management:${NC}"
    echo "  5. Store Certificate"
    echo "  6. Store SSH Key"
    echo ""
    echo -e "${BLUE}Key Management:${NC}"
    echo "  7. Generate Signing Key"
    echo "  8. Generate Encryption Key"
    echo ""
    echo -e "${BLUE}Administration:${NC}"
    echo "  9. List All Objects"
    echo "  10. Show Usage Statistics"
    echo "  11. Backup Objects"
    echo "  12. Show Storage Guide"
    echo ""
    echo "  0. Exit"
    echo ""
}

# Interactive mode
interactive_mode() {
    while true; do
        show_menu
        read -p "Select option: " choice
        
        case $choice in
            1)
                read -p "Enter password name: " name
                read -s -p "Enter password: " pass
                echo
                store_password "$name" "$pass"
                ;;
            2)
                read -p "Enter password name: " name
                retrieve_password "$name"
                ;;
            3)
                read -p "Enter service name: " service
                read -p "Enter API key: " key
                store_api_key "$service" "$key"
                ;;
            4)
                read -p "Database name: " db
                read -p "Username: " user
                read -s -p "Password: " pass
                echo
                read -p "Host [localhost]: " host
                read -p "Port [5432]: " port
                store_db_credentials "$db" "$user" "$pass" "${host:-localhost}" "${port:-5432}"
                ;;
            5)
                read -p "Certificate file path: " cert
                store_certificate "$cert"
                ;;
            6)
                read -p "SSH key name: " name
                read -p "Key file path [~/.ssh/id_rsa]: " file
                store_ssh_key "$name" "${file:-~/.ssh/id_rsa}"
                ;;
            7)
                read -p "Signing key name: " name
                read -p "Key type [RSA4096]: " type
                generate_signing_key "$name" "${type:-RSA4096}"
                ;;
            8)
                read -p "Encryption key name: " name
                read -p "Key size [256]: " size
                generate_encryption_key "$name" "${size:-256}"
                ;;
            9)
                list_stored_objects
                ;;
            10)
                show_usage_stats
                ;;
            11)
                read -p "Backup directory [/backup/yubihsm]: " dir
                backup_objects "${dir:-/backup/yubihsm}"
                ;;
            12)
                cat /tmp/yubihsm2_storage_guide.md
                ;;
            0)
                echo "Exiting..."
                exit 0
                ;;
            *)
                echo "Invalid option"
                ;;
        esac
        
        echo ""
        read -p "Press Enter to continue..."
    done
}

# Main function
main() {
    case "${1:-}" in
        store-password)
            shift
            store_password "$@"
            ;;
        get-password)
            shift
            retrieve_password "$@"
            ;;
        store-api)
            shift
            store_api_key "$@"
            ;;
        store-db)
            shift
            store_db_credentials "$@"
            ;;
        store-cert)
            shift
            store_certificate "$@"
            ;;
        store-ssh)
            shift
            store_ssh_key "$@"
            ;;
        gen-sign-key)
            shift
            generate_signing_key "$@"
            ;;
        gen-enc-key)
            shift
            generate_encryption_key "$@"
            ;;
        list)
            list_stored_objects
            ;;
        stats)
            show_usage_stats
            ;;
        backup)
            shift
            backup_objects "$@"
            ;;
        guide)
            cat /tmp/yubihsm2_storage_guide.md
            ;;
        interactive)
            interactive_mode
            ;;
        *)
            echo -e "${GREEN}YubiHSM 2 Storage Management${NC}"
            echo ""
            echo "Usage: $0 <command> [options]"
            echo ""
            echo "Commands:"
            echo "  store-password <n> <pass>  Store password"
            echo "  get-password <n>         Retrieve password"
            echo "  store-api <service> <key>   Store API key"
            echo "  store-db <db> <u> <p> [h] [port] Store DB credentials"
            echo "  store-cert <file>          Store certificate"
            echo "  store-ssh <n> [file]     Store SSH key"
            echo "  gen-sign-key <n> [type]  Generate signing key"
            echo "  gen-enc-key <n> [size]   Generate encryption key"
            echo "  list                         List all objects"
            echo "  stats                        Show usage statistics"
            echo "  backup [dir]                 Backup all objects"
            echo "  guide                        Show storage guide"
            echo "  interactive                  Interactive menu"
            echo ""
            echo "Examples:"
            echo "  $0 store-password gmail 'MySecretPass123'"
            echo "  $0 store-api github 'ghp_xxxxxxxxxxxx'"
            echo "  $0 store-cert /path/to/cert.pem"
            echo "  $0 gen-sign-key code-signing RSA4096"
            echo "  $0 interactive"
            exit 1
            ;;
    esac
}

# Create log directory
mkdir -p "$(dirname "$LOG_FILE")"

# Run main function
main "$@"
