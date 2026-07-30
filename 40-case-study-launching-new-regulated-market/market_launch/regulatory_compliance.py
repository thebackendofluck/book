#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 40, Case Study.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Ontario Regulatory Compliance Framework for iGaming Market Entry

Defines the comprehensive compliance configuration for operating under
iGaming Ontario and AGCO licensing, covering responsible gaming requirements,
technical standards (geo-verification, age verification, data localization),
and content requirements including French language support.

Usage:
    from regulatory_compliance import OntarioRegulatoryCompliance

    compliance = OntarioRegulatoryCompliance(operator_config=config)
    framework = compliance.compliance_framework
    # Access licensing_requirements, responsible_gaming_requirements,
    # technical_requirements, content_requirements
"""

from typing import Dict


class OntarioRegulatoryCompliance:
    def __init__(self, operator_config: Dict):
        self.operator = operator_config
        self.compliance_framework = self._initialize_compliance_framework()

    def _initialize_compliance_framework(self) -> Dict:
        """Initialize comprehensive compliance framework for Ontario"""

        return {
            "licensing_requirements": {
                "igaming_ontario_license": {
                    "application_fee": 50000,  # CAD
                    "annual_fee": 100000,
                    "processing_time": "6-12_months",
                    "renewal_frequency": "annual"
                },
                "agco_registration": {
                    "registration_fee": 25000,
                    "compliance_audit_frequency": "quarterly",
                    "reporting_requirements": "monthly"
                }
            },
            "responsible_gaming_requirements": {
                "self_exclusion_system": {
                    "mandatory_integration": True,
                    "national_register": "iGaming Ontario",
                    "cooling_off_period": "24_hours",
                    "permanent_exclusion": True
                },
                "reality_checks": {
                    "mandatory": True,
                    "default_interval": "60_minutes",
                    "customizable": True,
                    "minimum_interval": "15_minutes"
                },
                "deposit_limits": {
                    "daily_default": 1000,  # CAD
                    "weekly_default": 2500,
                    "monthly_default": 5000,
                    "customizable": True
                },
                "loss_limits": {
                    "daily_limit": True,
                    "weekly_limit": True,
                    "monthly_limit": True
                },
                "time_limits": {
                    "daily_default": "4_hours",
                    "weekly_default": "20_hours",
                    "mandatory_breaks": True
                }
            },
            "technical_requirements": {
                "geo_verification": {
                    "mandatory": True,
                    "accuracy_requirement": "99.9%",
                    "verification_methods": ["GPS", "IP", "device_fingerprinting"],
                    "update_frequency": "real_time"
                },
                "age_verification": {
                    "mandatory": True,
                    "minimum_age": 19,
                    "verification_methods": ["government_id", "credit_check", "biometric"],
                    "retention_period": "7_years"
                },
                "data_localization": {
                    "player_data_residency": "Canada",
                    "backup_data_location": "Canada",
                    "processing_restrictions": True
                },
                "encryption_standards": {
                    "data_at_rest": "AES-256",
                    "data_in_transit": "TLS_1.3",
                    "key_management": "FIPS_140-2_compliant"
                }
            },
            "content_requirements": {
                "language_support": {
                    "english": True,
                    "french": True,
                    "mandatory_french_content": True
                },
                "cultural_adaptation": {
                    "local_game_themes": True,
                    "canadian_payment_methods": True,
                    "local_support_contact": True
                },
                "advertising_restrictions": {
                    "no_targeting_underage": True,
                    "responsible_gaming_messaging": True,
                    "cross_border_restrictions": True
                }
            }
        }
