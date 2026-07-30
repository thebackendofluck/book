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
Byzantine Fault Tolerant (BFT) Consensus for RTC Timestamp Selection
=====================================================================

Implements a median-based BFT consensus algorithm for selecting authoritative
timestamps from multiple hardware RTC modules in a regulated gambling platform.

Algorithm Overview:
    1. Query all RTC modules in parallel (with timeout)
    2. Discard readings that exceed the drift threshold (Byzantine detection)
    3. Compute the median of remaining readings (BFT-resistant)
    4. Calculate confidence score based on agreement spread
    5. Sign the consensus result with HMAC-SHA256

GLI-11 Requirement: Section 5.4.1 mandates that time sources used in
electronic gaming systems must be resilient to tampering and single points
of failure. BFT consensus across 4+ modules ensures that up to f faulty
nodes (including malicious ones) are tolerated when n >= 3f+1.

With 4 modules: tolerates 1 Byzantine fault
With 7 modules: tolerates 2 Byzantine faults

Usage:
    from bft_consensus import BFTConsensus, RTCReading

    consensus = BFTConsensus(
        quorum=3,
        drift_threshold_ms=50.0,
        signing_key=b"hmac-secret-key"
    )
    result = consensus.run(readings)
"""

import hashlib
import hmac
import logging
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("bft-consensus")


# ---------------------------------------------------------------------------
# Data Types
# ---------------------------------------------------------------------------
class ConsensusStatus(Enum):
    """Status of a consensus round."""
    SUCCESS = "success"
    DEGRADED = "degraded"          # Achieved quorum but with warnings
    QUORUM_FAILURE = "quorum_failure"  # Not enough valid readings
    TIMEOUT = "timeout"            # Timed out waiting for readings
    BYZANTINE_DETECTED = "byzantine_detected"  # Malicious node detected


@dataclass
class RTCReading:
    """A single reading from an RTC hardware module."""
    module_id: str
    timestamp: datetime
    temperature_celsius: float = 25.0
    battery_percentage: float = 100.0
    read_latency_us: int = 0  # Microseconds to read from hardware
    is_valid: bool = True
    error: Optional[str] = None


@dataclass
class ConsensusResult:
    """Result of a BFT consensus round."""
    timestamp: datetime
    confidence: float                # 0.0 to 1.0
    status: ConsensusStatus
    signature: str                   # HMAC-SHA256 signature
    participating_modules: List[str]
    excluded_modules: List[str]
    total_modules: int
    valid_readings: int
    spread_ms: float                 # Max deviation among valid readings
    drift_from_system_ms: float      # Deviation from system clock
    round_id: str                    # Unique consensus round identifier
    round_duration_us: int           # Time to complete consensus (microseconds)
    details: Dict[str, object] = field(default_factory=dict)


@dataclass
class ByzantineEvidence:
    """Evidence of a Byzantine (faulty/malicious) node."""
    module_id: str
    deviation_ms: float
    expected_range_ms: Tuple[float, float]
    evidence_type: str  # "excessive_drift", "time_reversal", "impossible_value"
    timestamp: str
    details: str


# ---------------------------------------------------------------------------
# BFT Consensus Engine
# ---------------------------------------------------------------------------
class BFTConsensus:
    """
    Byzantine Fault Tolerant consensus for RTC timestamp selection.

    Uses median-based voting to select an authoritative timestamp that
    is resistant to up to f Byzantine (faulty or malicious) nodes,
    provided n >= 3f+1 total nodes are available.

    Args:
        quorum: Minimum number of valid readings required for consensus
        drift_threshold_ms: Maximum allowed drift from median (beyond = Byzantine)
        signing_key: HMAC-SHA256 key for signing consensus results
        query_timeout_ms: Timeout for querying individual RTC modules
        max_spread_ms: Maximum acceptable spread among valid readings
    """

    def __init__(
        self,
        quorum: int = 3,
        drift_threshold_ms: float = 50.0,
        signing_key: bytes = b"default-key-replace-in-production",
        query_timeout_ms: int = 500,
        max_spread_ms: float = 100.0,
    ):
        self.quorum = quorum
        self.drift_threshold_ms = drift_threshold_ms
        self.signing_key = signing_key
        self.query_timeout_ms = query_timeout_ms
        self.max_spread_ms = max_spread_ms

        # Statistics tracking
        self._round_counter = 0
        self._byzantine_events: List[ByzantineEvidence] = []

    def run(self, readings: List[RTCReading]) -> ConsensusResult:
        """
        Execute a single consensus round.

        Steps:
            1. Filter out error readings
            2. Compute initial median
            3. Detect and exclude Byzantine nodes (outliers beyond threshold)
            4. Recompute median from valid readings only
            5. Calculate confidence and spread
            6. Sign the consensus result

        Args:
            readings: List of RTCReading from hardware modules

        Returns:
            ConsensusResult with the authoritative timestamp
        """
        start_us = time.monotonic_ns() // 1000
        self._round_counter += 1
        round_id = f"consensus-{self._round_counter:08d}-{int(time.time())}"

        logger.info(f"Consensus round {round_id}: {len(readings)} readings received")

        # Step 1: Filter error readings
        valid_readings = [r for r in readings if r.is_valid and r.error is None]
        error_readings = [r for r in readings if not r.is_valid or r.error is not None]

        for r in error_readings:
            logger.warning(f"  Excluded {r.module_id}: {r.error}")

        if len(valid_readings) < self.quorum:
            elapsed_us = (time.monotonic_ns() // 1000) - start_us
            return ConsensusResult(
                timestamp=datetime.now(timezone.utc),
                confidence=0.0,
                status=ConsensusStatus.QUORUM_FAILURE,
                signature="",
                participating_modules=[],
                excluded_modules=[r.module_id for r in readings],
                total_modules=len(readings),
                valid_readings=len(valid_readings),
                spread_ms=float("inf"),
                drift_from_system_ms=0.0,
                round_id=round_id,
                round_duration_us=elapsed_us,
                details={"error": f"Only {len(valid_readings)} valid readings, need {self.quorum}"},
            )

        # Step 2: Compute initial median
        timestamps_ns = [
            int(r.timestamp.timestamp() * 1_000_000_000) for r in valid_readings
        ]
        initial_median_ns = self._compute_median(timestamps_ns)

        # Step 3: Detect Byzantine nodes
        participating = []
        excluded = []
        byzantine_evidence = []

        for r, ts_ns in zip(valid_readings, timestamps_ns):
            deviation_ms = abs(ts_ns - initial_median_ns) / 1_000_000
            if deviation_ms > self.drift_threshold_ms:
                excluded.append(r.module_id)
                evidence = ByzantineEvidence(
                    module_id=r.module_id,
                    deviation_ms=deviation_ms,
                    expected_range_ms=(
                        -self.drift_threshold_ms,
                        self.drift_threshold_ms,
                    ),
                    evidence_type="excessive_drift",
                    timestamp=r.timestamp.isoformat(),
                    details=f"Deviation {deviation_ms:.3f}ms exceeds threshold {self.drift_threshold_ms}ms",
                )
                byzantine_evidence.append(evidence)
                self._byzantine_events.append(evidence)
                logger.warning(
                    f"  BYZANTINE: {r.module_id} deviates by {deviation_ms:.3f}ms"
                )
            else:
                participating.append(r)

        # Add error modules to excluded list
        excluded.extend([r.module_id for r in error_readings])

        # Step 4: Verify quorum after exclusion
        if len(participating) < self.quorum:
            elapsed_us = (time.monotonic_ns() // 1000) - start_us
            return ConsensusResult(
                timestamp=datetime.now(timezone.utc),
                confidence=0.0,
                status=ConsensusStatus.QUORUM_FAILURE,
                signature="",
                participating_modules=[r.module_id for r in participating],
                excluded_modules=excluded,
                total_modules=len(readings),
                valid_readings=len(participating),
                spread_ms=float("inf"),
                drift_from_system_ms=0.0,
                round_id=round_id,
                round_duration_us=elapsed_us,
                details={
                    "error": "Quorum lost after Byzantine exclusion",
                    "byzantine_evidence": [
                        {
                            "module_id": e.module_id,
                            "deviation_ms": e.deviation_ms,
                            "type": e.evidence_type,
                        }
                        for e in byzantine_evidence
                    ],
                },
            )

        # Step 5: Recompute median from valid readings only
        valid_ns = [
            int(r.timestamp.timestamp() * 1_000_000_000) for r in participating
        ]
        final_median_ns = self._compute_median(valid_ns)
        consensus_time = datetime.fromtimestamp(
            final_median_ns / 1_000_000_000, tz=timezone.utc
        )

        # Step 6: Calculate confidence and spread
        deviations_ms = [abs(ns - final_median_ns) / 1_000_000 for ns in valid_ns]
        spread_ms = max(deviations_ms) if deviations_ms else 0.0

        confidence = self._calculate_confidence(
            valid_count=len(participating),
            total_count=len(readings),
            spread_ms=spread_ms,
            byzantine_count=len(byzantine_evidence),
        )

        # Drift from system clock
        system_ns = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
        drift_from_system_ms = abs(final_median_ns - system_ns) / 1_000_000

        # Determine status
        status = ConsensusStatus.SUCCESS
        if byzantine_evidence:
            status = ConsensusStatus.BYZANTINE_DETECTED
        elif len(participating) < len(readings):
            status = ConsensusStatus.DEGRADED

        # Step 7: Sign result
        signature = self._sign_consensus(consensus_time, round_id, participating)

        elapsed_us = (time.monotonic_ns() // 1000) - start_us

        result = ConsensusResult(
            timestamp=consensus_time,
            confidence=confidence,
            status=status,
            signature=signature,
            participating_modules=[r.module_id for r in participating],
            excluded_modules=excluded,
            total_modules=len(readings),
            valid_readings=len(participating),
            spread_ms=spread_ms,
            drift_from_system_ms=drift_from_system_ms,
            round_id=round_id,
            round_duration_us=elapsed_us,
            details={
                "median_method": "standard_median" if len(valid_ns) % 2 == 1 else "averaged_median",
                "byzantine_count": len(byzantine_evidence),
                "max_tolerable_faults": (len(readings) - 1) // 3,
            },
        )

        logger.info(
            f"  Consensus: {consensus_time.isoformat()} "
            f"confidence={confidence:.4f} spread={spread_ms:.3f}ms "
            f"participants={len(participating)}/{len(readings)} "
            f"duration={elapsed_us}us"
        )

        return result

    def run_parallel(
        self,
        query_funcs: List[Callable[[], RTCReading]],
        timeout_ms: Optional[int] = None,
    ) -> ConsensusResult:
        """
        Run consensus by querying modules in parallel.

        Queries all RTC modules concurrently and feeds results into
        the consensus algorithm. This is the typical production path.

        Args:
            query_funcs: List of callables that return RTCReading
            timeout_ms: Override default timeout per query

        Returns:
            ConsensusResult
        """
        timeout_s = (timeout_ms or self.query_timeout_ms) / 1000
        readings = []

        with ThreadPoolExecutor(max_workers=len(query_funcs)) as executor:
            futures = {
                executor.submit(fn): i for i, fn in enumerate(query_funcs)
            }
            for future in as_completed(futures, timeout=timeout_s * 2):
                idx = futures[future]
                try:
                    reading = future.result(timeout=timeout_s)
                    readings.append(reading)
                except TimeoutError:
                    readings.append(RTCReading(
                        module_id=f"module-{idx}",
                        timestamp=datetime.now(timezone.utc),
                        is_valid=False,
                        error="Query timeout",
                    ))
                except Exception as e:
                    readings.append(RTCReading(
                        module_id=f"module-{idx}",
                        timestamp=datetime.now(timezone.utc),
                        is_valid=False,
                        error=str(e),
                    ))

        return self.run(readings)

    # -----------------------------------------------------------------------
    # Internal Methods
    # -----------------------------------------------------------------------
    @staticmethod
    def _compute_median(values: List[int]) -> int:
        """
        Compute median of nanosecond timestamps.

        The median is the optimal choice for BFT consensus because:
        - It is not affected by up to n/2 - 1 outliers
        - It minimizes the maximum deviation from any honest node
        - It is computationally efficient (O(n log n))
        """
        sorted_vals = sorted(values)
        n = len(sorted_vals)
        mid = n // 2
        if n % 2 == 0:
            return (sorted_vals[mid - 1] + sorted_vals[mid]) // 2
        return sorted_vals[mid]

    def _calculate_confidence(
        self,
        valid_count: int,
        total_count: int,
        spread_ms: float,
        byzantine_count: int,
    ) -> float:
        """
        Calculate confidence score for the consensus result.

        Factors:
            - Participation ratio: What fraction of modules participated
            - Spread: How tightly readings agree (lower = higher confidence)
            - Byzantine: Penalty for detected Byzantine nodes
            - Quorum margin: How far above minimum quorum

        Returns:
            Confidence score between 0.0 and 1.0
        """
        if valid_count == 0 or total_count == 0:
            return 0.0

        # Participation factor (0.0 to 1.0)
        participation = valid_count / total_count

        # Spread factor: 1.0 at 0ms, 0.5 at threshold, 0.0 at 2x threshold
        if spread_ms <= 0:
            spread_factor = 1.0
        elif spread_ms >= self.drift_threshold_ms * 2:
            spread_factor = 0.0
        else:
            spread_factor = 1.0 - (spread_ms / (self.drift_threshold_ms * 2))

        # Byzantine penalty: 10% per detected fault
        byzantine_penalty = max(0.0, 1.0 - (byzantine_count * 0.1))

        # Quorum margin: bonus for being above minimum
        quorum_margin = min(1.0, valid_count / (self.quorum * 1.5))

        # Weighted combination
        confidence = (
            participation * 0.3
            + spread_factor * 0.35
            + byzantine_penalty * 0.2
            + quorum_margin * 0.15
        )

        return round(min(1.0, max(0.0, confidence)), 6)

    def _sign_consensus(
        self,
        timestamp: datetime,
        round_id: str,
        participants: List[RTCReading],
    ) -> str:
        """
        Sign consensus result with HMAC-SHA256.

        The signature covers:
            - Consensus timestamp (nanoseconds)
            - Round identifier
            - Sorted list of participating module IDs

        This ensures the result cannot be tampered with after consensus.
        """
        participant_ids = sorted([r.module_id for r in participants])
        data = (
            f"{int(timestamp.timestamp() * 1_000_000_000)}:"
            f"{round_id}:"
            f"{','.join(participant_ids)}"
        )
        return hmac.new(
            self.signing_key,
            data.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def verify_signature(
        self,
        result: ConsensusResult,
    ) -> bool:
        """
        Verify the HMAC-SHA256 signature on a consensus result.

        Used during audit and compliance verification to confirm that
        a previously issued consensus result has not been modified.
        """
        # Reconstruct participants as RTCReading stubs for signing
        participants = [
            RTCReading(module_id=mid, timestamp=result.timestamp)
            for mid in result.participating_modules
        ]
        expected = self._sign_consensus(result.timestamp, result.round_id, participants)
        return hmac.compare_digest(expected, result.signature)

    def get_byzantine_history(self) -> List[ByzantineEvidence]:
        """Return all detected Byzantine events for audit."""
        return list(self._byzantine_events)


# ---------------------------------------------------------------------------
# Demonstration
# ---------------------------------------------------------------------------
def demo():
    """Demonstrate BFT consensus with simulated RTC readings."""
    import random

    print("=" * 70)
    print("BFT Consensus Demonstration")
    print("Simulating 5 RTC modules (1 Byzantine)")
    print("=" * 70)

    now = datetime.now(timezone.utc)
    consensus = BFTConsensus(
        quorum=3,
        drift_threshold_ms=50.0,
        signing_key=b"demo-key-for-illustration-only",
    )

    # Simulate 5 RTC readings: 4 honest, 1 Byzantine
    readings = [
        RTCReading(
            module_id="rtc-east-01",
            timestamp=now + timedelta(microseconds=random.randint(-100, 100)),
            temperature_celsius=26.5,
        ),
        RTCReading(
            module_id="rtc-east-02",
            timestamp=now + timedelta(microseconds=random.randint(-150, 150)),
            temperature_celsius=27.1,
        ),
        RTCReading(
            module_id="rtc-east-03",
            timestamp=now + timedelta(microseconds=random.randint(-80, 80)),
            temperature_celsius=25.8,
        ),
        RTCReading(
            module_id="rtc-east-04",
            timestamp=now + timedelta(microseconds=random.randint(-120, 120)),
            temperature_celsius=26.2,
        ),
        # Byzantine node: large deliberate offset
        RTCReading(
            module_id="rtc-east-05-BYZANTINE",
            timestamp=now + timedelta(seconds=5),  # 5 seconds off
            temperature_celsius=72.0,  # Also running hot
        ),
    ]

    result = consensus.run(readings)

    print(f"\nResult:")
    print(f"  Timestamp:     {result.timestamp.isoformat()}")
    print(f"  Confidence:    {result.confidence:.4f}")
    print(f"  Status:        {result.status.value}")
    print(f"  Participants:  {result.participating_modules}")
    print(f"  Excluded:      {result.excluded_modules}")
    print(f"  Spread:        {result.spread_ms:.3f}ms")
    print(f"  System Drift:  {result.drift_from_system_ms:.3f}ms")
    print(f"  Signature:     {result.signature[:32]}...")
    print(f"  Duration:      {result.round_duration_us}us")

    # Verify signature
    is_valid = consensus.verify_signature(result)
    print(f"  Sig Valid:     {is_valid}")

    # Show Byzantine evidence
    evidence = consensus.get_byzantine_history()
    if evidence:
        print(f"\nByzantine Evidence ({len(evidence)} event(s)):")
        for e in evidence:
            print(f"  {e.module_id}: {e.evidence_type} ({e.deviation_ms:.3f}ms deviation)")

    print("\n" + "=" * 70)

    # Scenario 2: All honest
    print("\nScenario 2: All 4 modules honest")
    honest_readings = [
        RTCReading(
            module_id=f"rtc-west-{i:02d}",
            timestamp=now + timedelta(microseconds=random.randint(-50, 50)),
        )
        for i in range(1, 5)
    ]
    result2 = consensus.run(honest_readings)
    print(f"  Status: {result2.status.value}, Confidence: {result2.confidence:.4f}")

    # Scenario 3: Quorum failure
    print("\nScenario 3: Only 2 readings (below quorum)")
    few_readings = [
        RTCReading(module_id="rtc-solo-01", timestamp=now),
        RTCReading(module_id="rtc-solo-02", timestamp=now, is_valid=False, error="Hardware fault"),
    ]
    result3 = consensus.run(few_readings)
    print(f"  Status: {result3.status.value}, Confidence: {result3.confidence:.4f}")


if __name__ == "__main__":
    demo()
