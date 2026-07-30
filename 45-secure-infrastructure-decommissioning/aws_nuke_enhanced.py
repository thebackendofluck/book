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
Enhanced AWS Nuke with HSM Awareness
Executes aws-nuke with HSM-aware configuration and integrates with destruction sequence
"""

import sys
import os
import json
import subprocess
import logging
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/aws_nuke_enhanced.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class AWSNukeEnhanced:
    """Enhanced AWS Nuke with HSM awareness and destruction sequencing"""

    def __init__(self, config_file: str = 'config/aws_nuke_config.yml'):
        self.config_file = config_file
        self.audit_log = []
        self.dry_run = True  # Default to dry run for safety

    def validate_prerequisites(self) -> bool:
        """Validate that all prerequisites are met before running aws-nuke"""
        try:
            logger.info("Validating AWS Nuke prerequisites...")

            # Check if aws-nuke is installed
            result = subprocess.run(['which', 'aws-nuke'], capture_output=True, text=True)
            if result.returncode != 0:
                logger.error("aws-nuke command not found. Please install aws-nuke")
                return False

            # Check AWS credentials
            result = subprocess.run(['aws', 'sts', 'get-caller-identity'], capture_output=True, text=True)
            if result.returncode != 0:
                logger.error("AWS credentials not configured or invalid")
                logger.error(f"AWS Error: {result.stderr}")
                return False

            # Parse caller identity
            identity = json.loads(result.stdout)
            account_id = identity['Account']
            logger.info(f"AWS Account: {account_id}")

            # Check config file exists
            if not Path(self.config_file).exists():
                logger.error(f"Config file not found: {self.config_file}")
                return False

            # Validate config file
            if not self._validate_config():
                logger.error("Config file validation failed")
                return False

            logger.info("Prerequisites validation successful")
            return True

        except Exception as e:
            logger.error(f"Prerequisites validation failed: {e}")
            return False

    def _validate_config(self) -> bool:
        """Validate the aws-nuke configuration file"""
        try:
            import yaml
            with open(self.config_file, 'r') as f:
                config = yaml.safe_load(f)

            # Check required sections
            required_sections = ['accounts', 'resource-types', 'presets']
            for section in required_sections:
                if section not in config:
                    logger.error(f"Missing required section in config: {section}")
                    return False

            # Validate account IDs match known accounts
            known_accounts = [
                "AWS_ACCOUNT_ID",  # dev
                "AWS_ACCOUNT_ID",  # prod
                "AWS_ACCOUNT_ID",  # shared
                "AWS_ACCOUNT_ID",  # security
                "AWS_ACCOUNT_ID",  # logs
                "AWS_ACCOUNT_ID"   # stage
            ]

            config_accounts = list(config['accounts'].keys())
            for account in config_accounts:
                if account not in known_accounts:
                    logger.warning(f"Unknown account in config: {account}")

            logger.info("Config file validation successful")
            return True

        except Exception as e:
            logger.error(f"Config validation failed: {e}")
            return False

    def run_dry_run(self, accounts: Optional[List[str]] = None) -> bool:
        """Run aws-nuke in dry-run mode to show what would be destroyed"""
        try:
            logger.info("Starting AWS Nuke dry run...")

            cmd = ['aws-nuke', '--config', self.config_file, '--dry-run']

            if accounts:
                for account in accounts:
                    cmd.extend(['--account', account])

            # Add quiet mode to reduce output
            cmd.append('--quiet')

            logger.info(f"Executing: {' '.join(cmd)}")

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)

            if result.returncode == 0:
                logger.info("AWS Nuke dry run completed successfully")
                self._parse_dry_run_output(result.stdout)
                return True
            else:
                logger.error(f"AWS Nuke dry run failed: {result.stderr}")
                return False

        except subprocess.TimeoutExpired:
            logger.error("AWS Nuke dry run timed out")
            return False
        except Exception as e:
            logger.error(f"Dry run failed: {e}")
            return False

    def _parse_dry_run_output(self, output: str):
        """Parse and log dry run output"""
        lines = output.split('\n')
        resources_found = 0

        for line in lines:
            if 'would remove' in line.lower():
                resources_found += 1
                logger.info(f"DRY RUN: {line.strip()}")

        logger.info(f"Dry run summary: {resources_found} resources would be removed")

    def run_destruction(self, accounts: Optional[List[str]] = None, force: bool = False) -> bool:
        """Run actual aws-nuke destruction (USE WITH EXTREME CAUTION)"""
        if not force:
            logger.warning("DESTRUCTIVE OPERATION - This will permanently delete AWS resources!")
            confirm = input("Type 'DESTROY ALL AWS RESOURCES' to confirm: ")
            if confirm != "DESTROY ALL AWS RESOURCES":
                logger.info("AWS destruction cancelled")
                return False

        try:
            logger.warning("=== STARTING AWS RESOURCE DESTRUCTION ===")

            # First run dry run to show what will be destroyed
            logger.info("Running final dry run before destruction...")
            if not self.run_dry_run(accounts):
                logger.error("Dry run failed - aborting destruction")
                return False

            # Final confirmation
            if not force:
                confirm2 = input("Dry run complete. Type 'CONFIRM DESTRUCTION' to proceed: ")
                if confirm2 != "CONFIRM DESTRUCTION":
                    logger.info("AWS destruction cancelled at final confirmation")
                    return False

            # Run actual destruction
            cmd = ['aws-nuke', '--config', self.config_file, '--no-dry-run']

            if accounts:
                for account in accounts:
                    cmd.extend(['--account', account])

            logger.warning(f"Executing DESTRUCTIVE command: {' '.join(cmd)}")

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)

            if result.returncode == 0:
                logger.warning("=== AWS RESOURCE DESTRUCTION COMPLETED ===")
                self._audit_event("AWS_DESTRUCTION", "Completed successfully")
                return True
            else:
                logger.error(f"AWS destruction failed: {result.stderr}")
                self._audit_event("AWS_DESTRUCTION", f"Failed: {result.stderr}")
                return False

        except subprocess.TimeoutExpired:
            logger.error("AWS destruction timed out")
            return False
        except Exception as e:
            logger.error(f"AWS destruction failed: {e}")
            return False

    def get_resource_inventory(self, accounts: Optional[List[str]] = None) -> Dict:
        """Get inventory of resources that would be destroyed"""
        try:
            logger.info("Getting AWS resource inventory...")

            # Run dry run and capture output
            cmd = ['aws-nuke', '--config', self.config_file, '--dry-run', '--quiet']

            if accounts:
                for account in accounts:
                    cmd.extend(['--account', account])

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)

            if result.returncode == 0:
                inventory = self._parse_inventory_output(result.stdout)
                logger.info(f"Inventory complete: {sum(len(resources) for resources in inventory.values())} resources found")
                return inventory
            else:
                logger.error(f"Failed to get inventory: {result.stderr}")
                return {}

        except Exception as e:
            logger.error(f"Inventory failed: {e}")
            return {}

    def _parse_inventory_output(self, output: str) -> Dict[str, List]:
        """Parse aws-nuke output to extract resource inventory"""
        inventory = {}
        current_account = None

        lines = output.split('\n')
        for line in lines:
            line = line.strip()

            # Detect account changes
            if line.startswith('Account:'):
                current_account = line.split(':')[1].strip()
                inventory[current_account] = []
            elif 'would remove' in line.lower() and current_account:
                # Extract resource information
                parts = line.split()
                if len(parts) >= 4:
                    resource_type = parts[2]
                    resource_id = parts[3]
                    inventory[current_account].append({
                        'type': resource_type,
                        'id': resource_id,
                        'description': ' '.join(parts[4:]) if len(parts) > 4 else ''
                    })

        return inventory

    def verify_destruction(self, accounts: Optional[List[str]] = None) -> bool:
        """Verify that AWS resources have been successfully destroyed"""
        try:
            logger.info("Verifying AWS resource destruction...")

            # Get current inventory
            current_inventory = self.get_resource_inventory(accounts)

            # Check if any resources remain
            total_remaining = sum(len(resources) for resources in current_inventory.values())

            if total_remaining == 0:
                logger.info("✓ AWS destruction verification successful: No resources remaining")
                self._audit_event("AWS_VERIFICATION", "All resources destroyed")
                return True
            else:
                logger.warning(f"⚠ AWS destruction verification found {total_remaining} resources remaining")
                for account, resources in current_inventory.items():
                    if resources:
                        logger.warning(f"  Account {account}: {len(resources)} resources")
                        for resource in resources[:5]:  # Show first 5
                            logger.warning(f"    {resource['type']}: {resource['id']}")
                return False

        except Exception as e:
            logger.error(f"AWS verification failed: {e}")
            return False

    def _audit_event(self, action: str, details: str):
        """Log audit event"""
        timestamp = datetime.now().isoformat()
        event = {
            'timestamp': timestamp,
            'action': action,
            'details': details,
            'component': 'AWS_Nuke'
        }
        self.audit_log.append(event)
        logger.info(f"AUDIT: {action} - {details}")

    def get_audit_log(self) -> List[Dict]:
        """Get the audit log"""
        return self.audit_log.copy()

    def generate_report(self, filename: str = 'aws_destruction_report.json') -> bool:
        """Generate comprehensive AWS destruction report"""
        try:
            report = {
                'generated_at': datetime.now().isoformat(),
                'config_file': self.config_file,
                'audit_log': self.audit_log,
                'final_inventory': self.get_resource_inventory()
            }

            with open(filename, 'w') as f:
                json.dump(report, f, indent=2)

            logger.info(f"AWS destruction report generated: {filename}")
            return True

        except Exception as e:
            logger.error(f"Failed to generate report: {e}")
            return False

def main():
    """CLI interface for enhanced AWS Nuke"""
    import argparse

    parser = argparse.ArgumentParser(description='Enhanced AWS Nuke with HSM Awareness')
    parser.add_argument('--config', default='config/aws_nuke_config.yml',
                       help='AWS Nuke configuration file')
    parser.add_argument('--accounts', nargs='*',
                       help='Specific accounts to target')
    parser.add_argument('--dry-run', action='store_true',
                       help='Run in dry-run mode (default)')
    parser.add_argument('--destroy', action='store_true',
                       help='Run actual destruction (DANGER)')
    parser.add_argument('--verify', action='store_true',
                       help='Verify destruction completion')
    parser.add_argument('--inventory', action='store_true',
                       help='Get resource inventory')
    parser.add_argument('--report', help='Generate destruction report')
    parser.add_argument('--force', action='store_true',
                       help='Skip confirmation prompts')

    args = parser.parse_args()

    nuke = AWSNukeEnhanced(args.config)

    try:
        success = True

        # Validate prerequisites
        if not nuke.validate_prerequisites():
            logger.error("Prerequisites validation failed")
            sys.exit(1)

        # Handle different operations
        if args.inventory:
            inventory = nuke.get_resource_inventory(args.accounts)
            print(json.dumps(inventory, indent=2))

        elif args.dry_run:
            success &= nuke.run_dry_run(args.accounts)

        elif args.destroy:
            success &= nuke.run_destruction(args.accounts, args.force)

        elif args.verify:
            success &= nuke.verify_destruction(args.accounts)

        # Generate report if requested
        if args.report:
            success &= nuke.generate_report(args.report)

        # Print audit log summary
        audit_log = nuke.get_audit_log()
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