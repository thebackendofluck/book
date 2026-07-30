# Companion code for "The Backend of Luck" - Chapter 37, Marketing Technology and CRM Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# REGULATORY REQUIREMENT: GDPR + ePrivacy + UK GDPR + LGPD (consent & marketing)
# Regulation:  GDPR (EU) 2016/679 Art. 6(1)(a) — consent as legal basis for
#              marketing processing; Art. 7 — conditions for consent;
#              Art. 17 — right to withdraw consent and erasure;
#              ePrivacy Directive 2002/58/EC Art. 13 — electronic marketing consent
#              (pending ePrivacy Regulation to replace this; still in force 2026)
#              UK GDPR + PECR (Privacy and Electronic Communications Regulations 2003)
#              LGPD Art. 7(I) + Art. 18 — marketing consent and withdrawal
#              UKGC LCCP SR Code 7.1 — no marketing to self-excluded players;
#              UKGC LCCP OR Code 5.1 — marketing must be socially responsible;
#              UKGC Feb 2025 LCCP changes: bonus wagering caps; cross-product
#              promotion bans; deposit limit prompts effective 31 Oct 2025
# Purpose:     GDPR-compliant consent management for marketing campaigns.
#              7-year audit trail for consent records (Art. 7(1) evidence;
#              UKGC LCCP suggests 7 years; use as standard).
#              CRITICAL: Self-excluded players must NEVER receive marketing.
#              Sending marketing to a self-excluded player = immediate UKGC violation.
#              Cross-referencing consent records against exclusion flags is mandatory.
# Consent Categories:
#   essential:       Cannot be disabled — legitimate interest/contract basis
#   analytics:       Requires consent (Art. 6(1)(a)) if beyond legitimate interest
#   marketing:       Requires explicit opt-in consent (Art. 6(1)(a) + ePrivacy)
#   personalization: Risk-based — document in Legitimate Interest Assessment
# Retention:   Consent records: indefinitely (evidence of lawful basis; Art. 7(1));
#              Marketing campaign logs: 7 years (UKGC LCCP)
# Penalty:     GDPR Art. 83(5): up to €20M or 4% global annual turnover;
#              PECR (UK): up to £500,000 per campaign for unlawful direct marketing;
#              UKGC: regulatory action for marketing to excluded players
# Jurisdictions: All EU/EEA, UK, Brazil, Canada
#
# References:
#   GDPR Full Text: https://gdpr-info.eu/
#   Art. 17 (Right to Erasure): https://gdpr-info.eu/art-17-gdpr/
#   Art. 83 (Penalties): https://gdpr-info.eu/art-83-gdpr/
#   UK GDPR: https://www.legislation.gov.uk/uksi/2019/419/contents
#   UKGC LCCP: https://www.gamblingcommission.gov.uk/licensees-and-businesses/lccp
#   LGPD: https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/L13709.htm
# =============================================================================
"""
Privacy Compliance Manager for iGaming
========================================
Chapter 9: Marketing Technology and CRM Systems

GDPR-compliant data handling providing:
- Consent management with 7-year audit trail
- Data subject request handling (access, portability, deletion, rectification)
- Right to be forgotten with legal retention checks
- Cross-border data transfer validation (adequacy decisions and safeguards)

Dependencies:
    pip install redis
"""

# GDPR-compliant data handling
import redis.asyncio as redis
from typing import Dict, List
from datetime import datetime
import logging

class PrivacyComplianceManager:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.logger = logging.getLogger(__name__)
        self.consent_categories = {
            'essential': True,  # Cannot be disabled
            'analytics': False,
            'marketing': False,
            'personalization': False
        }

    async def handle_consent_update(
        self,
        customer_id: str,
        consent_preferences: Dict[str, bool]
    ):
        """Update customer consent preferences"""
        # Validate consent categories
        validated_consent = {}
        for category, required in self.consent_categories.items():
            if required:
                validated_consent[category] = True
            else:
                validated_consent[category] = consent_preferences.get(category, False)

        # Store consent
        await self.redis.hset(
            f"consent:{customer_id}",
            mapping=validated_consent
        )  # ty:ignore[invalid-await]

        # Set expiration for audit trail
        await self.redis.expire(f"consent:{customer_id}", 86400 * 365 * 7)  # 7 years

        # Apply consent changes
        await self._apply_consent_changes(customer_id, validated_consent)  # ty:ignore[unresolved-attribute]

        # Log consent change for audit
        await self._log_consent_change(customer_id, validated_consent)  # ty:ignore[unresolved-attribute]

    async def process_data_request(
        self,
        customer_id: str,
        request_type: str
    ) -> Dict:
        """Handle GDPR data requests (access, portability, deletion)"""
        if request_type == 'access':
            return await self._generate_data_export(customer_id)  # ty:ignore[unresolved-attribute]

        elif request_type == 'portability':
            return await self._generate_portable_data(customer_id)  # ty:ignore[unresolved-attribute]

        elif request_type == 'deletion':
            return await self._initiate_data_deletion(customer_id)

        elif request_type == 'rectification':
            return await self._handle_data_rectification(customer_id)  # ty:ignore[unresolved-attribute]

        else:
            raise ValueError(f"Unknown request type: {request_type}")

    async def _initiate_data_deletion(self, customer_id: str) -> Dict:
        """Initiate GDPR right to be forgotten"""
        # Check legal retention requirements
        retention_check = await self._check_legal_retention(customer_id)  # ty:ignore[unresolved-attribute]

        if retention_check['must_retain']:
            return {
                'status': 'partial_deletion',
                'reason': retention_check['reason'],
                'retained_data': retention_check['retained_categories'],
                'deleted_data': await self._delete_non_essential_data(customer_id)  # ty:ignore[unresolved-attribute]
            }

        # Full deletion
        deletion_result = await self._full_data_deletion(customer_id)  # ty:ignore[unresolved-attribute]

        return {
            'status': 'complete_deletion',
            'deleted_categories': deletion_result['deleted'],
            'deletion_timestamp': datetime.now().isoformat(),
            'confirmation_id': self._generate_deletion_confirmation_id(customer_id)  # ty:ignore[unresolved-attribute]
        }

    def validate_cross_border_transfer(
        self,
        data: Dict,
        destination_country: str
    ) -> bool:
        """Validate if data transfer is allowed under GDPR"""
        # Check adequacy decision
        adequate_countries = {
            'AD', 'AR', 'CA', 'CL', 'FO', 'GG', 'IL', 'IM', 'IS',
            'JE', 'JP', 'LI', 'MX', 'NO', 'NZ', 'PY', 'CH', 'UY', 'US'
        }

        if destination_country in adequate_countries:
            return True

        # Check for appropriate safeguards
        required_safeguards = [
            'standard_contractual_clauses',
            'binding_corporate_rules',
            'certification_mechanism'
        ]

        return any(safeguard in data.get('safeguards', [])
                  for safeguard in required_safeguards)
