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
Unit tests for WalletService: reserve/commit/release/credit flows.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from acmetocasino.gameservice.accounts.wallet_service import InMemoryWalletStore, WalletService
from acmetocasino.gameservice.errors import InsufficientFundsError, RoundClosedError


def _make_service(player_id: str = "p-1", cash: str = "100.00") -> tuple[WalletService, str]:
    store = InMemoryWalletStore()
    store.create(player_id, "EUR", initial_cash=Decimal(cash))
    svc = WalletService(store=store)
    return svc, player_id


def test_reserve_reduces_available_balance() -> None:
    svc, pid = _make_service(cash="100.00")
    svc.reserve(pid, Decimal("20.00"), "r-1")
    snap = svc.get_balance(pid)
    assert snap.cash_balance == Decimal("80.00")


def test_commit_marks_reservation_terminal() -> None:
    svc, pid = _make_service()
    res_id = svc.reserve(pid, Decimal("10.00"), "r-1")
    snap = svc.commit(res_id)
    assert snap.cash_balance == Decimal("90.00")


def test_commit_twice_raises_round_closed_error() -> None:
    svc, pid = _make_service()
    res_id = svc.reserve(pid, Decimal("10.00"), "r-1")
    svc.commit(res_id)
    with pytest.raises(RoundClosedError):
        svc.commit(res_id)


def test_release_returns_funds_to_balance() -> None:
    svc, pid = _make_service(cash="100.00")
    res_id = svc.reserve(pid, Decimal("25.00"), "r-1")
    snap = svc.release(res_id)
    assert snap.cash_balance == Decimal("100.00")


def test_release_after_commit_raises_round_closed_error() -> None:
    svc, pid = _make_service()
    res_id = svc.reserve(pid, Decimal("5.00"), "r-1")
    svc.commit(res_id)
    with pytest.raises(RoundClosedError):
        svc.release(res_id)


def test_reserve_insufficient_funds_raises() -> None:
    svc, pid = _make_service(cash="10.00")
    with pytest.raises(InsufficientFundsError) as exc_info:
        svc.reserve(pid, Decimal("50.00"), "r-1")
    assert exc_info.value.requested_amount == "50.00"
    assert exc_info.value.available_balance == "10.00"


def test_reserve_negative_amount_raises_value_error() -> None:
    svc, pid = _make_service()
    with pytest.raises(ValueError):
        svc.reserve(pid, Decimal("-1"), "r-1")


def test_credit_adds_to_cash_balance() -> None:
    svc, pid = _make_service(cash="50.00")
    snap = svc.credit(pid, Decimal("30.00"), "r-2")
    assert snap.cash_balance == Decimal("80.00")


def test_credit_negative_amount_raises() -> None:
    svc, pid = _make_service()
    with pytest.raises(ValueError):
        svc.credit(pid, Decimal("-1"), "r-1")


def test_get_balance_returns_current_snapshot() -> None:
    svc, pid = _make_service(cash="77.00")
    snap = svc.get_balance(pid)
    assert snap.cash_balance == Decimal("77.00")
    assert snap.currency == "EUR"


def test_unknown_reservation_id_raises_key_error() -> None:
    svc, pid = _make_service()
    with pytest.raises(KeyError):
        svc.commit("non-existent-id")


def test_reserve_zero_amount_is_valid() -> None:
    svc, pid = _make_service()
    res_id = svc.reserve(pid, Decimal("0"), "r-1")
    snap = svc.commit(res_id)
    assert snap.cash_balance == Decimal("100.00")


def test_multiple_reservations_accumulate() -> None:
    svc, pid = _make_service(cash="100.00")
    svc.reserve(pid, Decimal("20.00"), "r-1")
    svc.reserve(pid, Decimal("30.00"), "r-2")
    snap = svc.get_balance(pid)
    assert snap.cash_balance == Decimal("50.00")
