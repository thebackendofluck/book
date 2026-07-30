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
Regulatory Compliance Engine

This module implements comprehensive regulatory compliance features including
GDPR, CCPA, PCI DSS, AML/KYC, and gaming commission requirements.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Set
import structlog

import redis.asyncio as redis
from pydantic import BaseModel, Field
import pandas as pd

from ..data_ingestion.metrics import MetricsCollector  # ty:ignore[unresolved-import]

logger = structlog.get_logger(__name__)

# Initialize metrics collector
metrics_collector = MetricsCollector()


class ComplianceRule(BaseModel):
    """Compliance rule definition"""

    rule_id: str
    name: str
    description: str
    regulation: str  # GDPR, CCPA, PCI_DSS, AML, etc.
    category: str  # data_privacy, security, reporting, etc.
    severity: str  # critical, high, medium, low
    check_type: str  # automated, manual, periodic
    frequency: str  # realtime, hourly, daily, weekly, monthly
    enabled: bool = True


class ComplianceCheck(BaseModel):
    """Compliance check result"""

    check_id: str
    rule_id: str
    timestamp: str
    status: str  # pass, fail, warning, error
    details: Dict[str, Any]
    remediation_actions: List[str] = []
    evidence: Optional[Dict[str, Any]] = None


class DataSubjectRequest(BaseModel):
    """GDPR data subject request"""

    request_id: str
    subject_id: str
    request_type: str  # access, rectification, erasure, restriction, portability, objection
    status: str  # pending, processing, completed, rejected
    created_at: str
    completed_at: Optional[str] = None
    data_scope: Dict[str, Any] = {}
    requester_info: Dict[str, Any] = {}


class AuditLogEntry(BaseModel):
    """Audit log entry for compliance"""

    entry_id: str
    timestamp: str
    user_id: Optional[str]
    action: str
    resource: str
    resource_id: Optional[str]
    details: Dict[str, Any]
    ip_address: Optional[str]
    user_agent: Optional[str]
    compliance_tags: List[str] = []


class ComplianceEngine:
    """Main compliance engine"""

    def __init__(self):
        self.redis_client: Optional[redis.Redis] = None
        self.rules: Dict[str, ComplianceRule] = {}
        self.active_checks: Set[str] = set()

    async def initialize(self):
        """Initialize compliance engine"""
        self.redis_client = redis.Redis(host="redis", port=6379, decode_responses=True)
        await self.load_compliance_rules()
        logger.info("Compliance engine initialized")

    async def load_compliance_rules(self):
        """Load default compliance rules"""

        default_rules = [
            # GDPR Rules
            ComplianceRule(
                rule_id="gdpr_data_retention",
                name="Data Retention Limits",
                description="Ensure data is not retained beyond legal limits",
                regulation="GDPR",
                category="data_privacy",
                severity="high",
                check_type="automated",
                frequency="daily"
            ),
            ComplianceRule(
                rule_id="gdpr_consent_management",
                name="Consent Management",
                description="Verify user consents are properly managed",
                regulation="GDPR",
                category="data_privacy",
                severity="critical",
                check_type="automated",
                frequency="realtime"
            ),
            ComplianceRule(
                rule_id="gdpr_data_minimization",
                name="Data Minimization",
                description="Ensure only necessary data is collected and processed",
                regulation="GDPR",
                category="data_privacy",
                severity="medium",
                check_type="manual",
                frequency="monthly"
            ),

            # PCI DSS Rules
            ComplianceRule(
                rule_id="pci_dss_encryption",
                name="Data Encryption",
                description="Verify sensitive payment data is properly encrypted",
                regulation="PCI_DSS",
                category="security",
                severity="critical",
                check_type="automated",
                frequency="realtime"
            ),
            ComplianceRule(
                rule_id="pci_dss_access_control",
                name="Access Control",
                description="Verify access controls for cardholder data",
                regulation="PCI_DSS",
                category="security",
                severity="high",
                check_type="automated",
                frequency="hourly"
            ),

            # AML/KYC Rules
            ComplianceRule(
                rule_id="aml_sanctions_screening",
                name="Sanctions Screening",
                description="Screen against sanctions lists",
                regulation="AML",
                category="compliance",
                severity="critical",
                check_type="automated",
                frequency="realtime"
            ),
            ComplianceRule(
                rule_id="kyc_verification",
                name="KYC Verification",
                description="Verify customer identity and risk profile",
                regulation="KYC",
                category="compliance",
                severity="high",
                check_type="automated",
                frequency="realtime"
            ),

            # CCPA Rules
            ComplianceRule(
                rule_id="ccpa_data_sales",
                name="Data Sales Opt-out",
                description="Honor CCPA data sales opt-out requests",
                regulation="CCPA",
                category="data_privacy",
                severity="high",
                check_type="automated",
                frequency="realtime"
            ),

            # Gaming Commission Rules
            ComplianceRule(
                rule_id="gaming_responsible_gambling",
                name="Responsible Gambling",
                description="Monitor for responsible gambling compliance",
                regulation="GAMING",
                category="compliance",
                severity="medium",
                check_type="automated",
                frequency="daily"
            )
        ]

        for rule in default_rules:
            self.rules[rule.rule_id] = rule
            await self.store_compliance_rule(rule)

        logger.info(f"Loaded {len(default_rules)} compliance rules")

    async def run_compliance_check(self, rule_id: str, context: Optional[Dict[str, Any]] = None) -> ComplianceCheck:
        """Run a specific compliance check"""

        if rule_id not in self.rules:
            raise ValueError(f"Compliance rule '{rule_id}' not found")

        rule = self.rules[rule_id]
        check_id = f"check_{rule_id}_{int(datetime.now(timezone.utc).timestamp())}"

        # Prevent duplicate checks
        if check_id in self.active_checks:
            return ComplianceCheck(
                check_id=check_id,
                rule_id=rule_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                status="duplicate",
                details={"message": "Check already in progress"}
            )

        self.active_checks.add(check_id)

        try:
            # Execute the check based on rule type
            if rule.regulation == "GDPR":
                result = await self._run_gdpr_check(rule, context or {})
            elif rule.regulation == "PCI_DSS":
                result = await self._run_pci_dss_check(rule, context or {})
            elif rule.regulation == "AML":
                result = await self._run_aml_check(rule, context or {})
            elif rule.regulation == "KYC":
                result = await self._run_kyc_check(rule, context or {})
            elif rule.regulation == "CCPA":
                result = await self._run_ccpa_check(rule, context or {})
            elif rule.regulation == "GAMING":
                result = await self._run_gaming_check(rule, context or {})
            else:
                result = {
                    "status": "error",
                    "details": {"error": f"Unknown regulation: {rule.regulation}"},
                    "remediation_actions": ["Review rule configuration"]
                }

            check = ComplianceCheck(
                check_id=check_id,
                rule_id=rule_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                status=result["status"],  # ty:ignore[invalid-argument-type]
                details=result["details"],  # ty:ignore[invalid-argument-type]
                remediation_actions=result.get("remediation_actions", []),  # ty:ignore[invalid-argument-type]
                evidence=result.get("evidence")  # ty:ignore[invalid-argument-type]
            )

            # Store check result
            await self.store_compliance_check(check)

            # Update metrics
            metrics_collector.increment_counter(
                "compliance_checks_total",
                {"regulation": rule.regulation, "status": check.status}
            )

            return check

        finally:
            self.active_checks.discard(check_id)

    async def _run_gdpr_check(self, rule: ComplianceRule, context: Dict[str, Any]) -> Dict[str, Any]:
        """Run GDPR compliance check"""

        if rule.rule_id == "gdpr_data_retention":
            return await self._check_data_retention(context)
        elif rule.rule_id == "gdpr_consent_management":
            return await self._check_consent_management(context)
        elif rule.rule_id == "gdpr_data_minimization":
            return await self._check_data_minimization(context)

        return {"status": "error", "details": {"error": "Unknown GDPR rule"}}

    async def _run_pci_dss_check(self, rule: ComplianceRule, context: Dict[str, Any]) -> Dict[str, Any]:
        """Run PCI DSS compliance check"""

        if rule.rule_id == "pci_dss_encryption":
            return await self._check_data_encryption(context)
        elif rule.rule_id == "pci_dss_access_control":
            return await self._check_access_control(context)

        return {"status": "error", "details": {"error": "Unknown PCI DSS rule"}}

    async def _run_aml_check(self, rule: ComplianceRule, context: Dict[str, Any]) -> Dict[str, Any]:
        """Run AML compliance check"""

        if rule.rule_id == "aml_sanctions_screening":
            return await self._check_sanctions_screening(context)

        return {"status": "error", "details": {"error": "Unknown AML rule"}}

    async def _run_kyc_check(self, rule: ComplianceRule, context: Dict[str, Any]) -> Dict[str, Any]:
        """Run KYC compliance check"""

        if rule.rule_id == "kyc_verification":
            return await self._check_kyc_verification(context)

        return {"status": "error", "details": {"error": "Unknown KYC rule"}}

    async def _run_ccpa_check(self, rule: ComplianceRule, context: Dict[str, Any]) -> Dict[str, Any]:
        """Run CCPA compliance check"""

        if rule.rule_id == "ccpa_data_sales":
            return await self._check_ccpa_data_sales(context)

        return {"status": "error", "details": {"error": "Unknown CCPA rule"}}

    async def _run_gaming_check(self, rule: ComplianceRule, context: Dict[str, Any]) -> Dict[str, Any]:
        """Run gaming compliance check"""

        if rule.rule_id == "gaming_responsible_gambling":
            return await self._check_responsible_gambling(context)

        return {"status": "error", "details": {"error": "Unknown gaming rule"}}

    # Specific compliance check implementations
    async def _check_data_retention(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Check GDPR data retention compliance"""

        # Query for data older than retention limits
        # This is a simplified implementation
        expired_data_count = await self._query_expired_data()

        if expired_data_count > 0:
            return {
                "status": "fail",
                "details": {
                    "expired_records": expired_data_count,
                    "message": f"Found {expired_data_count} records exceeding retention limits"
                },
                "remediation_actions": [
                    "Delete expired records",
                    "Update data retention policies",
                    "Implement automated cleanup procedures"
                ]
            }

        return {
            "status": "pass",
            "details": {"message": "All data within retention limits"},
            "evidence": {"expired_records": 0}
        }

    async def _check_consent_management(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Check GDPR consent management"""

        # Check for users without valid consent
        invalid_consents = await self._query_invalid_consents()

        if invalid_consents > 0:
            return {
                "status": "fail",
                "details": {
                    "invalid_consents": invalid_consents,
                    "message": f"Found {invalid_consents} users without valid consent"
                },
                "remediation_actions": [
                    "Obtain valid consent from users",
                    "Implement consent management system",
                    "Stop processing data for non-consenting users"
                ]
            }

        return {
            "status": "pass",
            "details": {"message": "All user consents are valid"},
            "evidence": {"invalid_consents": 0}
        }

    async def _check_data_encryption(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Check PCI DSS data encryption"""

        # Check if sensitive data is properly encrypted
        unencrypted_data = await self._query_unencrypted_sensitive_data()

        if unencrypted_data > 0:
            return {
                "status": "fail",
                "details": {
                    "unencrypted_records": unencrypted_data,
                    "message": f"Found {unencrypted_data} unencrypted sensitive records"
                },
                "remediation_actions": [
                    "Encrypt sensitive data at rest",
                    "Implement proper key management",
                    "Use TLS for data in transit"
                ]
            }

        return {
            "status": "pass",
            "details": {"message": "All sensitive data is properly encrypted"},
            "evidence": {"unencrypted_records": 0}
        }

    async def _check_sanctions_screening(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Check AML sanctions screening"""

        # Check for matches against sanctions lists
        sanctions_matches = await self._query_sanctions_matches()

        if sanctions_matches > 0:
            return {
                "status": "fail",
                "details": {
                    "sanctions_matches": sanctions_matches,
                    "message": f"Found {sanctions_matches} potential sanctions matches"
                },
                "remediation_actions": [
                    "Freeze suspicious accounts",
                    "Report to authorities",
                    "Conduct enhanced due diligence"
                ]
            }

        return {
            "status": "pass",
            "details": {"message": "No sanctions matches found"},
            "evidence": {"sanctions_matches": 0}
        }

    # Placeholder implementations for other checks
    async def _check_data_minimization(self, context: Dict[str, Any]) -> Dict[str, Any]:
        return {"status": "warning", "details": {"message": "Manual review required"}}

    async def _check_access_control(self, context: Dict[str, Any]) -> Dict[str, Any]:
        return {"status": "pass", "details": {"message": "Access controls verified"}}

    async def _check_kyc_verification(self, context: Dict[str, Any]) -> Dict[str, Any]:
        return {"status": "pass", "details": {"message": "KYC verification completed"}}

    async def _check_ccpa_data_sales(self, context: Dict[str, Any]) -> Dict[str, Any]:
        return {"status": "pass", "details": {"message": "CCPA compliance verified"}}

    async def _check_responsible_gambling(self, context: Dict[str, Any]) -> Dict[str, Any]:
        return {"status": "pass", "details": {"message": "Responsible gambling measures in place"}}

    # Data query helper methods (simplified implementations)
    async def _query_expired_data(self) -> int:
        """Query for expired data (placeholder)"""
        # In real implementation, this would query the database
        return 0

    async def _query_invalid_consents(self) -> int:
        """Query for invalid consents (placeholder)"""
        return 0

    async def _query_unencrypted_sensitive_data(self) -> int:
        """Query for unencrypted sensitive data (placeholder)"""
        return 0

    async def _query_sanctions_matches(self) -> int:
        """Query for sanctions matches (placeholder)"""
        return 0

    async def store_compliance_rule(self, rule: ComplianceRule):
        """Store compliance rule in Redis"""

        if not self.redis_client:
            return

        rule_key = f"compliance_rule:{rule.rule_id}"
        rule_data = {
            "rule_id": rule.rule_id,
            "name": rule.name,
            "description": rule.description,
            "regulation": rule.regulation,
            "category": rule.category,
            "severity": rule.severity,
            "check_type": rule.check_type,
            "frequency": rule.frequency,
            "enabled": rule.enabled
        }

        await self.redis_client.set(rule_key, json.dumps(rule_data))

    async def store_compliance_check(self, check: ComplianceCheck):
        """Store compliance check result in Redis"""

        if not self.redis_client:
            return

        check_key = f"compliance_check:{check.check_id}"
        check_data = {
            "check_id": check.check_id,
            "rule_id": check.rule_id,
            "timestamp": check.timestamp,
            "status": check.status,
            "details": check.details,
            "remediation_actions": check.remediation_actions,
            "evidence": check.evidence
        }

        await self.redis_client.set(check_key, json.dumps(check_data))

        # Add to check history
        history_key = "compliance_check_history"
        await self.redis_client.lpush(history_key, json.dumps(check_data))  # ty:ignore[invalid-await]

        # Keep only last 1000 checks
        await self.redis_client.ltrim(history_key, 0, 999)  # ty:ignore[invalid-await]

        # Set expiration (90 days)
        await self.redis_client.expire(check_key, 90 * 24 * 60 * 60)

    async def log_audit_event(self, entry: AuditLogEntry):
        """Log audit event for compliance"""

        if not self.redis_client:
            return

        audit_key = f"audit_log:{entry.entry_id}"
        audit_data = {
            "entry_id": entry.entry_id,
            "timestamp": entry.timestamp,
            "user_id": entry.user_id,
            "action": entry.action,
            "resource": entry.resource,
            "resource_id": entry.resource_id,
            "details": entry.details,
            "ip_address": entry.ip_address,
            "user_agent": entry.user_agent,
            "compliance_tags": entry.compliance_tags
        }

        await self.redis_client.set(audit_key, json.dumps(audit_data))

        # Add to audit history
        history_key = "audit_log_history"
        await self.redis_client.lpush(history_key, json.dumps(audit_data))  # ty:ignore[invalid-await]

        # Keep only last 10000 audit entries
        await self.redis_client.ltrim(history_key, 0, 9999)  # ty:ignore[invalid-await]

        # Set expiration (7 years for audit logs)
        await self.redis_client.expire(audit_key, 7 * 365 * 24 * 60 * 60)

    async def get_compliance_status(self) -> Dict[str, Any]:
        """Get overall compliance status"""

        status: Dict[str, Any] = {
            "overall_status": "compliant",
            "regulations": {},
            "last_check": None,
            "critical_issues": 0,
            "high_issues": 0
        }

        # Get recent check results
        if self.redis_client:
            history_key = "compliance_check_history"
            recent_checks = await self.redis_client.lrange(history_key, 0, 99)  # Last 100 checks  # ty:ignore[invalid-await]

            regulation_status = {}
            last_check_time = None

            for check_json in recent_checks:
                try:
                    check = json.loads(check_json)
                    regulation = check.get("regulation", "unknown")

                    if regulation not in regulation_status:
                        regulation_status[regulation] = {"pass": 0, "fail": 0, "warning": 0}

                    status_type = check.get("status", "unknown")
                    if status_type in regulation_status[regulation]:
                        regulation_status[regulation][status_type] += 1

                    # Track issues
                    if status_type == "fail":
                        severity = check.get("severity", "medium")
                        if severity == "critical":
                            status["critical_issues"] += 1
                        elif severity == "high":
                            status["high_issues"] += 1

                    # Update last check time
                    check_time = check.get("timestamp")
                    if check_time and (not last_check_time or check_time > last_check_time):
                        last_check_time = check_time

                except Exception: 
                    continue

            status["regulations"] = regulation_status
            status["last_check"] = last_check_time

            # Determine overall status
            if status["critical_issues"] > 0:
                status["overall_status"] = "non_compliant"
            elif status["high_issues"] > 5:
                status["overall_status"] = "at_risk"

        return status


# Global compliance engine instance
compliance_engine = ComplianceEngine()


async def initialize_compliance_engine():
    """Initialize the global compliance engine"""
    await compliance_engine.initialize()


if __name__ == "__main__":
    # Example usage
    async def main():
        await initialize_compliance_engine()

        # Run a compliance check
        check_result = await compliance_engine.run_compliance_check("gdpr_data_retention")
        print(f"Compliance check result: {check_result.status}")

        # Get compliance status
        status = await compliance_engine.get_compliance_status()
        print(f"Overall compliance status: {status['overall_status']}")

    asyncio.run(main())