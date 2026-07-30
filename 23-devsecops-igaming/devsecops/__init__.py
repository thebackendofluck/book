# Companion code for "The Backend of Luck" - Chapter 23, DevSecOps for iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Chapter 11: DevSecOps for iGaming
=================================

Security pipeline components for iGaming platforms implementing
comprehensive DevSecOps practices including:

- Secret detection and entropy analysis
- ML-based secret detection
- Baseline management for detect-secrets
- Supply chain security for third-party providers
- Vulnerability management at scale
- Security champion program

Modules:
--------
- baseline_manager: Detect-secrets baseline management
- entropy_analyzer: High-entropy string detection for secrets
- simple_ml_detector: ML-based secret detection (standalone)
- ml_secret_detector: Advanced ML secret detection (requires sklearn)
- test_secret_detection: Comprehensive test suite
- supply_chain_security: Third-party provider security assessment
- vulnerability_management: Enterprise vulnerability management
- security_champion: Security champion program framework

Usage:
------
    from devsecops.entropy_analyzer import analyze_file, detect_high_entropy_strings
    from devsecops.baseline_manager import SecretBaselineManager
    from devsecops.simple_ml_detector import SimpleMLSecretDetector
    from devsecops.supply_chain_security import SupplyChainSecurityManager
    from devsecops.vulnerability_management import VulnerabilityManagementSystem
    from devsecops.security_champion import SecurityChampionProgram

Example:
--------
    # Analyze file for secrets
    result = analyze_file("config.py", min_entropy=4.0)
    if result["secrets_found"] > 0:
        print(f"Found {result['secrets_found']} potential secrets")

    # Use ML detector
    detector = SimpleMLSecretDetector()
    is_secret, confidence = detector.predict("AKIAIOSFODNN7EXAMPLE")

    # Supply chain security
    manager = SupplyChainSecurityManager(redis_client, db_pool)
    assessment = await manager.assess_provider_security(provider)

    # Vulnerability management
    vuln_system = VulnerabilityManagementSystem(redis_client, db_pool, config)
    vulnerabilities = await vuln_system.discover_vulnerabilities()
"""

from .baseline_manager import SecretBaselineManager  # ty:ignore[unresolved-import]
from .entropy_analyzer import analyze_file, calculate_entropy, detect_high_entropy_strings  # ty:ignore[unresolved-import]
from .simple_ml_detector import SimpleMLSecretDetector  # ty:ignore[unresolved-import]
from .supply_chain_security import (  # ty:ignore[unresolved-import]
    SupplyChainSecurityManager,
    ThirdPartyProvider,
    RiskLevel,
    ProviderType,
)
from .vulnerability_management import (  # ty:ignore[unresolved-import]
    VulnerabilityManagementSystem,
    Vulnerability,
    VulnerabilityStatus,
    VulnerabilitySeverity,
)
from .security_champion import (  # ty:ignore[unresolved-import]
    SecurityChampionProgram,
    SecurityChampion,
    ChampionLevel,
)

__all__ = [
    # Secret detection
    "SecretBaselineManager",
    "analyze_file",
    "calculate_entropy",
    "detect_high_entropy_strings",
    "SimpleMLSecretDetector",
    # Supply chain security
    "SupplyChainSecurityManager",
    "ThirdPartyProvider",
    "RiskLevel",
    "ProviderType",
    # Vulnerability management
    "VulnerabilityManagementSystem",
    "Vulnerability",
    "VulnerabilityStatus",
    "VulnerabilitySeverity",
    # Security champion
    "SecurityChampionProgram",
    "SecurityChampion",
    "ChampionLevel",
]

__version__ = "1.1.0"
__author__ = "iGaming Security Team"
