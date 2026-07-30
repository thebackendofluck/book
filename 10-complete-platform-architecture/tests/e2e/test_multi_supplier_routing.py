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
E2E test: multi-supplier routing — different suppliers for different brands/products.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from acmetocasino.gameservice.models.enums import GameMode, ProductType
from acmetocasino.gameservice.models.launch_request import LaunchRequest
from acmetocasino.gameservice.models.player_context import PlayerContext
from acmetocasino.gameservice.suppliers.base import LaunchResult
from acmetocasino.gameservice.suppliers.registry import (
    SupplierDisabledError,
    SupplierNotFoundError,
    SupplierRegistry,
)


def _ctx(
    player_id: str = "p-routing",
    brand_id: str = "acme_uk",
    jurisdiction: str = "MGA",
    currency: str = "EUR",
) -> PlayerContext:
    return PlayerContext(
        player_id=player_id,
        brand_id=brand_id,
        jurisdiction=jurisdiction,
        currency=currency,
        session_token="tok",
        cash_balance=Decimal("100"),
    )


def test_slots_supplier_resolves_correctly(supplier_registry: SupplierRegistry) -> None:
    adapter = supplier_registry.resolve("netent", "acme_uk", "MGA")
    assert adapter.supplier_id == "netent"


def test_live_casino_supplier_resolves_correctly(supplier_registry: SupplierRegistry) -> None:
    adapter = supplier_registry.resolve("evolution", "acme_uk", "MGA")
    assert adapter.supplier_id == "evolution"


def test_sportsbook_supplier_resolves_correctly(supplier_registry: SupplierRegistry) -> None:
    adapter = supplier_registry.resolve("kambi", "acme_uk", "MGA")
    assert adapter.supplier_id == "kambi"


def test_slots_launch_for_different_suppliers(supplier_registry: SupplierRegistry) -> None:
    ctx = _ctx()
    slot_suppliers = ["netent", "pragmatic", "hacksaw", "playngo"]
    for supplier_id in slot_suppliers:
        adapter = supplier_registry.resolve(supplier_id, "acme_uk", "MGA")
        request = LaunchRequest(
            player=ctx,
            game_id=f"{supplier_id}-game",
            supplier_id=supplier_id,
            mode=GameMode.REAL_MONEY,
        )
        result = adapter.launch_session(request)
        assert isinstance(result, LaunchResult), f"Launch failed for {supplier_id}"
        assert result.session_id, f"No session_id for {supplier_id}"


def test_blocked_jurisdiction_prevents_resolution(supplier_registry: SupplierRegistry) -> None:
    mgr = supplier_registry.get_settings_manager()
    mgr.update_config("playngo", {"blocked_jurisdictions": ["DE"]})
    with pytest.raises(SupplierDisabledError):
        supplier_registry.resolve("playngo", "acme_de", "DE")
    # Restore
    mgr.update_config("playngo", {"blocked_jurisdictions": []})


def test_unknown_supplier_raises(supplier_registry: SupplierRegistry) -> None:
    with pytest.raises(SupplierNotFoundError):
        supplier_registry.resolve("betgenius", "acme_uk", "MGA")


def test_filter_slots_suppliers_for_brand(supplier_registry: SupplierRegistry) -> None:
    infos = supplier_registry.list_available("acme_uk", "MGA", product_type=ProductType.SLOTS)
    slot_ids = {i.supplier_id for i in infos}
    assert "netent" in slot_ids
    assert "pragmatic" in slot_ids
    assert "hacksaw" in slot_ids
    # Kambi (sportsbook) and Evolution (live casino) must not appear
    assert "kambi" not in slot_ids
    assert "evolution" not in slot_ids


def test_filter_live_casino_for_brand(supplier_registry: SupplierRegistry) -> None:
    infos = supplier_registry.list_available("acme_uk", "MGA", product_type=ProductType.LIVE_CASINO)
    ids = {i.supplier_id for i in infos}
    assert "evolution" in ids


def test_multiple_brands_can_have_different_enabled_suppliers(
    supplier_registry: SupplierRegistry,
) -> None:
    mgr = supplier_registry.get_settings_manager()
    # Restrict push_gaming to acme_br only
    mgr.update_config("push_gaming", {"enabled_brands": ["acme_br"]})

    assert supplier_registry.is_enabled("push_gaming", "acme_br", "MGA") is True
    assert supplier_registry.is_enabled("push_gaming", "acme_uk", "MGA") is False

    # Restore
    mgr.update_config("push_gaming", {"enabled_brands": ["*"]})


def test_session_ids_unique_across_suppliers(supplier_registry: SupplierRegistry) -> None:
    ctx = _ctx()
    session_ids = set()
    for supplier_id in ["netent", "pragmatic", "playngo"]:
        adapter = supplier_registry.resolve(supplier_id, "acme_uk", "MGA")
        request = LaunchRequest(
            player=ctx,
            game_id="some-game",
            supplier_id=supplier_id,
        )
        result = adapter.launch_session(request)
        session_ids.add(result.session_id)
    assert len(session_ids) == 3  # All unique
