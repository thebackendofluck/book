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
auth_context — Player Identity Envelope
========================================

``AuthContext`` is the immutable record that every service receives once a
player has been authenticated.  It travels through every downstream call,
carrying all the information needed to make authorisation decisions without
going back to the authentication store.

``AuthResult`` wraps the outcome of an authentication attempt, holding either
a validated ``AuthContext`` or a failure explanation.

Design notes
------------
* Both models are **immutable** (``model_config = ConfigDict(frozen=True)``).
  Authentication facts must not be mutated after issuance.
* ``roles`` is an open list so that new entitlements (e.g. ``"tester"``,
  ``"vip_host"``) can be added without schema changes.
* ``jurisdiction`` is a two-letter ISO 3166-1 alpha-2 code (e.g. ``"MT"``,
  ``"GB"``, ``"BR"``).  Compliance checks downstream rely on this field.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field


class AuthContext(BaseModel):
    """Immutable identity envelope issued after successful authentication.

    Attributes
    ----------
    player_id:
        Platform-level player identifier (UUID or opaque string).
    brand_id:
        The white-label brand that owns this player relationship.
    session_id:
        Unique session identifier — not the raw token, but a stable UUID
        derived from it so that it is safe to log.
    issued_at:
        UTC timestamp when the session token was created.
    expires_at:
        UTC timestamp after which the token must be rejected.
    ip_address:
        The IPv4 or IPv6 address observed at login.  Used for geo-checks and
        fraud correlation.
    jurisdiction:
        ISO 3166-1 alpha-2 jurisdiction code resolved at login time.
    roles:
        List of entitlement roles granted to this player, e.g.
        ``["player", "vip"]``.  Empty by default.
    """

    model_config = ConfigDict(frozen=True)

    player_id: str = Field(..., description="Platform player UUID")
    brand_id: str = Field(..., description="White-label brand identifier")
    session_id: str = Field(..., description="Stable session UUID (safe to log)")
    issued_at: datetime = Field(..., description="UTC token creation timestamp")
    expires_at: datetime = Field(..., description="UTC token expiry timestamp")
    ip_address: str = Field(..., description="IPv4/IPv6 address at login")
    jurisdiction: str = Field(
        ...,
        min_length=2,
        max_length=2,
        description="ISO 3166-1 alpha-2 jurisdiction code",
    )
    roles: list[str] = Field(
        default_factory=list,
        description="Granted entitlement roles",
    )

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def is_expired(self, *, now: datetime | None = None) -> bool:
        """Return ``True`` if the context has passed its expiry time.

        Parameters
        ----------
        now:
            Optional override for "current time", useful in tests.
            Defaults to ``datetime.utcnow()``.
        """
        reference = now or datetime.now(timezone.utc)
        return reference >= self.expires_at

    def has_role(self, role: str) -> bool:
        """Return ``True`` if *role* is present in the granted roles list."""
        return role in self.roles


class AuthResult(BaseModel):
    """Outcome of an authentication attempt.

    Exactly one of ``context`` or ``failure_reason`` will be populated:

    * ``success=True``  → ``context`` is set, ``failure_reason`` is ``None``.
    * ``success=False`` → ``context`` is ``None``, ``failure_reason`` explains why.

    Attributes
    ----------
    success:
        Whether authentication succeeded.
    context:
        The issued ``AuthContext`` on success; ``None`` on failure.
    failure_reason:
        Human-readable reason for failure; ``None`` on success.
    """

    model_config = ConfigDict(frozen=True)

    success: bool
    context: AuthContext | None = None
    failure_reason: str | None = None

    # ------------------------------------------------------------------
    # Factory helpers — avoids scattered inline construction
    # ------------------------------------------------------------------

    @classmethod
    def ok(cls, context: AuthContext) -> AuthResult:
        """Create a successful ``AuthResult`` wrapping *context*."""
        return cls(success=True, context=context)

    @classmethod
    def fail(cls, reason: str) -> AuthResult:
        """Create a failed ``AuthResult`` with *reason* as the explanation."""
        return cls(success=False, failure_reason=reason)
