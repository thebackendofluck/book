# Companion code for "The Backend of Luck" - Chapter 26, Responsible Gaming and Player Protection Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Self-Exclusion Management System for iGaming
=============================================
Chapter 10: Responsible Gaming and Player Protection

Comprehensive self-exclusion system with multi-jurisdiction support providing:
- Self-exclusion initiation with cooling-off periods per jurisdiction
- UK (GAMSTOP), Sweden (ROFUS), and Ontario (iGaming Ontario) integration
- Revocation validation with jurisdiction-specific cooling-off periods
- Exclusion analytics with type, jurisdiction, and revocation breakdowns

Jurisdiction Configurations:
    UK:      6-day minimum, 24h cooling-off, GAMSTOP integration
    Sweden:  1-day minimum, immediate, ROFUS integration, no revocation
    Ontario: 6-day minimum, 24h cooling-off, iGaming Ontario integration

Dependencies:
    pip install redis asyncpg pytz aiohttp
"""

# Comprehensive self-exclusion system with multi-jurisdiction support
from enum import Enum
from datetime import datetime, timedelta
import pytz
from typing import Dict, List, Optional, Tuple
import asyncio
import hashlib
import json
import uuid
import logging
import aiohttp
import redis.asyncio as redis
import asyncpg  # ty:ignore[unresolved-import]
from dataclasses import dataclass

class ExclusionType(Enum):
    TEMPORARY = "temporary"
    PERMANENT = "permanent"
    COOLING_OFF = "cooling_off"
    TIME_OUT = "time_out"

class ExclusionScope(Enum):
    ACCOUNT_ONLY = "account_only"
    BRAND_WIDE = "brand_wide"
    GROUP_WIDE = "group_wide"
    NATIONAL = "national"

class ExclusionStatus(Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    PENDING = "pending"

@dataclass
class SelfExclusionRequest:
    request_id: str
    customer_id: str
    exclusion_type: ExclusionType
    scope: ExclusionScope
    duration_days: Optional[int]
    reason: str
    requested_date: datetime
    effective_date: datetime
    expiry_date: Optional[datetime]
    status: ExclusionStatus
    jurisdiction: str
    ip_address: str
    device_fingerprint: str
    cooling_off_period_hours: int = 24

class SelfExclusionManager:
    def __init__(self, redis_client: redis.Redis, db_pool: asyncpg.Pool):
        self.redis = redis_client
        self.db_pool = db_pool
        self.logger = logging.getLogger(__name__)

        # Jurisdiction-specific configurations
        self.jurisdiction_configs = {
            'UK': {
                'min_exclusion_days': 6,
                'max_exclusion_days': 5*365,
                'cooling_off_hours': 24,
                'national_register': 'GAMSTOP',
                'revocation_allowed': True,
                'revocation_cooling_off_hours': 24*7
            },
            'Sweden': {
                'min_exclusion_days': 1,
                'max_exclusion_days': 365,
                'cooling_off_hours': 0,
                'national_register': 'ROFUS',
                'revocation_allowed': False,
                'revocation_cooling_off_hours': 0
            },
            'Ontario': {
                'min_exclusion_days': 6,
                'max_exclusion_days': 5*365,
                'cooling_off_hours': 24,
                'national_register': 'iGaming Ontario',
                'revocation_allowed': True,
                'revocation_cooling_off_hours': 24*7
            }
        }

    async def initiate_self_exclusion(
        self,
        customer_id: str,
        exclusion_request: Dict
    ) -> Dict:
        """Initiate self-exclusion process with validation"""
        try:
            jurisdiction = exclusion_request['jurisdiction']
            config = self.jurisdiction_configs.get(jurisdiction)

            if not config:
                raise ValueError(f"Unsupported jurisdiction: {jurisdiction}")

            # Validate exclusion parameters
            validation = await self._validate_exclusion_request(
                customer_id,
                exclusion_request,
                config
            )

            if not validation['valid']:
                return {
                    'success': False,
                    'errors': validation['errors']
                }

            # Create exclusion request
            exclusion = SelfExclusionRequest(
                request_id=f"SE_{uuid.uuid4().hex[:12]}",
                customer_id=customer_id,
                exclusion_type=ExclusionType(exclusion_request['exclusion_type']),
                scope=ExclusionScope(exclusion_request['scope']),
                duration_days=exclusion_request.get('duration_days'),
                reason=exclusion_request['reason'],
                requested_date=datetime.now(pytz.UTC),  # ty:ignore[invalid-argument-type]
                effective_date=datetime.now(pytz.UTC) + timedelta(hours=config['cooling_off_hours']),  # ty:ignore[invalid-argument-type]
                expiry_date=self._calculate_expiry_date(  # ty:ignore[unresolved-attribute]
                    exclusion_request.get('duration_days'),
                    exclusion_request['exclusion_type']
                ),
                status=ExclusionStatus.PENDING,
                jurisdiction=jurisdiction,
                ip_address=exclusion_request['ip_address'],
                device_fingerprint=exclusion_request['device_fingerprint'],
                cooling_off_period_hours=config['cooling_off_hours']  # ty:ignore[invalid-argument-type]
            )

            # Store exclusion request
            await self._store_exclusion_request(exclusion)  # ty:ignore[unresolved-attribute]

            # Apply immediate restrictions
            await self._apply_immediate_restrictions(customer_id, exclusion)

            # Schedule cooling-off period end
            if config['cooling_off_hours'] > 0:  # ty:ignore[unsupported-operator]
                await self._schedule_cooling_off_end(exclusion)  # ty:ignore[unresolved-attribute]

            # Register with national database if applicable
            if exclusion.scope == ExclusionScope.NATIONAL:
                await self._register_national_exclusion(exclusion)

            # Send confirmation to customer
            await self._send_exclusion_confirmation(customer_id, exclusion)  # ty:ignore[unresolved-attribute]

            # Notify responsible gaming team
            await self._notify_responsible_gaming_team(exclusion)  # ty:ignore[unresolved-attribute]

            return {
                'success': True,
                'exclusion_id': exclusion.request_id,
                'effective_date': exclusion.effective_date.isoformat(),
                'cooling_off_end': (exclusion.effective_date - timedelta(hours=config['cooling_off_hours'])).isoformat(),  # ty:ignore[invalid-argument-type]
                'message': "Self-exclusion request processed successfully"
            }

        except Exception as e:
            self.logger.error(f"Self-exclusion initiation failed: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    async def _validate_exclusion_request(
        self,
        customer_id: str,
        request: Dict,
        config: Dict
    ) -> Dict:
        """Validate self-exclusion request parameters"""
        errors = []

        # Check if customer has existing exclusions
        existing_exclusions = await self._get_active_exclusions(customer_id)  # ty:ignore[unresolved-attribute]
        if existing_exclusions:
            errors.append("Customer already has active self-exclusion")

        # Validate exclusion type
        try:
            exclusion_type = ExclusionType(request['exclusion_type'])
        except ValueError:
            errors.append("Invalid exclusion type")

        # Validate duration for temporary exclusions
        if request['exclusion_type'] == 'temporary':
            duration = request.get('duration_days')
            if not duration:
                errors.append("Duration required for temporary exclusion")
            elif duration < config['min_exclusion_days']:
                errors.append(f"Minimum exclusion period is {config['min_exclusion_days']} days")
            elif duration > config['max_exclusion_days']:
                errors.append(f"Maximum exclusion period is {config['max_exclusion_days']} days")

        # Validate scope
        try:
            scope = ExclusionScope(request['scope'])
        except ValueError:
            errors.append("Invalid exclusion scope")

        # Check reason provided
        if not request.get('reason') or len(request['reason'].strip()) < 10:
            errors.append("Please provide a detailed reason for self-exclusion")

        # Validate jurisdiction
        if request['jurisdiction'] not in self.jurisdiction_configs:
            errors.append("Unsupported jurisdiction")

        return {
            'valid': len(errors) == 0,
            'errors': errors
        }

    async def check_exclusion_status(
        self,
        customer_id: str,
        jurisdiction: Optional[str] = None
    ) -> Dict:
        """Check if customer is currently excluded"""
        exclusions = await self._get_active_exclusions(customer_id, jurisdiction)  # ty:ignore[unresolved-attribute]

        if not exclusions:
            return {
                'is_excluded': False,
                'exclusions': []
            }

        # Check if any exclusion is currently effective
        now = datetime.now(pytz.UTC)  # ty:ignore[invalid-argument-type]
        active_exclusions = []

        for exclusion in exclusions:
            if exclusion.effective_date <= now <= (exclusion.expiry_date or datetime.max.replace(tzinfo=pytz.UTC)):  # ty:ignore[invalid-argument-type]
                active_exclusions.append(exclusion)

        return {
            'is_excluded': len(active_exclusions) > 0,
            'exclusions': [self._exclusion_to_dict(ex) for ex in active_exclusions],  # ty:ignore[unresolved-attribute]
            'restrictions': await self._get_exclusion_restrictions(active_exclusions)  # ty:ignore[unresolved-attribute]
        }

    async def _apply_immediate_restrictions(
        self,
        customer_id: str,
        exclusion: SelfExclusionRequest
    ):
        """Apply immediate account restrictions"""
        restrictions = []

        # Account-level restrictions
        if exclusion.scope in [ExclusionScope.ACCOUNT_ONLY, ExclusionScope.BRAND_WIDE, ExclusionScope.GROUP_WIDE]:
            restrictions.extend([
                'deposit_blocked',
                'betting_blocked',
                'bonus_blocked',
                'withdrawal_allowed'  # Allow withdrawals during cooling-off
            ])

        # Marketing restrictions
        restrictions.extend([
            'marketing_emails_blocked',
            'sms_marketing_blocked',
            'push_notifications_blocked',
            'promotional_calls_blocked'
        ])

        # Apply restrictions
        for restriction in restrictions:
            await self.redis.sadd(f"exclusion_restrictions:{customer_id}", restriction)  # ty:ignore[invalid-await]

        # Set expiration for cooling-off period
        cooling_off_end = exclusion.effective_date - timedelta(hours=exclusion.cooling_off_period_hours)
        if cooling_off_end > datetime.now(pytz.UTC):  # ty:ignore[invalid-argument-type]
            ttl = int((cooling_off_end - datetime.now(pytz.UTC)).total_seconds())  # ty:ignore[invalid-argument-type]
            await self.redis.expire(f"exclusion_restrictions:{customer_id}", ttl)

        # Log restrictions applied
        await self._log_restrictions_applied(customer_id, exclusion.request_id, restrictions)  # ty:ignore[unresolved-attribute]

    async def revoke_self_exclusion(
        self,
        customer_id: str,
        exclusion_id: str,
        reason: str
    ) -> Dict:
        """Revoke self-exclusion if allowed by jurisdiction"""
        try:
            # Get exclusion details
            exclusion = await self._get_exclusion(exclusion_id)  # ty:ignore[unresolved-attribute]

            if not exclusion:
                return {
                    'success': False,
                    'error': 'Exclusion not found'
                }

            if exclusion.customer_id != customer_id:
                return {
                    'success': False,
                    'error': 'Unauthorized'
                }

            # Check if revocation is allowed
            config = self.jurisdiction_configs.get(exclusion.jurisdiction)
            if not config:
                return {
                    'success': False,
                    'error': f'Unsupported jurisdiction: {exclusion.jurisdiction}'
                }
            if not config.get('revocation_allowed', False):
                return {
                    'success': False,
                    'error': 'Self-exclusion cannot be revoked in this jurisdiction'
                }

            # Check if cooling-off period has passed
            if exclusion.effective_date + timedelta(hours=config['revocation_cooling_off_hours']) > datetime.now(pytz.UTC):  # ty:ignore[invalid-argument-type]
                remaining_hours = int(((exclusion.effective_date + timedelta(hours=config['revocation_cooling_off_hours'])) - datetime.now(pytz.UTC)).total_seconds() / 3600)  # ty:ignore[invalid-argument-type]
                return {
                    'success': False,
                    'error': f'Revocation cooling-off period not complete. Please wait {remaining_hours} more hours.',
                    'can_revoke_after': (exclusion.effective_date + timedelta(hours=config['revocation_cooling_off_hours'])).isoformat()  # ty:ignore[invalid-argument-type]
                }

            # Process revocation
            exclusion.status = ExclusionStatus.REVOKED
            exclusion.revocation_date = datetime.now(pytz.UTC)  # ty:ignore[invalid-argument-type]
            exclusion.revocation_reason = reason

            # Update exclusion record
            await self._update_exclusion_record(exclusion)  # ty:ignore[unresolved-attribute]

            # Remove restrictions
            await self._remove_exclusion_restrictions(customer_id, exclusion_id)  # ty:ignore[unresolved-attribute]

            # Log revocation
            await self._log_revocation(customer_id, exclusion_id, reason)  # ty:ignore[unresolved-attribute]

            # Send confirmation
            await self._send_revocation_confirmation(customer_id, exclusion)  # ty:ignore[unresolved-attribute]

            return {
                'success': True,
                'message': 'Self-exclusion revoked successfully',
                'effective_date': datetime.now(pytz.UTC).isoformat()  # ty:ignore[invalid-argument-type]
            }

        except Exception as e:
            self.logger.error(f"Self-exclusion revocation failed: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    async def _register_national_exclusion(self, exclusion: SelfExclusionRequest):
        """Register exclusion with national database"""
        jurisdiction = exclusion.jurisdiction
        config = self.jurisdiction_configs[jurisdiction]

        national_register = config.get('national_register')
        if not national_register:
            return

        # Prepare registration data
        registration_data = {
            'customer_id': exclusion.customer_id,
            'exclusion_type': exclusion.exclusion_type.value,
            'effective_date': exclusion.effective_date.isoformat(),
            'expiry_date': exclusion.expiry_date.isoformat() if exclusion.expiry_date else None,
            'personal_data': {
                'first_name': await self._get_customer_first_name(exclusion.customer_id),  # ty:ignore[unresolved-attribute]
                'last_name': await self._get_customer_last_name(exclusion.customer_id),  # ty:ignore[unresolved-attribute]
                'date_of_birth': await self._get_customer_dob(exclusion.customer_id),  # ty:ignore[unresolved-attribute]
                'address': await self._get_customer_address(exclusion.customer_id)  # ty:ignore[unresolved-attribute]
            },
            'registration_metadata': {
                'operator_id': self.config['operator_id'],  # ty:ignore[unresolved-attribute]
                'registration_timestamp': datetime.now(pytz.UTC).isoformat(),  # ty:ignore[invalid-argument-type]
                'source': 'operator_direct'
            }
        }

        # Register based on jurisdiction
        if national_register == 'GAMSTOP':
            await self._register_gamstop(registration_data)
        elif national_register == 'ROFUS':
            await self._register_rofus(registration_data)  # ty:ignore[unresolved-attribute]
        elif national_register == 'iGaming Ontario':
            await self._register_igaming_ontario(registration_data)  # ty:ignore[unresolved-attribute]

    async def _register_gamstop(self, data: Dict):
        """Register with GAMSTOP (UK national self-exclusion scheme)"""
        # GAMSTOP API integration
        gamstop_api_url = self.config['gamstop_api_url']  # ty:ignore[unresolved-attribute]
        api_key = self.config['gamstop_api_key']  # ty:ignore[unresolved-attribute]

        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
            'X-Operator-ID': self.config['operator_id']  # ty:ignore[unresolved-attribute]
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{gamstop_api_url}/v1/exclusions",
                headers=headers,
                json=data
            ) as response:
                if response.status == 201:
                    result = await response.json()
                    self.logger.info(f"Successfully registered with GAMSTOP: {result['registration_id']}")
                else:
                    error_text = await response.text()
                    self.logger.error(f"GAMSTOP registration failed: {error_text}")
                    raise Exception(f"GAMSTOP registration failed: {error_text}")

    async def get_exclusion_analytics(self, date_range: Tuple[datetime, datetime]) -> Dict:
        """Get comprehensive exclusion analytics"""
        start_date, end_date = date_range

        async with self.db_pool.acquire() as conn:
            # Basic metrics
            total_exclusions = await conn.fetchval("""
                SELECT COUNT(*) FROM self_exclusions
                WHERE requested_date BETWEEN $1 AND $2
            """, start_date, end_date)

            active_exclusions = await conn.fetchval("""
                SELECT COUNT(*) FROM self_exclusions
                WHERE status = 'active'
                AND effective_date <= NOW()
                AND (expiry_date IS NULL OR expiry_date > NOW())
            """)

            # Breakdown by type
            type_breakdown = await conn.fetch("""
                SELECT exclusion_type, COUNT(*) as count
                FROM self_exclusions
                WHERE requested_date BETWEEN $1 AND $2
                GROUP BY exclusion_type
            """, start_date, end_date)

            # Breakdown by jurisdiction
            jurisdiction_breakdown = await conn.fetch("""
                SELECT jurisdiction, COUNT(*) as count
                FROM self_exclusions
                WHERE requested_date BETWEEN $1 AND $2
                GROUP BY jurisdiction
            """, start_date, end_date)

            # Revocation statistics
            revocation_stats = await conn.fetchrow("""
                SELECT
                    COUNT(*) as total_revocations,
                    AVG(EXTRACT(EPOCH FROM (revocation_date - effective_date))/86400) as avg_duration_days
                FROM self_exclusions
                WHERE status = 'revoked'
                AND revocation_date BETWEEN $1 AND $2
            """, start_date, end_date)

            return {
                'period': {
                    'start': start_date.isoformat(),
                    'end': end_date.isoformat()
                },
                'total_exclusions_requested': total_exclusions,
                'currently_active_exclusions': active_exclusions,
                'breakdown_by_type': {row['exclusion_type']: row['count'] for row in type_breakdown},
                'breakdown_by_jurisdiction': {row['jurisdiction']: row['count'] for row in jurisdiction_breakdown},
                'revocation_statistics': dict(revocation_stats) if revocation_stats else {},
                'trends': await self._calculate_exclusion_trends(date_range)  # ty:ignore[unresolved-attribute]
            }
