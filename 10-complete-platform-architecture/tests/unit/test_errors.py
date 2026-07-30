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
Unit tests for gameservice.errors — domain exception hierarchy.
"""
from __future__ import annotations

import pytest

from acmetocasino.gameservice.errors import (
    AccountLimitReachedError,
    GameServiceError,
    GeoBlockedError,
    InsufficientFundsError,
    InvalidSessionError,
    KycNotApprovedError,
    NoMatchingDebitError,
    RealityCheckExpiredError,
    RoundClosedError,
    TransactionBlockedError,
)


def test_game_service_error_is_exception() -> None:
    err = GameServiceError(message="something broke")
    assert isinstance(err, Exception)
    assert err.message == "something broke"


def test_game_service_error_str_includes_class_and_message() -> None:
    err = GameServiceError(message="oops", player_id="p-1")
    text = str(err)
    assert "GameServiceError" in text
    assert "oops" in text
    assert "p-1" in text


def test_game_service_error_correlation_id_in_str() -> None:
    err = GameServiceError(message="x", correlation_id="corr-123")
    assert "corr-123" in str(err)


def test_insufficient_funds_error_attributes() -> None:
    err = InsufficientFundsError(
        message="not enough",
        player_id="p-2",
        requested_amount="50.00",
        available_balance="10.00",
    )
    assert err.requested_amount == "50.00"
    assert err.available_balance == "10.00"
    assert err.http_status == 422
    assert isinstance(err, GameServiceError)


def test_transaction_blocked_error() -> None:
    err = TransactionBlockedError(message="blocked", reason_code="velocity_breach")
    assert err.reason_code == "velocity_breach"
    assert err.http_status == 403


def test_no_matching_debit_error() -> None:
    err = NoMatchingDebitError(message="no debit", round_id="r-xyz")
    assert err.round_id == "r-xyz"
    assert err.http_status == 409


def test_account_limit_reached_error() -> None:
    err = AccountLimitReachedError(message="limit hit", limit_type="deposit", reset_at="2026-01-01T00:00:00Z")
    assert err.limit_type == "deposit"
    assert err.reset_at == "2026-01-01T00:00:00Z"
    assert err.http_status == 403


def test_invalid_session_error() -> None:
    err = InvalidSessionError(message="expired", session_token="tok-old")
    assert err.session_token == "tok-old"
    assert err.http_status == 401


def test_round_closed_error() -> None:
    err = RoundClosedError(message="already settled", round_id="r-1")
    assert err.round_id == "r-1"
    assert err.http_status == 409


def test_geo_blocked_error() -> None:
    err = GeoBlockedError(message="US blocked", detected_country="US", required_jurisdiction="MGA")
    assert err.detected_country == "US"
    assert err.http_status == 451


def test_reality_check_expired_error() -> None:
    err = RealityCheckExpiredError(message="time up", elapsed_minutes=62, interval_minutes=60)
    assert err.elapsed_minutes == 62
    assert err.interval_minutes == 60
    assert err.http_status == 403


def test_kyc_not_approved_error() -> None:
    err = KycNotApprovedError(message="kyc pending", kyc_status="pending")
    assert err.kyc_status == "pending"
    assert err.http_status == 403


def test_retriable_flag_defaults_false() -> None:
    err = GameServiceError(message="x")
    assert err.retriable is False


def test_retriable_flag_can_be_set_true() -> None:
    err = GameServiceError(message="network glitch", retriable=True)
    assert err.retriable is True


def test_exception_hierarchy_catchable_as_base() -> None:
    errors = [
        InsufficientFundsError(message="x"),
        RoundClosedError(message="x"),
        GeoBlockedError(message="x"),
        InvalidSessionError(message="x"),
    ]
    for err in errors:
        assert isinstance(err, GameServiceError)
