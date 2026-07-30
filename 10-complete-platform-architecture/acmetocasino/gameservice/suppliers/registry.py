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
gameservice.suppliers.registry — Supplier Adapter Registry
===========================================================

The :class:`SupplierRegistry` is the single point through which the platform
resolves a live adapter instance for any supplier + brand + jurisdiction
combination at request time.

Design goals
------------
* **Thread-safe**: Multiple request threads call :meth:`resolve` concurrently.
  The registry uses a :class:`threading.RLock` for safe mutation and a
  per-supplier lock for lazy adapter initialisation.
* **Lazy initialisation**: Adapters are constructed on first use, not at
  registration time.  This keeps startup fast and allows credential injection
  after the registry is built.
* **Brand and jurisdiction filtering**: The registry delegates eligibility
  checks to the adapter factory's declared :class:`SupplierConfig` rather than
  storing redundant state.
* **Pluggable**: New suppliers are added by calling :meth:`register` with a
  factory callable.  No changes to the registry class itself are needed.

Usage::

    from acmetocasino.gameservice.suppliers.registry import SupplierRegistry
    from acmetocasino.gameservice.suppliers.pragmatic import PragmaticAdapter

    registry = SupplierRegistry(settings_manager=mgr, capability_matrix=matrix)
    registry.register("pragmatic", PragmaticAdapter)

    adapter = registry.resolve("pragmatic", brand_id="acme_uk", jurisdiction="MGA")
    result = adapter.launch_session(request)
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from acmetocasino.gameservice.errors import GameServiceError
from acmetocasino.gameservice.models.enums import ProductType
from acmetocasino.gameservice.suppliers.base import (
    BaseSupplierAdapter,
    SupplierInfo,
)
from acmetocasino.gameservice.suppliers.capability_matrix import CapabilityMatrix
from acmetocasino.gameservice.suppliers.settings import SupplierSettingsManager


# A factory is any callable that accepts a single config argument and returns
# a BaseSupplierAdapter.
AdapterFactory = Callable[..., BaseSupplierAdapter]


class SupplierNotFoundError(GameServiceError):
    """Raised when the registry has no adapter registered for the requested supplier."""

    http_status = 404  # type: ignore[assignment]


class SupplierDisabledError(GameServiceError):
    """Raised when the supplier exists but is disabled for the given brand/jurisdiction."""

    http_status = 403  # type: ignore[assignment]


class SupplierRegistry:
    """Thread-safe registry that resolves supplier adapters at request time.

    Parameters
    ----------
    settings_manager:
        Provides per-supplier and per-brand configuration.  If ``None``, a
        default empty manager is created; configs must be loaded before any
        resolution attempt.
    capability_matrix:
        Provides capability declarations for all suppliers.  If ``None``, the
        default matrix (all 11 built-in suppliers) is used.

    Thread safety
    -------------
    * :meth:`register` is protected by a write lock on ``_registry``.
    * :meth:`resolve` double-checks the cache under a per-supplier lock to
      ensure exactly one adapter instance is created per (supplier, brand)
      pair even under concurrent load.
    """

    def __init__(
        self,
        settings_manager: SupplierSettingsManager | None = None,
        capability_matrix: CapabilityMatrix | None = None,
    ) -> None:
        self._settings: SupplierSettingsManager = (
            settings_manager or SupplierSettingsManager()
        )
        self._matrix: CapabilityMatrix = capability_matrix or CapabilityMatrix()

        # Maps supplier_id → factory callable
        self._factories: dict[str, AdapterFactory] = {}
        # Cache of already-constructed adapters, keyed by (supplier_id, brand_id)
        self._adapter_cache: dict[tuple[str, str], BaseSupplierAdapter] = {}
        # Per-supplier init locks to prevent thundering-herd on first resolution
        self._init_locks: dict[str, threading.Lock] = {}

        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        supplier_id: str,
        adapter_factory: AdapterFactory,
    ) -> None:
        """Register an adapter factory for a supplier.

        A factory is any callable that accepts a single positional argument
        (the :class:`~acmetocasino.gameservice.suppliers.settings.SupplierConfig`)
        and returns a :class:`~acmetocasino.gameservice.suppliers.base.BaseSupplierAdapter`.

        Parameters
        ----------
        supplier_id:
            Unique supplier key (must match the directory name, e.g.
            ``"pragmatic"``).
        adapter_factory:
            Callable that constructs the adapter.  Typically the adapter class
            itself: ``registry.register("pragmatic", PragmaticAdapter)``.

        Notes
        -----
        Calling :meth:`register` a second time for the same ``supplier_id``
        silently replaces the factory and invalidates the cached adapter
        instances for that supplier.
        """
        with self._lock:
            self._factories[supplier_id] = adapter_factory
            self._init_locks[supplier_id] = threading.Lock()
            # Invalidate any cached adapters for this supplier
            stale = [k for k in self._adapter_cache if k[0] == supplier_id]
            for key in stale:
                del self._adapter_cache[key]

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def resolve(
        self,
        supplier_id: str,
        brand_id: str,
        jurisdiction: str,
    ) -> BaseSupplierAdapter:
        """Return the adapter for the given supplier + brand + jurisdiction.

        The result is cached per ``(supplier_id, brand_id)`` pair.  The
        ``jurisdiction`` argument is used only for eligibility checks, not
        for cache keying (adapters are brand-scoped, not jurisdiction-scoped).

        Parameters
        ----------
        supplier_id:
            The supplier to resolve.
        brand_id:
            The brand initiating the request.
        jurisdiction:
            The player's regulatory jurisdiction code.

        Returns
        -------
        BaseSupplierAdapter
            A fully initialised adapter, ready to handle ``launch_session``,
            ``debit``, ``credit``, etc.

        Raises
        ------
        SupplierNotFoundError
            If no factory has been registered for ``supplier_id``.
        SupplierDisabledError
            If the supplier exists but is disabled for the given brand or
            jurisdiction.
        """
        with self._lock:
            factory = self._factories.get(supplier_id)
            init_lock = self._init_locks.get(supplier_id)

        if factory is None:
            raise SupplierNotFoundError(
                message=f"No adapter registered for supplier {supplier_id!r}",
            )

        if not self.is_enabled(supplier_id, brand_id, jurisdiction):
            raise SupplierDisabledError(
                message=(
                    f"Supplier {supplier_id!r} is disabled for "
                    f"brand={brand_id!r}, jurisdiction={jurisdiction!r}"
                ),
            )

        cache_key = (supplier_id, brand_id)

        # Fast path — already initialised
        with self._lock:
            adapter = self._adapter_cache.get(cache_key)
        if adapter is not None:
            return adapter

        # Slow path — construct under per-supplier lock to prevent duplicate init
        assert init_lock is not None
        with init_lock:
            # Double-check after acquiring the per-supplier lock
            with self._lock:
                adapter = self._adapter_cache.get(cache_key)
            if adapter is not None:
                return adapter

            config = self._settings.get_config_for_brand(supplier_id, brand_id)
            adapter = factory(config)

            with self._lock:
                self._adapter_cache[cache_key] = adapter

        return adapter

    # ------------------------------------------------------------------
    # Eligibility
    # ------------------------------------------------------------------

    def is_enabled(
        self,
        supplier_id: str,
        brand_id: str,
        jurisdiction: str,
    ) -> bool:
        """Return ``True`` if the supplier is available for the given context.

        Checks performed (in order):
        1. Supplier has a registered factory.
        2. Supplier has a config registered in the settings manager.
        3. The config's ``enabled_brands`` allows ``brand_id``.
        4. The config's ``blocked_jurisdictions`` does not include ``jurisdiction``.

        Parameters
        ----------
        supplier_id:
            The supplier to check.
        brand_id:
            The brand.
        jurisdiction:
            The player's jurisdiction.

        Returns
        -------
        bool
        """
        with self._lock:
            has_factory = supplier_id in self._factories

        if not has_factory:
            return False

        try:
            config = self._settings.get_config_for_brand(supplier_id, brand_id)
        except KeyError:
            # No config means disabled
            return False

        if not config.is_brand_enabled(brand_id):
            return False

        if config.is_jurisdiction_blocked(jurisdiction):
            return False

        return True

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def list_available(
        self,
        brand_id: str,
        jurisdiction: str,
        product_type: ProductType | None = None,
    ) -> list[SupplierInfo]:
        """Return info for all suppliers that are available (and enabled) for the given context.

        Parameters
        ----------
        brand_id:
            The brand to list suppliers for.
        jurisdiction:
            The player's jurisdiction.
        product_type:
            If provided, only suppliers offering this product type are included.

        Returns
        -------
        list[SupplierInfo]
            Sorted by supplier_id.
        """
        with self._lock:
            supplier_ids = list(self._factories.keys())

        results: list[SupplierInfo] = []

        for sid in sorted(supplier_ids):
            try:
                caps = self._matrix.get_capabilities(sid)
            except KeyError:
                continue  # Supplier in factory registry but not in matrix — skip

            if product_type is not None and product_type not in caps.product_types:
                continue

            enabled = self.is_enabled(sid, brand_id, jurisdiction)

            try:
                cfg = self._settings.get_config_for_brand(sid, brand_id)
                display_name = cfg.display_name
            except KeyError:
                display_name = sid

            results.append(
                SupplierInfo(
                    supplier_id=sid,
                    display_name=display_name,
                    capabilities=caps,
                    is_enabled=enabled,
                )
            )

        return results

    def registered_supplier_ids(self) -> list[str]:
        """Return sorted list of all registered supplier IDs."""
        with self._lock:
            return sorted(self._factories.keys())

    def get_settings_manager(self) -> SupplierSettingsManager:
        """Expose the settings manager for external configuration updates."""
        return self._settings

    def get_capability_matrix(self) -> CapabilityMatrix:
        """Expose the capability matrix for external queries."""
        return self._matrix

    # ------------------------------------------------------------------
    # Convenience: pass kwargs through to factory
    # ------------------------------------------------------------------

    def resolve_with_kwargs(
        self,
        supplier_id: str,
        brand_id: str,
        jurisdiction: str,
        **kwargs: Any,
    ) -> BaseSupplierAdapter:
        """Like :meth:`resolve` but forwards extra kwargs to the factory.

        Useful when the adapter constructor requires additional arguments
        beyond the config object (e.g. a metrics client or an event bus).

        Parameters
        ----------
        supplier_id:
            The supplier to resolve.
        brand_id:
            The brand.
        jurisdiction:
            The player's jurisdiction.
        **kwargs:
            Extra keyword arguments forwarded to the adapter factory.
        """
        with self._lock:
            factory = self._factories.get(supplier_id)

        if factory is None:
            raise SupplierNotFoundError(
                message=f"No adapter registered for supplier {supplier_id!r}",
            )

        if not self.is_enabled(supplier_id, brand_id, jurisdiction):
            raise SupplierDisabledError(
                message=(
                    f"Supplier {supplier_id!r} is disabled for "
                    f"brand={brand_id!r}, jurisdiction={jurisdiction!r}"
                ),
            )

        config = self._settings.get_config_for_brand(supplier_id, brand_id)
        return factory(config, **kwargs)


__all__ = [
    "AdapterFactory",
    "SupplierDisabledError",
    "SupplierNotFoundError",
    "SupplierRegistry",
]
