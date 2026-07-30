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
security — Cross-Cutting Security Domain
=========================================

Provides authentication, authorisation, fraud detection, and geo-compliance
primitives used by every other domain in the platform.

Packages
--------
auth_context
    ``AuthContext`` and ``AuthResult`` — the canonical identity envelope that
    flows through every service boundary after a player authenticates.

session_guard
    ``SessionGuard`` — issues, validates, and revokes player session tokens,
    enforces concurrent-session limits, and integrates reality-check timers.

geo_policy
    ``GeoPolicy`` — blocks restricted jurisdictions per brand, detects VPN
    usage, and answers geo-eligibility queries.

token_service
    ``TokenService`` — creates and decodes short-lived, HMAC-signed game
    launch tokens that carry all the context a supplier RGS needs.

fraud_flags
    ``FraudFlags`` — sliding-window velocity checks, amount-anomaly detection,
    manual flagging, and per-player risk scoring.

Typical import pattern::

    from acmetocasino.security.auth_context import AuthContext, AuthResult
    from acmetocasino.security.session_guard import SessionGuard
    from acmetocasino.security.geo_policy import GeoPolicy, GeoCheckResult
    from acmetocasino.security.token_service import TokenService, GameTokenPayload
    from acmetocasino.security.fraud_flags import FraudFlags, RiskLevel
"""

from __future__ import annotations

from acmetocasino.security.auth_context import AuthContext, AuthResult
from acmetocasino.security.session_guard import SessionGuard, SessionStatus
from acmetocasino.security.geo_policy import GeoPolicy, GeoCheckResult
from acmetocasino.security.token_service import TokenService, GameTokenPayload
from acmetocasino.security.fraud_flags import FraudFlags, RiskLevel, FraudCheckResult

__all__ = [
    "AuthContext",
    "AuthResult",
    "SessionGuard",
    "SessionStatus",
    "GeoPolicy",
    "GeoCheckResult",
    "TokenService",
    "GameTokenPayload",
    "FraudFlags",
    "RiskLevel",
    "FraudCheckResult",
]
