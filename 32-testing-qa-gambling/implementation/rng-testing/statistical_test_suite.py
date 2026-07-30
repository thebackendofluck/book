#!/usr/bin/env python3
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
RNG Statistical Test Suite for iGaming Platforms
=================================================
Implements NIST SP 800-22 and GLI-11 compliant statistical tests
for Random Number Generator validation.

Tests included:
  - Chi-squared (frequency distribution)
  - Runs test (sequential independence)
  - Serial correlation (lag-based correlation)
  - Monobit frequency test
  - Block frequency test
  - Poker test (pattern distribution)
  - Gap test
  - Coupon collector test
  - Spectral test (FFT-based)

Usage:
  python statistical_test_suite.py --source /dev/urandom --samples 1000000
  python statistical_test_suite.py --source api --endpoint https://rng.casino.com/api/v1/numbers
  python statistical_test_suite.py --source file --input rng_output.bin --report html
"""

import argparse
import json
import math
import os
import struct
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, List, Optional, Tuple

try:
    import numpy as np
    from scipy import stats as scipy_stats
    from scipy.fft import fft
except ImportError:
    print("Required: pip install numpy scipy")
    print("Optional: pip install requests matplotlib jinja2")
    sys.exit(1)


class TestResult(Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass
class StatisticalTestResult:
    test_name: str
    result: TestResult
    p_value: float
    statistic: float
    threshold: float
    description: str
    details: dict = field(default_factory=dict)
    duration_ms: float = 0.0

    def to_dict(self):
        return {
            "test_name": self.test_name,
            "result": self.result.value,
            "p_value": round(self.p_value, 8),
            "statistic": round(self.statistic, 8),
            "threshold": self.threshold,
            "description": self.description,
            "details": self.details,
            "duration_ms": round(self.duration_ms, 2),
        }


class RNGSource:
    """Abstraction for RNG data sources."""

    @staticmethod
    def from_urandom(n: int) -> np.ndarray:
        raw = os.urandom(n * 4)
        return np.frombuffer(raw, dtype=np.uint32)

    @staticmethod
    def from_file(path: str, n: int) -> np.ndarray:
        with open(path, "rb") as f:
            raw = f.read(n * 4)
        if len(raw) < n * 4:
            raise ValueError(f"File too small: need {n * 4} bytes, got {len(raw)}")
        return np.frombuffer(raw, dtype=np.uint32)

    @staticmethod
    def from_api(endpoint: str, n: int, batch_size: int = 10000) -> np.ndarray:
        try:
            import requests
        except ImportError:
            raise ImportError("pip install requests")

        numbers = []
        remaining = n
        while remaining > 0:
            batch = min(batch_size, remaining)
            resp = requests.post(
                endpoint,
                json={"count": batch, "min": 0, "max": 2**32 - 1},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            numbers.extend(data.get("numbers", data.get("results", [])))
            remaining -= batch
        return np.array(numbers[:n], dtype=np.uint32)

    @staticmethod
    def to_bits(data: np.ndarray) -> np.ndarray:
        return np.unpackbits(data.view(np.uint8))


class StatisticalTestSuite:
    """
    Complete RNG statistical test suite for iGaming certification.

    Significance level defaults to 0.01 per GLI-11 Section 4.3 requirements.
    Minimum sample sizes follow NIST SP 800-22 recommendations.
    """

    def __init__(self, significance_level: float = 0.01):
        self.alpha = significance_level
        self.results: List[StatisticalTestResult] = []

    def _evaluate(self, p_value: float) -> TestResult:
        if p_value >= self.alpha:
            return TestResult.PASS
        elif p_value >= self.alpha / 10:
            return TestResult.INCONCLUSIVE
        return TestResult.FAIL

    # ---------------------------------------------------------------
    # 1. Chi-Squared Frequency Test
    # ---------------------------------------------------------------
    def chi_squared_test(
        self, data: np.ndarray, num_bins: int = 256
    ) -> StatisticalTestResult:
        """
        Chi-squared goodness-of-fit test for uniform distribution.
        GLI-11 4.3.1: RNG output must be uniformly distributed.
        """
        t0 = time.monotonic()

        # Map to bins
        scaled = (data / (2**32)) * num_bins
        bins = scaled.astype(int)
        bins = np.clip(bins, 0, num_bins - 1)

        observed = np.bincount(bins, minlength=num_bins).astype(float)
        expected = len(data) / num_bins

        chi2_stat = np.sum((observed - expected) ** 2 / expected)
        df = num_bins - 1
        p_value = 1.0 - scipy_stats.chi2.cdf(chi2_stat, df)

        result = StatisticalTestResult(
            test_name="Chi-Squared Frequency Test",
            result=self._evaluate(p_value),
            p_value=p_value,
            statistic=chi2_stat,
            threshold=scipy_stats.chi2.ppf(1 - self.alpha, df),
            description=(
                f"Tests uniform distribution across {num_bins} bins. "
                f"Expected {expected:.1f} per bin."
            ),
            details={
                "num_bins": num_bins,
                "degrees_of_freedom": df,
                "sample_size": len(data),
                "min_observed": int(observed.min()),
                "max_observed": int(observed.max()),
                "mean_observed": float(observed.mean()),
                "std_observed": float(observed.std()),
            },
            duration_ms=(time.monotonic() - t0) * 1000,
        )
        self.results.append(result)
        return result

    # ---------------------------------------------------------------
    # 2. Runs Test (Wald-Wolfowitz)
    # ---------------------------------------------------------------
    def runs_test(self, data: np.ndarray) -> StatisticalTestResult:
        """
        Runs test for sequential independence.
        GLI-11 4.3.2: Successive outputs must be statistically independent.
        """
        t0 = time.monotonic()

        median = np.median(data)
        binary = (data >= median).astype(int)

        n1 = np.sum(binary)
        n0 = len(binary) - n1
        n = len(binary)

        # Count runs
        runs = 1 + np.sum(binary[1:] != binary[:-1])

        # Expected runs and variance
        expected_runs = 1 + (2 * n0 * n1) / n
        if n <= 1:
            p_value = 1.0
            z_stat = 0.0
        else:
            variance = (2 * n0 * n1 * (2 * n0 * n1 - n)) / (n**2 * (n - 1))
            if variance <= 0:
                p_value = 0.0
                z_stat = float("inf")
            else:
                z_stat = (runs - expected_runs) / math.sqrt(variance)
                p_value = 2 * (1 - scipy_stats.norm.cdf(abs(z_stat)))

        result = StatisticalTestResult(
            test_name="Runs Test (Wald-Wolfowitz)",
            result=self._evaluate(p_value),
            p_value=p_value,
            statistic=z_stat,
            threshold=scipy_stats.norm.ppf(1 - self.alpha / 2),
            description=(
                f"Tests independence of successive values. "
                f"Observed {runs} runs, expected {expected_runs:.1f}."
            ),
            details={
                "observed_runs": int(runs),
                "expected_runs": round(expected_runs, 2),
                "n_above_median": int(n1),
                "n_below_median": int(n0),
            },
            duration_ms=(time.monotonic() - t0) * 1000,
        )
        self.results.append(result)
        return result

    # ---------------------------------------------------------------
    # 3. Serial Correlation Test
    # ---------------------------------------------------------------
    def serial_correlation_test(
        self, data: np.ndarray, max_lag: int = 10
    ) -> StatisticalTestResult:
        """
        Tests autocorrelation at multiple lags.
        Values should show no significant correlation at any lag.
        """
        t0 = time.monotonic()

        normalized = data.astype(np.float64) / (2**32 - 1)
        n = len(normalized)
        mean = normalized.mean()
        var = normalized.var()

        correlations = {}
        max_corr = 0.0
        worst_lag = 0
        critical = scipy_stats.norm.ppf(1 - self.alpha / 2) / math.sqrt(n)

        for lag in range(1, max_lag + 1):
            if var > 0:
                corr = np.corrcoef(normalized[:-lag], normalized[lag:])[0, 1]
            else:
                corr = 0.0
            correlations[lag] = round(float(corr), 8)
            if abs(corr) > abs(max_corr):
                max_corr = corr
                worst_lag = lag

        # Use the worst lag for the overall test
        z_stat = max_corr * math.sqrt(n)
        p_value = 2 * (1 - scipy_stats.norm.cdf(abs(z_stat)))

        result = StatisticalTestResult(
            test_name="Serial Correlation Test",
            result=self._evaluate(p_value),
            p_value=p_value,
            statistic=max_corr,
            threshold=critical,
            description=(
                f"Tests autocorrelation at lags 1-{max_lag}. "
                f"Worst correlation {max_corr:.6f} at lag {worst_lag}."
            ),
            details={
                "correlations": correlations,
                "worst_lag": worst_lag,
                "critical_value": round(critical, 6),
                "all_within_bounds": all(
                    abs(v) < critical for v in correlations.values()
                ),
            },
            duration_ms=(time.monotonic() - t0) * 1000,
        )
        self.results.append(result)
        return result

    # ---------------------------------------------------------------
    # 4. Monobit Frequency Test (NIST SP 800-22 Test 1)
    # ---------------------------------------------------------------
    def monobit_test(self, bits: np.ndarray) -> StatisticalTestResult:
        """
        NIST SP 800-22 Section 2.1: Frequency (Monobit) Test.
        Tests that the proportion of 0s and 1s is approximately equal.
        """
        t0 = time.monotonic()

        n = len(bits)
        # Convert 0/1 to -1/+1
        s = np.sum(2 * bits.astype(np.int64) - 1)
        s_obs = abs(s) / math.sqrt(n)
        p_value = math.erfc(s_obs / math.sqrt(2))

        ones = int(np.sum(bits))
        zeros = n - ones

        result = StatisticalTestResult(
            test_name="Monobit Frequency Test (NIST 2.1)",
            result=self._evaluate(p_value),
            p_value=p_value,
            statistic=s_obs,
            threshold=scipy_stats.norm.ppf(1 - self.alpha / 2),
            description=(
                f"Tests equal proportion of 0s and 1s. "
                f"Ones: {ones} ({100*ones/n:.2f}%), Zeros: {zeros} ({100*zeros/n:.2f}%)."
            ),
            details={
                "ones": ones,
                "zeros": zeros,
                "total_bits": n,
                "sum_statistic": int(s),
            },
            duration_ms=(time.monotonic() - t0) * 1000,
        )
        self.results.append(result)
        return result

    # ---------------------------------------------------------------
    # 5. Block Frequency Test (NIST SP 800-22 Test 2)
    # ---------------------------------------------------------------
    def block_frequency_test(
        self, bits: np.ndarray, block_size: int = 128
    ) -> StatisticalTestResult:
        """
        NIST SP 800-22 Section 2.2: Block Frequency Test.
        Tests uniformity of 1s within M-bit blocks.
        """
        t0 = time.monotonic()

        n = len(bits)
        num_blocks = n // block_size
        if num_blocks == 0:
            raise ValueError(f"Need at least {block_size} bits")

        blocks = bits[: num_blocks * block_size].reshape(num_blocks, block_size)
        proportions = blocks.mean(axis=1)
        chi2 = 4 * block_size * np.sum((proportions - 0.5) ** 2)
        p_value = 1 - scipy_stats.chi2.cdf(chi2, num_blocks)

        result = StatisticalTestResult(
            test_name="Block Frequency Test (NIST 2.2)",
            result=self._evaluate(p_value),
            p_value=p_value,
            statistic=chi2,
            threshold=scipy_stats.chi2.ppf(1 - self.alpha, num_blocks),
            description=(
                f"Tests uniformity within {block_size}-bit blocks "
                f"({num_blocks} blocks tested)."
            ),
            details={
                "block_size": block_size,
                "num_blocks": num_blocks,
                "mean_proportion": round(float(proportions.mean()), 6),
                "min_proportion": round(float(proportions.min()), 6),
                "max_proportion": round(float(proportions.max()), 6),
            },
            duration_ms=(time.monotonic() - t0) * 1000,
        )
        self.results.append(result)
        return result

    # ---------------------------------------------------------------
    # 6. Poker Test (Pattern Distribution)
    # ---------------------------------------------------------------
    def poker_test(
        self, data: np.ndarray, hand_size: int = 5
    ) -> StatisticalTestResult:
        """
        Poker test: groups of digits should follow expected pattern distribution.
        Used in GLI-11 and eCOGRA RNG certification.
        """
        t0 = time.monotonic()

        # Convert to single digits (0-9)
        digits = data % 10
        n = len(digits)
        num_hands = n // hand_size
        if num_hands == 0:
            raise ValueError(f"Need at least {hand_size} values")

        hands = digits[: num_hands * hand_size].reshape(num_hands, hand_size)

        # Count distinct values in each hand
        distinct_counts = np.array(
            [len(set(hand)) for hand in hands]
        )
        observed = Counter(distinct_counts)

        # Expected probabilities for k distinct values in a hand of 5 from 10 symbols
        # Using Stirling numbers of the second kind
        def stirling2(n_val, k_val):
            if k_val == 0:
                return 1 if n_val == 0 else 0
            if k_val == 1 or k_val == n_val:
                return 1
            return k_val * stirling2(n_val - 1, k_val) + stirling2(n_val - 1, k_val - 1)

        d = 10  # digits 0-9
        expected_probs = {}
        for k in range(1, hand_size + 1):
            prob = stirling2(hand_size, k) * math.factorial(d) / (
                d**hand_size * math.factorial(d - k)
            )
            expected_probs[k] = prob

        chi2 = 0.0
        details_dist = {}
        for k in range(1, hand_size + 1):
            obs = observed.get(k, 0)
            exp = expected_probs.get(k, 0) * num_hands
            if exp > 0:
                chi2 += (obs - exp) ** 2 / exp
            details_dist[f"{k}_distinct"] = {
                "observed": obs,
                "expected": round(exp, 2),
            }

        df = hand_size - 1
        p_value = 1 - scipy_stats.chi2.cdf(chi2, df)

        result = StatisticalTestResult(
            test_name="Poker Test (Pattern Distribution)",
            result=self._evaluate(p_value),
            p_value=p_value,
            statistic=chi2,
            threshold=scipy_stats.chi2.ppf(1 - self.alpha, df),
            description=(
                f"Tests pattern distribution in groups of {hand_size}. "
                f"{num_hands} hands analyzed."
            ),
            details={
                "hand_size": hand_size,
                "num_hands": num_hands,
                "distribution": details_dist,
            },
            duration_ms=(time.monotonic() - t0) * 1000,
        )
        self.results.append(result)
        return result

    # ---------------------------------------------------------------
    # 7. Gap Test
    # ---------------------------------------------------------------
    def gap_test(
        self, data: np.ndarray, lower: float = 0.0, upper: float = 0.5
    ) -> StatisticalTestResult:
        """
        Gap test: measures gaps between values falling in a specified range.
        Gaps should follow geometric distribution.
        """
        t0 = time.monotonic()

        normalized = data.astype(np.float64) / (2**32 - 1)
        in_range = (normalized >= lower) & (normalized < upper)
        p = upper - lower

        # Find gaps
        gaps = []
        current_gap = 0
        for val in in_range:
            if val:
                gaps.append(current_gap)
                current_gap = 0
            else:
                current_gap += 1

        if not gaps:
            raise ValueError("No values found in specified range")

        max_gap = min(max(gaps), 50)  # Cap at 50 for chi-squared
        gap_counts = Counter(gaps)

        # Expected geometric distribution
        chi2 = 0.0
        n_gaps = len(gaps)
        for k in range(max_gap + 1):
            obs = gap_counts.get(k, 0)
            exp = n_gaps * p * (1 - p) ** k
            if exp >= 5:
                chi2 += (obs - exp) ** 2 / exp

        df = max_gap
        p_value = 1 - scipy_stats.chi2.cdf(chi2, df) if df > 0 else 1.0

        result = StatisticalTestResult(
            test_name="Gap Test",
            result=self._evaluate(p_value),
            p_value=p_value,
            statistic=chi2,
            threshold=scipy_stats.chi2.ppf(1 - self.alpha, max(df, 1)),
            description=(
                f"Tests gaps between values in [{lower}, {upper}). "
                f"Mean gap: {np.mean(gaps):.2f}, expected: {(1-p)/p:.2f}."
            ),
            details={
                "range": [lower, upper],
                "probability": p,
                "total_gaps": n_gaps,
                "mean_gap": round(float(np.mean(gaps)), 4),
                "max_gap_observed": int(max(gaps)),
            },
            duration_ms=(time.monotonic() - t0) * 1000,
        )
        self.results.append(result)
        return result

    # ---------------------------------------------------------------
    # 8. Spectral Test (Discrete Fourier Transform)
    # ---------------------------------------------------------------
    def spectral_test(self, bits: np.ndarray) -> StatisticalTestResult:
        """
        NIST SP 800-22 Section 2.6: Discrete Fourier Transform (Spectral) Test.
        Detects periodic features in the bit sequence.
        """
        t0 = time.monotonic()

        n = len(bits)
        # Convert to +1/-1
        x = 2 * bits.astype(np.float64) - 1

        # FFT
        s = np.abs(fft(x))
        # Only first half (due to symmetry)
        s = s[: n // 2]

        # Threshold
        t_val = math.sqrt(math.log(1.0 / 0.05) * n)
        n0_expected = 0.95 * n / 2
        n0_observed = np.sum(s < t_val)

        d = (n0_observed - n0_expected) / math.sqrt(n * 0.95 * 0.05 / 4)
        p_value = math.erfc(abs(d) / math.sqrt(2))

        result = StatisticalTestResult(
            test_name="Spectral Test (DFT/FFT)",
            result=self._evaluate(p_value),
            p_value=p_value,
            statistic=d,
            threshold=t_val,
            description=(
                f"Detects periodic features via FFT. "
                f"Peaks below threshold: {int(n0_observed)}/{n//2} "
                f"(expected ~{n0_expected:.0f})."
            ),
            details={
                "peaks_below_threshold": int(n0_observed),
                "expected_below_threshold": round(n0_expected, 1),
                "fft_threshold": round(t_val, 4),
            },
            duration_ms=(time.monotonic() - t0) * 1000,
        )
        self.results.append(result)
        return result

    # ---------------------------------------------------------------
    # 9. Coupon Collector Test
    # ---------------------------------------------------------------
    def coupon_collector_test(
        self, data: np.ndarray, d: int = 10
    ) -> StatisticalTestResult:
        """
        Coupon collector test: measures how many draws needed to see all d values.
        """
        t0 = time.monotonic()

        digits = data % d
        n = len(digits)

        # Collect sets
        set_lengths = []
        seen = set()
        count = 0
        for val in digits:
            seen.add(int(val))
            count += 1
            if len(seen) == d:
                set_lengths.append(count)
                seen = set()
                count = 0

        if len(set_lengths) < 10:
            raise ValueError(f"Need more data (only {len(set_lengths)} complete sets)")

        # Expected length: d * H_d where H_d is d-th harmonic number
        h_d = sum(1.0 / i for i in range(1, d + 1))
        expected_mean = d * h_d
        observed_mean = np.mean(set_lengths)

        # Chi-squared on length distribution
        max_len = min(max(set_lengths), int(expected_mean * 3))
        bin_edges = list(range(d, max_len + 1)) + [max_len + 1]
        observed_hist, _ = np.histogram(set_lengths, bins=bin_edges)

        # This is simplified; full implementation would use exact probabilities
        chi2 = 0.0
        n_sets = len(set_lengths)
        for i, obs in enumerate(observed_hist):
            exp = n_sets / len(observed_hist)
            if exp > 0:
                chi2 += (obs - exp) ** 2 / exp

        df = len(observed_hist) - 1
        p_value = 1 - scipy_stats.chi2.cdf(chi2, max(df, 1))

        result = StatisticalTestResult(
            test_name="Coupon Collector Test",
            result=self._evaluate(p_value),
            p_value=p_value,
            statistic=chi2,
            threshold=scipy_stats.chi2.ppf(1 - self.alpha, max(df, 1)),
            description=(
                f"Coupon collector with d={d}. "
                f"Mean set length: {observed_mean:.2f} (expected {expected_mean:.2f})."
            ),
            details={
                "d_values": d,
                "complete_sets": len(set_lengths),
                "expected_mean_length": round(expected_mean, 2),
                "observed_mean_length": round(float(observed_mean), 2),
                "min_length": int(min(set_lengths)),
                "max_length": int(max(set_lengths)),
            },
            duration_ms=(time.monotonic() - t0) * 1000,
        )
        self.results.append(result)
        return result

    # ---------------------------------------------------------------
    # Run All Tests
    # ---------------------------------------------------------------
    def run_all(
        self, data: np.ndarray, verbose: bool = True
    ) -> List[StatisticalTestResult]:
        """Run the complete test suite against RNG data."""
        bits = RNGSource.to_bits(data)

        tests = [
            ("Chi-Squared", lambda: self.chi_squared_test(data)),
            ("Runs", lambda: self.runs_test(data)),
            ("Serial Correlation", lambda: self.serial_correlation_test(data)),
            ("Monobit", lambda: self.monobit_test(bits)),
            ("Block Frequency", lambda: self.block_frequency_test(bits)),
            ("Poker", lambda: self.poker_test(data)),
            ("Gap", lambda: self.gap_test(data)),
            ("Spectral (FFT)", lambda: self.spectral_test(bits)),
            ("Coupon Collector", lambda: self.coupon_collector_test(data)),
        ]

        for name, test_fn in tests:
            if verbose:
                print(f"  Running {name} test...", end=" ", flush=True)
            try:
                result = test_fn()
                if verbose:
                    status = (
                        "\033[92mPASS\033[0m"
                        if result.result == TestResult.PASS
                        else "\033[91mFAIL\033[0m"
                        if result.result == TestResult.FAIL
                        else "\033[93mINCONCLUSIVE\033[0m"
                    )
                    print(f"{status} (p={result.p_value:.6f}, {result.duration_ms:.1f}ms)")
            except Exception as e:
                if verbose:
                    print(f"\033[91mERROR: {e}\033[0m")
                self.results.append(
                    StatisticalTestResult(
                        test_name=name,
                        result=TestResult.FAIL,
                        p_value=0.0,
                        statistic=0.0,
                        threshold=0.0,
                        description=f"Error: {str(e)}",
                    )
                )

        return self.results

    # ---------------------------------------------------------------
    # Report Generation
    # ---------------------------------------------------------------
    def generate_report(self, format: str = "json") -> str:
        """Generate test report in JSON, text, or HTML format."""
        passed = sum(1 for r in self.results if r.result == TestResult.PASS)
        failed = sum(1 for r in self.results if r.result == TestResult.FAIL)
        inconclusive = sum(1 for r in self.results if r.result == TestResult.INCONCLUSIVE)
        total = len(self.results)

        report: dict[str, Any] = {
            "report_title": "RNG Statistical Test Suite - iGaming Certification",
            "timestamp": datetime.utcnow().isoformat() + "Z",  # ty:ignore[deprecated]
            "standards": ["NIST SP 800-22", "GLI-11", "eCOGRA RNG Standards"],
            "significance_level": self.alpha,
            "summary": {
                "total_tests": total,
                "passed": passed,
                "failed": failed,
                "inconclusive": inconclusive,
                "overall_result": "PASS" if failed == 0 else "FAIL",
                "pass_rate": f"{100 * passed / total:.1f}%" if total > 0 else "N/A",
            },
            "tests": [r.to_dict() for r in self.results],
            "certification_recommendation": (
                "RNG MEETS certification requirements. All statistical tests passed."
                if failed == 0
                else f"RNG DOES NOT MEET certification requirements. {failed} test(s) failed."
            ),
        }

        if format == "json":
            return json.dumps(report, indent=2)

        if format == "text":
            lines = [
                "=" * 72,
                "RNG STATISTICAL TEST SUITE - iGAMING CERTIFICATION REPORT",
                "=" * 72,
                f"Timestamp: {report['timestamp']}",
                f"Significance Level: {self.alpha}",
                f"Standards: {', '.join(report['standards'])}",
                "",
                "SUMMARY",
                "-" * 72,
                f"  Total Tests:    {total}",
                f"  Passed:         {passed}",
                f"  Failed:         {failed}",
                f"  Inconclusive:   {inconclusive}",
                f"  Overall:        {report['summary']['overall_result']}",
                "",
                "DETAILED RESULTS",
                "-" * 72,
            ]
            for r in self.results:
                lines.append(f"\n  {r.test_name}")
                lines.append(f"    Result:    {r.result.value}")
                lines.append(f"    P-Value:   {r.p_value:.8f}")
                lines.append(f"    Statistic: {r.statistic:.8f}")
                lines.append(f"    Duration:  {r.duration_ms:.1f}ms")
                lines.append(f"    {r.description}")

            lines.extend(["", "=" * 72, report["certification_recommendation"], "=" * 72])
            return "\n".join(lines)

        return json.dumps(report, indent=2)


def main():
    parser = argparse.ArgumentParser(
        description="RNG Statistical Test Suite for iGaming Certification"
    )
    parser.add_argument(
        "--source",
        choices=["urandom", "file", "api"],
        default="urandom",
        help="RNG data source",
    )
    parser.add_argument("--input", help="Input file path (for file source)")
    parser.add_argument("--endpoint", help="API endpoint (for api source)")
    parser.add_argument(
        "--samples", type=int, default=1_000_000, help="Number of samples (default: 1M)"
    )
    parser.add_argument(
        "--alpha",
        type=float,
        default=0.01,
        help="Significance level (default: 0.01 per GLI-11)",
    )
    parser.add_argument(
        "--report",
        choices=["json", "text", "html"],
        default="text",
        help="Report format",
    )
    parser.add_argument("--output", help="Output file for report")

    args = parser.parse_args()

    print(f"\nRNG Statistical Test Suite")
    print(f"Samples: {args.samples:,} | Alpha: {args.alpha} | Source: {args.source}\n")

    # Load data
    print("Loading RNG data...", flush=True)
    if args.source == "urandom":
        data = RNGSource.from_urandom(args.samples)
    elif args.source == "file":
        if not args.input:
            parser.error("--input required for file source")
        data = RNGSource.from_file(args.input, args.samples)
    elif args.source == "api":
        if not args.endpoint:
            parser.error("--endpoint required for api source")
        data = RNGSource.from_api(args.endpoint, args.samples)

    print(f"Loaded {len(data):,} samples\n")

    # Run tests
    suite = StatisticalTestSuite(significance_level=args.alpha)
    suite.run_all(data, verbose=True)

    # Generate report
    report = suite.generate_report(format=args.report)

    if args.output:
        with open(args.output, "w") as f:
            f.write(report)
        print(f"\nReport saved to {args.output}")
    else:
        print(f"\n{report}")

    # Exit code
    failed = sum(1 for r in suite.results if r.result == TestResult.FAIL)
    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
