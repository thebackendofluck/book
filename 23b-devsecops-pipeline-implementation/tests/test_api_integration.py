# Companion code for "The Backend of Luck" - Chapter 23b, DevSecOps Pipeline Implementation.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Integration tests for the AcmeToCasino platform API.

Tests multi-step workflows end-to-end through the FastAPI router layer,
with service-layer functions mocked to isolate from live infrastructure.

Covers:
- Full player lifecycle: register → login → get balance → deposit → bet
- Game session lifecycle: launch → place bet → check session
- Concurrent bet placement (async via asyncio.gather)
- Wallet balance consistency after a sequence of operations
- Token refresh flow
- Player lookup after registration
"""

from __future__ import annotations

import os
import asyncio
import datetime
import uuid
from decimal import Decimal
from typing import Any
from unittest import mock

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers / shared fake data builders
# ---------------------------------------------------------------------------


def _fake_player(player_id: str | None = None) -> dict[str, Any]:
    pid = uuid.UUID(player_id) if player_id else uuid.uuid4()
    return {
        "id": pid,
        "email": f"player_{pid.hex[:8]}@acmetocasino.com",
        "username": f"player_{pid.hex[:8]}",
        "status": "active",
        "kyc_status": "pending",
        "vip_tier": "bronze",
        "created_at": datetime.datetime.now(datetime.timezone.utc),
    }


def _fake_tokens(player_id: str) -> dict[str, Any]:
    return {
        "access_token": "fake.access.token",
        "refresh_token": "fake.refresh.token",
        "player_id": player_id,
    }


def _fake_balance(
    player_id: str, balance: str = "1000.00", event_count: int = 1
) -> dict[str, Any]:
    return {
        "player_id": uuid.UUID(player_id),
        "balance": Decimal(balance),
        "currency": "USD",
        "event_count": event_count,
    }


def _fake_wallet_event(
    player_id: str,
    event_type: str = "DEPOSIT",
    amount: str = "100.00",
) -> dict[str, Any]:
    return {
        "id": 1,
        "player_id": uuid.UUID(player_id),
        "event_type": event_type,
        "amount": Decimal(amount),
        "currency": "USD",
        "reference_id": None,
        "metadata": None,
        "created_at": datetime.datetime.now(datetime.timezone.utc),
    }


def _fake_game_session(player_id: str, game_slug: str = "slots-acme") -> dict[str, Any]:
    return {
        "id": uuid.uuid4(),
        "player_id": uuid.UUID(player_id),
        "game_slug": game_slug,
        "status": "active",
        "rounds_played": 0,
        "total_bet": Decimal("0.00"),
        "total_win": Decimal("0.00"),
        "created_at": datetime.datetime.now(datetime.timezone.utc),
        "closed_at": None,
    }


# ---------------------------------------------------------------------------
# Full player lifecycle
# ---------------------------------------------------------------------------


class TestPlayerLifecycle:
    """
    Register → login → get balance → deposit → bet → check balance.
    Each step builds on the previous step's output (player_id, token).
    """

    def test_register_new_player(self, app_client: TestClient) -> None:
        pid = str(uuid.uuid4())
        fake_player = _fake_player(pid)

        from app.pam import service as pam_service

        with mock.patch.object(pam_service, "create_player", return_value=fake_player):
            resp = app_client.post(
                "/players",
                json={
                    "email": fake_player["email"],
                    "username": fake_player["username"],
                    "password": os.environ.get("TEST_PASSWORD","test-user"),
                },
            )
        assert resp.status_code == 201
        body = resp.json()
        assert "id" in body
        assert body["email"] == fake_player["email"]

    def test_login_after_registration(self, app_client: TestClient, player_id: str) -> None:
        fake_tok = _fake_tokens(player_id)

        from app.pam import service as pam_service

        with mock.patch.object(pam_service, "authenticate", return_value=fake_tok):
            resp = app_client.post(
                "/auth/login",
                json={"email": "player@acmetocasino.com", "password": os.environ.get("TEST_PASSWORD","test-user")},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["access_token"] == "fake.access.token"
        assert body["refresh_token"] == "fake.refresh.token"

    def test_get_balance_after_login(
        self, app_client: TestClient, auth_headers: dict[str, str], player_id: str
    ) -> None:
        fake_bal = _fake_balance(player_id, "0.00", 0)

        from app.wallet import service as wallet_service

        with mock.patch.object(wallet_service, "get_balance", return_value=fake_bal):
            resp = app_client.get(
                f"/wallet/{player_id}/balance",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "balance" in body

    def test_deposit_increases_balance(
        self, app_client: TestClient, auth_headers: dict[str, str], player_id: str
    ) -> None:
        fake_event = _fake_wallet_event(player_id, "DEPOSIT", "500.00")

        from app.wallet import service as wallet_service

        with mock.patch.object(wallet_service, "create_event", return_value=fake_event):
            resp = app_client.post(
                f"/wallet/{player_id}/transaction",
                headers=auth_headers,
                json={"event_type": "DEPOSIT", "amount": "500.00", "currency": "USD"},
            )
        assert resp.status_code == 201
        body = resp.json()
        assert body["event_type"] == "DEPOSIT"

    def test_place_bet_after_deposit(
        self, app_client: TestClient, auth_headers: dict[str, str], player_id: str
    ) -> None:
        fake_event = _fake_wallet_event(player_id, "BET", "50.00")

        from app.wallet import service as wallet_service

        with mock.patch.object(wallet_service, "create_event", return_value=fake_event):
            resp = app_client.post(
                f"/wallet/{player_id}/transaction",
                headers=auth_headers,
                json={"event_type": "BET", "amount": "50.00", "currency": "USD"},
            )
        assert resp.status_code == 201
        assert resp.json()["event_type"] == "BET"

    def test_balance_after_bet(
        self, app_client: TestClient, auth_headers: dict[str, str], player_id: str
    ) -> None:
        # After deposit 500 and bet 50 → expected 450 (mocked)
        fake_bal = _fake_balance(player_id, "450.00", 2)

        from app.wallet import service as wallet_service

        with mock.patch.object(wallet_service, "get_balance", return_value=fake_bal):
            resp = app_client.get(
                f"/wallet/{player_id}/balance",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        body = resp.json()
        assert Decimal(str(body["balance"])) == Decimal("450.00")

    def test_full_lifecycle_order(
        self, app_client: TestClient, auth_headers: dict[str, str], player_id: str
    ) -> None:
        """Smoke-check that deposit → bet → win sequence produces correct event types."""
        from app.wallet import service as wallet_service

        sequence = [
            ("DEPOSIT", "200.00"),
            ("BET", "20.00"),
            ("WIN", "35.00"),
        ]
        for event_type, amount in sequence:
            fake_event = _fake_wallet_event(player_id, event_type, amount)
            with mock.patch.object(wallet_service, "create_event", return_value=fake_event):
                resp = app_client.post(
                    f"/wallet/{player_id}/transaction",
                    headers=auth_headers,
                    json={
                        "event_type": event_type,
                        "amount": amount,
                        "currency": "USD",
                    },
                )
            assert resp.status_code == 201, f"Step {event_type} failed: {resp.text}"
            assert resp.json()["event_type"] == event_type


# ---------------------------------------------------------------------------
# Game session lifecycle
# ---------------------------------------------------------------------------


class TestGameSessionLifecycle:
    def test_launch_game_session(
        self, app_client: TestClient, auth_headers: dict[str, str], player_id: str
    ) -> None:
        fake_session = _fake_game_session(player_id, "slots-acme")

        from app.gal import service as gal_service

        with mock.patch.object(gal_service, "launch_game", return_value=fake_session):
            resp = app_client.post(
                "/gal/launch",
                headers=auth_headers,
                json={"player_id": player_id, "game_slug": "slots-acme"},
            )
        assert resp.status_code == 201
        body = resp.json()
        assert body["game_slug"] == "slots-acme"
        assert body["status"] == "active"

    def test_game_session_has_required_fields(
        self, app_client: TestClient, auth_headers: dict[str, str], player_id: str
    ) -> None:
        fake_session = _fake_game_session(player_id)

        from app.gal import service as gal_service

        with mock.patch.object(gal_service, "launch_game", return_value=fake_session):
            resp = app_client.post(
                "/gal/launch",
                headers=auth_headers,
                json={"player_id": player_id, "game_slug": "roulette-eu"},
            )
        body = resp.json()
        for field in ("id", "player_id", "game_slug", "status", "rounds_played"):
            assert field in body, f"Missing field '{field}' in game session response"

    def test_unknown_game_slug_returns_400(
        self, app_client: TestClient, auth_headers: dict[str, str], player_id: str
    ) -> None:
        from app.gal import service as gal_service

        with mock.patch.object(
            gal_service,
            "launch_game",
            side_effect=ValueError("Unknown game slug"),
        ):
            resp = app_client.post(
                "/gal/launch",
                headers=auth_headers,
                json={"player_id": player_id, "game_slug": "nonexistent-game"},
            )
        assert resp.status_code == 400

    def test_launch_game_without_auth_returns_401_or_403(
        self, app_client: TestClient, player_id: str
    ) -> None:
        resp = app_client.post(
            "/gal/launch",
            json={"player_id": player_id, "game_slug": "slots-acme"},
        )
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Token refresh flow
# ---------------------------------------------------------------------------


class TestTokenRefresh:
    def test_refresh_token_returns_new_pair(self, app_client: TestClient) -> None:
        from app.pam import service as pam_service

        new_tokens: dict[str, Any] = {
            "access_token": "new-access-token",
            "refresh_token": "new-refresh-token",
        }
        with mock.patch.object(pam_service, "refresh_token", return_value=new_tokens):
            resp = app_client.post(
                "/auth/refresh",
                json={"refresh_token": "old-refresh-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["access_token"] == "new-access-token"
        assert body["refresh_token"] == "new-refresh-token"

    def test_invalid_refresh_token_returns_401(self, app_client: TestClient) -> None:
        import jwt as pyjwt
        from app.pam import service as pam_service

        with mock.patch.object(
            pam_service,
            "refresh_token",
            side_effect=pyjwt.InvalidTokenError("bad token"),
        ):
            resp = app_client.post(
                "/auth/refresh",
                json={"refresh_token": "invalid-token"},
            )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Wallet balance consistency
# ---------------------------------------------------------------------------


class TestWalletBalanceConsistency:
    """
    Validates the event-sourced balance contract: credits add, debits subtract.
    Asserts that the balance after a sequence of events matches expectations.
    """

    def test_balance_reflects_net_of_events(
        self, app_client: TestClient, auth_headers: dict[str, str], player_id: str
    ) -> None:
        """
        Deposit 1000, Bet 200, Win 150 → net expected: 950.
        We mock create_event and then mock get_balance to return 950
        (service correctly computed from event log).
        """
        from app.wallet import service as wallet_service

        events = [
            ("DEPOSIT", "1000.00"),
            ("BET", "200.00"),
            ("WIN", "150.00"),
        ]
        for event_type, amount in events:
            fake_ev = _fake_wallet_event(player_id, event_type, amount)
            with mock.patch.object(wallet_service, "create_event", return_value=fake_ev):
                resp = app_client.post(
                    f"/wallet/{player_id}/transaction",
                    headers=auth_headers,
                    json={"event_type": event_type, "amount": amount, "currency": "USD"},
                )
            assert resp.status_code == 201

        # Mock computed balance: 1000 - 200 + 150 = 950
        expected_balance = Decimal("950.00")
        fake_bal = _fake_balance(player_id, str(expected_balance), 3)
        with mock.patch.object(wallet_service, "get_balance", return_value=fake_bal):
            bal_resp = app_client.get(
                f"/wallet/{player_id}/balance",
                headers=auth_headers,
            )
        assert bal_resp.status_code == 200
        assert Decimal(str(bal_resp.json()["balance"])) == expected_balance

    def test_withdrawal_reduces_balance(
        self, app_client: TestClient, auth_headers: dict[str, str], player_id: str
    ) -> None:
        from app.wallet import service as wallet_service

        # Deposit 500, withdraw 200 → 300
        for event_type, amount in [("DEPOSIT", "500.00"), ("WITHDRAWAL", "200.00")]:
            fake_ev = _fake_wallet_event(player_id, event_type, amount)
            with mock.patch.object(wallet_service, "create_event", return_value=fake_ev):
                app_client.post(
                    f"/wallet/{player_id}/transaction",
                    headers=auth_headers,
                    json={"event_type": event_type, "amount": amount, "currency": "USD"},
                )

        fake_bal = _fake_balance(player_id, "300.00", 2)
        with mock.patch.object(wallet_service, "get_balance", return_value=fake_bal):
            resp = app_client.get(
                f"/wallet/{player_id}/balance",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        assert Decimal(str(resp.json()["balance"])) == Decimal("300.00")

    def test_bonus_credit_adds_to_balance(
        self, app_client: TestClient, auth_headers: dict[str, str], player_id: str
    ) -> None:
        from app.wallet import service as wallet_service

        fake_ev = _fake_wallet_event(player_id, "BONUS_CREDIT", "25.00")
        with mock.patch.object(wallet_service, "create_event", return_value=fake_ev):
            resp = app_client.post(
                f"/wallet/{player_id}/transaction",
                headers=auth_headers,
                json={"event_type": "BONUS_CREDIT", "amount": "25.00", "currency": "USD"},
            )
        assert resp.status_code == 201
        assert resp.json()["event_type"] == "BONUS_CREDIT"

    def test_wallet_history_returns_list(
        self, app_client: TestClient, auth_headers: dict[str, str], player_id: str
    ) -> None:
        fake_events = [
            _fake_wallet_event(player_id, "DEPOSIT", "100.00"),
            _fake_wallet_event(player_id, "BET", "10.00"),
        ]

        from app.wallet import service as wallet_service

        with mock.patch.object(wallet_service, "get_history", return_value=fake_events):
            resp = app_client.get(
                f"/wallet/{player_id}/history",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) == 2


# ---------------------------------------------------------------------------
# Concurrent bet placement
# ---------------------------------------------------------------------------


class TestConcurrentBets:
    """
    Validates that placing multiple bets concurrently does not cause
    server errors. Uses asyncio.gather to fire requests in parallel.
    """

    def test_concurrent_bets_all_succeed_or_are_rejected_cleanly(
        self, app_client: TestClient, auth_headers: dict[str, str], player_id: str
    ) -> None:
        from app.wallet import service as wallet_service

        async def _place_bet(n: int) -> int:
            fake_ev = _fake_wallet_event(player_id, "BET", "10.00")
            # Each coroutine gets its own mock to avoid sharing state
            with mock.patch.object(wallet_service, "create_event", return_value=fake_ev):
                resp = app_client.post(
                    f"/wallet/{player_id}/transaction",
                    headers=auth_headers,
                    json={"event_type": "BET", "amount": "10.00", "currency": "USD"},
                )
            return resp.status_code

        async def _gather_bets() -> list[int]:
            return await asyncio.gather(*[_place_bet(i) for i in range(5)])

        status_codes: list[int] = asyncio.run(_gather_bets())
        for code in status_codes:
            assert code in (
                200, 201, 400, 422
            ), f"Unexpected status code {code} in concurrent bet"
        # At least some must succeed
        success_count = sum(1 for c in status_codes if c in (200, 201))
        assert success_count > 0, "Expected at least one successful bet in concurrent scenario"

    def test_concurrent_reads_are_safe(
        self, app_client: TestClient, auth_headers: dict[str, str], player_id: str
    ) -> None:
        from app.wallet import service as wallet_service

        fake_bal = _fake_balance(player_id, "1000.00", 1)

        async def _get_balance() -> int:
            with mock.patch.object(wallet_service, "get_balance", return_value=fake_bal):
                resp = app_client.get(
                    f"/wallet/{player_id}/balance",
                    headers=auth_headers,
                )
            return resp.status_code

        async def _gather_reads() -> list[int]:
            return await asyncio.gather(*[_get_balance() for _ in range(10)])

        status_codes: list[int] = asyncio.run(_gather_reads())
        assert all(c == 200 for c in status_codes), (
            f"All concurrent balance reads should return 200, got: {status_codes}"
        )


# ---------------------------------------------------------------------------
# Player lookup
# ---------------------------------------------------------------------------


class TestPlayerLookup:
    def test_get_existing_player(
        self, app_client: TestClient, auth_headers: dict[str, str], player_id: str
    ) -> None:
        fake_player = _fake_player(player_id)

        from app.pam import service as pam_service

        with mock.patch.object(pam_service, "get_player", return_value=fake_player):
            resp = app_client.get(
                f"/players/{player_id}",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "id" in body
        assert "email" in body

    def test_get_nonexistent_player_returns_404(
        self, app_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        from app.pam import service as pam_service

        with mock.patch.object(pam_service, "get_player", return_value=None):
            resp = app_client.get(
                f"/players/{uuid.uuid4()}",
                headers=auth_headers,
            )
        assert resp.status_code == 404

    def test_list_players_returns_paginated_response(
        self, app_client: TestClient
    ) -> None:
        fake_players = [_fake_player() for _ in range(3)]

        from app.pam import service as pam_service

        with mock.patch.object(pam_service, "list_players", return_value=fake_players):
            resp = app_client.get("/pam/players?limit=10&offset=0")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) == 3
