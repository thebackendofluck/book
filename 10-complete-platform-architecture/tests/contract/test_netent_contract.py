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
Contract tests specific to NetEntAdapter.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from acmetocasino.gameservice.models.enums import CommandType, GameMode
from acmetocasino.gameservice.models.launch_request import LaunchRequest
from acmetocasino.gameservice.models.player_context import PlayerContext
from acmetocasino.gameservice.models.round_command import RoundCommand
from acmetocasino.gameservice.suppliers.base import LaunchResult, TransactionResult
from acmetocasino.gameservice.suppliers.netent.adapter import NetEntAdapter
from acmetocasino.gameservice.suppliers.netent.config import NetEntConfig


@pytest.fixture
def netent_adapter() -> NetEntAdapter:
    config = NetEntConfig(
        supplier_id="netent",
        display_name="NetEnt",
        api_base_url="https://api.netent.com",
        casino_id="acme_uk_test",
        game_server_url="https://games.netent.com",
    )
    return NetEntAdapter(config)


@pytest.fixture
def player_ctx() -> PlayerContext:
    return PlayerContext(
        player_id="p-ne",
        brand_id="acme_uk",
        jurisdiction="MGA",
        currency="EUR",
        session_token="tok-ne",
        cash_balance=Decimal("150"),
        kyc_verified=True,
    )


def test_netent_launch_real_money(
    netent_adapter: NetEntAdapter,
    player_ctx: PlayerContext,
) -> None:
    request = LaunchRequest(
        player=player_ctx,
        game_id="starburst",
        supplier_id="netent",
        mode=GameMode.REAL_MONEY,
    )
    result = netent_adapter.launch_session(request)
    assert isinstance(result, LaunchResult)
    assert result.game_url
    assert result.session_id


def test_netent_launch_demo_mode(
    netent_adapter: NetEntAdapter,
    player_ctx: PlayerContext,
) -> None:
    request = LaunchRequest(
        player=player_ctx,
        game_id="starburst",
        supplier_id="netent",
        mode=GameMode.DEMO,
    )
    result = netent_adapter.launch_session(request)
    assert isinstance(result, LaunchResult)


def test_netent_debit_returns_result(netent_adapter: NetEntAdapter) -> None:
    # Stub get_balance returns 0, so debit 0 to avoid InsufficientFundsError
    cmd = RoundCommand(
        command_type=CommandType.DEBIT,
        round_id="r-ne-1",
        amount=Decimal("0"),
        supplier_ref="ne-ref-1",
    )
    result = netent_adapter.debit("s-ne", "r-ne-1", Decimal("0"), cmd)
    assert isinstance(result, TransactionResult)
    assert result.transaction_id


def test_netent_credit_returns_result(netent_adapter: NetEntAdapter) -> None:
    cmd = RoundCommand(
        command_type=CommandType.CREDIT,
        round_id="r-ne-1",
        amount=Decimal("8.00"),
    )
    result = netent_adapter.credit("s-ne", "r-ne-1", Decimal("8.00"), cmd)
    assert isinstance(result, TransactionResult)


def test_netent_rollback_returns_result(netent_adapter: NetEntAdapter) -> None:
    result = netent_adapter.rollback("s-ne", "r-ne-1", "ne-ref-1")
    assert isinstance(result, TransactionResult)


def test_netent_get_balance_returns_snapshot(netent_adapter: NetEntAdapter) -> None:
    snap = netent_adapter.get_balance("s-ne")
    assert snap.cash_balance >= Decimal("0")
    assert snap.currency


def test_netent_supplier_id(netent_adapter: NetEntAdapter) -> None:
    assert netent_adapter.supplier_id == "netent"


def test_netent_end_session_does_not_raise(netent_adapter: NetEntAdapter) -> None:
    netent_adapter.end_session("s-ne-end")
