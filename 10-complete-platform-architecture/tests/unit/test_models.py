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
Unit tests for Pydantic domain models.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from acmetocasino.gameservice.models.enums import (
    ActionCode,
    CommandType,
    FundSource,
    GameMode,
    ProductType,
    RealityCheckAction,
)
from acmetocasino.gameservice.models.launch_request import LaunchRequest
from acmetocasino.gameservice.models.player_context import PlayerContext
from acmetocasino.gameservice.models.round_command import RoundCommand
from acmetocasino.gameservice.models.wallet_snapshot import WalletSnapshot


# ---------------------------------------------------------------------------
# WalletSnapshot
# ---------------------------------------------------------------------------


def test_wallet_snapshot_total_balance_is_sum() -> None:
    snap = WalletSnapshot(cash_balance=Decimal("50"), bonus_balance=Decimal("10"), currency="EUR")
    assert snap.total_balance == Decimal("60")


def test_wallet_snapshot_free_round_credits_included_in_total() -> None:
    snap = WalletSnapshot(
        cash_balance=Decimal("50"),
        bonus_balance=Decimal("5"),
        free_round_credits=Decimal("2"),
        currency="GBP",
    )
    assert snap.total_balance == Decimal("57")


def test_wallet_snapshot_negative_cash_rejected() -> None:
    with pytest.raises(ValidationError):
        WalletSnapshot(cash_balance=Decimal("-1"), currency="EUR")


def test_wallet_snapshot_with_cash_delta_positive() -> None:
    snap = WalletSnapshot(cash_balance=Decimal("100"), currency="EUR")
    updated = snap.with_cash_delta(Decimal("50"))
    assert updated.cash_balance == Decimal("150")


def test_wallet_snapshot_with_cash_delta_negative() -> None:
    snap = WalletSnapshot(cash_balance=Decimal("100"), currency="EUR")
    updated = snap.with_cash_delta(Decimal("-30"))
    assert updated.cash_balance == Decimal("70")


def test_wallet_snapshot_with_cash_delta_negative_overdraft_raises() -> None:
    snap = WalletSnapshot(cash_balance=Decimal("10"), currency="EUR")
    with pytest.raises(ValueError):
        snap.with_cash_delta(Decimal("-20"))


def test_wallet_snapshot_currency_must_be_3_chars() -> None:
    with pytest.raises(ValidationError):
        WalletSnapshot(cash_balance=Decimal("0"), currency="EURO")


def test_wallet_snapshot_is_frozen() -> None:
    snap = WalletSnapshot(cash_balance=Decimal("100"), currency="EUR")
    with pytest.raises(Exception):
        snap.cash_balance = Decimal("200")  # type: ignore[misc]


# ---------------------------------------------------------------------------
# RoundCommand
# ---------------------------------------------------------------------------


def test_round_command_valid_debit() -> None:
    cmd = RoundCommand(
        command_type=CommandType.DEBIT,
        round_id="r-1",
        amount=Decimal("5.00"),
    )
    assert cmd.is_debit()
    assert not cmd.is_credit()
    assert not cmd.is_rollback()


def test_round_command_valid_credit() -> None:
    cmd = RoundCommand(
        command_type=CommandType.CREDIT,
        round_id="r-1",
        amount=Decimal("10.00"),
    )
    assert cmd.is_credit()


def test_round_command_valid_rollback() -> None:
    cmd = RoundCommand(
        command_type=CommandType.ROLLBACK,
        round_id="r-1",
        amount=Decimal("0"),
    )
    assert cmd.is_rollback()


def test_round_command_float_amount_coerced() -> None:
    cmd = RoundCommand(command_type=CommandType.DEBIT, round_id="r-1", amount=5.0)  # type: ignore[arg-type]
    assert isinstance(cmd.amount, Decimal)


def test_round_command_negative_amount_rejected() -> None:
    with pytest.raises(ValidationError):
        RoundCommand(command_type=CommandType.DEBIT, round_id="r-1", amount=Decimal("-1"))


def test_round_command_empty_round_id_rejected() -> None:
    with pytest.raises(ValidationError):
        RoundCommand(command_type=CommandType.DEBIT, round_id="", amount=Decimal("1"))


def test_round_command_supplier_ref_optional() -> None:
    cmd = RoundCommand(command_type=CommandType.CREDIT, round_id="r-1", amount=Decimal("1"))
    assert cmd.supplier_ref is None


def test_round_command_is_frozen() -> None:
    cmd = RoundCommand(command_type=CommandType.DEBIT, round_id="r-1", amount=Decimal("1"))
    with pytest.raises(Exception):
        cmd.amount = Decimal("999")  # type: ignore[misc]


# ---------------------------------------------------------------------------
# PlayerContext
# ---------------------------------------------------------------------------


def test_player_context_defaults() -> None:
    ctx = PlayerContext(player_id="p-1", brand_id="acme", jurisdiction="MGA")
    assert ctx.currency == "EUR"
    assert ctx.kyc_verified is False
    assert ctx.self_excluded is False


def test_player_context_is_frozen() -> None:
    ctx = PlayerContext(player_id="p-1", brand_id="acme", jurisdiction="MGA")
    with pytest.raises(Exception):
        ctx.player_id = "p-2"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# LaunchRequest
# ---------------------------------------------------------------------------


def test_launch_request_defaults_real_money() -> None:
    ctx = PlayerContext(player_id="p-1", brand_id="acme", jurisdiction="MGA")
    req = LaunchRequest(player=ctx, game_id="starburst", supplier_id="netent")
    assert req.mode == GameMode.REAL_MONEY
    assert req.channel == "web"


def test_launch_request_invalid_channel_rejected() -> None:
    ctx = PlayerContext(player_id="p-1", brand_id="acme", jurisdiction="MGA")
    with pytest.raises(ValidationError):
        LaunchRequest(player=ctx, game_id="g", supplier_id="s", channel="telegram")


def test_launch_request_is_real_money() -> None:
    ctx = PlayerContext(player_id="p-1", brand_id="acme", jurisdiction="MGA")
    req = LaunchRequest(player=ctx, game_id="g", supplier_id="s", mode=GameMode.DEMO)
    assert not req.is_real_money()


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


def test_game_mode_values() -> None:
    assert GameMode.REAL_MONEY == "real_money"
    assert GameMode.DEMO == "demo"
    assert GameMode.FREE_ROUND == "free_round"


def test_command_type_values() -> None:
    assert CommandType.DEBIT == "debit"
    assert CommandType.CREDIT == "credit"
    assert CommandType.ROLLBACK == "rollback"


def test_fund_source_values() -> None:
    assert FundSource.CASH == "cash"
    assert FundSource.BONUS == "bonus"


def test_reality_check_action_values() -> None:
    assert RealityCheckAction.CONTINUE == "continue"
    assert RealityCheckAction.TAKE_BREAK == "take_break"
