#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 43, Future Technology & Innovation in iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Tests for the embedded-partner-hub gateway (chapter 43c, Pattern 1)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jurisdiction_gate import JurisdictionGate  # noqa: E402
from market_lifecycle import MarketCategory  # noqa: E402
from partner_hub_gateway import (  # noqa: E402
    FilterResult,
    HandoffClaims,
    HubCategoryFilter,
    InsufficientFunds,
    Reservation,
    ReservationClosed,
    TokenError,
    UnknownAccount,
    UnknownReservation,
    WalletBridge,
    issue_handoff_token,
    verify_handoff_token,
)


class FakeClock:
    def __init__(self, start: float = 1_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_claims(**overrides) -> HandoffClaims:
    fields = dict(
        host_operator="matchbook",
        account_ref="mb-7f3a9c21",
        jurisdiction="BR",
        allowed_categories=(MarketCategory.FOOTBALL.value,
                            MarketCategory.OTHER_SPORTS.value),
        issued_at=1_000.0,
        expires_at=1_600.0,
    )
    fields.update(overrides)
    return HandoffClaims(**fields)


SECRET = b"top-secret-hub-key"


class TestHandoffToken:
    def test_round_trip(self):
        clock = FakeClock(start=1_100.0)
        claims = make_claims()
        token = issue_handoff_token(SECRET, claims)
        recovered = verify_handoff_token(SECRET, token, clock)
        assert recovered == claims

    def test_tampered_byte_breaks_verification(self):
        clock = FakeClock(start=1_100.0)
        token = issue_handoff_token(SECRET, make_claims())
        tampered = list(token)
        # flip a character in the payload segment (before the ".")
        idx = tampered.index(".") - 1
        tampered[idx] = "a" if tampered[idx] != "a" else "b"
        with pytest.raises(TokenError):
            verify_handoff_token(SECRET, "".join(tampered), clock)

    def test_wrong_secret_rejected(self):
        clock = FakeClock(start=1_100.0)
        token = issue_handoff_token(SECRET, make_claims())
        with pytest.raises(TokenError):
            verify_handoff_token(b"wrong-secret", token, clock)

    def test_expired_token_rejected(self):
        clock = FakeClock(start=2_000.0)  # past expires_at=1600
        token = issue_handoff_token(SECRET, make_claims())
        with pytest.raises(TokenError):
            verify_handoff_token(SECRET, token, clock)

    def test_malformed_token_rejected(self):
        clock = FakeClock()
        with pytest.raises(TokenError):
            verify_handoff_token(SECRET, "not-a-valid-token", clock)

    def test_email_account_ref_rejected_at_issue(self):
        clock = FakeClock()
        claims = make_claims(account_ref="player@example.com")
        with pytest.raises(ValueError):
            issue_handoff_token(SECRET, claims)
        assert clock.now == 1_000.0  # nothing to verify; issuance never happened

    def test_cpf_like_account_ref_rejected_at_issue(self):
        claims = make_claims(account_ref="12345678901")  # 11 digits
        with pytest.raises(ValueError):
            issue_handoff_token(SECRET, claims)

    def test_pseudonymous_ref_accepted(self):
        claims = make_claims(account_ref="mb-7f3a9c21")
        token = issue_handoff_token(SECRET, claims)
        assert token  # did not raise


class TestWalletBridge:
    def test_reserve_capture_flow(self):
        wallet = WalletBridge()
        wallet.open_account("mb-1", 10_000)
        reservation = wallet.reserve("mb-1", 3_000, idempotency_key="k1")
        assert isinstance(reservation, Reservation)
        assert wallet.balance("mb-1") == 7_000
        captured = wallet.capture(reservation.reservation_id, idempotency_key="k2")
        assert captured == 3_000

    def test_reserve_insufficient_funds(self):
        wallet = WalletBridge()
        wallet.open_account("mb-1", 500)
        with pytest.raises(InsufficientFunds):
            wallet.reserve("mb-1", 3_000, idempotency_key="k1")

    def test_reserve_unknown_account(self):
        wallet = WalletBridge()
        with pytest.raises(UnknownAccount):
            wallet.reserve("ghost", 100, idempotency_key="k1")

    def test_release_returns_cents(self):
        wallet = WalletBridge()
        wallet.open_account("mb-1", 10_000)
        reservation = wallet.reserve("mb-1", 3_000, idempotency_key="k1")
        released = wallet.release(reservation.reservation_id, idempotency_key="k2")
        assert released == 3_000
        assert wallet.balance("mb-1") == 10_000

    def test_credit_settlement(self):
        wallet = WalletBridge()
        wallet.open_account("mb-1", 0)
        credited = wallet.credit_settlement("mb-1", 4_200, idempotency_key="k1")
        assert credited == 4_200
        assert wallet.balance("mb-1") == 4_200

    def test_reserve_idempotent_same_key_reserves_once(self):
        wallet = WalletBridge()
        wallet.open_account("mb-1", 10_000)
        r1 = wallet.reserve("mb-1", 3_000, idempotency_key="same-key")
        r2 = wallet.reserve("mb-1", 3_000, idempotency_key="same-key")
        assert r1.reservation_id == r2.reservation_id
        # balance only moved once, not twice
        assert wallet.balance("mb-1") == 7_000

    def test_capture_idempotent_same_key_captures_once(self):
        wallet = WalletBridge()
        wallet.open_account("mb-1", 10_000)
        reservation = wallet.reserve("mb-1", 3_000, idempotency_key="k1")
        first = wallet.capture(reservation.reservation_id, idempotency_key="cap-key")
        second = wallet.capture(reservation.reservation_id, idempotency_key="cap-key")
        assert first == second == 3_000

    def test_capture_with_different_key_on_consumed_reservation_raises(self):
        wallet = WalletBridge()
        wallet.open_account("mb-1", 10_000)
        reservation = wallet.reserve("mb-1", 3_000, idempotency_key="k1")
        wallet.capture(reservation.reservation_id, idempotency_key="cap-key-1")
        with pytest.raises(ReservationClosed):
            wallet.capture(reservation.reservation_id, idempotency_key="cap-key-2")

    def test_release_with_different_key_on_consumed_reservation_raises(self):
        wallet = WalletBridge()
        wallet.open_account("mb-1", 10_000)
        reservation = wallet.reserve("mb-1", 3_000, idempotency_key="k1")
        wallet.release(reservation.reservation_id, idempotency_key="rel-key-1")
        with pytest.raises(ReservationClosed):
            wallet.release(reservation.reservation_id, idempotency_key="rel-key-2")

    def test_capture_unknown_reservation(self):
        wallet = WalletBridge()
        with pytest.raises(UnknownReservation):
            wallet.capture("rsv-does-not-exist", idempotency_key="k1")

    def test_journal_records_only_applied_mutations(self):
        wallet = WalletBridge()
        wallet.open_account("mb-1", 10_000)
        wallet.reserve("mb-1", 3_000, idempotency_key="k1")
        wallet.reserve("mb-1", 3_000, idempotency_key="k1")  # replay, not re-applied
        assert len(wallet.journal) == 1
        assert wallet.journal[0][0] == "reserve"
        assert wallet.journal[0][2] == "mb-1"
        assert wallet.journal[0][3] == 3_000

    def test_open_account_twice_rejected(self):
        wallet = WalletBridge()
        wallet.open_account("mb-1", 100)
        with pytest.raises(ValueError):
            wallet.open_account("mb-1", 200)

    def test_balance_unknown_account(self):
        wallet = WalletBridge()
        with pytest.raises(UnknownAccount):
            wallet.balance("ghost")


class TestHubCategoryFilter:
    def _markets(self):
        return [
            ("m-football", MarketCategory.FOOTBALL),
            ("m-politics", MarketCategory.POLITICS),
            ("m-weather", MarketCategory.WEATHER),
        ]

    def test_br_sports_pass_politics_dropped(self):
        gate = JurisdictionGate()
        filt = HubCategoryFilter(gate)
        result = filt.filter_markets("BR", self._markets())
        assert isinstance(result, FilterResult)
        allowed_ids = {mid for mid, _ in result.allowed}
        dropped_ids = {mid for mid, _ in result.dropped}
        assert allowed_ids == {"m-football"}
        assert dropped_ids == {"m-politics", "m-weather"}

    def test_de_everything_dropped(self):
        gate = JurisdictionGate()
        filt = HubCategoryFilter(gate)
        result = filt.filter_markets("DE", self._markets())
        assert result.allowed == []
        dropped_ids = {mid for mid, _ in result.dropped}
        assert dropped_ids == {"m-football", "m-politics", "m-weather"}
        # every dropped entry carries a human-readable reason
        assert all(isinstance(reason, str) and reason for _, reason in result.dropped)

    def test_dropped_reason_mentions_mode_for_blocked_jurisdiction(self):
        gate = JurisdictionGate()
        filt = HubCategoryFilter(gate)
        result = filt.filter_markets("DE", [("m-football", MarketCategory.FOOTBALL)])
        _, reason = result.dropped[0]
        assert "BLOCKED" in reason
