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
Contract tests specific to KambiAdapter (sportsbook / PULL integration).
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from acmetocasino.gameservice.models.enums import CommandType, GameMode
from acmetocasino.gameservice.models.launch_request import LaunchRequest
from acmetocasino.gameservice.models.player_context import PlayerContext
from acmetocasino.gameservice.models.round_command import RoundCommand
from acmetocasino.gameservice.suppliers.base import LaunchResult, TransactionResult
from acmetocasino.gameservice.suppliers.kambi.adapter import KambiAdapter
from acmetocasino.gameservice.suppliers.kambi.config import KambiConfig


@pytest.fixture
def kambi_adapter() -> KambiAdapter:
    config = KambiConfig(
        supplier_id="kambi",
        display_name="Kambi",
        api_base_url="https://api.kambi.com",
        offering_url="acme",
        brand_id="acme_kambi",
    )
    return KambiAdapter(config)


@pytest.fixture
def player_ctx() -> PlayerContext:
    return PlayerContext(
        player_id="p-kambi",
        brand_id="acme_uk",
        jurisdiction="MGA",
        currency="EUR",
        session_token="tok-kambi",
        cash_balance=Decimal("500"),
    )


def test_kambi_launch_returns_result(
    kambi_adapter: KambiAdapter,
    player_ctx: PlayerContext,
) -> None:
    request = LaunchRequest(
        player=player_ctx,
        game_id="football",
        supplier_id="kambi",
        mode=GameMode.REAL_MONEY,
    )
    result = kambi_adapter.launch_session(request)
    assert isinstance(result, LaunchResult)
    assert result.game_url
    assert result.session_id


def test_kambi_debit_returns_result(kambi_adapter: KambiAdapter) -> None:
    # Stub returns cash_balance=0, so we debit 0 to avoid InsufficientFundsError
    cmd = RoundCommand(
        command_type=CommandType.DEBIT,
        round_id="bet-kambi-1",
        amount=Decimal("0"),
    )
    result = kambi_adapter.debit("s-kambi", "bet-kambi-1", Decimal("0"), cmd)
    assert isinstance(result, TransactionResult)
    assert result.transaction_id


def test_kambi_credit_returns_result(kambi_adapter: KambiAdapter) -> None:
    cmd = RoundCommand(
        command_type=CommandType.CREDIT,
        round_id="bet-kambi-1",
        amount=Decimal("20.00"),
    )
    result = kambi_adapter.credit("s-kambi", "bet-kambi-1", Decimal("20.00"), cmd)
    assert isinstance(result, TransactionResult)


def test_kambi_rollback_returns_result(kambi_adapter: KambiAdapter) -> None:
    result = kambi_adapter.rollback("s-kambi", "bet-kambi-1", "kambi-ref-orig")
    assert isinstance(result, TransactionResult)


def test_kambi_supplier_id(kambi_adapter: KambiAdapter) -> None:
    assert kambi_adapter.supplier_id == "kambi"


def test_kambi_end_session_does_not_raise(kambi_adapter: KambiAdapter) -> None:
    kambi_adapter.end_session("s-kambi-end")


def test_kambi_get_balance_returns_snapshot(kambi_adapter: KambiAdapter) -> None:
    from acmetocasino.gameservice.models.wallet_snapshot import WalletSnapshot
    snap = kambi_adapter.get_balance("s-kambi")
    assert isinstance(snap, WalletSnapshot)
    assert snap.cash_balance >= Decimal("0")
