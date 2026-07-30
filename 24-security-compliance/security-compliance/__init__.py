# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Chapter 29: Security and Compliance
Enterprise security modules for iGaming platforms.

This module provides production-ready security implementations including:
- Penetration testing framework
- Intrusion Detection/Prevention Systems (IDS/IPS)
- Network encryption monitoring
- CIS Docker Security Benchmark scanning
- Automated compliance reporting

Usage:
    from security_compliance import (
        PenetrationTestingFramework,
        GamblingIDS,
        NetworkEncryptionMonitor,
        ReportGenerator,
        CISDockerScanner
    )
"""

from .pentest_framework import (  # ty:ignore[unresolved-import]
    PenetrationTestingFramework,
    TestResult,
    Vulnerability,
    VulnerabilitySeverity,
)
from .ids_ips import (  # ty:ignore[unresolved-import]
    GamblingIDS,
    ThreatAlert,
    DetectionRule,
    ThreatCategory,
)
from .network_monitor import (  # ty:ignore[unresolved-import]
    NetworkEncryptionMonitor,
    PacketAnalysisResult,
    EncryptionStats,
)
from .reporting import (  # ty:ignore[unresolved-import]
    ReportGenerator,
    SecurityReport,
    ComplianceReport,
)
from .cis_scanner import (  # ty:ignore[unresolved-import]
    CISDockerScanner,
    CISControl,
    AuditResult,
)

__all__ = [
    # Penetration Testing
    "PenetrationTestingFramework",
    "TestResult",
    "Vulnerability",
    "VulnerabilitySeverity",
    # IDS/IPS
    "GamblingIDS",
    "ThreatAlert",
    "DetectionRule",
    "ThreatCategory",
    # Network Monitoring
    "NetworkEncryptionMonitor",
    "PacketAnalysisResult",
    "EncryptionStats",
    # Reporting
    "ReportGenerator",
    "SecurityReport",
    "ComplianceReport",
    # CIS Scanner
    "CISDockerScanner",
    "CISControl",
    "AuditResult",
]

__version__ = "1.0.0"
