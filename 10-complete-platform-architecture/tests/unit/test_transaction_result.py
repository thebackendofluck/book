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
Unit tests for TransactionResult (gameservice layer).
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from acmetocasino.gameservice.models.wallet_snapshot import WalletSnapshot
from acmetocasino.gameservice.transaction_result import TransactionResult


def _snap(cash: str = "100.00", currency: str = "EUR") -> WalletSnapshot:
    return WalletSnapshot(cash_balance=Decimal(cash), currency=currency)


def test_transaction_result_defaults() -> None:
    result = TransactionResult(
        external_id="eid-1",
        balance=_snap(),
    )
    assert result.succeeded is True
    assert result.already_processed is False
    assert result.reality_check_elapsed is False
    assert result.error_message is None
    assert result.cash_usage == Decimal("0")
    assert result.bonus_usage == Decimal("0")


def test_transaction_result_total_usage() -> None:
    result = TransactionResult(
        external_id="eid-1",
        balance=_snap(),
        cash_usage=Decimal("5"),
        bonus_usage=Decimal("3"),
    )
    assert result.total_usage == Decimal("8")


def test_transaction_result_failed_state() -> None:
    result = TransactionResult(
        external_id="eid-1",
        balance=_snap(),
        succeeded=False,
        error_message="Insufficient funds",
    )
    assert result.succeeded is False
    assert result.error_message == "Insufficient funds"


def test_transaction_result_already_processed_flag() -> None:
    result = TransactionResult(
        external_id="eid-1",
        balance=_snap(),
        already_processed=True,
    )
    assert result.already_processed is True


def test_transaction_result_negative_cash_usage_rejected() -> None:
    with pytest.raises(ValidationError):
        TransactionResult(
            external_id="eid-1",
            balance=_snap(),
            cash_usage=Decimal("-1"),
        )


def test_transaction_result_negative_bonus_usage_rejected() -> None:
    with pytest.raises(ValidationError):
        TransactionResult(
            external_id="eid-1",
            balance=_snap(),
            bonus_usage=Decimal("-0.01"),
        )


def test_transaction_result_reality_check_elapsed_flag() -> None:
    result = TransactionResult(
        external_id="eid-1",
        balance=_snap(),
        reality_check_elapsed=True,
    )
    assert result.reality_check_elapsed is True


def test_transaction_result_repr_ok() -> None:
    result = TransactionResult(external_id="eid-x", balance=_snap())
    r = repr(result)
    assert "eid-x" in r
    assert "ok" in r


def test_transaction_result_repr_failed() -> None:
    result = TransactionResult(
        external_id="eid-x",
        balance=_snap(),
        succeeded=False,
        error_message="oops",
    )
    r = repr(result)
    assert "FAILED" in r
