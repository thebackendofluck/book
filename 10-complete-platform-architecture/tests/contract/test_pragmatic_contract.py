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
Contract tests specific to PragmaticAdapter.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from acmetocasino.gameservice.models.enums import ActionCode, CommandType, GameMode
from acmetocasino.gameservice.models.launch_request import LaunchRequest
from acmetocasino.gameservice.models.player_context import PlayerContext
from acmetocasino.gameservice.models.round_command import RoundCommand
from acmetocasino.gameservice.suppliers.base import LaunchResult, TransactionResult
from acmetocasino.gameservice.suppliers.pragmatic.adapter import PragmaticAdapter
from acmetocasino.gameservice.suppliers.pragmatic.config import PragmaticConfig


@pytest.fixture
def pragmatic_adapter() -> PragmaticAdapter:
    config = PragmaticConfig(
        supplier_id="pragmatic",
        display_name="Pragmatic Play",
        api_base_url="https://api.pragmaticplay.net",
        secure_login="test_operator",
        secret_key="test_secret",
    )
    return PragmaticAdapter(config)


@pytest.fixture
def player_ctx() -> PlayerContext:
    return PlayerContext(
        player_id="p-pp",
        brand_id="acme_uk",
        jurisdiction="MGA",
        currency="EUR",
        session_token="tok-pp",
        cash_balance=Decimal("200"),
        kyc_verified=True,
    )


def test_pragmatic_launch_real_money(
    pragmatic_adapter: PragmaticAdapter,
    player_ctx: PlayerContext,
) -> None:
    request = LaunchRequest(
        player=player_ctx,
        game_id="sweet-bonanza",
        supplier_id="pragmatic",
        mode=GameMode.REAL_MONEY,
    )
    result = pragmatic_adapter.launch_session(request)
    assert isinstance(result, LaunchResult)
    assert result.game_url
    assert result.session_id


def test_pragmatic_launch_demo_mode(
    pragmatic_adapter: PragmaticAdapter,
    player_ctx: PlayerContext,
) -> None:
    request = LaunchRequest(
        player=player_ctx,
        game_id="sweet-bonanza",
        supplier_id="pragmatic",
        mode=GameMode.DEMO,
    )
    result = pragmatic_adapter.launch_session(request)
    assert isinstance(result, LaunchResult)
    assert result.game_url


def test_pragmatic_debit_returns_result(pragmatic_adapter: PragmaticAdapter) -> None:
    # Stub get_balance returns 0, so debit 0 to avoid InsufficientFundsError
    cmd = RoundCommand(
        command_type=CommandType.DEBIT,
        round_id="r-pp-1",
        amount=Decimal("0"),
        action_code=ActionCode.REGULAR,
        supplier_ref="pp-ref-1",
    )
    result = pragmatic_adapter.debit("s-pp", "r-pp-1", Decimal("0"), cmd)
    assert isinstance(result, TransactionResult)
    assert result.transaction_id


def test_pragmatic_credit_returns_result(pragmatic_adapter: PragmaticAdapter) -> None:
    cmd = RoundCommand(
        command_type=CommandType.CREDIT,
        round_id="r-pp-2",
        amount=Decimal("10.00"),
        action_code=ActionCode.REGULAR,
    )
    result = pragmatic_adapter.credit("s-pp", "r-pp-2", Decimal("10.00"), cmd)
    assert isinstance(result, TransactionResult)
    assert result.balance_after.cash_balance >= Decimal("0")


def test_pragmatic_rollback_returns_result(pragmatic_adapter: PragmaticAdapter) -> None:
    result = pragmatic_adapter.rollback("s-pp", "r-pp-1", "pp-ref-1")
    assert isinstance(result, TransactionResult)


def test_pragmatic_bonus_buy_action_code(pragmatic_adapter: PragmaticAdapter) -> None:
    # Stub returns 0 balance; use 0 amount to avoid InsufficientFundsError
    cmd = RoundCommand(
        command_type=CommandType.DEBIT,
        round_id="r-pp-bb",
        amount=Decimal("0"),
        action_code=ActionCode.BONUS_BUY,
    )
    result = pragmatic_adapter.debit("s-pp", "r-pp-bb", Decimal("0"), cmd)
    assert isinstance(result, TransactionResult)


def test_pragmatic_supplier_id_is_correct(pragmatic_adapter: PragmaticAdapter) -> None:
    assert pragmatic_adapter.supplier_id == "pragmatic"


def test_pragmatic_end_session_does_not_raise(pragmatic_adapter: PragmaticAdapter) -> None:
    pragmatic_adapter.end_session("s-pp-end")
