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
# YubiHSM 2 Lifecycle Management System
# Complete object lifecycle: Create, Read, Update, Delete, Rotate, Cleanup

set -euo pipefail

# Configuration
YUBIHSM_CONNECTOR_URL="${YUBIHSM_CONNECTOR_URL:-http://localhost:12345}"
YUBIHSM_AUTH_KEY="${YUBIHSM_AUTH_KEY:-2}"
LOG_FILE="/var/log/yubihsm-lifecycle.log"
AUDIT_FILE="/var/log/yubihsm-audit.log"

# Object ID ranges
ID_RANGE_PASSWORDS=1000
ID_RANGE_CERTS=2000
ID_RANGE_KEYS=3000
ID_RANGE_SIGNING=4000
ID_RANGE_SSH=5000

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
NC='\033[0m'

# Logging
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

audit_log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] AUDIT: $1" | tee -a "$AUDIT_FILE"
}

info() {
    echo -e "${BLUE}[INFO]${NC} $1"
    log "INFO: $1"
}

success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
    log "SUCCESS: $1"
}

warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
    log "WARNING: $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
    log "ERROR: $1"
    exit 1
}

# ============================================================================
# DELETE OPERATIONS
# ============================================================================

delete_object_by_id() {
    local object_id="$1"
    local confirm="${2:-no}"
    
    if [ "$confirm" != "yes" ]; then
        warning "About to DELETE object ID: $object_id"
        read -p "Are you sure? Type 'yes' to confirm: " confirm
        if [ "$confirm" != "yes" ]; then
            info "Deletion cancelled"
            return 1
        fi
    fi
    
    info "Deleting object ID: $object_id"
    
    python3 - <<EOF
import sys
import os
from yubihsm import YubiHsm
from yubihsm.defs import OBJECT

try:
    # Connect to YubiHSM
    hsm = YubiHsm.connect("$YUBIHSM_CONNECTOR_URL")
    # fail-fast: set YUBIHSM_PASSWORD in environment before running this script
    password = os.environ['YUBIHSM_PASSWORD']  # raises KeyError if unset
    session = hsm.create_session_derived($YUBIHSM_AUTH_KEY, password)
    
    # Get object info before deletion
    try:
        objects = session.list_objects()
        target_obj = None
        for obj in objects:
            if obj.id == $object_id:
                target_obj = obj
                break
        
        if target_obj:
            print(f"Found object: {target_obj.label} (Type: {target_obj.object_type.name})")
            
            # Delete the object
            session.delete_object(target_obj.id, target_obj.object_type)
            print(f"✓ Deleted object ID {$object_id}: {target_obj.label}")
            
            # Log the deletion
            with open("$AUDIT_FILE", "a") as f:
                f.write(f"DELETED: ID={$object_id}, Label={target_obj.label}, Type={target_obj.object_type.name}\n")
        else:
            print(f"Object ID {$object_id} not found")
            sys.exit(1)
            
    except Exception as e:
        print(f"Error during deletion: {e}", file=sys.stderr)
        sys.exit(1)
    
    session.close()
    
except Exception as e:
    print(f"Connection error: {e}", file=sys.stderr)
    sys.exit(1)
EOF
    
    _cmd_exit_1=$?
    if [ $_cmd_exit_1 -eq 0 ]; then
        success "Object deleted successfully"
        audit_log "Deleted object ID: $object_id"
    else
        error "Failed to delete object"
    fi
}

delete_password() {
    local name="$1"
    local object_id
    object_id=$((ID_RANGE_PASSWORDS + $(echo "$name" | cksum | cut -d' ' -f1) % 1000))
    
    info "Deleting password: $name (ID: $object_id)"
    delete_object_by_id "$object_id"
}

delete_certificate() {
    local cert_name="$1"
    local object_id
    object_id=$((ID_RANGE_CERTS + $(echo "$cert_name" | cksum | cut -d' ' -f1) % 1000))
    
    info "Deleting certificate: $cert_name (ID: $object_id)"
    delete_object_by_id "$object_id"
}

# ============================================================================
# UPDATE/ROTATE OPERATIONS
# ============================================================================

update_password() {
    local name="$1"
    local new_password="$2"
    local object_id
    object_id=$((ID_RANGE_PASSWORDS + $(echo "$name" | cksum | cut -d' ' -f1) % 1000))
    
    info "Updating password: $name (ID: $object_id)"
    
    # Delete old password
    delete_object_by_id "$object_id" "yes"
    
    # Store new password
    python3 - <<EOF
import sys
import os
import json
from yubihsm import YubiHsm
from yubihsm.defs import CAPABILITY, ALGORITHM, OBJECT
from yubihsm.objects import Opaque
from datetime import datetime

try:
    # Connect to YubiHSM
    hsm = YubiHsm.connect("$YUBIHSM_CONNECTOR_URL")
    # fail-fast: set YUBIHSM_PASSWORD in environment before running this script
    password = os.environ['YUBIHSM_PASSWORD']  # raises KeyError if unset
    session = hsm.create_session_derived($YUBIHSM_AUTH_KEY, password)
    
    # Create new password object
    password_data = {
        "name": "$name",
        "password": "$new_password",
        "created": datetime.now().isoformat(),
        "updated": datetime.now().isoformat(),
        "type": "password",
        "rotation_count": 1
    }
    
    # Store as opaque object
    opaque = Opaque.put(
        session=session,
        object_id=$object_id,
        label=f"pwd-$name"[:40],
        domains=1,
        capabilities=CAPABILITY.EXPORTABLE_UNDER_WRAP,
        algorithm=ALGORITHM.OPAQUE_DATA,
        data=json.dumps(password_data).encode()
    )
    
    print(f"Password updated with ID: {opaque.id}")
    session.close()
    
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
EOF
    
    _cmd_exit_2=$?
    if [ $_cmd_exit_2 -eq 0 ]; then
        success "Password updated successfully"
        audit_log "Updated password: $name"
    fi
}

rotate_encryption_key() {
    local key_name="$1"
    local object_id
    object_id=$((ID_RANGE_KEYS + $(echo "$key_name" | cksum | cut -d' ' -f1) % 1000))
    local backup_id  # Backup with offset
    backup_id=$((object_id + 10000))
    
    info "Rotating encryption key: $key_name"
    
    python3 - <<EOF
import sys
import os
from yubihsm import YubiHsm
from yubihsm.defs import CAPABILITY, ALGORITHM, OBJECT
from yubihsm.objects import SymmetricKey, WrapKey
from datetime import datetime

try:
    # Connect to YubiHSM
    hsm = YubiHsm.connect("$YUBIHSM_CONNECTOR_URL")
    # fail-fast: set YUBIHSM_PASSWORD in environment before running this script
    password = os.environ['YUBIHSM_PASSWORD']  # raises KeyError if unset
    session = hsm.create_session_derived($YUBIHSM_AUTH_KEY, password)
    
    # Get old key
    try:
        old_key = session.get_object($object_id, OBJECT.SYMMETRIC_KEY)
        print(f"Found existing key: {old_key.label}")
        
        # Create backup of old key (as wrapped export)
        wrap_key = session.get_object(999, OBJECT.WRAP_KEY)
        wrapped_key = old_key.export_wrapped(wrap_key)
        
        # Delete old key
        session.delete_object($object_id, OBJECT.SYMMETRIC_KEY)
        print("Old key deleted")
        
    except:
        print("No existing key found, creating new one")
    
    # Generate new key with same ID
    new_key = SymmetricKey.generate(
        session=session,
        object_id=$object_id,
        label=f"enc-$key_name-v{datetime.now().strftime('%Y%m%d')}"[:40],
        domains=1,
        capabilities=CAPABILITY.ENCRYPT_CBC | CAPABILITY.DECRYPT_CBC,
        algorithm=ALGORITHM.AES256
    )
    
    print(f"✓ New encryption key generated with ID: {new_key.id}")
    print(f"  Algorithm: AES-256")
    print(f"  Rotated at: {datetime.now().isoformat()}")
    
    session.close()
    
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
EOF
    
    _cmd_exit_3=$?
    if [ $_cmd_exit_3 -eq 0 ]; then
        success "Encryption key rotated successfully"
        audit_log "Rotated encryption key: $key_name"
    fi
}

# ============================================================================
# BULK DELETE OPERATIONS
# ============================================================================

bulk_delete_passwords() {
    warning "This will delete ALL passwords (ID range 1000-1999)"
    read -p "Are you ABSOLUTELY sure? Type 'DELETE ALL PASSWORDS': " confirm
    
    if [ "$confirm" != "DELETE ALL PASSWORDS" ]; then
        info "Bulk deletion cancelled"
        return
    fi
    
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
    
    # List all objects
    objects = session.list_objects()
    deleted_count = 0
    
    for obj in objects:
        if 1000 <= obj.id < 2000:  # Password range
            try:
                session.delete_object(obj.id, obj.object_type)
                print(f"Deleted: {obj.label} (ID: {obj.id})")
                deleted_count += 1
            except Exception as e:
                print(f"Failed to delete {obj.id}: {e}")
    
    print(f"\n✓ Total passwords deleted: {deleted_count}")
    session.close()
    
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
EOF
    
    audit_log "BULK DELETE: Removed all passwords"
}

# ============================================================================
# CLEANUP OPERATIONS
# ============================================================================

cleanup_expired_certificates() {
    info "Scanning for expired certificates..."
    
    python3 - <<EOF
import sys
import os
from yubihsm import YubiHsm
from yubihsm.defs import OBJECT
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from datetime import datetime

try:
    # Connect to YubiHSM
    hsm = YubiHsm.connect("$YUBIHSM_CONNECTOR_URL")
    # fail-fast: set YUBIHSM_PASSWORD in environment before running this script
    password = os.environ['YUBIHSM_PASSWORD']  # raises KeyError if unset
    session = hsm.create_session_derived($YUBIHSM_AUTH_KEY, password)
    
    # List all objects
    objects = session.list_objects()
    expired_count = 0
    
    for obj in objects:
        if 2000 <= obj.id < 3000:  # Certificate range
            if obj.object_type == OBJECT.OPAQUE:
                try:
                    # Get certificate data
                    cert_data = session.get_object(obj.id, OBJECT.OPAQUE).get()
                    
                    # Parse certificate
                    cert = x509.load_pem_x509_certificate(cert_data, default_backend())
                    
                    # Check expiration
                    if datetime.utcnow() > cert.not_valid_after:
                        print(f"Expired certificate found: {obj.label}")
                        print(f"  Expired on: {cert.not_valid_after}")
                        
                        # Delete expired certificate
                        session.delete_object(obj.id, obj.object_type)
                        print(f"  ✓ Deleted")
                        expired_count += 1
                        
                except Exception as e:
                    print(f"Error processing {obj.id}: {e}")
    
    print(f"\n✓ Total expired certificates removed: {expired_count}")
    session.close()
    
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
EOF
    
    audit_log "Cleaned up expired certificates"
}

cleanup_old_objects() {
    local days="${1:-90}"
    info "Cleaning objects older than $days days..."
    
    python3 - <<EOF
import sys
import os
import json
from yubihsm import YubiHsm
from yubihsm.defs import OBJECT
from datetime import datetime, timedelta

try:
    # Connect to YubiHSM
    hsm = YubiHsm.connect("$YUBIHSM_CONNECTOR_URL")
    # fail-fast: set YUBIHSM_PASSWORD in environment before running this script
    password = os.environ['YUBIHSM_PASSWORD']  # raises KeyError if unset
    session = hsm.create_session_derived($YUBIHSM_AUTH_KEY, password)
    
    # Calculate cutoff date
    cutoff_date = datetime.now() - timedelta(days=$days)
    
    # List all objects
    objects = session.list_objects()
    deleted_count = 0
    
    for obj in objects:
        if obj.object_type == OBJECT.OPAQUE:
            try:
                # Get object data
                data = session.get_object(obj.id, OBJECT.OPAQUE).get()
                
                # Try to parse as JSON
                try:
                    obj_data = json.loads(data.decode())
                    created_str = obj_data.get('created', '')
                    
                    if created_str:
                        created_date = datetime.fromisoformat(created_str.replace('Z', '+00:00'))
                        
                        if created_date < cutoff_date:
                            print(f"Old object found: {obj.label}")
                            print(f"  Created: {created_str}")
                            print(f"  Age: {(datetime.now() - created_date).days} days")
                            
                            # Delete old object
                            session.delete_object(obj.id, obj.object_type)
                            print(f"  ✓ Deleted")
                            deleted_count += 1
                            
                except (json.JSONDecodeError, ValueError):
                    pass  # Not a JSON object or no date
                    
            except Exception as e:
                print(f"Error processing {obj.id}: {e}")
    
    print(f"\n✓ Total old objects removed: {deleted_count}")
    session.close()
    
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
EOF
}

# ============================================================================
# SPACE MANAGEMENT
# ============================================================================

show_space_usage() {
    info "Analyzing YubiHSM space usage..."
    
    python3 - <<EOF
import sys
import os
from yubihsm import YubiHsm
from yubihsm.defs import OBJECT
from tabulate import tabulate
from collections import defaultdict

try:
    # Connect to YubiHSM
    hsm = YubiHsm.connect("$YUBIHSM_CONNECTOR_URL")
    # fail-fast: set YUBIHSM_PASSWORD in environment before running this script
    password = os.environ['YUBIHSM_PASSWORD']  # raises KeyError if unset
    session = hsm.create_session_derived($YUBIHSM_AUTH_KEY, password)
    
    # Get device info
    info = session.get_device_info()
    
    # List all objects
    objects = session.list_objects()
    
    # Analyze by category
    categories = defaultdict(list)
    
    for obj in objects:
        if 1000 <= obj.id < 2000:
            categories['Passwords'].append(obj)
        elif 2000 <= obj.id < 3000:
            categories['Certificates'].append(obj)
        elif 3000 <= obj.id < 4000:
            categories['Encryption Keys'].append(obj)
        elif 4000 <= obj.id < 5000:
            categories['Signing Keys'].append(obj)
        elif 5000 <= obj.id < 6000:
            categories['SSH Keys'].append(obj)
        else:
            categories['Other'].append(obj)
    
    # Print summary
    print("\n" + "="*60)
    print(" "*20 + "YUBIHSM SPACE USAGE")
    print("="*60)
    
    print(f"\nDevice Serial: {info.serial}")
    print(f"Firmware Version: {info.version}")
    
    # Usage bar
    used = len(objects)
    total = 256
    percentage = (used/total) * 100
    bar_length = 40
    filled_length = int(bar_length * used // total)
    bar = '█' * filled_length + '░' * (bar_length - filled_length)
    
    print(f"\n[{bar}] {percentage:.1f}%")
    print(f"Used: {used}/{total} objects")
    print(f"Free: {total-used} objects")
    
    # Category breakdown
    print("\n" + "-"*60)
    print("CATEGORY BREAKDOWN:")
    print("-"*60)
    
    table_data = []
    for category, objs in sorted(categories.items()):
        count = len(objs)
        pct = (count/used*100) if used > 0 else 0
        table_data.append([category, count, f"{pct:.1f}%"])
    
    print(tabulate(table_data, headers=["Category", "Count", "% of Used"], tablefmt="grid"))
    
    # Recommendations
    print("\n" + "-"*60)
    print("RECOMMENDATIONS:")
    print("-"*60)
    
    if percentage > 90:
        print("⚠️  CRITICAL: Over 90% usage! Consider:")
        print("   - Delete unused objects")
        print("   - Archive old passwords")
        print("   - Remove expired certificates")
    elif percentage > 75:
        print("⚠️  WARNING: Over 75% usage. Consider cleanup soon.")
    elif percentage > 50:
        print("ℹ️  INFO: Moderate usage. Monitor growth.")
    else:
        print("✓  GOOD: Plenty of space available.")
    
    # Quick actions
    print("\n" + "-"*60)
    print("QUICK ACTIONS:")
    print("-"*60)
    print("1. Run cleanup-expired to remove expired certificates")
    print("2. Run cleanup-old 90 to remove objects older than 90 days")
    print("3. Run defrag to optimize space")
    print("4. Run backup before any major cleanup")
    
    session.close()
    
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
EOF
}

# ============================================================================
# DEFRAGMENTATION / OPTIMIZATION
# ============================================================================

defragment_space() {
    info "Optimizing YubiHSM storage space..."
    
    python3 - <<EOF
import sys
import os
import json
from yubihsm import YubiHsm
from yubihsm.defs import CAPABILITY, ALGORITHM, OBJECT
from yubihsm.objects import WrapKey, Opaque

try:
    # Connect to YubiHSM
    hsm = YubiHsm.connect("$YUBIHSM_CONNECTOR_URL")
    # fail-fast: set YUBIHSM_PASSWORD in environment before running this script
    password = os.environ['YUBIHSM_PASSWORD']  # raises KeyError if unset
    session = hsm.create_session_derived($YUBIHSM_AUTH_KEY, password)
    
    print("Starting defragmentation process...")
    
    # Create or get wrap key
    try:
        wrap_key = session.get_object(999, OBJECT.WRAP_KEY)
    except:
        wrap_key = WrapKey.generate(
            session=session,
            object_id=999,
            label="defrag-wrap-key",
            domains=0xFFFF,
            capabilities=CAPABILITY.EXPORT_WRAPPED | CAPABILITY.IMPORT_WRAPPED,
            algorithm=ALGORITHM.AES256_CCM_WRAP
        )
    
    # List all moveable objects
    objects = session.list_objects()
    reorganized = 0
    
    # Group objects by category
    passwords = []
    certificates = []
    keys = []
    
    for obj in objects:
        if obj.id == 999:  # Skip wrap key
            continue
            
        if 1000 <= obj.id < 2000:
            passwords.append(obj)
        elif 2000 <= obj.id < 3000:
            certificates.append(obj)
        else:
            keys.append(obj)
    
    # Reorganize passwords to start from 1000
    new_id = 1000
    for obj in sorted(passwords, key=lambda x: x.id):
        if obj.id != new_id:
            try:
                # Export object
                if obj.object_type == OBJECT.OPAQUE:
                    data = session.get_object(obj.id, OBJECT.OPAQUE).get()
                    
                    # Delete old
                    session.delete_object(obj.id, obj.object_type)
                    
                    # Create with new ID
                    Opaque.put(
                        session=session,
                        object_id=new_id,
                        label=obj.label,
                        domains=obj.domains,
                        capabilities=obj.capabilities,
                        algorithm=obj.algorithm,
                        data=data
                    )
                    
                    print(f"Moved {obj.label} from ID {obj.id} to {new_id}")
                    reorganized += 1
                    
            except Exception as e:
                print(f"Failed to move {obj.label}: {e}")
        
        new_id += 1
    
    print(f"\n✓ Defragmentation complete")
    print(f"  Objects reorganized: {reorganized}")
    print(f"  Space optimized: Yes")
    
    session.close()
    
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
EOF
}

# ============================================================================
# MIGRATION TOOLS
# ============================================================================

export_for_migration() {
    local export_file="${1:-yubihsm_export_$(date +%Y%m%d_%H%M%S).json}"
    
    info "Exporting all objects for migration..."
    
    python3 - <<EOF
import sys
import os
import json
import base64
from yubihsm import YubiHsm
from yubihsm.defs import OBJECT
from datetime import datetime

try:
    # Connect to YubiHSM
    hsm = YubiHsm.connect("$YUBIHSM_CONNECTOR_URL")
    # fail-fast: set YUBIHSM_PASSWORD in environment before running this script
    password = os.environ['YUBIHSM_PASSWORD']  # raises KeyError if unset
    session = hsm.create_session_derived($YUBIHSM_AUTH_KEY, password)
    
    # List all objects
    objects = session.list_objects()
    
    export_data = {
        "export_date": datetime.now().isoformat(),
        "device_serial": session.get_device_info().serial,
        "total_objects": len(objects),
        "objects": []
    }
    
    for obj in objects:
        obj_info = {
            "id": obj.id,
            "type": obj.object_type.name,
            "label": obj.label,
            "domains": obj.domains,
            "capabilities": obj.capabilities.value if hasattr(obj, 'capabilities') else None,
            "algorithm": obj.algorithm.name if hasattr(obj, 'algorithm') else None
        }
        
        # Export data if opaque
        if obj.object_type == OBJECT.OPAQUE:
            try:
                data = session.get_object(obj.id, OBJECT.OPAQUE).get()
                obj_info["data"] = base64.b64encode(data).decode()
            except:
                obj_info["data"] = None
        
        export_data["objects"].append(obj_info)
        print(f"Exported: {obj.label} (ID: {obj.id})")
    
    # Save to file
    with open("$export_file", "w") as f:
        json.dump(export_data, f, indent=2)
    
    print(f"\n✓ Export complete: $export_file")
    print(f"  Total objects exported: {len(objects)}")
    
    session.close()
    
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
EOF
    
    success "Migration export complete: $export_file"
}

# ============================================================================
# FACTORY RESET
# ============================================================================

factory_reset() {
    echo -e "${RED}╔══════════════════════════════════════════════════════╗${NC}"
    echo -e "${RED}║              ⚠️  FACTORY RESET WARNING ⚠️              ║${NC}"
    echo -e "${RED}╠══════════════════════════════════════════════════════╣${NC}"
    echo -e "${RED}║  This will DELETE ALL objects in the YubiHSM!       ║${NC}"
    echo -e "${RED}║  This action is IRREVERSIBLE!                       ║${NC}"
    echo -e "${RED}║                                                      ║${NC}"
    echo -e "${RED}║  All passwords, certificates, and keys will be lost!║${NC}"
    echo -e "${RED}╚══════════════════════════════════════════════════════╝${NC}"
    echo ""
    
    read -p "Type 'FACTORY RESET YUBIHSM' to confirm: " confirm
    
    if [ "$confirm" != "FACTORY RESET YUBIHSM" ]; then
        info "Factory reset cancelled"
        return
    fi
    
    # Create backup first
    warning "Creating emergency backup before reset..."
    export_for_migration "emergency_backup_$(date +%Y%m%d_%H%M%S).json"
    
    yubihsm-shell <<EOF
connect
session open 1 password
reset 0
session close
quit
EOF
    
    _cmd_exit_4=$?
    if [ $_cmd_exit_4 -eq 0 ]; then
        success "Factory reset complete - YubiHSM is now empty"
        audit_log "FACTORY RESET PERFORMED"
    else
        error "Factory reset failed"
    fi
}

# ============================================================================
# INTERACTIVE MANAGEMENT MENU
# ============================================================================

show_management_menu() {
    clear
    echo -e "${GREEN}╔═══════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║    YubiHSM 2 - Lifecycle Management System          ║${NC}"
    echo -e "${GREEN}╚═══════════════════════════════════════════════════════╝${NC}"
    echo ""
    
    # Show current usage
    python3 -c "
from yubihsm import YubiHsm
hsm = YubiHsm.connect('$YUBIHSM_CONNECTOR_URL')
session = hsm.create_session_derived($YUBIHSM_AUTH_KEY, 'password')
objects = session.list_objects()
used = len(objects)
free = 256 - used
pct = (used/256)*100
bar_len = 30
filled = int(bar_len * used // 256)
bar = '█' * filled + '░' * (bar_len - filled)
print(f'Storage: [{bar}] {pct:.1f}% ({used}/256)')
print(f'Free slots: {free}')
session.close()
" 2>/dev/null || echo "Storage: [Unable to connect]"
    
    echo ""
    echo -e "${BLUE}Object Management:${NC}"
    echo "  1. Delete object by ID"
    echo "  2. Delete password by name"
    echo "  3. Delete certificate by name"
    echo "  4. Update/rotate password"
    echo "  5. Rotate encryption key"
    echo ""
    echo -e "${YELLOW}Bulk Operations:${NC}"
    echo "  6. Delete ALL passwords"
    echo "  7. Cleanup expired certificates"
    echo "  8. Cleanup old objects (>90 days)"
    echo "  9. Defragment/optimize space"
    echo ""
    echo -e "${PURPLE}Space Management:${NC}"
    echo "  10. Show detailed space usage"
    echo "  11. Export for migration"
    echo "  12. Factory reset (DELETE ALL)"
    echo ""
    echo "  0. Exit"
    echo ""
}

# Interactive mode
interactive() {
    while true; do
        show_management_menu
        read -p "Select option: " choice
        
        case $choice in
            1)
                read -p "Enter object ID to delete: " id
                delete_object_by_id "$id"
                ;;
            2)
                read -p "Enter password name to delete: " name
                delete_password "$name"
                ;;
            3)
                read -p "Enter certificate name to delete: " name
                delete_certificate "$name"
                ;;
            4)
                read -p "Enter password name to update: " name
                read -s -p "Enter new password: " pass
                echo
                update_password "$name" "$pass"
                ;;
            5)
                read -p "Enter key name to rotate: " name
                rotate_encryption_key "$name"
                ;;
            6)
                bulk_delete_passwords
                ;;
            7)
                cleanup_expired_certificates
                ;;
            8)
                read -p "Delete objects older than (days) [90]: " days
                cleanup_old_objects "${days:-90}"
                ;;
            9)
                defragment_space
                ;;
            10)
                show_space_usage
                ;;
            11)
                read -p "Export filename [auto]: " filename
                export_for_migration "$filename"
                ;;
            12)
                factory_reset
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
        delete)
            shift
            delete_object_by_id "$@"
            ;;
        delete-password)
            shift
            delete_password "$@"
            ;;
        delete-cert)
            shift
            delete_certificate "$@"
            ;;
        update-password)
            shift
            update_password "$@"
            ;;
        rotate-key)
            shift
            rotate_encryption_key "$@"
            ;;
        bulk-delete-passwords)
            bulk_delete_passwords
            ;;
        cleanup-expired)
            cleanup_expired_certificates
            ;;
        cleanup-old)
            shift
            cleanup_old_objects "${1:-90}"
            ;;
        defrag)
            defragment_space
            ;;
        space)
            show_space_usage
            ;;
        export)
            shift
            export_for_migration "$@"
            ;;
        factory-reset)
            factory_reset
            ;;
        interactive)
            interactive
            ;;
        *)
            echo -e "${GREEN}YubiHSM 2 Lifecycle Management${NC}"
            echo ""
            echo "Usage: $0 <command> [options]"
            echo ""
            echo "Delete Commands:"
            echo "  delete <id>                Delete object by ID"
            echo "  delete-password <name>     Delete password"
            echo "  delete-cert <name>         Delete certificate"
            echo ""
            echo "Update Commands:"
            echo "  update-password <n> <p>  Update password"
            echo "  rotate-key <name>           Rotate encryption key"
            echo ""
            echo "Cleanup Commands:"
            echo "  bulk-delete-passwords       Delete ALL passwords"
            echo "  cleanup-expired             Remove expired certificates"
            echo "  cleanup-old [days]          Remove old objects"
            echo "  defrag                      Optimize space usage"
            echo ""
            echo "Management Commands:"
            echo "  space                       Show space usage"
            echo "  export [file]               Export for migration"
            echo "  factory-reset               DELETE EVERYTHING"
            echo "  interactive                 Interactive menu"
            echo ""
            echo "Examples:"
            echo "  $0 delete 1234"
            echo "  $0 delete-password gmail"
            echo "  $0 update-password gmail 'NewP@ss123'"
            echo "  $0 cleanup-old 30"
            echo "  $0 space"
            exit 1
            ;;
    esac
}

# Create log directories
mkdir -p "$(dirname "$LOG_FILE")"
mkdir -p "$(dirname "$AUDIT_FILE")"

# Run main function
main "$@"
