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
geo_policy — Geo-Compliance and Jurisdiction Enforcement
=========================================================

``GeoPolicy`` answers two kinds of questions:

1. **Access check** — Is this IP address + jurisdiction combination permitted
   to interact with the platform right now?
2. **Supplier check** — Is a specific supplier licensed to operate in this
   jurisdiction for a given brand?

``GeoCheckResult`` is a lightweight dataclass (not Pydantic) because it is a
pure value object that never crosses a serialisation boundary in this layer —
it lives entirely within the security domain.

Design notes
------------
VPN detection is modelled as an async-friendly flag:  ``GeoPolicy`` accepts a
``vpn_detection_enabled`` flag and, when true, delegates to a pluggable
``VpnDetector`` protocol.  The default implementation always returns
``False`` (no VPN detected), which is correct for test/dev environments.
In production, wire in a real IP-reputation provider (e.g. MaxMind, IPQualityScore).

Blocked jurisdiction lists are per-brand, keyed by ``brand_id``.  A ``None``
key acts as a wildcard default applied to all brands that do not have an
explicit entry.

All jurisdiction codes are ISO 3166-1 alpha-2 (two uppercase letters).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


# ---------------------------------------------------------------------------
# VPN detection protocol — pluggable for testing
# ---------------------------------------------------------------------------


class VpnDetector(Protocol):
    """Protocol for pluggable VPN/proxy detection."""

    def is_vpn(self, ip_address: str) -> bool:
        """Return ``True`` if *ip_address* appears to be a VPN or proxy exit."""
        ...


class _NullVpnDetector:
    """No-op implementation — always reports 'not a VPN'."""

    def is_vpn(self, ip_address: str) -> bool:  # noqa: ARG002
        return False


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GeoCheckResult:
    """Outcome of a geo-compliance check.

    Attributes
    ----------
    allowed:
        ``True`` if the player is permitted to access the platform.
    jurisdiction_detected:
        The jurisdiction code that the platform resolved for this request.
        May differ from the client-declared jurisdiction if the IP resolves
        to a different country.
    reason:
        Human-readable explanation when ``allowed=False``; ``None`` otherwise.
    vpn_detected:
        ``True`` if the IP was flagged as a VPN or proxy exit node.
    """

    allowed: bool
    jurisdiction_detected: str
    reason: str | None = None
    vpn_detected: bool = False


# ---------------------------------------------------------------------------
# GeoPolicy
# ---------------------------------------------------------------------------

# Default blocked jurisdiction list (applies to all brands unless overridden).
# These codes represent jurisdictions where online gambling is broadly prohibited
# or where operating requires country-specific licences not held by the platform.
_DEFAULT_BLOCKED: frozenset[str] = frozenset(
    {
        "US",  # United States — federal wire act / state patchwork
        "KP",  # North Korea — OFAC sanctions
        "IR",  # Iran — OFAC sanctions
        "SY",  # Syria — OFAC sanctions
        "CU",  # Cuba — OFAC sanctions
    }
)

# Jurisdiction → set of supplier_ids that are NOT licensed there.
# In production this comes from a licensing database.
_DEFAULT_SUPPLIER_BLOCKS: dict[str, frozenset[str]] = {
    "GB": frozenset({"unlicensed-rng"}),  # UKGC requires specific approval
    "SE": frozenset({"unlicensed-rng"}),  # Spelinspektionen licence required
}


class GeoPolicy:
    """Enforces geographic access rules for the platform.

    Parameters
    ----------
    brand_blocked_jurisdictions:
        Mapping of ``brand_id → set[jurisdiction_code]`` for per-brand
        jurisdiction blocking.  Use ``None`` as the key for platform-wide
        defaults that apply to all brands.
    vpn_detector:
        Pluggable VPN detection implementation.  Defaults to no-op.
    vpn_blocks_access:
        If ``True``, VPN-detected connections are rejected.  Default: ``False``
        (VPN is flagged but access is still granted — useful for testing).
    supplier_jurisdiction_blocks:
        Mapping of ``jurisdiction_code → set[supplier_id]`` for supplier
        licensing restrictions.

    Examples
    --------
    >>> policy = GeoPolicy()
    >>> result = policy.check_access("1.2.3.4", "MT")
    >>> result.allowed
    True
    >>> result = policy.check_access("1.2.3.4", "US")
    >>> result.allowed
    False
    """

    def __init__(
        self,
        *,
        brand_blocked_jurisdictions: dict[str | None, set[str]] | None = None,
        vpn_detector: VpnDetector | None = None,
        vpn_blocks_access: bool = False,
        supplier_jurisdiction_blocks: dict[str, frozenset[str]] | None = None,
    ) -> None:
        # Normalise brand blocks — merge caller config with platform defaults.
        self._brand_blocks: dict[str | None, frozenset[str]] = {
            None: _DEFAULT_BLOCKED,
        }
        if brand_blocked_jurisdictions:
            for brand, codes in brand_blocked_jurisdictions.items():
                self._brand_blocks[brand] = frozenset(
                    c.upper() for c in codes
                )

        self._vpn_detector: VpnDetector = vpn_detector or _NullVpnDetector()
        self._vpn_blocks_access = vpn_blocks_access
        self._supplier_blocks: dict[str, frozenset[str]] = (
            supplier_jurisdiction_blocks or _DEFAULT_SUPPLIER_BLOCKS
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_access(
        self,
        ip_address: str,
        jurisdiction: str,
        brand_id: str | None = None,
    ) -> GeoCheckResult:
        """Determine whether *ip_address* from *jurisdiction* may access the platform.

        Parameters
        ----------
        ip_address:
            IPv4 or IPv6 address to evaluate.
        jurisdiction:
            ISO 3166-1 alpha-2 code declared by the client or resolved from IP.
        brand_id:
            Optional brand context for brand-specific overrides.

        Returns
        -------
        GeoCheckResult
            Access decision with full diagnostic detail.
        """
        normalised = jurisdiction.upper()
        vpn_detected = self._vpn_detector.is_vpn(ip_address)

        if vpn_detected and self._vpn_blocks_access:
            return GeoCheckResult(
                allowed=False,
                jurisdiction_detected=normalised,
                reason="VPN or proxy usage is not permitted",
                vpn_detected=True,
            )

        if self._is_blocked(normalised, brand_id):
            return GeoCheckResult(
                allowed=False,
                jurisdiction_detected=normalised,
                reason=f"Jurisdiction {normalised!r} is not permitted for this brand",
                vpn_detected=vpn_detected,
            )

        return GeoCheckResult(
            allowed=True,
            jurisdiction_detected=normalised,
            vpn_detected=vpn_detected,
        )

    def is_jurisdiction_allowed(
        self,
        jurisdiction: str,
        supplier_id: str,
    ) -> bool:
        """Return ``True`` if *supplier_id* is licensed to operate in *jurisdiction*.

        Parameters
        ----------
        jurisdiction:
            ISO 3166-1 alpha-2 code.
        supplier_id:
            Supplier/RGS identifier.

        Returns
        -------
        bool
            ``True`` when the supplier may serve players in this jurisdiction.
        """
        normalised = jurisdiction.upper()
        blocked_suppliers = self._supplier_blocks.get(normalised, frozenset())
        return supplier_id not in blocked_suppliers

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _is_blocked(self, jurisdiction: str, brand_id: str | None) -> bool:
        """Return ``True`` if *jurisdiction* is blocked for *brand_id*."""
        # Check brand-specific block list first
        if brand_id and brand_id in self._brand_blocks:
            if jurisdiction in self._brand_blocks[brand_id]:
                return True

        # Fall back to platform-wide defaults
        default_blocks = self._brand_blocks.get(None, frozenset())
        return jurisdiction in default_blocks
