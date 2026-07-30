# Companion code for "The Backend of Luck" - Chapter 19, Anti-Fraud System Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Compliance Module

This module provides comprehensive regulatory compliance features including
GDPR, CCPA, PCI DSS, AML/KYC, and gaming commission requirements.
"""

__version__ = "1.0.0"
__author__ = "Compliance Team"

from .compliance_engine import (  # ty:ignore[unresolved-import]
    ComplianceEngine,
    ComplianceRule,
    ComplianceCheck,
    DataSubjectRequest,
    AuditLogEntry,
    compliance_engine,
    initialize_compliance_engine
)

__all__ = [
    "ComplianceEngine",
    "ComplianceRule",
    "ComplianceCheck",
    "DataSubjectRequest",
    "AuditLogEntry",
    "compliance_engine",
    "initialize_compliance_engine"
]