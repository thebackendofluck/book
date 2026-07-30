# Companion code for "The Backend of Luck" - Chapter 22, Internal Docker Registry.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Chapter 37: Internal Docker Registry Management
# =============================================================================
"""
Registry management modules for iGaming platforms.

This package provides enterprise-grade Docker registry management including:
- Security configuration and authentication
- Automated maintenance and cleanup
- Version management and updates
- Comprehensive security scanning (Trivy integration)
- Aqua Security integration for advanced protection

Regulatory Compliance:
- PCI-DSS: Secure image storage and access controls
- GDPR: Data protection for container artifacts
- SOX: Audit trails and integrity verification
- ISO 27001: Security management framework

Usage:
    from registry_management import (
        RegistrySecurityManager,
        RegistryMaintenanceManager,
        RegistryVersionManager,
        RegistrySecurityScanner,
        AquaSecurityClient
    )
"""

from .security import RegistrySecurityManager  # ty:ignore[unresolved-import]
from .maintenance import RegistryMaintenanceManager  # ty:ignore[unresolved-import]
from .version_manager import RegistryVersionManager  # ty:ignore[unresolved-import]
from .scanner import RegistrySecurityScanner, Vulnerability  # ty:ignore[unresolved-import]
from .aqua_integration import AquaSecurityClient  # ty:ignore[unresolved-import]

__all__ = [
    # Security
    "RegistrySecurityManager",
    # Maintenance
    "RegistryMaintenanceManager",
    # Version Management
    "RegistryVersionManager",
    # Security Scanning
    "RegistrySecurityScanner",
    "Vulnerability",
    # Aqua Security
    "AquaSecurityClient",
]

__version__ = "1.0.0"
__author__ = "iGaming Infrastructure Team"
