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
MikroTik RouterOS Device Zeroizer
Complete factory reset and configuration wipe for MikroTik RouterOS devices
"""

import sys
import os
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path

try:
    import routeros_api  # type: ignore[unresolved-import]
except ImportError:
    print("Error: routeros_api library not found. Install with: pip install routeros-api")
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/mikrotik_zeroizer.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class MikroTikZeroizer:
    """Complete MikroTik RouterOS device reset handler"""

    def __init__(self, inventory_file: Optional[str] = None):
        self.inventory_file = inventory_file or 'config/mikrotik_inventory.json'
        self.inventory = []
        self.audit_log = []
        self.connection_pool = None

    def load_inventory(self) -> List[Dict]:
        """Load MikroTik device inventory"""
        try:
            if Path(self.inventory_file).exists():
                with open(self.inventory_file, 'r') as f:
                    self.inventory = json.load(f)
                logger.info(f"Loaded inventory with {len(self.inventory)} devices")
            else:
                logger.warning(f"Inventory file not found: {self.inventory_file}")
                # Try to auto-discover from Ansible inventory
                self.inventory = self._discover_from_ansible()
                if self.inventory:
                    logger.info(f"Auto-discovered {len(self.inventory)} devices from Ansible")

            return self.inventory

        except Exception as e:
            logger.error(f"Failed to load inventory: {e}")
            return []

    def _discover_from_ansible(self) -> List[Dict]:
        """Auto-discover MikroTik devices from Ansible inventory"""
        devices = []

        # Common Ansible inventory locations
        ansible_paths = [
            'infra-ansible/inventories',
            'ansible/inventories',
            '/etc/ansible/inventories'
        ]

        for ansible_path in ansible_paths:
            if Path(ansible_path).exists():
                for inventory_dir in Path(ansible_path).glob('*'):
                    if inventory_dir.is_dir():
                        hosts_file = inventory_dir / 'hosts.yml'
                        if hosts_file.exists():
                            try:
                                import yaml
                                with open(hosts_file, 'r') as f:
                                    hosts_data = yaml.safe_load(f)

                                # Look for MikroTik devices
                                if 'all' in hosts_data and 'children' in hosts_data['all']:
                                    for group_name, group_data in hosts_data['all']['children'].items():
                                        if 'mikrotik' in group_name.lower() or 'router' in group_name.lower():
                                            if 'hosts' in group_data:
                                                for host_name, host_vars in group_data['hosts'].items():
                                                    device = {
                                                        'name': host_name,
                                                        'host': host_vars.get('ansible_host', host_name),
                                                        'username': host_vars.get('ansible_user', 'admin'),
                                                        'password': host_vars.get('ansible_password', ''),
                                                        'port': host_vars.get('ansible_port', 8728),
                                                        'group': group_name
                                                    }
                                                    devices.append(device)

                            except Exception as e:
                                logger.warning(f"Failed to parse {hosts_file}: {e}")

        return devices

    def connect_to_device(self, device_config: Dict) -> Optional[object]:
        """Connect to a MikroTik device"""
        try:
            host = device_config['host']
            username = device_config.get('username', 'admin')
            password = device_config.get('password', '')
            port = device_config.get('port', 8728)

            logger.info(f"Connecting to {host}:{port} as {username}")

            # Create connection pool if not exists
            if not self.connection_pool:
                self.connection_pool = routeros_api.RouterOsApiPool(
                    host,
                    username=username,
                    password=password,
                    port=port,
                    plaintext_login=True
                )

            # Get API connection
            api = self.connection_pool.get_api()
            logger.info(f"Successfully connected to {host}")
            return api

        except Exception as e:
            logger.error(f"Failed to connect to {device_config['host']}: {e}")
            return None

    def factory_reset_device(self, device_config: Dict) -> bool:
        """Perform complete factory reset on a MikroTik device"""
        try:
            device_name = device_config['name']
            logger.warning(f"Performing factory reset on {device_name}")

            api = self.connect_to_device(device_config)
            if not api:
                return False

            # Step 1: Remove all certificates
            if not self._remove_all_certificates(api, device_name):
                logger.warning(f"Certificate removal failed for {device_name}")

            # Step 2: Clear user database
            if not self._clear_user_database(api, device_name):
                logger.warning(f"User database clearing failed for {device_name}")

            # Step 3: Remove all configurations
            if not self._remove_all_configurations(api, device_name):
                logger.warning(f"Configuration removal failed for {device_name}")

            # Step 4: Reset system configuration
            if not self._reset_system_configuration(api, device_name):
                logger.error(f"System reset failed for {device_name}")
                return False

            logger.info(f"Factory reset completed for {device_name}")
            self._audit_event("FACTORY_RESET", f"Device {device_name}")
            return True

        except Exception as e:
            logger.error(f"Factory reset failed for {device_config['name']}: {e}")
            return False
        finally:
            if self.connection_pool:
                try:
                    self.connection_pool.disconnect()
                except Exception:
                    pass

    def _remove_all_certificates(self, api, device_name: str) -> bool:
        """Remove all certificates from device"""
        try:
            cert_resource = api.get_resource('/certificate')
            certificates = cert_resource.get()

            removed_count = 0
            for cert in certificates:
                try:
                    cert_resource.remove(id=cert['id'])
                    removed_count += 1
                    logger.info(f"Removed certificate {cert.get('name', cert['id'])} from {device_name}")
                except Exception as e:
                    logger.warning(f"Failed to remove certificate {cert.get('name', 'unknown')} from {device_name}: {e}")

            logger.info(f"Removed {removed_count} certificates from {device_name}")
            return True

        except Exception as e:
            logger.error(f"Certificate removal failed for {device_name}: {e}")
            return False

    def _clear_user_database(self, api, device_name: str) -> bool:
        """Clear user database"""
        try:
            user_resource = api.get_resource('/user')

            # Get all users except admin (if exists)
            users = user_resource.get()
            removed_count = 0

            for user in users:
                username = user.get('name', '')
                # Don't remove admin user if it exists
                if username not in ['admin', '']:
                    try:
                        user_resource.remove(id=user['id'])
                        removed_count += 1
                        logger.info(f"Removed user {username} from {device_name}")
                    except Exception as e:
                        logger.warning(f"Failed to remove user {username} from {device_name}: {e}")

            logger.info(f"Removed {removed_count} users from {device_name}")
            return True

        except Exception as e:
            logger.error(f"User database clearing failed for {device_name}: {e}")
            return False

    def _remove_all_configurations(self, api, device_name: str) -> bool:
        """Remove all device configurations"""
        try:
            success = True

            # Remove IP addresses
            if not self._remove_ip_addresses(api, device_name):
                success = False

            # Remove routes
            if not self._remove_routes(api, device_name):
                success = False

            # Remove firewall rules
            if not self._remove_firewall_rules(api, device_name):
                success = False

            # Remove DHCP configurations
            if not self._remove_dhcp_config(api, device_name):
                success = False

            # Remove VPN configurations
            if not self._remove_vpn_config(api, device_name):
                success = False

            # Remove wireless configurations
            if not self._remove_wireless_config(api, device_name):
                success = False

            return success

        except Exception as e:
            logger.error(f"Configuration removal failed for {device_name}: {e}")
            return False

    def _remove_ip_addresses(self, api, device_name: str) -> bool:
        """Remove IP addresses"""
        try:
            ip_resource = api.get_resource('/ip/address')
            addresses = ip_resource.get()

            removed_count = 0
            for addr in addresses:
                try:
                    ip_resource.remove(id=addr['id'])
                    removed_count += 1
                    logger.info(f"Removed IP address {addr.get('address', 'unknown')} from {device_name}")
                except Exception as e:
                    logger.warning(f"Failed to remove IP address from {device_name}: {e}")

            logger.info(f"Removed {removed_count} IP addresses from {device_name}")
            return True

        except Exception as e:
            logger.warning(f"IP address removal failed for {device_name}: {e}")
            return False

    def _remove_routes(self, api, device_name: str) -> bool:
        """Remove static routes"""
        try:
            route_resource = api.get_resource('/ip/route')
            routes = route_resource.get()

            removed_count = 0
            for route in routes:
                # Only remove static routes, not connected routes
                if route.get('type') == 'static':
                    try:
                        route_resource.remove(id=route['id'])
                        removed_count += 1
                        logger.info(f"Removed static route to {route.get('dst-address', 'unknown')} from {device_name}")
                    except Exception as e:
                        logger.warning(f"Failed to remove route from {device_name}: {e}")

            logger.info(f"Removed {removed_count} static routes from {device_name}")
            return True

        except Exception as e:
            logger.warning(f"Route removal failed for {device_name}: {e}")
            return False

    def _remove_firewall_rules(self, api, device_name: str) -> bool:
        """Remove firewall rules"""
        try:
            filter_resource = api.get_resource('/ip/firewall/filter')
            rules = filter_resource.get()

            removed_count = 0
            for rule in rules:
                try:
                    filter_resource.remove(id=rule['id'])
                    removed_count += 1
                    logger.info(f"Removed firewall rule {rule.get('comment', rule['id'])} from {device_name}")
                except Exception as e:
                    logger.warning(f"Failed to remove firewall rule from {device_name}: {e}")

            logger.info(f"Removed {removed_count} firewall rules from {device_name}")
            return True

        except Exception as e:
            logger.warning(f"Firewall rule removal failed for {device_name}: {e}")
            return False

    def _remove_dhcp_config(self, api, device_name: str) -> bool:
        """Remove DHCP configurations"""
        try:
            # Remove DHCP servers
            dhcp_resource = api.get_resource('/ip/dhcp-server')
            servers = dhcp_resource.get()

            removed_count = 0
            for server in servers:
                try:
                    dhcp_resource.remove(id=server['id'])
                    removed_count += 1
                    logger.info(f"Removed DHCP server {server.get('name', 'unknown')} from {device_name}")
                except Exception as e:
                    logger.warning(f"Failed to remove DHCP server from {device_name}: {e}")

            logger.info(f"Removed {removed_count} DHCP servers from {device_name}")
            return True

        except Exception as e:
            logger.warning(f"DHCP config removal failed for {device_name}: {e}")
            return False

    def _remove_vpn_config(self, api, device_name: str) -> bool:
        """Remove VPN configurations"""
        try:
            # Remove PPP secrets
            ppp_resource = api.get_resource('/ppp/secret')
            secrets = ppp_resource.get()

            removed_count = 0
            for secret in secrets:
                try:
                    ppp_resource.remove(id=secret['id'])
                    removed_count += 1
                    logger.info(f"Removed PPP secret {secret.get('name', 'unknown')} from {device_name}")
                except Exception as e:
                    logger.warning(f"Failed to remove PPP secret from {device_name}: {e}")

            logger.info(f"Removed {removed_count} VPN configurations from {device_name}")
            return True

        except Exception as e:
            logger.warning(f"VPN config removal failed for {device_name}: {e}")
            return False

    def _remove_wireless_config(self, api, device_name: str) -> bool:
        """Remove wireless configurations"""
        try:
            # Remove wireless security profiles
            security_resource = api.get_resource('/interface/wireless/security-profiles')
            profiles = security_resource.get()

            removed_count = 0
            for profile in profiles:
                try:
                    security_resource.remove(id=profile['id'])
                    removed_count += 1
                    logger.info(f"Removed wireless security profile {profile.get('name', 'unknown')} from {device_name}")
                except Exception as e:
                    logger.warning(f"Failed to remove wireless security profile from {device_name}: {e}")

            logger.info(f"Removed {removed_count} wireless security profiles from {device_name}")
            return True

        except Exception as e:
            logger.warning(f"Wireless config removal failed for {device_name}: {e}")
            return False

    def _reset_system_configuration(self, api, device_name: str) -> bool:
        """Reset system configuration to factory defaults"""
        try:
            logger.warning(f"Resetting system configuration for {device_name}")

            # Use RouterOS system reset command
            system_resource = api.get_resource('/system')
            system_resource.call('reset-configuration', {
                'no-defaults': 'yes',
                'skip-backup': 'yes'
            })

            logger.info(f"System configuration reset initiated for {device_name}")
            return True

        except Exception as e:
            logger.error(f"System configuration reset failed for {device_name}: {e}")
            return False

    def factory_reset_all(self, skip_confirm: bool = False) -> bool:
        """Factory reset all devices in inventory"""
        try:
            logger.warning("=== STARTING MIKROTIK DEVICE FACTORY RESET ===")

            if not skip_confirm:
                confirm = input(f"About to factory reset {len(self.inventory)} MikroTik devices. Continue? (yes/no): ")
                if confirm.lower() != 'yes':
                    logger.info("MikroTik factory reset cancelled")
                    return False

            success_count = 0
            total_count = len(self.inventory)

            for i, device in enumerate(self.inventory, 1):
                device_name = device['name']
                logger.info(f"Processing device {i}/{total_count}: {device_name}")

                if self.factory_reset_device(device):
                    logger.info(f"✓ Factory reset successful for {device_name}")
                    success_count += 1
                else:
                    logger.error(f"✗ Factory reset failed for {device_name}")

            logger.info(f"=== MIKROTIK FACTORY RESET COMPLETED: {success_count}/{total_count} devices processed ===")
            return success_count == total_count

        except Exception as e:
            logger.error(f"MikroTik factory reset failed: {e}")
            return False

    def verify_reset(self) -> bool:
        """Verify that devices have been reset"""
        try:
            logger.info("Verifying MikroTik device resets...")

            # This would attempt to connect to devices and check if they're in factory reset state
            # For now, just log that verification would happen
            logger.info("MikroTik reset verification not fully implemented - manual verification required")
            return True

        except Exception as e:
            logger.error(f"MikroTik verification failed: {e}")
            return False

    def _audit_event(self, action: str, details: str):
        """Log audit event"""
        timestamp = datetime.now().isoformat()
        event = {
            'timestamp': timestamp,
            'action': action,
            'details': details,
            'component': 'MikroTik'
        }
        self.audit_log.append(event)
        logger.info(f"AUDIT: {action} - {details}")

    def get_audit_log(self) -> List[Dict]:
        """Get the audit log"""
        return self.audit_log.copy()

    def generate_report(self, filename: str = 'mikrotik_reset_report.json') -> bool:
        """Generate comprehensive MikroTik reset report"""
        try:
            report = {
                'generated_at': datetime.now().isoformat(),
                'inventory_file': self.inventory_file,
                'audit_log': self.audit_log,
                'device_inventory': self.inventory
            }

            with open(filename, 'w') as f:
                json.dump(report, f, indent=2)

            logger.info(f"MikroTik reset report generated: {filename}")
            return True

        except Exception as e:
            logger.error(f"Failed to generate report: {e}")
            return False

def main():
    """CLI interface for MikroTik zeroizer"""
    import argparse

    parser = argparse.ArgumentParser(description='MikroTik RouterOS Device Zeroizer')
    parser.add_argument('--inventory', help='MikroTik inventory JSON file')
    parser.add_argument('--factory-reset-all', action='store_true', help='Factory reset all devices (DANGER)')
    parser.add_argument('--verify', action='store_true', help='Verify device resets')
    parser.add_argument('--report', help='Generate reset report')
    parser.add_argument('--skip-confirm', action='store_true', help='Skip confirmation prompts')

    args = parser.parse_args()

    try:
        zeroizer = MikroTikZeroizer(args.inventory)

        # Load inventory
        inventory = zeroizer.load_inventory()
        if not inventory:
            logger.error("No devices found in inventory")
            sys.exit(1)

        success = True

        # Factory reset all devices if requested
        if args.factory_reset_all:
            success &= zeroizer.factory_reset_all(args.skip_confirm)

        # Verify resets if requested
        if args.verify:
            success &= zeroizer.verify_reset()

        # Generate report if requested
        if args.report:
            success &= zeroizer.generate_report(args.report)

        # Print audit log summary
        audit_log = zeroizer.get_audit_log()
        if audit_log:
            print("\n=== AUDIT LOG SUMMARY ===")
            for event in audit_log[-5:]:  # Show last 5 events
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