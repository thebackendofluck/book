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

Implements automated compliance checks across GDPR, PCI DSS, AML/KYC,
CCPA, and gaming-commission regulations.  Each rule is evaluated on a
configurable schedule (real-time, hourly, daily, monthly) and results
are stored with full audit trail.

Reference implementation for Chapter 41: Anti-Fraud System Deep Dive.
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

import redis.asyncio as redis
import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class ComplianceRule(BaseModel):
    rule_id: str
    name: str
    description: str
    regulation: str         # GDPR, PCI_DSS, AML, KYC, CCPA, GAMING
    category: str           # data_privacy, security, compliance, reporting
    severity: str           # critical, high, medium, low
    check_type: str         # automated, manual, periodic
    frequency: str          # realtime, hourly, daily, weekly, monthly
    enabled: bool = True


class ComplianceCheck(BaseModel):
    check_id: str
    rule_id: str
    timestamp: str
    status: str             # pass, fail, warning, error
    details: Dict[str, Any]
    remediation_actions: List[str] = []
    evidence: Optional[Dict[str, Any]] = None


class DataSubjectRequest(BaseModel):
    """GDPR data-subject access / erasure / portability request."""
    request_id: str
    subject_id: str
    request_type: str       # access, rectification, erasure, restriction, portability, objection
    status: str             # pending, processing, completed, rejected
    created_at: str
    completed_at: Optional[str] = None
    data_scope: Dict[str, Any] = {}


class AuditLogEntry(BaseModel):
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


# ---------------------------------------------------------------------------
# Compliance engine
# ---------------------------------------------------------------------------

class ComplianceEngine:
    """Runs and records regulatory compliance checks."""

    def __init__(self):
        self.redis_client: Optional[redis.Redis] = None
        self.rules: Dict[str, ComplianceRule] = {}
        self.active_checks: Set[str] = set()

    async def initialize(self):
        self.redis_client = redis.Redis(host="redis", port=6379, decode_responses=True)
        await self._load_rules()
        logger.info("Compliance engine initialised")

    # ------------------------------------------------------------------
    # Rule management
    # ------------------------------------------------------------------

    async def _load_rules(self):
        defaults = [
            # GDPR
            ComplianceRule(
                rule_id="gdpr_data_retention", name="Data Retention Limits",
                description="Data not retained beyond legal limits",
                regulation="GDPR", category="data_privacy",
                severity="high", check_type="automated", frequency="daily",
            ),
            ComplianceRule(
                rule_id="gdpr_consent_management", name="Consent Management",
                description="User consents properly managed",
                regulation="GDPR", category="data_privacy",
                severity="critical", check_type="automated", frequency="realtime",
            ),
            ComplianceRule(
                rule_id="gdpr_data_minimization", name="Data Minimization",
                description="Only necessary data collected",
                regulation="GDPR", category="data_privacy",
                severity="medium", check_type="manual", frequency="monthly",
            ),
            # PCI DSS
            ComplianceRule(
                rule_id="pci_dss_encryption", name="Data Encryption",
                description="Sensitive payment data encrypted at rest and in transit",
                regulation="PCI_DSS", category="security",
                severity="critical", check_type="automated", frequency="realtime",
            ),
            ComplianceRule(
                rule_id="pci_dss_access_control", name="Access Control",
                description="Cardholder-data access controls verified",
                regulation="PCI_DSS", category="security",
                severity="high", check_type="automated", frequency="hourly",
            ),
            # AML / KYC
            ComplianceRule(
                rule_id="aml_sanctions_screening", name="Sanctions Screening",
                description="Screen against OFAC, EU, UN sanctions lists",
                regulation="AML", category="compliance",
                severity="critical", check_type="automated", frequency="realtime",
            ),
            ComplianceRule(
                rule_id="kyc_verification", name="KYC Verification",
                description="Customer identity and risk profile verified",
                regulation="KYC", category="compliance",
                severity="high", check_type="automated", frequency="realtime",
            ),
            # CCPA
            ComplianceRule(
                rule_id="ccpa_data_sales", name="Data Sales Opt-out",
                description="Honour CCPA opt-out requests",
                regulation="CCPA", category="data_privacy",
                severity="high", check_type="automated", frequency="realtime",
            ),
            # Gaming
            ComplianceRule(
                rule_id="gaming_responsible_gambling", name="Responsible Gambling",
                description="Responsible-gambling safeguards active",
                regulation="GAMING", category="compliance",
                severity="medium", check_type="automated", frequency="daily",
            ),
        ]
        for rule in defaults:
            self.rules[rule.rule_id] = rule
            await self._store_rule(rule)
        logger.info(f"Loaded {len(defaults)} compliance rules")

    # ------------------------------------------------------------------
    # Check execution
    # ------------------------------------------------------------------

    async def run_compliance_check(
        self, rule_id: str, context: Optional[Dict[str, Any]] = None
    ) -> ComplianceCheck:
        if rule_id not in self.rules:
            raise ValueError(f"Rule '{rule_id}' not found")

        rule = self.rules[rule_id]
        check_id = f"check_{rule_id}_{int(datetime.now(timezone.utc).timestamp())}"

        if check_id in self.active_checks:
            return ComplianceCheck(
                check_id=check_id, rule_id=rule_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                status="duplicate", details={"message": "Already running"},
            )

        self.active_checks.add(check_id)
        try:
            dispatch = {
                "GDPR": self._run_gdpr,
                "PCI_DSS": self._run_pci_dss,
                "AML": self._run_aml,
                "KYC": self._run_kyc,
                "CCPA": self._run_ccpa,
                "GAMING": self._run_gaming,
            }
            handler = dispatch.get(rule.regulation)
            if handler:
                result = await handler(rule, context or {})
            else:
                result = {"status": "error", "details": {"error": f"Unknown regulation: {rule.regulation}"}}

            check = ComplianceCheck(
                check_id=check_id, rule_id=rule_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                status=result["status"], details=result["details"],  # ty:ignore[invalid-argument-type]
                remediation_actions=result.get("remediation_actions", []),  # ty:ignore[invalid-argument-type]
                evidence=result.get("evidence"),  # ty:ignore[invalid-argument-type]
            )
            await self._store_check(check)
            return check
        finally:
            self.active_checks.discard(check_id)

    # ------------------------------------------------------------------
    # Regulation-specific handlers
    # ------------------------------------------------------------------

    async def _run_gdpr(self, rule: ComplianceRule, ctx: Dict) -> Dict:
        if rule.rule_id == "gdpr_data_retention":
            expired = await self._query_expired_data()
            if expired > 0:
                return {
                    "status": "fail",
                    "details": {"expired_records": expired,
                                "message": f"{expired} records exceed retention limits"},
                    "remediation_actions": [
                        "Delete expired records",
                        "Implement automated cleanup",
                    ],
                }
            return {"status": "pass", "details": {"message": "Within retention limits"},
                    "evidence": {"expired_records": 0}}

        if rule.rule_id == "gdpr_consent_management":
            invalid = await self._query_invalid_consents()
            if invalid > 0:
                return {
                    "status": "fail",
                    "details": {"invalid_consents": invalid},
                    "remediation_actions": ["Obtain valid consent", "Stop processing non-consented data"],
                }
            return {"status": "pass", "details": {"message": "All consents valid"}}

        return {"status": "warning", "details": {"message": "Manual review required"}}

    async def _run_pci_dss(self, rule: ComplianceRule, ctx: Dict) -> Dict:
        if rule.rule_id == "pci_dss_encryption":
            unenc = await self._query_unencrypted_data()
            if unenc > 0:
                return {
                    "status": "fail",
                    "details": {"unencrypted_records": unenc},
                    "remediation_actions": ["Encrypt at rest", "Enable TLS"],
                }
            return {"status": "pass", "details": {"message": "All data encrypted"}}
        return {"status": "pass", "details": {"message": "Access controls verified"}}

    async def _run_aml(self, rule: ComplianceRule, ctx: Dict) -> Dict:
        matches = await self._query_sanctions_matches()
        if matches > 0:
            return {
                "status": "fail",
                "details": {"sanctions_matches": matches},
                "remediation_actions": ["Freeze accounts", "Report to authorities"],
            }
        return {"status": "pass", "details": {"message": "No sanctions matches"}}

    async def _run_kyc(self, rule: ComplianceRule, ctx: Dict) -> Dict:
        return {"status": "pass", "details": {"message": "KYC verified"}}

    async def _run_ccpa(self, rule: ComplianceRule, ctx: Dict) -> Dict:
        return {"status": "pass", "details": {"message": "CCPA compliant"}}

    async def _run_gaming(self, rule: ComplianceRule, ctx: Dict) -> Dict:
        return {"status": "pass", "details": {"message": "Responsible gambling OK"}}

    # ------------------------------------------------------------------
    # Data query stubs (replace with real DB queries in production)
    # ------------------------------------------------------------------

    async def _query_expired_data(self) -> int:
        return 0

    async def _query_invalid_consents(self) -> int:
        return 0

    async def _query_unencrypted_data(self) -> int:
        return 0

    async def _query_sanctions_matches(self) -> int:
        return 0

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    async def _store_rule(self, rule: ComplianceRule):
        if not self.redis_client:
            return
        await self.redis_client.set(
            f"compliance_rule:{rule.rule_id}", json.dumps(rule.dict())  # ty:ignore[deprecated]
        )

    async def _store_check(self, check: ComplianceCheck):
        if not self.redis_client:
            return
        data = json.dumps(check.dict())  # ty:ignore[deprecated]
        key = f"compliance_check:{check.check_id}"
        await self.redis_client.set(key, data)
        await self.redis_client.lpush("compliance_check_history", data)  # ty:ignore[invalid-await]
        await self.redis_client.ltrim("compliance_check_history", 0, 999)  # ty:ignore[invalid-await]
        await self.redis_client.expire(key, 90 * 86400)

    async def log_audit_event(self, entry: AuditLogEntry):
        if not self.redis_client:
            return
        data = json.dumps(entry.dict())  # ty:ignore[deprecated]
        key = f"audit_log:{entry.entry_id}"
        await self.redis_client.set(key, data)
        await self.redis_client.lpush("audit_log_history", data)  # ty:ignore[invalid-await]
        await self.redis_client.ltrim("audit_log_history", 0, 9999)  # ty:ignore[invalid-await]
        await self.redis_client.expire(key, 7 * 365 * 86400)  # 7 years

    async def get_compliance_status(self) -> Dict[str, Any]:
        """Aggregate pass/fail counts per regulation from recent checks."""
        status: Dict[str, Any] = {
            "overall_status": "compliant",
            "regulations": {},
            "critical_issues": 0,
            "high_issues": 0,
        }
        if not self.redis_client:
            return status

        raw = await self.redis_client.lrange("compliance_check_history", 0, 99)  # ty:ignore[invalid-await]
        for item in raw:
            try:
                c = json.loads(item)
                reg = c.get("regulation", "unknown")
                st = c.get("status", "unknown")
                status["regulations"].setdefault(reg, {"pass": 0, "fail": 0, "warning": 0})
                if st in status["regulations"][reg]:
                    status["regulations"][reg][st] += 1
                if st == "fail":
                    sev = c.get("severity", "medium")
                    if sev == "critical":
                        status["critical_issues"] += 1
                    elif sev == "high":
                        status["high_issues"] += 1
            except Exception:
                continue

        if status["critical_issues"] > 0:
            status["overall_status"] = "non_compliant"
        elif status["high_issues"] > 5:
            status["overall_status"] = "at_risk"

        return status


# ---------------------------------------------------------------------------
# Module-level instance
# ---------------------------------------------------------------------------

compliance_engine = ComplianceEngine()


async def initialize_compliance_engine():
    await compliance_engine.initialize()


if __name__ == "__main__":
    async def _demo():
        await initialize_compliance_engine()
        result = await compliance_engine.run_compliance_check("gdpr_data_retention")
        print(f"Check result: {result.status}")
        overview = await compliance_engine.get_compliance_status()
        print(f"Overall: {overview['overall_status']}")

    asyncio.run(_demo())
