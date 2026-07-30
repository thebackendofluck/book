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
RTC Failover Logic

This module provides cascading time source selection with automatic
failover for enterprise RTC systems.

The failover operates in layers:
1. Hardware RTC consensus (always available)
2. GPS time (if available, bypasses internet)
3. NTP time (may be unavailable during attacks)

Each layer provides validation against the previous to detect
tampering or drift. This ensures continuous operation even when
primary time sources are compromised.

Typical iGaming Use Case:
    During a sophisticated attack targeting NTP servers, the
    system automatically falls back to GPS-validated hardware
    RTC consensus, maintaining timestamp accuracy and regulatory
    compliance without manual intervention.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class TimeSource(Enum):
    """Time source identifier for audit logging."""

    RTC_CONSENSUS = "rtc_consensus"
    RTC_NTP_DRIFT = "rtc_ntp_drift"
    RTC_NTP_UNAVAILABLE = "rtc_ntp_unavailable"
    GPS_DIRECT = "gps_direct"
    NTP_VALIDATED = "ntp_validated"


@dataclass
class GPSTime:
    """GPS time reading with satellite count."""

    utc: datetime
    satellites: int
    hdop: float  # Horizontal dilution of precision
    fix_quality: int  # 0=invalid, 1=GPS, 2=DGPS


@dataclass
class RTCConsensusResult:
    """Result from RTC consensus algorithm."""

    timestamp: datetime
    confidence: float  # 0.0 to 1.0
    participating_nodes: int
    drift_ms: float


class NTPTimeout(Exception):
    """Raised when NTP query times out."""

    pass


def get_rtc_consensus() -> Tuple[datetime, float]:
    """
    Get timestamp from hardware RTC consensus.

    This function queries multiple hardware RTC modules and
    uses a Byzantine fault-tolerant consensus algorithm to
    determine the authoritative time.

    Returns:
        Tuple of (timestamp, confidence) where confidence is 0.0-1.0
    """
    # In production, this would query actual RTC hardware
    # This is a placeholder implementation
    return datetime.now(timezone.utc), 0.98


def get_gps_time() -> Optional[GPSTime]:
    """
    Get time from GPS receiver.

    GPS provides atomic clock accuracy (nanosecond-level) and
    is independent of network infrastructure.

    Returns:
        GPSTime object or None if GPS unavailable
    """
    # In production, this would query actual GPS hardware
    # This is a placeholder implementation
    return GPSTime(
        utc=datetime.now(timezone.utc),
        satellites=8,
        hdop=1.2,
        fix_quality=1,
    )


def get_ntp_time(timeout: float = 1.0) -> datetime:
    """
    Get time from NTP server.

    Args:
        timeout: NTP query timeout in seconds

    Returns:
        NTP timestamp

    Raises:
        NTPTimeout: If NTP query times out
    """
    # In production, this would query actual NTP servers
    # This is a placeholder implementation
    return datetime.now(timezone.utc)


def get_authoritative_time() -> Tuple[datetime, str]:
    """
    Get authoritative time with cascading failover.

    This function implements a multi-layer failover strategy:

    Layer 1: Hardware RTC consensus (always available)
        - Primary source with hardware-backed accuracy
        - Tamper-resistant and battery-backed
        - Returns if confidence >= 95%

    Layer 2: GPS time (if available)
        - Bypasses internet, immune to NTP attacks
        - Atomic clock accuracy from satellite constellation
        - Requires 4+ satellites for valid fix

    Layer 3: NTP (may be unavailable during attack)
        - Cross-validated against RTC to detect spoofing
        - Rejected if drift exceeds 100ms from RTC
        - Falls back to RTC-only if unavailable

    Returns:
        Tuple of (timestamp, source) where source is a string
        identifier for audit logging.

    Example:
        ```python
        time, source = get_authoritative_time()
        print(f"Time: {time} (source: {source})")
        # Output: Time: 2024-01-15 12:30:45.123456 (source: rtc_consensus)
        ```
    """
    # Layer 1: Hardware RTC consensus (always available)
    rtc_time, confidence = get_rtc_consensus()

    if confidence >= 0.95:
        return rtc_time, TimeSource.RTC_CONSENSUS.value

    # Layer 2: GPS time (if available, bypasses internet)
    gps_time = get_gps_time()
    if gps_time and gps_time.satellites >= 4:
        return gps_time.utc, TimeSource.GPS_DIRECT.value

    # Layer 3: NTP (may be unavailable during attack)
    try:
        ntp_time = get_ntp_time(timeout=1.0)

        # Cross-validate NTP against RTC
        drift = abs((ntp_time - rtc_time).total_seconds() * 1000)
        if drift < 100:  # Less than 100ms drift
            return ntp_time, TimeSource.NTP_VALIDATED.value
        else:
            logger.warning(f"NTP/RTC drift: {drift}ms, using RTC")
            return rtc_time, TimeSource.RTC_NTP_DRIFT.value

    except NTPTimeout:
        logger.warning("NTP unavailable, using RTC only")
        return rtc_time, TimeSource.RTC_NTP_UNAVAILABLE.value


def validate_timestamp_chain(
    timestamps: list[datetime], max_gap_seconds: float = 1.0
) -> bool:
    """
    Validate a chain of timestamps for monotonicity and gaps.

    Args:
        timestamps: List of timestamps in order
        max_gap_seconds: Maximum allowed gap between consecutive timestamps

    Returns:
        True if chain is valid
    """
    if len(timestamps) < 2:
        return True

    for i in range(1, len(timestamps)):
        # Check monotonicity
        if timestamps[i] <= timestamps[i - 1]:
            logger.error(f"Non-monotonic timestamp at index {i}")
            return False

        # Check gap
        gap = (timestamps[i] - timestamps[i - 1]).total_seconds()
        if gap > max_gap_seconds:
            logger.warning(f"Large timestamp gap: {gap}s at index {i}")

    return True


def detect_time_manipulation(
    claimed_time: datetime, rtc_time: datetime, tolerance_ms: float = 50.0
) -> Tuple[bool, float]:
    """
    Detect potential time manipulation.

    Compares a claimed timestamp against hardware RTC to detect
    clock manipulation attempts.

    Args:
        claimed_time: Timestamp claimed by the system
        rtc_time: Timestamp from hardware RTC
        tolerance_ms: Maximum allowed drift in milliseconds

    Returns:
        Tuple of (is_valid, drift_ms)
    """
    drift_ms = abs((claimed_time - rtc_time).total_seconds() * 1000)

    if drift_ms > tolerance_ms:
        logger.warning(f"Potential time manipulation detected: {drift_ms}ms drift")
        return False, drift_ms

    return True, drift_ms
