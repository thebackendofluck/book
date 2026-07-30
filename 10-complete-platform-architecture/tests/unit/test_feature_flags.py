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
Unit tests for CapabilityMatrix feature flags and filtering.
"""
from __future__ import annotations

import pytest

from acmetocasino.gameservice.models.enums import CallbackStyle, ProductType
from acmetocasino.gameservice.suppliers.base import SupplierCapabilities
from acmetocasino.gameservice.suppliers.capability_matrix import CapabilityMatrix


def test_matrix_has_all_11_suppliers() -> None:
    matrix = CapabilityMatrix()
    ids = matrix.all_supplier_ids()
    expected = {"evolution", "pragmatic", "netent", "kambi", "playngo",
                "hacksaw", "push_gaming", "igt", "nyx", "relax", "betgenius"}
    assert set(ids) == expected


def test_evolution_supports_tipping() -> None:
    matrix = CapabilityMatrix()
    assert matrix.supports("evolution", "tipping") is True


def test_evolution_does_not_support_free_rounds() -> None:
    matrix = CapabilityMatrix()
    assert matrix.supports("evolution", "free_rounds") is False


def test_pragmatic_supports_jackpots() -> None:
    matrix = CapabilityMatrix()
    assert matrix.supports("pragmatic", "jackpots") is True


def test_pragmatic_supports_bonus_buy() -> None:
    matrix = CapabilityMatrix()
    assert matrix.supports("pragmatic", "bonus_buy") is True


def test_kambi_supports_live_betting() -> None:
    matrix = CapabilityMatrix()
    assert matrix.supports("kambi", "live_betting") is True


def test_kambi_supports_cash_out() -> None:
    matrix = CapabilityMatrix()
    assert matrix.supports("kambi", "cash_out") is True


def test_filter_by_capability_free_rounds() -> None:
    matrix = CapabilityMatrix()
    suppliers = matrix.filter_by_capability("free_rounds")
    assert "pragmatic" in suppliers
    assert "netent" in suppliers
    assert "evolution" not in suppliers
    assert "kambi" not in suppliers


def test_filter_by_product_slots() -> None:
    matrix = CapabilityMatrix()
    suppliers = matrix.filter_by_product(ProductType.SLOTS)
    assert "netent" in suppliers
    assert "pragmatic" in suppliers
    assert "evolution" not in suppliers


def test_filter_by_product_sportsbook() -> None:
    matrix = CapabilityMatrix()
    suppliers = matrix.filter_by_product(ProductType.SPORTSBOOK)
    assert "kambi" in suppliers
    assert "betgenius" in suppliers
    assert "netent" not in suppliers


def test_unknown_supplier_supports_returns_false() -> None:
    matrix = CapabilityMatrix()
    assert matrix.supports("unknown_supplier", "free_rounds") is False


def test_get_capabilities_unknown_supplier_raises() -> None:
    matrix = CapabilityMatrix()
    with pytest.raises(KeyError):
        matrix.get_capabilities("does_not_exist")


def test_custom_matrix_replaces_defaults() -> None:
    custom = {
        "test_supplier": SupplierCapabilities(
            supplier_id="test_supplier",
            product_types=(ProductType.SLOTS,),
            callback_style=CallbackStyle.SEAMLESS,
            free_rounds=True,
        )
    }
    matrix = CapabilityMatrix(custom_matrix=custom)
    assert matrix.all_supplier_ids() == ["test_supplier"]
    assert matrix.supports("test_supplier", "free_rounds") is True


def test_get_matrix_report_returns_all_suppliers() -> None:
    matrix = CapabilityMatrix()
    report = matrix.get_matrix_report()
    assert len(report) == 11


def test_evolution_no_demo_available() -> None:
    matrix = CapabilityMatrix()
    caps = matrix.get_capabilities("evolution")
    assert caps.demo_available is False
