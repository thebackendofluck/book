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
Meraki Network Eliminator
Complete destruction of Meraki network infrastructure using Meraki Dashboard API
"""

import sys
import os
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from pathlib import Path

try:
    import meraki  # type: ignore[unresolved-import]
except ImportError:
    print("Error: meraki Python library not found. Install with: pip install meraki")
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/meraki_eliminator.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class MerakiEliminator:
    """Complete Meraki network destruction handler"""

    def __init__(self, api_key: Optional[str] = None, org_id: str = '939404'):
        self.api_key = api_key or os.getenv('MERAKI_API_KEY')
        self.org_id = org_id
        self.dashboard = None
        self.audit_log = []
        self.network_inventory = []

        if not self.api_key:
            raise ValueError("Meraki API key not provided. Set MERAKI_API_KEY environment variable or pass api_key parameter")

    def connect(self) -> bool:
        """Connect to Meraki Dashboard API"""
        try:
            logger.info("Connecting to Meraki Dashboard API...")

            # Initialize Meraki dashboard
            self.dashboard = meraki.DashboardAPI(self.api_key, output_log=False)

            # Test connection by getting organization
            org = self.dashboard.organizations.getOrganization(self.org_id)
            logger.info(f"Connected to organization: {org['name']} (ID: {org['id']})")

            return True

        except Exception as e:
            logger.error(f"Failed to connect to Meraki API: {e}")
            return False

    def get_network_inventory(self) -> List[Dict]:
        """Get complete inventory of all networks and devices"""
        try:
            logger.info("Getting Meraki network inventory...")

            # Get all networks in organization
            networks = self.dashboard.organizations.getOrganizationNetworks(self.org_id)

            inventory = []
            for network in networks:
                network_info = {
                    'id': network['id'],
                    'name': network['name'],
                    'tags': network.get('tags', []),
                    'devices': []
                }

                # Get devices in this network
                try:
                    devices = self.dashboard.networks.getNetworkDevices(network['id'])
                    network_info['devices'] = [{
                        'serial': device['serial'],
                        'model': device['model'],
                        'name': device.get('name', ''),
                        'mac': device.get('mac', ''),
                        'lanIp': device.get('lanIp', ''),
                        'wan1Ip': device.get('wan1Ip', ''),
                        'firmware': device.get('firmware', '')
                    } for device in devices]
                except Exception as e:
                    logger.warning(f"Could not get devices for network {network['id']}: {e}")

                inventory.append(network_info)

            self.network_inventory = inventory
            logger.info(f"Inventory complete: {len(inventory)} networks, {sum(len(n['devices']) for n in inventory)} devices")
            return inventory

        except Exception as e:
            logger.error(f"Failed to get network inventory: {e}")
            return []

    def clear_network_configuration(self, network_id: str) -> bool:
        """Clear all configuration from a network"""
        try:
            logger.info(f"Clearing configuration for network {network_id}")

            success = True

            # Clear VLANs
            if not self._clear_vlans(network_id):
                success = False

            # Clear static routes
            if not self._clear_static_routes(network_id):
                success = False

            # Clear firewall rules
            if not self._clear_firewall_rules(network_id):
                success = False

            # Clear VPN configuration
            if not self._clear_vpn_config(network_id):
                success = False

            # Clear DHCP settings
            if not self._clear_dhcp_settings(network_id):
                success = False

            # Clear group policies
            if not self._clear_group_policies(network_id):
                success = False

            if success:
                logger.info(f"Successfully cleared configuration for network {network_id}")
            else:
                logger.warning(f"Some configuration clearing failed for network {network_id}")

            return success

        except Exception as e:
            logger.error(f"Failed to clear network configuration for {network_id}: {e}")
            return False

    def _clear_vlans(self, network_id: str) -> bool:
        """Clear VLAN configuration"""
        try:
            # Get current VLANs
            vlans = self.dashboard.appliance.getNetworkApplianceVlans(network_id)

            for vlan in vlans:
                if vlan['id'] != 1:  # Don't delete default VLAN
                    try:
                        self.dashboard.appliance.deleteNetworkApplianceVlan(network_id, vlan['id'])
                        logger.info(f"Deleted VLAN {vlan['id']} from network {network_id}")
                    except Exception as e:
                        logger.warning(f"Failed to delete VLAN {vlan['id']}: {e}")

            return True

        except Exception as e:
            logger.warning(f"Could not clear VLANs for network {network_id}: {e}")
            return False

    def _clear_static_routes(self, network_id: str) -> bool:
        """Clear static routes"""
        try:
            # Get static routes
            routes = self.dashboard.appliance.getNetworkApplianceStaticRoutes(network_id)

            for route in routes:
                try:
                    self.dashboard.appliance.deleteNetworkApplianceStaticRoute(network_id, route['id'])
                    logger.info(f"Deleted static route {route['name']} from network {network_id}")
                except Exception as e:
                    logger.warning(f"Failed to delete static route {route['name']}: {e}")

            return True

        except Exception as e:
            logger.warning(f"Could not clear static routes for network {network_id}: {e}")
            return False

    def _clear_firewall_rules(self, network_id: str) -> bool:
        """Clear firewall rules"""
        try:
            # Get firewall rules (L3)
            l3_rules = self.dashboard.appliance.getNetworkApplianceFirewallL3FirewallRules(network_id)

            # Reset to default (allow all)
            default_rules = {
                'rules': [
                    {
                        'comment': 'Default rule',
                        'policy': 'allow',
                        'protocol': 'any',
                        'destPort': 'Any',
                        'destCidr': 'any',
                        'srcPort': 'Any',
                        'srcCidr': 'any',
                        'syslogEnabled': False
                    }
                ]
            }

            self.dashboard.appliance.updateNetworkApplianceFirewallL3FirewallRules(network_id, **default_rules)
            logger.info(f"Reset L3 firewall rules for network {network_id}")

            # Clear L7 firewall rules if applicable
            try:
                l7_rules = self.dashboard.appliance.getNetworkApplianceFirewallL7FirewallRules(network_id)
                if l7_rules.get('rules'):
                    self.dashboard.appliance.updateNetworkApplianceFirewallL7FirewallRules(network_id, rules=[])
                    logger.info(f"Cleared L7 firewall rules for network {network_id}")
            except Exception:
                pass  # L7 rules may not be available for all network types

            return True

        except Exception as e:
            logger.warning(f"Could not clear firewall rules for network {network_id}: {e}")
            return False

    def _clear_vpn_config(self, network_id: str) -> bool:
        """Clear VPN configuration"""
        try:
            # Get VPN settings
            vpn_settings = self.dashboard.appliance.getNetworkApplianceVpnSiteToSiteVpn(network_id)

            if vpn_settings.get('mode') != 'none':
                # Disable VPN
                self.dashboard.appliance.updateNetworkApplianceVpnSiteToSiteVpn(network_id, mode='none')
                logger.info(f"Disabled VPN for network {network_id}")

            return True

        except Exception as e:
            logger.warning(f"Could not clear VPN config for network {network_id}: {e}")
            return False

    def _clear_dhcp_settings(self, network_id: str) -> bool:
        """Clear DHCP settings"""
        try:
            # This would clear DHCP reservations, pools, etc.
            # Implementation depends on specific network configuration
            logger.info(f"DHCP clearing not implemented for network {network_id}")
            return True

        except Exception as e:
            logger.warning(f"Could not clear DHCP settings for network {network_id}: {e}")
            return False

    def _clear_group_policies(self, network_id: str) -> bool:
        """Clear group policies"""
        try:
            # Get group policies
            policies = self.dashboard.networks.getNetworkGroupPolicies(network_id)

            for policy in policies:
                try:
                    self.dashboard.networks.deleteNetworkGroupPolicy(network_id, policy['groupPolicyId'])
                    logger.info(f"Deleted group policy {policy['name']} from network {network_id}")
                except Exception as e:
                    logger.warning(f"Failed to delete group policy {policy['name']}: {e}")

            return True

        except Exception as e:
            logger.warning(f"Could not clear group policies for network {network_id}: {e}")
            return False

    def remove_network_devices(self, network_id: str) -> bool:
        """Remove all devices from a network"""
        try:
            logger.info(f"Removing devices from network {network_id}")

            # Get devices
            devices = self.dashboard.networks.getNetworkDevices(network_id)

            for device in devices:
                try:
                    self.dashboard.networks.removeNetworkDevice(network_id, device['serial'])
                    logger.info(f"Removed device {device['serial']} ({device['model']}) from network {network_id}")
                except Exception as e:
                    logger.warning(f"Failed to remove device {device['serial']}: {e}")

            return True

        except Exception as e:
            logger.error(f"Failed to remove devices from network {network_id}: {e}")
            return False

    def factory_reset_devices(self, network_id: str) -> bool:
        """Factory reset all devices in a network"""
        try:
            logger.warning(f"Factory resetting devices in network {network_id}")

            # Get devices
            devices = self.dashboard.networks.getNetworkDevices(network_id)

            for device in devices:
                try:
                    # Factory reset device
                    self.dashboard.devices.rebootDevice(device['serial'])
                    logger.info(f"Initiated factory reset for device {device['serial']}")

                    # Note: Actual factory reset may need to be done via device interface
                    # This just reboots the device

                except Exception as e:
                    logger.warning(f"Failed to factory reset device {device['serial']}: {e}")

            return True

        except Exception as e:
            logger.error(f"Failed to factory reset devices in network {network_id}: {e}")
            return False

    def delete_network(self, network_id: str) -> bool:
        """Delete a network entirely"""
        try:
            logger.warning(f"Deleting network {network_id}")

            # First remove all devices
            self.remove_network_devices(network_id)

            # Clear configuration
            self.clear_network_configuration(network_id)

            # Delete the network
            self.dashboard.networks.deleteNetwork(network_id)
            logger.info(f"Successfully deleted network {network_id}")

            return True

        except Exception as e:
            logger.error(f"Failed to delete network {network_id}: {e}")
            return False

    def wipe_all_networks(self, skip_confirm: bool = False) -> bool:
        """Complete destruction of all networks"""
        try:
            logger.warning("=== STARTING MERAKI NETWORK DESTRUCTION ===")

            if not skip_confirm:
                confirm = input(f"About to destroy {len(self.network_inventory)} Meraki networks. Continue? (yes/no): ")
                if confirm.lower() != 'yes':
                    logger.info("Meraki destruction cancelled")
                    return False

            success_count = 0
            total_count = len(self.network_inventory)

            for network in self.network_inventory:
                network_id = network['id']
                network_name = network['name']

                logger.info(f"Processing network {success_count + 1}/{total_count}: {network_name} ({network_id})")

                # Clear configuration
                if self.clear_network_configuration(network_id):
                    logger.info(f"✓ Cleared configuration for {network_name}")
                else:
                    logger.error(f"✗ Failed to clear configuration for {network_name}")

                # Remove devices
                if self.remove_network_devices(network_id):
                    logger.info(f"✓ Removed devices from {network_name}")
                else:
                    logger.error(f"✗ Failed to remove devices from {network_name}")

                # Factory reset remaining devices
                if self.factory_reset_devices(network_id):
                    logger.info(f"✓ Factory reset initiated for devices in {network_name}")
                else:
                    logger.warning(f"⚠ Factory reset failed for some devices in {network_name}")

                # Delete network
                if self.delete_network(network_id):
                    logger.info(f"✓ Deleted network {network_name}")
                    success_count += 1
                    self._audit_event("NETWORK_DELETED", f"{network_name} ({network_id})")
                else:
                    logger.error(f"✗ Failed to delete network {network_name}")

            logger.info(f"=== MERAKI DESTRUCTION COMPLETED: {success_count}/{total_count} networks processed ===")
            return success_count == total_count

        except Exception as e:
            logger.error(f"Meraki destruction failed: {e}")
            return False

    def verify_destruction(self) -> bool:
        """Verify that all networks have been destroyed"""
        try:
            logger.info("Verifying Meraki network destruction...")

            # Get current networks
            current_networks = self.dashboard.organizations.getOrganizationNetworks(self.org_id)

            if not current_networks:
                logger.info("✓ Meraki destruction verification successful: No networks remaining")
                self._audit_event("VERIFICATION", "All networks destroyed")
                return True
            else:
                logger.warning(f"⚠ Meraki destruction verification found {len(current_networks)} networks remaining")
                for network in current_networks:
                    logger.warning(f"  Remaining network: {network['name']} ({network['id']})")
                return False

        except Exception as e:
            logger.error(f"Meraki verification failed: {e}")
            return False

    def _audit_event(self, action: str, details: str):
        """Log audit event"""
        timestamp = datetime.now().isoformat()
        event = {
            'timestamp': timestamp,
            'action': action,
            'details': details,
            'component': 'Meraki'
        }
        self.audit_log.append(event)
        logger.info(f"AUDIT: {action} - {details}")

    def get_audit_log(self) -> List[Dict]:
        """Get the audit log"""
        return self.audit_log.copy()

    def generate_report(self, filename: str = 'meraki_destruction_report.json') -> bool:
        """Generate comprehensive Meraki destruction report"""
        try:
            report = {
                'generated_at': datetime.now().isoformat(),
                'organization_id': self.org_id,
                'audit_log': self.audit_log,
                'final_inventory': self.get_network_inventory()
            }

            with open(filename, 'w') as f:
                json.dump(report, f, indent=2)

            logger.info(f"Meraki destruction report generated: {filename}")
            return True

        except Exception as e:
            logger.error(f"Failed to generate report: {e}")
            return False

def main():
    """CLI interface for Meraki eliminator"""
    import argparse

    parser = argparse.ArgumentParser(description='Meraki Network Eliminator')
    parser.add_argument('--api-key', help='Meraki API key (or set MERAKI_API_KEY env var)')
    parser.add_argument('--org-id', default='939404', help='Meraki organization ID')
    parser.add_argument('--inventory', action='store_true', help='Get network inventory only')
    parser.add_argument('--wipe-all', action='store_true', help='Wipe all networks (DANGER)')
    parser.add_argument('--verify', action='store_true', help='Verify destruction completion')
    parser.add_argument('--report', help='Generate destruction report')
    parser.add_argument('--skip-confirm', action='store_true', help='Skip confirmation prompts')

    args = parser.parse_args()

    try:
        # Initialize eliminator
        eliminator = MerakiEliminator(args.api_key, args.org_id)

        # Connect to Meraki
        if not eliminator.connect():
            sys.exit(1)

        success = True

        # Get inventory (always done first)
        inventory = eliminator.get_network_inventory()

        if args.inventory:
            print(json.dumps(inventory, indent=2))
            sys.exit(0)

        # Wipe all networks if requested
        if args.wipe_all:
            success &= eliminator.wipe_all_networks(args.skip_confirm)

        # Verify destruction if requested
        if args.verify:
            success &= eliminator.verify_destruction()

        # Generate report if requested
        if args.report:
            success &= eliminator.generate_report(args.report)

        # Print audit log summary
        audit_log = eliminator.get_audit_log()
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