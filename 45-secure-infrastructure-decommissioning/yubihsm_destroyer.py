#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 45, Secure Infrastructure Decommissioning.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
YubiHSM 2 Complete Destruction Module
Handles complete wipe of YubiHSM 2 FIPS hardware security modules
"""

import sys
import os
import json
import time
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path

try:
    from yubihsm import YubiHsm  # type: ignore[unresolved-import]
    from yubihsm.defs import CAPABILITY, ALGORITHM, OBJECT, COMMAND  # type: ignore[unresolved-import]
    from yubihsm.objects import AuthenticationKey, WrapKey, AsymmetricKey, SymmetricKey, Opaque  # type: ignore[unresolved-import]
    from cryptography import x509
    from cryptography.hazmat.backends import default_backend
except ImportError as e:
    print(f"Error: Required module not found. Please install: pip install yubihsm[http,usb] cryptography")
    print(f"Missing module: {e}")
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/yubihsm_destroyer.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class YubiHSMDestroyer:
    """Complete YubiHSM 2 destruction handler"""

    def __init__(self, connector_url: str = "http://localhost:12345"):
        self.connector_url = connector_url
        self.hsm = None
        self.session = None
        self.audit_log = []
        self.backup_data = {}

    def connect(self, auth_key_id: int = 2, password: Optional[str] = None) -> bool:
        """Connect to YubiHSM and establish authenticated session"""
        try:
            logger.info(f"Connecting to YubiHSM at {self.connector_url}")
            self.hsm = YubiHsm.connect(self.connector_url)

            if password is None:
                password = input(f"Enter password for auth key {auth_key_id}: ")

            self.session = self.hsm.create_session_derived(auth_key_id, password)
            logger.info("Successfully connected to YubiHSM")
            return True

        except Exception as e:
            logger.error(f"Failed to connect to YubiHSM: {e}")
            return False

    def disconnect(self):
        """Close YubiHSM session"""
        if self.session:
            self.session.close()
            logger.info("Disconnected from YubiHSM")

    def export_audit_logs(self, backup_file: Optional[str] = None) -> bool:
        """Export final audit logs before destruction"""
        try:
            logger.info("Exporting final audit logs...")

            # Get device info
            device_info = self.session.get_device_info()

            # Get audit logs
            audit_entries = []
            try:
                logs = self.session.get_log_entries()
                for log_entry in logs:
                    audit_entries.append({
                        'timestamp': log_entry.timestamp.isoformat() if hasattr(log_entry, 'timestamp') else str(datetime.now()),
                        'command': log_entry.command.name if hasattr(log_entry, 'command') else 'unknown',
                        'result': log_entry.result.name if hasattr(log_entry, 'result') else 'unknown',
                        'session_key': getattr(log_entry, 'session_key', 'unknown'),
                        'command_data': getattr(log_entry, 'command_data', 'unknown'),
                        'response_data': getattr(log_entry, 'response_data', 'unknown')
                    })
            except Exception as e:
                logger.warning(f"Could not retrieve audit logs: {e}")
                audit_entries = [{"error": "Could not retrieve audit logs", "timestamp": datetime.now().isoformat()}]

            # Create backup data
            self.backup_data = {
                'export_timestamp': datetime.now().isoformat(),
                'device_info': {
                    'serial': device_info.serial,
                    'version': device_info.version,
                    'log_size': device_info.log_size,
                    'log_used': device_info.log_used
                },
                'audit_logs': audit_entries,
                'final_inventory': self._get_inventory_snapshot()
            }

            # Save to file if specified
            if backup_file:
                with open(backup_file, 'w') as f:
                    json.dump(self.backup_data, f, indent=2)
                logger.info(f"Audit logs exported to {backup_file}")

            self._audit_event("AUDIT_EXPORT", f"Exported {len(audit_entries)} audit entries")
            return True

        except Exception as e:
            logger.error(f"Failed to export audit logs: {e}")
            return False

    def _get_inventory_snapshot(self) -> Dict:
        """Get snapshot of all objects before destruction"""
        try:
            objects = self.session.list_objects()
            inventory: Dict[str, Any] = {
                'total_objects': len(objects),
                'object_types': {},
                'objects': []
            }

            for obj in objects:
                obj_type = obj.object_type.name
                if obj_type not in inventory['object_types']:
                    inventory['object_types'][obj_type] = 0
                inventory['object_types'][obj_type] += 1

                inventory['objects'].append({
                    'id': obj.id,
                    'type': obj_type,
                    'label': obj.label,
                    'capabilities': obj.capabilities.value if hasattr(obj, 'capabilities') else None,
                    'algorithm': obj.algorithm.name if hasattr(obj, 'algorithm') else None,
                    'domains': obj.domains
                })

            return inventory

        except Exception as e:
            logger.error(f"Failed to get inventory snapshot: {e}")
            return {'error': str(e)}

    def destroy_all_keys(self) -> bool:
        """Destroy all cryptographic keys in the HSM"""
        try:
            logger.info("Starting key destruction sequence...")

            objects = self.session.list_objects()
            destroyed_count = 0

            # Destroy in specific order for safety
            destroy_order = [
                OBJECT.SYMMETRIC_KEY,  # Symmetric keys first
                OBJECT.ASYMMETRIC_KEY, # Asymmetric keys
                OBJECT.WRAP_KEY,       # Wrap keys
                OBJECT.OPAQUE          # Opaque objects (certificates, etc.)
            ]

            for obj_type in destroy_order:
                type_destroyed = 0
                for obj in objects:
                    if obj.object_type == obj_type:
                        try:
                            self.session.delete_object(obj.id, obj.object_type)
                            logger.info(f"Destroyed {obj_type.name} ID {obj.id}: {obj.label}")
                            self._audit_event("KEY_DESTROY", f"{obj_type.name}:{obj.id}:{obj.label}")
                            destroyed_count += 1
                            type_destroyed += 1
                        except Exception as e:
                            logger.error(f"Failed to destroy {obj_type.name} ID {obj.id}: {e}")

                logger.info(f"Destroyed {type_destroyed} {obj_type.name} objects")

            logger.info(f"Total objects destroyed: {destroyed_count}")
            return True

        except Exception as e:
            logger.error(f"Failed during key destruction: {e}")
            return False

    def delete_all_certificates(self) -> bool:
        """Delete all certificates stored as opaque objects"""
        try:
            logger.info("Deleting certificate objects...")

            objects = self.session.list_objects()
            cert_count = 0

            for obj in objects:
                if obj.object_type == OBJECT.OPAQUE and obj.label.startswith(('cert-', 'Cert')):
                    try:
                        self.session.delete_object(obj.id, obj.object_type)
                        logger.info(f"Deleted certificate: {obj.label}")
                        self._audit_event("CERT_DELETE", f"{obj.id}:{obj.label}")
                        cert_count += 1
                    except Exception as e:
                        logger.error(f"Failed to delete certificate {obj.id}: {e}")

            logger.info(f"Deleted {cert_count} certificate objects")
            return True

        except Exception as e:
            logger.error(f"Failed during certificate deletion: {e}")
            return False

    def factory_reset_hsm(self) -> bool:
        """Perform factory reset of the YubiHSM"""
        try:
            logger.warning("Performing factory reset - this will erase ALL data!")

            # Confirm with user
            confirm = input("Type 'FACTORY RESET' to confirm: ")
            if confirm != "FACTORY RESET":
                logger.info("Factory reset cancelled")
                return False

            # Perform reset
            self.session.reset_device()
            logger.info("Factory reset completed successfully")
            self._audit_event("FACTORY_RESET", "Device reset to factory defaults")

            return True

        except Exception as e:
            logger.error(f"Factory reset failed: {e}")
            return False

    def verify_hsm_empty(self) -> bool:
        """Verify that HSM is completely empty after destruction"""
        try:
            logger.info("Verifying HSM is empty...")

            objects = self.session.list_objects()

            if len(objects) == 0:
                logger.info("✓ HSM verification successful: No objects remaining")
                self._audit_event("VERIFICATION", "HSM confirmed empty")
                return True
            else:
                logger.error(f"✗ HSM verification failed: {len(objects)} objects still present")
                for obj in objects:
                    logger.error(f"  Remaining: {obj.object_type.name} ID {obj.id}: {obj.label}")
                return False

        except Exception as e:
            logger.error(f"HSM verification failed: {e}")
            return False

    def _audit_event(self, action: str, details: str):
        """Log audit event"""
        timestamp = datetime.now().isoformat()
        event = {
            'timestamp': timestamp,
            'action': action,
            'details': details,
            'session': id(self.session) if self.session else None
        }
        self.audit_log.append(event)
        logger.info(f"AUDIT: {action} - {details}")

    def get_audit_log(self) -> List[Dict]:
        """Get the audit log"""
        return self.audit_log.copy()

    def destroy_all(self, backup_file: Optional[str] = None, skip_confirm: bool = False) -> bool:
        """Complete destruction sequence"""
        try:
            logger.info("=== STARTING YUBIHSM COMPLETE DESTRUCTION ===")

            # Confirm before anything is destroyed, not after. Steps 2 and 3
            # erase every key and certificate on the device and there is no
            # recovery, so asking at step 4 would be asking too late.
            if not skip_confirm:
                print("This erases every key and certificate on the HSM. "
                      "There is no recovery.")
                print("Type 'DESTROY HSM' to confirm:")
                if input().strip() != 'DESTROY HSM':
                    logger.info("Destruction cancelled by user")
                    return False

            # Step 1: Export audit logs
            if not self.export_audit_logs(backup_file):
                logger.error("Failed to export audit logs")
                return False

            # Step 2: Destroy all keys
            if not self.destroy_all_keys():
                logger.error("Failed to destroy keys")
                return False

            # Step 3: Delete certificates
            if not self.delete_all_certificates():
                logger.error("Failed to delete certificates")
                return False

            # Step 4: Factory reset
            if not self.factory_reset_hsm():
                logger.error("Factory reset cancelled or failed")
                return False

            # Step 5: Verify empty
            if not self.verify_hsm_empty():
                logger.error("HSM verification failed")
                return False

            logger.info("=== YUBIHSM DESTRUCTION COMPLETED SUCCESSFULLY ===")
            return True

        except Exception as e:
            logger.error(f"Destruction sequence failed: {e}")
            return False

def main():
    """CLI interface for YubiHSM destruction"""
    import argparse

    parser = argparse.ArgumentParser(description='YubiHSM 2 Complete Destruction Tool')
    parser.add_argument('--connector-url', default='http://localhost:12345',
                       help='YubiHSM connector URL')
    parser.add_argument('--auth-key-id', type=int, default=2,
                       help='Authentication key ID')
    parser.add_argument('--export-audit', action='store_true',
                       help='Export audit logs before destruction')
    parser.add_argument('--backup-file', help='File to save audit backup')
    parser.add_argument('--destroy-all', action='store_true',
                       help='Perform complete destruction')
    parser.add_argument('--verify', action='store_true',
                       help='Verify HSM is empty')
    parser.add_argument('--skip-confirm', action='store_true',
                       help='Skip confirmation prompts')

    args = parser.parse_args()

    destroyer = YubiHSMDestroyer(args.connector_url)

    try:
        # Connect to HSM
        if not destroyer.connect(args.auth_key_id):
            sys.exit(1)

        success = True

        # Export audit logs if requested
        if args.export_audit:
            success &= destroyer.export_audit_logs(args.backup_file)

        # Perform complete destruction if requested
        if args.destroy_all:
            success &= destroyer.destroy_all(args.backup_file, args.skip_confirm)

        # Verify empty if requested
        if args.verify:
            success &= destroyer.verify_hsm_empty()

        # Print audit log
        audit_log = destroyer.get_audit_log()
        if audit_log:
            print("\n=== AUDIT LOG ===")
            for event in audit_log:
                print(f"{event['timestamp']} | {event['action']} | {event['details']}")

        sys.exit(0 if success else 1)

    except KeyboardInterrupt:
        logger.info("Operation cancelled by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)
    finally:
        destroyer.disconnect()

if __name__ == '__main__':
    main()