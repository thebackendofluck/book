# Companion code for "The Backend of Luck" - Chapter 32, Testing and QA in Gambling.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Multi-jurisdictional Compliance Testing Framework

Comprehensive compliance testing for iGaming platforms across
multiple regulatory jurisdictions including:
- UK (UKGC)
- Malta (MGA)
- New Jersey (NJ DGE)
- Sweden (Spelinspektionen)

Compliance Categories:
- RNG Fairness
- RTP Verification
- Responsible Gaming
- Data Protection (GDPR)
- Anti-Money Laundering (AML)
- Customer Verification (KYC)
- Geolocation Accuracy
- Game Audit Trail
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List


class ComplianceTestingFramework:
    """
    Multi-jurisdictional compliance testing framework.

    Provides comprehensive compliance testing for iGaming platforms
    across multiple regulatory jurisdictions with automated
    reporting and certification support.

    Example:
        >>> framework = ComplianceTestingFramework(redis_client, db_pool)
        >>> result = await framework.run_compliance_test_suite(
        ...     jurisdiction="UK",
        ...     test_scope={"game_ids": ["blackjack_v1", "roulette_v2"]}
        ... )
    """

    def __init__(self, redis_client: Any, db_pool: Any) -> None:
        self.redis = redis_client
        self.db_pool = db_pool
        self.logger = logging.getLogger(__name__)

        # Jurisdiction-specific compliance requirements
        self.compliance_requirements: Dict[str, Dict[str, Any]] = {
            "UK": {
                "regulator": "UKGC",
                "technical_standards": "UKGC Technical Standards",
                "required_tests": [
                    "rng_fairness",
                    "rtp_verification",
                    "responsible_gaming",
                    "data_protection",
                    "anti_money_laundering",
                    "customer_verification",
                ],
                "minimum_test_coverage": 0.95,
                "audit_frequency": "quarterly",
                "reporting_deadline_days": 30,
            },
            "Malta": {
                "regulator": "MGA",
                "technical_standards": "MGA Technical Guidelines",
                "required_tests": [
                    "rng_fairness",
                    "rtp_verification",
                    "game_integrity",
                    "player_funds_segregation",
                    "responsible_gaming",
                    "cybersecurity",
                ],
                "minimum_test_coverage": 0.98,
                "audit_frequency": "semi_annually",
                "reporting_deadline_days": 45,
            },
            "New Jersey": {
                "regulator": "NJ DGE",
                "technical_standards": "NJ DGE Technical Standards",
                "required_tests": [
                    "rng_fairness",
                    "rtp_verification",
                    "geolocation_accuracy",
                    "age_verification",
                    "responsible_gaming",
                    "game_audit_trail",
                ],
                "minimum_test_coverage": 0.99,
                "audit_frequency": "monthly",
                "reporting_deadline_days": 15,
            },
            "Sweden": {
                "regulator": "Spelinspektionen",
                "technical_standards": "Swedish Gambling Act",
                "required_tests": [
                    "rng_fairness",
                    "rtp_verification",
                    "responsible_gaming",
                    "bonus_regulations",
                    "marketing_compliance",
                    "self_exclusion_integration",
                ],
                "minimum_test_coverage": 0.97,
                "audit_frequency": "quarterly",
                "reporting_deadline_days": 30,
            },
        }

    async def run_compliance_test_suite(
        self, jurisdiction: str, test_scope: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Run comprehensive compliance test suite for specific jurisdiction."""
        try:
            config = self.compliance_requirements.get(jurisdiction)
            if not config:
                raise ValueError(f"Unsupported jurisdiction: {jurisdiction}")

            now = datetime.now(timezone.utc)
            test_suite_id = f"COMPLIANCE_{jurisdiction}_{now.strftime('%Y%m%d_%H%M%S')}"

            self.logger.info(f"Starting compliance test suite for {jurisdiction}")

            test_results: Dict[str, Any] = {
                "test_suite_id": test_suite_id,
                "jurisdiction": jurisdiction,
                "regulator": config["regulator"],
                "start_time": now.isoformat(),
                "test_categories": {},
                "overall_score": 0.0,
                "compliance_status": "pending",
                "issues": [],
                "recommendations": [],
            }

            # Run tests for each required category
            for test_category in config["required_tests"]:
                category_result = await self._run_compliance_test_category(
                    jurisdiction, test_category, test_scope
                )
                test_results["test_categories"][test_category] = category_result

                # Aggregate issues
                if category_result.get("issues"):
                    test_results["issues"].extend(category_result["issues"])

            # Calculate overall compliance score
            test_results["overall_score"] = await self._calculate_compliance_score(
                test_results
            )

            # Determine compliance status
            min_coverage = config["minimum_test_coverage"]
            test_results["compliance_status"] = await self._determine_compliance_status(
                test_results, min_coverage
            )

            # Generate recommendations
            test_results["recommendations"] = (
                await self._generate_compliance_recommendations(test_results)
            )

            # Store results
            await self._store_compliance_test_results(test_results)

            # Generate regulatory report if compliant
            if test_results["compliance_status"] in [
                "compliant",
                "conditionally_compliant",
            ]:
                regulatory_report = await self._generate_regulatory_report(
                    jurisdiction, test_results
                )
                test_results["regulatory_report"] = regulatory_report

            return test_results

        except Exception as e:
            self.logger.error(f"Compliance test suite failed for {jurisdiction}: {e}")
            return {"jurisdiction": jurisdiction, "status": "failed", "error": str(e)}

    async def _run_compliance_test_category(
        self, jurisdiction: str, category: str, test_scope: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Run tests for specific compliance category."""
        category_result: Dict[str, Any] = {
            "category": category,
            "tests": [],
            "score": 0.0,
            "status": "pending",
            "issues": [],
        }

        # Get test cases for category
        test_cases = await self._get_compliance_test_cases(jurisdiction, category)

        for test_case in test_cases:
            try:
                test_result = await self._execute_compliance_test(test_case, test_scope)
                category_result["tests"].append(test_result)

                if not test_result.get("passed", True):
                    category_result["issues"].append(
                        {
                            "test_id": test_result.get("test_id"),
                            "severity": test_result.get("severity", "medium"),
                            "description": test_result.get("description", "Test failed"),
                            "evidence": test_result.get("evidence", {}),
                        }
                    )

            except Exception as e:
                self.logger.error(f"Compliance test {test_case['test_id']} failed: {e}")
                category_result["tests"].append(
                    {
                        "test_id": test_case["test_id"],
                        "passed": False,
                        "error": str(e),
                        "severity": "high",
                    }
                )
                category_result["issues"].append(
                    {
                        "test_id": test_case["test_id"],
                        "severity": "high",
                        "description": f"Test execution failed: {e}",
                    }
                )

        # Calculate category score
        tests = category_result["tests"]
        passed_tests = sum(1 for test in tests if test.get("passed", False))
        total_tests = len(tests)
        category_result["score"] = (
            (passed_tests / total_tests * 100) if total_tests > 0 else 0
        )

        # Determine category status
        score = category_result["score"]
        if score >= 95:
            category_result["status"] = "compliant"
        elif score >= 80:
            category_result["status"] = "conditionally_compliant"
        else:
            category_result["status"] = "non_compliant"

        return category_result

    async def _test_responsible_gaming(
        self, test_scope: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Test responsible gaming features and compliance."""
        issues: List[Dict[str, Any]] = []

        # Test self-exclusion
        self_exclusion_test = await self._test_self_exclusion_features()
        if not self_exclusion_test.get("passed", True):
            issues.append(self_exclusion_test)

        # Test deposit limits
        deposit_limits_test = await self._test_deposit_limits()
        if not deposit_limits_test.get("passed", True):
            issues.append(deposit_limits_test)

        # Test time limits
        time_limits_test = await self._test_time_limits()
        if not time_limits_test.get("passed", True):
            issues.append(time_limits_test)

        # Test reality checks
        reality_checks_test = await self._test_reality_checks()
        if not reality_checks_test.get("passed", True):
            issues.append(reality_checks_test)

        return {
            "passed": len(issues) == 0,
            "description": "Responsible gaming compliance testing completed",
            "issues": issues,
            "severity": (
                "high"
                if any(i.get("severity") in ["critical", "high"] for i in issues)
                else "medium"
            ),
        }

    async def _test_self_exclusion_features(self) -> Dict[str, Any]:
        """Test self-exclusion functionality."""
        issues: List[Dict[str, Any]] = []

        test_user_id = f"test_self_exclusion_{uuid.uuid4().hex[:8]}"

        try:
            exclusion_request = {
                "user_id": test_user_id,
                "exclusion_type": "temporary",
                "duration_days": 7,
                "reason": "Test exclusion for compliance verification",
                "jurisdiction": "UK",
            }

            result = await self._submit_self_exclusion_request(exclusion_request)

            if not result.get("success", False):
                issues.append(
                    {
                        "feature": "self_exclusion_submission",
                        "issue": "Self-exclusion request failed",
                        "severity": "critical",
                    }
                )
            else:
                # Test enforcement
                enforced = await self._test_exclusion_enforcement(test_user_id)
                if not enforced:
                    issues.append(
                        {
                            "feature": "self_exclusion_enforcement",
                            "issue": "Self-exclusion not properly enforced",
                            "severity": "critical",
                        }
                    )

        except Exception as e:
            issues.append(
                {
                    "feature": "self_exclusion_system",
                    "issue": f"Self-exclusion system error: {e}",
                    "severity": "high",
                }
            )

        return {
            "passed": len(issues) == 0,
            "description": "Self-exclusion features tested",
            "issues": issues,
            "severity": (
                "critical"
                if any(i["severity"] == "critical" for i in issues)
                else "high"
            ),
        }

    async def generate_compliance_report(
        self, jurisdiction: str, test_results: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Generate regulatory compliance report."""
        config = self.compliance_requirements.get(jurisdiction)

        if not config:
            return {"error": f"Unknown jurisdiction: {jurisdiction}"}

        now = datetime.now(timezone.utc)

        return {
            "jurisdiction": jurisdiction,
            "regulator": config["regulator"],
            "report_date": now.isoformat(),
            "test_period": {
                "start": test_results.get("start_time"),
                "end": now.isoformat(),
            },
            "overall_compliance_status": test_results.get("compliance_status"),
            "compliance_score": test_results.get("overall_score"),
            "executive_summary": self._generate_executive_summary(test_results),
        }

    def _generate_executive_summary(self, test_results: Dict[str, Any]) -> str:
        """Generate executive summary for compliance report."""
        categories = test_results.get("test_categories", {})
        total_tests = sum(len(cat.get("tests", [])) for cat in categories.values())
        passed_tests = sum(
            sum(1 for t in cat.get("tests", []) if t.get("passed", False))
            for cat in categories.values()
        )
        issues = test_results.get("issues", [])

        return f"""
## Compliance Assessment Overview
- **Test Suite ID**: {test_results.get('test_suite_id')}
- **Jurisdiction**: {test_results.get('jurisdiction')}
- **Regulator**: {test_results.get('regulator')}
- **Overall Score**: {test_results.get('overall_score', 0):.1f}%
- **Status**: {test_results.get('compliance_status', 'unknown').upper()}

## Test Execution Summary
- **Total Tests**: {total_tests}
- **Passed**: {passed_tests}
- **Failed**: {total_tests - passed_tests}
- **Critical Issues**: {sum(1 for i in issues if i.get('severity') == 'critical')}
- **High Priority**: {sum(1 for i in issues if i.get('severity') == 'high')}
"""

    # Placeholder implementations
    async def _get_compliance_test_cases(
        self, jurisdiction: str, category: str
    ) -> List[Dict[str, Any]]:
        """Get test cases for compliance category."""
        return [
            {
                "test_id": f"{category}_test_1",
                "test_type": category,
                "jurisdiction": jurisdiction,
            }
        ]

    async def _execute_compliance_test(
        self, test_case: Dict[str, Any], test_scope: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute individual compliance test."""
        return {
            "test_id": test_case["test_id"],
            "passed": True,
            "description": "Test passed",
        }

    async def _calculate_compliance_score(
        self, test_results: Dict[str, Any]
    ) -> float:
        """Calculate overall compliance score."""
        categories = test_results.get("test_categories", {})
        if not categories:
            return 0.0
        scores = [cat.get("score", 0) for cat in categories.values()]
        return sum(scores) / len(scores) if scores else 0.0

    async def _determine_compliance_status(
        self, test_results: Dict[str, Any], min_coverage: float
    ) -> str:
        """Determine overall compliance status."""
        score = test_results.get("overall_score", 0)
        if score >= min_coverage * 100:
            return "compliant"
        elif score >= 80:
            return "conditionally_compliant"
        return "non_compliant"

    async def _generate_compliance_recommendations(
        self, test_results: Dict[str, Any]
    ) -> List[str]:
        """Generate compliance recommendations."""
        recommendations: List[str] = []
        issues = test_results.get("issues", [])

        critical = [i for i in issues if i.get("severity") == "critical"]
        high = [i for i in issues if i.get("severity") == "high"]

        if critical:
            recommendations.append(
                f"URGENT: Address {len(critical)} critical compliance issues"
            )
        if high:
            recommendations.append(f"Review {len(high)} high-priority issues")

        return recommendations

    async def _store_compliance_test_results(
        self, test_results: Dict[str, Any]
    ) -> None:
        """Store compliance test results."""
        pass

    async def _generate_regulatory_report(
        self, jurisdiction: str, test_results: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Generate regulatory report."""
        return await self.generate_compliance_report(jurisdiction, test_results)

    async def _test_deposit_limits(self) -> Dict[str, Any]:
        """Test deposit limit functionality."""
        return {"passed": True}

    async def _test_time_limits(self) -> Dict[str, Any]:
        """Test time limit functionality."""
        return {"passed": True}

    async def _test_reality_checks(self) -> Dict[str, Any]:
        """Test reality check functionality."""
        return {"passed": True}

    async def _submit_self_exclusion_request(
        self, request: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Submit self-exclusion request."""
        return {"success": True}

    async def _test_exclusion_enforcement(self, user_id: str) -> bool:
        """Test if exclusion is enforced."""
        return True
