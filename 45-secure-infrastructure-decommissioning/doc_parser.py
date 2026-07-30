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
Documentation Parser for Secure Data Destruction System
Parses infrastructure, Meraki, and YubiHSM documentation to build destruction inventory
"""

import os
import json
import yaml
import argparse
from pathlib import Path
from typing import Dict, List, Any
import re
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class DocumentationParser:
    """Parses all infrastructure documentation to build destruction inventory"""

    def __init__(self):
        self.infrastructure_data = {}
        self.meraki_data = {}
        self.yubihsm_data = {}
        self.destruction_inventory = {
            'yubihsm_devices': [],
            'yubikey_tokens': [],
            'aws_accounts': [],
            'ansible_hosts': [],
            'terraform_workspaces': [],
            'meraki_networks': [],
            'mikrotik_devices': [],
            'security_configs': {}
        }

    def parse_all_documentation(self, infra_path: str, meraki_path: str, yubihsm_path: str) -> Dict[str, Any]:
        """Parse all documentation sources"""
        logger.info("Starting comprehensive documentation analysis...")

        # Parse infrastructure documentation
        self.parse_infrastructure_docs(infra_path)

        # Parse Meraki documentation
        self.parse_meraki_docs(meraki_path)

        # Parse YubiHSM documentation
        self.parse_yubihsm_docs(yubihsm_path)

        # Build destruction inventory
        self.build_destruction_inventory()

        # Validate completeness
        self.validate_inventory()

        return self.destruction_inventory

    def parse_infrastructure_docs(self, infra_path: str):
        """Parse infrastructure documentation"""
        logger.info(f"Parsing infrastructure documentation from {infra_path}")

        infra_path_obj = Path(infra_path)

        # Parse Terragrunt/Terraform structure
        self.parse_terragrunt_structure(infra_path_obj / 'infrastructure-live')

        # Parse Ansible inventory
        self.parse_ansible_inventory(infra_path_obj / 'infra-ansible')

        # Parse Terraform modules
        self.parse_terraform_modules(infra_path_obj / 'infra-terraform')

        # Parse load testing infrastructure
        self.parse_load_testing(infra_path_obj / 'load-testing')

    def parse_terragrunt_structure(self, terragrunt_path: Path):
        """Parse Terragrunt account structure"""
        if not terragrunt_path.exists():
            logger.warning(f"Terragrunt path not found: {terragrunt_path}")
            return

        accounts = ['dev', 'prod', 'security', 'shared', 'stage']
        for account in accounts:
            account_path = terragrunt_path / account
            if account_path.exists():
                self.destruction_inventory['aws_accounts'].append({
                    'name': account,
                    'path': str(account_path),
                    'type': 'terragrunt',
                    'regions': self.get_terragrunt_regions(account_path)
                })

        # Parse variables
        vars_path = terragrunt_path / 'vars' / 'autogen'
        if vars_path.exists():
            for var_file in vars_path.glob('*.yml'):
                self.parse_terragrunt_vars(var_file)

    def get_terragrunt_regions(self, account_path: Path) -> List[str]:
        """Extract regions from Terragrunt structure"""
        regions = []
        for region_dir in account_path.glob('*'):
            if region_dir.is_dir() and not region_dir.name.startswith('_'):
                regions.append(region_dir.name)
        return regions

    def parse_terragrunt_vars(self, var_file: Path):
        """Parse Terragrunt variable files"""
        try:
            with open(var_file, 'r') as f:
                vars_data = yaml.safe_load(f)
                account_name = var_file.stem.replace('_vars', '')
                self.infrastructure_data[f'vars_{account_name}'] = vars_data
        except Exception as e:
            logger.error(f"Error parsing {var_file}: {e}")

    def parse_ansible_inventory(self, ansible_path: Path):
        """Parse Ansible inventory structure"""
        if not ansible_path.exists():
            logger.warning(f"Ansible path not found: {ansible_path}")
            return

        # Parse inventory files
        inventory_path = ansible_path / 'inventories'
        if inventory_path.exists():
            for inventory_dir in inventory_path.glob('*'):
                if inventory_dir.is_dir():
                    self.parse_ansible_inventory_dir(inventory_dir)

        # Parse group variables
        group_vars_path = ansible_path / 'group_vars'
        if group_vars_path.exists():
            for var_file in group_vars_path.glob('*.yaml'):
                self.parse_ansible_group_vars(var_file)

    def parse_ansible_inventory_dir(self, inventory_dir: Path):
        """Parse individual Ansible inventory directory"""
        hosts_file = inventory_dir / 'hosts.yml'
        if hosts_file.exists():
            try:
                with open(hosts_file, 'r') as f:
                    hosts_data = yaml.safe_load(f)
                    self.destruction_inventory['ansible_hosts'].extend(
                        self.extract_ansible_hosts(hosts_data, inventory_dir.name)
                    )
            except Exception as e:
                logger.error(f"Error parsing Ansible inventory {hosts_file}: {e}")

    def extract_ansible_hosts(self, hosts_data: Dict, inventory_name: str) -> List[Dict]:
        """Extract host information from Ansible inventory"""
        hosts = []
        if 'all' in hosts_data and 'children' in hosts_data['all']:
            for group_name, group_data in hosts_data['all']['children'].items():
                if 'hosts' in group_data:
                    for host_name, host_data in group_data['hosts'].items():
                        hosts.append({
                            'name': host_name,
                            'group': group_name,
                            'inventory': inventory_name,
                            'vars': host_data
                        })
        return hosts

    def parse_ansible_group_vars(self, var_file: Path):
        """Parse Ansible group variables"""
        try:
            with open(var_file, 'r') as f:
                vars_data = yaml.safe_load(f)
                group_name = var_file.stem
                self.infrastructure_data[f'ansible_group_{group_name}'] = vars_data
        except Exception as e:
            logger.error(f"Error parsing Ansible group vars {var_file}: {e}")

    def parse_terraform_modules(self, terraform_path: Path):
        """Parse Terraform module structure"""
        if not terraform_path.exists():
            logger.warning(f"Terraform path not found: {terraform_path}")
            return

        # This would parse Terraform state files and modules
        # For now, just catalog the structure
        self.infrastructure_data['terraform_path'] = str(terraform_path)

    def parse_load_testing(self, load_testing_path: Path):
        """Parse load testing infrastructure"""
        if not load_testing_path.exists():
            logger.warning(f"Load testing path not found: {load_testing_path}")
            return

        # Parse Docker Compose and Kubernetes configs
        compose_file = load_testing_path / 'docker-compose.yml'
        if compose_file.exists():
            try:
                with open(compose_file, 'r') as f:
                    compose_data = yaml.safe_load(f)
                    self.infrastructure_data['load_testing_compose'] = compose_data
            except Exception as e:
                logger.error(f"Error parsing Docker Compose: {e}")

    def parse_meraki_docs(self, meraki_path: str):
        """Parse Meraki network documentation"""
        logger.info(f"Parsing Meraki documentation from {meraki_path}")

        meraki_path_obj = Path(meraki_path)

        # Parse network topology
        network_topology = meraki_path_obj / 'network_topology_summary.md'
        if network_topology.exists():
            self.parse_meraki_network_topology(network_topology)

        # Parse device inventory
        device_inventory = meraki_path_obj / 'device_inventory_placement.md'
        if device_inventory.exists():
            self.parse_meraki_device_inventory(device_inventory)

        # Parse security topology
        security_topology = meraki_path_obj / 'security_firewall_topology.md'
        if security_topology.exists():
            self.parse_meraki_security_topology(security_topology)

        # Parse subnet addressing
        subnet_addressing = meraki_path_obj / 'subnet_ip_addressing.md'
        if subnet_addressing.exists():
            self.parse_meraki_subnet_addressing(subnet_addressing)

        # Parse VPN topology
        vpn_topology = meraki_path_obj / 'vpn_topology_overview.md'
        if vpn_topology.exists():
            self.parse_meraki_vpn_topology(vpn_topology)

        # Parse cloud integration
        cloud_integration = meraki_path_obj / 'cloud_integration_topology.md'
        if cloud_integration.exists():
            self.parse_meraki_cloud_integration(cloud_integration)

    def parse_meraki_network_topology(self, topology_file: Path):
        """Parse Meraki network topology"""
        try:
            with open(topology_file, 'r') as f:
                content = f.read()

            # Extract network information using regex
            network_pattern = r'L_(\d+)'
            networks = re.findall(network_pattern, content)

            for network_id in networks:
                self.destruction_inventory['meraki_networks'].append({
                    'id': f'L_{network_id}',
                    'type': 'meraki_network',
                    'source': 'topology'
                })

        except Exception as e:
            logger.error(f"Error parsing Meraki topology: {e}")

    def parse_meraki_device_inventory(self, inventory_file: Path):
        """Parse Meraki device inventory"""
        try:
            with open(inventory_file, 'r') as f:
                content = f.read()

            # Extract device information
            device_patterns = [
                (r'MX\d+', 'security_appliance'),
                (r'MS\d+', 'switch'),
                (r'MR\d+', 'wireless_access_point'),
                (r'vMX\d+', 'virtual_mx')
            ]

            for pattern, device_type in device_patterns:
                devices = re.findall(pattern, content)
                for device in devices:
                    self.destruction_inventory['meraki_networks'].append({
                        'model': device,
                        'type': device_type,
                        'source': 'inventory'
                    })

        except Exception as e:
            logger.error(f"Error parsing Meraki inventory: {e}")

    def parse_meraki_security_topology(self, security_file: Path):
        """Parse Meraki security topology"""
        try:
            with open(security_file, 'r') as f:
                content = f.read()

            # Extract security configurations
            self.meraki_data['security_topology'] = content

        except Exception as e:
            logger.error(f"Error parsing Meraki security: {e}")

    def parse_meraki_subnet_addressing(self, subnet_file: Path):
        """Parse Meraki subnet addressing"""
        try:
            with open(subnet_file, 'r') as f:
                content = f.read()

            # Extract subnet information
            subnet_pattern = r'(\d+\.\d+\.\d+\.\d+/\d+)'
            subnets = re.findall(subnet_pattern, content)

            self.meraki_data['subnets'] = subnets

        except Exception as e:
            logger.error(f"Error parsing Meraki subnets: {e}")

    def parse_meraki_vpn_topology(self, vpn_file: Path):
        """Parse Meraki VPN topology"""
        try:
            with open(vpn_file, 'r') as f:
                content = f.read()

            # Extract VPN configuration
            self.meraki_data['vpn_topology'] = content

        except Exception as e:
            logger.error(f"Error parsing Meraki VPN: {e}")

    def parse_meraki_cloud_integration(self, cloud_file: Path):
        """Parse Meraki cloud integration"""
        try:
            with open(cloud_file, 'r') as f:
                content = f.read()

            # Extract cloud integration details
            self.meraki_data['cloud_integration'] = content

        except Exception as e:
            logger.error(f"Error parsing Meraki cloud integration: {e}")

    def parse_yubihsm_docs(self, yubihsm_path: str):
        """Parse YubiHSM documentation"""
        logger.info(f"Parsing YubiHSM documentation from {yubihsm_path}")

        yubihsm_path_obj = Path(yubihsm_path)

        # Parse README for architecture
        readme = yubihsm_path_obj / 'README.md'
        if readme.exists():
            self.parse_yubihsm_readme(readme)

        # Parse Terraform configurations
        terraform_dir = yubihsm_path_obj / 'terraform'
        if terraform_dir.exists():
            self.parse_yubihsm_terraform(terraform_dir)

        # Parse scripts
        scripts = [
            'yubihsm_complete_storage.sh',
            'yubihsm_lifecycle_management.sh',
            'vaultwarden_mtls_setup.sh',
            'password_vault.py'
        ]

        for script in scripts:
            script_path = yubihsm_path_obj / script
            if script_path.exists():
                self.parse_yubihsm_script(script_path)

    def parse_yubihsm_readme(self, readme_file: Path):
        """Parse YubiHSM README for configuration details"""
        try:
            with open(readme_file, 'r') as f:
                content = f.read()

            # Extract HSM configuration patterns
            hsm_patterns = [
                (r'YubiHSM 2.*FIPS', 'fips_certified'),
                (r'256 objects', 'storage_capacity'),
                (r'AES-256', 'encryption_standard'),
                (r'RSA.*2048', 'key_types'),
                (r'ECC.*P-256', 'ecc_keys')
            ]

            for pattern, config_type in hsm_patterns:
                if re.search(pattern, content, re.IGNORECASE):
                    self.yubihsm_data[config_type] = True

            # Extract storage recommendations
            storage_section = re.search(r'Storage Distribution.*?\n(.*?)\n\n', content, re.DOTALL)
            if storage_section:
                self.yubihsm_data['storage_recommendations'] = storage_section.group(1)

        except Exception as e:
            logger.error(f"Error parsing YubiHSM README: {e}")

    def parse_yubihsm_terraform(self, terraform_dir: Path):
        """Parse YubiHSM Terraform configurations"""
        try:
            main_tf = terraform_dir / 'main.tf'
            if main_tf.exists():
                with open(main_tf, 'r') as f:
                    content = f.read()
                    self.yubihsm_data['terraform_config'] = content

            variables_tf = terraform_dir / 'variables.tf'
            if variables_tf.exists():
                with open(variables_tf, 'r') as f:
                    content = f.read()
                    self.yubihsm_data['terraform_variables'] = content

        except Exception as e:
            logger.error(f"Error parsing YubiHSM Terraform: {e}")

    def parse_yubihsm_script(self, script_file: Path):
        """Parse YubiHSM management scripts"""
        try:
            with open(script_file, 'r') as f:
                content = f.read()

            script_name = script_file.name
            self.yubihsm_data[f'script_{script_name}'] = content

            # Extract configuration from scripts
            if 'yubihsm_complete_storage.sh' in script_name:
                self.extract_storage_config(content)
            elif 'yubihsm_lifecycle_management.sh' in script_name:
                self.extract_lifecycle_config(content)

        except Exception as e:
            logger.error(f"Error parsing YubiHSM script {script_file}: {e}")

    def extract_storage_config(self, content: str):
        """Extract storage configuration from scripts"""
        # Extract object ID ranges
        id_ranges = re.findall(r'(\d+)-\d+.*objects', content)
        if id_ranges:
            self.yubihsm_data['object_ranges'] = id_ranges

    def extract_lifecycle_config(self, content: str):
        """Extract lifecycle configuration from scripts"""
        # Extract lifecycle operations
        operations = re.findall(r'(\w+).*operations', content)
        if operations:
            self.yubihsm_data['lifecycle_operations'] = operations

    def build_destruction_inventory(self):
        """Build comprehensive destruction inventory from parsed data"""
        logger.info("Building destruction inventory...")

        # Build YubiHSM inventory from documentation
        self.build_yubihsm_inventory()

        # Build YubiKey inventory
        self.build_yubikey_inventory()

        # Build MikroTik inventory from Ansible data
        self.build_mikrotik_inventory()

        # Build security configuration inventory
        self.build_security_inventory()

    def build_yubihsm_inventory(self):
        """Build YubiHSM device inventory"""
        # From YubiHSM documentation analysis
        if 'storage_recommendations' in self.yubihsm_data:
            # Assume standard YubiHSM 2 configuration
            self.destruction_inventory['yubihsm_devices'].append({
                'serial': 'auto-detected',
                'model': 'YubiHSM 2 FIPS',
                'capacity': 256,
                'fips_certified': True,
                'source': 'documentation_analysis'
            })

    def build_yubikey_inventory(self):
        """Build YubiKey token inventory"""
        # This would be populated from actual inventory systems
        # For now, placeholder based on documentation
        self.destruction_inventory['yubikey_tokens'] = []

    def build_mikrotik_inventory(self):
        """Build MikroTik device inventory from Ansible data"""
        for host in self.destruction_inventory['ansible_hosts']:
            if 'mikrotik' in host.get('group', '').lower() or 'router' in host.get('group', '').lower():
                self.destruction_inventory['mikrotik_devices'].append({
                    'name': host['name'],
                    'group': host['group'],
                    'inventory': host['inventory'],
                    'vars': host.get('vars', {})
                })

    def build_security_inventory(self):
        """Build security configuration inventory"""
        self.destruction_inventory['security_configs'] = {
            'yubihsm_config': self.yubihsm_data,
            'meraki_config': self.meraki_data,
            'infrastructure_config': self.infrastructure_data
        }

    def validate_inventory(self):
        """Validate completeness of destruction inventory"""
        logger.info("Validating destruction inventory...")

        required_components = [
            'yubihsm_devices',
            'aws_accounts',
            'ansible_hosts',
            'meraki_networks'
        ]

        for component in required_components:
            if not self.destruction_inventory.get(component):
                logger.warning(f"Missing or empty component: {component}")
            else:
                logger.info(f"Validated component: {component} ({len(self.destruction_inventory[component])} items)")

    def export_inventory(self, output_file: str = 'destruction_inventory.json'):
        """Export destruction inventory to JSON file"""
        with open(output_file, 'w') as f:
            json.dump(self.destruction_inventory, f, indent=2, default=str)

        logger.info(f"Destruction inventory exported to {output_file}")

    def generate_report(self, output_file: str = 'documentation_analysis_report.md'):
        """Generate analysis report"""
        report = f"""# Documentation Analysis Report

## Infrastructure Components Found

### AWS Accounts ({len(self.destruction_inventory['aws_accounts'])})
{chr(10).join(f"- {acc['name']}: {len(acc.get('regions', []))} regions" for acc in self.destruction_inventory['aws_accounts'])}

### Ansible Hosts ({len(self.destruction_inventory['ansible_hosts'])})
{chr(10).join(f"- {host['name']} ({host['group']})" for host in self.destruction_inventory['ansible_hosts'][:10])}
{'...' if len(self.destruction_inventory['ansible_hosts']) > 10 else ''}

### Meraki Networks ({len(self.destruction_inventory['meraki_networks'])})
{chr(10).join(f"- {net.get('id', net.get('model', 'Unknown'))}" for net in self.destruction_inventory['meraki_networks'][:10])}
{'...' if len(self.destruction_inventory['meraki_networks']) > 10 else ''}

### YubiHSM Devices ({len(self.destruction_inventory['yubihsm_devices'])})
{chr(10).join(f"- {hsm['model']} (Serial: {hsm['serial']})" for hsm in self.destruction_inventory['yubihsm_devices'])}

### MikroTik Devices ({len(self.destruction_inventory['mikrotik_devices'])})
{chr(10).join(f"- {dev['name']} ({dev['group']})" for dev in self.destruction_inventory['mikrotik_devices'])}

## Security Configurations
- YubiHSM FIPS Certified: {self.yubihsm_data.get('fips_certified', False)}
- Storage Capacity: {self.yubihsm_data.get('storage_capacity', 'Unknown')}
- Encryption Standard: {self.yubihsm_data.get('encryption_standard', 'Unknown')}

## Recommendations
1. Verify all inventories are complete before destruction
2. Test all destruction procedures in isolated environment
3. Ensure proper backup of audit logs before destruction
4. Confirm legal authorization for all destructive actions

---
Generated by Documentation Parser
"""

        with open(output_file, 'w') as f:
            f.write(report)

        logger.info(f"Analysis report generated: {output_file}")


def main():
    parser = argparse.ArgumentParser(description='Parse infrastructure documentation for destruction planning')
    parser.add_argument('--infrastructure', required=True, help='Path to infrastructure documentation')
    parser.add_argument('--meraki', required=True, help='Path to Meraki documentation')
    parser.add_argument('--yubihsm', required=True, help='Path to YubiHSM documentation')
    parser.add_argument('--output', default='destruction_inventory.json', help='Output inventory file')
    parser.add_argument('--report', default='documentation_analysis_report.md', help='Output report file')

    args = parser.parse_args()

    doc_parser = DocumentationParser()
    inventory = doc_parser.parse_all_documentation(args.infrastructure, args.meraki, args.yubihsm)

    # Export results
    doc_parser.export_inventory(args.output)
    doc_parser.generate_report(args.report)

    logger.info("Documentation parsing complete!")


if __name__ == '__main__':
    main()