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
Entropy Pool Management with Continuous Health Monitoring
=========================================================

GLI-11 Section 4.3 Compliance: Entropy Source Requirements
- Must collect entropy from multiple independent sources
- Must validate entropy quality before use
- Must detect entropy source failure and trigger alerts
- Must maintain minimum entropy level at all times
- Must log entropy collection events for audit

NIST SP 800-90B: Entropy source health tests
- Repetition Count Test: Detects stuck sources
- Adaptive Proportion Test: Detects bias
- Startup health test: 1024 samples before first use

Usage:
    pool = EntropyPoolManager()
    pool.register_source("os_urandom", OsUrandomSource())
    pool.register_source("rdrand", RdrandSource())
    pool.start()
    entropy = pool.get_entropy(32)
"""

import hashlib
import math
import os
import struct
import threading
import time
import logging
import json
import statistics
from abc import ABC, abstractmethod
from collections import deque
from datetime import datetime, timezone
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("rng.entropy_pool")


class SourceStatus(Enum):
    STARTUP = "startup"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILED = "failed"


@dataclass
class EntropySourceMetrics:
    """Per-source health metrics."""
    name: str
    status: SourceStatus = SourceStatus.STARTUP
    samples_collected: int = 0
    bytes_collected: int = 0
    last_collection_time: float = 0.0
    min_entropy_estimate: float = 0.0
    repetition_failures: int = 0
    proportion_failures: int = 0
    consecutive_failures: int = 0
    startup_samples_remaining: int = 1024  # NIST SP 800-90B startup test


class EntropySource(ABC):
    """
    Abstract base class for entropy sources.

    Each source must implement collect() to return raw entropy bytes.
    GLI-11 4.3.1: Each source must be independently identifiable.
    """

    @abstractmethod
    def collect(self, num_bytes: int) -> bytes:
        """Collect raw entropy from this source."""
        ...

    @abstractmethod
    def get_source_id(self) -> int:
        """Return unique source identifier (0-255)."""
        ...

    @abstractmethod
    def estimated_entropy_per_byte(self) -> float:
        """Return estimated bits of entropy per byte of output."""
        ...


class OsUrandomSource(EntropySource):
    """OS kernel entropy source (/dev/urandom or CryptGenRandom)."""

    def collect(self, num_bytes: int) -> bytes:
        return os.urandom(num_bytes)

    def get_source_id(self) -> int:
        return 0

    def estimated_entropy_per_byte(self) -> float:
        return 8.0  # Full entropy from kernel CSPRNG


class TimingJitterSource(EntropySource):
    """
    CPU timing jitter entropy source.

    Collects entropy from variations in CPU instruction timing.
    Lower entropy density than hardware sources but always available.
    """

    def collect(self, num_bytes: int) -> bytes:
        samples = []
        for _ in range(num_bytes * 8):  # Oversample for concentration
            t1 = time.perf_counter_ns()
            # Perform variable-time operations
            _ = hashlib.sha256(struct.pack(">Q", t1)).digest()
            t2 = time.perf_counter_ns()
            samples.append((t2 - t1) & 0xFF)

        # Compress timing samples into output bytes
        result = bytearray(num_bytes)
        for i in range(num_bytes):
            h = hashlib.sha256(bytes(samples[i * 8 : (i + 1) * 8])).digest()
            result[i] = h[0]
        return bytes(result)

    def get_source_id(self) -> int:
        return 4

    def estimated_entropy_per_byte(self) -> float:
        return 1.0  # Conservative estimate


class ProcessMetricsSource(EntropySource):
    """
    System process metrics entropy source.

    Collects entropy from process scheduling, memory allocation,
    and other non-deterministic system state.
    """

    def collect(self, num_bytes: int) -> bytes:
        data = bytearray()
        for _ in range(num_bytes):
            metrics = struct.pack(
                ">QQdQ",
                time.perf_counter_ns(),
                time.process_time_ns(),
                time.monotonic(),
                id(object()),  # Memory allocation address
            )
            h = hashlib.sha256(metrics).digest()
            data.append(h[0])
        return bytes(data)

    def get_source_id(self) -> int:
        return 5

    def estimated_entropy_per_byte(self) -> float:
        return 0.5  # Very conservative


# ---------------------------------------------------------------------------
# NIST SP 800-90B Health Tests
# ---------------------------------------------------------------------------

class RepetitionCountTest:
    """
    NIST SP 800-90B Section 4.4.1: Repetition Count Test.

    Detects a source that becomes stuck producing the same value.
    Cutoff C = 1 + ceil(-log2(alpha) / H), where:
    - alpha = 2^-20 (false positive probability)
    - H = estimated min-entropy per sample
    """

    def __init__(self, min_entropy_per_sample: float = 1.0):
        self._cutoff = 1 + math.ceil(20.0 / max(min_entropy_per_sample, 0.1))
        self._last_value: Optional[int] = None
        self._count = 0

    def add_sample(self, value: int) -> bool:
        """
        Add a sample. Returns True if healthy, False if test fails.
        """
        if value == self._last_value:
            self._count += 1
            if self._count >= self._cutoff:
                return False  # FAIL: too many consecutive identical values
        else:
            self._last_value = value
            self._count = 1
        return True


class AdaptiveProportionTest:
    """
    NIST SP 800-90B Section 4.4.2: Adaptive Proportion Test.

    Detects a source with too much bias toward a single value.
    Window size W = 512 for binary sources, 64 for non-binary.
    """

    def __init__(
        self,
        window_size: int = 512,
        min_entropy_per_sample: float = 1.0,
        alphabet_size: int = 256,
    ):
        self._window_size = window_size
        self._window: deque = deque(maxlen=window_size)
        # Cutoff based on false positive rate 2^-20
        self._cutoff = int(
            window_size * (1.0 / alphabet_size)
            + 5.0 * math.sqrt(window_size * (1.0 / alphabet_size))
        )
        self._cutoff = max(self._cutoff, window_size // 4)

    def add_sample(self, value: int) -> bool:
        """
        Add a sample. Returns True if healthy, False if test fails.
        """
        self._window.append(value)

        if len(self._window) < self._window_size:
            return True  # Not enough samples yet

        # Count most frequent value
        freq: Dict[int, int] = {}
        for v in self._window:
            freq[v] = freq.get(v, 0) + 1

        max_freq = max(freq.values())
        return max_freq < self._cutoff


class MinEntropyEstimator:
    """
    Estimate min-entropy from collected samples.
    Uses the Most Common Value (MCV) estimator from NIST SP 800-90B.
    """

    def __init__(self, window_size: int = 10000):
        self._samples: deque = deque(maxlen=window_size)
        self._window_size = window_size

    def add_sample(self, value: int) -> None:
        self._samples.append(value)

    def estimate(self) -> float:
        """Return estimated min-entropy per sample in bits."""
        if len(self._samples) < 100:
            return 0.0

        freq: Dict[int, int] = {}
        for v in self._samples:
            freq[v] = freq.get(v, 0) + 1

        n = len(self._samples)
        p_max = max(freq.values()) / n

        if p_max <= 0 or p_max >= 1:
            return 0.0

        return -math.log2(p_max)


# ---------------------------------------------------------------------------
# Entropy Pool Manager
# ---------------------------------------------------------------------------

class EntropyPoolManager:
    """
    Central entropy pool with multi-source collection and health monitoring.

    GLI-11 Compliance Features:
    - Multiple independent entropy sources with round-robin collection
    - NIST SP 800-90B health tests (repetition count, adaptive proportion)
    - Automatic failover when a source degrades or fails
    - Minimum entropy threshold enforcement
    - Continuous min-entropy estimation
    - Full audit logging of collection and health events
    - Startup health test (1024 samples before first use)

    Architecture:
    - Each source runs in its own collection thread
    - Raw entropy is hashed (SHA-256) before pool addition
    - Pool uses conditioned entropy (post-health-test)
    - Output is further conditioned via SHA-512 extraction
    """

    def __init__(
        self,
        min_sources: int = 2,
        min_entropy_bytes: int = 256,
        collection_interval: float = 0.1,
        audit_log_path: Optional[str] = None,
    ):
        """
        Args:
            min_sources: Minimum healthy sources required (GLI-11 4.3.2)
            min_entropy_bytes: Minimum pool level before output allowed
            collection_interval: Seconds between collection cycles per source
            audit_log_path: Path for audit log (JSONL)
        """
        self._sources: Dict[str, EntropySource] = {}
        self._metrics: Dict[str, EntropySourceMetrics] = {}
        self._health_tests: Dict[str, dict] = {}
        self._entropy_estimators: Dict[str, MinEntropyEstimator] = {}

        self._pool = bytearray()
        self._pool_lock = threading.Lock()
        self._pool_entropy_bits = 0.0
        self._conditioned_pool = bytearray()

        self._min_sources = min_sources
        self._min_entropy_bytes = min_entropy_bytes
        self._collection_interval = collection_interval
        self._audit_log_path = audit_log_path
        self._audit_sequence = 0

        self._running = False
        self._threads: List[threading.Thread] = []
        self._callbacks: List[Callable] = []

        self._total_collected = 0
        self._total_extracted = 0

    def register_source(self, name: str, source: EntropySource) -> None:
        """
        Register an entropy source.

        GLI-11 4.3.1: Each source must be independently identified
        and monitored.
        """
        self._sources[name] = source
        self._metrics[name] = EntropySourceMetrics(name=name)
        self._health_tests[name] = {
            "repetition": RepetitionCountTest(
                source.estimated_entropy_per_byte()
            ),
            "proportion": AdaptiveProportionTest(
                min_entropy_per_sample=source.estimated_entropy_per_byte()
            ),
        }
        self._entropy_estimators[name] = MinEntropyEstimator()

        self._audit("SOURCE_REGISTERED", {
            "name": name,
            "source_id": source.get_source_id(),
            "estimated_entropy_per_byte": source.estimated_entropy_per_byte(),
        })

        logger.info(
            "Registered entropy source: %s (id=%d, est=%.1f bits/byte)",
            name,
            source.get_source_id(),
            source.estimated_entropy_per_byte(),
        )

    def register_alert_callback(self, callback: Callable) -> None:
        """Register callback for health alerts: callback(source_name, status, details)."""
        self._callbacks.append(callback)

    def start(self) -> None:
        """Start entropy collection threads for all registered sources."""
        if self._running:
            return

        if len(self._sources) < self._min_sources:
            raise RuntimeError(
                f"GLI-11 VIOLATION: Need at least {self._min_sources} "
                f"entropy sources, only {len(self._sources)} registered."
            )

        self._running = True

        for name, source in self._sources.items():
            thread = threading.Thread(
                target=self._collection_loop,
                args=(name, source),
                daemon=True,
                name=f"entropy-{name}",
            )
            thread.start()
            self._threads.append(thread)

        self._audit("POOL_STARTED", {
            "sources": list(self._sources.keys()),
            "min_sources": self._min_sources,
        })

        logger.info(
            "Entropy pool started with %d sources", len(self._sources)
        )

    def stop(self) -> None:
        """Stop entropy collection."""
        self._running = False
        for t in self._threads:
            t.join(timeout=5.0)
        self._threads.clear()
        self._audit("POOL_STOPPED", {})

    def _collection_loop(self, name: str, source: EntropySource) -> None:
        """Background collection loop for a single source."""
        metrics = self._metrics[name]
        tests = self._health_tests[name]
        estimator = self._entropy_estimators[name]

        while self._running:
            try:
                # Collect raw entropy
                raw = source.collect(32)

                # Run health tests on each byte
                healthy = True
                for byte_val in raw:
                    if not tests["repetition"].add_sample(byte_val):
                        metrics.repetition_failures += 1
                        healthy = False
                        break
                    if not tests["proportion"].add_sample(byte_val):
                        metrics.proportion_failures += 1
                        healthy = False
                        break
                    estimator.add_sample(byte_val)

                # Update startup counter
                if metrics.startup_samples_remaining > 0:
                    metrics.startup_samples_remaining -= len(raw)
                    if metrics.startup_samples_remaining <= 0:
                        metrics.status = SourceStatus.HEALTHY
                        self._audit("SOURCE_STARTUP_COMPLETE", {
                            "name": name,
                        })

                if healthy:
                    metrics.consecutive_failures = 0

                    if metrics.status == SourceStatus.FAILED:
                        metrics.status = SourceStatus.HEALTHY
                        self._alert(name, SourceStatus.HEALTHY, "recovered")

                    # Condition entropy via SHA-256
                    conditioned = hashlib.sha256(raw).digest()

                    # Add to pool
                    with self._pool_lock:
                        self._pool.extend(conditioned)
                        entropy_bits = (
                            source.estimated_entropy_per_byte() * len(raw)
                        )
                        self._pool_entropy_bits += entropy_bits

                    metrics.samples_collected += 1
                    metrics.bytes_collected += len(raw)
                    metrics.last_collection_time = time.monotonic()
                    metrics.min_entropy_estimate = estimator.estimate()
                    self._total_collected += len(raw)

                else:
                    metrics.consecutive_failures += 1
                    if metrics.consecutive_failures >= 10:
                        metrics.status = SourceStatus.FAILED
                        self._alert(
                            name,
                            SourceStatus.FAILED,
                            f"consecutive_failures={metrics.consecutive_failures}",
                        )
                    elif metrics.consecutive_failures >= 3:
                        metrics.status = SourceStatus.DEGRADED
                        self._alert(
                            name,
                            SourceStatus.DEGRADED,
                            f"consecutive_failures={metrics.consecutive_failures}",
                        )

            except Exception as exc:
                metrics.consecutive_failures += 1
                metrics.status = SourceStatus.FAILED
                self._alert(name, SourceStatus.FAILED, str(exc))
                logger.error("Entropy source %s error: %s", name, exc)

            time.sleep(self._collection_interval)

    def _alert(self, source: str, status: SourceStatus, detail: str) -> None:
        """Trigger health alert."""
        self._audit("HEALTH_ALERT", {
            "source": source,
            "status": status.value,
            "detail": detail,
        })
        for cb in self._callbacks:
            try:
                cb(source, status, detail)
            except Exception:
                pass

    # ----- Entropy Extraction -----

    def get_entropy(self, num_bytes: int) -> bytes:
        """
        Extract conditioned entropy from the pool.

        GLI-11 4.3.3: Output must meet minimum entropy requirements.

        Uses SHA-512 as a conditioning function to produce full-entropy
        output from the accumulated pool.

        Args:
            num_bytes: Number of entropy bytes to extract

        Returns:
            Conditioned entropy bytes

        Raises:
            RuntimeError: If pool has insufficient entropy or
                          not enough healthy sources
        """
        # Check minimum healthy sources
        healthy_count = sum(
            1
            for m in self._metrics.values()
            if m.status in (SourceStatus.HEALTHY, SourceStatus.DEGRADED)
        )
        if healthy_count < self._min_sources:
            raise RuntimeError(
                f"GLI-11 VIOLATION: Only {healthy_count} healthy sources, "
                f"minimum {self._min_sources} required."
            )

        with self._pool_lock:
            # Check pool has sufficient data
            if len(self._pool) < self._min_entropy_bytes:
                raise RuntimeError(
                    f"Insufficient entropy: {len(self._pool)} bytes "
                    f"in pool, need {self._min_entropy_bytes}."
                )

            # Extract pool contents
            pool_data = bytes(self._pool)
            self._pool.clear()
            self._pool_entropy_bits = 0.0

        # Condition via SHA-512 to produce output
        result = bytearray()
        counter = 0
        while len(result) < num_bytes:
            h = hashlib.sha512(
                struct.pack(">Q", counter) + pool_data
            ).digest()
            result.extend(h)
            counter += 1

        output = bytes(result[:num_bytes])
        self._total_extracted += num_bytes

        self._audit("ENTROPY_EXTRACTED", {
            "bytes": num_bytes,
            "pool_bytes_consumed": len(pool_data),
            "healthy_sources": healthy_count,
        })

        return output

    # ----- Health Status -----

    def get_health_status(self) -> dict:
        """
        Return comprehensive health status.
        GLI-11 4.5.1: Continuous health monitoring required.
        """
        sources = {}
        for name, metrics in self._metrics.items():
            sources[name] = {
                "status": metrics.status.value,
                "samples_collected": metrics.samples_collected,
                "bytes_collected": metrics.bytes_collected,
                "min_entropy_estimate": round(
                    metrics.min_entropy_estimate, 4
                ),
                "repetition_failures": metrics.repetition_failures,
                "proportion_failures": metrics.proportion_failures,
                "consecutive_failures": metrics.consecutive_failures,
                "startup_remaining": max(
                    0, metrics.startup_samples_remaining
                ),
                "last_collection_age_s": round(
                    time.monotonic() - metrics.last_collection_time, 3
                )
                if metrics.last_collection_time > 0
                else None,
            }

        healthy_count = sum(
            1
            for m in self._metrics.values()
            if m.status == SourceStatus.HEALTHY
        )

        with self._pool_lock:
            pool_size = len(self._pool)
            pool_entropy = self._pool_entropy_bits

        return {
            "overall_healthy": healthy_count >= self._min_sources,
            "healthy_sources": healthy_count,
            "total_sources": len(self._sources),
            "min_sources_required": self._min_sources,
            "pool_size_bytes": pool_size,
            "pool_entropy_bits": round(pool_entropy, 1),
            "total_collected_bytes": self._total_collected,
            "total_extracted_bytes": self._total_extracted,
            "sources": sources,
        }

    def _audit(self, event_type: str, details: dict) -> None:
        """Record audit event."""
        self._audit_sequence += 1
        entry = {
            "seq": self._audit_sequence,
            "ts": datetime.now(timezone.utc).isoformat(),
            "component": "EntropyPool",
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
# Self-Test
# ---------------------------------------------------------------------------

def self_test() -> bool:
    """Entropy pool self-test."""
    print("=== Entropy Pool Manager Self-Test ===\n")

    pool = EntropyPoolManager(
        min_sources=2,
        min_entropy_bytes=32,
        collection_interval=0.01,
    )

    # Register sources
    pool.register_source("os_urandom", OsUrandomSource())
    pool.register_source("timing_jitter", TimingJitterSource())
    pool.register_source("process_metrics", ProcessMetricsSource())
    print("[PASS] Registered 3 entropy sources")

    # Alert tracking
    alerts = []
    pool.register_alert_callback(
        lambda name, status, detail: alerts.append((name, status, detail))
    )

    # Start collection
    pool.start()
    print("[PASS] Pool started")

    # Wait for startup health test
    time.sleep(2.0)

    # Extract entropy
    entropy = pool.get_entropy(64)
    assert len(entropy) == 64, "Wrong entropy length"
    print(f"[PASS] Extracted 64 bytes of entropy")

    # Verify uniqueness
    samples = set()
    for _ in range(100):
        time.sleep(0.02)
        try:
            e = pool.get_entropy(16)
            samples.add(e)
        except RuntimeError:
            pass  # Pool may need refilling

    assert len(samples) > 90, f"Only {len(samples)}/100 unique samples"
    print(f"[PASS] Uniqueness: {len(samples)}/100 unique samples")

    # Check health status
    status = pool.get_health_status()
    assert status["overall_healthy"] is True
    assert status["healthy_sources"] >= 2
    print(f"[PASS] Health: {status['healthy_sources']} healthy sources")
    print(f"  Pool: {status['pool_size_bytes']} bytes, "
          f"{status['pool_entropy_bits']:.0f} bits entropy")

    # Repetition count test validation
    rct = RepetitionCountTest(min_entropy_per_sample=4.0)
    for i in range(100):
        assert rct.add_sample(i) is True
    # Stuck source should fail
    for _ in range(30):
        result = rct.add_sample(42)
    assert result is False, "Repetition test should have failed"
    print("[PASS] Repetition Count Test detects stuck source")

    # Adaptive proportion test validation
    apt = AdaptiveProportionTest(window_size=64)
    for i in range(64):
        apt.add_sample(i % 256)
    assert apt.add_sample(0) is True
    print("[PASS] Adaptive Proportion Test passes on uniform data")

    pool.stop()
    print("[PASS] Pool stopped cleanly")

    print("\n=== All self-tests passed ===")
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    self_test()
