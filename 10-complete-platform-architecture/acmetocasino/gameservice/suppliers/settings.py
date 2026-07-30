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
gameservice.suppliers.settings — Supplier Configuration Management
===================================================================

Each supplier integration requires its own set of credentials, API endpoints,
and operational parameters.  This module provides:

* :class:`SupplierConfig` — a validated Pydantic model for a single supplier's
  configuration, suitable for loading from environment variables, secrets
  managers, or a configuration database.

* :class:`SupplierSettingsManager` — a thread-safe registry that stores and
  retrieves supplier configs, with support for per-brand overrides.

Configuration loading order
---------------------------
1. Base config loaded from the ``raw_config`` dict at startup.
2. Per-brand overrides applied on top (``brand_overrides`` key).
3. Secrets (``api_key``, ``secret_key``, etc.) injected from environment
   variables or a secrets manager at resolve time.

This design ensures that non-secret parameters can be stored in a config file
checked into version control while credentials remain in a secure vault.

Example::

    manager = SupplierSettingsManager()
    manager.load({
        "pragmatic": {
            "supplier_id": "pragmatic",
            "display_name": "Pragmatic Play",
            "api_base_url": "https://api.pragmaticplay.net",
            "api_key": "",          # injected from vault at runtime
            "supported_currencies": ["EUR", "GBP", "USD", "BRL"],
            "brand_overrides": {
                "acme_br": {
                    "supported_currencies": ["BRL"],
                    "callback_url_template": "https://br.acmetocasino.com/wallet/pragmatic",
                }
            }
        }
    })
    cfg = manager.get_config_for_brand("pragmatic", "acme_br")
"""

from __future__ import annotations

import copy
import threading
from typing import Any

from pydantic import BaseModel, Field


class SupplierConfig(BaseModel):
    """Validated configuration for a single supplier integration.

    Attributes
    ----------
    supplier_id:
        Unique key matching the supplier's directory name (e.g. ``"pragmatic"``).
    display_name:
        Human-readable supplier name for operator dashboards.
    api_base_url:
        Root URL of the supplier's API.  No trailing slash.
    api_key:
        Primary API credential.  Intentionally empty by default — must be
        injected from a secrets manager before any real traffic.
    enabled_brands:
        List of ``brand_id`` values this supplier is enabled for.
        ``["*"]`` means all brands.
    blocked_jurisdictions:
        Jurisdiction codes where this supplier must not be served.
        Takes precedence over ``enabled_brands``.
    supported_currencies:
        ISO-4217 currency codes the supplier accepts.
    timeout_seconds:
        HTTP request timeout for outbound calls to this supplier.
    max_retries:
        Number of times to retry a failed request before giving up.
    callback_url_template:
        URL template for the wallet callback endpoint this supplier should
        use.  Supports ``{brand_id}`` and ``{supplier_id}`` placeholders.
        E.g. ``"https://{brand_id}.acmetocasino.com/wallet/{supplier_id}"``.
    demo_mode_available:
        Whether this supplier supports play-money demo sessions.
    """

    model_config = {"frozen": False}

    supplier_id: str = Field(..., min_length=1)
    display_name: str = Field(..., min_length=1)
    api_base_url: str = Field(..., description="Root API URL, no trailing slash.")
    api_key: str = Field(
        default="",
        description="Primary API credential; load from secrets vault at runtime.",
    )
    enabled_brands: list[str] = Field(
        default_factory=lambda: ["*"],
        description='Brand IDs this supplier is enabled for. ["*"] = all brands.',
    )
    blocked_jurisdictions: list[str] = Field(
        default_factory=list,
        description="Jurisdiction codes where this supplier is blocked.",
    )
    supported_currencies: list[str] = Field(
        default_factory=list,
        description="ISO-4217 currency codes the supplier accepts.",
    )
    timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        description="HTTP timeout in seconds for outbound supplier calls.",
    )
    max_retries: int = Field(
        default=3,
        ge=0,
        description="Number of retry attempts for transient failures.",
    )
    callback_url_template: str = Field(
        default="",
        description="Template for wallet callback URL; supports {brand_id}, {supplier_id}.",
    )
    demo_mode_available: bool = Field(
        default=True,
        description="Whether this supplier supports demo/play-money sessions.",
    )

    def callback_url_for(self, brand_id: str) -> str:
        """Resolve the callback URL for a specific brand.

        Parameters
        ----------
        brand_id:
            The brand to generate the callback URL for.

        Returns
        -------
        str
            The resolved callback URL, or an empty string if no template was set.
        """
        if not self.callback_url_template:
            return ""
        return self.callback_url_template.format(
            brand_id=brand_id,
            supplier_id=self.supplier_id,
        )

    def is_currency_supported(self, currency: str) -> bool:
        """Return ``True`` if the supplier accepts the given currency code."""
        return currency.upper() in {c.upper() for c in self.supported_currencies}

    def is_jurisdiction_blocked(self, jurisdiction: str) -> bool:
        """Return ``True`` if this supplier is blocked in the given jurisdiction."""
        return jurisdiction.upper() in {j.upper() for j in self.blocked_jurisdictions}

    def is_brand_enabled(self, brand_id: str) -> bool:
        """Return ``True`` if this supplier is enabled for the given brand."""
        if "*" in self.enabled_brands:
            return True
        return brand_id in self.enabled_brands


class SupplierSettingsManager:
    """Thread-safe store for supplier configurations with per-brand override support.

    Lifecycle
    ---------
    Instantiate once at application startup, call :meth:`load` with the full
    configuration dict, then inject the manager into the
    :class:`~acmetocasino.gameservice.suppliers.registry.SupplierRegistry`.

    Per-brand overrides
    -------------------
    The raw config dict may contain a ``brand_overrides`` key whose value is a
    ``{brand_id: {field: value, ...}}`` mapping.  When
    :meth:`get_config_for_brand` is called, the base config is shallow-merged
    with the brand-specific override dict before being returned.

    Example
    -------
    ::

        manager = SupplierSettingsManager()
        manager.load({"pragmatic": {..., "brand_overrides": {"acme_br": {"api_key": "BR_KEY"}}}})
        cfg = manager.get_config_for_brand("pragmatic", "acme_br")
        assert cfg.api_key == "BR_KEY"
    """

    def __init__(self) -> None:
        self._configs: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def load(self, raw: dict[str, dict[str, Any]]) -> None:
        """Populate the manager from a raw configuration mapping.

        Parameters
        ----------
        raw:
            Mapping of ``supplier_id`` → raw config dict.  The dict must
            contain at minimum ``supplier_id``, ``display_name``,
            ``api_base_url``, and ``supported_currencies``.
            May contain ``brand_overrides`` which is stripped before
            constructing :class:`SupplierConfig`.
        """
        with self._lock:
            for supplier_id, cfg in raw.items():
                self._configs[supplier_id] = copy.deepcopy(cfg)

    def get_config(self, supplier_id: str) -> SupplierConfig:
        """Return the base (non-brand-specific) config for a supplier.

        Parameters
        ----------
        supplier_id:
            The supplier key.

        Raises
        ------
        KeyError
            If the supplier has not been registered.
        """
        with self._lock:
            raw = self._configs.get(supplier_id)
        if raw is None:
            raise KeyError(f"No configuration found for supplier {supplier_id!r}")
        return self._build_config(raw)

    def get_config_for_brand(self, supplier_id: str, brand_id: str) -> SupplierConfig:
        """Return the effective config for a supplier + brand combination.

        The base config is deep-merged with any ``brand_overrides[brand_id]``
        entry before being returned as an immutable :class:`SupplierConfig`.

        Parameters
        ----------
        supplier_id:
            The supplier key.
        brand_id:
            The requesting brand.
        """
        with self._lock:
            raw = self._configs.get(supplier_id)
        if raw is None:
            raise KeyError(f"No configuration found for supplier {supplier_id!r}")
        base = copy.deepcopy(raw)
        overrides = base.pop("brand_overrides", {})
        brand_override = overrides.get(brand_id, {})
        base.update(brand_override)
        return self._build_config(base)

    def update_config(self, supplier_id: str, updates: dict[str, Any]) -> None:
        """Apply a partial update to a supplier's base configuration.

        Intended for runtime reconfiguration (e.g. rotating API keys without
        restarting the process).

        Parameters
        ----------
        supplier_id:
            The supplier to update.
        updates:
            Partial dict of fields to update.  Merged shallowly onto the
            existing config.

        Raises
        ------
        KeyError
            If the supplier has not been registered.
        """
        with self._lock:
            if supplier_id not in self._configs:
                raise KeyError(
                    f"Cannot update unknown supplier {supplier_id!r}"
                )
            self._configs[supplier_id].update(updates)

    def registered_suppliers(self) -> list[str]:
        """Return the list of registered supplier IDs."""
        with self._lock:
            return list(self._configs.keys())

    @staticmethod
    def _build_config(raw: dict[str, Any]) -> SupplierConfig:
        """Strip internal keys and construct a validated :class:`SupplierConfig`."""
        clean = {k: v for k, v in raw.items() if k != "brand_overrides"}
        return SupplierConfig.model_validate(clean)


__all__ = ["SupplierConfig", "SupplierSettingsManager"]
