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
High-Performance RNG Testing Suite with Parallelism and Multithreading

This module provides enterprise-grade RNG testing with:
- Multiprocessing for CPU-bound statistical tests
- Thread pools for I/O-bound operations
- Async coordination for test orchestration
- Comprehensive test scenarios for all iGaming use cases
- Performance benchmarking and stress testing

Requirements:
    pip install numpy  # Optional, for enhanced statistics

Usage:
    ```python
    from performance_testing import RNGPerformanceTester, run_full_validation

    # Quick validation
    tester = RNGPerformanceTester(num_workers=8)
    results = tester.run_parallel_tests(rng, num_samples=10_000_000)

    # Full casino validation
    validation = run_full_validation(rng, parallel=True)
    print(f"Valid: {validation.is_valid}, Score: {validation.score}")
    ```

Author: iGaming Platform Team
License: MIT
"""

import logging
import math
import os
import queue
import statistics
import threading
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from multiprocessing import Manager, Pool, cpu_count
from typing import Any, Callable, Dict, List, Optional, Tuple

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class PerformanceMetrics:
    """Performance metrics for RNG testing."""

    test_name: str
    execution_time_ms: float
    samples_processed: int
    throughput_per_sec: float
    memory_mb: float = 0.0
    cpu_utilization: float = 0.0


@dataclass
class ParallelTestResult:
    """Result from parallel test execution."""

    test_name: str
    passed: bool
    p_value: float
    statistic: float
    execution_time_ms: float
    worker_id: int
    samples_tested: int
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ValidationReport:
    """Comprehensive validation report."""

    is_valid: bool
    score: float
    tests_passed: int
    tests_total: int
    total_samples: int
    total_time_ms: float
    throughput: float
    test_results: List[ParallelTestResult]
    performance_metrics: List[PerformanceMetrics]
    summary: str
    recommendations: List[str] = field(default_factory=list)


# =============================================================================
# Statistical Test Functions (Optimized for Parallel Execution)
# =============================================================================


def _monobit_test_chunk(
    data: Tuple[List[int], int, float]
) -> Tuple[bool, float, float, Dict[str, Any]]:
    """
    Monobit test optimized for chunk processing.

    Args:
        data: Tuple of (bits, worker_id, significance)

    Returns:
        Tuple of (passed, p_value, statistic, details)
    """
    bits, worker_id, significance = data
    n = len(bits)

    if n == 0:
        return False, 0.0, 0.0, {"error": "Empty bit sequence"}

    # Calculate sum efficiently
    ones = sum(bits)
    s = 2 * ones - n

    # Calculate test statistic
    s_obs = abs(s) / math.sqrt(n)

    # Calculate p-value
    p_value = math.erfc(s_obs / math.sqrt(2))
    passed = p_value >= significance

    return passed, p_value, s_obs, {
        "n": n,
        "ones": ones,
        "zeros": n - ones,
        "worker_id": worker_id,
    }


def _runs_test_chunk(
    data: Tuple[List[int], int, float]
) -> Tuple[bool, float, float, Dict[str, Any]]:
    """
    Runs test optimized for chunk processing.

    Args:
        data: Tuple of (bits, worker_id, significance)

    Returns:
        Tuple of (passed, p_value, statistic, details)
    """
    bits, worker_id, significance = data
    n = len(bits)

    if n < 10:
        return False, 0.0, 0.0, {"error": "Sequence too short"}

    pi = sum(bits) / n
    tau = 2 / math.sqrt(n)

    if abs(pi - 0.5) >= tau:
        return False, 0.0, 0.0, {
            "reason": "Failed frequency pre-test",
            "pi": pi,
            "worker_id": worker_id,
        }

    # Count runs
    runs = 1
    for i in range(1, n):
        if bits[i] != bits[i - 1]:
            runs += 1

    expected_runs = 2 * n * pi * (1 - pi) + 1
    std_runs = 2 * math.sqrt(2 * n) * pi * (1 - pi)

    if std_runs == 0:
        p_value = 0.0
    else:
        z = (runs - expected_runs) / std_runs
        p_value = math.erfc(abs(z) / math.sqrt(2))

    passed = p_value >= significance

    return passed, p_value, float(runs), {
        "runs": runs,
        "expected_runs": expected_runs,
        "worker_id": worker_id,
    }


def _chi_square_test_chunk(
    data: Tuple[List[int], int, int, float]
) -> Tuple[bool, float, float, Dict[str, Any]]:
    """
    Chi-square test for integer range uniformity.

    Args:
        data: Tuple of (values, num_categories, worker_id, significance)

    Returns:
        Tuple of (passed, p_value, statistic, details)
    """
    values, num_categories, worker_id, significance = data

    # Count occurrences
    counts = [0] * num_categories
    for v in values:
        if 0 <= v < num_categories:
            counts[v] += 1

    total = sum(counts)
    expected = total / num_categories

    if expected == 0:
        return False, 0.0, 0.0, {"error": "No samples"}

    # Calculate chi-square statistic
    chi_square = sum((c - expected) ** 2 / expected for c in counts)

    df = num_categories - 1

    # P-value approximation
    if df > 0:
        z = ((chi_square / df) ** (1 / 3) - (1 - 2 / (9 * df))) / math.sqrt(
            2 / (9 * df)
        )
        p_value = 0.5 * math.erfc(z / math.sqrt(2))
    else:
        p_value = 1.0

    passed = p_value >= significance

    return passed, p_value, chi_square, {
        "counts": counts[:20],  # First 20 for readability
        "total": total,
        "expected": expected,
        "df": df,
        "worker_id": worker_id,
    }


def _poker_test_chunk(
    data: Tuple[List[int], int, int, float]
) -> Tuple[bool, float, float, Dict[str, Any]]:
    """
    Poker test optimized for chunk processing.

    Args:
        data: Tuple of (bits, block_size, worker_id, significance)

    Returns:
        Tuple of (passed, p_value, statistic, details)
    """
    bits, block_size, worker_id, significance = data
    n = len(bits)
    num_blocks = n // block_size

    if num_blocks == 0:
        return False, 0.0, 0.0, {"error": "Insufficient data for blocks"}

    num_patterns = 2**block_size
    counts = [0] * num_patterns

    for i in range(num_blocks):
        block = bits[i * block_size : (i + 1) * block_size]
        pattern = sum(b * (2**j) for j, b in enumerate(block))
        counts[pattern] += 1

    expected_count = num_blocks / num_patterns

    if expected_count == 0:
        return False, 0.0, 0.0, {"error": "Expected count is zero"}

    chi_square = sum((c - expected_count) ** 2 / expected_count for c in counts)

    df = num_patterns - 1

    if df > 0:
        z = ((chi_square / df) ** (1 / 3) - (1 - 2 / (9 * df))) / math.sqrt(
            2 / (9 * df)
        )
        p_value = 0.5 * math.erfc(z / math.sqrt(2))
    else:
        p_value = 1.0

    passed = p_value >= significance

    return passed, p_value, chi_square, {
        "block_size": block_size,
        "num_blocks": num_blocks,
        "worker_id": worker_id,
    }


def _autocorrelation_test_chunk(
    data: Tuple[List[int], int, int, float]
) -> Tuple[bool, float, float, Dict[str, Any]]:
    """
    Autocorrelation test optimized for chunk processing.

    Args:
        data: Tuple of (bits, lag, worker_id, significance)

    Returns:
        Tuple of (passed, p_value, statistic, details)
    """
    bits, lag, worker_id, significance = data
    n = len(bits)

    if n - lag < 10:
        return False, 0.0, 0.0, {"error": "Sequence too short for lag"}

    matches = sum(bits[i] == bits[i + lag] for i in range(n - lag))
    autocorr = (2 * matches - (n - lag)) / (n - lag)

    std = 1 / math.sqrt(n - lag)
    z = abs(autocorr) / std

    p_value = math.erfc(z / math.sqrt(2))
    passed = p_value >= significance

    return passed, p_value, autocorr, {
        "lag": lag,
        "matches": matches,
        "worker_id": worker_id,
    }


# =============================================================================
# RNG Performance Tester Class
# =============================================================================


class RNGPerformanceTester:
    """
    High-performance RNG testing with parallel execution.

    This class provides enterprise-grade testing capabilities with:
    - Automatic worker pool sizing based on CPU cores
    - Chunk-based parallel test execution
    - Real-time progress tracking
    - Comprehensive performance metrics
    """

    def __init__(
        self,
        num_workers: Optional[int] = None,
        chunk_size: int = 100000,
        significance: float = 0.01,
    ):
        """
        Initialize the performance tester.

        Args:
            num_workers: Number of parallel workers (default: CPU count)
            chunk_size: Size of data chunks for parallel processing
            significance: Statistical significance level
        """
        self.num_workers = num_workers or max(1, cpu_count() - 1)
        self.chunk_size = chunk_size
        self.significance = significance
        self._results_lock = threading.Lock()
        self._progress_callback: Optional[Callable[[str, float], None]] = None

    def set_progress_callback(
        self, callback: Callable[[str, float], None]
    ) -> None:
        """Set callback for progress updates."""
        self._progress_callback = callback

    def _report_progress(self, test_name: str, progress: float) -> None:
        """Report progress if callback is set."""
        if self._progress_callback:
            self._progress_callback(test_name, progress)

    def generate_test_bits(
        self, rng: Any, num_bits: int
    ) -> List[int]:
        """
        Generate test bits from RNG.

        Args:
            rng: RNG instance with random_bytes method
            num_bits: Number of bits to generate

        Returns:
            List of 0s and 1s
        """
        num_bytes = (num_bits + 7) // 8
        random_bytes = rng.random_bytes(num_bytes)

        bits: List[int] = []
        for byte in random_bytes:
            for i in range(8):
                bits.append((byte >> i) & 1)

        return bits[:num_bits]

    def generate_test_integers(
        self, rng: Any, count: int, min_val: int, max_val: int
    ) -> List[int]:
        """
        Generate test integers from RNG.

        Args:
            rng: RNG instance with random_int method
            count: Number of integers to generate
            min_val: Minimum value (inclusive)
            max_val: Maximum value (inclusive)

        Returns:
            List of random integers
        """
        return [rng.random_int(min_val, max_val) for _ in range(count)]

    def run_parallel_monobit_tests(
        self, bits: List[int], num_chunks: Optional[int] = None
    ) -> List[ParallelTestResult]:
        """
        Run monobit tests in parallel across chunks.

        Args:
            bits: List of bits to test
            num_chunks: Number of chunks (default: num_workers)

        Returns:
            List of test results from each chunk
        """
        num_chunks = num_chunks or self.num_workers
        chunk_size = len(bits) // num_chunks

        chunks = [
            (bits[i * chunk_size : (i + 1) * chunk_size], i, self.significance)
            for i in range(num_chunks)
        ]

        results: List[ParallelTestResult] = []
        start_time = time.perf_counter()

        with ProcessPoolExecutor(max_workers=self.num_workers) as executor:
            futures = {
                executor.submit(_monobit_test_chunk, chunk): i
                for i, chunk in enumerate(chunks)
            }

            for future in as_completed(futures):
                chunk_idx = futures[future]
                passed, p_value, statistic, details = future.result()

                results.append(
                    ParallelTestResult(
                        test_name="Monobit",
                        passed=passed,
                        p_value=p_value,
                        statistic=statistic,
                        execution_time_ms=(time.perf_counter() - start_time) * 1000,
                        worker_id=chunk_idx,
                        samples_tested=chunk_size,
                        details=details,
                    )
                )
                self._report_progress("Monobit", (chunk_idx + 1) / num_chunks)

        return results

    def run_parallel_runs_tests(
        self, bits: List[int], num_chunks: Optional[int] = None
    ) -> List[ParallelTestResult]:
        """Run runs tests in parallel across chunks."""
        num_chunks = num_chunks or self.num_workers
        chunk_size = len(bits) // num_chunks

        chunks = [
            (bits[i * chunk_size : (i + 1) * chunk_size], i, self.significance)
            for i in range(num_chunks)
        ]

        results: List[ParallelTestResult] = []
        start_time = time.perf_counter()

        with ProcessPoolExecutor(max_workers=self.num_workers) as executor:
            futures = {
                executor.submit(_runs_test_chunk, chunk): i
                for i, chunk in enumerate(chunks)
            }

            for future in as_completed(futures):
                chunk_idx = futures[future]
                passed, p_value, statistic, details = future.result()

                results.append(
                    ParallelTestResult(
                        test_name="Runs",
                        passed=passed,
                        p_value=p_value,
                        statistic=statistic,
                        execution_time_ms=(time.perf_counter() - start_time) * 1000,
                        worker_id=chunk_idx,
                        samples_tested=chunk_size,
                        details=details,
                    )
                )

        return results

    def run_parallel_poker_tests(
        self,
        bits: List[int],
        block_size: int = 4,
        num_chunks: Optional[int] = None,
    ) -> List[ParallelTestResult]:
        """Run poker tests in parallel across chunks."""
        num_chunks = num_chunks or self.num_workers
        chunk_size = len(bits) // num_chunks

        chunks = [
            (
                bits[i * chunk_size : (i + 1) * chunk_size],
                block_size,
                i,
                self.significance,
            )
            for i in range(num_chunks)
        ]

        results: List[ParallelTestResult] = []
        start_time = time.perf_counter()

        with ProcessPoolExecutor(max_workers=self.num_workers) as executor:
            futures = {
                executor.submit(_poker_test_chunk, chunk): i
                for i, chunk in enumerate(chunks)
            }

            for future in as_completed(futures):
                chunk_idx = futures[future]
                passed, p_value, statistic, details = future.result()

                results.append(
                    ParallelTestResult(
                        test_name=f"Poker (block={block_size})",
                        passed=passed,
                        p_value=p_value,
                        statistic=statistic,
                        execution_time_ms=(time.perf_counter() - start_time) * 1000,
                        worker_id=chunk_idx,
                        samples_tested=chunk_size,
                        details=details,
                    )
                )

        return results

    def run_parallel_autocorrelation_tests(
        self, bits: List[int], lags: List[int], num_chunks: Optional[int] = None
    ) -> List[ParallelTestResult]:
        """Run autocorrelation tests for multiple lags in parallel."""
        num_chunks = num_chunks or self.num_workers
        chunk_size = len(bits) // num_chunks

        # Create tasks for each chunk and lag combination
        tasks: List[Tuple[List[int], int, int, float]] = []
        for chunk_idx in range(num_chunks):
            chunk_bits = bits[chunk_idx * chunk_size : (chunk_idx + 1) * chunk_size]
            for lag in lags:
                tasks.append((chunk_bits, lag, chunk_idx, self.significance))

        results: List[ParallelTestResult] = []
        start_time = time.perf_counter()

        with ProcessPoolExecutor(max_workers=self.num_workers) as executor:
            futures = {
                executor.submit(_autocorrelation_test_chunk, task): task
                for task in tasks
            }

            for future in as_completed(futures):
                task = futures[future]
                _, lag, chunk_idx, _ = task
                passed, p_value, statistic, details = future.result()

                results.append(
                    ParallelTestResult(
                        test_name=f"Autocorrelation (lag={lag})",
                        passed=passed,
                        p_value=p_value,
                        statistic=statistic,
                        execution_time_ms=(time.perf_counter() - start_time) * 1000,
                        worker_id=chunk_idx,
                        samples_tested=chunk_size,
                        details=details,
                    )
                )

        return results

    def run_parallel_chi_square_tests(
        self,
        integers: List[int],
        num_categories: int,
        num_chunks: Optional[int] = None,
    ) -> List[ParallelTestResult]:
        """Run chi-square uniformity tests in parallel."""
        num_chunks = num_chunks or self.num_workers
        chunk_size = len(integers) // num_chunks

        chunks = [
            (
                integers[i * chunk_size : (i + 1) * chunk_size],
                num_categories,
                i,
                self.significance,
            )
            for i in range(num_chunks)
        ]

        results: List[ParallelTestResult] = []
        start_time = time.perf_counter()

        with ProcessPoolExecutor(max_workers=self.num_workers) as executor:
            futures = {
                executor.submit(_chi_square_test_chunk, chunk): i
                for i, chunk in enumerate(chunks)
            }

            for future in as_completed(futures):
                chunk_idx = futures[future]
                passed, p_value, statistic, details = future.result()

                results.append(
                    ParallelTestResult(
                        test_name=f"Chi-Square ({num_categories} categories)",
                        passed=passed,
                        p_value=p_value,
                        statistic=statistic,
                        execution_time_ms=(time.perf_counter() - start_time) * 1000,
                        worker_id=chunk_idx,
                        samples_tested=chunk_size,
                        details=details,
                    )
                )

        return results

    def run_comprehensive_parallel_tests(
        self, rng: Any, num_samples: int = 10_000_000
    ) -> ValidationReport:
        """
        Run comprehensive parallel tests on RNG.

        Args:
            rng: RNG instance to test
            num_samples: Total number of samples to generate

        Returns:
            ValidationReport with all results
        """
        logger.info(
            f"Starting comprehensive parallel tests with {num_samples:,} samples "
            f"using {self.num_workers} workers"
        )

        start_time = time.perf_counter()
        all_results: List[ParallelTestResult] = []
        performance_metrics: List[PerformanceMetrics] = []

        # 1. Generate bits for bit-based tests
        logger.info("Generating test bits...")
        gen_start = time.perf_counter()
        bits = self.generate_test_bits(rng, num_samples)
        gen_time = (time.perf_counter() - gen_start) * 1000

        performance_metrics.append(
            PerformanceMetrics(
                test_name="Bit Generation",
                execution_time_ms=gen_time,
                samples_processed=num_samples,
                throughput_per_sec=num_samples / (gen_time / 1000),
            )
        )

        # 2. Run monobit tests
        logger.info("Running parallel monobit tests...")
        test_start = time.perf_counter()
        monobit_results = self.run_parallel_monobit_tests(bits)
        all_results.extend(monobit_results)

        performance_metrics.append(
            PerformanceMetrics(
                test_name="Monobit Tests",
                execution_time_ms=(time.perf_counter() - test_start) * 1000,
                samples_processed=num_samples,
                throughput_per_sec=num_samples
                / (time.perf_counter() - test_start),
            )
        )

        # 3. Run runs tests
        logger.info("Running parallel runs tests...")
        test_start = time.perf_counter()
        runs_results = self.run_parallel_runs_tests(bits)
        all_results.extend(runs_results)

        performance_metrics.append(
            PerformanceMetrics(
                test_name="Runs Tests",
                execution_time_ms=(time.perf_counter() - test_start) * 1000,
                samples_processed=num_samples,
                throughput_per_sec=num_samples
                / (time.perf_counter() - test_start),
            )
        )

        # 4. Run poker tests
        logger.info("Running parallel poker tests...")
        test_start = time.perf_counter()
        poker_results = self.run_parallel_poker_tests(bits, block_size=4)
        all_results.extend(poker_results)

        performance_metrics.append(
            PerformanceMetrics(
                test_name="Poker Tests",
                execution_time_ms=(time.perf_counter() - test_start) * 1000,
                samples_processed=num_samples,
                throughput_per_sec=num_samples
                / (time.perf_counter() - test_start),
            )
        )

        # 5. Run autocorrelation tests
        logger.info("Running parallel autocorrelation tests...")
        test_start = time.perf_counter()
        autocorr_results = self.run_parallel_autocorrelation_tests(
            bits, lags=[1, 2, 4, 8, 16]
        )
        all_results.extend(autocorr_results)

        performance_metrics.append(
            PerformanceMetrics(
                test_name="Autocorrelation Tests",
                execution_time_ms=(time.perf_counter() - test_start) * 1000,
                samples_processed=num_samples,
                throughput_per_sec=num_samples
                / (time.perf_counter() - test_start),
            )
        )

        # 6. Generate integers for uniformity tests
        logger.info("Generating test integers...")
        int_samples = num_samples // 10
        dice_values = self.generate_test_integers(rng, int_samples, 0, 5)
        card_values = self.generate_test_integers(rng, int_samples, 0, 51)

        # 7. Run dice uniformity tests
        logger.info("Running parallel dice uniformity tests...")
        test_start = time.perf_counter()
        dice_results = self.run_parallel_chi_square_tests(dice_values, 6)
        for r in dice_results:
            r.test_name = "Dice Uniformity (1-6)"
        all_results.extend(dice_results)

        performance_metrics.append(
            PerformanceMetrics(
                test_name="Dice Uniformity Tests",
                execution_time_ms=(time.perf_counter() - test_start) * 1000,
                samples_processed=int_samples,
                throughput_per_sec=int_samples
                / (time.perf_counter() - test_start),
            )
        )

        # 8. Run card uniformity tests
        logger.info("Running parallel card uniformity tests...")
        test_start = time.perf_counter()
        card_results = self.run_parallel_chi_square_tests(card_values, 52)
        for r in card_results:
            r.test_name = "Card Uniformity (0-51)"
        all_results.extend(card_results)

        performance_metrics.append(
            PerformanceMetrics(
                test_name="Card Uniformity Tests",
                execution_time_ms=(time.perf_counter() - test_start) * 1000,
                samples_processed=int_samples,
                throughput_per_sec=int_samples
                / (time.perf_counter() - test_start),
            )
        )

        # Calculate final results
        total_time = (time.perf_counter() - start_time) * 1000
        tests_passed = sum(1 for r in all_results if r.passed)
        tests_total = len(all_results)

        # Calculate pass rate per test type
        pass_rates: Dict[str, List[float]] = {}
        for r in all_results:
            base_name = r.test_name.split(" (")[0]
            if base_name not in pass_rates:
                pass_rates[base_name] = []
            pass_rates[base_name].append(1.0 if r.passed else 0.0)

        avg_pass_rates = {
            name: statistics.mean(rates) for name, rates in pass_rates.items()
        }

        # Overall score (weighted average of pass rates)
        score = statistics.mean(avg_pass_rates.values()) * 100

        # Determine validity (95% threshold)
        is_valid = score >= 95.0

        # Generate recommendations
        recommendations: List[str] = []
        for name, rate in avg_pass_rates.items():
            if rate < 0.95:
                recommendations.append(
                    f"{name} tests show {rate*100:.1f}% pass rate - "
                    "investigate RNG quality for this test type"
                )

        if not recommendations:
            recommendations.append("All test categories passed - RNG is suitable for casino use")

        summary = (
            f"Comprehensive Parallel Validation: {tests_passed}/{tests_total} tests passed "
            f"({score:.1f}% score) in {total_time:.0f}ms"
        )

        if is_valid:
            summary += " - RNG APPROVED FOR CASINO USE"
        else:
            summary += " - RNG FAILED VALIDATION"

        return ValidationReport(
            is_valid=is_valid,
            score=score,
            tests_passed=tests_passed,
            tests_total=tests_total,
            total_samples=num_samples,
            total_time_ms=total_time,
            throughput=num_samples / (total_time / 1000),
            test_results=all_results,
            performance_metrics=performance_metrics,
            summary=summary,
            recommendations=recommendations,
        )


# =============================================================================
# Thread-Based Stress Testing
# =============================================================================


class RNGStressTester:
    """
    Stress testing for RNG systems using threads.

    Tests RNG behavior under concurrent access and high load conditions.
    """

    def __init__(self, num_threads: int = 16):
        """
        Initialize stress tester.

        Args:
            num_threads: Number of concurrent threads
        """
        self.num_threads = num_threads
        self._results_queue: queue.Queue[Dict[str, Any]] = queue.Queue()
        self._stop_event = threading.Event()

    def _worker_thread(
        self,
        thread_id: int,
        rng: Any,
        operations: int,
        operation_type: str,
    ) -> None:
        """Worker thread for stress testing."""
        start_time = time.perf_counter()
        errors = 0
        values: List[Any] = []

        try:
            for _ in range(operations):
                if self._stop_event.is_set():
                    break

                try:
                    if operation_type == "bytes":
                        value = rng.random_bytes(32)
                    elif operation_type == "int":
                        value = rng.random_int(1, 1000000)
                    elif operation_type == "float":
                        value = rng.random_float()
                    else:
                        value = rng.random_bytes(16)

                    values.append(value)
                except Exception:
                    errors += 1

        except Exception as e:
            logger.error(f"Thread {thread_id} error: {e}")
            errors += 1

        elapsed = time.perf_counter() - start_time

        self._results_queue.put({
            "thread_id": thread_id,
            "operations": len(values),
            "errors": errors,
            "elapsed_ms": elapsed * 1000,
            "ops_per_sec": len(values) / elapsed if elapsed > 0 else 0,
        })

    def run_stress_test(
        self,
        rng: Any,
        duration_seconds: float = 10.0,
        operations_per_thread: int = 100000,
        operation_type: str = "bytes",
    ) -> Dict[str, Any]:
        """
        Run stress test on RNG.

        Args:
            rng: RNG instance to test
            duration_seconds: Maximum test duration
            operations_per_thread: Operations per thread
            operation_type: Type of operation ("bytes", "int", "float")

        Returns:
            Stress test results
        """
        logger.info(
            f"Starting stress test with {self.num_threads} threads, "
            f"{operations_per_thread:,} ops/thread"
        )

        self._stop_event.clear()
        threads: List[threading.Thread] = []

        start_time = time.perf_counter()

        # Start threads
        for i in range(self.num_threads):
            t = threading.Thread(
                target=self._worker_thread,
                args=(i, rng, operations_per_thread, operation_type),
            )
            threads.append(t)
            t.start()

        # Wait for completion or timeout
        for t in threads:
            remaining = duration_seconds - (time.perf_counter() - start_time)
            if remaining > 0:
                t.join(timeout=remaining)
            else:
                self._stop_event.set()
                t.join(timeout=1.0)

        total_time = time.perf_counter() - start_time

        # Collect results
        thread_results: List[Dict[str, Any]] = []
        while not self._results_queue.empty():
            thread_results.append(self._results_queue.get())

        total_ops = sum(r["operations"] for r in thread_results)
        total_errors = sum(r["errors"] for r in thread_results)
        avg_ops_per_sec = statistics.mean(
            r["ops_per_sec"] for r in thread_results
        ) if thread_results else 0

        return {
            "num_threads": self.num_threads,
            "total_operations": total_ops,
            "total_errors": total_errors,
            "error_rate": total_errors / total_ops if total_ops > 0 else 0,
            "total_time_ms": total_time * 1000,
            "throughput_ops_per_sec": total_ops / total_time if total_time > 0 else 0,
            "avg_thread_ops_per_sec": avg_ops_per_sec,
            "thread_results": thread_results,
            "passed": total_errors == 0,
        }


# =============================================================================
# Convenience Functions
# =============================================================================


def run_full_validation(
    rng: Any,
    num_samples: int = 10_000_000,
    parallel: bool = True,
    num_workers: Optional[int] = None,
) -> ValidationReport:
    """
    Run full RNG validation.

    Args:
        rng: RNG instance to test
        num_samples: Number of samples to test
        parallel: Whether to use parallel execution
        num_workers: Number of workers (default: CPU count - 1)

    Returns:
        ValidationReport with all results
    """
    if parallel:
        tester = RNGPerformanceTester(num_workers=num_workers)
        return tester.run_comprehensive_parallel_tests(rng, num_samples)
    else:
        # Single-threaded fallback
        tester = RNGPerformanceTester(num_workers=1)
        return tester.run_comprehensive_parallel_tests(rng, num_samples)


def run_quick_validation(rng: Any, num_samples: int = 100_000) -> ValidationReport:
    """
    Run quick validation with reduced samples.

    Args:
        rng: RNG instance to test
        num_samples: Number of samples (default: 100,000)

    Returns:
        ValidationReport with results
    """
    tester = RNGPerformanceTester(num_workers=4)
    return tester.run_comprehensive_parallel_tests(rng, num_samples)


def benchmark_rng(rng: Any, duration_seconds: float = 5.0) -> Dict[str, Any]:
    """
    Benchmark RNG throughput.

    Args:
        rng: RNG instance to benchmark
        duration_seconds: Duration of benchmark

    Returns:
        Benchmark results
    """
    stress_tester = RNGStressTester(num_threads=cpu_count())

    results = {
        "bytes_benchmark": stress_tester.run_stress_test(
            rng, duration_seconds, 50000, "bytes"
        ),
        "int_benchmark": stress_tester.run_stress_test(
            rng, duration_seconds, 50000, "int"
        ),
        "float_benchmark": stress_tester.run_stress_test(
            rng, duration_seconds, 50000, "float"
        ),
    }

    return results


# =============================================================================
# Main Entry Point
# =============================================================================


if __name__ == "__main__":
    # Example usage with mock RNG
    import secrets

    class MockRNG:
        """Mock RNG for testing."""

        def random_bytes(self, n: int) -> bytes:
            return secrets.token_bytes(n)

        def random_int(self, min_val: int, max_val: int) -> int:
            return secrets.randbelow(max_val - min_val + 1) + min_val

        def random_float(self) -> float:
            return secrets.randbelow(2**53) / (2**53)

    print("=" * 60)
    print("RNG Performance Testing Suite")
    print("=" * 60)

    rng = MockRNG()

    # Quick validation
    print("\n[1] Running quick validation (100K samples)...")
    result = run_quick_validation(rng, 100_000)
    print(f"Result: {result.summary}")
    print(f"Score: {result.score:.1f}%")
    print(f"Time: {result.total_time_ms:.0f}ms")
    print(f"Throughput: {result.throughput:,.0f} samples/sec")

    # Stress test
    print("\n[2] Running stress test (5 seconds)...")
    stress_tester = RNGStressTester(num_threads=8)
    stress_result = stress_tester.run_stress_test(rng, 5.0, 50000, "bytes")
    print(f"Total ops: {stress_result['total_operations']:,}")
    print(f"Throughput: {stress_result['throughput_ops_per_sec']:,.0f} ops/sec")
    print(f"Errors: {stress_result['total_errors']}")
    print(f"Passed: {stress_result['passed']}")

    print("\n" + "=" * 60)
    print("Testing complete!")
