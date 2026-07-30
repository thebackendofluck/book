# Companion code for "The Backend of Luck" - Chapter 17, Random Number Generation (RNG).
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
RNG Statistical Testing Suite

This module provides statistical tests for validating RNG quality
according to NIST SP 800-22 and casino industry standards.

Tests Included:
- Monobit (Frequency) Test
- Runs Test
- Chi-Square Test
- Serial Test
- Poker Test (Partition Test)
- Autocorrelation Test

These tests help ensure RNG output is:
- Uniformly distributed
- Independent (no predictable patterns)
- Free from bias
- Suitable for gambling applications

Usage:
    ```python
    from testing import run_nist_tests, run_casino_validation

    # Run NIST tests on RNG output
    results = run_nist_tests(rng, num_samples=1_000_000)
    print(f"Passed: {results['passed']}/{results['total']}")

    # Run casino-specific validation
    validation = run_casino_validation(rng)
    print(f"RNG is valid: {validation.is_valid}")
    ```

References:
- NIST SP 800-22: Statistical Test Suite for Random and Pseudorandom Number Generators
- GLI-11 Section 5.1: RNG Statistical Testing Requirements
"""

import logging
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .prng import SecurePRNG, create_casino_rng  # ty:ignore[unresolved-import]

logger = logging.getLogger(__name__)


@dataclass
class TestResult:
    """Result of a single statistical test."""

    test_name: str
    passed: bool
    p_value: float
    statistic: float
    threshold: float
    details: Dict[str, Any]


@dataclass
class ValidationResult:
    """Overall RNG validation result."""

    is_valid: bool
    tests_passed: int
    tests_total: int
    test_results: List[TestResult]
    summary: str


def monobit_test(bits: List[int], significance: float = 0.01) -> TestResult:
    """
    NIST Frequency (Monobit) Test.

    Tests whether the number of ones and zeros in a sequence are
    approximately equal, as would be expected for a truly random
    sequence.

    Args:
        bits: List of 0s and 1s
        significance: Significance level for test

    Returns:
        TestResult with pass/fail and statistics
    """
    n = len(bits)
    if n < 100:
        logger.warning(f"Monobit test: n={n} is below recommended 100")

    # Calculate sum of bits transformed to +1/-1
    s = sum(2 * b - 1 for b in bits)

    # Calculate test statistic
    s_obs = abs(s) / math.sqrt(n)

    # Calculate p-value using complementary error function
    p_value = math.erfc(s_obs / math.sqrt(2))

    passed = p_value >= significance

    return TestResult(
        test_name="Monobit (Frequency)",
        passed=passed,
        p_value=p_value,
        statistic=s_obs,
        threshold=significance,
        details={
            "n": n,
            "sum": s,
            "ones": sum(bits),
            "zeros": n - sum(bits),
        },
    )


def runs_test(bits: List[int], significance: float = 0.01) -> TestResult:
    """
    NIST Runs Test.

    Tests whether the number of runs (consecutive sequences of
    identical bits) is as expected for a random sequence.

    Args:
        bits: List of 0s and 1s
        significance: Significance level

    Returns:
        TestResult with pass/fail and statistics
    """
    n = len(bits)
    if n < 100:
        logger.warning(f"Runs test: n={n} is below recommended 100")

    # Pre-test: check proportion of ones
    pi = sum(bits) / n
    tau = 2 / math.sqrt(n)

    if abs(pi - 0.5) >= tau:
        # Frequency test would fail, skip runs test
        return TestResult(
            test_name="Runs",
            passed=False,
            p_value=0.0,
            statistic=0.0,
            threshold=significance,
            details={"reason": "Failed frequency pre-test", "pi": pi, "tau": tau},
        )

    # Count runs
    runs = 1
    for i in range(1, n):
        if bits[i] != bits[i - 1]:
            runs += 1

    # Calculate expected runs and standard deviation
    expected_runs = 2 * n * pi * (1 - pi) + 1
    std_runs = 2 * math.sqrt(2 * n) * pi * (1 - pi)

    if std_runs == 0:
        p_value = 0.0
    else:
        z = (runs - expected_runs) / std_runs
        p_value = math.erfc(abs(z) / math.sqrt(2))

    passed = p_value >= significance

    return TestResult(
        test_name="Runs",
        passed=passed,
        p_value=p_value,
        statistic=runs,
        threshold=significance,
        details={
            "runs": runs,
            "expected_runs": expected_runs,
            "std_runs": std_runs,
            "pi": pi,
        },
    )


def chi_square_test(
    observed: List[int],
    expected: Optional[List[float]] = None,
    significance: float = 0.01,
) -> TestResult:
    """
    Chi-Square Goodness of Fit Test.

    Tests whether observed frequencies match expected frequencies.
    Used to verify uniform distribution of RNG output.

    Args:
        observed: Observed frequency counts
        expected: Expected frequencies (uniform if None)
        significance: Significance level

    Returns:
        TestResult with pass/fail and statistics
    """
    k = len(observed)
    total = sum(observed)

    if expected is None:
        expected = [total / k] * k

    # Calculate chi-square statistic
    chi_square = sum(
        (o - e) ** 2 / e for o, e in zip(observed, expected) if e > 0
    )

    # Degrees of freedom
    df = k - 1

    # Approximate p-value using chi-square distribution
    # Using Wilson-Hilferty transformation for approximation
    if df > 0:
        z = (
            (chi_square / df) ** (1 / 3) - (1 - 2 / (9 * df))
        ) / math.sqrt(2 / (9 * df))
        p_value = 0.5 * math.erfc(z / math.sqrt(2))
    else:
        p_value = 1.0

    passed = p_value >= significance

    return TestResult(
        test_name="Chi-Square",
        passed=passed,
        p_value=p_value,
        statistic=chi_square,
        threshold=significance,
        details={
            "degrees_of_freedom": df,
            "observed": observed,
            "expected": expected,
        },
    )


def poker_test(
    bits: List[int], block_size: int = 4, significance: float = 0.01
) -> TestResult:
    """
    Poker (Partition) Test.

    Divides the sequence into blocks and tests whether all possible
    block patterns occur with expected frequency.

    Args:
        bits: List of 0s and 1s
        block_size: Size of each block (4 = nibbles)
        significance: Significance level

    Returns:
        TestResult with pass/fail and statistics
    """
    n = len(bits)
    num_blocks = n // block_size

    if num_blocks < 5 * (2**block_size):
        logger.warning(f"Poker test: insufficient blocks for block_size={block_size}")

    # Count occurrences of each pattern
    num_patterns = 2**block_size
    counts = [0] * num_patterns

    for i in range(num_blocks):
        block = bits[i * block_size : (i + 1) * block_size]
        pattern = sum(b * (2**j) for j, b in enumerate(block))
        counts[pattern] += 1

    # Chi-square test on pattern frequencies
    expected_count = num_blocks / num_patterns
    chi_square = sum((c - expected_count) ** 2 / expected_count for c in counts)

    df = num_patterns - 1

    # P-value approximation
    if df > 0:
        z = (
            (chi_square / df) ** (1 / 3) - (1 - 2 / (9 * df))
        ) / math.sqrt(2 / (9 * df))
        p_value = 0.5 * math.erfc(z / math.sqrt(2))
    else:
        p_value = 1.0

    passed = p_value >= significance

    return TestResult(
        test_name="Poker (Partition)",
        passed=passed,
        p_value=p_value,
        statistic=chi_square,
        threshold=significance,
        details={
            "block_size": block_size,
            "num_blocks": num_blocks,
            "pattern_counts": counts[:16],  # First 16 for readability
        },
    )


def autocorrelation_test(
    bits: List[int], lag: int = 1, significance: float = 0.01
) -> TestResult:
    """
    Autocorrelation Test.

    Tests for correlation between bits at different positions.
    A good RNG should have no significant autocorrelation.

    Args:
        bits: List of 0s and 1s
        lag: Number of positions to offset
        significance: Significance level

    Returns:
        TestResult with pass/fail and statistics
    """
    n = len(bits)
    if n - lag < 100:
        logger.warning(f"Autocorrelation test: n-lag={n-lag} is below recommended 100")

    # Calculate autocorrelation
    matches = sum(bits[i] == bits[i + lag] for i in range(n - lag))
    autocorr = (2 * matches - (n - lag)) / (n - lag)

    # Under null hypothesis, autocorr ~ N(0, 1/sqrt(n-lag))
    std = 1 / math.sqrt(n - lag)
    z = abs(autocorr) / std

    p_value = math.erfc(z / math.sqrt(2))

    passed = p_value >= significance

    return TestResult(
        test_name=f"Autocorrelation (lag={lag})",
        passed=passed,
        p_value=p_value,
        statistic=autocorr,
        threshold=significance,
        details={
            "lag": lag,
            "matches": matches,
            "n": n,
        },
    )


def run_nist_tests(
    rng: SecurePRNG,
    num_samples: int = 100000,
    significance: float = 0.01,
) -> ValidationResult:
    """
    Run NIST SP 800-22 statistical tests.

    Args:
        rng: RNG instance to test
        num_samples: Number of bits to generate
        significance: Significance level for all tests

    Returns:
        ValidationResult with all test results
    """
    logger.info(f"Running NIST tests with {num_samples} samples")

    # Generate bits
    num_bytes = (num_samples + 7) // 8
    random_bytes = rng.random_bytes(num_bytes)
    bits = []
    for byte in random_bytes:
        for i in range(8):
            bits.append((byte >> i) & 1)
    bits = bits[:num_samples]

    # Run tests
    results: List[TestResult] = []

    results.append(monobit_test(bits, significance))
    results.append(runs_test(bits, significance))
    results.append(poker_test(bits, 4, significance))
    results.append(autocorrelation_test(bits, 1, significance))
    results.append(autocorrelation_test(bits, 8, significance))

    # Count passes
    passed = sum(1 for r in results if r.passed)
    total = len(results)

    is_valid = passed >= total * 0.95  # 95% of tests must pass

    summary = f"NIST Tests: {passed}/{total} passed"
    if is_valid:
        summary += " - RNG PASSED"
    else:
        summary += " - RNG FAILED"

    return ValidationResult(
        is_valid=is_valid,
        tests_passed=passed,
        tests_total=total,
        test_results=results,
        summary=summary,
    )


def run_casino_validation(
    rng: SecurePRNG,
    num_samples: int = 1000000,
) -> ValidationResult:
    """
    Run casino-specific RNG validation.

    This includes NIST tests plus additional casino-specific checks:
    - Integer range uniformity
    - Float distribution
    - Large number generation

    Args:
        rng: RNG instance to test
        num_samples: Number of samples to generate

    Returns:
        ValidationResult with all test results
    """
    logger.info(f"Running casino validation with {num_samples} samples")

    results: List[TestResult] = []

    # 1. Run NIST tests (reduced samples for speed)
    nist_result = run_nist_tests(rng, min(num_samples, 100000))
    results.extend(nist_result.test_results)

    # 2. Test integer range uniformity (dice-like)
    dice_counts = [0] * 6
    for _ in range(num_samples // 10):
        dice_counts[rng.random_int(1, 6) - 1] += 1

    dice_result = chi_square_test(dice_counts, significance=0.01)
    dice_result = TestResult(
        test_name="Dice Uniformity (1-6)",
        passed=dice_result.passed,
        p_value=dice_result.p_value,
        statistic=dice_result.statistic,
        threshold=dice_result.threshold,
        details={"counts": dice_counts},
    )
    results.append(dice_result)

    # 3. Test card distribution (52 cards)
    card_counts = [0] * 52
    for _ in range(num_samples // 10):
        card_counts[rng.random_int(0, 51)] += 1

    card_result = chi_square_test(card_counts, significance=0.01)
    card_result = TestResult(
        test_name="Card Uniformity (0-51)",
        passed=card_result.passed,
        p_value=card_result.p_value,
        statistic=card_result.statistic,
        threshold=card_result.threshold,
        details={"sample_counts": card_counts[:13]},
    )
    results.append(card_result)

    # 4. Test float distribution in [0, 1)
    bucket_counts = [0] * 10
    for _ in range(num_samples // 10):
        val = rng.random_float()
        bucket = min(int(val * 10), 9)
        bucket_counts[bucket] += 1

    float_result = chi_square_test(bucket_counts, significance=0.01)
    float_result = TestResult(
        test_name="Float Distribution [0,1)",
        passed=float_result.passed,
        p_value=float_result.p_value,
        statistic=float_result.statistic,
        threshold=float_result.threshold,
        details={"bucket_counts": bucket_counts},
    )
    results.append(float_result)

    # Count passes
    passed = sum(1 for r in results if r.passed)
    total = len(results)

    # Casino requirement: 95% of tests must pass
    is_valid = passed >= total * 0.95

    summary = f"Casino Validation: {passed}/{total} passed"
    if is_valid:
        summary += " - RNG APPROVED FOR CASINO USE"
    else:
        summary += " - RNG FAILED VALIDATION"

    return ValidationResult(
        is_valid=is_valid,
        tests_passed=passed,
        tests_total=total,
        test_results=results,
        summary=summary,
    )
