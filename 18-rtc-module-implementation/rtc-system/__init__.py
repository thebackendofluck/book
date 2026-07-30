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
Real-Time Clock (RTC) System for iGaming Platforms

This module provides enterprise-grade Real-Time Clock implementations
specifically designed for online gambling platforms where temporal
accuracy is a legal mandate and business-critical requirement.

Key Components:
- redis_cache: High-performance Redis caching for RTC operations
- chaos_testing: Chaos engineering tests for RTC resilience
- failover: Cascading time source failover logic
- yubihsm_integration: Hardware Security Module integration

Features:
- Microsecond-precision timestamps
- Cryptographically signed timestamps
- Distributed consensus across multiple RTC modules
- Hardware security module integration (YubiHSM, Zymkey)
- Tamper-evident audit logging
- Regulatory compliance (GLI-11, ISO 27001)

Note: For hardware entropy generation (RNG seeding), see the
chapter-39 rng-system module which provides the entropy collection
and management functionality that integrates with this RTC system.
"""

from .redis_cache import RTCCache  # ty:ignore[unresolved-import]
from .chaos_testing import RTCChaosTests  # ty:ignore[unresolved-import]
from .failover import (  # ty:ignore[unresolved-import]
    get_authoritative_time,
    get_rtc_consensus,
    get_gps_time,
    get_ntp_time,
)
from .yubihsm_integration import YubiHSMEnhancedRTC  # ty:ignore[unresolved-import]
from .entropy import (  # ty:ignore[unresolved-import]
    ZymkeyEntropy,
    get_hardware_entropy,
    get_hardware_entropy_hex,
    is_zymkey_available,
)

__all__ = [
    # Cache Layer
    "RTCCache",
    # Testing
    "RTCChaosTests",
    # Failover
    "get_authoritative_time",
    "get_rtc_consensus",
    "get_gps_time",
    "get_ntp_time",
    # HSM
    "YubiHSMEnhancedRTC",
    # Entropy
    "ZymkeyEntropy",
    "get_hardware_entropy",
    "get_hardware_entropy_hex",
    "is_zymkey_available",
]

__version__ = "1.0.0"
