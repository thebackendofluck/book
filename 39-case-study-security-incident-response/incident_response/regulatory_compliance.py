#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 39, Case Study.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Regulatory Compliance and Incident Notification Manager for iGaming

Manages multi-jurisdiction regulatory notifications following security incidents,
with jurisdiction-specific notification templates for UKGC, MGA, Swedish
Spelinspektionen, and other regulators. Tracks response requirements and
maintains overall compliance status across all affected jurisdictions.

Also includes the IncidentResponseBestPractices utility class with
comprehensive playbook generation and incident metrics calculation.

Usage:
    from regulatory_compliance import RegulatoryComplianceManager

    manager = RegulatoryComplianceManager(jurisdiction_configs=configs)
    result = await manager.handle_regulatory_notifications(incident_data)
    # Returns: notifications sent count, per-jurisdiction status, next actions
"""

from datetime import datetime
from typing import Dict, List


class RegulatoryComplianceManager:

    def __init__(self, jurisdiction_configs: Dict):
        self.jurisdictions = jurisdiction_configs
        self.notification_templates = self._load_notification_templates()

    async def handle_regulatory_notifications(self, incident_data: Dict) -> Dict:
        """Handle regulatory notifications across multiple jurisdictions"""

        affected_jurisdictions = incident_data.get('affected_jurisdictions', [])

        regulatory_responses = {}

        for jurisdiction in affected_jurisdictions:
            jurisdiction_config = self.jurisdictions.get(jurisdiction)
            if not jurisdiction_config:
                continue

            # Generate jurisdiction-specific notification
            notification = await self._generate_regulatory_notification(
                incident_data,
                jurisdiction_config
            )

            # Submit notification
            submission_result = await self._submit_regulatory_notification(
                notification,
                jurisdiction_config
            )

            # Track response requirements
            response_tracking = await self._track_regulatory_response_requirements(
                jurisdiction,
                incident_data,
                submission_result
            )

            regulatory_responses[jurisdiction] = {
                'notification_submitted': submission_result,
                'response_requirements': response_tracking,
                'compliance_status': 'submitted'
            }

        return {
            'regulatory_notifications_sent': len(regulatory_responses),
            'jurisdiction_responses': regulatory_responses,
            'overall_compliance_status': self._assess_overall_compliance(regulatory_responses),
            'next_actions': self._generate_next_regulatory_actions(regulatory_responses)
        }

    async def _generate_regulatory_notification(self, incident_data: Dict,
                                                jurisdiction_config: Dict) -> Dict:
        """Generate regulatory notification specific to jurisdiction"""

        jurisdiction = jurisdiction_config['name']

        # Base notification data
        notification_data = {
            'incident_id': incident_data['incident_id'],
            'notification_date': datetime.now().isoformat(),
            'operator_license': jurisdiction_config['license_number'],
            'incident_classification': incident_data['classification'],
            'affected_systems': incident_data['affected_systems'],
            'potential_impact': incident_data['potential_impact'],
            'containment_status': incident_data['containment_status'],
            'response_actions': incident_data['response_actions']
        }

        # Jurisdiction-specific requirements
        if jurisdiction == 'UK':
            notification_data.update({
                'ukgc_notification_type': 'security_incident',
                'data_breach_notification': incident_data.get('data_breach_involved', False),
                'player_funds_at_risk': incident_data.get('player_funds_at_risk', False),
                'estimated_financial_impact': incident_data.get('estimated_financial_impact', 0)
            })
        elif jurisdiction == 'Malta':
            notification_data.update({
                'mga_notification_category': 'cyber_security_incident',
                'technical_details_required': True,
                'remediation_timeline': incident_data.get('remediation_timeline', ''),
                'third_party_involvement': incident_data.get('third_party_involvement', False)
            })
        elif jurisdiction == 'Sweden':
            notification_data.update({
                'spelinspektionen_incident_type': 'sakerhetsincident',
                'swedish_language_summary': await self._generate_swedish_summary(incident_data),
                'data_protection_impact': incident_data.get('gdpr_impact_assessment', {})
            })

        return notification_data

    def _load_notification_templates(self) -> Dict:
        """Load jurisdiction-specific notification templates"""
        # Placeholder: load from template storage
        return {}

    async def _submit_regulatory_notification(self, notification: Dict,
                                               jurisdiction_config: Dict) -> Dict:
        """Submit notification to regulatory authority"""
        # Placeholder: send via regulator API or secure email
        return {
            'submitted': True,
            'submission_id': f"NOTIF-{notification['incident_id']}-{jurisdiction_config['name']}",
            'submission_timestamp': datetime.now().isoformat(),
            'acknowledgement_received': False
        }

    async def _track_regulatory_response_requirements(self, jurisdiction: str,
                                                       incident_data: Dict,
                                                       submission_result: Dict) -> Dict:
        """Track regulatory response deadlines and requirements"""
        deadlines = {
            'UK': {'initial_report': '72_hours', 'full_report': '30_days'},
            'Malta': {'initial_report': '24_hours', 'full_report': '14_days'},
            'Sweden': {'initial_report': '72_hours', 'full_report': '30_days'}
        }
        return {
            'deadlines': deadlines.get(jurisdiction, {}),
            'follow_up_required': True,
            'tracking_reference': submission_result.get('submission_id')
        }

    def _assess_overall_compliance(self, regulatory_responses: Dict) -> str:
        """Assess overall compliance status across jurisdictions"""
        all_submitted = all(
            r.get('compliance_status') == 'submitted'
            for r in regulatory_responses.values()
        )
        return 'compliant' if all_submitted else 'action_required'

    def _generate_next_regulatory_actions(self, regulatory_responses: Dict) -> List[str]:
        """Generate list of next required regulatory actions"""
        return [
            'Prepare full incident report for 30-day submission deadline',
            'Schedule follow-up call with UKGC regulatory liaison',
            'Complete GDPR data breach assessment documentation',
            'Submit remediation evidence to MGA within 14 days'
        ]

    async def _generate_swedish_summary(self, incident_data: Dict) -> str:
        """Generate Swedish language incident summary"""
        # Placeholder: translate incident summary to Swedish
        return "Säkerhetsincident sammanfattning..."


class IncidentResponseBestPractices:

    @staticmethod
    def create_incident_response_playbook() -> Dict:
        """Create comprehensive incident response playbook"""

        return {
            'preparation_phase': {
                'team_setup': [
                    'Establish incident response team with defined roles',
                    'Create on-call rotation with 24/7 coverage',
                    'Implement secure communication channels',
                    'Develop incident classification criteria',
                    'Create escalation procedures'
                ],
                'tools_and_systems': [
                    'Deploy SIEM with custom iGaming rules',
                    'Implement automated incident detection',
                    'Setup forensic evidence collection tools',
                    'Create secure evidence storage system',
                    'Establish regulatory notification workflows'
                ],
                'training_and_exercises': [
                    'Conduct quarterly tabletop exercises',
                    'Perform annual full-scale simulations',
                    'Train team on forensic procedures',
                    'Practice regulatory notification processes',
                    'Test disaster recovery procedures'
                ]
            },
            'detection_and_analysis': {
                'detection_methods': [
                    'SIEM correlation rules for iGaming threats',
                    'Behavioral analytics for anomaly detection',
                    'Threat intelligence integration',
                    'Player behavior monitoring',
                    'Financial transaction monitoring'
                ],
                'analysis_procedures': [
                    'Evidence preservation protocols',
                    'Impact assessment methodologies',
                    'Threat actor attribution techniques',
                    'Attack vector identification',
                    'Timeline reconstruction procedures'
                ]
            },
            'containment_eradication_recovery': {
                'containment_strategies': [
                    'Network segmentation and isolation',
                    'Malicious IP address blocking',
                    'Compromised account disabling',
                    'Service isolation procedures',
                    'Data access restriction protocols'
                ],
                'eradication_methods': [
                    'Malware removal procedures',
                    'Backdoor elimination techniques',
                    'Persistence mechanism removal',
                    'Vulnerability patching protocols',
                    'System hardening procedures'
                ],
                'recovery_procedures': [
                    'System restoration from clean backups',
                    'Service validation and testing',
                    'Performance monitoring post-recovery',
                    'Security control validation',
                    'Regulatory compliance verification'
                ]
            }
        }

    @staticmethod
    def calculate_incident_response_metrics(incident_data: Dict) -> Dict:
        """Calculate comprehensive incident response metrics"""

        metrics = {
            'detection_metrics': {
                'mean_time_to_detect': incident_data.get('mttd_hours', 0),
                'detection_accuracy': incident_data.get('detection_accuracy', 0),
                'false_positive_rate': incident_data.get('false_positive_rate', 0)
            },
            'response_metrics': {
                'mean_time_to_respond': incident_data.get('mttr_hours', 0),
                'response_team_activation_time': incident_data.get('activation_time_minutes', 0),
                'containment_time': incident_data.get('containment_time_hours', 0)
            },
            'recovery_metrics': {
                'mean_time_to_recovery': incident_data.get('mttr_recovery_hours', 0),
                'service_restoration_time': incident_data.get('restoration_time_hours', 0),
                'data_recovery_percentage': incident_data.get('data_recovery_percentage', 0)
            },
            'effectiveness_metrics': {
                'incident_resolution_rate': incident_data.get('resolution_rate', 0),
                'customer_impact_minimization': incident_data.get('customer_impact_score', 0),
                'regulatory_compliance_rate': incident_data.get('compliance_rate', 0),
                'communication_effectiveness': incident_data.get('communication_score', 0)
            }
        }

        # Calculate overall incident response maturity
        maturity_score = (
            metrics['detection_metrics']['detection_accuracy'] * 0.25 +
            (1 / (1 + metrics['response_metrics']['mean_time_to_respond'])) * 0.25 +
            metrics['recovery_metrics']['data_recovery_percentage'] * 0.25 +
            metrics['effectiveness_metrics']['incident_resolution_rate'] * 0.25
        )

        metrics['overall_maturity_score'] = maturity_score

        return metrics
