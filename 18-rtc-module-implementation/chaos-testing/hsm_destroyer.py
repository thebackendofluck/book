#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 18, Real-Time Clock Module Implementation.
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
Handles complete wipe of YubiHSM 2 FIPS hardware security modules.

This module is used during secure decommissioning of gambling infrastructure
to ensure all cryptographic material is irrecoverably destroyed. It follows
a strict sequence: audit export -> key destruction -> certificate deletion ->
factory reset -> verification.

Usage:
    python3 hsm_destroyer.py --export-audit --backup-file audit_backup.json
    python3 hsm_destroyer.py --destroy-all --backup-file audit_backup.json
    python3 hsm_destroyer.py --verify

Requires:
    pip install yubihsm[http,usb] cryptography
"""

import sys
import os
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path

try:
    from yubihsm import YubiHsm  # ty:ignore[unresolved-import]
    from yubihsm.defs import CAPABILITY, ALGORITHM, OBJECT, COMMAND  # ty:ignore[unresolved-import]
    from yubihsm.objects import AuthenticationKey, WrapKey, AsymmetricKey, SymmetricKey, Opaque  # ty:ignore[unresolved-import]
except ImportError as e:
    print(f"Error: Required module not found. Please install: pip install yubihsm[http,usb] cryptography")
    print(f"Missing module: {e}")
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/hsm_destroyer.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class HSMDestroyer:
    """Complete YubiHSM 2 destruction handler for secure decommissioning"""

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
                password = os.getenv('YUBIHSM_PASSWORD')
            if not password:
                import getpass
                password = getpass.getpass(f"Enter password for auth key {auth_key_id}: ")

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

    def export_audit_logs(self, backup_file: str = None) -> bool:  # ty:ignore[invalid-parameter-default]
        """Export final audit logs before destruction"""
        try:
            logger.info("Exporting final audit logs...")

            device_info = self.session.get_device_info()  # ty:ignore[unresolved-attribute]

            audit_entries = []
            try:
                logs = self.session.get_log_entries()  # ty:ignore[unresolved-attribute]
                for log_entry in logs:
                    audit_entries.append({
                        'timestamp': log_entry.timestamp.isoformat()
                            if hasattr(log_entry, 'timestamp') else datetime.now().isoformat(),
                        'command': log_entry.command.name
                            if hasattr(log_entry, 'command') else 'unknown',
                        'result': log_entry.result.name
                            if hasattr(log_entry, 'result') else 'unknown',
                    })
            except Exception as e:
                logger.warning(f"Could not retrieve audit logs: {e}")
                audit_entries = [{"error": "Could not retrieve audit logs",
                                  "timestamp": datetime.now().isoformat()}]

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
            objects = self.session.list_objects()  # ty:ignore[unresolved-attribute]
            inventory = {
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

            objects = self.session.list_objects()  # ty:ignore[unresolved-attribute]
            destroyed_count = 0

            # Destroy in specific order for safety
            destroy_order = [
                OBJECT.SYMMETRIC_KEY,
                OBJECT.ASYMMETRIC_KEY,
                OBJECT.WRAP_KEY,
                OBJECT.OPAQUE
            ]

            for obj_type in destroy_order:
                type_destroyed = 0
                for obj in objects:
                    if obj.object_type == obj_type:
                        try:
                            self.session.delete_object(obj.id, obj.object_type)  # ty:ignore[unresolved-attribute]
                            logger.info(f"Destroyed {obj_type.name} ID {obj.id}: {obj.label}")
                            self._audit_event("KEY_DESTROY",
                                              f"{obj_type.name}:{obj.id}:{obj.label}")
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

    def factory_reset_hsm(self) -> bool:
        """Perform factory reset of the YubiHSM"""
        try:
            logger.warning("Performing factory reset - this will erase ALL data!")

            confirm = input("Type 'FACTORY RESET' to confirm: ")
            if confirm != "FACTORY RESET":
                logger.info("Factory reset cancelled")
                return False

            self.session.reset_device()  # ty:ignore[unresolved-attribute]
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

            objects = self.session.list_objects()  # ty:ignore[unresolved-attribute]

            if len(objects) == 0:
                logger.info("HSM verification successful: No objects remaining")
                self._audit_event("VERIFICATION", "HSM confirmed empty")
                return True
            else:
                logger.error(f"HSM verification failed: {len(objects)} objects still present")
                for obj in objects:
                    logger.error(f"  Remaining: {obj.object_type.name} ID {obj.id}: {obj.label}")
                return False

        except Exception as e:
            logger.error(f"HSM verification failed: {e}")
            return False

    def _audit_event(self, action: str, details: str):
        """Log audit event"""
        event = {
            'timestamp': datetime.now().isoformat(),
            'action': action,
            'details': details,
            'session': id(self.session) if self.session else None
        }
        self.audit_log.append(event)
        logger.info(f"AUDIT: {action} - {details}")

    def get_audit_log(self) -> List[Dict]:
        """Get the audit log"""
        return self.audit_log.copy()

    def destroy_all(self, backup_file: str = None, skip_confirm: bool = False) -> bool:  # ty:ignore[invalid-parameter-default]
        """Complete destruction sequence"""
        try:
            logger.info("=== STARTING HSM COMPLETE DESTRUCTION ===")

            if not self.export_audit_logs(backup_file):
                logger.error("Failed to export audit logs")
                return False

            if not self.destroy_all_keys():
                logger.error("Failed to destroy keys")
                return False

            if not skip_confirm and not self.factory_reset_hsm():
                logger.error("Factory reset cancelled or failed")
                return False

            if not self.verify_hsm_empty():
                logger.error("HSM verification failed")
                return False

            logger.info("=== HSM DESTRUCTION COMPLETED SUCCESSFULLY ===")
            return True

        except Exception as e:
            logger.error(f"Destruction sequence failed: {e}")
            return False


def main():
    """CLI interface for HSM destruction"""
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

    destroyer = HSMDestroyer(args.connector_url)

    try:
        if not destroyer.connect(args.auth_key_id):
            sys.exit(1)

        success = True

        if args.export_audit:
            success &= destroyer.export_audit_logs(args.backup_file)

        if args.destroy_all:
            success &= destroyer.destroy_all(args.backup_file, args.skip_confirm)

        if args.verify:
            success &= destroyer.verify_hsm_empty()

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
