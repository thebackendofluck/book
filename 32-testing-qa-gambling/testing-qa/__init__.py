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
Testing and QA Module for iGaming Platforms

This module provides comprehensive testing frameworks for:
- RNG certification and statistical verification
- Load testing for high-volume gambling platforms
- Sports betting and pari-mutuel system testing
- Multi-jurisdictional compliance testing
- Continuous testing and CI/CD integration

Regulatory Compliance:
- GLI (Gaming Laboratories International)
- eCOGRA (eCommerce Online Gaming Regulation and Assurance)
- iTech Labs
- UKGC (UK Gambling Commission)
- MGA (Malta Gaming Authority)
- NJ DGE (New Jersey Division of Gaming Enforcement)
"""

from .rng_certification import (  # ty:ignore[unresolved-import]
    RNGCertificationSystem,
    RNGTestType,
    CertificationStatus,
    RNGTestResult,
)
from .load_testing import (  # ty:ignore[unresolved-import]
    LoadTestingSystem,
    LoadTestType,
    LoadTestPhase,
    LoadTestConfig,
    LoadTestMetrics,
)
from .sports_betting_testing import SportsBettingTestingFramework  # ty:ignore[unresolved-import]
from .pari_mutuel_testing import PariMutuelTestingFramework  # ty:ignore[unresolved-import]
from .compliance_testing import ComplianceTestingFramework  # ty:ignore[unresolved-import]
from .continuous_testing import ContinuousTestingFramework  # ty:ignore[unresolved-import]

__all__ = [
    # RNG Certification
    "RNGCertificationSystem",
    "RNGTestType",
    "CertificationStatus",
    "RNGTestResult",
    # Load Testing
    "LoadTestingSystem",
    "LoadTestType",
    "LoadTestPhase",
    "LoadTestConfig",
    "LoadTestMetrics",
    # Sports & Pari-mutuel Testing
    "SportsBettingTestingFramework",
    "PariMutuelTestingFramework",
    # Compliance & Continuous Testing
    "ComplianceTestingFramework",
    "ContinuousTestingFramework",
]

__version__ = "1.0.0"
