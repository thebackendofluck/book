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
Integration tests for session launch through SupplierRegistry.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from acmetocasino.gameservice.errors import GameServiceError
from acmetocasino.gameservice.models.enums import GameMode
from acmetocasino.gameservice.models.launch_request import LaunchRequest
from acmetocasino.gameservice.models.player_context import PlayerContext
from acmetocasino.gameservice.suppliers.base import LaunchResult
from acmetocasino.gameservice.suppliers.registry import SupplierRegistry


@pytest.fixture
def real_money_ctx() -> PlayerContext:
    return PlayerContext(
        player_id="player-launch-001",
        brand_id="acme_uk",
        jurisdiction="MGA",
        currency="EUR",
        session_token="tok-launch",
        cash_balance=Decimal("100"),
        kyc_verified=True,
    )


def test_netent_launch_returns_launch_result(
    supplier_registry: SupplierRegistry,
    real_money_ctx: PlayerContext,
) -> None:
    adapter = supplier_registry.resolve("netent", "acme_uk", "MGA")
    request = LaunchRequest(
        player=real_money_ctx,
        game_id="starburst",
        supplier_id="netent",
        mode=GameMode.REAL_MONEY,
    )
    result = adapter.launch_session(request)
    assert isinstance(result, LaunchResult)
    assert result.game_url.startswith("http")
    assert result.session_id
    assert result.token is not None
    assert result.expires_at is not None


def test_pragmatic_launch_returns_launch_result(
    supplier_registry: SupplierRegistry,
    real_money_ctx: PlayerContext,
) -> None:
    adapter = supplier_registry.resolve("pragmatic", "acme_uk", "MGA")
    request = LaunchRequest(
        player=real_money_ctx,
        game_id="sweet-bonanza",
        supplier_id="pragmatic",
        mode=GameMode.REAL_MONEY,
    )
    result = adapter.launch_session(request)
    assert isinstance(result, LaunchResult)
    assert result.game_url


def test_evolution_demo_mode_raises(
    supplier_registry: SupplierRegistry,
    real_money_ctx: PlayerContext,
) -> None:
    adapter = supplier_registry.resolve("evolution", "acme_uk", "MGA")
    request = LaunchRequest(
        player=real_money_ctx,
        game_id="lightning-roulette",
        supplier_id="evolution",
        mode=GameMode.DEMO,
    )
    with pytest.raises(GameServiceError):
        adapter.launch_session(request)


def test_netent_demo_launch_succeeds(
    supplier_registry: SupplierRegistry,
    real_money_ctx: PlayerContext,
) -> None:
    adapter = supplier_registry.resolve("netent", "acme_uk", "MGA")
    request = LaunchRequest(
        player=real_money_ctx,
        game_id="starburst",
        supplier_id="netent",
        mode=GameMode.DEMO,
    )
    result = adapter.launch_session(request)
    assert result.game_url


def test_hacksaw_launch_returns_valid_result(
    supplier_registry: SupplierRegistry,
    real_money_ctx: PlayerContext,
) -> None:
    adapter = supplier_registry.resolve("hacksaw", "acme_uk", "MGA")
    request = LaunchRequest(
        player=real_money_ctx,
        game_id="wanted-dead-or-a-wild",
        supplier_id="hacksaw",
    )
    result = adapter.launch_session(request)
    assert isinstance(result, LaunchResult)


def test_kambi_launch_returns_valid_result(
    supplier_registry: SupplierRegistry,
    real_money_ctx: PlayerContext,
) -> None:
    adapter = supplier_registry.resolve("kambi", "acme_uk", "MGA")
    request = LaunchRequest(
        player=real_money_ctx,
        game_id="football-premierleague",
        supplier_id="kambi",
    )
    result = adapter.launch_session(request)
    assert isinstance(result, LaunchResult)


def test_session_id_is_unique_across_launches(
    supplier_registry: SupplierRegistry,
    real_money_ctx: PlayerContext,
) -> None:
    adapter = supplier_registry.resolve("netent", "acme_uk", "MGA")
    request = LaunchRequest(player=real_money_ctx, game_id="starburst", supplier_id="netent")
    r1 = adapter.launch_session(request)
    r2 = adapter.launch_session(request)
    assert r1.session_id != r2.session_id


def test_launch_url_contains_game_domain(
    supplier_registry: SupplierRegistry,
    real_money_ctx: PlayerContext,
) -> None:
    adapter = supplier_registry.resolve("netent", "acme_uk", "MGA")
    request = LaunchRequest(player=real_money_ctx, game_id="starburst", supplier_id="netent")
    result = adapter.launch_session(request)
    # The URL must be a proper URL
    assert "://" in result.game_url
