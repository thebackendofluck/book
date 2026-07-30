# Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Jurisdiction-Aware Data Router - Chapter 24: Data Residency and Backup/Recovery Strategy

Routes data requests according to jurisdiction-specific data residency requirements,
enforcing local-only storage, cross-border transfer rules, and compliance logging.

Part of the iGaming Platform Engineering book.
"""

import asyncio
import redis.asyncio as redis
from typing import Any, Dict, Optional, List
from dataclasses import dataclass
from datetime import datetime
import json


@dataclass
class JurisdictionConfig:
    code: str
    name: str
    data_residency: str
    backup_allowed: bool
    encryption_required: bool
    retention_years: int
    allowed_transfers: List[str]


@dataclass
class DataRequest:
    user_id: str
    data_type: str
    jurisdiction: str
    operation: str
    payload: Dict


class JurisdictionAwareDataRouter:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.jurisdictions = self._load_jurisdiction_configs()
        self.data_stores = self._initialize_data_stores()

    def _load_jurisdiction_configs(self) -> Dict[str, JurisdictionConfig]:
        return {
            'US_NJ': JurisdictionConfig(
                code='US_NJ',
                name='New Jersey',
                data_residency='strict_local_only',
                backup_allowed=False,  # In-state only
                encryption_required=True,
                retention_years=7,
                allowed_transfers=['regulatory_audit', 'legal_subpoena']
            ),
            'EU': JurisdictionConfig(
                code='EU',
                name='European Union',
                data_residency='eu_only',
                backup_allowed=True,
                encryption_required=True,
                retention_years=5,
                allowed_transfers=['adequacy_countries', 'sccs']
            ),
            'UK': JurisdictionConfig(
                code='UK',
                name='United Kingdom',
                data_residency='uk_eu_gdpr',
                backup_allowed=True,
                encryption_required=True,
                retention_years=6,
                allowed_transfers=['eu_countries', 'trusted_third_parties']
            )
        }

    async def route_data_request(self, request: DataRequest) -> Dict | list:
        """Route data request based on jurisdiction requirements"""
        jurisdiction = self.jurisdictions.get(request.jurisdiction)
        if not jurisdiction:
            raise ValueError(f"Unknown jurisdiction: {request.jurisdiction}")

        # Validate operation against jurisdiction rules
        await self._validate_operation(request, jurisdiction)

        # Route to appropriate data store
        data_store = self._get_data_store_for_jurisdiction(request.jurisdiction)

        # Execute operation with jurisdiction-specific logic
        if request.operation == 'read':
            result = await self._execute_read(request, data_store, jurisdiction)
        elif request.operation == 'write':
            result = await self._execute_write(request, data_store, jurisdiction)
        elif request.operation == 'backup':
            result = await self._execute_backup(request, data_store, jurisdiction)
        else:
            raise ValueError(f"Unsupported operation: {request.operation}")

        # Log for compliance
        await self._log_compliance_event(request, result)

        return result

    async def _validate_operation(self, request: DataRequest, jurisdiction: JurisdictionConfig):
        """Validate operation against jurisdiction rules"""
        if request.operation == 'transfer' and request.data_type == 'player_data':
            if 'marketing' in request.payload.get('purpose', []):
                if jurisdiction.data_residency == 'strict_local_only':
                    raise PermissionError("Marketing data transfer not allowed in strict local jurisdiction")

        # Additional validation logic...

    def _get_data_store_for_jurisdiction(self, jurisdiction_code: str):
        """Get appropriate data store for jurisdiction"""
        return self.data_stores.get(jurisdiction_code, self.data_stores['default'])

    async def _execute_read(self, request: DataRequest, data_store, jurisdiction: JurisdictionConfig) -> Dict | list:
        """Execute read operation with jurisdiction compliance"""
        # Add jurisdiction-specific filtering
        query = self._add_jurisdiction_filters(request, jurisdiction)

        # Execute query
        result = await data_store.read(query)

        # Apply data residency checks
        if jurisdiction.data_residency == 'strict_local_only':
            result = self._filter_local_data_only(result, jurisdiction)

        return result

    async def _execute_write(self, request: DataRequest, data_store, jurisdiction: JurisdictionConfig) -> Dict:
        """Execute write operation with jurisdiction compliance"""
        # Validate data against jurisdiction requirements
        validated_data = await self._validate_data_payload(request.payload, jurisdiction)

        # Add jurisdiction metadata
        enriched_data = self._enrich_with_jurisdiction_metadata(validated_data, jurisdiction)

        # Execute write
        result = await data_store.write(enriched_data)

        # Trigger compliance logging
        await self._log_data_modification(request, result, jurisdiction)

        return result

    async def _execute_backup(self, request: DataRequest, data_store, jurisdiction: JurisdictionConfig) -> Dict:
        """Execute backup operation with jurisdiction compliance"""
        if not jurisdiction.backup_allowed:
            # For strict jurisdictions, implement local-only backup
            return await self._execute_local_backup(request, data_store, jurisdiction)
        else:
            # For flexible jurisdictions, allow cross-border backup
            return await self._execute_cross_border_backup(request, data_store, jurisdiction)

    async def _execute_local_backup(self, request: DataRequest, data_store, jurisdiction: JurisdictionConfig) -> Dict:
        """Execute backup within jurisdiction boundaries"""
        backup_config = {
            'location': f"{jurisdiction.code}_local_backup",
            'encryption': 'aes256',
            'retention': f"{jurisdiction.retention_years}y",
            'compliance_flags': ['local_only', 'encrypted_at_rest']
        }

        return await data_store.backup(backup_config)

    async def _execute_cross_border_backup(self, request: DataRequest, data_store, jurisdiction: JurisdictionConfig) -> Dict:
        """Execute backup with cross-border capabilities"""
        backup_config = {
            'primary_location': f"{jurisdiction.code}_local_backup",
            'secondary_location': f"{jurisdiction.code}_remote_backup",
            'encryption': 'aes256',
            'retention': f"{jurisdiction.retention_years}y",
            'compliance_flags': ['encrypted_transit', 'access_logging']
        }

        return await data_store.backup(backup_config)

    def _add_jurisdiction_filters(self, request: DataRequest, jurisdiction: JurisdictionConfig) -> Dict:
        """Add jurisdiction-specific filters to query"""
        base_query = request.payload.copy()

        if jurisdiction.data_residency == 'strict_local_only':
            base_query['jurisdiction_filter'] = jurisdiction.code

        return base_query

    def _filter_local_data_only(self, data: Dict | list, jurisdiction: JurisdictionConfig) -> Dict | list:
        """Filter data to include only local jurisdiction data"""
        if isinstance(data, list):
            return [item for item in data if item.get('jurisdiction') == jurisdiction.code]
        elif isinstance(data, dict):
            return data if data.get('jurisdiction') == jurisdiction.code else {}
        return data

    async def _validate_data_payload(self, payload: Dict, jurisdiction: JurisdictionConfig) -> Dict:
        """Validate data payload against jurisdiction requirements"""
        # Implement validation logic based on jurisdiction rules
        if jurisdiction.encryption_required:
            if not payload.get('encrypted', False):
                raise ValueError("Data must be encrypted for this jurisdiction")

        # Additional validation...

        return payload

    def _enrich_with_jurisdiction_metadata(self, data: Dict, jurisdiction: JurisdictionConfig) -> Dict[str, Any]:
        """Add jurisdiction metadata to data"""
        return {
            **data,
            '_jurisdiction': jurisdiction.code,
            '_residency_class': jurisdiction.data_residency,
            '_retention_years': jurisdiction.retention_years,
            '_last_modified': datetime.utcnow().isoformat(),  # ty:ignore[deprecated]
            '_compliance_flags': jurisdiction.allowed_transfers
        }

    async def _log_compliance_event(self, request: DataRequest, result: Dict | list):
        """Log compliance event for audit trail"""
        compliance_log = {
            'timestamp': datetime.utcnow().isoformat(),  # ty:ignore[deprecated]
            'user_id': request.user_id,
            'jurisdiction': request.jurisdiction,
            'operation': request.operation,
            'data_type': request.data_type,
            'result_status': result.get('status', 'unknown') if isinstance(result, dict) else 'success',
            'compliance_check': 'passed'
        }

        await self.redis.lpush('compliance_audit_log', json.dumps(compliance_log))  # ty:ignore[invalid-await]

    async def _log_data_modification(self, request: DataRequest, result: Dict, jurisdiction: JurisdictionConfig):
        """Log data modification for compliance"""
        modification_log = {
            'timestamp': datetime.utcnow().isoformat(),  # ty:ignore[deprecated]
            'jurisdiction': jurisdiction.code,
            'operation': request.operation,
            'data_type': request.data_type,
            'user_id': request.user_id,
            'compliance_flags': jurisdiction.allowed_transfers
        }

        await self.redis.lpush(f'compliance_modification_log_{jurisdiction.code}', json.dumps(modification_log))  # ty:ignore[invalid-await]

    def _initialize_data_stores(self) -> dict:
        """Initialize data stores for each jurisdiction"""
        return {}
