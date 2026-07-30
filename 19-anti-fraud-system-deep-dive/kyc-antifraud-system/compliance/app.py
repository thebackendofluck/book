#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 19, Anti-Fraud System Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Compliance Engine Service
Handles GDPR, AML, sanctions screening, and regulatory compliance
"""

import os
import json
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from enum import Enum
import aiohttp
import redis
import psycopg2
from fastapi import FastAPI, HTTPException
import uvicorn

logger = logging.getLogger(__name__)

class ComplianceCheck(Enum):
    SANCTIONS = "sanctions"
    PEP = "pep"
    AML = "aml"
    GDPR = "gdpr"
    KYC = "kyc"
    FRAUD = "fraud"

class RegulatoryJurisdiction(Enum):
    EU = "european_union"
    US = "united_states"
    UK = "united_kingdom"
    APAC = "asia_pacific"
    LATAM = "latin_america"

class ComplianceEngine:
    def __init__(self):
        self.redis_client = self._init_redis()
        self.db_conn = self._init_database()
        self.sanctions_lists = self._load_sanctions_lists()  # ty:ignore[unresolved-attribute]
        self.pep_database = self._init_pep_database()  # ty:ignore[unresolved-attribute]
        
    def _init_redis(self):
        return redis.Redis(
            host=os.environ.get('REDIS_HOST', 'redis-cache'),
            password=os.environ.get('REDIS_PASSWORD'),
            decode_responses=True
        )
    
    def _init_database(self):
        return psycopg2.connect(
            host=os.environ.get('DB_HOST', 'postgres-db'),
            database='kyc_db',
            user=os.environ.get('DB_USER'),
            password=os.environ.get('DB_PASSWORD'),
            sslmode='require'
        )
    
    async def check_sanctions(self, person_data: Dict) -> Dict[str, Any]:
        """Check against international sanctions lists"""
        
        # Check OFAC (US Treasury)
        ofac_result = await self._check_ofac(person_data)  # ty:ignore[unresolved-attribute]
        
        # Check UN sanctions
        un_result = await self._check_un_sanctions(person_data)  # ty:ignore[unresolved-attribute]
        
        # Check EU consolidated list
        eu_result = await self._check_eu_sanctions(person_data)  # ty:ignore[unresolved-attribute]
        
        # Check UK sanctions
        uk_result = await self._check_uk_sanctions(person_data)  # ty:ignore[unresolved-attribute]
        
        hit = any([ofac_result['hit'], un_result['hit'], 
                   eu_result['hit'], uk_result['hit']])
        
        return {
            'hit': hit,
            'lists_checked': ['OFAC', 'UN', 'EU', 'UK'],
            'results': {
                'ofac': ofac_result,
                'un': un_result,
                'eu': eu_result,
                'uk': uk_result
            },
            'risk_score': 1.0 if hit else 0.0
        }
    
    async def check_pep(self, person_data: Dict) -> Dict[str, Any]:
        """Check if person is Politically Exposed Person"""
        
        # Implementation would connect to PEP database services
        # like World-Check, Dow Jones, or ComplyAdvantage
        
        name = f"{person_data.get('first_name', '')} {person_data.get('last_name', '')}"
        
        # Simulated PEP check
        is_pep = await self._query_pep_database(name)  # ty:ignore[unresolved-attribute]
        
        return {
            'match': is_pep,
            'confidence': 0.95 if is_pep else 0.0,
            'category': 'high_risk' if is_pep else 'standard',
            'positions': [] if not is_pep else ['government_official']
        }
    
    async def perform_aml_checks(self, transaction_data: Dict) -> Dict[str, Any]:
        """Perform Anti-Money Laundering checks"""
        
        checks = {
            'structuring': self._check_structuring(transaction_data),
            'velocity': self._check_velocity(transaction_data),
            'pattern': self._check_unusual_patterns(transaction_data),  # ty:ignore[unresolved-attribute]
            'threshold': self._check_thresholds(transaction_data),  # ty:ignore[unresolved-attribute]
            'geographic': self._check_geographic_risk(transaction_data)  # ty:ignore[unresolved-attribute]
        }
        
        risk_score = sum(1 for check in checks.values() if check) / len(checks)
        
        return {
            'passed': risk_score < 0.3,
            'risk_score': risk_score,
            'checks': checks,
            'requires_sar': risk_score > 0.7
        }
    
    async def generate_sar(self, transaction_data: Dict, reason: str) -> Dict[str, Any]:
        """Generate Suspicious Activity Report"""
        
        sar = {
            'report_id': f"SAR-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",  # ty:ignore[deprecated]
            'filing_date': datetime.utcnow().isoformat(),  # ty:ignore[deprecated]
            'reporting_entity': os.environ.get('CASINO_NAME', 'Casino'),
            'transaction_details': transaction_data,
            'suspicion_reason': reason,
            'narrative': self._generate_sar_narrative(transaction_data, reason)
        }
        
        # Store SAR
        self._store_sar(sar)  # ty:ignore[unresolved-attribute]
        
        # Submit to FinCEN (US) or equivalent
        if os.environ.get('JURISDICTION') == 'US':
            await self._submit_to_fincen(sar)  # ty:ignore[unresolved-attribute]
        
        return sar
    
    async def handle_gdpr_request(self, request_type: str, user_id: str) -> Dict[str, Any]:
        """Handle GDPR data subject requests"""
        
        if request_type == 'access':
            return await self._handle_access_request(user_id)  # ty:ignore[unresolved-attribute]
        elif request_type == 'deletion':
            return await self._handle_deletion_request(user_id)
        elif request_type == 'portability':
            return await self._handle_portability_request(user_id)  # ty:ignore[unresolved-attribute]
        elif request_type == 'rectification':
            return await self._handle_rectification_request(user_id)  # ty:ignore[unresolved-attribute]
        else:
            raise ValueError(f"Unknown GDPR request type: {request_type}")
    
    async def _handle_deletion_request(self, user_id: str) -> Dict[str, Any]:
        """Process GDPR right to be forgotten"""
        
        # Check if we can legally delete
        if self._has_legal_retention_requirement(user_id):  # ty:ignore[unresolved-attribute]
            return {
                'success': False,
                'reason': 'Legal retention requirement',
                'retention_until': self._get_retention_end_date(user_id)  # ty:ignore[unresolved-attribute]
            }
        
        # Delete personal data
        deleted_items = []
        
        # Delete from main database
        with self.db_conn.cursor() as cursor:
            cursor.execute("""
                UPDATE users SET 
                    personal_data = NULL,
                    documents = NULL,
                    biometric_data = NULL,
                    is_deleted = TRUE,
                    deleted_at = %s
                WHERE user_id = %s
            """, (datetime.utcnow(), user_id))  # ty:ignore[deprecated]
            
            deleted_items.append('personal_data')
            deleted_items.append('documents')
            deleted_items.append('biometric_data')
        
        # Clear from cache
        self.redis_client.delete(f"user:{user_id}")
        
        # Schedule document destruction
        await self._schedule_document_destruction(user_id)  # ty:ignore[unresolved-attribute]
        
        return {
            'success': True,
            'deleted_items': deleted_items,
            'completion_time': datetime.utcnow().isoformat()  # ty:ignore[deprecated]
        }
    
    def _check_structuring(self, transaction_data: Dict) -> bool:
        """Check for structuring patterns (smurfing)"""
        
        amount = transaction_data.get('amount', 0)
        
        # Check if just below reporting threshold
        if 9000 <= amount <= 9999:  # Just below $10,000 threshold
            return True
        
        # Check for multiple similar amounts
        recent_transactions = transaction_data.get('recent_transactions', [])
        similar_amounts = [t for t in recent_transactions 
                          if abs(t['amount'] - amount) < 100]
        
        if len(similar_amounts) >= 3:
            return True
        
        return False
    
    def _check_velocity(self, transaction_data: Dict) -> bool:
        """Check transaction velocity"""
        
        recent_transactions = transaction_data.get('recent_transactions', [])
        
        # Check frequency in last 24 hours
        last_24h = [t for t in recent_transactions 
                    if (datetime.utcnow() - t['timestamp']).hours <= 24]  # ty:ignore[deprecated]
        
        if len(last_24h) > 10:  # More than 10 transactions in 24 hours
            return True
        
        # Check rapid succession
        if len(recent_transactions) >= 2:
            time_diff = recent_transactions[0]['timestamp'] - recent_transactions[1]['timestamp']
            if time_diff.seconds < 60:  # Less than 1 minute between transactions
                return True
        
        return False
    
    def _generate_sar_narrative(self, transaction_data: Dict, reason: str) -> str:
        """Generate narrative for SAR"""
        
        narrative = f"""
        Suspicious Activity Report Narrative
        
        Date: {datetime.utcnow().strftime('%Y-%m-%d')}
        Account: {transaction_data.get('account_id')}
        Amount: ${transaction_data.get('amount', 0):,.2f}
        
        Reason for Suspicion:
        {reason}
        
        Transaction Pattern Analysis:
        The customer has conducted {len(transaction_data.get('recent_transactions', []))} 
        transactions in the past 30 days with a total volume of 
        ${sum(t['amount'] for t in transaction_data.get('recent_transactions', [])):,.2f}.
        
        Additional Information:
        - IP Address: {transaction_data.get('ip_address')}
        - Device ID: {transaction_data.get('device_id')}
        - Geographic Location: {transaction_data.get('location')}
        
        This activity has been flagged for review due to patterns consistent with 
        potential money laundering or other illicit activity.
        """  # ty:ignore[deprecated]
        
        return narrative.strip()

# Create FastAPI app
app = FastAPI(title="Compliance Engine API")
compliance_engine = ComplianceEngine()

@app.post("/check/sanctions")
async def check_sanctions(person_data: Dict[str, Any]):
    result = await compliance_engine.check_sanctions(person_data)
    return result

@app.post("/check/pep")
async def check_pep(person_data: Dict[str, Any]):
    result = await compliance_engine.check_pep(person_data)
    return result

@app.post("/check/aml")
async def check_aml(transaction_data: Dict[str, Any]):
    result = await compliance_engine.perform_aml_checks(transaction_data)
    return result

@app.post("/gdpr/{request_type}")
async def handle_gdpr(request_type: str, user_id: str):
    result = await compliance_engine.handle_gdpr_request(request_type, user_id)
    return result

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
