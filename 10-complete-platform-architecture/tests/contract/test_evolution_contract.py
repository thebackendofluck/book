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
Contract tests specific to EvolutionAdapter.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from acmetocasino.gameservice.errors import GameServiceError
from acmetocasino.gameservice.models.enums import CommandType, GameMode
from acmetocasino.gameservice.models.launch_request import LaunchRequest
from acmetocasino.gameservice.models.player_context import PlayerContext
from acmetocasino.gameservice.models.round_command import RoundCommand
from acmetocasino.gameservice.suppliers.evolution.adapter import EvolutionAdapter
from acmetocasino.gameservice.suppliers.evolution.config import EvolutionConfig
from acmetocasino.gameservice.suppliers.evolution.models import EvolutionWebhookEvent


@pytest.fixture
def evo_adapter() -> EvolutionAdapter:
    config = EvolutionConfig(
        supplier_id="evolution",
        display_name="Evolution",
        api_base_url="https://api.staging.evo-services.com",
        casino_key="test-casino",
        api_token="test-token",
        webhook_secret="",
        environment="staging",
    )
    return EvolutionAdapter(config)


@pytest.fixture
def player_ctx() -> PlayerContext:
    return PlayerContext(
        player_id="p-evo",
        brand_id="acme_uk",
        jurisdiction="MGA",
        currency="EUR",
        session_token="tok-evo",
        cash_balance=Decimal("500"),
    )


def test_evolution_launch_real_money_succeeds(
    evo_adapter: EvolutionAdapter,
    player_ctx: PlayerContext,
) -> None:
    request = LaunchRequest(
        player=player_ctx,
        game_id="lightning-roulette",
        supplier_id="evolution",
        mode=GameMode.REAL_MONEY,
    )
    result = evo_adapter.launch_session(request)
    assert result.session_id
    assert "casinoKey=test-casino" in result.game_url


def test_evolution_demo_raises_error(
    evo_adapter: EvolutionAdapter,
    player_ctx: PlayerContext,
) -> None:
    request = LaunchRequest(
        player=player_ctx,
        game_id="lightning-roulette",
        supplier_id="evolution",
        mode=GameMode.DEMO,
    )
    with pytest.raises(GameServiceError) as exc_info:
        evo_adapter.launch_session(request)
    assert "demo" in exc_info.value.message.lower()


def test_evolution_debit_returns_updated_balance(evo_adapter: EvolutionAdapter) -> None:
    cmd = RoundCommand(
        command_type=CommandType.DEBIT,
        round_id="r-evo-1",
        amount=Decimal("0"),  # stub returns 0 balance
    )
    result = evo_adapter.debit("s-evo", "r-evo-1", Decimal("0"), cmd)
    assert result.transaction_id
    assert result.balance_after is not None


def test_evolution_credit_returns_result(evo_adapter: EvolutionAdapter) -> None:
    cmd = RoundCommand(
        command_type=CommandType.CREDIT,
        round_id="r-evo-2",
        amount=Decimal("15.00"),
    )
    result = evo_adapter.credit("s-evo", "r-evo-2", Decimal("15.00"), cmd)
    assert result.transaction_id
    assert result.balance_after.cash_balance == Decimal("15.00")


def test_evolution_rollback_returns_result(evo_adapter: EvolutionAdapter) -> None:
    result = evo_adapter.rollback("s-evo", "r-evo-1", "ref-orig")
    assert result.transaction_id


def test_evolution_handle_webhook_credit_event(evo_adapter: EvolutionAdapter) -> None:
    event = EvolutionWebhookEvent(
        type="CREDIT",
        sid="s-evo",
        roundId="r-webhook-1",
        transactionId="txn-1",
        gameId="LightningRoulette",
        token="tok-evo",
        value=1000,  # 10.00 in cents
    )
    response = evo_adapter.handle_webhook(event, b"body", "")
    assert response.status == "OK"
    assert response.transactionId


def test_evolution_handle_webhook_debit_event(evo_adapter: EvolutionAdapter) -> None:
    event = EvolutionWebhookEvent(
        type="DEBIT",
        sid="s-evo",
        roundId="r-webhook-2",
        transactionId="txn-2",
        gameId="LightningRoulette",
        token="tok-evo",
        value=0,  # zero debit on stub
    )
    response = evo_adapter.handle_webhook(event, b"body", "")
    assert response.status == "OK"


def test_evolution_handle_webhook_rollback_event(evo_adapter: EvolutionAdapter) -> None:
    event = EvolutionWebhookEvent(
        type="CANCEL",
        sid="s-evo",
        roundId="r-webhook-3",
        transactionId="txn-3",
        gameId="LightningRoulette",
        token="tok-evo",
        value=0,
    )
    response = evo_adapter.handle_webhook(event, b"body", "")
    assert response.status == "OK"
