#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 23, DevSecOps for iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Supply Chain Security Manager for iGaming Platforms

Comprehensive security management for 100+ third-party providers including
game providers, payment processors, marketing tools, and analytics platforms.

Requirements:
    pip install aiohttp redis asyncpg pyyaml

Usage:
    from supply_chain_security import SupplyChainSecurityManager

    manager = SupplyChainSecurityManager(redis_client, db_pool)
    assessment = await manager.assess_provider_security(provider)
"""

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

try:
    import aiohttp
except ImportError:
    aiohttp = None  # type: ignore

try:
    import redis.asyncio as redis
except ImportError:
    redis = None  # type: ignore

try:
    import asyncpg  # ty:ignore[unresolved-import]
except ImportError:
    asyncpg = None


class RiskLevel(Enum):
    """Risk severity levels for security findings"""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class ProviderType(Enum):
    """Types of third-party providers"""
    GAME_PROVIDER = "game_provider"
    PAYMENT_PROCESSOR = "payment_processor"
    MARKETING_TOOL = "marketing_tool"
    ANALYTICS_PLATFORM = "analytics_platform"
    INFRASTRUCTURE = "infrastructure"
    SECURITY_TOOL = "security_tool"


@dataclass
class ThirdPartyProvider:
    """Represents a third-party provider with security metadata"""
    provider_id: str
    name: str
    provider_type: ProviderType
    endpoint_url: str
    api_key: str
    risk_level: RiskLevel
    compliance_status: Dict[str, Any]
    last_assessment: datetime
    next_assessment: datetime
    security_contacts: List[str]
    incident_history: List[Dict[str, Any]]
    data_classification: str
    integration_method: str


@dataclass
class SecurityFinding:
    """Represents a security finding from assessment"""
    category: str
    severity: RiskLevel
    finding: str
    score: int
    evidence: Dict[str, Any] = field(default_factory=dict)
    recommendation: Optional[str] = None


class SupplyChainSecurityManager:
    """
    Comprehensive supply chain security management for iGaming platforms.

    Provides automated security assessments for third-party providers
    including SSL/TLS validation, API security testing, SBOM analysis,
    and compliance verification.

    Supports:
    - Game providers (Evolution, Pragmatic, etc.)
    - Payment processors (PCI DSS compliant)
    - Marketing tools
    - Analytics platforms
    """

    def __init__(
        self,
        redis_client: Any,
        db_pool: Any,
        logger: Optional[logging.Logger] = None
    ):
        self.redis = redis_client
        self.db_pool = db_pool
        self.logger = logger or logging.getLogger(__name__)

        # Security assessment criteria by provider type
        self.assessment_criteria: Dict[str, Dict[str, Dict[str, Any]]] = {
            'game_provider': {
                'ssl_encryption': {'weight': 0.15, 'required': True},
                'api_authentication': {'weight': 0.20, 'required': True},
                'data_encryption': {'weight': 0.15, 'required': True},
                'audit_logging': {'weight': 0.10, 'required': True},
                'incident_response': {'weight': 0.10, 'required': True},
                'compliance_certifications': {'weight': 0.20, 'required': True},
                'vulnerability_management': {'weight': 0.10, 'required': False}
            },
            'payment_processor': {
                'pci_compliance': {'weight': 0.30, 'required': True},
                'encryption_standards': {'weight': 0.25, 'required': True},
                'fraud_detection': {'weight': 0.20, 'required': True},
                'audit_trails': {'weight': 0.15, 'required': True},
                'incident_history': {'weight': 0.10, 'required': False}
            },
            'marketing_tool': {
                'data_handling': {'weight': 0.30, 'required': True},
                'gdpr_compliance': {'weight': 0.25, 'required': True},
                'api_security': {'weight': 0.25, 'required': True},
                'audit_logging': {'weight': 0.20, 'required': False}
            },
            'analytics_platform': {
                'data_encryption': {'weight': 0.30, 'required': True},
                'access_controls': {'weight': 0.25, 'required': True},
                'data_retention': {'weight': 0.25, 'required': True},
                'api_security': {'weight': 0.20, 'required': True}
            }
        }

    async def assess_provider_security(
        self,
        provider: ThirdPartyProvider
    ) -> Dict[str, Any]:
        """
        Perform comprehensive security assessment of third-party provider.

        Args:
            provider: ThirdPartyProvider instance to assess

        Returns:
            Assessment results including findings, risk level, recommendations
        """
        try:
            findings: List[Dict[str, Any]] = []
            recommendations: List[str] = []

            # SSL/TLS assessment
            ssl_assessment = await self._assess_ssl_security(provider.endpoint_url)
            findings.extend(ssl_assessment.get('findings', []))

            # API security assessment
            api_assessment = await self._assess_api_security(provider)
            findings.extend(api_assessment.get('findings', []))

            # Data protection assessment
            data_assessment = await self._assess_data_protection(provider)
            findings.extend(data_assessment.get('findings', []))

            # Compliance certification verification
            compliance_assessment = await self._verify_compliance_certifications(
                provider
            )
            findings.extend(compliance_assessment.get('findings', []))

            # Incident history analysis
            incident_assessment = await self._analyze_incident_history(provider)
            findings.extend(incident_assessment.get('findings', []))

            # Calculate overall risk score
            provider_type_key = provider.provider_type.value
            total_score = self._calculate_overall_score(findings, provider_type_key)

            # Determine risk level
            risk_level = self._determine_risk_level(total_score)

            # Generate recommendations
            recommendations = self._generate_recommendations(findings)

            assessment_results: Dict[str, Any] = {
                'provider_id': provider.provider_id,
                'assessment_date': datetime.now(timezone.utc).isoformat(),
                'overall_score': total_score,
                'risk_level': risk_level.value,
                'findings': findings,
                'recommendations': recommendations,
                'compliance_gaps': []
            }

            # Store assessment results
            await self._store_assessment_results(assessment_results)

            # Trigger alerts for high-risk findings
            if risk_level in [RiskLevel.CRITICAL, RiskLevel.HIGH]:
                await self._trigger_security_alert(provider, assessment_results)

            return assessment_results

        except Exception as e:
            self.logger.error(
                f"Security assessment failed for {provider.provider_id}: {e}"
            )
            return {
                'provider_id': provider.provider_id,
                'error': str(e),
                'risk_level': RiskLevel.CRITICAL.value
            }

    async def _assess_ssl_security(self, endpoint_url: str) -> Dict[str, Any]:
        """Assess SSL/TLS security configuration"""
        findings: List[Dict[str, Any]] = []

        try:
            if aiohttp is None:
                findings.append({
                    'category': 'ssl_encryption',
                    'severity': RiskLevel.MEDIUM.value,
                    'finding': 'aiohttp not available for SSL testing',
                    'score': 50
                })
                return {'findings': findings}

            # Test SSL configuration using SSL Labs API
            ssl_test_url = (
                f"https://api.ssllabs.com/api/v3/analyze"
                f"?host={endpoint_url}&publish=off&startNew=on"
            )

            async with aiohttp.ClientSession() as session:
                async with session.get(ssl_test_url) as response:
                    if response.status == 200:
                        ssl_data = await response.json()

                        if ssl_data.get('endpoints'):
                            endpoint = ssl_data['endpoints'][0]
                            grade = endpoint.get('grade', 'Unknown')

                            if grade in ['A+', 'A']:
                                findings.append({
                                    'category': 'ssl_encryption',
                                    'severity': RiskLevel.INFO.value,
                                    'finding': f"SSL grade {grade} - Excellent",
                                    'score': 100
                                })
                            elif grade == 'B':
                                findings.append({
                                    'category': 'ssl_encryption',
                                    'severity': RiskLevel.MEDIUM.value,
                                    'finding': "SSL grade B - Could be improved",
                                    'score': 80
                                })
                            else:
                                findings.append({
                                    'category': 'ssl_encryption',
                                    'severity': RiskLevel.HIGH.value,
                                    'finding': f"SSL grade {grade} - Weak",
                                    'score': 40
                                })

                            # Check for vulnerabilities
                            if endpoint.get('vulnBeast'):
                                findings.append({
                                    'category': 'ssl_encryption',
                                    'severity': RiskLevel.HIGH.value,
                                    'finding': "Vulnerable to BEAST attack",
                                    'score': 20
                                })
                    else:
                        findings.append({
                            'category': 'ssl_encryption',
                            'severity': RiskLevel.MEDIUM.value,
                            'finding': "Unable to assess SSL configuration",
                            'score': 70
                        })

        except Exception as e:
            findings.append({
                'category': 'ssl_encryption',
                'severity': RiskLevel.MEDIUM.value,
                'finding': f"SSL assessment failed: {str(e)}",
                'score': 50
            })

        return {'findings': findings}

    async def _assess_api_security(
        self,
        provider: ThirdPartyProvider
    ) -> Dict[str, Any]:
        """Assess API security implementation"""
        findings: List[Dict[str, Any]] = []

        try:
            # Test API authentication
            auth_test = await self._test_api_authentication(provider)
            findings.extend(auth_test.get('findings', []))

            # Test rate limiting
            rate_limit_test = await self._test_rate_limiting(provider)
            findings.extend(rate_limit_test.get('findings', []))

        except Exception as e:
            findings.append({
                'category': 'api_security',
                'severity': RiskLevel.HIGH.value,
                'finding': f"API security assessment failed: {str(e)}",
                'score': 0
            })

        return {'findings': findings}

    async def _test_api_authentication(
        self,
        provider: ThirdPartyProvider
    ) -> Dict[str, Any]:
        """Test API authentication mechanisms"""
        findings: List[Dict[str, Any]] = []

        if aiohttp is None:
            return {'findings': findings}

        test_endpoints = [
            {'path': '/api/test', 'method': 'GET'},
            {'path': '/api/user/profile', 'method': 'GET'},
        ]

        for endpoint in test_endpoints:
            try:
                async with aiohttp.ClientSession() as session:
                    test_url = f"{provider.endpoint_url}{endpoint['path']}"
                    timeout = aiohttp.ClientTimeout(total=10)

                    async with session.request(
                        endpoint['method'],
                        test_url,
                        timeout=timeout
                    ) as response:
                        if response.status == 200:
                            findings.append({
                                'category': 'api_authentication',
                                'severity': RiskLevel.CRITICAL.value,
                                'finding': (
                                    f"Endpoint {endpoint['path']} accessible "
                                    "without authentication"
                                ),
                                'score': 0
                            })
                        elif response.status == 401:
                            findings.append({
                                'category': 'api_authentication',
                                'severity': RiskLevel.INFO.value,
                                'finding': (
                                    f"Endpoint {endpoint['path']} properly "
                                    "requires authentication"
                                ),
                                'score': 100
                            })

            except asyncio.TimeoutError:
                findings.append({
                    'category': 'api_authentication',
                    'severity': RiskLevel.MEDIUM.value,
                    'finding': f"Timeout testing {endpoint['path']}",
                    'score': 60
                })
            except Exception as e:
                findings.append({
                    'category': 'api_authentication',
                    'severity': RiskLevel.LOW.value,
                    'finding': f"Error testing {endpoint['path']}: {str(e)}",
                    'score': 70
                })

        return {'findings': findings}

    async def _test_rate_limiting(
        self,
        provider: ThirdPartyProvider
    ) -> Dict[str, Any]:
        """Test API rate limiting implementation"""
        findings: List[Dict[str, Any]] = []

        if aiohttp is None:
            return {'findings': findings}

        test_url = f"{provider.endpoint_url}/api/test"
        rapid_requests = 50

        try:
            async with aiohttp.ClientSession() as session:
                responses: List[int] = []

                for _ in range(rapid_requests):
                    try:
                        async with session.get(
                            test_url,
                            timeout=aiohttp.ClientTimeout(total=5)
                        ) as response:
                            responses.append(response.status)
                    except Exception:
                        responses.append(0)

                rate_limited = sum(1 for s in responses if s == 429)
                success = sum(1 for s in responses if s == 200)

                if rate_limited > 0:
                    findings.append({
                        'category': 'rate_limiting',
                        'severity': RiskLevel.INFO.value,
                        'finding': (
                            f"Rate limiting detected: "
                            f"{rate_limited}/{rapid_requests} blocked"
                        ),
                        'score': 90
                    })
                elif success == rapid_requests:
                    findings.append({
                        'category': 'rate_limiting',
                        'severity': RiskLevel.HIGH.value,
                        'finding': "No rate limiting - vulnerable to DoS",
                        'score': 30
                    })

        except Exception as e:
            findings.append({
                'category': 'rate_limiting',
                'severity': RiskLevel.MEDIUM.value,
                'finding': f"Rate limiting test failed: {str(e)}",
                'score': 50
            })

        return {'findings': findings}

    async def _assess_data_protection(
        self,
        provider: ThirdPartyProvider
    ) -> Dict[str, Any]:
        """Assess data protection measures"""
        findings: List[Dict[str, Any]] = []

        # Check data classification
        if provider.data_classification in ['confidential', 'restricted']:
            findings.append({
                'category': 'data_protection',
                'severity': RiskLevel.INFO.value,
                'finding': "Proper data classification in place",
                'score': 90
            })
        else:
            findings.append({
                'category': 'data_protection',
                'severity': RiskLevel.MEDIUM.value,
                'finding': "Data classification needs review",
                'score': 60
            })

        return {'findings': findings}

    async def _verify_compliance_certifications(
        self,
        provider: ThirdPartyProvider
    ) -> Dict[str, Any]:
        """Verify compliance certifications"""
        findings: List[Dict[str, Any]] = []

        required_certs = {
            ProviderType.PAYMENT_PROCESSOR: ['PCI_DSS'],
            ProviderType.GAME_PROVIDER: ['GLI', 'ISO27001'],
            ProviderType.ANALYTICS_PLATFORM: ['GDPR', 'SOC2'],
        }

        provider_certs = provider.compliance_status.get('certifications', [])
        required = required_certs.get(provider.provider_type, [])

        for cert in required:
            if cert in provider_certs:
                findings.append({
                    'category': 'compliance',
                    'severity': RiskLevel.INFO.value,
                    'finding': f"{cert} certification verified",
                    'score': 100
                })
            else:
                findings.append({
                    'category': 'compliance',
                    'severity': RiskLevel.HIGH.value,
                    'finding': f"Missing required {cert} certification",
                    'score': 20
                })

        return {'findings': findings}

    async def _analyze_incident_history(
        self,
        provider: ThirdPartyProvider
    ) -> Dict[str, Any]:
        """Analyze provider incident history"""
        findings: List[Dict[str, Any]] = []

        incident_count = len(provider.incident_history)
        recent_incidents = [
            i for i in provider.incident_history
            if datetime.fromisoformat(
                str(i.get('date', datetime.now(timezone.utc).isoformat()))
            ) > datetime.now(timezone.utc) - timedelta(days=365)
        ]

        if incident_count == 0:
            findings.append({
                'category': 'incident_history',
                'severity': RiskLevel.INFO.value,
                'finding': "No security incidents on record",
                'score': 100
            })
        elif len(recent_incidents) > 3:
            findings.append({
                'category': 'incident_history',
                'severity': RiskLevel.HIGH.value,
                'finding': (
                    f"{len(recent_incidents)} incidents in the last year"
                ),
                'score': 30
            })
        else:
            findings.append({
                'category': 'incident_history',
                'severity': RiskLevel.MEDIUM.value,
                'finding': f"{incident_count} total historical incidents",
                'score': 70
            })

        return {'findings': findings}

    def _calculate_overall_score(
        self,
        findings: List[Dict[str, Any]],
        provider_type: str
    ) -> float:
        """Calculate weighted overall security score"""
        if not findings:
            return 0.0

        criteria = self.assessment_criteria.get(
            provider_type,
            self.assessment_criteria.get('game_provider', {})
        )

        total_weight = sum(c.get('weight', 0) for c in criteria.values())
        weighted_score = 0.0

        for finding in findings:
            category = finding.get('category', '')
            score = finding.get('score', 0)

            if category in criteria:
                weight = criteria[category].get('weight', 0.1)
                weighted_score += score * weight

        return (weighted_score / total_weight * 100) if total_weight > 0 else 0.0

    def _determine_risk_level(self, score: float) -> RiskLevel:
        """Determine risk level based on overall score"""
        if score >= 90:
            return RiskLevel.LOW
        elif score >= 70:
            return RiskLevel.MEDIUM
        elif score >= 50:
            return RiskLevel.HIGH
        else:
            return RiskLevel.CRITICAL

    def _generate_recommendations(
        self,
        findings: List[Dict[str, Any]]
    ) -> List[str]:
        """Generate actionable recommendations from findings"""
        recommendations: List[str] = []

        severity_order = [
            RiskLevel.CRITICAL.value,
            RiskLevel.HIGH.value,
            RiskLevel.MEDIUM.value
        ]

        for severity in severity_order:
            for finding in findings:
                if finding.get('severity') == severity:
                    desc = finding.get('finding', 'Unknown issue')
                    if severity == RiskLevel.CRITICAL.value:
                        recommendations.append(f"URGENT: Address {desc}")
                    elif severity == RiskLevel.HIGH.value:
                        recommendations.append(f"HIGH: Resolve {desc}")
                    elif severity == RiskLevel.MEDIUM.value:
                        recommendations.append(f"MEDIUM: Review {desc}")

        return recommendations[:10]  # Top 10 recommendations

    async def _store_assessment_results(
        self,
        results: Dict[str, Any]
    ) -> None:
        """Store assessment results in database"""
        # In production, persist to database
        self.logger.info(
            f"Assessment stored for provider {results.get('provider_id')}"
        )

    async def _trigger_security_alert(
        self,
        provider: ThirdPartyProvider,
        assessment: Dict[str, Any]
    ) -> None:
        """Trigger security alert for high-risk findings"""
        self.logger.warning(
            f"SECURITY ALERT: Provider {provider.provider_id} "
            f"assessed as {assessment.get('risk_level')}"
        )

    async def generate_sbom(
        self,
        provider: ThirdPartyProvider
    ) -> Dict[str, Any]:
        """Generate Software Bill of Materials for provider"""
        try:
            return {
                'provider_id': provider.provider_id,
                'sbom_version': '1.0',
                'components_count': 0,
                'vulnerabilities_found': [],
                'license_issues': [],
                'risk_score': 50,
                'last_updated': datetime.now(timezone.utc).isoformat()
            }
        except Exception as e:
            self.logger.error(f"SBOM generation failed: {e}")
            return {
                'provider_id': provider.provider_id,
                'error': str(e),
                'risk_score': 70
            }

    async def create_security_dashboard(self) -> Dict[str, Any]:
        """Create comprehensive security dashboard for all providers"""
        return {
            'summary': {
                'total_providers': 0,
                'critical_risk': 0,
                'high_risk': 0,
                'medium_risk': 0,
                'low_risk': 0,
                'avg_security_score': 0.0
            },
            'recent_vulnerabilities': [],
            'compliance_by_type': [],
            'trend_analysis': {},
            'action_items': []
        }


# Provider type assessment weights for reference
ASSESSMENT_WEIGHTS = {
    'game_provider': {
        'ssl_encryption': 0.15,
        'api_authentication': 0.20,
        'data_encryption': 0.15,
        'compliance_certifications': 0.20,
        'audit_logging': 0.10,
        'incident_response': 0.10,
        'vulnerability_management': 0.10
    },
    'payment_processor': {
        'pci_compliance': 0.30,
        'encryption_standards': 0.25,
        'fraud_detection': 0.20,
        'audit_trails': 0.15,
        'incident_history': 0.10
    }
}
