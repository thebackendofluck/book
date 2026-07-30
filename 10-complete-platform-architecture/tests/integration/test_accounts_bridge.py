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
Integration tests for AccountsBridge: login → debit → credit → logout flow.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from acmetocasino.gameservice.accounts_bridge import AccountsBridge
from acmetocasino.gameservice.errors import InsufficientFundsError, InvalidSessionError
from acmetocasino.gameservice.models.enums import CommandType, RealityCheckAction
from acmetocasino.gameservice.models.player_context import PlayerContext
from acmetocasino.gameservice.models.round_command import RoundCommand

from tests.conftest import InMemoryAccountsProvider


@pytest.fixture
def fresh_provider() -> InMemoryAccountsProvider:
    p = InMemoryAccountsProvider()
    p.seed_wallet("player-001", "EUR", cash=Decimal("100.00"))
    return p


@pytest.fixture
def bridge(fresh_provider: InMemoryAccountsProvider) -> AccountsBridge:
    return AccountsBridge(default_provider=fresh_provider)


@pytest.fixture
def ctx() -> PlayerContext:
    return PlayerContext(
        player_id="player-001",
        brand_id="acme_uk",
        jurisdiction="MGA",
        session_token="",  # empty token accepted by InMemoryProvider
    )


def test_login_returns_auth_result(bridge: AccountsBridge, ctx: PlayerContext) -> None:
    result = bridge.login(ctx)
    assert result.player_id == ctx.player_id
    assert result.session_token is not None


def test_login_invalid_token_raises(fresh_provider: InMemoryAccountsProvider) -> None:
    bridge = AccountsBridge(default_provider=fresh_provider)
    ctx = PlayerContext(
        player_id="p-1",
        brand_id="acme_uk",
        jurisdiction="MGA",
        session_token="bad-token-xyz",
    )
    with pytest.raises(InvalidSessionError):
        bridge.login(ctx)


def test_get_balance_returns_snapshot(bridge: AccountsBridge) -> None:
    snap = bridge.get_balance("player-001", "acme_uk")
    assert snap.cash_balance == Decimal("100.00")
    assert snap.currency == "EUR"


def test_debit_reduces_balance(bridge: AccountsBridge) -> None:
    commands = [RoundCommand(command_type=CommandType.DEBIT, round_id="r-1", amount=Decimal("20.00"))]
    result = bridge.debit("player-001", "acme_uk", "starburst", "r-1", commands)
    assert result.succeeded is True
    assert result.balance.cash_balance == Decimal("80.00")


def test_credit_increases_balance(bridge: AccountsBridge) -> None:
    commands = [RoundCommand(command_type=CommandType.CREDIT, round_id="r-1", amount=Decimal("15.00"))]
    result = bridge.credit("player-001", "acme_uk", "starburst", "r-1", commands)
    assert result.balance.cash_balance == Decimal("115.00")


def test_debit_then_credit_full_round(bridge: AccountsBridge) -> None:
    debit_cmds = [RoundCommand(command_type=CommandType.DEBIT, round_id="r-2", amount=Decimal("10.00"))]
    bridge.debit("player-001", "acme_uk", "starburst", "r-2", debit_cmds)

    credit_cmds = [RoundCommand(command_type=CommandType.CREDIT, round_id="r-2", amount=Decimal("25.00"))]
    final = bridge.credit("player-001", "acme_uk", "starburst", "r-2", credit_cmds)
    assert final.balance.cash_balance == Decimal("115.00")


def test_debit_insufficient_funds_raises(bridge: AccountsBridge) -> None:
    commands = [RoundCommand(command_type=CommandType.DEBIT, round_id="r-3", amount=Decimal("999.00"))]
    with pytest.raises(InsufficientFundsError):
        bridge.debit("player-001", "acme_uk", "starburst", "r-3", commands)


def test_logout_clears_session(bridge: AccountsBridge, ctx: PlayerContext) -> None:
    bridge.login(ctx)
    bridge.logout(ctx.player_id)
    # After logout, reality check elapsed should be False (no session tracked)
    assert not bridge._reality_check_elapsed(ctx.player_id)


def test_debit_idempotency_returns_same_result(bridge: AccountsBridge) -> None:
    commands = [
        RoundCommand(
            command_type=CommandType.DEBIT,
            round_id="r-idem",
            amount=Decimal("5.00"),
            supplier_ref="ref-idem-1",
        )
    ]
    r1 = bridge.debit("player-001", "acme_uk", "starburst", "r-idem", commands)
    r2 = bridge.debit("player-001", "acme_uk", "starburst", "r-idem", commands)
    assert r2.already_processed is True
    assert r2.balance.cash_balance == r1.balance.cash_balance


def test_brand_routing_uses_default_provider(fresh_provider: InMemoryAccountsProvider) -> None:
    brand_provider = InMemoryAccountsProvider()
    brand_provider.seed_wallet("player-001", "EUR", cash=Decimal("500.00"))
    bridge = AccountsBridge(
        default_provider=fresh_provider,
        brand_providers={"acme_br": brand_provider},
    )
    # Querying for acme_br brand should use the brand provider
    snap = bridge.get_balance("player-001", "acme_br")
    assert snap.cash_balance == Decimal("500.00")

    # Querying for acme_uk uses default
    snap_default = bridge.get_balance("player-001", "acme_uk")
    assert snap_default.cash_balance == Decimal("100.00")


def test_add_bonus_increases_bonus_balance(bridge: AccountsBridge) -> None:
    result = bridge.add_bonus("player-001", "acme_uk", Decimal("50.00"), "welcome")
    assert result.balance.bonus_balance == Decimal("50.00")
