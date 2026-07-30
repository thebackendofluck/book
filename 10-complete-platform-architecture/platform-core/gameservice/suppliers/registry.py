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
suppliers/registry.py
---------------------
Supplier registry for the Game Aggregation Layer.

The registry maps supplier identifiers to their AccountsProvider
implementations. At startup, each supplier module registers itself.
The bridge uses `resolve_provider()` to get the correct provider for
a given supplier ID.

Supplier types
--------------
CASINO    — RNG slots, table games, live dealer. Seamless-wallet integration:
            the supplier calls back into the GAL for every debit/credit.
SPORTS    — Sportsbook feed. Fund/withdraw model: the sportsbook holds
            the coupon balance and calls the GAL for settlement.
AGGREGATOR — A content aggregator (like Relax or NYX) that wraps multiple
             game studios. The GAL integrates with the aggregator, not each
             individual studio.

Per-brand availability
----------------------
Not every supplier is available on every brand. The registry stores a
set of brand IDs for each supplier. An empty set means the supplier is
available everywhere.

Configuration is loaded from environment variables at startup and can
be overridden at test time via `register()`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from accounts_provider import AccountsProvider

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Supplier type classification
# ---------------------------------------------------------------------------


class SupplierType(str, Enum):
    """Broad category of supplier integration."""

    CASINO = "CASINO"          # Live dealer, RNG slots, table games
    SPORTS_BOOK = "SPORTS_BOOK"  # Fixed-odds and in-play sports betting
    AGGREGATOR = "AGGREGATOR"  # Content aggregator wrapping multiple studios
    CRASH = "CRASH"            # Crash / instant-win games
    VIRTUAL = "VIRTUAL"        # Virtual sports


# ---------------------------------------------------------------------------
# Supplier descriptor
# ---------------------------------------------------------------------------


@dataclass
class SupplierDescriptor:
    """
    Metadata about a registered supplier.

    Fields
    ------
    supplier_id:      Short slug used in callbacks, e.g. "evolution".
    display_name:     Human-readable name shown in BO dashboards.
    supplier_type:    Broad category of the integration.
    provider:         The AccountsProvider implementation.
    brand_ids:        Brands this supplier is available on. Empty = all brands.
    jurisdiction_ids: Jurisdictions enabled for this supplier. Empty = all.
    seamless_wallet:  True when the supplier calls back into the GAL for
                      every transaction (the most common modern pattern).
    """

    supplier_id: str
    display_name: str
    supplier_type: SupplierType
    provider: AccountsProvider
    brand_ids: set[str] = field(default_factory=set)
    jurisdiction_ids: set[str] = field(default_factory=set)
    seamless_wallet: bool = True

    def is_available_for_brand(self, brand_id: str) -> bool:
        """Return True if this supplier serves the given brand."""
        return not self.brand_ids or brand_id in self.brand_ids

    def is_available_for_jurisdiction(self, jurisdiction: str) -> bool:
        """Return True if this supplier operates in the given jurisdiction."""
        return not self.jurisdiction_ids or jurisdiction in self.jurisdiction_ids


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class SupplierRegistry:
    """
    Thread-safe registry of all active supplier integrations.

    Usage::

        registry = SupplierRegistry()
        registry.register(SupplierDescriptor(
            supplier_id="evolution",
            display_name="Evolution Gaming",
            supplier_type=SupplierType.CASINO,
            provider=EvolutionProvider(...),
            seamless_wallet=True,
        ))
        provider = registry.resolve_provider("evolution")
    """

    def __init__(self) -> None:
        self._suppliers: dict[str, SupplierDescriptor] = {}

    def register(self, descriptor: SupplierDescriptor) -> None:
        """
        Register a supplier.

        Registering a supplier with an existing ID replaces the previous
        entry. This allows test code to swap implementations.
        """
        self._suppliers[descriptor.supplier_id] = descriptor
        logger.info(
            "Registered supplier: %s (%s) type=%s seamless=%s",
            descriptor.supplier_id,
            descriptor.display_name,
            descriptor.supplier_type.value,
            descriptor.seamless_wallet,
        )

    def deregister(self, supplier_id: str) -> None:
        """Remove a supplier from the registry (used in tests)."""
        self._suppliers.pop(supplier_id, None)

    def resolve_provider(
        self,
        supplier_id: str,
        brand_id: Optional[str] = None,
        jurisdiction: Optional[str] = None,
    ) -> AccountsProvider:
        """
        Return the AccountsProvider for a supplier.

        Args:
            supplier_id:  Slug identifying the supplier.
            brand_id:     Optional brand to check availability for.
            jurisdiction: Optional jurisdiction to check availability for.

        Raises:
            KeyError:  Supplier not registered.
            ValueError: Supplier not available for brand / jurisdiction.
        """
        descriptor = self._suppliers.get(supplier_id)
        if descriptor is None:
            raise KeyError(f"Unknown supplier: {supplier_id!r}")

        if brand_id and not descriptor.is_available_for_brand(brand_id):
            raise ValueError(
                f"Supplier {supplier_id!r} is not available for brand {brand_id!r}"
            )

        if jurisdiction and not descriptor.is_available_for_jurisdiction(jurisdiction):
            raise ValueError(
                f"Supplier {supplier_id!r} is not available in jurisdiction {jurisdiction!r}"
            )

        return descriptor.provider

    def get_descriptor(self, supplier_id: str) -> Optional[SupplierDescriptor]:
        """Return the full descriptor or None if not registered."""
        return self._suppliers.get(supplier_id)

    def list_suppliers(
        self,
        brand_id: Optional[str] = None,
        supplier_type: Optional[SupplierType] = None,
    ) -> list[SupplierDescriptor]:
        """
        List all registered suppliers, optionally filtered.

        Args:
            brand_id:      Only return suppliers available for this brand.
            supplier_type: Only return suppliers of this type.
        """
        results = list(self._suppliers.values())
        if brand_id:
            results = [s for s in results if s.is_available_for_brand(brand_id)]
        if supplier_type:
            results = [s for s in results if s.supplier_type == supplier_type]
        return results

    def __len__(self) -> int:
        return len(self._suppliers)

    def __contains__(self, supplier_id: str) -> bool:
        return supplier_id in self._suppliers


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

# The global registry — imported by main.py and supplier modules.
registry = SupplierRegistry()


def get_provider(supplier_id: str) -> AccountsProvider:
    """
    Convenience function for use as a bridge provider_factory.

    Usage::

        bridge = AccountsBridge(
            provider_factory=get_provider,
            ...
        )
    """
    return registry.resolve_provider(supplier_id)
