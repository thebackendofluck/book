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
Contract tests: every adapter satisfies the SupplierAdapter protocol.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pytest

from acmetocasino.gameservice.models.enums import ActionCode, CommandType, GameMode
from acmetocasino.gameservice.models.launch_request import LaunchRequest
from acmetocasino.gameservice.models.player_context import PlayerContext
from acmetocasino.gameservice.models.round_command import RoundCommand
from acmetocasino.gameservice.models.wallet_snapshot import WalletSnapshot
from acmetocasino.gameservice.suppliers.base import (
    BaseSupplierAdapter,
    LaunchResult,
    SupplierAdapter,
    TransactionResult,
)
from acmetocasino.gameservice.suppliers.registry import SupplierRegistry


def _ctx() -> PlayerContext:
    return PlayerContext(
        player_id="p-contract",
        brand_id="acme_uk",
        jurisdiction="MGA",
        currency="EUR",
        session_token="tok",
        cash_balance=Decimal("200"),
        kyc_verified=True,
    )


def _debit_cmd(amount: str = "0") -> RoundCommand:
    # Use 0 amount because all stubs return cash_balance=0 from _do_get_balance
    return RoundCommand(
        command_type=CommandType.DEBIT,
        round_id="r-contract-1",
        amount=Decimal(amount),
        supplier_ref="sup-ref-1",
    )


def _credit_cmd(amount: str = "10.00") -> RoundCommand:
    return RoundCommand(
        command_type=CommandType.CREDIT,
        round_id="r-contract-1",
        amount=Decimal(amount),
    )


ALL_SUPPLIER_IDS = [
    "evolution", "pragmatic", "netent", "kambi", "playngo",
    "hacksaw", "push_gaming", "igt", "nyx", "relax",
]


@pytest.mark.parametrize("supplier_id", ALL_SUPPLIER_IDS)
def test_adapter_implements_supplier_adapter_protocol(
    supplier_registry: SupplierRegistry,
    supplier_id: str,
) -> None:
    """Every registered adapter must satisfy the SupplierAdapter Protocol."""
    adapter = supplier_registry.resolve(supplier_id, "acme_uk", "MGA")
    assert isinstance(adapter, SupplierAdapter)


@pytest.mark.parametrize("supplier_id", ALL_SUPPLIER_IDS)
def test_adapter_has_supplier_id_attribute(
    supplier_registry: SupplierRegistry,
    supplier_id: str,
) -> None:
    adapter = supplier_registry.resolve(supplier_id, "acme_uk", "MGA")
    assert isinstance(adapter.supplier_id, str)
    assert len(adapter.supplier_id) > 0
    assert adapter.supplier_id == supplier_id


@pytest.mark.parametrize("supplier_id", ALL_SUPPLIER_IDS)
def test_launch_session_returns_launch_result(
    supplier_registry: SupplierRegistry,
    supplier_id: str,
) -> None:
    adapter = supplier_registry.resolve(supplier_id, "acme_uk", "MGA")
    request = LaunchRequest(
        player=_ctx(),
        game_id="test-game",
        supplier_id=supplier_id,
        mode=GameMode.REAL_MONEY,
    )
    result = adapter.launch_session(request)
    assert isinstance(result, LaunchResult)
    assert result.session_id
    assert result.game_url
    assert result.token is not None
    assert isinstance(result.expires_at, datetime)
    assert result.expires_at.tzinfo is not None


@pytest.mark.parametrize("supplier_id", ALL_SUPPLIER_IDS)
def test_debit_returns_transaction_result(
    supplier_registry: SupplierRegistry,
    supplier_id: str,
) -> None:
    adapter = supplier_registry.resolve(supplier_id, "acme_uk", "MGA")
    cmd = _debit_cmd()  # amount=0 to avoid stub InsufficientFundsError
    result = adapter.debit("s-1", "r-1", Decimal("0"), cmd)
    assert isinstance(result, TransactionResult)
    assert result.transaction_id
    assert isinstance(result.balance_after, WalletSnapshot)


@pytest.mark.parametrize("supplier_id", ALL_SUPPLIER_IDS)
def test_credit_returns_transaction_result(
    supplier_registry: SupplierRegistry,
    supplier_id: str,
) -> None:
    adapter = supplier_registry.resolve(supplier_id, "acme_uk", "MGA")
    cmd = _credit_cmd()
    result = adapter.credit("s-1", "r-1", Decimal("10.00"), cmd)
    assert isinstance(result, TransactionResult)
    assert result.transaction_id


@pytest.mark.parametrize("supplier_id", ALL_SUPPLIER_IDS)
def test_rollback_returns_transaction_result(
    supplier_registry: SupplierRegistry,
    supplier_id: str,
) -> None:
    adapter = supplier_registry.resolve(supplier_id, "acme_uk", "MGA")
    result = adapter.rollback("s-1", "r-1", "ref-orig")
    assert isinstance(result, TransactionResult)


@pytest.mark.parametrize("supplier_id", ALL_SUPPLIER_IDS)
def test_end_session_does_not_raise(
    supplier_registry: SupplierRegistry,
    supplier_id: str,
) -> None:
    adapter = supplier_registry.resolve(supplier_id, "acme_uk", "MGA")
    # Must not raise
    adapter.end_session("s-1")


@pytest.mark.parametrize("supplier_id", ALL_SUPPLIER_IDS)
def test_get_balance_returns_wallet_snapshot(
    supplier_registry: SupplierRegistry,
    supplier_id: str,
) -> None:
    adapter = supplier_registry.resolve(supplier_id, "acme_uk", "MGA")
    snap = adapter.get_balance("s-1")
    assert isinstance(snap, WalletSnapshot)
    assert snap.cash_balance >= Decimal("0")
