#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 18, Real-Time Clock Module Implementation.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Test Suite for BFT Consensus Algorithm
=======================================

Comprehensive tests covering edge cases in the Byzantine Fault Tolerant
consensus algorithm used for RTC timestamp selection.

GLI-11 Testing Requirements: Section 5.4.4 requires that time synchronization
systems be tested for accuracy, fault tolerance, and tamper resistance. This
test suite validates all three dimensions.

Usage:
    python3 consensus-test.py
    python3 -m pytest consensus-test.py -v
"""

import random
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone

# Add parent directory to path for imports
sys.path.insert(0, ".")
from bft_consensus import (  # ty:ignore[unresolved-import]
    BFTConsensus,
    ByzantineEvidence,
    ConsensusResult,
    ConsensusStatus,
    RTCReading,
)


class TestBFTConsensusBasic(unittest.TestCase):
    """Test basic consensus operations."""

    def setUp(self):
        self.consensus = BFTConsensus(
            quorum=3,
            drift_threshold_ms=50.0,
            signing_key=b"test-key-do-not-use-in-production",
        )
        self.now = datetime.now(timezone.utc)

    def test_four_honest_nodes(self):
        """4 honest nodes should reach consensus with high confidence."""
        readings = [
            RTCReading(
                module_id=f"rtc-{i}",
                timestamp=self.now + timedelta(microseconds=i * 10),
            )
            for i in range(4)
        ]
        result = self.consensus.run(readings)

        self.assertEqual(result.status, ConsensusStatus.SUCCESS)
        self.assertGreater(result.confidence, 0.8)
        self.assertEqual(result.valid_readings, 4)
        self.assertEqual(len(result.excluded_modules), 0)

    def test_minimum_quorum(self):
        """Exactly 3 readings should still achieve consensus."""
        readings = [
            RTCReading(
                module_id=f"rtc-{i}",
                timestamp=self.now + timedelta(microseconds=i * 5),
            )
            for i in range(3)
        ]
        result = self.consensus.run(readings)

        self.assertEqual(result.status, ConsensusStatus.SUCCESS)
        self.assertEqual(result.valid_readings, 3)

    def test_below_quorum(self):
        """Fewer than quorum should fail."""
        readings = [
            RTCReading(module_id="rtc-1", timestamp=self.now),
            RTCReading(module_id="rtc-2", timestamp=self.now),
        ]
        result = self.consensus.run(readings)

        self.assertEqual(result.status, ConsensusStatus.QUORUM_FAILURE)
        self.assertEqual(result.confidence, 0.0)

    def test_empty_readings(self):
        """No readings should fail gracefully."""
        result = self.consensus.run([])
        self.assertEqual(result.status, ConsensusStatus.QUORUM_FAILURE)
        self.assertEqual(result.confidence, 0.0)

    def test_single_reading(self):
        """Single reading below quorum."""
        readings = [RTCReading(module_id="rtc-1", timestamp=self.now)]
        result = self.consensus.run(readings)
        self.assertEqual(result.status, ConsensusStatus.QUORUM_FAILURE)

    def test_identical_timestamps(self):
        """All modules returning the exact same time."""
        readings = [
            RTCReading(module_id=f"rtc-{i}", timestamp=self.now)
            for i in range(5)
        ]
        result = self.consensus.run(readings)

        self.assertEqual(result.status, ConsensusStatus.SUCCESS)
        self.assertAlmostEqual(result.spread_ms, 0.0, places=1)
        self.assertGreater(result.confidence, 0.9)


class TestByzantineFaultDetection(unittest.TestCase):
    """Test Byzantine node detection and exclusion."""

    def setUp(self):
        self.consensus = BFTConsensus(
            quorum=3,
            drift_threshold_ms=50.0,
            signing_key=b"test-key",
        )
        self.now = datetime.now(timezone.utc)

    def test_one_byzantine_of_four(self):
        """One Byzantine node out of 4 should be detected and excluded."""
        readings = [
            RTCReading(module_id="rtc-1", timestamp=self.now),
            RTCReading(module_id="rtc-2", timestamp=self.now + timedelta(microseconds=100)),
            RTCReading(module_id="rtc-3", timestamp=self.now + timedelta(microseconds=200)),
            # Byzantine: 5 seconds off
            RTCReading(module_id="rtc-byzantine", timestamp=self.now + timedelta(seconds=5)),
        ]
        result = self.consensus.run(readings)

        self.assertEqual(result.status, ConsensusStatus.BYZANTINE_DETECTED)
        self.assertIn("rtc-byzantine", result.excluded_modules)
        self.assertEqual(result.valid_readings, 3)
        self.assertGreater(result.confidence, 0.5)

    def test_one_byzantine_of_seven(self):
        """One Byzantine node out of 7 (well above threshold)."""
        readings = [
            RTCReading(
                module_id=f"rtc-{i}",
                timestamp=self.now + timedelta(microseconds=i * 20),
            )
            for i in range(6)
        ]
        # Add Byzantine node
        readings.append(
            RTCReading(
                module_id="rtc-byzantine",
                timestamp=self.now + timedelta(seconds=10),
            )
        )
        result = self.consensus.run(readings)

        self.assertEqual(result.status, ConsensusStatus.BYZANTINE_DETECTED)
        self.assertEqual(result.valid_readings, 6)

    def test_two_byzantine_of_seven(self):
        """Two Byzantine nodes out of 7 (n >= 3f+1 for f=2)."""
        readings = [
            RTCReading(
                module_id=f"rtc-{i}",
                timestamp=self.now + timedelta(microseconds=i * 10),
            )
            for i in range(5)
        ]
        readings.extend([
            RTCReading(
                module_id="rtc-byz-1",
                timestamp=self.now + timedelta(seconds=3),
            ),
            RTCReading(
                module_id="rtc-byz-2",
                timestamp=self.now - timedelta(seconds=3),
            ),
        ])
        result = self.consensus.run(readings)

        self.assertEqual(result.status, ConsensusStatus.BYZANTINE_DETECTED)
        self.assertIn("rtc-byz-1", result.excluded_modules)
        self.assertIn("rtc-byz-2", result.excluded_modules)
        self.assertEqual(result.valid_readings, 5)

    def test_all_byzantine_different_directions(self):
        """Byzantine nodes deviating in both directions should both be caught."""
        readings = [
            RTCReading(module_id="rtc-1", timestamp=self.now),
            RTCReading(module_id="rtc-2", timestamp=self.now + timedelta(microseconds=50)),
            RTCReading(module_id="rtc-3", timestamp=self.now + timedelta(microseconds=100)),
            RTCReading(module_id="rtc-4", timestamp=self.now + timedelta(microseconds=75)),
            # Positive offset Byzantine
            RTCReading(module_id="rtc-byz-pos", timestamp=self.now + timedelta(seconds=1)),
            # Negative offset Byzantine
            RTCReading(module_id="rtc-byz-neg", timestamp=self.now - timedelta(seconds=1)),
        ]
        result = self.consensus.run(readings)

        self.assertIn("rtc-byz-pos", result.excluded_modules)
        self.assertIn("rtc-byz-neg", result.excluded_modules)

    def test_byzantine_causes_quorum_failure(self):
        """When too many nodes are Byzantine, quorum should fail."""
        # 4 modules, 2 Byzantine = only 2 honest < quorum of 3
        readings = [
            RTCReading(module_id="rtc-1", timestamp=self.now),
            RTCReading(module_id="rtc-2", timestamp=self.now + timedelta(microseconds=20)),
            RTCReading(module_id="rtc-byz-1", timestamp=self.now + timedelta(seconds=10)),
            RTCReading(module_id="rtc-byz-2", timestamp=self.now - timedelta(seconds=10)),
        ]
        result = self.consensus.run(readings)

        self.assertEqual(result.status, ConsensusStatus.QUORUM_FAILURE)

    def test_byzantine_history_tracking(self):
        """Byzantine events should be tracked across rounds."""
        consensus = BFTConsensus(quorum=3, drift_threshold_ms=50.0, signing_key=b"key")

        for round_num in range(3):
            readings = [
                RTCReading(module_id=f"rtc-{i}", timestamp=self.now)
                for i in range(3)
            ]
            readings.append(
                RTCReading(
                    module_id=f"rtc-byz-{round_num}",
                    timestamp=self.now + timedelta(seconds=5),
                )
            )
            consensus.run(readings)

        history = consensus.get_byzantine_history()
        self.assertEqual(len(history), 3)


class TestErrorHandling(unittest.TestCase):
    """Test error conditions and edge cases."""

    def setUp(self):
        self.consensus = BFTConsensus(quorum=3, drift_threshold_ms=50.0, signing_key=b"key")
        self.now = datetime.now(timezone.utc)

    def test_readings_with_errors(self):
        """Readings with errors should be excluded."""
        readings = [
            RTCReading(module_id="rtc-1", timestamp=self.now),
            RTCReading(module_id="rtc-2", timestamp=self.now),
            RTCReading(module_id="rtc-3", timestamp=self.now),
            RTCReading(
                module_id="rtc-err",
                timestamp=self.now,
                is_valid=False,
                error="I2C bus error",
            ),
        ]
        result = self.consensus.run(readings)

        self.assertEqual(result.status, ConsensusStatus.DEGRADED)
        self.assertIn("rtc-err", result.excluded_modules)
        self.assertEqual(result.valid_readings, 3)

    def test_all_errors(self):
        """All error readings should fail with quorum failure."""
        readings = [
            RTCReading(
                module_id=f"rtc-{i}",
                timestamp=self.now,
                is_valid=False,
                error="Hardware fault",
            )
            for i in range(4)
        ]
        result = self.consensus.run(readings)
        self.assertEqual(result.status, ConsensusStatus.QUORUM_FAILURE)

    def test_mixed_errors_and_byzantine(self):
        """Mix of error readings and Byzantine should handle correctly."""
        readings = [
            RTCReading(module_id="rtc-1", timestamp=self.now),
            RTCReading(module_id="rtc-2", timestamp=self.now + timedelta(microseconds=10)),
            RTCReading(module_id="rtc-3", timestamp=self.now + timedelta(microseconds=20)),
            RTCReading(
                module_id="rtc-err",
                timestamp=self.now,
                is_valid=False,
                error="Timeout",
            ),
            RTCReading(
                module_id="rtc-byz",
                timestamp=self.now + timedelta(seconds=99),
            ),
        ]
        result = self.consensus.run(readings)

        self.assertIn("rtc-err", result.excluded_modules)
        self.assertIn("rtc-byz", result.excluded_modules)
        self.assertEqual(result.valid_readings, 3)


class TestMedianComputation(unittest.TestCase):
    """Test median computation accuracy."""

    def test_odd_number_of_values(self):
        """Median of odd count should be the middle value."""
        values = [100, 200, 300, 400, 500]
        median = BFTConsensus._compute_median(values)
        self.assertEqual(median, 300)

    def test_even_number_of_values(self):
        """Median of even count should be the average of two middle values."""
        values = [100, 200, 300, 400]
        median = BFTConsensus._compute_median(values)
        self.assertEqual(median, 250)

    def test_single_value(self):
        """Single value median."""
        median = BFTConsensus._compute_median([42])
        self.assertEqual(median, 42)

    def test_unsorted_input(self):
        """Median should work regardless of input order."""
        values = [500, 100, 300, 200, 400]
        median = BFTConsensus._compute_median(values)
        self.assertEqual(median, 300)

    def test_duplicate_values(self):
        """Median with duplicates."""
        values = [100, 100, 100, 200, 200]
        median = BFTConsensus._compute_median(values)
        self.assertEqual(median, 100)


class TestSignatureVerification(unittest.TestCase):
    """Test HMAC-SHA256 signature generation and verification."""

    def setUp(self):
        self.consensus = BFTConsensus(
            quorum=3,
            drift_threshold_ms=50.0,
            signing_key=b"test-signing-key-32-bytes-long!!",
        )
        self.now = datetime.now(timezone.utc)

    def test_signature_verification(self):
        """Valid consensus result should verify correctly."""
        readings = [
            RTCReading(module_id=f"rtc-{i}", timestamp=self.now)
            for i in range(4)
        ]
        result = self.consensus.run(readings)
        self.assertTrue(self.consensus.verify_signature(result))

    def test_tampered_result_fails_verification(self):
        """Tampered result should fail signature verification."""
        readings = [
            RTCReading(module_id=f"rtc-{i}", timestamp=self.now)
            for i in range(4)
        ]
        result = self.consensus.run(readings)

        # Tamper with the timestamp
        result.timestamp = self.now + timedelta(hours=1)
        self.assertFalse(self.consensus.verify_signature(result))

    def test_different_keys_fail_verification(self):
        """Result signed with different key should fail."""
        consensus2 = BFTConsensus(
            quorum=3,
            drift_threshold_ms=50.0,
            signing_key=b"different-key-for-verification!!!",
        )
        readings = [
            RTCReading(module_id=f"rtc-{i}", timestamp=self.now)
            for i in range(4)
        ]
        result = self.consensus.run(readings)

        # Verify with different key should fail
        self.assertFalse(consensus2.verify_signature(result))


class TestConfidenceScoring(unittest.TestCase):
    """Test confidence score calculation."""

    def setUp(self):
        self.consensus = BFTConsensus(quorum=3, drift_threshold_ms=50.0, signing_key=b"key")
        self.now = datetime.now(timezone.utc)

    def test_perfect_agreement(self):
        """All modules reporting the same time should yield high confidence."""
        readings = [
            RTCReading(module_id=f"rtc-{i}", timestamp=self.now)
            for i in range(6)
        ]
        result = self.consensus.run(readings)
        self.assertGreater(result.confidence, 0.9)

    def test_low_participation(self):
        """Low participation should reduce confidence."""
        # 3 valid out of 6 (3 with errors)
        readings = [
            RTCReading(module_id=f"rtc-{i}", timestamp=self.now)
            for i in range(3)
        ]
        readings.extend([
            RTCReading(
                module_id=f"rtc-err-{i}",
                timestamp=self.now,
                is_valid=False,
                error="Offline",
            )
            for i in range(3)
        ])
        result = self.consensus.run(readings)

        # Should be lower than 100% participation
        all_valid = [
            RTCReading(module_id=f"rtc-{i}", timestamp=self.now)
            for i in range(6)
        ]
        result_all = self.consensus.run(all_valid)
        self.assertLess(result.confidence, result_all.confidence)

    def test_high_spread_reduces_confidence(self):
        """Wide spread among readings should reduce confidence."""
        # Tight spread
        tight = [
            RTCReading(
                module_id=f"rtc-{i}",
                timestamp=self.now + timedelta(microseconds=i),
            )
            for i in range(4)
        ]
        result_tight = self.consensus.run(tight)

        # Wide spread (but still within threshold)
        wide = [
            RTCReading(
                module_id=f"rtc-{i}",
                timestamp=self.now + timedelta(milliseconds=i * 10),
            )
            for i in range(4)
        ]
        result_wide = self.consensus.run(wide)

        self.assertGreater(result_tight.confidence, result_wide.confidence)


class TestPerformance(unittest.TestCase):
    """Test performance characteristics."""

    def test_consensus_latency(self):
        """Consensus should complete within 1ms for typical load."""
        consensus = BFTConsensus(quorum=3, drift_threshold_ms=50.0, signing_key=b"key")
        now = datetime.now(timezone.utc)

        readings = [
            RTCReading(module_id=f"rtc-{i}", timestamp=now)
            for i in range(7)
        ]

        # Run 100 rounds and measure
        durations = []
        for _ in range(100):
            start = time.monotonic_ns()
            consensus.run(readings)
            elapsed_us = (time.monotonic_ns() - start) / 1000
            durations.append(elapsed_us)

        avg_us = sum(durations) / len(durations)
        p99_us = sorted(durations)[98]

        # Average should be well under 1ms
        self.assertLess(avg_us, 1000, f"Average consensus latency {avg_us:.0f}us exceeds 1ms")
        # P99 under 5ms
        self.assertLess(p99_us, 5000, f"P99 consensus latency {p99_us:.0f}us exceeds 5ms")

    def test_large_cluster(self):
        """Consensus should work with a large number of modules."""
        consensus = BFTConsensus(quorum=7, drift_threshold_ms=50.0, signing_key=b"key")
        now = datetime.now(timezone.utc)

        readings = [
            RTCReading(
                module_id=f"rtc-{i}",
                timestamp=now + timedelta(microseconds=random.randint(-100, 100)),
            )
            for i in range(20)
        ]

        result = consensus.run(readings)
        self.assertEqual(result.status, ConsensusStatus.SUCCESS)
        self.assertEqual(result.valid_readings, 20)


class TestGLI11Compliance(unittest.TestCase):
    """
    Tests specifically targeting GLI-11 compliance requirements.

    GLI-11 Section 5.4 specifies:
    - Time accuracy within 100ms of reference source
    - Resilience to single points of failure
    - Tamper detection and alerting
    - Audit trail for all time source changes
    """

    def setUp(self):
        # GLI-11 requires max 100ms drift, we use 50ms for safety margin
        self.consensus = BFTConsensus(
            quorum=3,
            drift_threshold_ms=50.0,
            signing_key=b"gli-11-compliance-test-key-32b!!",
        )
        self.now = datetime.now(timezone.utc)

    def test_drift_within_gli11_threshold(self):
        """Consensus drift must be within GLI-11 100ms threshold."""
        readings = [
            RTCReading(
                module_id=f"rtc-{i}",
                timestamp=self.now + timedelta(microseconds=random.randint(-100, 100)),
            )
            for i in range(5)
        ]
        result = self.consensus.run(readings)

        # GLI-11 requires < 100ms; our threshold is 50ms
        self.assertLess(result.spread_ms, 100.0,
                        "Consensus spread exceeds GLI-11 100ms threshold")

    def test_single_node_failure_resilience(self):
        """System must continue operating with one node failure."""
        readings = [
            RTCReading(module_id=f"rtc-{i}", timestamp=self.now)
            for i in range(4)
        ]
        # Simulate one node failure
        readings[3] = RTCReading(
            module_id="rtc-3",
            timestamp=self.now,
            is_valid=False,
            error="Hardware failure",
        )

        result = self.consensus.run(readings)
        self.assertNotEqual(result.status, ConsensusStatus.QUORUM_FAILURE,
                            "System failed with single node failure - GLI-11 violation")

    def test_tamper_detection(self):
        """Tampered node must be detected and excluded."""
        readings = [
            RTCReading(module_id=f"rtc-{i}", timestamp=self.now)
            for i in range(4)
        ]
        # Tamper with one node
        readings[3] = RTCReading(
            module_id="rtc-tampered",
            timestamp=self.now + timedelta(minutes=5),
        )

        result = self.consensus.run(readings)
        self.assertIn("rtc-tampered", result.excluded_modules,
                       "Tampered node not detected - GLI-11 violation")

    def test_audit_trail(self):
        """Every consensus round must produce a signed, auditable result."""
        readings = [
            RTCReading(module_id=f"rtc-{i}", timestamp=self.now)
            for i in range(4)
        ]
        result = self.consensus.run(readings)

        # Must have a signature
        self.assertTrue(result.signature, "Missing signature - audit trail incomplete")
        # Must have round ID
        self.assertTrue(result.round_id, "Missing round ID - audit trail incomplete")
        # Must list participants
        self.assertTrue(result.participating_modules, "Missing participants - audit trail incomplete")
        # Signature must verify
        self.assertTrue(
            self.consensus.verify_signature(result),
            "Signature verification failed - audit integrity compromised"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
