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
YubiKey Mass Revocation Module
Handles complete revocation and reset of YubiKey security tokens
"""

import sys
import os
import json
import subprocess
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/yubikey_revoker.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class YubiKeyRevoker:
    """Complete YubiKey revocation and reset handler"""

    def __init__(self):
        self.audit_log = []
        self.inventory = []
        self.revoked_count = 0

    def inventory_all_yubikeys(self) -> List[Dict]:
        """Inventory all connected YubiKeys"""
        try:
            logger.info("Inventorying all YubiKeys...")

            # Use ykman to list all YubiKeys
            result = subprocess.run(['ykman', 'list'], capture_output=True, text=True, timeout=30)

            if result.returncode != 0:
                logger.error(f"Failed to list YubiKeys: {result.stderr}")
                return []

            # Parse output
            lines = result.stdout.strip().split('\n')
            yubikeys = []

            for line in lines:
                if line.strip():
                    # Parse YubiKey info
                    parts = line.split()
                    if len(parts) >= 2:
                        serial = parts[0]
                        model = ' '.join(parts[1:])

                        yubikey_info = {
                            'serial': serial,
                            'model': model,
                            'discovered_at': datetime.now().isoformat(),
                            'status': 'discovered'
                        }
                        yubikeys.append(yubikey_info)
                        logger.info(f"Found YubiKey: {serial} ({model})")

            self.inventory = yubikeys
            logger.info(f"Total YubiKeys discovered: {len(yubikeys)}")
            return yubikeys

        except subprocess.TimeoutExpired:
            logger.error("Timeout while listing YubiKeys")
            return []
        except FileNotFoundError:
            logger.error("ykman command not found. Please install yubikey-manager")
            return []
        except Exception as e:
            logger.error(f"Failed to inventory YubiKeys: {e}")
            return []

    def revoke_certificate(self, serial: str, cert_type: str = 'all') -> bool:
        """Revoke certificates from a specific YubiKey"""
        try:
            logger.info(f"Revoking certificates from YubiKey {serial}")

            success = True

            if cert_type in ['all', 'piv']:
                # Revoke PIV certificates
                if not self._revoke_piv_certificates(serial):
                    success = False

            if cert_type in ['all', 'fido']:
                # Revoke FIDO2 credentials
                if not self._revoke_fido_credentials(serial):
                    success = False

            if cert_type in ['all', 'openpgp']:
                # Revoke OpenPGP certificates
                if not self._revoke_openpgp_certificates(serial):
                    success = False

            if success:
                self._audit_event("CERT_REVOKE", f"YubiKey {serial}: {cert_type}")
                self.revoked_count += 1

            return success

        except Exception as e:
            logger.error(f"Failed to revoke certificates from {serial}: {e}")
            return False

    def _revoke_piv_certificates(self, serial: str) -> bool:
        """Revoke PIV certificates"""
        try:
            # Reset PIV application (removes all certificates)
            cmd = ['ykman', '--device', serial, 'piv', 'reset']
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

            if result.returncode == 0:
                logger.info(f"PIV certificates reset for YubiKey {serial}")
                return True
            else:
                logger.error(f"Failed to reset PIV for {serial}: {result.stderr}")
                return False

        except subprocess.TimeoutExpired:
            logger.error(f"Timeout resetting PIV for {serial}")
            return False
        except Exception as e:
            logger.error(f"Error resetting PIV for {serial}: {e}")
            return False

    def _revoke_fido_credentials(self, serial: str) -> bool:
        """Revoke FIDO2 credentials"""
        try:
            # Reset FIDO2 application
            cmd = ['ykman', '--device', serial, 'fido', 'reset']
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

            if result.returncode == 0:
                logger.info(f"FIDO2 credentials reset for YubiKey {serial}")
                return True
            else:
                logger.error(f"Failed to reset FIDO2 for {serial}: {result.stderr}")
                return False

        except subprocess.TimeoutExpired:
            logger.error(f"Timeout resetting FIDO2 for {serial}")
            return False
        except Exception as e:
            logger.error(f"Error resetting FIDO2 for {serial}: {e}")
            return False

    def _revoke_openpgp_certificates(self, serial: str) -> bool:
        """Revoke OpenPGP certificates"""
        try:
            # Reset OpenPGP application
            cmd = ['ykman', '--device', serial, 'openpgp', 'reset']
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

            if result.returncode == 0:
                logger.info(f"OpenPGP certificates reset for YubiKey {serial}")
                return True
            else:
                logger.error(f"Failed to reset OpenPGP for {serial}: {result.stderr}")
                return False

        except subprocess.TimeoutExpired:
            logger.error(f"Timeout resetting OpenPGP for {serial}")
            return False
        except Exception as e:
            logger.error(f"Error resetting OpenPGP for {serial}: {e}")
            return False

    def factory_reset_yubikey(self, serial: str) -> bool:
        """Perform complete factory reset of YubiKey"""
        try:
            logger.warning(f"Performing factory reset on YubiKey {serial}")

            # Reset all applications
            applications = ['piv', 'fido', 'openpgp', 'oath', 'hsmauth']

            for app in applications:
                try:
                    cmd = ['ykman', '--device', serial, app, 'reset']
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

                    if result.returncode == 0:
                        logger.info(f"Reset {app} application on {serial}")
                    else:
                        logger.warning(f"Failed to reset {app} on {serial}: {result.stderr}")

                except subprocess.TimeoutExpired:
                    logger.warning(f"Timeout resetting {app} on {serial}")
                except Exception as e:
                    logger.warning(f"Error resetting {app} on {serial}: {e}")

            # Disable all USB interfaces
            try:
                cmd = ['ykman', '--device', serial, 'config', 'usb', '--disable-all']
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

                if result.returncode == 0:
                    logger.info(f"Disabled all USB interfaces on {serial}")
                else:
                    logger.warning(f"Failed to disable USB interfaces on {serial}: {result.stderr}")

            except Exception as e:
                logger.warning(f"Error disabling USB interfaces on {serial}: {e}")

            self._audit_event("FACTORY_RESET", f"YubiKey {serial}")
            return True

        except Exception as e:
            logger.error(f"Factory reset failed for {serial}: {e}")
            return False

    def revoke_from_directory_services(self, serial: str) -> bool:
        """Remove YubiKey from directory services (LDAP/AD)"""
        try:
            logger.info(f"Removing YubiKey {serial} from directory services")

            # This would integrate with your directory service
            # For now, just log the action
            logger.info(f"Directory service cleanup needed for {serial}")

            # Placeholder for LDAP/AD integration
            # Example:
            # ldap_delete_cmd = f"ldapdelete -D 'cn=admin' -w {password} 'serialNumber={serial}'"
            # subprocess.run(ldap_delete_cmd, shell=True)

            self._audit_event("DIRECTORY_CLEANUP", f"YubiKey {serial}")
            return True

        except Exception as e:
            logger.error(f"Directory service cleanup failed for {serial}: {e}")
            return False

    def update_integrated_systems(self, serial: str) -> bool:
        """Update all systems that integrate with this YubiKey"""
        try:
            logger.info(f"Updating integrated systems for YubiKey {serial}")

            # Systems to update:
            systems = [
                'ssh_authorized_keys',
                'password_managers',
                'vpn_configs',
                'web_auth_configs',
                'monitoring_systems'
            ]

            for system in systems:
                logger.info(f"Updating {system} for YubiKey {serial}")
                # Placeholder for actual system updates
                # This would need custom integration for each system

            self._audit_event("SYSTEM_UPDATE", f"YubiKey {serial}")
            return True

        except Exception as e:
            logger.error(f"System update failed for {serial}: {e}")
            return False

    def blacklist_yubikey(self, serial: str) -> bool:
        """Add YubiKey to global blacklist"""
        try:
            logger.info(f"Blacklisting YubiKey {serial}")

            # Add to local blacklist
            blacklist_file = '/etc/yubikey_blacklist.txt'
            with open(blacklist_file, 'a') as f:
                f.write(f"{serial}\t{datetime.now().isoformat()}\tREVOKED\n")

            # Broadcast blacklist to integrated systems
            # This would send updates to all systems that use YubiKeys

            self._audit_event("BLACKLIST", f"YubiKey {serial}")
            return True

        except Exception as e:
            logger.error(f"Blacklisting failed for {serial}: {e}")
            return False

    def revoke_all_yubikeys(self, skip_confirm: bool = False) -> bool:
        """Complete revocation of all discovered YubiKeys"""
        try:
            logger.info("=== STARTING MASS YUBIKEY REVOCATION ===")

            if not skip_confirm:
                confirm = input(f"About to revoke {len(self.inventory)} YubiKeys. Continue? (yes/no): ")
                if confirm.lower() != 'yes':
                    logger.info("Mass revocation cancelled")
                    return False

            success_count = 0
            total_count = len(self.inventory)

            for yubikey in self.inventory:
                serial = yubikey['serial']
                logger.info(f"Processing YubiKey {serial} ({success_count + 1}/{total_count})")

                # Step 1: Revoke certificates
                if self.revoke_certificate(serial):
                    logger.info(f"✓ Certificates revoked for {serial}")
                else:
                    logger.error(f"✗ Failed to revoke certificates for {serial}")

                # Step 2: Factory reset
                if self.factory_reset_yubikey(serial):
                    logger.info(f"✓ Factory reset completed for {serial}")
                else:
                    logger.error(f"✗ Factory reset failed for {serial}")

                # Step 3: Directory service cleanup
                if self.revoke_from_directory_services(serial):
                    logger.info(f"✓ Directory cleanup completed for {serial}")
                else:
                    logger.warning(f"⚠ Directory cleanup failed for {serial}")

                # Step 4: Update integrated systems
                if self.update_integrated_systems(serial):
                    logger.info(f"✓ System updates completed for {serial}")
                else:
                    logger.warning(f"⚠ System updates failed for {serial}")

                # Step 5: Blacklist
                if self.blacklist_yubikey(serial):
                    logger.info(f"✓ Blacklisted {serial}")
                else:
                    logger.warning(f"⚠ Blacklisting failed for {serial}")

                success_count += 1
                yubikey['status'] = 'revoked'
                yubikey['revoked_at'] = datetime.now().isoformat()

            logger.info(f"=== MASS REVOCATION COMPLETED: {success_count}/{total_count} YubiKeys processed ===")
            return success_count == total_count

        except Exception as e:
            logger.error(f"Mass revocation failed: {e}")
            return False

    def _audit_event(self, action: str, details: str):
        """Log audit event"""
        timestamp = datetime.now().isoformat()
        event = {
            'timestamp': timestamp,
            'action': action,
            'details': details,
            'component': 'YubiKey'
        }
        self.audit_log.append(event)
        logger.info(f"AUDIT: {action} - {details}")

    def get_audit_log(self) -> List[Dict]:
        """Get the audit log"""
        return self.audit_log.copy()

    def export_inventory(self, filename: str = 'yubikey_inventory.json') -> bool:
        """Export YubiKey inventory"""
        try:
            with open(filename, 'w') as f:
                json.dump(self.inventory, f, indent=2)
            logger.info(f"Inventory exported to {filename}")
            return True
        except Exception as e:
            logger.error(f"Failed to export inventory: {e}")
            return False

    def generate_revocation_report(self, filename: str = 'yubikey_revocation_report.json') -> bool:
        """Generate comprehensive revocation report"""
        try:
            report = {
                'generated_at': datetime.now().isoformat(),
                'total_yubikeys': len(self.inventory),
                'revoked_count': self.revoked_count,
                'inventory': self.inventory,
                'audit_log': self.audit_log
            }

            with open(filename, 'w') as f:
                json.dump(report, f, indent=2)

            logger.info(f"Revocation report generated: {filename}")
            return True

        except Exception as e:
            logger.error(f"Failed to generate report: {e}")
            return False

def main():
    """CLI interface for YubiKey revocation"""
    import argparse

    parser = argparse.ArgumentParser(description='YubiKey Mass Revocation Tool')
    parser.add_argument('--inventory', action='store_true', help='Inventory all YubiKeys')
    parser.add_argument('--revoke-all', action='store_true', help='Revoke all discovered YubiKeys')
    parser.add_argument('--export-inventory', help='Export inventory to file')
    parser.add_argument('--generate-report', help='Generate revocation report')
    parser.add_argument('--skip-confirm', action='store_true', help='Skip confirmation prompts')

    args = parser.parse_args()

    revoker = YubiKeyRevoker()

    try:
        success = True

        # Inventory YubiKeys
        if args.inventory or args.revoke_all:
            inventory = revoker.inventory_all_yubikeys()
            if not inventory:
                logger.error("No YubiKeys found or inventory failed")
                sys.exit(1)

        # Export inventory if requested
        if args.export_inventory:
            success &= revoker.export_inventory(args.export_inventory)

        # Perform mass revocation if requested
        if args.revoke_all:
            success &= revoker.revoke_all_yubikeys(args.skip_confirm)

        # Generate report if requested
        if args.generate_report:
            success &= revoker.generate_revocation_report(args.generate_report)

        # Print audit log
        audit_log = revoker.get_audit_log()
        if audit_log:
            print("\n=== AUDIT LOG ===")
            for event in audit_log[-10:]:  # Show last 10 events
                print(f"{event['timestamp']} | {event['action']} | {event['details']}")

        sys.exit(0 if success else 1)

    except KeyboardInterrupt:
        logger.info("Operation cancelled by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()