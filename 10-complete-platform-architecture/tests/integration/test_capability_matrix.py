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
Integration tests for CapabilityMatrix queries in a full registry context.
"""
from __future__ import annotations

import pytest

from acmetocasino.gameservice.models.enums import ProductType
from acmetocasino.gameservice.suppliers.capability_matrix import CapabilityMatrix
from acmetocasino.gameservice.suppliers.registry import SupplierRegistry


def test_matrix_available_via_registry(supplier_registry: SupplierRegistry) -> None:
    matrix = supplier_registry.get_capability_matrix()
    assert matrix is not None


def test_matrix_jackpot_suppliers(supplier_registry: SupplierRegistry) -> None:
    matrix = supplier_registry.get_capability_matrix()
    jackpot_suppliers = matrix.filter_by_capability("jackpots")
    assert "pragmatic" in jackpot_suppliers
    assert "playngo" in jackpot_suppliers
    assert "igt" in jackpot_suppliers
    assert "push_gaming" in jackpot_suppliers


def test_matrix_live_betting_suppliers() -> None:
    matrix = CapabilityMatrix()
    suppliers = matrix.filter_by_capability("live_betting")
    assert "kambi" in suppliers
    assert "betgenius" in suppliers
    assert "netent" not in suppliers


def test_matrix_tournament_suppliers() -> None:
    matrix = CapabilityMatrix()
    suppliers = matrix.filter_by_capability("tournaments")
    assert "pragmatic" in suppliers
    assert "hacksaw" in suppliers
    assert "relax" in suppliers


def test_matrix_regulatory_reporting_suppliers() -> None:
    matrix = CapabilityMatrix()
    suppliers = matrix.filter_by_capability("regulatory_reporting")
    assert "evolution" in suppliers
    assert "pragmatic" in suppliers
    assert "netent" in suppliers
    assert "kambi" in suppliers


def test_matrix_no_demo_for_live_casino() -> None:
    matrix = CapabilityMatrix()
    caps_evo = matrix.get_capabilities("evolution")
    assert caps_evo.demo_available is False
    caps_netent = matrix.get_capabilities("netent")
    assert caps_netent.demo_available is True


def test_matrix_filter_by_product_live_casino() -> None:
    matrix = CapabilityMatrix()
    suppliers = matrix.filter_by_product(ProductType.LIVE_CASINO)
    assert "evolution" in suppliers
    assert "pragmatic" in suppliers
    assert "netent" not in suppliers


def test_matrix_filter_by_product_scratch_cards() -> None:
    matrix = CapabilityMatrix()
    suppliers = matrix.filter_by_product(ProductType.SCRATCH_CARDS)
    assert "hacksaw" in suppliers


def test_list_available_slots_filters_correctly(supplier_registry: SupplierRegistry) -> None:
    infos = supplier_registry.list_available("acme_uk", "MGA", product_type=ProductType.SLOTS)
    slot_ids = {i.supplier_id for i in infos}
    assert "netent" in slot_ids
    assert "hacksaw" in slot_ids
    # Kambi (sportsbook only) must NOT appear
    assert "kambi" not in slot_ids


def test_list_available_returns_supplier_info_with_display_name(supplier_registry: SupplierRegistry) -> None:
    infos = supplier_registry.list_available("acme_uk", "MGA")
    for info in infos:
        assert info.display_name  # Non-empty
        assert info.capabilities is not None
