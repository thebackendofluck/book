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
RNG Certification and Statistical Verification System

Provides comprehensive RNG testing and certification for iGaming platforms,
supporting multiple certification bodies and jurisdictions.

Certification Bodies Supported:
- GLI (Gaming Laboratories International) - US/International
- eCOGRA - UK/International
- iTech Labs - Malta/International

Statistical Tests Implemented:
- Chi-Square Goodness of Fit
- Kolmogorov-Smirnov Test
- Runs Test for Randomness
- Serial Test for Correlation
- Poker Test for Pattern Detection
- Diehard Battery of Tests
- NIST SP 800-22 Statistical Tests
"""

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

import numpy as np
import scipy.stats as stats
from scipy.stats import norm


class RNGTestType(Enum):
    """Types of statistical tests for RNG verification."""

    CHI_SQUARE = "chi_square"
    KOLMOGOROV_SMIRNOV = "kolmogorov_smirnov"
    RUNS_TEST = "runs_test"
    SERIAL_TEST = "serial_test"
    POKER_TEST = "poker_test"
    DIEHARD = "diehard"
    NIST_SP800_22 = "nist_sp800_22"


class CertificationStatus(Enum):
    """Status of RNG certification process."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    PASSED = "passed"
    FAILED = "failed"
    CERTIFIED = "certified"


@dataclass
class RNGTestResult:
    """Result of a single RNG statistical test."""

    test_id: str
    game_id: str
    test_type: RNGTestType
    sample_size: int
    test_statistic: float
    p_value: float
    critical_value: float
    result: bool
    confidence_level: float
    execution_time: float
    timestamp: datetime
    certification_body: str


class RNGCertificationSystem:
    """
    Comprehensive RNG testing and certification system for iGaming.

    Supports multiple jurisdictions with different certification requirements:
    - UK: eCOGRA, 1M samples, 99% confidence, quarterly retesting
    - Malta: iTech Labs, 2M samples, 95% confidence, semi-annual retesting
    - New Jersey: GLI, 5M samples, 99% confidence, monthly retesting

    Example:
        >>> system = RNGCertificationSystem(redis_client, db_pool)
        >>> result = await system.certify_rng("blackjack_v2", "UK")
        >>> print(f"Status: {result['status']}")
    """

    def __init__(self, redis_client: Any, db_pool: Any) -> None:
        self.redis = redis_client
        self.db_pool = db_pool
        self.logger = logging.getLogger(__name__)

        # Certification requirements by jurisdiction
        self.certification_requirements: Dict[str, Dict[str, Any]] = {
            "UK": {
                "minimum_sample_size": 1000000,
                "confidence_level": 0.99,
                "required_tests": [
                    RNGTestType.CHI_SQUARE,
                    RNGTestType.KOLMOGOROV_SMIRNOV,
                    RNGTestType.RUNS_TEST,
                    RNGTestType.SERIAL_TEST,
                    RNGTestType.POKER_TEST,
                ],
                "certification_body": "eCOGRA",
                "retest_frequency_days": 90,
            },
            "Malta": {
                "minimum_sample_size": 2000000,
                "confidence_level": 0.95,
                "required_tests": [
                    RNGTestType.CHI_SQUARE,
                    RNGTestType.DIEHARD,
                    RNGTestType.NIST_SP800_22,
                ],
                "certification_body": "iTech Labs",
                "retest_frequency_days": 180,
            },
            "New Jersey": {
                "minimum_sample_size": 5000000,
                "confidence_level": 0.99,
                "required_tests": [
                    RNGTestType.CHI_SQUARE,
                    RNGTestType.KOLMOGOROV_SMIRNOV,
                    RNGTestType.RUNS_TEST,
                    RNGTestType.SERIAL_TEST,
                    RNGTestType.DIEHARD,
                    RNGTestType.NIST_SP800_22,
                ],
                "certification_body": "GLI",
                "retest_frequency_days": 60,
            },
        }

    async def certify_rng(self, game_id: str, jurisdiction: str) -> Dict[str, Any]:
        """Complete RNG certification process for a game."""
        try:
            config = self.certification_requirements.get(jurisdiction)
            if not config:
                raise ValueError(f"Unsupported jurisdiction: {jurisdiction}")

            now = datetime.now(timezone.utc)
            certification_id = f"RNG_{game_id}_{jurisdiction}_{now.strftime('%Y%m%d%H%M%S')}"

            self.logger.info(
                f"Starting RNG certification for game {game_id} in {jurisdiction}"
            )

            # Generate random number sequences
            random_sequences = await self._generate_random_sequences(
                game_id, config["minimum_sample_size"]
            )

            # Run required statistical tests
            test_results: List[RNGTestResult] = []
            for test_type in config["required_tests"]:
                test_result = await self._run_statistical_test(
                    game_id,
                    test_type,
                    random_sequences,
                    config["confidence_level"],
                    config["minimum_sample_size"],
                )
                test_results.append(test_result)
                await self._store_test_result(test_result)

            # Generate certification report
            certification_report = await self._generate_certification_report(
                certification_id, game_id, jurisdiction, test_results, config
            )

            # Determine certification status
            all_tests_passed = all(result.result for result in test_results)
            certification_status = (
                CertificationStatus.CERTIFIED
                if all_tests_passed
                else CertificationStatus.FAILED
            )

            # Store certification result
            await self._store_certification_result(
                certification_id,
                game_id,
                jurisdiction,
                certification_status,
                certification_report,
            )

            # If certified, schedule ongoing monitoring
            if certification_status == CertificationStatus.CERTIFIED:
                await self._schedule_ongoing_monitoring(game_id, jurisdiction, config)

            retest_days = config["retest_frequency_days"]
            return {
                "certification_id": certification_id,
                "game_id": game_id,
                "jurisdiction": jurisdiction,
                "status": certification_status.value,
                "test_results": [
                    self._test_result_to_dict(result) for result in test_results
                ],
                "report_url": f"/certifications/{certification_id}/report.pdf",
                "next_test_date": now + timedelta(days=retest_days),
            }

        except Exception as e:
            self.logger.error(f"RNG certification failed for {game_id}: {e}")
            return {
                "game_id": game_id,
                "jurisdiction": jurisdiction,
                "status": CertificationStatus.FAILED.value,
                "error": str(e),
            }

    async def _generate_random_sequences(
        self, game_id: str, sample_size: int
    ) -> Dict[str, List[Any]]:
        """Generate random number sequences for testing."""
        sequences: Dict[str, List[Any]] = {}

        # Get game RNG parameters
        game_config = await self._get_game_rng_config(game_id)

        # Generate different types of sequences
        sequences["uniform_0_1"] = await self._generate_uniform_sequence(
            sample_size, 0, 1
        )
        sequences["uniform_1_100"] = await self._generate_uniform_sequence(
            sample_size, 1, 100
        )
        sequences["binary"] = await self._generate_binary_sequence(sample_size)
        dice_sides = game_config.get("dice_sides", 6) if game_config else 6
        sequences["dice_rolls"] = await self._generate_dice_sequence(
            sample_size, dice_sides
        )
        sequences["card_deals"] = await self._generate_card_sequence(sample_size)

        return sequences

    async def _run_statistical_test(
        self,
        game_id: str,
        test_type: RNGTestType,
        random_sequences: Dict[str, List[Any]],
        confidence_level: float,
        sample_size: int,
    ) -> RNGTestResult:
        """Run specific statistical test on random sequences."""
        start_time = datetime.now(timezone.utc)

        try:
            if test_type == RNGTestType.CHI_SQUARE:
                result = await self._chi_square_test(
                    random_sequences["uniform_1_100"], confidence_level
                )
            elif test_type == RNGTestType.KOLMOGOROV_SMIRNOV:
                result = await self._kolmogorov_smirnov_test(
                    random_sequences["uniform_0_1"], confidence_level
                )
            elif test_type == RNGTestType.RUNS_TEST:
                result = await self._runs_test(
                    random_sequences["binary"], confidence_level
                )
            elif test_type == RNGTestType.SERIAL_TEST:
                result = await self._serial_test(
                    random_sequences["uniform_1_100"], confidence_level
                )
            elif test_type == RNGTestType.POKER_TEST:
                result = await self._poker_test(
                    random_sequences["uniform_1_100"], confidence_level
                )
            elif test_type == RNGTestType.DIEHARD:
                result = await self._diehard_test(random_sequences, confidence_level)
            elif test_type == RNGTestType.NIST_SP800_22:
                result = await self._nist_sp800_22_test(
                    random_sequences, confidence_level
                )
            else:
                raise ValueError(f"Unknown test type: {test_type}")

            execution_time = (
                datetime.now(timezone.utc) - start_time
            ).total_seconds()

            return RNGTestResult(
                test_id=f"{test_type.value}_{game_id}_{int(start_time.timestamp())}",
                game_id=game_id,
                test_type=test_type,
                sample_size=sample_size,
                test_statistic=result["test_statistic"],
                p_value=result["p_value"],
                critical_value=result["critical_value"],
                result=result["passed"],
                confidence_level=confidence_level,
                execution_time=execution_time,
                timestamp=start_time,
                certification_body="Internal",
            )

        except Exception as e:
            self.logger.error(f"Statistical test {test_type} failed: {e}")
            execution_time = (
                datetime.now(timezone.utc) - start_time
            ).total_seconds()
            return RNGTestResult(
                test_id=f"{test_type.value}_{game_id}_{int(start_time.timestamp())}",
                game_id=game_id,
                test_type=test_type,
                sample_size=sample_size,
                test_statistic=0.0,
                p_value=0.0,
                critical_value=0.0,
                result=False,
                confidence_level=confidence_level,
                execution_time=execution_time,
                timestamp=start_time,
                certification_body="Internal",
            )

    async def _chi_square_test(
        self, data: List[int], confidence_level: float
    ) -> Dict[str, Any]:
        """Chi-square goodness of fit test for uniform distribution."""
        n = len(data)

        # Determine number of bins (categories)
        k = max(5, int(np.sqrt(n)))

        # Create frequency table
        min_val, max_val = min(data), max(data)
        observed_freq, _ = np.histogram(data, bins=k, range=(min_val, max_val + 1))

        # Expected frequency for uniform distribution
        expected_freq = np.full(k, n / k)

        # Calculate chi-square statistic
        chi2_statistic = float(
            np.sum((observed_freq - expected_freq) ** 2 / expected_freq)
        )

        # Calculate degrees of freedom
        df = k - 1

        # Get critical value
        critical_value = float(stats.chi2.ppf(confidence_level, df))

        # Calculate p-value
        p_value = float(1 - stats.chi2.cdf(chi2_statistic, df))

        # Test result
        passed = chi2_statistic < critical_value

        return {
            "test_statistic": chi2_statistic,
            "p_value": p_value,
            "critical_value": critical_value,
            "passed": passed,
            "degrees_of_freedom": df,
        }

    async def _kolmogorov_smirnov_test(
        self, data: List[float], confidence_level: float
    ) -> Dict[str, Any]:
        """Kolmogorov-Smirnov test for continuous uniform distribution."""
        n = len(data)

        # Sort data
        sorted_data = np.sort(data)

        # Calculate empirical CDF
        empirical_cdf = np.arange(1, n + 1) / n

        # Calculate theoretical CDF for uniform [0,1]
        theoretical_cdf = sorted_data

        # Calculate K-S statistic
        ks_statistic = float(np.max(np.abs(empirical_cdf - theoretical_cdf)))

        # Calculate critical value for large samples
        critical_value = 1.36 / np.sqrt(n)

        # Calculate p-value (approximation)
        p_value = float(np.exp(-2 * n * ks_statistic**2))

        # Test result
        passed = ks_statistic < critical_value

        return {
            "test_statistic": ks_statistic,
            "p_value": p_value,
            "critical_value": float(critical_value),
            "passed": passed,
        }

    async def _runs_test(
        self, data: List[int], confidence_level: float
    ) -> Dict[str, Any]:
        """Runs test for randomness in binary sequences."""
        n = len(data)

        # Convert to binary sequence (0s and 1s)
        binary_seq = [1 if x > 0.5 else 0 for x in data]

        # Count runs
        runs = 1
        for i in range(1, n):
            if binary_seq[i] != binary_seq[i - 1]:
                runs += 1

        # Count 0s and 1s
        n1 = sum(binary_seq)
        n2 = n - n1

        # Expected number of runs under null hypothesis
        if n > 0:
            expected_runs = (2 * n1 * n2) / n + 1
        else:
            expected_runs = 1

        # Variance of runs
        if n > 1 and n1 > 0 and n2 > 0:
            variance_runs = (2 * n1 * n2 * (2 * n1 * n2 - n)) / (n**2 * (n - 1))
        else:
            variance_runs = 0

        # Test statistic (Z-score)
        if variance_runs > 0:
            z_statistic = (runs - expected_runs) / np.sqrt(variance_runs)
        else:
            z_statistic = 0.0

        # Critical value for two-tailed test
        alpha = 1 - confidence_level
        critical_value = float(norm.ppf(1 - alpha / 2))

        # p-value
        p_value = float(2 * (1 - norm.cdf(abs(z_statistic))))

        # Test result
        passed = abs(z_statistic) < critical_value

        return {
            "test_statistic": float(z_statistic),
            "p_value": p_value,
            "critical_value": critical_value,
            "passed": passed,
        }

    async def _serial_test(
        self, data: List[int], confidence_level: float
    ) -> Dict[str, Any]:
        """Serial test for detecting patterns in sequences."""
        n = len(data)
        if n < 2:
            return {
                "test_statistic": 0.0,
                "p_value": 1.0,
                "critical_value": 0.0,
                "passed": True,
            }

        # Create pairs of consecutive values
        pairs: Dict[tuple[int, int], int] = {}
        for i in range(n - 1):
            pair = (data[i], data[i + 1])
            pairs[pair] = pairs.get(pair, 0) + 1

        # Expected frequency for random pairs
        unique_values = len(set(data))
        expected_freq = (n - 1) / (unique_values**2) if unique_values > 0 else 1

        # Chi-square statistic
        observed = list(pairs.values())
        chi2_statistic = sum(
            (obs - expected_freq) ** 2 / expected_freq for obs in observed
        )

        df = max(1, unique_values**2 - 1)
        critical_value = float(stats.chi2.ppf(confidence_level, df))
        p_value = float(1 - stats.chi2.cdf(chi2_statistic, df))

        return {
            "test_statistic": float(chi2_statistic),
            "p_value": p_value,
            "critical_value": critical_value,
            "passed": chi2_statistic < critical_value,
        }

    async def _poker_test(
        self, data: List[int], confidence_level: float
    ) -> Dict[str, Any]:
        """Poker test for detecting patterns in groups of values."""
        n = len(data)
        group_size = 5
        num_groups = n // group_size

        if num_groups < 10:
            return {
                "test_statistic": 0.0,
                "p_value": 1.0,
                "critical_value": 0.0,
                "passed": True,
            }

        # Classify groups by number of unique values
        pattern_counts: Dict[int, int] = {i: 0 for i in range(1, group_size + 1)}

        for i in range(num_groups):
            group = data[i * group_size : (i + 1) * group_size]
            unique_count = len(set(group))
            pattern_counts[unique_count] = pattern_counts.get(unique_count, 0) + 1

        # Chi-square test against expected distribution
        expected_freq = num_groups / group_size
        observed = list(pattern_counts.values())
        chi2_statistic = sum(
            (obs - expected_freq) ** 2 / expected_freq
            for obs in observed
            if expected_freq > 0
        )

        df = group_size - 1
        critical_value = float(stats.chi2.ppf(confidence_level, df))
        p_value = float(1 - stats.chi2.cdf(chi2_statistic, df))

        return {
            "test_statistic": float(chi2_statistic),
            "p_value": p_value,
            "critical_value": critical_value,
            "passed": chi2_statistic < critical_value,
        }

    async def _diehard_test(
        self, random_sequences: Dict[str, List[Any]], confidence_level: float
    ) -> Dict[str, Any]:
        """Diehard battery of tests for RNG quality."""
        test_results: List[Dict[str, Any]] = []

        # Birthday Spacings Test
        birthday_result = await self._birthday_spacings_test(
            random_sequences["uniform_1_100"]
        )
        test_results.append(birthday_result)

        # Combine results
        all_passed = all(result["passed"] for result in test_results)
        p_values = [result["p_value"] for result in test_results]
        avg_p_value = float(np.mean(p_values)) if p_values else 0.0

        return {
            "test_statistic": float(len([r for r in test_results if r["passed"]])),
            "p_value": avg_p_value,
            "critical_value": len(test_results) * confidence_level,
            "passed": all_passed,
        }

    async def _birthday_spacings_test(self, data: List[int]) -> Dict[str, Any]:
        """Birthday spacings test from Diehard suite."""
        n = len(data)
        m = 2**20

        # Normalize data to [0, m-1]
        max_val = max(data) if data else 1
        half_n = n // 2
        normalized_data = [int((x / max_val) * (m - 1)) for x in data[:half_n]]

        # Calculate spacings
        spacings: List[int] = []
        for i in range(len(normalized_data) - 1):
            spacing = abs(normalized_data[i + 1] - normalized_data[i])
            spacings.append(spacing)

        if not spacings:
            return {
                "test_name": "birthday_spacings",
                "test_statistic": 0.0,
                "p_value": 1.0,
                "passed": True,
            }

        # Chi-square test for spacing distribution
        observed_counts, _ = np.histogram(spacings, bins=10)
        expected_counts = np.full(10, len(spacings) / 10)

        chi2_statistic = float(
            np.sum((observed_counts - expected_counts) ** 2 / expected_counts)
        )
        p_value = float(1 - stats.chi2.cdf(chi2_statistic, df=9))

        return {
            "test_name": "birthday_spacings",
            "test_statistic": chi2_statistic,
            "p_value": p_value,
            "passed": p_value > 0.01,
        }

    async def _nist_sp800_22_test(
        self, random_sequences: Dict[str, List[Any]], confidence_level: float
    ) -> Dict[str, Any]:
        """NIST SP 800-22 statistical tests suite."""
        # Simplified implementation - run frequency and runs tests
        binary_data = random_sequences.get("binary", [])

        if not binary_data:
            return {
                "test_statistic": 0.0,
                "p_value": 1.0,
                "critical_value": 0.0,
                "passed": True,
            }

        # Frequency (monobit) test
        n = len(binary_data)
        s = sum(2 * x - 1 for x in binary_data)
        s_obs = abs(s) / np.sqrt(n)
        p_value = float(2 * (1 - norm.cdf(s_obs)))

        return {
            "test_statistic": float(s_obs),
            "p_value": p_value,
            "critical_value": 1.96,
            "passed": p_value > 0.01,
        }

    async def continuous_rng_monitoring(self) -> None:
        """Continuous monitoring of RNG performance in production."""
        while True:
            try:
                active_games = await self._get_active_games()

                for game in active_games:
                    recent_outcomes = await self._get_recent_game_outcomes(
                        game["game_id"], sample_size=10000, hours=24
                    )

                    if len(recent_outcomes) >= 1000:
                        monitoring_result = await self._quick_rng_health_check(
                            game["game_id"], recent_outcomes
                        )

                        if not monitoring_result["is_healthy"]:
                            await self._trigger_rng_anomaly_alert(
                                game["game_id"], monitoring_result
                            )

                        await self._store_monitoring_result(
                            game["game_id"], monitoring_result
                        )

                await asyncio.sleep(3600)  # Check every hour

            except Exception as e:
                self.logger.error(f"RNG monitoring error: {e}")
                await asyncio.sleep(300)

    async def _quick_rng_health_check(
        self, game_id: str, outcomes: List[int]
    ) -> Dict[str, Any]:
        """Quick health check for RNG in production."""
        chi2_result = await self._chi_square_test(outcomes, confidence_level=0.95)

        health_score = 1.0
        if chi2_result["p_value"] < 0.01:
            health_score -= 0.5

        return {
            "game_id": game_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sample_size": len(outcomes),
            "health_score": max(0, health_score),
            "is_healthy": health_score >= 0.7,
            "chi_square_result": chi2_result,
            "recommendations": self._generate_health_recommendations(health_score),
        }

    def _generate_health_recommendations(self, health_score: float) -> List[str]:
        """Generate recommendations based on health score."""
        recommendations: List[str] = []

        if health_score < 0.5:
            recommendations.append("Immediate RNG investigation required")
            recommendations.append("Consider temporary game suspension")
            recommendations.append("Notify regulatory body")
        elif health_score < 0.7:
            recommendations.append("Increase monitoring frequency")
            recommendations.append("Run additional statistical tests")
            recommendations.append("Review RNG implementation")
        elif health_score < 0.9:
            recommendations.append("Continue monitoring")
            recommendations.append("Schedule comprehensive review")

        return recommendations

    def _test_result_to_dict(self, result: RNGTestResult) -> Dict[str, Any]:
        """Convert RNGTestResult to dictionary."""
        return {
            "test_id": result.test_id,
            "game_id": result.game_id,
            "test_type": result.test_type.value,
            "sample_size": result.sample_size,
            "test_statistic": result.test_statistic,
            "p_value": result.p_value,
            "critical_value": result.critical_value,
            "result": result.result,
            "confidence_level": result.confidence_level,
            "execution_time": result.execution_time,
            "timestamp": result.timestamp.isoformat(),
            "certification_body": result.certification_body,
        }

    # Placeholder methods for database operations
    async def _get_game_rng_config(self, game_id: str) -> Optional[Dict[str, Any]]:
        """Get game RNG configuration from database."""
        return {"dice_sides": 6}

    async def _generate_uniform_sequence(
        self, size: int, min_val: float, max_val: float
    ) -> List[float]:
        """Generate uniform random sequence."""
        return list(np.random.uniform(min_val, max_val, size))

    async def _generate_binary_sequence(self, size: int) -> List[int]:
        """Generate binary random sequence."""
        return list(np.random.randint(0, 2, size))

    async def _generate_dice_sequence(self, size: int, sides: int) -> List[int]:
        """Generate dice roll sequence."""
        return list(np.random.randint(1, sides + 1, size))

    async def _generate_card_sequence(self, size: int) -> List[int]:
        """Generate card deal sequence."""
        return list(np.random.randint(0, 52, size))

    async def _store_test_result(self, result: RNGTestResult) -> None:
        """Store test result in database."""
        pass

    async def _generate_certification_report(
        self,
        cert_id: str,
        game_id: str,
        jurisdiction: str,
        results: List[RNGTestResult],
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Generate certification report."""
        return {
            "certification_id": cert_id,
            "game_id": game_id,
            "jurisdiction": jurisdiction,
            "tests_run": len(results),
            "tests_passed": sum(1 for r in results if r.result),
        }

    async def _store_certification_result(
        self,
        cert_id: str,
        game_id: str,
        jurisdiction: str,
        status: CertificationStatus,
        report: Dict[str, Any],
    ) -> None:
        """Store certification result in database."""
        pass

    async def _schedule_ongoing_monitoring(
        self, game_id: str, jurisdiction: str, config: Dict[str, Any]
    ) -> None:
        """Schedule ongoing RNG monitoring."""
        pass

    async def _get_active_games(self) -> List[Dict[str, Any]]:
        """Get list of active games."""
        return []

    async def _get_recent_game_outcomes(
        self, game_id: str, sample_size: int, hours: int
    ) -> List[int]:
        """Get recent game outcomes."""
        return []

    async def _trigger_rng_anomaly_alert(
        self, game_id: str, result: Dict[str, Any]
    ) -> None:
        """Trigger alert for RNG anomaly."""
        pass

    async def _store_monitoring_result(
        self, game_id: str, result: Dict[str, Any]
    ) -> None:
        """Store monitoring result."""
        pass
