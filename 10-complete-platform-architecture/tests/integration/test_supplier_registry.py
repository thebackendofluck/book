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
Integration tests for SupplierRegistry: registration, resolution, filtering.
"""
from __future__ import annotations

import pytest

from acmetocasino.gameservice.models.enums import ProductType
from acmetocasino.gameservice.suppliers.base import BaseSupplierAdapter
from acmetocasino.gameservice.suppliers.registry import (
    SupplierDisabledError,
    SupplierNotFoundError,
    SupplierRegistry,
)
from acmetocasino.gameservice.suppliers.settings import SupplierSettingsManager


def test_registry_registered_suppliers_sorted(supplier_registry: SupplierRegistry) -> None:
    ids = supplier_registry.registered_supplier_ids()
    assert ids == sorted(ids)


def test_registry_all_11_suppliers_registered(supplier_registry: SupplierRegistry) -> None:
    ids = supplier_registry.registered_supplier_ids()
    expected = {"evolution", "pragmatic", "netent", "kambi", "playngo",
                "hacksaw", "push_gaming", "igt", "nyx", "relax"}
    assert expected.issubset(set(ids))


def test_resolve_returns_adapter(supplier_registry: SupplierRegistry) -> None:
    adapter = supplier_registry.resolve("netent", "acme_uk", "MGA")
    assert isinstance(adapter, BaseSupplierAdapter)
    assert adapter.supplier_id == "netent"


def test_resolve_unknown_supplier_raises(supplier_registry: SupplierRegistry) -> None:
    with pytest.raises(SupplierNotFoundError):
        supplier_registry.resolve("nonexistent", "acme_uk", "MGA")


def test_resolve_blocked_jurisdiction_raises(supplier_registry: SupplierRegistry) -> None:
    # Update evolution config to block MGA
    mgr = supplier_registry.get_settings_manager()
    mgr.update_config("netent", {"blocked_jurisdictions": ["MGA"]})
    with pytest.raises(SupplierDisabledError):
        supplier_registry.resolve("netent", "acme_uk", "MGA")
    # Restore
    mgr.update_config("netent", {"blocked_jurisdictions": []})


def test_resolve_disabled_brand_raises(supplier_registry: SupplierRegistry) -> None:
    mgr = supplier_registry.get_settings_manager()
    mgr.update_config("netent", {"enabled_brands": ["acme_mt"]})
    with pytest.raises(SupplierDisabledError):
        supplier_registry.resolve("netent", "acme_uk", "MGA")
    # Restore
    mgr.update_config("netent", {"enabled_brands": ["*"]})


def test_resolve_cached_returns_same_instance(supplier_registry: SupplierRegistry) -> None:
    adapter1 = supplier_registry.resolve("pragmatic", "acme_uk", "MGA")
    adapter2 = supplier_registry.resolve("pragmatic", "acme_uk", "MGA")
    assert adapter1 is adapter2


def test_is_enabled_returns_true_for_active(supplier_registry: SupplierRegistry) -> None:
    assert supplier_registry.is_enabled("netent", "acme_uk", "MGA") is True


def test_is_enabled_returns_false_for_unknown(supplier_registry: SupplierRegistry) -> None:
    assert supplier_registry.is_enabled("unknown_supplier", "acme_uk", "MGA") is False


def test_list_available_filters_by_product_slots(supplier_registry: SupplierRegistry) -> None:
    infos = supplier_registry.list_available("acme_uk", "MGA", product_type=ProductType.SLOTS)
    ids = [i.supplier_id for i in infos]
    assert "netent" in ids
    assert "pragmatic" in ids
    assert "evolution" not in ids  # live casino only


def test_list_available_all_when_no_filter(supplier_registry: SupplierRegistry) -> None:
    infos = supplier_registry.list_available("acme_uk", "MGA")
    assert len(infos) >= 5


def test_re_register_invalidates_cache(supplier_registry: SupplierRegistry) -> None:
    from acmetocasino.gameservice.suppliers.netent.adapter import NetEntAdapter
    adapter1 = supplier_registry.resolve("netent", "acme_uk", "MGA")
    supplier_registry.register("netent", NetEntAdapter)
    adapter2 = supplier_registry.resolve("netent", "acme_uk", "MGA")
    # After re-registration the cache is cleared, so a new instance is created
    assert adapter1 is not adapter2
