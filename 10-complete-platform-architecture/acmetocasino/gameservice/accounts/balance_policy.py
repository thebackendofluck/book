# Companion code for "The Backend of Luck" - Chapter 10, Complete Platform Architecture.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
gameservice.accounts.balance_policy — BalancePolicy
=====================================================

Determines how a player's wallet funds are consumed and how wagers
contribute to bonus wagering requirements.

Fund usage order
----------------
Most platforms consume cash first, then bonus (``CASH_FIRST`` policy).
Some promotional mechanics require bonus first (``BONUS_FIRST``).  A few
allow split usage where both sources contribute proportionally.

Wagering contributions
-----------------------
Not all wagers count equally toward a bonus wagering requirement.  The
contribution rate is typically:

* **Slots**: 100% — every £1 staked counts £1 toward the requirement.
* **Live casino / table games**: 10–20% — only a fraction counts.
* **Scratch cards / virtual sports**: 0–50% — varies widely.
* **Sports betting**: Often excluded entirely.

Contribution rates are configured per supplier and per product type so
operators can tune them without code changes.

Example::

    policy = BalancePolicy()
    sources = policy.determine_usage_order("player-1", Decimal("10.00"))
    # → [FundSource.CASH, FundSource.BONUS]

    contribution = policy.apply_wagering_contribution(
        supplier_id="netent",
        game_type="slots",
        wager_amount=Decimal("5.00"),
    )
    # → Decimal("5.00")  (100% contribution)

    contribution = policy.apply_wagering_contribution(
        supplier_id="evolution",
        game_type="live_casino",
        wager_amount=Decimal("5.00"),
    )
    # → Decimal("0.50")  (10% contribution)
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum, unique

from acmetocasino.gameservice.models.enums import FundSource


# ---------------------------------------------------------------------------
# Policy mode
# ---------------------------------------------------------------------------


@unique
class UsageOrderPolicy(str, Enum):
    """The strategy for determining which fund source is consumed first.

    ``CASH_FIRST``
        Draw from cash balance before touching bonus funds.  The most
        common policy; ensures players must exhaust real money first.
    ``BONUS_FIRST``
        Draw from bonus balance before cash.  Used for promotional mechanics
        where the operator wants to accelerate wagering completion.
    ``PROPORTIONAL``
        Split the wager proportionally across cash and bonus based on the
        ratio of available balances at the time of the bet.
    """

    CASH_FIRST = "cash_first"
    BONUS_FIRST = "bonus_first"
    PROPORTIONAL = "proportional"


# ---------------------------------------------------------------------------
# Contribution rates registry
# ---------------------------------------------------------------------------

# Default contribution rates by (supplier_id OR "*", game_type OR "*").
# More specific entries override less specific ones.
# Key: (supplier_id, game_type) — use "*" as a wildcard.
_DEFAULT_CONTRIBUTION_RATES: dict[tuple[str, str], Decimal] = {
    # Wildcard defaults
    ("*", "slots"): Decimal("1.00"),           # 100%
    ("*", "live_casino"): Decimal("0.10"),     # 10%
    ("*", "table_games"): Decimal("0.10"),     # 10%
    ("*", "sportsbook"): Decimal("0.00"),      # 0% — excluded
    ("*", "virtual_sports"): Decimal("0.50"),  # 50%
    ("*", "scratch_cards"): Decimal("0.00"),   # 0% — excluded
    ("*", "poker"): Decimal("0.10"),           # 10%
    ("*", "bingo"): Decimal("0.50"),           # 50%
    ("*", "*"): Decimal("1.00"),               # Fallback for unknown types
}


# ---------------------------------------------------------------------------
# BalancePolicy
# ---------------------------------------------------------------------------


class BalancePolicy:
    """Determines fund usage order and wagering contribution rates.

    Parameters
    ----------
    usage_order:
        The global default policy for fund usage ordering.
    player_overrides:
        Per-player policy overrides (e.g. for VIP promotional mechanics).
        Maps ``player_id`` → :class:`UsageOrderPolicy`.
    contribution_rates:
        Override table for wagering contribution rates.  Merged with the
        built-in defaults — entries in this dict take precedence.
        Key: ``(supplier_id, game_type)``; value: contribution rate (0–1).
    """

    def __init__(
        self,
        usage_order: UsageOrderPolicy = UsageOrderPolicy.CASH_FIRST,
        player_overrides: dict[str, UsageOrderPolicy] | None = None,
        contribution_rates: dict[tuple[str, str], Decimal] | None = None,
    ) -> None:
        self._default_policy = usage_order
        self._player_overrides: dict[str, UsageOrderPolicy] = player_overrides or {}
        # Merge caller-supplied rates over the built-in defaults.
        self._rates = dict(_DEFAULT_CONTRIBUTION_RATES)
        if contribution_rates:
            self._rates.update(contribution_rates)

    # ------------------------------------------------------------------
    # Fund usage order
    # ------------------------------------------------------------------

    def determine_usage_order(
        self,
        player_id: str,
        amount: Decimal,
    ) -> list[FundSource]:
        """Return the ordered list of fund sources to draw from.

        The first item in the returned list is the *primary* source; the
        platform exhausts it before moving to subsequent sources.

        Parameters
        ----------
        player_id:
            The player placing the bet.
        amount:
            The wager amount (included for future proportional-split logic).

        Returns
        -------
        list[FundSource]
            Ordered fund sources.

        Examples
        --------
        ``CASH_FIRST``  → ``[CASH, BONUS]``
        ``BONUS_FIRST`` → ``[BONUS, CASH]``
        ``PROPORTIONAL`` → ``[CASH, BONUS]`` (order is advisory;
            caller must split the amount by balance ratio)
        """
        policy = self._player_overrides.get(player_id, self._default_policy)

        if policy == UsageOrderPolicy.CASH_FIRST:
            return [FundSource.CASH, FundSource.BONUS]
        elif policy == UsageOrderPolicy.BONUS_FIRST:
            return [FundSource.BONUS, FundSource.CASH]
        else:  # PROPORTIONAL — return both, caller handles the split
            return [FundSource.CASH, FundSource.BONUS]

    def set_player_policy(
        self,
        player_id: str,
        policy: UsageOrderPolicy,
    ) -> None:
        """Override the usage-order policy for a specific player.

        Parameters
        ----------
        player_id:
            Target player.
        policy:
            The policy to apply for this player's transactions.
        """
        self._player_overrides[player_id] = policy

    # ------------------------------------------------------------------
    # Wagering contribution
    # ------------------------------------------------------------------

    def apply_wagering_contribution(
        self,
        supplier_id: str,
        game_type: str,
        wager_amount: Decimal,
    ) -> Decimal:
        """Calculate the qualifying contribution of a wager toward the bonus
        wagering requirement.

        Parameters
        ----------
        supplier_id:
            The supplier integration ID (e.g. ``"netent"``, ``"evolution"``).
            Used to look up supplier-specific rates; falls back to the global
            wildcard for ``game_type`` if no supplier-specific rate is found.
        game_type:
            The game category string (e.g. ``"slots"``, ``"live_casino"``).
        wager_amount:
            The gross wager amount before contribution calculation.

        Returns
        -------
        Decimal
            The qualifying contribution amount.  This is ``wager_amount``
            multiplied by the applicable rate (0–1).

        Examples
        --------
        ::

            policy.apply_wagering_contribution("netent", "slots", Decimal("10"))
            # → Decimal("10.00")  (100% contribution)

            policy.apply_wagering_contribution("evolution", "live_casino", Decimal("10"))
            # → Decimal("1.00")   (10% contribution)
        """
        rate = self._lookup_rate(supplier_id, game_type)
        return (wager_amount * rate).quantize(Decimal("0.01"))

    def set_contribution_rate(
        self,
        game_type: str,
        rate: Decimal,
        supplier_id: str = "*",
    ) -> None:
        """Register or override a wagering contribution rate.

        Parameters
        ----------
        game_type:
            The game category (e.g. ``"slots"``).  Use ``"*"`` for a
            catch-all.
        rate:
            Contribution rate in the range [0, 1].  ``Decimal("1.00")``
            means full contribution; ``Decimal("0.00")`` means excluded.
        supplier_id:
            The supplier to scope this rate to, or ``"*"`` for all suppliers.

        Raises
        ------
        ValueError
            If *rate* is outside [0, 1].
        """
        if not (Decimal("0") <= rate <= Decimal("1")):
            raise ValueError(f"Contribution rate must be in [0, 1], got {rate!r}")
        self._rates[(supplier_id, game_type)] = rate

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _lookup_rate(self, supplier_id: str, game_type: str) -> Decimal:
        """Find the most specific applicable contribution rate.

        Lookup order (most → least specific):
        1. (supplier_id, game_type)
        2. ("*", game_type)
        3. (supplier_id, "*")
        4. ("*", "*")
        """
        for key in [
            (supplier_id, game_type),
            ("*", game_type),
            (supplier_id, "*"),
            ("*", "*"),
        ]:
            rate = self._rates.get(key)
            if rate is not None:
                return rate
        # Should never reach here due to the ("*", "*") fallback.
        return Decimal("1.00")


__all__ = ["BalancePolicy", "UsageOrderPolicy"]
