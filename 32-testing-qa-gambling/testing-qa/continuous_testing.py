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
Continuous Testing Framework for iGaming Platforms

Implements continuous testing strategies across the development lifecycle:
- Pre-commit: Unit tests, security linting, code quality
- Post-commit: Integration tests, API contract tests
- Pre-deployment: Regression suite, security scanning
- Post-deployment: Smoke tests, monitoring validation
- Production: Synthetic monitoring, user journey tests

Integration Points:
- CI/CD pipelines (GitHub Actions, GitLab CI, Jenkins)
- Quality gates and automated rollback
- Test result aggregation and reporting
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List


class ContinuousTestingFramework:
    """
    Continuous testing implementation for gambling platforms.

    Provides automated testing at every stage of the development
    lifecycle with quality gates and automated notifications.

    Example:
        >>> framework = ContinuousTestingFramework(redis_client)
        >>> await framework.implement_continuous_testing()
        >>> result = await framework.run_regression_test_suite({
        ...     "include_unit_tests": True,
        ...     "include_integration_tests": True
        ... })
    """

    def __init__(
        self, redis_client: Any, ci_cd_integration: bool = True
    ) -> None:
        self.redis = redis_client
        self.ci_cd_integration = ci_cd_integration
        self.test_suites: Dict[str, Any] = {}
        self.test_metrics: Dict[str, Any] = {}
        self.logger = logging.getLogger(__name__)

    async def implement_continuous_testing(self) -> None:
        """Implement continuous testing across development lifecycle."""

        testing_stages: Dict[str, Dict[str, Any]] = {
            "pre_commit": {
                "trigger": "git_pre_commit_hook",
                "tests": ["unit_tests", "security_linting", "code_quality"],
                "timeout": 300,  # 5 minutes
                "failure_action": "block_commit",
            },
            "post_commit": {
                "trigger": "git_push",
                "tests": [
                    "integration_tests",
                    "api_contract_tests",
                    "performance_smoke_tests",
                ],
                "timeout": 1800,  # 30 minutes
                "failure_action": "notify_team",
            },
            "pre_deployment": {
                "trigger": "deployment_request",
                "tests": [
                    "full_regression_suite",
                    "security_scanning",
                    "compliance_checks",
                ],
                "timeout": 7200,  # 2 hours
                "failure_action": "block_deployment",
            },
            "post_deployment": {
                "trigger": "deployment_complete",
                "tests": ["smoke_tests", "monitoring_validation", "chaos_tests"],
                "timeout": 600,  # 10 minutes
                "failure_action": "rollback",
            },
            "production_monitoring": {
                "trigger": "continuous",
                "tests": [
                    "synthetic_monitoring",
                    "user_journey_tests",
                    "security_monitoring",
                ],
                "timeout": 0,  # Continuous
                "failure_action": "alert_on_call",
            },
        }

        # Set up test automation for each stage
        for stage, config in testing_stages.items():
            await self._setup_test_automation(stage, config)

        # Set up test result aggregation
        await self._setup_test_result_aggregation()

        # Set up quality gates
        await self._setup_quality_gates()

    async def run_regression_test_suite(
        self, test_scope: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Run comprehensive regression test suite."""

        regression_suite: Dict[str, Any] = {
            "test_id": f"regression_{int(time.time())}",
            "scope": test_scope,
            "start_time": datetime.now(timezone.utc).isoformat(),
            "test_categories": {},
            "overall_result": "pending",
            "coverage": 0.0,
            "execution_time": 0.0,
            "failed_tests": [],
            "flaky_tests": [],
        }

        # Run test categories in parallel
        test_categories = [
            "unit_tests",
            "integration_tests",
            "api_tests",
            "ui_tests",
            "performance_tests",
            "security_tests",
            "compliance_tests",
        ]

        # Execute tests concurrently
        test_tasks: List[tuple[str, asyncio.Task[Dict[str, Any]]]] = []
        for category in test_categories:
            if test_scope.get(f"include_{category}", True):
                task = asyncio.create_task(
                    self._run_test_category(category, test_scope)
                )
                test_tasks.append((category, task))

        # Collect results
        for category, task in test_tasks:
            try:
                result = await task
                regression_suite["test_categories"][category] = result

                if not result.get("passed", True):
                    failed = result.get("failed_tests", [])
                    regression_suite["failed_tests"].extend(failed)

                flaky = result.get("flaky_tests", [])
                if flaky:
                    regression_suite["flaky_tests"].extend(flaky)

            except Exception as e:
                regression_suite["test_categories"][category] = {
                    "passed": False,
                    "error": str(e),
                    "execution_time": 0,
                }

        # Calculate overall result
        all_passed = all(
            cat.get("passed", False)
            for cat in regression_suite["test_categories"].values()
        )
        regression_suite["overall_result"] = "passed" if all_passed else "failed"

        # Calculate coverage
        regression_suite["coverage"] = await self._calculate_test_coverage(
            regression_suite
        )

        # Calculate execution time
        start = datetime.fromisoformat(regression_suite["start_time"])
        end = datetime.now(timezone.utc)
        regression_suite["execution_time"] = (end - start).total_seconds()

        # Store results
        await self._store_regression_results(regression_suite)

        # Generate notifications
        if regression_suite["overall_result"] == "failed":
            await self._notify_regression_failure(regression_suite)

        return regression_suite

    async def _run_test_category(
        self, category: str, test_scope: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Run tests for specific category."""

        category_config: Dict[str, Dict[str, Any]] = {
            "unit_tests": {
                "parallel_execution": True,
                "timeout": 600,
                "retry_count": 2,
                "test_discovery": "test_*.py",
            },
            "integration_tests": {
                "parallel_execution": True,
                "timeout": 1200,
                "retry_count": 1,
                "test_discovery": "integration_*.py",
            },
            "api_tests": {
                "parallel_execution": True,
                "timeout": 900,
                "retry_count": 2,
                "test_discovery": "api_*.py",
            },
            "ui_tests": {
                "parallel_execution": False,
                "timeout": 1800,
                "retry_count": 1,
                "test_discovery": "ui_*.py",
            },
            "performance_tests": {
                "parallel_execution": False,
                "timeout": 3600,
                "retry_count": 0,
                "test_discovery": "performance_*.py",
            },
            "security_tests": {
                "parallel_execution": True,
                "timeout": 1200,
                "retry_count": 1,
                "test_discovery": "security_*.py",
            },
            "compliance_tests": {
                "parallel_execution": True,
                "timeout": 1800,
                "retry_count": 1,
                "test_discovery": "compliance_*.py",
            },
        }

        config = category_config.get(category, {})
        start_time = time.time()

        try:
            # Discover tests
            test_files = await self._discover_test_files(
                category, config.get("test_discovery", "test_*.py")
            )

            if not test_files:
                return {
                    "passed": True,
                    "message": f"No {category} tests found",
                    "execution_time": time.time() - start_time,
                    "test_count": 0,
                    "failed_tests": [],
                }

            # Execute tests
            if config.get("parallel_execution", True):
                test_results = await self._run_tests_parallel(test_files, config)
            else:
                test_results = await self._run_tests_sequential(test_files, config)

            # Retry failed tests if configured
            retry_count = config.get("retry_count", 0)
            failed_tests = test_results.get("failed_tests", [])
            if retry_count > 0 and failed_tests:
                retry_results = await self._retry_failed_tests(
                    failed_tests, retry_count
                )
                test_results = self._merge_test_results(test_results, retry_results)

            execution_time = time.time() - start_time

            return {
                "passed": len(test_results.get("failed_tests", [])) == 0,
                "execution_time": execution_time,
                "test_count": len(test_files),
                "failed_tests": test_results.get("failed_tests", []),
                "flaky_tests": test_results.get("flaky_tests", []),
                "coverage": test_results.get("coverage", 0),
            }

        except Exception as e:
            return {
                "passed": False,
                "error": str(e),
                "execution_time": time.time() - start_time,
                "failed_tests": [],
            }

    async def run_smoke_tests(self) -> Dict[str, Any]:
        """Run post-deployment smoke tests."""
        smoke_tests = [
            {"name": "homepage_load", "endpoint": "/", "expected_status": 200},
            {"name": "api_health", "endpoint": "/api/health", "expected_status": 200},
            {"name": "login_page", "endpoint": "/login", "expected_status": 200},
            {"name": "game_lobby", "endpoint": "/games", "expected_status": 200},
        ]

        results: Dict[str, Any] = {
            "test_id": f"smoke_{int(time.time())}",
            "tests": [],
            "passed": True,
            "execution_time": 0,
        }

        start_time = time.time()

        for test in smoke_tests:
            test_result = await self._execute_smoke_test(test)
            results["tests"].append(test_result)
            if not test_result.get("passed", True):
                results["passed"] = False

        results["execution_time"] = time.time() - start_time

        return results

    async def run_chaos_tests(self) -> Dict[str, Any]:
        """Run chaos engineering tests post-deployment."""
        chaos_scenarios = [
            "pod_failure",
            "network_latency",
            "database_disconnect",
            "cache_eviction",
            "cpu_stress",
        ]

        results: Dict[str, Any] = {
            "test_id": f"chaos_{int(time.time())}",
            "scenarios": [],
            "passed": True,
            "system_resilience_score": 0.0,
        }

        for scenario in chaos_scenarios:
            scenario_result = await self._execute_chaos_scenario(scenario)
            results["scenarios"].append(scenario_result)
            if not scenario_result.get("recovered", True):
                results["passed"] = False

        # Calculate resilience score
        recovered = sum(1 for s in results["scenarios"] if s.get("recovered", False))
        results["system_resilience_score"] = (
            (recovered / len(chaos_scenarios)) * 100 if chaos_scenarios else 0
        )

        return results

    def generate_test_report(self, test_results: Dict[str, Any]) -> str:
        """Generate comprehensive test report."""
        categories = test_results.get("test_categories", {})

        total_tests = sum(cat.get("test_count", 0) for cat in categories.values())
        failed_count = len(test_results.get("failed_tests", []))
        flaky_count = len(test_results.get("flaky_tests", []))

        report = f"""# Regression Test Report

## Summary
- **Test ID**: {test_results.get('test_id')}
- **Result**: {test_results.get('overall_result', 'unknown').upper()}
- **Coverage**: {test_results.get('coverage', 0):.1f}%
- **Execution Time**: {test_results.get('execution_time', 0):.1f}s

## Test Categories
| Category | Passed | Tests | Time |
|----------|--------|-------|------|
"""
        for cat_name, cat_data in categories.items():
            passed = "Yes" if cat_data.get("passed", False) else "No"
            count = cat_data.get("test_count", 0)
            exec_time = cat_data.get("execution_time", 0)
            report += f"| {cat_name} | {passed} | {count} | {exec_time:.1f}s |\n"

        report += f"""
## Statistics
- **Total Tests**: {total_tests}
- **Failed Tests**: {failed_count}
- **Flaky Tests**: {flaky_count}
"""
        return report

    # Placeholder implementations
    async def _setup_test_automation(
        self, stage: str, config: Dict[str, Any]
    ) -> None:
        """Set up test automation for stage."""
        self.test_suites[stage] = config

    async def _setup_test_result_aggregation(self) -> None:
        """Set up test result aggregation."""
        pass

    async def _setup_quality_gates(self) -> None:
        """Set up quality gates."""
        pass

    async def _discover_test_files(
        self, category: str, pattern: str
    ) -> List[str]:
        """Discover test files for category."""
        return [f"{category}_test_1.py", f"{category}_test_2.py"]

    async def _run_tests_parallel(
        self, test_files: List[str], config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Run tests in parallel."""
        return {"failed_tests": [], "flaky_tests": [], "coverage": 85.0}

    async def _run_tests_sequential(
        self, test_files: List[str], config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Run tests sequentially."""
        return {"failed_tests": [], "flaky_tests": [], "coverage": 85.0}

    async def _retry_failed_tests(
        self, failed_tests: List[str], retry_count: int
    ) -> Dict[str, Any]:
        """Retry failed tests."""
        return {"failed_tests": [], "flaky_tests": failed_tests}

    def _merge_test_results(
        self, original: Dict[str, Any], retry: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Merge test results after retry."""
        return {
            "failed_tests": retry.get("failed_tests", []),
            "flaky_tests": original.get("flaky_tests", [])
            + retry.get("flaky_tests", []),
            "coverage": original.get("coverage", 0),
        }

    async def _calculate_test_coverage(
        self, regression_suite: Dict[str, Any]
    ) -> float:
        """Calculate test coverage."""
        categories = regression_suite.get("test_categories", {})
        if not categories:
            return 0.0
        coverages = [
            cat.get("coverage", 0)
            for cat in categories.values()
            if "coverage" in cat
        ]
        return sum(coverages) / len(coverages) if coverages else 0.0

    async def _store_regression_results(
        self, regression_suite: Dict[str, Any]
    ) -> None:
        """Store regression results."""
        pass

    async def _notify_regression_failure(
        self, regression_suite: Dict[str, Any]
    ) -> None:
        """Notify team of regression failure."""
        self.logger.warning(
            f"Regression test failed: {regression_suite.get('test_id')}"
        )

    async def _execute_smoke_test(
        self, test: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute individual smoke test."""
        return {"name": test["name"], "passed": True, "response_time": 50}

    async def _execute_chaos_scenario(
        self, scenario: str
    ) -> Dict[str, Any]:
        """Execute chaos test scenario."""
        return {"scenario": scenario, "recovered": True, "recovery_time": 30}
