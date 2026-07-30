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
Cascading Failover Implementation for RTC Time Sources
========================================================

Implements the full cascading failover chain:
    RTC Consensus -> GPS -> NTP -> Degraded RTC

Each layer validates against the previous to detect tampering or
drift, ensuring continuous operation even when primary time sources
are compromised.

GLI-11 Requirement: Section 5.4.2 mandates that gaming systems maintain
accurate time even during infrastructure failures. This module ensures
uninterrupted timestamp availability with automatic degradation and
recovery.

Architecture:
    +-----------------+
    | RTC Consensus   |  Primary: 4+ hardware modules, BFT consensus
    | (Hardware)      |  Accuracy: +/- 2ppm
    +--------+--------+
             |fail
    +--------v--------+
    | GPS Time        |  Secondary: satellite-based, immune to network attacks
    | (Satellite)     |  Accuracy: +/- 100ns (with PPS)
    +--------+--------+
             |fail
    +--------v--------+
    | NTP Validated   |  Tertiary: network-based, cross-validated with RTC
    | (Network)       |  Accuracy: +/- 10ms (typical)
    +--------+--------+
             |fail
    +--------v--------+
    | Degraded RTC    |  Emergency: single RTC module, reduced confidence
    | (Single Module) |  Accuracy: +/- 500ms (24h without sync)
    +-----------------+

Usage:
    from cascading_failover import FailoverManager, FailoverConfig

    config = FailoverConfig(
        drift_threshold_ms=50.0,
        gps_device="/dev/ttyAMA0",
        ntp_servers=["time.google.com", "time.cloudflare.com"],
    )
    manager = FailoverManager(config)

    # Get authoritative time (automatically selects best source)
    result = manager.get_time()
    print(f"Time: {result.timestamp}, Source: {result.source}")
"""

import enum
import hashlib
import hmac
import logging
import os
import random
import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("rtc-failover")


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------
class TimeSource(enum.Enum):
    """Time source identifier for audit trail."""
    RTC_CONSENSUS = "rtc_consensus"       # Primary: BFT consensus across modules
    GPS = "gps"                           # Secondary: satellite time
    NTP_VALIDATED = "ntp_validated"        # Tertiary: NTP cross-validated with RTC
    DEGRADED_RTC = "degraded_rtc"         # Emergency: single RTC, reduced confidence


class FailoverReason(enum.Enum):
    """Reason for failover event."""
    LOW_CONFIDENCE = "low_confidence"
    QUORUM_FAILURE = "quorum_failure"
    GPS_NO_FIX = "gps_no_fix"
    NTP_TIMEOUT = "ntp_timeout"
    NTP_DRIFT_MISMATCH = "ntp_drift_mismatch"
    HARDWARE_FAILURE = "hardware_failure"
    MANUAL_OVERRIDE = "manual_override"
    RECOVERY = "recovery"


@dataclass
class FailoverConfig:
    """Configuration for the failover manager."""
    # Drift thresholds
    drift_threshold_ms: float = 50.0
    degraded_drift_threshold_ms: float = 500.0
    ntp_validation_threshold_ms: float = 100.0

    # RTC consensus settings
    consensus_min_confidence: float = 0.95
    consensus_quorum: int = 3

    # GPS settings
    gps_enabled: bool = True
    gps_device: str = "/dev/ttyAMA0"
    gps_baud_rate: int = 9600
    gps_min_satellites: int = 4
    gps_timeout_s: float = 5.0

    # NTP settings
    ntp_servers: List[str] = field(default_factory=lambda: [
        "time.google.com",
        "time.cloudflare.com",
        "pool.ntp.org",
        "time.nist.gov",
    ])
    ntp_timeout_s: float = 2.0
    ntp_max_retries: int = 2

    # Failover behavior
    auto_recovery: bool = True
    recovery_check_interval_s: float = 30.0
    max_degraded_duration_s: float = 3600.0  # 1 hour max in degraded mode

    # Signing
    signing_key: bytes = b"replace-with-hsm-key"


@dataclass
class TimeResult:
    """Result from time source query."""
    timestamp: datetime
    source: TimeSource
    confidence: float
    drift_ms: float
    signature: str
    details: Dict[str, object] = field(default_factory=dict)


@dataclass
class FailoverEvent:
    """Record of a failover event."""
    event_time: datetime
    previous_source: TimeSource
    new_source: TimeSource
    reason: FailoverReason
    drift_at_failover_ms: float
    details: str


# ---------------------------------------------------------------------------
# Time Source Implementations
# ---------------------------------------------------------------------------
class RTCConsensusSource:
    """Primary time source: BFT consensus across hardware RTC modules."""

    def __init__(self, config: FailoverConfig):
        self.config = config
        self.last_confidence = 0.0
        self.last_drift_ms = 0.0

    def query(self) -> Optional[TimeResult]:
        """
        Query hardware RTC modules for consensus time.

        In production, this interfaces with the BFT consensus engine.
        """
        try:
            # In production: call bft_consensus.run() with hardware readings
            # Simulated for portability
            now = datetime.now(timezone.utc)
            drift_ms = random.uniform(-0.5, 0.5)
            confidence = random.uniform(0.96, 1.0)

            self.last_confidence = confidence
            self.last_drift_ms = drift_ms

            if confidence < self.config.consensus_min_confidence:
                logger.warning(f"RTC consensus confidence low: {confidence:.4f}")
                return None

            return TimeResult(
                timestamp=now + timedelta(milliseconds=drift_ms),
                source=TimeSource.RTC_CONSENSUS,
                confidence=confidence,
                drift_ms=drift_ms,
                signature="",
                details={
                    "modules_participating": 4,
                    "spread_ms": abs(drift_ms) * 2,
                },
            )
        except Exception as e:
            logger.error(f"RTC consensus query failed: {e}")
            return None


class GPSTimeSource:
    """Secondary time source: GPS satellite time."""

    def __init__(self, config: FailoverConfig):
        self.config = config

    def query(self) -> Optional[TimeResult]:
        """
        Query GPS receiver for satellite time.

        GPS provides atomic clock accuracy and is immune to network
        attacks. Requires clear sky visibility and a GPS module
        (e.g., u-blox NEO-M8N).
        """
        if not self.config.gps_enabled:
            return None

        try:
            # In production: parse NMEA sentences from GPS device
            # $GPRMC,123456.00,A,5149.56,N,00000.00,E,0.0,0.0,080326,,,A*6F
            if not os.path.exists(self.config.gps_device):
                logger.debug(f"GPS device not found: {self.config.gps_device}")
                return None

            # Simulated GPS reading
            now = datetime.now(timezone.utc)
            satellites = random.randint(4, 12)

            if satellites < self.config.gps_min_satellites:
                logger.warning(f"GPS: insufficient satellites ({satellites})")
                return None

            return TimeResult(
                timestamp=now,
                source=TimeSource.GPS,
                confidence=0.99,
                drift_ms=0.001,
                signature="",
                details={
                    "satellites": satellites,
                    "hdop": 1.2,
                    "fix_quality": 1,
                },
            )
        except Exception as e:
            logger.error(f"GPS query failed: {e}")
            return None


class NTPTimeSource:
    """Tertiary time source: NTP servers, cross-validated with RTC."""

    def __init__(self, config: FailoverConfig):
        self.config = config

    def query(self, rtc_reference: Optional[datetime] = None) -> Optional[TimeResult]:
        """
        Query NTP servers and cross-validate against RTC.

        NTP provides good accuracy (~10ms) but is vulnerable to
        network attacks. Cross-validation with RTC detects spoofing.
        """
        for server in self.config.ntp_servers:
            try:
                ntp_time = self._query_ntp_server(server)
                if ntp_time is None:
                    continue

                # Cross-validate with RTC reference if available
                if rtc_reference:
                    drift_ms = abs(
                        (ntp_time - rtc_reference).total_seconds() * 1000
                    )
                    if drift_ms > self.config.ntp_validation_threshold_ms:
                        logger.warning(
                            f"NTP/{server} drift from RTC: {drift_ms:.2f}ms "
                            f"(threshold: {self.config.ntp_validation_threshold_ms}ms)"
                        )
                        continue  # Try next server

                system_drift = abs(
                    (ntp_time - datetime.now(timezone.utc)).total_seconds() * 1000
                )

                return TimeResult(
                    timestamp=ntp_time,
                    source=TimeSource.NTP_VALIDATED,
                    confidence=0.90,
                    drift_ms=system_drift,
                    signature="",
                    details={
                        "ntp_server": server,
                        "cross_validated": rtc_reference is not None,
                    },
                )
            except Exception as e:
                logger.warning(f"NTP query to {server} failed: {e}")
                continue

        logger.error("All NTP servers failed")
        return None

    def _query_ntp_server(self, server: str) -> Optional[datetime]:
        """
        Send NTP query and parse response.

        Uses simplified NTP v4 query (mode 3, client).
        """
        try:
            # NTP packet: 48 bytes, first byte = LI=0, VN=4, Mode=3
            ntp_data = b"\x23" + 47 * b"\0"

            # Set transmit timestamp at bytes 40-47
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(self.config.ntp_timeout_s)
            sock.sendto(ntp_data, (server, 123))

            data, _ = sock.recvfrom(1024)
            sock.close()

            if len(data) < 48:
                return None

            # Extract transmit timestamp (bytes 40-47)
            t = struct.unpack("!12I", data)[10]
            t -= 2208988800  # Convert NTP epoch to Unix epoch

            return datetime.fromtimestamp(t, tz=timezone.utc)
        except (socket.timeout, OSError) as e:
            logger.debug(f"NTP socket error for {server}: {e}")
            return None


class DegradedRTCSource:
    """Emergency time source: single RTC module with reduced confidence."""

    def __init__(self, config: FailoverConfig):
        self.config = config

    def query(self) -> Optional[TimeResult]:
        """
        Query a single RTC module as last resort.

        Provides degraded accuracy and low confidence. The system
        should alert operators and attempt recovery to primary source.
        """
        try:
            now = datetime.now(timezone.utc)
            # In degraded mode, drift could be higher
            drift_ms = random.uniform(-5.0, 5.0)

            return TimeResult(
                timestamp=now + timedelta(milliseconds=drift_ms),
                source=TimeSource.DEGRADED_RTC,
                confidence=0.5,
                drift_ms=drift_ms,
                signature="",
                details={
                    "degraded": True,
                    "warning": "Operating on single RTC module without consensus",
                },
            )
        except Exception as e:
            logger.error(f"Degraded RTC query failed: {e}")
            return None


# ---------------------------------------------------------------------------
# Failover Manager
# ---------------------------------------------------------------------------
class FailoverManager:
    """
    Manages cascading failover across time sources.

    Automatically selects the best available time source and handles
    failover/recovery transitions with full audit logging.
    """

    def __init__(self, config: Optional[FailoverConfig] = None):
        self.config = config or FailoverConfig()

        # Initialize sources
        self.rtc_source = RTCConsensusSource(self.config)
        self.gps_source = GPSTimeSource(self.config)
        self.ntp_source = NTPTimeSource(self.config)
        self.degraded_source = DegradedRTCSource(self.config)

        # State
        self.current_source = TimeSource.RTC_CONSENSUS
        self.failover_history: List[FailoverEvent] = []
        self.degraded_since: Optional[datetime] = None
        self._lock = threading.Lock()

        # Recovery thread
        if self.config.auto_recovery:
            self._recovery_thread = threading.Thread(
                target=self._recovery_loop, daemon=True
            )
            self._recovery_thread.start()

    def get_time(self) -> TimeResult:
        """
        Get authoritative time from the best available source.

        Implements the cascading failover chain:
            RTC Consensus -> GPS -> NTP -> Degraded RTC

        Returns:
            TimeResult with timestamp, source, confidence, and signature
        """
        with self._lock:
            # Layer 1: RTC Consensus (primary)
            result = self.rtc_source.query()
            if result is not None:
                if self.current_source != TimeSource.RTC_CONSENSUS:
                    self._record_failover(
                        self.current_source,
                        TimeSource.RTC_CONSENSUS,
                        FailoverReason.RECOVERY,
                        result.drift_ms,
                        "Recovered to primary RTC consensus",
                    )
                    self.degraded_since = None
                self.current_source = TimeSource.RTC_CONSENSUS
                result.signature = self._sign(result)
                return result

            # Layer 2: GPS (secondary)
            rtc_ref_time = datetime.now(timezone.utc)  # Use system as rough reference
            result = self.gps_source.query()
            if result is not None:
                if self.current_source != TimeSource.GPS:
                    self._record_failover(
                        self.current_source,
                        TimeSource.GPS,
                        FailoverReason.LOW_CONFIDENCE
                            if self.current_source == TimeSource.RTC_CONSENSUS
                            else FailoverReason.RECOVERY,
                        result.drift_ms,
                        "Failed over to GPS time source",
                    )
                self.current_source = TimeSource.GPS
                result.signature = self._sign(result)
                return result

            # Layer 3: NTP (tertiary, cross-validated)
            result = self.ntp_source.query(rtc_reference=rtc_ref_time)
            if result is not None:
                if self.current_source != TimeSource.NTP_VALIDATED:
                    self._record_failover(
                        self.current_source,
                        TimeSource.NTP_VALIDATED,
                        FailoverReason.GPS_NO_FIX,
                        result.drift_ms,
                        "Failed over to NTP (cross-validated)",
                    )
                self.current_source = TimeSource.NTP_VALIDATED
                result.signature = self._sign(result)
                return result

            # Layer 4: Degraded RTC (emergency)
            result = self.degraded_source.query()
            if result is not None:
                if self.current_source != TimeSource.DEGRADED_RTC:
                    self._record_failover(
                        self.current_source,
                        TimeSource.DEGRADED_RTC,
                        FailoverReason.NTP_TIMEOUT,
                        result.drift_ms,
                        "EMERGENCY: All primary sources failed, using degraded RTC",
                    )
                    self.degraded_since = datetime.now(timezone.utc)
                self.current_source = TimeSource.DEGRADED_RTC

                # Check degraded duration
                if self.degraded_since:
                    degraded_duration = (
                        datetime.now(timezone.utc) - self.degraded_since
                    ).total_seconds()
                    if degraded_duration > self.config.max_degraded_duration_s:
                        logger.critical(
                            f"Degraded mode for {degraded_duration:.0f}s "
                            f"exceeds maximum {self.config.max_degraded_duration_s}s"
                        )
                        result.details["max_degraded_exceeded"] = True

                result.signature = self._sign(result)
                return result

            # Should never reach here
            raise RuntimeError("All time sources have failed")

    def get_status(self) -> Dict[str, object]:
        """Get current failover status for monitoring."""
        return {
            "current_source": self.current_source.value,
            "degraded_since": self.degraded_since.isoformat() if self.degraded_since else None,
            "failover_count": len(self.failover_history),
            "recent_failovers": [
                {
                    "time": e.event_time.isoformat(),
                    "from": e.previous_source.value,
                    "to": e.new_source.value,
                    "reason": e.reason.value,
                }
                for e in self.failover_history[-5:]
            ],
        }

    def force_source(self, source: TimeSource) -> None:
        """
        Force a specific time source (admin operation).

        Used during maintenance or troubleshooting to manually
        select a time source.
        """
        old_source = self.current_source
        self.current_source = source
        self._record_failover(
            old_source, source,
            FailoverReason.MANUAL_OVERRIDE,
            0.0,
            f"Manual override to {source.value}",
        )
        logger.warning(f"Time source manually set to {source.value}")

    def _record_failover(
        self,
        previous: TimeSource,
        new: TimeSource,
        reason: FailoverReason,
        drift_ms: float,
        details: str,
    ) -> None:
        """Record a failover event for audit trail."""
        event = FailoverEvent(
            event_time=datetime.now(timezone.utc),
            previous_source=previous,
            new_source=new,
            reason=reason,
            drift_at_failover_ms=drift_ms,
            details=details,
        )
        self.failover_history.append(event)
        logger.warning(
            f"FAILOVER: {previous.value} -> {new.value} "
            f"reason={reason.value} drift={drift_ms:.3f}ms: {details}"
        )

    def _sign(self, result: TimeResult) -> str:
        """Sign a time result with HMAC-SHA256."""
        data = (
            f"{int(result.timestamp.timestamp() * 1_000_000_000)}:"
            f"{result.source.value}:"
            f"{result.confidence}"
        )
        return hmac.new(
            self.config.signing_key,
            data.encode(),
            hashlib.sha256,
        ).hexdigest()

    def _recovery_loop(self) -> None:
        """Background thread that attempts recovery to primary source."""
        while True:
            time.sleep(self.config.recovery_check_interval_s)

            if self.current_source == TimeSource.RTC_CONSENSUS:
                continue  # Already on primary

            logger.info(
                f"Recovery check: currently on {self.current_source.value}, "
                f"attempting primary..."
            )

            # Try to recover to primary
            result = self.rtc_source.query()
            if result is not None:
                with self._lock:
                    self._record_failover(
                        self.current_source,
                        TimeSource.RTC_CONSENSUS,
                        FailoverReason.RECOVERY,
                        result.drift_ms,
                        "Auto-recovered to primary RTC consensus",
                    )
                    self.current_source = TimeSource.RTC_CONSENSUS
                    self.degraded_since = None


# ---------------------------------------------------------------------------
# Demonstration
# ---------------------------------------------------------------------------
def demo():
    """Demonstrate cascading failover scenarios."""
    print("=" * 70)
    print("Cascading Failover Demonstration")
    print("=" * 70)

    config = FailoverConfig(auto_recovery=False)
    manager = FailoverManager(config)

    # Scenario 1: Normal operation (RTC Consensus)
    print("\n1. Normal operation (RTC Consensus)")
    result = manager.get_time()
    print(f"   Time: {result.timestamp.isoformat()}")
    print(f"   Source: {result.source.value}")
    print(f"   Confidence: {result.confidence:.4f}")
    print(f"   Signature: {result.signature[:32]}...")

    # Scenario 2: Get multiple timestamps
    print("\n2. Multiple timestamps:")
    for i in range(3):
        r = manager.get_time()
        print(f"   [{i}] {r.timestamp.isoformat()} via {r.source.value}")

    # Show status
    status = manager.get_status()
    print(f"\n3. Failover status:")
    print(f"   Current source: {status['current_source']}")
    print(f"   Failover count: {status['failover_count']}")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    demo()
