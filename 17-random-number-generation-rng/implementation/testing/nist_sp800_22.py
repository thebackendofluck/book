#!/usr/bin/env python3
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
NIST SP 800-22 Statistical Test Suite for RNG Validation
=========================================================

GLI-11 Section 4.6 Compliance: RNG Statistical Testing Requirements
- RNG output must pass NIST SP 800-22 statistical tests
- Tests must be run on sufficient sample sizes (minimum 1,000,000 bits)
- P-values must exceed significance level (alpha = 0.01)
- Results must be documented for certification audit

Implemented Tests:
1. Frequency (Monobit) Test
2. Block Frequency Test
3. Runs Test
4. Longest Run of Ones Test
5. Discrete Fourier Transform (Spectral) Test
6. Non-Overlapping Template Matching Test
7. Serial Test
8. Approximate Entropy Test
9. Cumulative Sums (Forward and Reverse) Test

Reference: NIST Special Publication 800-22 Revision 1a
https://csrc.nist.gov/publications/detail/sp/800-22/rev-1a/final

Usage:
    suite = NISTTestSuite()
    results = suite.run_all_tests(bit_string)
    suite.print_report(results)
"""

import json
import logging
import math
import os
import struct
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("rng.nist_tests")


# ---------------------------------------------------------------------------
# Special Functions
# ---------------------------------------------------------------------------

def _erfc(x: float) -> float:
    """Complementary error function (pure Python fallback)."""
    try:
        return math.erfc(x)
    except (AttributeError, ValueError):
        # Abramowitz & Stegun approximation
        t = 1.0 / (1.0 + 0.3275911 * abs(x))
        poly = t * (0.254829592 + t * (-0.284496736 + t * (
            1.421413741 + t * (-1.453152027 + t * 1.061405429))))
        result = poly * math.exp(-x * x)
        return result if x >= 0 else 2.0 - result


def _igamc(a: float, x: float) -> float:
    """
    Upper incomplete gamma function Q(a, x) = 1 - P(a, x).
    Used for chi-squared p-value computation.
    """
    if x <= 0 or a <= 0:
        return 1.0

    if x < 1.0 or x < a:
        return 1.0 - _igam(a, x)

    # Continued fraction expansion (Legendre)
    big = 4.503599627370496e15
    biginv = 2.22044604925031308085e-16

    ax = a * math.log(x) - x - math.lgamma(a)
    if ax < -709.78:
        return 0.0
    ax = math.exp(ax)

    y = 1.0 - a
    z = x + y + 1.0
    c = 0.0
    pkm2 = 1.0
    qkm2 = x
    pkm1 = x + 1.0
    qkm1 = z * x
    ans = pkm1 / qkm1

    for _ in range(300):
        c += 1.0
        y += 1.0
        z += 2.0
        yc = y * c
        pk = pkm1 * z - pkm2 * yc
        qk = qkm1 * z - qkm2 * yc
        if qk != 0:
            r = pk / qk
            t = abs((ans - r) / r)
            ans = r
        else:
            t = 1.0
        pkm2 = pkm1
        pkm1 = pk
        qkm2 = qkm1
        qkm1 = qk
        if abs(pk) > big:
            pkm2 *= biginv
            pkm1 *= biginv
            qkm2 *= biginv
            qkm1 *= biginv
        if t <= 1e-14:
            break

    return ans * ax


def _igam(a: float, x: float) -> float:
    """Lower incomplete gamma function P(a, x)."""
    if x <= 0 or a <= 0:
        return 0.0

    if x > 1.0 and x > a:
        return 1.0 - _igamc(a, x)

    ax = a * math.log(x) - x - math.lgamma(a)
    if ax < -709.78:
        return 0.0
    ax = math.exp(ax)

    r = a
    c = 1.0
    ans = 1.0

    for _ in range(300):
        r += 1.0
        c *= x / r
        ans += c
        if c / ans < 1e-14:
            break

    return ans * ax / a


# ---------------------------------------------------------------------------
# Test Result
# ---------------------------------------------------------------------------

@dataclass
class TestResult:
    """Result of a single NIST statistical test."""
    test_name: str
    p_value: float
    passed: bool
    statistic: float
    details: dict

    def to_dict(self) -> dict:
        return {
            "test_name": self.test_name,
            "p_value": round(self.p_value, 8),
            "passed": self.passed,
            "statistic": round(self.statistic, 8),
            "details": self.details,
        }


# ---------------------------------------------------------------------------
# Utility: Convert bytes to bit string
# ---------------------------------------------------------------------------

def bytes_to_bits(data: bytes) -> str:
    """Convert bytes to a string of '0' and '1' characters."""
    return "".join(format(byte, "08b") for byte in data)


def bits_to_ints(bits: str) -> List[int]:
    """Convert bit string to list of integers (0 or 1)."""
    return [int(b) for b in bits]


# ---------------------------------------------------------------------------
# NIST SP 800-22 Test Implementations
# ---------------------------------------------------------------------------

class NISTTestSuite:
    """
    NIST SP 800-22 Rev 1a Statistical Test Suite.

    GLI-11 4.6.1: The RNG must pass the NIST SP 800-22 statistical
    test suite with a significance level of alpha = 0.01.

    Minimum recommended input: 1,000,000 bits (125,000 bytes).
    """

    def __init__(self, significance_level: float = 0.01):
        self.alpha = significance_level

    def run_all_tests(self, bits: str) -> List[TestResult]:
        """
        Run all implemented NIST SP 800-22 tests.

        Args:
            bits: String of '0' and '1' characters (minimum 1,000,000 recommended)

        Returns:
            List of TestResult objects
        """
        n = len(bits)
        logger.info("Running NIST SP 800-22 tests on %d bits", n)

        results = []
        tests = [
            ("Frequency (Monobit)", self.frequency_test),
            ("Block Frequency", self.block_frequency_test),
            ("Runs", self.runs_test),
            ("Longest Run of Ones", self.longest_run_test),
            ("Discrete Fourier Transform", self.fft_test),
            ("Non-Overlapping Template", self.non_overlapping_template_test),
            ("Serial", self.serial_test),
            ("Approximate Entropy", self.approximate_entropy_test),
            ("Cumulative Sums (Forward)", lambda b: self.cumulative_sums_test(b, forward=True)),
            ("Cumulative Sums (Reverse)", lambda b: self.cumulative_sums_test(b, forward=False)),
        ]

        for test_name, test_func in tests:
            try:
                result = test_func(bits)
                if isinstance(result, list):
                    results.extend(result)
                else:
                    results.append(result)
                status = "PASS" if (isinstance(result, list) and all(r.passed for r in result)) or (isinstance(result, TestResult) and result.passed) else "FAIL"  # ty:ignore[unresolved-attribute]
                logger.info("  %s: %s", test_name, status)
            except Exception as e:
                logger.error("  %s: ERROR - %s", test_name, str(e))
                results.append(TestResult(
                    test_name=test_name,
                    p_value=0.0,
                    passed=False,
                    statistic=0.0,
                    details={"error": str(e)},
                ))

        return results

    # --- Test 1: Frequency (Monobit) Test ---

    def frequency_test(self, bits: str) -> TestResult:
        """
        NIST SP 800-22 Section 2.1: Frequency (Monobit) Test.

        Tests whether the number of ones and zeros in the sequence
        is approximately equal, as expected for a random sequence.
        """
        n = len(bits)
        s = sum(1 if b == "1" else -1 for b in bits)
        s_obs = abs(s) / math.sqrt(n)
        p_value = _erfc(s_obs / math.sqrt(2))

        return TestResult(
            test_name="Frequency (Monobit)",
            p_value=p_value,
            passed=p_value >= self.alpha,
            statistic=s_obs,
            details={"n": n, "sum": s, "s_obs": round(s_obs, 6)},
        )

    # --- Test 2: Block Frequency Test ---

    def block_frequency_test(self, bits: str, block_size: int = 128) -> TestResult:
        """
        NIST SP 800-22 Section 2.2: Frequency Test within a Block.

        Tests the proportion of ones within M-bit blocks.
        """
        n = len(bits)
        num_blocks = n // block_size

        if num_blocks == 0:
            return TestResult(
                test_name="Block Frequency",
                p_value=0.0, passed=False, statistic=0.0,
                details={"error": "Sequence too short for block size"},
            )

        chi_sq = 0.0
        for i in range(num_blocks):
            block = bits[i * block_size : (i + 1) * block_size]
            pi = sum(1 for b in block if b == "1") / block_size
            chi_sq += (pi - 0.5) ** 2

        chi_sq *= 4.0 * block_size
        p_value = _igamc(num_blocks / 2.0, chi_sq / 2.0)

        return TestResult(
            test_name="Block Frequency",
            p_value=p_value,
            passed=p_value >= self.alpha,
            statistic=chi_sq,
            details={"block_size": block_size, "num_blocks": num_blocks},
        )

    # --- Test 3: Runs Test ---

    def runs_test(self, bits: str) -> TestResult:
        """
        NIST SP 800-22 Section 2.3: Runs Test.

        Tests whether the number of runs (uninterrupted sequences of
        identical bits) is as expected for a random sequence.
        """
        n = len(bits)
        pi = sum(1 for b in bits if b == "1") / n

        # Pre-test: frequency must be close to 0.5
        tau = 2.0 / math.sqrt(n)
        if abs(pi - 0.5) >= tau:
            return TestResult(
                test_name="Runs",
                p_value=0.0, passed=False, statistic=0.0,
                details={"error": "Frequency pre-test failed", "pi": round(pi, 6)},
            )

        # Count runs
        v_obs = 1
        for i in range(1, n):
            if bits[i] != bits[i - 1]:
                v_obs += 1

        numerator = abs(v_obs - 2.0 * n * pi * (1.0 - pi))
        denominator = 2.0 * math.sqrt(2.0 * n) * pi * (1.0 - pi)

        if denominator == 0:
            p_value = 0.0
        else:
            p_value = _erfc(numerator / denominator)

        return TestResult(
            test_name="Runs",
            p_value=p_value,
            passed=p_value >= self.alpha,
            statistic=float(v_obs),
            details={"n": n, "pi": round(pi, 6), "v_obs": v_obs},
        )

    # --- Test 4: Longest Run of Ones Test ---

    def longest_run_test(self, bits: str) -> TestResult:
        """
        NIST SP 800-22 Section 2.4: Longest Run of Ones in a Block.

        Tests whether the longest run of ones within M-bit blocks
        is consistent with a random sequence.
        """
        n = len(bits)

        # Select parameters based on sequence length
        if n < 128:
            return TestResult(
                test_name="Longest Run of Ones",
                p_value=0.0, passed=False, statistic=0.0,
                details={"error": "Sequence too short (need >= 128 bits)"},
            )
        elif n < 6272:
            m, k = 8, 3
            v_values = [1, 2, 3, 4]  # Categories: <=1, 2, 3, >=4
            pi_values = [0.2148, 0.3672, 0.2305, 0.1875]
        elif n < 750000:
            m, k = 128, 5
            v_values = [4, 5, 6, 7, 8, 9]
            pi_values = [0.1174, 0.2430, 0.2493, 0.1752, 0.1027, 0.1124]
        else:
            m, k = 10000, 6
            v_values = [10, 11, 12, 13, 14, 15, 16]
            pi_values = [0.0882, 0.2092, 0.2483, 0.1933, 0.1208, 0.0675, 0.0727]

        num_blocks = n // m
        if num_blocks == 0:
            return TestResult(
                test_name="Longest Run of Ones",
                p_value=0.0, passed=False, statistic=0.0,
                details={"error": "No complete blocks"},
            )

        # Count frequencies of longest runs
        freq = [0] * (k + 1)
        for i in range(num_blocks):
            block = bits[i * m : (i + 1) * m]
            max_run = 0
            current_run = 0
            for b in block:
                if b == "1":
                    current_run += 1
                    max_run = max(max_run, current_run)
                else:
                    current_run = 0

            # Map to category
            if max_run <= v_values[0]:
                freq[0] += 1
            elif max_run >= v_values[-1]:
                freq[k] += 1
            else:
                for j in range(1, k):
                    if max_run == v_values[j]:
                        freq[j] += 1
                        break

        # Chi-squared statistic
        chi_sq = sum(
            (freq[i] - num_blocks * pi_values[i]) ** 2 / (num_blocks * pi_values[i])
            for i in range(k + 1)
            if pi_values[i] > 0
        )

        p_value = _igamc(k / 2.0, chi_sq / 2.0)

        return TestResult(
            test_name="Longest Run of Ones",
            p_value=p_value,
            passed=p_value >= self.alpha,
            statistic=chi_sq,
            details={
                "block_size": m, "num_blocks": num_blocks,
                "frequencies": freq[:k + 1],
            },
        )

    # --- Test 5: Discrete Fourier Transform (Spectral) Test ---

    def fft_test(self, bits: str) -> TestResult:
        """
        NIST SP 800-22 Section 2.6: Discrete Fourier Transform (Spectral) Test.

        Detects periodic features in the sequence that indicate
        deviation from randomness.
        """
        n = len(bits)
        # Convert to +1/-1
        x = [1 if b == "1" else -1 for b in bits]

        # Compute DFT using a simple implementation (for portability)
        # For large sequences, use numpy.fft if available
        try:
            import numpy as np
            fft_result = np.fft.fft(x)
            magnitudes = np.abs(fft_result[:n // 2])
        except ImportError:
            # Pure Python DFT (slow but works for small sequences)
            half_n = n // 2
            magnitudes = []
            for k in range(half_n):
                real_sum = 0.0
                imag_sum = 0.0
                for t in range(n):
                    angle = 2.0 * math.pi * k * t / n
                    real_sum += x[t] * math.cos(angle)
                    imag_sum -= x[t] * math.sin(angle)
                magnitudes.append(math.sqrt(real_sum ** 2 + imag_sum ** 2))

        # Threshold
        threshold = math.sqrt(math.log(1.0 / 0.05) * n)

        # Count peaks below threshold
        n0 = 0.95 * n / 2.0  # Expected number below threshold
        n1 = sum(1 for m in magnitudes if m < threshold)

        d = (n1 - n0) / math.sqrt(n * 0.95 * 0.05 / 4.0)
        p_value = _erfc(abs(d) / math.sqrt(2))

        return TestResult(
            test_name="Discrete Fourier Transform",
            p_value=p_value,
            passed=p_value >= self.alpha,
            statistic=d,
            details={
                "n": n, "threshold": round(threshold, 4),
                "expected_below": round(n0, 2), "actual_below": n1,
            },
        )

    # --- Test 6: Non-Overlapping Template Matching Test ---

    def non_overlapping_template_test(
        self, bits: str, template: str = "000000001", block_size: int = 0
    ) -> TestResult:
        """
        NIST SP 800-22 Section 2.7: Non-Overlapping Template Matching.

        Tests whether the number of occurrences of a given non-periodic
        template pattern is as expected.
        """
        n = len(bits)
        m = len(template)
        if block_size <= 0:
            block_size = max(m * 10, n // 100)

        num_blocks = n // block_size
        if num_blocks == 0:
            return TestResult(
                test_name="Non-Overlapping Template",
                p_value=0.0, passed=False, statistic=0.0,
                details={"error": "Sequence too short"},
            )

        # Count template occurrences per block
        counts = []
        for i in range(num_blocks):
            block = bits[i * block_size : (i + 1) * block_size]
            count = 0
            j = 0
            while j <= len(block) - m:
                if block[j : j + m] == template:
                    count += 1
                    j += m  # Non-overlapping
                else:
                    j += 1
            counts.append(count)

        # Theoretical mean and variance
        mu = (block_size - m + 1) / (2 ** m)
        sigma_sq = block_size * (1.0 / (2 ** m) - (2 * m - 1) / (2 ** (2 * m)))

        if sigma_sq <= 0:
            return TestResult(
                test_name="Non-Overlapping Template",
                p_value=0.0, passed=False, statistic=0.0,
                details={"error": "Invalid variance"},
            )

        # Chi-squared statistic
        chi_sq = sum((c - mu) ** 2 / sigma_sq for c in counts)
        p_value = _igamc(num_blocks / 2.0, chi_sq / 2.0)

        return TestResult(
            test_name=f"Non-Overlapping Template ({template})",
            p_value=p_value,
            passed=p_value >= self.alpha,
            statistic=chi_sq,
            details={
                "template": template, "block_size": block_size,
                "num_blocks": num_blocks, "mu": round(mu, 4),
                "sigma_sq": round(sigma_sq, 4),
                "mean_count": round(sum(counts) / len(counts), 4),
            },
        )

    # --- Test 7: Serial Test ---

    def serial_test(self, bits: str, block_size: int = 16) -> List[TestResult]:
        """
        NIST SP 800-22 Section 2.11: Serial Test.

        Tests the frequency of all 2^m overlapping m-bit patterns.
        """
        n = len(bits)
        m = block_size

        # Augment sequence
        augmented = bits + bits[:m - 1]

        def psi_sq(pattern_len: int) -> float:
            """Compute psi-squared statistic for pattern_len."""
            if pattern_len == 0:
                return 0.0
            counts: Dict[str, int] = {}
            for i in range(n):
                pattern = augmented[i : i + pattern_len]
                counts[pattern] = counts.get(pattern, 0) + 1
            total = sum(v * v for v in counts.values())
            return (2 ** pattern_len / n) * total - n

        psi_m = psi_sq(m)
        psi_m1 = psi_sq(m - 1)
        psi_m2 = psi_sq(m - 2) if m >= 2 else 0.0

        delta1 = psi_m - psi_m1
        delta2 = psi_m - 2 * psi_m1 + psi_m2

        p_value1 = _igamc(2 ** (m - 2), delta1 / 2.0)
        p_value2 = _igamc(2 ** (m - 3), delta2 / 2.0) if m >= 3 else 1.0

        return [
            TestResult(
                test_name=f"Serial (delta1, m={m})",
                p_value=p_value1,
                passed=p_value1 >= self.alpha,
                statistic=delta1,
                details={"m": m, "psi_m": round(psi_m, 4), "psi_m1": round(psi_m1, 4)},
            ),
            TestResult(
                test_name=f"Serial (delta2, m={m})",
                p_value=p_value2,
                passed=p_value2 >= self.alpha,
                statistic=delta2,
                details={"m": m},
            ),
        ]

    # --- Test 8: Approximate Entropy Test ---

    def approximate_entropy_test(self, bits: str, block_size: int = 10) -> TestResult:
        """
        NIST SP 800-22 Section 2.12: Approximate Entropy Test.

        Compares frequency of overlapping patterns of consecutive lengths.
        """
        n = len(bits)
        m = block_size

        def phi(pattern_len: int) -> float:
            augmented = bits + bits[:pattern_len - 1] if pattern_len > 0 else bits
            counts: Dict[str, int] = {}
            for i in range(n):
                if pattern_len == 0:
                    break
                pattern = augmented[i : i + pattern_len]
                counts[pattern] = counts.get(pattern, 0) + 1
            if not counts:
                return 0.0
            total = sum(
                (c / n) * math.log(c / n) for c in counts.values() if c > 0
            )
            return total

        phi_m = phi(m)
        phi_m1 = phi(m + 1)

        apen = phi_m - phi_m1
        chi_sq = 2.0 * n * (math.log(2) - apen)
        p_value = _igamc(2 ** (m - 1), chi_sq / 2.0)

        return TestResult(
            test_name="Approximate Entropy",
            p_value=p_value,
            passed=p_value >= self.alpha,
            statistic=chi_sq,
            details={
                "m": m, "phi_m": round(phi_m, 6),
                "phi_m1": round(phi_m1, 6), "apen": round(apen, 6),
            },
        )

    # --- Test 9: Cumulative Sums Test ---

    def cumulative_sums_test(self, bits: str, forward: bool = True) -> TestResult:
        """
        NIST SP 800-22 Section 2.13: Cumulative Sums Test.

        Tests whether the cumulative sum of the adjusted (-1, +1) sequence
        is too large or too small.
        """
        n = len(bits)
        direction = "Forward" if forward else "Reverse"

        # Convert to +1/-1 and optionally reverse
        sequence = bits if forward else bits[::-1]
        x = [1 if b == "1" else -1 for b in sequence]

        # Compute cumulative sum and find maximum absolute value
        cumsum = 0
        z = 0
        for val in x:
            cumsum += val
            z = max(z, abs(cumsum))

        # Compute p-value
        sum1 = 0.0
        sum2 = 0.0
        sqrt_n = math.sqrt(n)

        for k in range(int((-n / z + 1) / 4), int((n / z - 1) / 4) + 1):
            term1 = _normal_cdf((4 * k + 1) * z / sqrt_n)
            term2 = _normal_cdf((4 * k - 1) * z / sqrt_n)
            sum1 += term1 - term2

        for k in range(int((-n / z - 3) / 4), int((n / z - 1) / 4) + 1):
            term1 = _normal_cdf((4 * k + 3) * z / sqrt_n)
            term2 = _normal_cdf((4 * k + 1) * z / sqrt_n)
            sum2 += term1 - term2

        p_value = 1.0 - sum1 + sum2
        p_value = max(0.0, min(1.0, p_value))

        return TestResult(
            test_name=f"Cumulative Sums ({direction})",
            p_value=p_value,
            passed=p_value >= self.alpha,
            statistic=float(z),
            details={"n": n, "z": z, "direction": direction},
        )

    # --- Report Generation ---

    def print_report(self, results: List[TestResult]) -> None:
        """Print a formatted test report."""
        print("=" * 72)
        print("NIST SP 800-22 Statistical Test Suite Report")
        print(f"Significance Level (alpha): {self.alpha}")
        print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
        print("=" * 72)

        passed = sum(1 for r in results if r.passed)
        total = len(results)

        for result in results:
            status = "PASS" if result.passed else "FAIL"
            marker = " " if result.passed else "*"
            print(f" {marker} [{status}] {result.test_name:45s} "
                  f"p-value={result.p_value:.8f}")

        print("-" * 72)
        print(f"Results: {passed}/{total} tests passed")
        print(f"Overall: {'PASS' if passed == total else 'FAIL'}")
        print("=" * 72)

    def generate_certification_report(self, results: List[TestResult], rng_info: dict) -> dict:
        """
        Generate JSON report suitable for GLI-11 certification submission.
        """
        passed = sum(1 for r in results if r.passed)
        return {
            "report_type": "NIST SP 800-22 Rev 1a",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "significance_level": self.alpha,
            "rng_info": rng_info,
            "overall_result": "PASS" if passed == len(results) else "FAIL",
            "tests_passed": passed,
            "tests_total": len(results),
            "results": [r.to_dict() for r in results],
        }


def _normal_cdf(x: float) -> float:
    """Standard normal cumulative distribution function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2)))


# ---------------------------------------------------------------------------
# Self-Test
# ---------------------------------------------------------------------------

def self_test() -> bool:
    """NIST test suite self-test."""
    print("=== NIST SP 800-22 Test Suite Self-Test ===\n")

    suite = NISTTestSuite(significance_level=0.01)

    # Generate test data from os.urandom (should pass all tests)
    test_bytes = os.urandom(125000)  # 1,000,000 bits
    bits = bytes_to_bits(test_bytes)
    print(f"Test data: {len(bits)} bits from os.urandom\n")

    # Run all tests
    results = suite.run_all_tests(bits)

    # Print report
    suite.print_report(results)

    # Verify results structure
    assert len(results) > 0, "No results returned"
    for r in results:
        assert 0.0 <= r.p_value <= 1.0, f"Invalid p-value: {r.p_value}"
        assert isinstance(r.passed, bool)

    passed = sum(1 for r in results if r.passed)
    print(f"\n[{'PASS' if passed >= len(results) - 1 else 'WARN'}] "
          f"{passed}/{len(results)} tests passed")

    # Test with biased data (should fail)
    print("\n--- Biased data test (should fail) ---")
    biased_bits = "1" * 500000 + "0" * 500000
    biased_results = suite.run_all_tests(biased_bits)
    biased_passed = sum(1 for r in biased_results if r.passed)
    assert biased_passed < len(biased_results), "Biased data should fail some tests"
    print(f"[PASS] Biased data correctly detected: "
          f"{biased_passed}/{len(biased_results)} passed (expected failures)")

    # Generate certification report
    cert_report = suite.generate_certification_report(results, {
        "generator": "os.urandom",
        "platform": "test",
    })
    assert cert_report["overall_result"] in ("PASS", "FAIL")
    print(f"\n[PASS] Certification report generated: {cert_report['overall_result']}")

    print("\n=== All self-tests passed ===")
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    self_test()
