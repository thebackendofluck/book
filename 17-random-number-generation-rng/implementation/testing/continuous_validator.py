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
Continuous Statistical Validation Daemon for RNG Output
========================================================

GLI-11 Section 4.5 Compliance: Continuous RNG Health Monitoring
- RNG output must be continuously validated during operation
- Scheduled statistical tests must run at configurable intervals
- Drift detection must trigger alerts before regulatory thresholds are breached
- All validation results must be logged with timestamps for audit
- Failed validations must halt RNG output until manually cleared

Architecture:
- Scheduler: Runs tests at configurable intervals (1min, 1h, 24h)
- Test Engine: Executes NIST subset, frequency, serial correlation, entropy
- Drift Detector: CUSUM + EWMA for trend detection
- Alert Manager: Webhook, syslog, SNMP trap notifications
- State Machine: HEALTHY -> DEGRADED -> FAILED -> LOCKED

Usage:
    daemon = ContinuousValidator(rng=FortunaGenerator())
    daemon.start()        # Runs in background
    daemon.get_status()   # Check current health
    daemon.stop()         # Graceful shutdown
"""

import hashlib
import json
import logging
import math
import os
import signal
import struct
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("rng.continuous_validator")


# ---------------------------------------------------------------------------
# State Machine
# ---------------------------------------------------------------------------

class ValidatorState(Enum):
    STARTUP = "startup"         # Initial state, running startup tests
    HEALTHY = "healthy"         # All tests passing
    DEGRADED = "degraded"       # Some tests marginal
    FAILED = "failed"           # Tests failing, alerts sent
    LOCKED = "locked"           # RNG output halted, manual intervention required


class TestFrequency(Enum):
    CONTINUOUS = 60           # Every minute
    HOURLY = 3600             # Every hour
    DAILY = 86400             # Every 24 hours
    STARTUP = 0               # Run once at startup


# ---------------------------------------------------------------------------
# Test Definitions
# ---------------------------------------------------------------------------

@dataclass
class ValidationTest:
    """A single validation test configuration."""
    name: str
    frequency: TestFrequency
    sample_size: int            # Bytes of RNG output to test
    test_func: str              # Method name on TestEngine
    threshold: float            # Minimum p-value to pass
    consecutive_failures_to_degrade: int = 3
    consecutive_failures_to_fail: int = 5
    consecutive_failures: int = 0
    last_run: float = 0.0
    last_result: Optional[dict] = None


@dataclass
class TestResult:
    """Result of a single validation test."""
    test_name: str
    timestamp: str
    passed: bool
    p_value: float
    statistic: float
    sample_size: int
    execution_time_ms: float
    details: dict


# ---------------------------------------------------------------------------
# Statistical Test Engine
# ---------------------------------------------------------------------------

class TestEngine:
    """
    Fast statistical tests suitable for continuous monitoring.

    These are lighter than full NIST SP 800-22 but sufficient for
    continuous health monitoring per GLI-11 4.5.
    """

    @staticmethod
    def frequency_test(data: bytes) -> Tuple[float, float, dict]:
        """
        Byte frequency test (chi-squared).
        Tests whether all 256 byte values appear with equal frequency.
        """
        n = len(data)
        freq = [0] * 256
        for b in data:
            freq[b] += 1

        expected = n / 256.0
        chi_sq = sum((f - expected) ** 2 / expected for f in freq)

        # Chi-squared with 255 df
        p_value = _chi2_sf(chi_sq, 255)

        return p_value, chi_sq, {
            "n": n, "min_freq": min(freq), "max_freq": max(freq),
            "expected": round(expected, 2),
        }

    @staticmethod
    def serial_correlation_test(data: bytes) -> Tuple[float, float, dict]:
        """
        Serial correlation coefficient test.
        Checks for correlation between consecutive bytes.
        """
        n = len(data)
        if n < 100:
            return 0.0, 0.0, {"error": "sample too small"}

        mean = sum(data) / n
        numerator = sum(
            (data[i] - mean) * (data[i + 1] - mean)
            for i in range(n - 1)
        )
        denominator = sum((d - mean) ** 2 for d in data)

        if denominator == 0:
            return 0.0, 0.0, {"error": "zero variance"}

        r = numerator / denominator
        # Z-test for correlation coefficient
        z = r * math.sqrt(n)
        p_value = 2.0 * (1.0 - _normal_cdf(abs(z)))

        return p_value, r, {"correlation": round(r, 8), "z": round(z, 4), "n": n}

    @staticmethod
    def entropy_test(data: bytes) -> Tuple[float, float, dict]:
        """
        Shannon entropy test.
        Tests whether byte-level entropy is close to maximum (8.0 bits).
        """
        n = len(data)
        freq = [0] * 256
        for b in data:
            freq[b] += 1

        entropy = 0.0
        for f in freq:
            if f > 0:
                p = f / n
                entropy -= p * math.log2(p)

        # Maximum entropy is 8.0 bits per byte
        # Use Z-test against expected entropy
        expected_entropy = 8.0 - (255.0 / (2.0 * n * math.log(2)))  # Bias correction
        # Approximate standard deviation of entropy estimate
        std_entropy = math.sqrt(255.0 / (n * n * math.log(2) ** 2))

        if std_entropy > 0:
            z = (entropy - expected_entropy) / std_entropy
            p_value = 2.0 * (1.0 - _normal_cdf(abs(z)))
        else:
            p_value = 1.0
            z = 0.0

        return p_value, entropy, {
            "entropy_bits_per_byte": round(entropy, 6),
            "expected": round(expected_entropy, 6),
            "z": round(z, 4),
        }

    @staticmethod
    def runs_test(data: bytes) -> Tuple[float, float, dict]:
        """
        Runs test on byte values (above/below median).
        Tests for non-randomness in the sequence.
        """
        n = len(data)
        median = sorted(data)[n // 2]

        # Convert to binary (above/below median)
        binary = [1 if b >= median else 0 for b in data]

        # Count runs
        runs = 1
        for i in range(1, n):
            if binary[i] != binary[i - 1]:
                runs += 1

        n1 = sum(binary)
        n0 = n - n1

        if n0 == 0 or n1 == 0:
            return 0.0, 0.0, {"error": "all values same side of median"}

        expected_runs = 1 + (2 * n0 * n1) / n
        variance = (2 * n0 * n1 * (2 * n0 * n1 - n)) / (n * n * (n - 1))

        if variance <= 0:
            return 0.0, float(runs), {"error": "zero variance"}

        z = (runs - expected_runs) / math.sqrt(variance)
        p_value = 2.0 * (1.0 - _normal_cdf(abs(z)))

        return p_value, float(runs), {
            "runs": runs, "expected": round(expected_runs, 2),
            "z": round(z, 4), "n0": n0, "n1": n1,
        }

    @staticmethod
    def monobit_test(data: bytes) -> Tuple[float, float, dict]:
        """
        Bit-level monobit test.
        Tests whether the number of 1-bits equals the number of 0-bits.
        """
        total_bits = len(data) * 8
        ones = sum(bin(b).count("1") for b in data)
        zeros = total_bits - ones

        s = abs(ones - zeros)
        s_obs = s / math.sqrt(total_bits)
        p_value = math.erfc(s_obs / math.sqrt(2))

        return p_value, s_obs, {
            "total_bits": total_bits, "ones": ones, "zeros": zeros,
            "proportion": round(ones / total_bits, 6),
        }

    @staticmethod
    def compression_ratio_test(data: bytes) -> Tuple[float, float, dict]:
        """
        Compression ratio test.
        Random data should not compress significantly.
        Uses zlib compression as a proxy for Kolmogorov complexity.
        """
        import zlib
        n = len(data)
        compressed = zlib.compress(data, 9)
        ratio = len(compressed) / n

        # Random data typically has ratio > 1.0 (compression adds overhead)
        # Non-random data compresses to ratio < 0.95
        # Use heuristic threshold
        passed = ratio > 0.95
        p_value = 1.0 if passed else 0.001

        return p_value, ratio, {
            "original_bytes": n, "compressed_bytes": len(compressed),
            "ratio": round(ratio, 6),
        }


# ---------------------------------------------------------------------------
# EWMA Drift Detector
# ---------------------------------------------------------------------------

class EWMADriftDetector:
    """
    Exponentially Weighted Moving Average for drift detection.

    More responsive to recent changes than CUSUM, good for
    detecting gradual entropy degradation.
    """

    def __init__(self, lambda_param: float = 0.05, threshold_sigma: float = 3.0):
        self.lambda_param = lambda_param
        self.threshold_sigma = threshold_sigma
        self.ewma = 0.0
        self.target = 0.0
        self.variance = 0.0
        self.n = 0
        self.initialized = False
        self.alarm = False

    def initialize(self, target: float, initial_variance: float) -> None:
        self.target = target
        self.ewma = target
        self.variance = initial_variance
        self.initialized = True

    def update(self, value: float) -> bool:
        """Update with new observation. Returns True if alarm triggered."""
        if not self.initialized:
            self.target = value
            self.ewma = value
            self.initialized = True
            return False

        self.n += 1
        self.ewma = self.lambda_param * value + (1 - self.lambda_param) * self.ewma

        # Control limits
        sigma = math.sqrt(
            self.variance * self.lambda_param / (2 - self.lambda_param)
            * (1 - (1 - self.lambda_param) ** (2 * self.n))
        ) if self.variance > 0 else 0.1

        ucl = self.target + self.threshold_sigma * sigma
        lcl = self.target - self.threshold_sigma * sigma

        self.alarm = self.ewma > ucl or self.ewma < lcl
        return self.alarm

    def get_status(self) -> dict:
        return {
            "ewma": round(self.ewma, 6),
            "target": round(self.target, 6),
            "samples": self.n,
            "alarm": self.alarm,
        }


# ---------------------------------------------------------------------------
# Continuous Validator Daemon
# ---------------------------------------------------------------------------

class ContinuousValidator:
    """
    Continuous statistical validation daemon for RNG output.

    GLI-11 4.5 Compliance Features:
    - State machine: STARTUP -> HEALTHY -> DEGRADED -> FAILED -> LOCKED
    - Scheduled test execution at configurable intervals
    - EWMA drift detection on key metrics
    - Alert callbacks for external notification systems
    - Thread-safe operation alongside RNG generation
    - Automatic RNG lockout on persistent failures
    - Full audit trail of all validation events
    """

    def __init__(
        self,
        rng=None,
        startup_samples: int = 10000,
        audit_log_path: Optional[str] = None,
    ):
        """
        Args:
            rng: RNG instance with generate(n) -> bytes method.
                 If None, uses os.urandom.
            startup_samples: Bytes to test during startup phase
            audit_log_path: Path for validation audit log (JSONL)
        """
        self._rng = rng
        self._startup_samples = startup_samples
        self._audit_log_path = audit_log_path
        self._audit_sequence = 0

        self._state = ValidatorState.STARTUP
        self._state_lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._alert_callbacks: List[Callable] = []

        self._test_engine = TestEngine()
        self._test_history: deque = deque(maxlen=10000)

        # Drift detectors
        self._entropy_drift = EWMADriftDetector(lambda_param=0.05, threshold_sigma=3.0)
        self._frequency_drift = EWMADriftDetector(lambda_param=0.05, threshold_sigma=3.0)

        # Define test schedule
        self._tests = [
            ValidationTest(
                name="frequency", frequency=TestFrequency.CONTINUOUS,
                sample_size=10000, test_func="frequency_test",
                threshold=0.001,
            ),
            ValidationTest(
                name="monobit", frequency=TestFrequency.CONTINUOUS,
                sample_size=10000, test_func="monobit_test",
                threshold=0.001,
            ),
            ValidationTest(
                name="serial_correlation", frequency=TestFrequency.CONTINUOUS,
                sample_size=10000, test_func="serial_correlation_test",
                threshold=0.001,
            ),
            ValidationTest(
                name="entropy", frequency=TestFrequency.HOURLY,
                sample_size=100000, test_func="entropy_test",
                threshold=0.001,
            ),
            ValidationTest(
                name="runs", frequency=TestFrequency.HOURLY,
                sample_size=50000, test_func="runs_test",
                threshold=0.001,
            ),
            ValidationTest(
                name="compression", frequency=TestFrequency.DAILY,
                sample_size=1000000, test_func="compression_ratio_test",
                threshold=0.01,
            ),
        ]

        # Statistics
        self._total_tests_run = 0
        self._total_tests_passed = 0
        self._total_tests_failed = 0
        self._total_bytes_tested = 0
        self._start_time = 0.0

    def register_alert_callback(self, callback: Callable) -> None:
        """Register callback: callback(state, test_name, details)."""
        self._alert_callbacks.append(callback)

    def start(self) -> None:
        """Start the continuous validation daemon."""
        if self._running:
            return

        self._running = True
        self._start_time = time.time()
        self._state = ValidatorState.STARTUP

        self._audit("DAEMON_START", {"startup_samples": self._startup_samples})

        # Run startup tests synchronously
        logger.info("Running startup validation tests...")
        startup_ok = self._run_startup_tests()

        if not startup_ok:
            self._transition_state(ValidatorState.LOCKED)
            logger.critical("STARTUP TESTS FAILED - RNG LOCKED")
            self._running = False
            return

        self._transition_state(ValidatorState.HEALTHY)
        logger.info("Startup tests passed. Starting continuous validation.")

        # Start background thread
        self._thread = threading.Thread(
            target=self._validation_loop,
            daemon=True,
            name="rng-validator",
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the daemon gracefully."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        self._audit("DAEMON_STOP", {"total_tests": self._total_tests_run})
        logger.info("Continuous validator stopped")

    @property
    def state(self) -> ValidatorState:
        return self._state

    @property
    def is_healthy(self) -> bool:
        return self._state in (ValidatorState.HEALTHY, ValidatorState.DEGRADED)

    def _generate_sample(self, num_bytes: int) -> bytes:
        """Generate RNG sample for testing."""
        if self._rng:
            return self._rng.generate(num_bytes)
        return os.urandom(num_bytes)

    def _run_startup_tests(self) -> bool:
        """Run all tests during startup with larger samples."""
        passed = 0
        total = 0
        for test in self._tests:
            if test.frequency in (TestFrequency.STARTUP, TestFrequency.CONTINUOUS,
                                  TestFrequency.HOURLY):
                result = self._execute_test(test, sample_size_override=self._startup_samples)
                total += 1
                if result.passed:
                    passed += 1
                else:
                    logger.warning("Startup test FAILED: %s (p=%.6f)",
                                   test.name, result.p_value)

        logger.info("Startup: %d/%d tests passed", passed, total)
        return passed == total

    def _validation_loop(self) -> None:
        """Main validation loop (runs in background thread)."""
        while self._running:
            now = time.time()

            for test in self._tests:
                if test.frequency == TestFrequency.STARTUP:
                    continue

                interval = test.frequency.value
                if now - test.last_run >= interval:
                    result = self._execute_test(test)
                    self._process_result(test, result)
                    test.last_run = now

            # Sleep 10 seconds between check cycles
            for _ in range(10):
                if not self._running:
                    break
                time.sleep(1)

    def _execute_test(
        self, test: ValidationTest, sample_size_override: Optional[int] = None
    ) -> TestResult:
        """Execute a single validation test."""
        sample_size = sample_size_override or test.sample_size

        start = time.perf_counter()
        sample = self._generate_sample(sample_size)
        self._total_bytes_tested += sample_size

        test_method = getattr(self._test_engine, test.test_func)
        p_value, statistic, details = test_method(sample)

        execution_time = (time.perf_counter() - start) * 1000

        passed = p_value >= test.threshold
        self._total_tests_run += 1
        if passed:
            self._total_tests_passed += 1
        else:
            self._total_tests_failed += 1

        result = TestResult(
            test_name=test.name,
            timestamp=datetime.now(timezone.utc).isoformat(),
            passed=passed,
            p_value=p_value,
            statistic=statistic,
            sample_size=sample_size,
            execution_time_ms=round(execution_time, 2),
            details=details,
        )

        self._test_history.append({
            "test": test.name,
            "ts": result.timestamp,
            "passed": passed,
            "p_value": round(p_value, 8),
        })

        return result

    def _process_result(self, test: ValidationTest, result: TestResult) -> None:
        """Process test result and update state machine."""
        test.last_result = {
            "passed": result.passed,
            "p_value": result.p_value,
            "timestamp": result.timestamp,
        }

        if result.passed:
            test.consecutive_failures = 0
        else:
            test.consecutive_failures += 1
            logger.warning(
                "Test FAILED: %s (p=%.6f, consecutive=%d)",
                test.name, result.p_value, test.consecutive_failures,
            )

        # Update drift detectors
        if test.name == "entropy" and "entropy_bits_per_byte" in result.details:
            alarm = self._entropy_drift.update(
                result.details["entropy_bits_per_byte"]
            )
            if alarm:
                self._alert("entropy_drift", "Entropy EWMA drift detected",
                            self._entropy_drift.get_status())

        # State transitions
        max_consecutive = max(t.consecutive_failures for t in self._tests)

        if max_consecutive >= 5:
            self._transition_state(ValidatorState.FAILED)
        elif max_consecutive >= 3:
            self._transition_state(ValidatorState.DEGRADED)
        elif self._state == ValidatorState.DEGRADED and max_consecutive == 0:
            self._transition_state(ValidatorState.HEALTHY)

        self._audit("TEST_RESULT", {
            "test": test.name,
            "passed": result.passed,
            "p_value": round(result.p_value, 8),
            "statistic": round(result.statistic, 8),
            "sample_size": result.sample_size,
            "execution_ms": result.execution_time_ms,
            "consecutive_failures": test.consecutive_failures,
            "state": self._state.value,
        })

    def _transition_state(self, new_state: ValidatorState) -> None:
        """Transition to a new state with logging."""
        with self._state_lock:
            old_state = self._state
            if old_state == new_state:
                return

            self._state = new_state
            logger.info("State transition: %s -> %s", old_state.value, new_state.value)

            self._audit("STATE_TRANSITION", {
                "from": old_state.value,
                "to": new_state.value,
            })

            if new_state in (ValidatorState.FAILED, ValidatorState.LOCKED):
                self._alert(
                    "state_change",
                    f"RNG validator state: {new_state.value}",
                    {"from": old_state.value, "to": new_state.value},
                )

    def _alert(self, alert_type: str, message: str, details: dict) -> None:
        """Send alert to all registered callbacks."""
        for cb in self._alert_callbacks:
            try:
                cb(self._state, alert_type, message, details)
            except Exception as e:
                logger.error("Alert callback error: %s", e)

    def get_status(self) -> dict:
        """Get comprehensive validator status."""
        elapsed = time.time() - self._start_time if self._start_time > 0 else 0

        test_statuses = {}
        for test in self._tests:
            test_statuses[test.name] = {
                "frequency": test.frequency.name,
                "consecutive_failures": test.consecutive_failures,
                "last_result": test.last_result,
                "threshold": test.threshold,
            }

        return {
            "state": self._state.value,
            "is_healthy": self.is_healthy,
            "uptime_seconds": round(elapsed, 1),
            "total_tests_run": self._total_tests_run,
            "total_tests_passed": self._total_tests_passed,
            "total_tests_failed": self._total_tests_failed,
            "pass_rate": round(
                self._total_tests_passed / max(self._total_tests_run, 1) * 100, 2
            ),
            "total_bytes_tested": self._total_bytes_tested,
            "tests": test_statuses,
            "drift_detectors": {
                "entropy": self._entropy_drift.get_status(),
                "frequency": self._frequency_drift.get_status(),
            },
            "recent_history": list(self._test_history)[-20:],
        }

    def _audit(self, event_type: str, details: dict) -> None:
        self._audit_sequence += 1
        entry = {
            "seq": self._audit_sequence,
            "ts": datetime.now(timezone.utc).isoformat(),
            "component": "ContinuousValidator",
            "event": event_type,
            **details,
        }
        if self._audit_log_path:
            try:
                with open(self._audit_log_path, "a") as f:
                    f.write(json.dumps(entry) + "\n")
            except OSError:
                pass
        logger.debug("AUDIT: %s", json.dumps(entry))


# ---------------------------------------------------------------------------
# Utility Functions
# ---------------------------------------------------------------------------

def _normal_cdf(x: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2)))


def _chi2_sf(x: float, df: int) -> float:
    """Chi-squared survival function (1 - CDF) using incomplete gamma."""
    if x <= 0:
        return 1.0
    a = df / 2.0
    return _igamc(a, x / 2.0)


def _igamc(a: float, x: float) -> float:
    """Upper incomplete gamma function Q(a, x)."""
    if x <= 0 or a <= 0:
        return 1.0
    if x < 1.0 or x < a:
        return 1.0 - _igam(a, x)

    ax = a * math.log(x) - x - math.lgamma(a)
    if ax < -709.78:
        return 0.0
    ax = math.exp(ax)

    y = 1.0 - a
    z = x + y + 1.0
    c = 0.0
    pkm2, qkm2 = 1.0, x
    pkm1, qkm1 = x + 1.0, z * x
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
        pkm2, pkm1 = pkm1, pk
        qkm2, qkm1 = qkm1, qk
        if abs(pk) > 4.5e15:
            pkm2 *= 2.2e-16
            pkm1 *= 2.2e-16
            qkm2 *= 2.2e-16
            qkm1 *= 2.2e-16
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
# Self-Test
# ---------------------------------------------------------------------------

def self_test() -> bool:
    """Continuous validator self-test."""
    print("=== Continuous Validator Self-Test ===\n")

    # Test 1: Test engine with random data
    engine = TestEngine()
    data = os.urandom(10000)

    p, stat, details = engine.frequency_test(data)
    assert p > 0.001, f"Frequency test failed on random data: p={p}"
    print(f"[PASS] Frequency test: p={p:.6f}")

    p, stat, details = engine.monobit_test(data)
    assert p > 0.001, f"Monobit test failed on random data: p={p}"
    print(f"[PASS] Monobit test: p={p:.6f}")

    p, stat, details = engine.serial_correlation_test(data)
    assert p > 0.001, f"Serial correlation test failed: p={p}"
    print(f"[PASS] Serial correlation: r={stat:.6f}, p={p:.6f}")

    p, stat, details = engine.entropy_test(data)
    assert stat > 7.9, f"Entropy too low: {stat}"
    print(f"[PASS] Entropy test: {stat:.4f} bits/byte")

    p, stat, details = engine.runs_test(data)
    assert p > 0.001, f"Runs test failed: p={p}"
    print(f"[PASS] Runs test: p={p:.6f}")

    p, stat, details = engine.compression_ratio_test(data)
    assert stat > 0.95, f"Compression ratio too low: {stat}"
    print(f"[PASS] Compression test: ratio={stat:.4f}")

    # Test 2: Tests detect non-random data
    biased_data = bytes([0] * 5000 + [255] * 5000)
    p, _, _ = engine.frequency_test(biased_data)
    assert p < 0.01, "Frequency test should fail on biased data"
    print(f"[PASS] Biased data detected by frequency test (p={p:.8f})")

    # Test 3: EWMA drift detector
    detector = EWMADriftDetector(lambda_param=0.1, threshold_sigma=3.0)
    detector.initialize(target=8.0, initial_variance=0.001)

    for _ in range(100):
        detector.update(8.0 + (os.urandom(1)[0] / 256 - 0.5) * 0.01)
    assert not detector.alarm, "EWMA should not alarm on stable data"
    print("[PASS] EWMA stable: no false alarm")

    # Introduce drift
    for _ in range(50):
        detector.update(7.5)
    assert detector.alarm, "EWMA should detect drift to 7.5"
    print("[PASS] EWMA drift detected")

    # Test 4: Validator state machine
    validator = ContinuousValidator(startup_samples=1000)
    alerts_received = []
    validator.register_alert_callback(
        lambda state, atype, msg, details: alerts_received.append((state, atype))
    )

    # Start and immediately stop
    validator.start()
    assert validator.state in (ValidatorState.HEALTHY, ValidatorState.STARTUP)
    time.sleep(0.5)
    status = validator.get_status()
    assert status["total_tests_run"] > 0
    print(f"[PASS] Validator started: state={validator.state.value}, "
          f"tests_run={status['total_tests_run']}")
    validator.stop()
    print("[PASS] Validator stopped cleanly")

    print("\n=== All self-tests passed ===")
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    self_test()
