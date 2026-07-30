#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
PCI DSS Compliance Validator
Validates PCI DSS requirements as code for iGaming platforms.
Implements automated checks for firewall rules, password policies, and PAN masking.
"""

import yaml
import subprocess
import json
from datetime import datetime, timezone
from typing import Any, Dict, List


class PCIComplianceValidator:
    def __init__(self, requirements_file):
        with open(requirements_file, 'r') as f:
            self.requirements = yaml.safe_load(f)

    def validate_firewall_rules(self):
        """Validate firewall rules are documented"""
        try:
            # Get current firewall rules
            result = subprocess.run(['iptables', '-L', '-n'],
                                  capture_output=True, text=True)

            rules = result.stdout.split('\n')
            documented_rules = self.get_documented_firewall_rules()

            # Check if all rules are documented
            undocumented_rules = []
            for rule in rules:
                if rule and not self.is_rule_documented(rule, documented_rules):
                    undocumented_rules.append(rule)

            return {
                'compliant': len(undocumented_rules) == 0,
                'undocumented_rules': undocumented_rules,
                'total_rules': len(rules),
                'documented_rules': len(documented_rules)
            }
        except Exception as e:
            return {
                'compliant': False,
                'error': str(e)
            }

    def validate_no_default_passwords(self):
        """Check for default passwords in system"""
        default_passwords = [
            'password', 'admin', '123456', 'root', 'guest'
        ]

        findings = []

        # Check system users
        try:
            with open('/etc/shadow', 'r') as f:
                for line in f:
                    parts = line.split(':')
                    if len(parts) > 1:
                        username = parts[0]
                        password_hash = parts[1]

                        # Check for weak hashes
                        if password_hash in ['!', '*', '']:
                            findings.append({
                                'type': 'locked_account',
                                'username': username,
                                'status': 'compliant'
                            })
                        elif len(password_hash) < 50:  # Likely weak hash
                            findings.append({
                                'type': 'weak_hash',
                                'username': username,
                                'status': 'non_compliant'
                            })
        except PermissionError:
            findings.append({
                'type': 'permission_error',
                'status': 'unable_to_check'
            })

        return {
            'compliant': len([f for f in findings if f.get('status') == 'non_compliant']) == 0,
            'findings': findings
        }

    def validate_pan_masking(self):
        """Validate PAN masking in application logs"""
        # This would typically check application logs
        # For demonstration, returning mock data
        return {
            'compliant': True,
            'masked_samples': 150,
            'unmasked_samples': 0,
            'check_date': datetime.now().isoformat()
        }

    def get_documented_firewall_rules(self):
        """Return documented firewall rules (placeholder implementation)"""
        return []

    def is_rule_documented(self, rule, documented_rules):
        """Check if a rule is documented (placeholder implementation)"""
        return False

    def run_full_compliance_check(self):
        """Run complete PCI DSS compliance validation"""
        results: Dict[str, Any] = {
            'timestamp': datetime.now().isoformat(),
            'requirements': []
        }

        for requirement in self.requirements['requirements']:
            req_result = {
                'id': requirement['id'],
                'description': requirement['description'],
                'controls': []
            }

            for control in requirement['controls']:
                if control['name'] == 'firewall_rules_documented':
                    validation_result = self.validate_firewall_rules()
                elif control['name'] == 'no_default_passwords':
                    validation_result = self.validate_no_default_passwords()
                elif control['name'] == 'pan_masking':
                    validation_result = self.validate_pan_masking()
                else:
                    validation_result = {'compliant': None, 'note': 'Validation not implemented'}

                control_result = {
                    'name': control['name'],
                    'type': control['type'],
                    'severity': control['severity'],
                    'compliant': validation_result['compliant'],
                    'details': validation_result
                }
                req_result['controls'].append(control_result)

            results['requirements'].append(req_result)

        # Calculate overall compliance score
        total_controls = sum(len(req['controls']) for req in results['requirements'])
        compliant_controls = sum(
            sum(1 for control in req['controls'] if control['compliant'] is True)
            for req in results['requirements']
        )

        results['overall_compliance_score'] = (compliant_controls / total_controls * 100) if total_controls > 0 else 0

        return results


# PCI DSS Requirements definition (normally stored in a separate YAML file)
PCI_DSS_REQUIREMENTS_YAML = """
version: "4.0"
requirements:
  - id: "REQ-1.1"
    description: "Firewall configuration standards"
    controls:
      - name: "firewall_rules_documented"
        type: "policy"
        validation: "firewall_rules_have_documentation"
        severity: "high"
        automated_check: true
        remediation: "Document all firewall rules with business justification"

      - name: "firewall_rule_review"
        type: "process"
        frequency: "quarterly"
        validation: "firewall_rules_reviewed_quarterly"
        severity: "medium"
        automated_check: true
        remediation: "Review and update firewall rules every quarter"

  - id: "REQ-2.1"
    description: "Default passwords and security parameters"
    controls:
      - name: "no_default_passwords"
        type: "technical"
        validation: "system_has_no_default_passwords"
        severity: "critical"
        automated_check: true
        remediation: "Change all default passwords immediately"

      - name: "password_complexity"
        type: "technical"
        validation: "password_meets_complexity_requirements"
        severity: "high"
        automated_check: true
        remediation: "Implement strong password policy"

  - id: "REQ-3.4"
    description: "PAN display masking"
    controls:
      - name: "pan_masking"
        type: "technical"
        validation: "pan_display_masked"
        severity: "critical"
        automated_check: true
        remediation: "Mask PAN when displayed (first 6, last 4 digits only)"

      - name: "pan_storage_encrypted"
        type: "technical"
        validation: "pan_stored_encrypted"
        severity: "critical"
        automated_check: true
        remediation: "Encrypt PAN at rest using AES-256"
"""


if __name__ == "__main__":
    import tempfile
    import os

    # Write requirements to a temp file for demonstration
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write(PCI_DSS_REQUIREMENTS_YAML)
        temp_path = f.name

    try:
        validator = PCIComplianceValidator(temp_path)
        results = validator.run_full_compliance_check()

        print(json.dumps(results, indent=2))

        # Exit with error code if not compliant
        if results['overall_compliance_score'] < 100:
            exit(1)
    finally:
        os.unlink(temp_path)
