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
gameservice.suppliers.capability_matrix — Supplier Capability Index
====================================================================

The :class:`CapabilityMatrix` is the authoritative source for what each
integrated supplier can do.  It is consulted at runtime to:

* Filter suppliers for bonus campaign eligibility (e.g. free-round capable).
* Determine which suppliers to include in a given product landing page.
* Gate features in the platform API (e.g. only show cash-out button if the
  supplier supports it).
* Generate operational reports for the operator dashboard.

The matrix is hardcoded in this module based on real-world supplier
specifications.  In a production system the matrix would be editable via the
operator back-office, but a hardcoded baseline ensures correctness at startup
before any back-office overrides are applied.

Capability definitions match :class:`~acmetocasino.gameservice.suppliers.base.SupplierCapabilities`.
"""

from __future__ import annotations

from acmetocasino.gameservice.models.enums import CallbackStyle, ProductType
from acmetocasino.gameservice.suppliers.base import SupplierCapabilities

# ---------------------------------------------------------------------------
# Hardcoded capability declarations for all 11 integrated suppliers
# ---------------------------------------------------------------------------

_MATRIX: dict[str, SupplierCapabilities] = {
    "evolution": SupplierCapabilities(
        supplier_id="evolution",
        product_types=(ProductType.LIVE_CASINO,),
        callback_style=CallbackStyle.PUSH,
        free_rounds=False,
        jackpots=False,
        tournaments=False,
        live_betting=False,
        cash_out=False,
        tipping=True,       # Evolution supports live-dealer tips
        multi_seat=True,    # Multiple players at the same table
        bonus_buy=False,
        progressive_pools=False,
        regulatory_reporting=True,
        demo_available=False,  # Live casino cannot offer demo play
    ),
    "pragmatic": SupplierCapabilities(
        supplier_id="pragmatic",
        product_types=(ProductType.SLOTS, ProductType.LIVE_CASINO),
        callback_style=CallbackStyle.SEAMLESS,
        free_rounds=True,   # Drops & Wins, standard free spins
        jackpots=True,       # Pragmatic jackpot network
        tournaments=True,   # Drops & Wins tournament engine
        live_betting=False,
        cash_out=False,
        tipping=False,
        multi_seat=False,
        bonus_buy=True,     # Bonus Buy feature on qualifying slots
        progressive_pools=True,
        regulatory_reporting=True,
        demo_available=True,
    ),
    "netent": SupplierCapabilities(
        supplier_id="netent",
        product_types=(ProductType.SLOTS,),
        callback_style=CallbackStyle.SEAMLESS,
        free_rounds=True,   # NetEnt Free Spins standard
        jackpots=False,
        tournaments=False,
        live_betting=False,
        cash_out=False,
        tipping=False,
        multi_seat=False,
        bonus_buy=False,
        progressive_pools=False,
        regulatory_reporting=True,
        demo_available=True,
    ),
    "kambi": SupplierCapabilities(
        supplier_id="kambi",
        product_types=(ProductType.SPORTSBOOK,),
        callback_style=CallbackStyle.PULL,
        free_rounds=False,
        jackpots=False,
        tournaments=False,
        live_betting=True,  # In-play betting
        cash_out=True,      # Cash-out on open bets
        tipping=False,
        multi_seat=False,
        bonus_buy=False,
        progressive_pools=False,
        regulatory_reporting=True,
        demo_available=False,
    ),
    "playngo": SupplierCapabilities(
        supplier_id="playngo",
        product_types=(ProductType.SLOTS,),
        callback_style=CallbackStyle.SEAMLESS,
        free_rounds=True,
        jackpots=True,      # Play'n GO jackpot games
        tournaments=False,
        live_betting=False,
        cash_out=False,
        tipping=False,
        multi_seat=False,
        bonus_buy=False,
        progressive_pools=False,
        regulatory_reporting=True,
        demo_available=True,
    ),
    "hacksaw": SupplierCapabilities(
        supplier_id="hacksaw",
        product_types=(ProductType.SLOTS, ProductType.SCRATCH_CARDS),
        callback_style=CallbackStyle.SEAMLESS,
        free_rounds=False,
        jackpots=False,
        tournaments=True,   # Hacksaw operator tournaments
        live_betting=False,
        cash_out=False,
        tipping=False,
        multi_seat=False,
        bonus_buy=True,     # Hacksaw supports bonus buy on select titles
        progressive_pools=False,
        regulatory_reporting=False,
        demo_available=True,
    ),
    "push_gaming": SupplierCapabilities(
        supplier_id="push_gaming",
        product_types=(ProductType.SLOTS,),
        callback_style=CallbackStyle.SEAMLESS,
        free_rounds=True,
        jackpots=True,
        tournaments=False,
        live_betting=False,
        cash_out=False,
        tipping=False,
        multi_seat=False,
        bonus_buy=False,
        progressive_pools=False,
        regulatory_reporting=False,
        demo_available=True,
    ),
    "igt": SupplierCapabilities(
        supplier_id="igt",
        product_types=(ProductType.SLOTS, ProductType.TABLE_GAMES),
        callback_style=CallbackStyle.PULL,
        free_rounds=False,
        jackpots=True,
        tournaments=False,
        live_betting=False,
        cash_out=False,
        tipping=False,
        multi_seat=False,
        bonus_buy=False,
        progressive_pools=True,  # IGT Megajackpots network
        regulatory_reporting=True,
        demo_available=True,
    ),
    "nyx": SupplierCapabilities(
        supplier_id="nyx",
        product_types=(ProductType.SLOTS,),
        callback_style=CallbackStyle.SEAMLESS,
        free_rounds=True,
        jackpots=False,
        tournaments=False,
        live_betting=False,
        cash_out=False,
        tipping=False,
        multi_seat=False,
        bonus_buy=False,
        progressive_pools=False,
        regulatory_reporting=False,
        demo_available=True,
    ),
    "relax": SupplierCapabilities(
        supplier_id="relax",
        product_types=(ProductType.SLOTS,),
        callback_style=CallbackStyle.SEAMLESS,
        free_rounds=True,   # Silver Bullet free-rounds distribution
        jackpots=False,
        tournaments=True,   # Relax partner studio tournaments
        live_betting=False,
        cash_out=False,
        tipping=False,
        multi_seat=False,
        bonus_buy=False,
        progressive_pools=False,
        regulatory_reporting=False,
        demo_available=True,
    ),
    "betgenius": SupplierCapabilities(
        supplier_id="betgenius",
        product_types=(ProductType.SPORTSBOOK,),
        callback_style=CallbackStyle.PUSH,
        free_rounds=False,
        jackpots=False,
        tournaments=False,
        live_betting=True,  # Real-time fixture and odds data
        cash_out=False,
        tipping=False,
        multi_seat=False,
        bonus_buy=False,
        progressive_pools=False,
        regulatory_reporting=False,
        demo_available=False,
    ),
}


class CapabilityMatrix:
    """Query interface for supplier capabilities.

    The matrix is immutable after initialisation.  All query methods are
    O(1) or O(n) on the number of suppliers (11) and are safe to call
    from multiple threads without synchronisation.

    Usage::

        matrix = CapabilityMatrix()
        if matrix.supports("pragmatic", "free_rounds"):
            award_free_rounds(player, "pragmatic")

        slots_suppliers = matrix.filter_by_capability("free_rounds")
        # → ["pragmatic", "netent", "playngo", "push_gaming", "nyx", "relax"]
    """

    def __init__(
        self,
        custom_matrix: dict[str, SupplierCapabilities] | None = None,
    ) -> None:
        """Initialise with the default hardcoded matrix or a custom override.

        Parameters
        ----------
        custom_matrix:
            Optional dict mapping supplier_id → SupplierCapabilities.
            If provided, entirely replaces the built-in matrix.  This is
            used in tests and for out-of-tree supplier plugins.
        """
        self._matrix: dict[str, SupplierCapabilities] = (
            custom_matrix if custom_matrix is not None else dict(_MATRIX)
        )

    def supports(self, supplier_id: str, capability: str) -> bool:
        """Return ``True`` if the supplier declares the named capability.

        Parameters
        ----------
        supplier_id:
            The supplier to query.
        capability:
            Attribute name on :class:`SupplierCapabilities`
            (e.g. ``"free_rounds"``, ``"jackpots"``, ``"live_betting"``).

        Returns
        -------
        bool
            ``False`` if the supplier is unknown or the capability is not
            declared / not a boolean attribute.

        Examples
        --------
        ::

            matrix.supports("evolution", "tipping")   # True
            matrix.supports("netent", "jackpots")      # False
            matrix.supports("unknown", "free_rounds")  # False
        """
        caps = self._matrix.get(supplier_id)
        if caps is None:
            return False
        value = getattr(caps, capability, None)
        # Only boolean flags count as "supports"
        return bool(value) if isinstance(value, bool) else False

    def get_capabilities(self, supplier_id: str) -> SupplierCapabilities:
        """Return the full capability declaration for a supplier.

        Parameters
        ----------
        supplier_id:
            The supplier to query.

        Raises
        ------
        KeyError
            If the supplier is not in the matrix.
        """
        try:
            return self._matrix[supplier_id]
        except KeyError:
            raise KeyError(
                f"Supplier {supplier_id!r} not found in capability matrix"
            ) from None

    def filter_by_capability(self, capability: str) -> list[str]:
        """Return supplier IDs that declare a given capability as ``True``.

        Parameters
        ----------
        capability:
            Boolean attribute name on :class:`SupplierCapabilities`.

        Returns
        -------
        list[str]
            Sorted list of supplier IDs.

        Examples
        --------
        ::

            matrix.filter_by_capability("jackpots")
            # → ["igt", "playngo", "pragmatic", "push_gaming"]
        """
        result = [
            sid
            for sid, caps in self._matrix.items()
            if bool(getattr(caps, capability, False))
        ]
        return sorted(result)

    def filter_by_product(self, product_type: ProductType) -> list[str]:
        """Return supplier IDs that offer the given product type.

        Parameters
        ----------
        product_type:
            The :class:`~acmetocasino.gameservice.models.enums.ProductType`
            to filter by.

        Returns
        -------
        list[str]
            Sorted list of supplier IDs.
        """
        result = [
            sid
            for sid, caps in self._matrix.items()
            if product_type in caps.product_types
        ]
        return sorted(result)

    def get_matrix_report(self) -> dict[str, SupplierCapabilities]:
        """Return a snapshot of the full capability matrix.

        Useful for generating operator dashboards and integration docs.

        Returns
        -------
        dict[str, SupplierCapabilities]
            Shallow copy of the matrix keyed by supplier_id.
        """
        return dict(self._matrix)

    def all_supplier_ids(self) -> list[str]:
        """Return a sorted list of all registered supplier IDs."""
        return sorted(self._matrix.keys())


__all__ = ["CapabilityMatrix"]
