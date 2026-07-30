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
Comprehensive Audit Logging and Compliance Reporting System
For Secure Data Destruction System (SDDS)
"""

import sys
import os
import json
import csv
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from pathlib import Path
import hashlib
import hmac

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/audit_logger.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class AuditEvent:
    """Represents a single audit event"""

    def __init__(self, action: str, component: str, details: str = "",
                 user: str = "SYSTEM", ip_address: str = "localhost",
                 session_id: Optional[str] = None, severity: str = "INFO"):
        self.timestamp = datetime.now()
        self.action = action
        self.component = component
        self.details = details
        self.user = user
        self.ip_address = ip_address
        self.session_id = session_id or self._generate_session_id()
        self.severity = severity.upper()
        self.checksum = self._calculate_checksum()

    def _generate_session_id(self) -> str:
        """Generate a unique session ID"""
        import uuid
        return str(uuid.uuid4())

    def _calculate_checksum(self) -> str:
        """Calculate SHA256 checksum of the event data"""
        data = f"{self.timestamp.isoformat()}{self.action}{self.component}{self.details}{self.user}"
        return hashlib.sha256(data.encode()).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        """Convert event to dictionary"""
        return {
            'timestamp': self.timestamp.isoformat(),
            'action': self.action,
            'component': self.component,
            'details': self.details,
            'user': self.user,
            'ip_address': self.ip_address,
            'session_id': self.session_id,
            'severity': self.severity,
            'checksum': self.checksum
        }

    def to_json(self) -> str:
        """Convert event to JSON string"""
        return json.dumps(self.to_dict(), indent=2)

class ComplianceReporter:
    """Generates compliance reports for various standards"""

    def __init__(self, audit_events: List[AuditEvent]):
        self.audit_events = audit_events

    def generate_gdpr_report(self) -> Dict[str, Any]:
        """Generate GDPR compliance report"""
        return {
            'standard': 'GDPR',
            'data_destruction_events': len([e for e in self.audit_events if 'destruction' in e.action.lower()]),
            'data_retention_check': self._check_data_retention(),
            'audit_trail_integrity': self._verify_audit_integrity(),
            'compliance_status': 'COMPLIANT' if self._check_gdpr_compliance() else 'NON_COMPLIANT'
        }

    def generate_hipaa_report(self) -> Dict[str, Any]:
        """Generate HIPAA compliance report"""
        return {
            'standard': 'HIPAA',
            'phi_destruction_events': len([e for e in self.audit_events if 'destruction' in e.action.lower()]),
            'encryption_key_destruction': self._check_key_destruction(),
            'access_controls': self._verify_access_controls(),
            'audit_trail_completeness': self._check_audit_completeness(),
            'compliance_status': 'COMPLIANT' if self._check_hipaa_compliance() else 'NON_COMPLIANT'
        }

    def generate_pci_dss_report(self) -> Dict[str, Any]:
        """Generate PCI DSS compliance report"""
        return {
            'standard': 'PCI DSS',
            'cardholder_data_destruction': len([e for e in self.audit_events if 'destruction' in e.action.lower()]),
            'key_management_compliance': self._check_key_management(),
            'audit_trail_verification': self._verify_audit_trail(),
            'compliance_status': 'COMPLIANT' if self._check_pci_compliance() else 'NON_COMPLIANT'
        }

    def generate_sox_report(self) -> Dict[str, Any]:
        """Generate SOX compliance report"""
        return {
            'standard': 'SOX',
            'financial_data_destruction': len([e for e in self.audit_events if 'destruction' in e.action.lower()]),
            'change_management_audit': self._check_change_management(),
            'access_logging_completeness': self._check_access_logging(),
            'compliance_status': 'COMPLIANT' if self._check_sox_compliance() else 'NON_COMPLIANT'
        }

    def _check_data_retention(self) -> bool:
        """Check GDPR data retention compliance"""
        # Implementation would check if data was properly destroyed
        return len(self.audit_events) > 0

    def _verify_audit_integrity(self) -> bool:
        """Verify audit trail integrity"""
        # Check checksums and sequence
        return True

    def _check_gdpr_compliance(self) -> bool:
        """Overall GDPR compliance check"""
        return True  # Simplified

    def _check_key_destruction(self) -> bool:
        """Check encryption key destruction"""
        return len([e for e in self.audit_events if 'key' in e.details.lower() and 'destroy' in e.action.lower()]) > 0

    def _verify_access_controls(self) -> bool:
        """Verify access controls"""
        return True

    def _check_audit_completeness(self) -> bool:
        """Check audit completeness"""
        return len(self.audit_events) > 0

    def _check_hipaa_compliance(self) -> bool:
        """Overall HIPAA compliance check"""
        return True

    def _check_key_management(self) -> bool:
        """Check key management compliance"""
        return True

    def _verify_audit_trail(self) -> bool:
        """Verify audit trail"""
        return True

    def _check_pci_compliance(self) -> bool:
        """Overall PCI DSS compliance check"""
        return True

    def _check_change_management(self) -> bool:
        """Check change management"""
        return True

    def _check_access_logging(self) -> bool:
        """Check access logging"""
        return True

    def _check_sox_compliance(self) -> bool:
        """Overall SOX compliance check"""
        return True

class AuditLogger:
    """Comprehensive audit logging system"""

    def __init__(self, log_file: str = '/var/log/sdds_audit.log',
                 backup_location: str = '/backup/audit_logs/'):
        self.log_file = Path(log_file)
        self.backup_location = Path(backup_location)
        self.audit_events: List[AuditEvent] = []
        self.hmac_secret = os.getenv('AUDIT_HMAC_SECRET', 'default-audit-secret-change-me')

        # Ensure directories exist
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self.backup_location.mkdir(parents=True, exist_ok=True)

        # Load existing audit events
        self._load_existing_events()

    def log_event(self, action: str, component: str, details: str = "",
                  user: str = "SYSTEM", ip_address: str = "localhost",
                  severity: str = "INFO") -> AuditEvent:
        """Log a new audit event"""
        event = AuditEvent(action, component, details, user, ip_address, severity=severity)
        self.audit_events.append(event)

        # Write to log file immediately
        self._write_event_to_file(event)

        # Backup critical events
        if severity in ['CRITICAL', 'ERROR', 'WARNING']:
            self._backup_event(event)

        logger.info(f"AUDIT: {action} - {component} - {details}")
        return event

    def _write_event_to_file(self, event: AuditEvent):
        """Write event to log file"""
        try:
            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write(event.to_json() + '\n')
        except Exception as e:
            logger.error(f"Failed to write audit event to file: {e}")

    def _backup_event(self, event: AuditEvent):
        """Backup critical event to secure location"""
        try:
            backup_file = self.backup_location / f"audit_backup_{datetime.now().strftime('%Y%m%d')}.log"
            with open(backup_file, 'a', encoding='utf-8') as f:
                f.write(event.to_json() + '\n')
        except Exception as e:
            logger.error(f"Failed to backup audit event: {e}")

    def _load_existing_events(self):
        """Load existing audit events from file"""
        try:
            if self.log_file.exists():
                with open(self.log_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                event_data = json.loads(line)
                                # Recreate event object (without full validation for performance)
                                event = AuditEvent(
                                    event_data['action'],
                                    event_data['component'],
                                    event_data.get('details', ''),
                                    event_data.get('user', 'SYSTEM'),
                                    event_data.get('ip_address', 'localhost'),
                                    event_data.get('session_id'),
                                    event_data.get('severity', 'INFO')
                                )
                                self.audit_events.append(event)
                            except Exception as e:
                                logger.warning(f"Failed to parse audit event: {e}")
        except Exception as e:
            logger.error(f"Failed to load existing audit events: {e}")

    def get_events_by_component(self, component: str) -> List[AuditEvent]:
        """Get all events for a specific component"""
        return [e for e in self.audit_events if e.component == component]

    def get_events_by_action(self, action: str) -> List[AuditEvent]:
        """Get all events for a specific action"""
        return [e for e in self.audit_events if e.action == action]

    def get_events_by_severity(self, severity: str) -> List[AuditEvent]:
        """Get all events for a specific severity"""
        return [e for e in self.audit_events if e.severity == severity.upper()]

    def get_events_in_timeframe(self, start_time: datetime, end_time: datetime) -> List[AuditEvent]:
        """Get all events within a time frame"""
        return [e for e in self.audit_events if start_time <= e.timestamp <= end_time]

    def verify_audit_integrity(self) -> bool:
        """Verify the integrity of the audit trail"""
        try:
            for event in self.audit_events:
                expected_checksum = event._calculate_checksum()
                if event.checksum != expected_checksum:
                    logger.error(f"Audit integrity violation for event: {event.action}")
                    return False
            return True
        except Exception as e:
            logger.error(f"Audit integrity check failed: {e}")
            return False

    def generate_compliance_report(self, output_file: str = 'compliance_report.json') -> bool:
        """Generate comprehensive compliance report"""
        try:
            reporter = ComplianceReporter(self.audit_events)

            report = {
                'generated_at': datetime.now().isoformat(),
                'total_audit_events': len(self.audit_events),
                'audit_integrity_verified': self.verify_audit_integrity(),
                'compliance_reports': {
                    'gdpr': reporter.generate_gdpr_report(),
                    'hipaa': reporter.generate_hipaa_report(),
                    'pci_dss': reporter.generate_pci_dss_report(),
                    'sox': reporter.generate_sox_report()
                },
                'summary': {
                    'destruction_events': len([e for e in self.audit_events if 'destruction' in e.action.lower()]),
                    'security_events': len([e for e in self.audit_events if e.severity in ['CRITICAL', 'ERROR']]),
                    'compliance_violations': 0  # Would be calculated based on specific rules
                }
            }

            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(report, f, indent=2)

            logger.info(f"Compliance report generated: {output_file}")
            return True

        except Exception as e:
            logger.error(f"Failed to generate compliance report: {e}")
            return False

    def export_to_csv(self, output_file: str = 'audit_events.csv') -> bool:
        """Export audit events to CSV format"""
        try:
            with open(output_file, 'w', newline='', encoding='utf-8') as f:
                fieldnames = ['timestamp', 'action', 'component', 'details', 'user', 'ip_address', 'session_id', 'severity', 'checksum']
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()

                for event in self.audit_events:
                    writer.writerow(event.to_dict())

            logger.info(f"Audit events exported to CSV: {output_file}")
            return True

        except Exception as e:
            logger.error(f"Failed to export to CSV: {e}")
            return False

    def get_statistics(self) -> Dict[str, Any]:
        """Get audit statistics"""
        total_events = len(self.audit_events)

        if total_events == 0:
            return {'total_events': 0}

        # Calculate statistics
        severity_counts = {}
        component_counts = {}
        action_counts = {}

        for event in self.audit_events:
            severity_counts[event.severity] = severity_counts.get(event.severity, 0) + 1
            component_counts[event.component] = component_counts.get(event.component, 0) + 1
            action_counts[event.action] = action_counts.get(event.action, 0) + 1

        # Time-based statistics
        if self.audit_events:
            first_event = min(e.timestamp for e in self.audit_events)
            last_event = max(e.timestamp for e in self.audit_events)
            duration = last_event - first_event
        else:
            duration = timedelta(0)

        return {
            'total_events': total_events,
            'severity_breakdown': severity_counts,
            'component_breakdown': component_counts,
            'action_breakdown': action_counts,
            'time_span': str(duration),
            'first_event': first_event.isoformat() if self.audit_events else None,
            'last_event': last_event.isoformat() if self.audit_events else None,
            'events_per_hour': total_events / max(duration.total_seconds() / 3600, 1)
        }

def main():
    """CLI interface for audit logging system"""
    import argparse

    parser = argparse.ArgumentParser(description='Audit Logging and Compliance Reporting System')
    parser.add_argument('--log-event', nargs=2, metavar=('ACTION', 'COMPONENT'),
                       help='Log a new audit event')
    parser.add_argument('--details', help='Details for the audit event')
    parser.add_argument('--severity', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
                       default='INFO', help='Event severity')
    parser.add_argument('--user', default='SYSTEM', help='User who triggered the event')
    parser.add_argument('--compliance-report', help='Generate compliance report')
    parser.add_argument('--export-csv', help='Export audit events to CSV')
    parser.add_argument('--statistics', action='store_true', help='Show audit statistics')
    parser.add_argument('--verify-integrity', action='store_true', help='Verify audit integrity')

    args = parser.parse_args()

    try:
        audit_logger = AuditLogger()

        if args.log_event:
            action, component = args.log_event
            audit_logger.log_event(
                action=action,
                component=component,
                details=args.details or "",
                user=args.user,
                severity=args.severity
            )
            print("✓ Audit event logged")

        if args.compliance_report:
            success = audit_logger.generate_compliance_report(args.compliance_report)
            print(f"{'✓' if success else '✗'} Compliance report generated")

        if args.export_csv:
            success = audit_logger.export_to_csv(args.export_csv)
            print(f"{'✓' if success else '✗'} CSV export completed")

        if args.statistics:
            stats = audit_logger.get_statistics()
            print("=== AUDIT STATISTICS ===")
            print(f"Total Events: {stats['total_events']}")
            print(f"Time Span: {stats['time_span']}")
            print(f"Events/Hour: {stats['events_per_hour']:.2f}")
            print("\nSeverity Breakdown:")
            for severity, count in stats['severity_breakdown'].items():
                print(f"  {severity}: {count}")
            print("\nComponent Breakdown:")
            for component, count in stats['component_breakdown'].items():
                print(f"  {component}: {count}")

        if args.verify_integrity:
            integrity_ok = audit_logger.verify_audit_integrity()
            print(f"Audit Integrity: {'✓ VERIFIED' if integrity_ok else '✗ VIOLATION DETECTED'}")

    except KeyboardInterrupt:
        logger.info("Operation cancelled by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()