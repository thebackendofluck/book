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
Ansible Infrastructure Destroyer
Executes comprehensive Ansible playbooks for complete infrastructure wipe
"""

import sys
import os
import json
import subprocess
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/ansible_destroyer.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class AnsibleDestroyer:
    """Ansible-based infrastructure destruction handler"""

    def __init__(self, playbook_path: str = 'ansible/playbooks/destroy_all.yml'):
        self.playbook_path = playbook_path
        self.inventory_path = None
        self.audit_log = []
        self.destruction_mode = 'safe'  # Default to safe mode

    def validate_prerequisites(self) -> bool:
        """Validate Ansible prerequisites"""
        try:
            logger.info("Validating Ansible prerequisites...")

            # Check if ansible-playbook is available
            result = subprocess.run(['which', 'ansible-playbook'], capture_output=True, text=True)
            if result.returncode != 0:
                logger.error("ansible-playbook command not found")
                return False

            # Check if playbook exists
            if not Path(self.playbook_path).exists():
                logger.error(f"Playbook not found: {self.playbook_path}")
                return False

            # Validate playbook syntax
            result = subprocess.run(['ansible-playbook', '--syntax-check', self.playbook_path],
                                  capture_output=True, text=True)
            if result.returncode != 0:
                logger.error(f"Playbook syntax error: {result.stderr}")
                return False

            logger.info("Ansible prerequisites validation successful")
            return True

        except Exception as e:
            logger.error(f"Prerequisites validation failed: {e}")
            return False

    def set_inventory(self, inventory_path: str):
        """Set Ansible inventory path"""
        if Path(inventory_path).exists():
            self.inventory_path = inventory_path
            logger.info(f"Inventory set to: {inventory_path}")
        else:
            logger.error(f"Inventory file not found: {inventory_path}")

    def discover_inventory(self) -> List[str]:
        """Auto-discover Ansible inventory files"""
        inventory_paths = []

        # Common inventory locations
        search_paths = [
            'ansible/inventories',
            'infra-ansible/inventories',
            '/etc/ansible/inventories'
        ]

        for search_path in search_paths:
            if Path(search_path).exists():
                for inventory_dir in Path(search_path).glob('*'):
                    if inventory_dir.is_dir():
                        hosts_file = inventory_dir / 'hosts.yml'
                        if hosts_file.exists():
                            inventory_paths.append(str(hosts_file))

        logger.info(f"Discovered {len(inventory_paths)} inventory files")
        return inventory_paths

    def run_dry_run(self, inventory: Optional[str] = None, limit: Optional[str] = None) -> bool:
        """Run Ansible playbook in check mode (dry run)"""
        try:
            logger.info("Starting Ansible dry run...")

            cmd = ['ansible-playbook', self.playbook_path, '--check', '--diff']

            if inventory or self.inventory_path:
                cmd.extend(['-i', inventory or self.inventory_path])

            if limit:
                cmd.extend(['--limit', limit])

            # Set safe mode for dry run
            cmd.extend(['--extra-vars', f'destruction_mode=safe'])

            logger.info(f"Executing dry run: {' '.join(cmd)}")

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)

            if result.returncode == 0:
                logger.info("Ansible dry run completed successfully")
                self._parse_dry_run_output(result.stdout)
                return True
            else:
                logger.error(f"Ansible dry run failed: {result.stderr}")
                return False

        except subprocess.TimeoutExpired:
            logger.error("Ansible dry run timed out")
            return False
        except Exception as e:
            logger.error(f"Dry run failed: {e}")
            return False

    def _parse_dry_run_output(self, output: str):
        """Parse Ansible dry run output"""
        lines = output.split('\n')
        changes_count = 0

        for line in lines:
            if 'changed=' in line or 'ok=' in line:
                changes_count += 1
                logger.info(f"DRY RUN: {line.strip()}")

        logger.info(f"Dry run summary: {changes_count} tasks would be executed")

    def run_destruction(self, inventory: Optional[str] = None, limit: Optional[str] = None,
                       force: bool = False) -> bool:
        """Run actual Ansible destruction (USE WITH EXTREME CAUTION)"""
        if not force:
            logger.warning("DESTRUCTIVE OPERATION - This will permanently destroy infrastructure!")
            confirm = input("Type 'DESTROY ALL INFRASTRUCTURE' to confirm: ")
            if confirm != "DESTROY ALL INFRASTRUCTURE":
                logger.info("Ansible destruction cancelled")
                return False

        try:
            logger.warning("=== STARTING ANSIBLE INFRASTRUCTURE DESTRUCTION ===")

            # First run dry run to show what will be destroyed
            logger.info("Running final dry run before destruction...")
            if not self.run_dry_run(inventory, limit):
                logger.error("Dry run failed - aborting destruction")
                return False

            # Final confirmation
            if not force:
                confirm2 = input("Dry run complete. Type 'CONFIRM DESTRUCTION' to proceed: ")
                if confirm2 != "CONFIRM DESTRUCTION":
                    logger.info("Ansible destruction cancelled at final confirmation")
                    return False

            # Run actual destruction
            cmd = ['ansible-playbook', self.playbook_path]

            if inventory or self.inventory_path:
                cmd.extend(['-i', inventory or self.inventory_path])

            if limit:
                cmd.extend(['--limit', limit])

            # Set destructive mode
            cmd.extend(['--extra-vars', f'destruction_mode=destructive'])

            logger.warning(f"Executing DESTRUCTIVE command: {' '.join(cmd)}")

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)

            if result.returncode == 0:
                logger.warning("=== ANSIBLE INFRASTRUCTURE DESTRUCTION COMPLETED ===")
                self._audit_event("ANSIBLE_DESTRUCTION", "Completed successfully")
                return True
            else:
                logger.error(f"Ansible destruction failed: {result.stderr}")
                self._audit_event("ANSIBLE_DESTRUCTION", f"Failed: {result.stderr}")
                return False

        except subprocess.TimeoutExpired:
            logger.error("Ansible destruction timed out")
            return False
        except Exception as e:
            logger.error(f"Ansible destruction failed: {e}")
            return False

    def get_host_inventory(self, inventory: Optional[str] = None) -> Dict[str, List]:
        """Get inventory of hosts that would be affected"""
        try:
            logger.info("Getting Ansible host inventory...")

            cmd = ['ansible-inventory', '--list']

            if inventory or self.inventory_path:
                cmd.extend(['-i', inventory or self.inventory_path])

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

            if result.returncode == 0:
                inventory_data = json.loads(result.stdout)
                host_inventory = self._parse_inventory_data(inventory_data)
                logger.info(f"Host inventory complete: {sum(len(hosts) for hosts in host_inventory.values())} hosts found")
                return host_inventory
            else:
                logger.error(f"Failed to get host inventory: {result.stderr}")
                return {}

        except Exception as e:
            logger.error(f"Host inventory failed: {e}")
            return {}

    def _parse_inventory_data(self, inventory_data: Dict) -> Dict[str, List]:
        """Parse Ansible inventory JSON data"""
        host_inventory = {}

        # Extract hosts by group
        if '_meta' in inventory_data and 'hostvars' in inventory_data['_meta']:
            hostvars = inventory_data['_meta']['hostvars']

            for group_name, group_data in inventory_data.items():
                if group_name != '_meta' and isinstance(group_data, dict):
                    if 'hosts' in group_data:
                        hosts = []
                        for host_name in group_data['hosts']:
                            host_info = {
                                'name': host_name,
                                'vars': hostvars.get(host_name, {})
                            }
                            hosts.append(host_info)
                        host_inventory[group_name] = hosts

        return host_inventory

    def verify_destruction(self, inventory: Optional[str] = None) -> bool:
        """Verify that infrastructure has been successfully destroyed"""
        try:
            logger.info("Verifying Ansible infrastructure destruction...")

            # This would run a verification playbook
            # For now, just check if hosts are still reachable
            host_inventory = self.get_host_inventory(inventory)

            unreachable_hosts = []
            for group, hosts in host_inventory.items():
                for host in hosts:
                    if not self._check_host_reachability(host['name']):
                        unreachable_hosts.append(host['name'])

            if not unreachable_hosts:
                logger.info("✓ Ansible destruction verification successful: All hosts unreachable")
                self._audit_event("ANSIBLE_VERIFICATION", "All hosts destroyed/unreachable")
                return True
            else:
                logger.warning(f"⚠ Ansible destruction verification found {len(unreachable_hosts)} hosts still reachable")
                for host in unreachable_hosts[:5]:  # Show first 5
                    logger.warning(f"  Host still reachable: {host}")
                return False

        except Exception as e:
            logger.error(f"Ansible verification failed: {e}")
            return False

    def _check_host_reachability(self, hostname: str) -> bool:
        """Check if a host is still reachable"""
        try:
            result = subprocess.run(['ping', '-c', '1', '-W', '2', hostname],
                                  capture_output=True, text=True, timeout=10)
            return result.returncode == 0
        except Exception:
            return False

    def _audit_event(self, action: str, details: str):
        """Log audit event"""
        timestamp = datetime.now().isoformat()
        event = {
            'timestamp': timestamp,
            'action': action,
            'details': details,
            'component': 'Ansible'
        }
        self.audit_log.append(event)
        logger.info(f"AUDIT: {action} - {details}")

    def get_audit_log(self) -> List[Dict]:
        """Get the audit log"""
        return self.audit_log.copy()

    def generate_report(self, filename: str = 'ansible_destruction_report.json') -> bool:
        """Generate comprehensive Ansible destruction report"""
        try:
            report = {
                'generated_at': datetime.now().isoformat(),
                'playbook': self.playbook_path,
                'inventory': self.inventory_path,
                'audit_log': self.audit_log,
                'host_inventory': self.get_host_inventory()
            }

            with open(filename, 'w') as f:
                json.dump(report, f, indent=2)

            logger.info(f"Ansible destruction report generated: {filename}")
            return True

        except Exception as e:
            logger.error(f"Failed to generate report: {e}")
            return False

def main():
    """CLI interface for Ansible destroyer"""
    import argparse

    parser = argparse.ArgumentParser(description='Ansible Infrastructure Destroyer')
    parser.add_argument('--playbook', default='ansible/playbooks/destroy_all.yml',
                       help='Ansible playbook to execute')
    parser.add_argument('--inventory', help='Ansible inventory file')
    parser.add_argument('--limit', help='Limit execution to specific hosts')
    parser.add_argument('--dry-run', action='store_true',
                       help='Run in check mode (dry run)')
    parser.add_argument('--destroy', action='store_true',
                       help='Run actual destruction (DANGER)')
    parser.add_argument('--verify', action='store_true',
                       help='Verify destruction completion')
    parser.add_argument('--inventory-list', action='store_true',
                       help='List available inventory files')
    parser.add_argument('--host-inventory', action='store_true',
                       help='Get host inventory')
    parser.add_argument('--report', help='Generate destruction report')
    parser.add_argument('--force', action='store_true',
                       help='Skip confirmation prompts')

    args = parser.parse_args()

    destroyer = AnsibleDestroyer(args.playbook)

    try:
        success = True

        # Validate prerequisites
        if not destroyer.validate_prerequisites():
            logger.error("Prerequisites validation failed")
            sys.exit(1)

        # Handle inventory operations
        if args.inventory_list:
            inventories = destroyer.discover_inventory()
            print("Available inventories:")
            for inv in inventories:
                print(f"  {inv}")
            sys.exit(0)

        if args.host_inventory:
            inventory = destroyer.get_host_inventory(args.inventory)
            print(json.dumps(inventory, indent=2))
            sys.exit(0)

        # Set inventory if provided
        if args.inventory:
            destroyer.set_inventory(args.inventory)

        # Handle different operations
        if args.dry_run:
            success &= destroyer.run_dry_run(args.inventory, args.limit)

        elif args.destroy:
            success &= destroyer.run_destruction(args.inventory, args.limit, args.force)

        elif args.verify:
            success &= destroyer.verify_destruction(args.inventory)

        # Generate report if requested
        if args.report:
            success &= destroyer.generate_report(args.report)

        # Print audit log summary
        audit_log = destroyer.get_audit_log()
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