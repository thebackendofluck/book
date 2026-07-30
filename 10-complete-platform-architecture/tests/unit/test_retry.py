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
Unit tests for error translation and retry-related behaviour in BaseSupplierAdapter.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from acmetocasino.gameservice.errors import GameServiceError
from acmetocasino.gameservice.models.enums import ActionCode, CommandType
from acmetocasino.gameservice.models.round_command import RoundCommand
from acmetocasino.gameservice.models.wallet_snapshot import WalletSnapshot
from acmetocasino.gameservice.suppliers.base import BaseSupplierAdapter, LaunchResult, TransactionResult


class _AlwaysFailAdapter(BaseSupplierAdapter):
    """Test adapter whose _do_* methods always raise a generic Exception."""

    supplier_id = "always_fail"

    def _do_launch(self, request, correlation_id):  # type: ignore[override]
        raise RuntimeError("network error")

    def _do_get_balance(self, session_id):  # type: ignore[override]
        raise RuntimeError("balance lookup failed")

    def _do_debit(self, session_id, round_id, amount, command):  # type: ignore[override]
        raise RuntimeError("debit failed")

    def _do_credit(self, session_id, round_id, amount, command):  # type: ignore[override]
        raise RuntimeError("credit failed")

    def _do_rollback(self, session_id, round_id, original_ref):  # type: ignore[override]
        raise RuntimeError("rollback failed")

    def _do_end_session(self, session_id):  # type: ignore[override]
        raise RuntimeError("end session failed")


class _SuccessAdapter(BaseSupplierAdapter):
    """Test adapter that always succeeds."""

    supplier_id = "always_ok"

    def _do_get_balance(self, session_id):  # type: ignore[override]
        return WalletSnapshot(cash_balance=Decimal("50"), currency="EUR")

    def _do_debit(self, session_id, round_id, amount, command):  # type: ignore[override]
        bal = WalletSnapshot(cash_balance=Decimal("50") - amount, currency="EUR")
        return TransactionResult(
            transaction_id=self._new_transaction_id(),
            balance_after=bal,
        )

    def _do_credit(self, session_id, round_id, amount, command):  # type: ignore[override]
        bal = WalletSnapshot(cash_balance=Decimal("50") + amount, currency="EUR")
        return TransactionResult(
            transaction_id=self._new_transaction_id(),
            balance_after=bal,
        )

    def _do_rollback(self, session_id, round_id, original_ref):  # type: ignore[override]
        bal = WalletSnapshot(cash_balance=Decimal("50"), currency="EUR")
        return TransactionResult(
            transaction_id=self._new_transaction_id(),
            balance_after=bal,
        )

    def _do_end_session(self, session_id):  # type: ignore[override]
        pass

    def _do_launch(self, request, correlation_id):  # type: ignore[override]
        from datetime import datetime, timezone, timedelta
        return LaunchResult(
            session_id="s-1",
            game_url="https://example.com/game",
            token="tok-1",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )


def test_translate_error_wraps_non_game_service_exception() -> None:
    adapter = _AlwaysFailAdapter()
    exc = RuntimeError("boom")
    wrapped = adapter._translate_error(exc)
    assert isinstance(wrapped, GameServiceError)
    assert "always_fail" in wrapped.message
    assert wrapped.retriable is True


def test_get_balance_wraps_unexpected_exception() -> None:
    adapter = _AlwaysFailAdapter()
    with pytest.raises(GameServiceError):
        adapter.get_balance("s-1")


def test_debit_wraps_unexpected_exception() -> None:
    adapter = _AlwaysFailAdapter()
    cmd = RoundCommand(command_type=CommandType.DEBIT, round_id="r-1", amount=Decimal("5"))
    with pytest.raises(GameServiceError):
        adapter.debit("s-1", "r-1", Decimal("5"), cmd)


def test_credit_wraps_unexpected_exception() -> None:
    adapter = _AlwaysFailAdapter()
    cmd = RoundCommand(command_type=CommandType.CREDIT, round_id="r-1", amount=Decimal("5"))
    with pytest.raises(GameServiceError):
        adapter.credit("s-1", "r-1", Decimal("5"), cmd)


def test_rollback_wraps_unexpected_exception() -> None:
    adapter = _AlwaysFailAdapter()
    with pytest.raises(GameServiceError):
        adapter.rollback("s-1", "r-1", "ref-orig")


def test_game_service_error_propagates_unchanged() -> None:
    """GameServiceErrors should pass through without double-wrapping."""
    class _DomainErrorAdapter(BaseSupplierAdapter):
        supplier_id = "domain_err"

        def _do_debit(self, session_id, round_id, amount, command):  # type: ignore[override]
            raise GameServiceError(message="domain error")

        def _do_credit(self, session_id, round_id, amount, command):  # type: ignore[override]
            raise NotImplementedError

        def _do_rollback(self, session_id, round_id, original_ref):  # type: ignore[override]
            raise NotImplementedError

        def _do_end_session(self, session_id):  # type: ignore[override]
            raise NotImplementedError

        def _do_launch(self, request, correlation_id):  # type: ignore[override]
            raise NotImplementedError

        def _do_get_balance(self, session_id):  # type: ignore[override]
            raise NotImplementedError

    adapter = _DomainErrorAdapter()
    cmd = RoundCommand(command_type=CommandType.DEBIT, round_id="r-1", amount=Decimal("1"))
    with pytest.raises(GameServiceError) as exc_info:
        adapter.debit("s-1", "r-1", Decimal("1"), cmd)
    # Should be the original error, not wrapped again
    assert exc_info.value.message == "domain error"


def test_success_adapter_debit_returns_result() -> None:
    adapter = _SuccessAdapter()
    cmd = RoundCommand(command_type=CommandType.DEBIT, round_id="r-1", amount=Decimal("10"))
    result = adapter.debit("s-1", "r-1", Decimal("10"), cmd)
    assert result.balance_after.cash_balance == Decimal("40")


def test_success_adapter_rollback_returns_result() -> None:
    adapter = _SuccessAdapter()
    result = adapter.rollback("s-1", "r-1", "ref-orig")
    assert result.transaction_id is not None
