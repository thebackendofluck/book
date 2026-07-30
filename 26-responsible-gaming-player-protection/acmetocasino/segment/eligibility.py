# Companion code for "The Backend of Luck" - Chapter 26, Responsible Gaming and Player Protection Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
segment.eligibility — Player Eligibility Checks
================================================

``EligibilityChecker`` is a pure decision service: given a ``PlayerSegment``
and contextual parameters, it answers three core questions:

1. **Can this player launch this game in this jurisdiction?**
2. **Can this player receive this type of bonus?**
3. **Can this player access content from this supplier?**

Each question returns an :class:`EligibilityResult` with a boolean verdict,
an optional human-readable reason, and a list of restriction codes.  Restriction
codes are machine-readable strings (e.g. ``"SELF_EXCLUDED"``, ``"KYC_REQUIRED"``)
that the calling API layer can translate into localised messages.

Design notes
------------
The checker is **stateless** — it receives all required information as
parameters and produces a deterministic result.  This makes it trivially
testable without mocking.

Jurisdiction rules and supplier restrictions are provided as configuration at
construction time, defaulting to a conservative set of rules that mirror
common European regulatory requirements.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from acmetocasino.segment.player_segment import PlayerSegment, RiskCategory, SegmentTier


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EligibilityResult:
    """Outcome of an eligibility check.

    Attributes
    ----------
    eligible:
        ``True`` if the player is permitted to perform the requested action.
    reason:
        Human-readable explanation when ``eligible=False``; ``None`` otherwise.
    restrictions:
        Machine-readable restriction codes that explain the denial.  Empty when
        ``eligible=True``.
    """

    eligible: bool
    reason: str | None = None
    restrictions: list[str] = field(default_factory=list)

    @classmethod
    def allow(cls) -> EligibilityResult:
        """Factory: create an unrestricted eligible result."""
        return cls(eligible=True)

    @classmethod
    def deny(cls, reason: str, *restriction_codes: str) -> EligibilityResult:
        """Factory: create a denial result with reason and restriction codes."""
        return cls(
            eligible=False,
            reason=reason,
            restrictions=list(restriction_codes),
        )


# ---------------------------------------------------------------------------
# Default configuration constants
# ---------------------------------------------------------------------------

# Jurisdictions where demo/free play is not permitted for real accounts.
_NO_DEMO_JURISDICTIONS: frozenset[str] = frozenset({"DE", "SE", "DK"})

# Bonus types blocked for problem-gambling-risk players.
_BLOCKED_BONUS_TYPES_HIGH_RISK: frozenset[str] = frozenset(
    {"deposit_match", "free_spins", "reload", "cashback"}
)

# Tiers required to access certain suppliers (VIP-only content).
_VIP_ONLY_SUPPLIERS: frozenset[str] = frozenset({"evolution_vip", "pragmatic_vip_live"})

# Jurisdictions where a supplier has regulatory restrictions.
# Extends (but does not replace) the geo_policy layer; this is the
# segment-level view based on licensing.
_SUPPLIER_JURISDICTION_RESTRICTIONS: dict[str, frozenset[str]] = {
    "evolution_vip": frozenset({"DE"}),
    "pragmatic_vip_live": frozenset({"DE", "SE"}),
}


# ---------------------------------------------------------------------------
# EligibilityChecker
# ---------------------------------------------------------------------------


class EligibilityChecker:
    """Evaluates player eligibility for games, bonuses, and supplier content.

    Parameters
    ----------
    no_demo_jurisdictions:
        Set of jurisdiction codes where demo play is prohibited.
    vip_only_suppliers:
        Supplier IDs that are restricted to ``VIP``-tier players.
    supplier_jurisdiction_restrictions:
        Mapping of ``supplier_id → set[jurisdiction]`` for regulatory blocks.
    blocked_bonus_types_high_risk:
        Bonus types that may not be offered to ``PROBLEM_GAMBLING_RISK`` players.

    Examples
    --------
    >>> from decimal import Decimal
    >>> from datetime import date, datetime
    >>> checker = EligibilityChecker()
    >>> segment = PlayerSegment(
    ...     player_id="player-1",
    ...     signup_date=date.today(),
    ...     last_active=datetime.utcnow(),
    ... )
    >>> result = checker.can_play_game(segment, "book-of-dead", "MT")
    >>> result.eligible
    True
    """

    def __init__(
        self,
        *,
        no_demo_jurisdictions: set[str] | None = None,
        vip_only_suppliers: set[str] | None = None,
        supplier_jurisdiction_restrictions: dict[str, frozenset[str]] | None = None,
        blocked_bonus_types_high_risk: set[str] | None = None,
    ) -> None:
        self._no_demo = frozenset(
            no_demo_jurisdictions or _NO_DEMO_JURISDICTIONS
        )
        self._vip_only_suppliers = frozenset(
            vip_only_suppliers or _VIP_ONLY_SUPPLIERS
        )
        self._supplier_restrictions = (
            supplier_jurisdiction_restrictions or _SUPPLIER_JURISDICTION_RESTRICTIONS
        )
        self._blocked_bonus_high_risk = frozenset(
            blocked_bonus_types_high_risk or _BLOCKED_BONUS_TYPES_HIGH_RISK
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def can_play_game(
        self,
        player_segment: PlayerSegment,
        game_id: str,  # noqa: ARG002 — reserved for future game-level rules
        jurisdiction: str,
    ) -> EligibilityResult:
        """Determine whether *player_segment* may launch a game in *jurisdiction*.

        Parameters
        ----------
        player_segment:
            The player's current segmentation record.
        game_id:
            Identifier of the game being launched.  Reserved for future
            game-level restrictions (e.g. age-gated titles).
        jurisdiction:
            The jurisdiction in which the game would be played.

        Returns
        -------
        EligibilityResult
            Eligibility verdict with reasons and restriction codes.
        """
        restrictions: list[str] = []

        # Responsible gaming: self-exclusion is an absolute block.
        if player_segment.has_tag("self_excluded"):
            return EligibilityResult.deny(
                "Player is under a self-exclusion order",
                "SELF_EXCLUDED",
            )

        # Responsible gaming: problem gambling risk — block in certain jurisdictions.
        if (
            player_segment.is_high_risk()
            and jurisdiction.upper() in {"GB", "SE", "DE", "DK"}
        ):
            restrictions.append("PROBLEM_GAMBLING_RISK_RESTRICTED")

        # Responsible gaming: cooling-off / time-out tag.
        if player_segment.has_tag("cooling_off"):
            return EligibilityResult.deny(
                "Player is in a cooling-off period",
                "COOLING_OFF",
            )

        if restrictions:
            return EligibilityResult.deny(
                "Player has active responsible-gaming restrictions in this jurisdiction",
                *restrictions,
            )

        return EligibilityResult.allow()

    def can_receive_bonus(
        self,
        player_segment: PlayerSegment,
        bonus_type: str,
    ) -> EligibilityResult:
        """Determine whether *player_segment* is eligible for *bonus_type*.

        Parameters
        ----------
        player_segment:
            The player's current segmentation record.
        bonus_type:
            Identifier for the bonus category (e.g. ``"deposit_match"``,
            ``"free_spins"``, ``"cashback"``).

        Returns
        -------
        EligibilityResult
            Eligibility verdict.
        """
        # Self-excluded players may not receive any promotional offers.
        if player_segment.has_tag("self_excluded"):
            return EligibilityResult.deny(
                "Self-excluded players cannot receive bonus offers",
                "SELF_EXCLUDED",
            )

        # Bonus abuse flag (set by fraud system).
        if player_segment.has_tag("bonus_abuser"):
            return EligibilityResult.deny(
                "Player has been flagged for bonus abuse",
                "BONUS_ABUSE_FLAG",
            )

        # Problem gambling risk: block promotional bonus types.
        if (
            player_segment.risk_category == RiskCategory.PROBLEM_GAMBLING_RISK
            and bonus_type.lower() in self._blocked_bonus_high_risk
        ):
            return EligibilityResult.deny(
                f"Bonus type {bonus_type!r} is not available for players with "
                "responsible-gaming restrictions",
                "PROBLEM_GAMBLING_RISK_BONUS_BLOCK",
            )

        # Cooling-off players should not receive retention bonuses.
        if player_segment.has_tag("cooling_off"):
            return EligibilityResult.deny(
                "Players in a cooling-off period cannot receive bonuses",
                "COOLING_OFF",
            )

        return EligibilityResult.allow()

    def can_access_supplier(
        self,
        player_segment: PlayerSegment,
        supplier_id: str,
        jurisdiction: str | None = None,
    ) -> EligibilityResult:
        """Determine whether *player_segment* may access content from *supplier_id*.

        Parameters
        ----------
        player_segment:
            The player's current segmentation record.
        supplier_id:
            Supplier / RGS identifier.
        jurisdiction:
            Optional jurisdiction context for supplier licensing checks.

        Returns
        -------
        EligibilityResult
            Eligibility verdict.
        """
        # Self-excluded players cannot access any supplier.
        if player_segment.has_tag("self_excluded"):
            return EligibilityResult.deny(
                "Self-excluded players cannot access supplier content",
                "SELF_EXCLUDED",
            )

        # VIP-only suppliers require VIP tier.
        if (
            supplier_id in self._vip_only_suppliers
            and player_segment.tier != SegmentTier.VIP
        ):
            return EligibilityResult.deny(
                f"Supplier {supplier_id!r} is available to VIP-tier players only",
                "VIP_TIER_REQUIRED",
            )

        # Jurisdiction-level supplier restrictions.
        if jurisdiction:
            blocked_jurisdictions = self._supplier_restrictions.get(
                supplier_id, frozenset()
            )
            if jurisdiction.upper() in blocked_jurisdictions:
                return EligibilityResult.deny(
                    f"Supplier {supplier_id!r} is not licensed to operate in "
                    f"jurisdiction {jurisdiction.upper()!r}",
                    "SUPPLIER_JURISDICTION_BLOCK",
                )

        return EligibilityResult.allow()
