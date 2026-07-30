# Companion code for "The Backend of Luck" - Chapter 23b, DevSecOps Pipeline Implementation.
import os
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Smoke tests for post-deploy validation of the AcmeToCasino platform.

These are the first tests to run after a deployment. They verify that
the platform is alive and core flows are operational end-to-end before
more detailed integration tests execute.

Covers:
- Health endpoint returns 200
- Metrics endpoint is reachable and returns Prometheus text
- Auth login returns a token pair
- Auth refresh returns a new access token
- Player registration returns 201
- Game list (recent-rounds) is reachable
- Wallet recent-events endpoint is reachable
- Stats endpoint returns platform statistics
- Dashboard summary endpoint responds
- WebSocket /ws accepts and echoes ping → pong
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


class TestHealthEndpoint:
    def test_health_returns_200(self, app_client: TestClient) -> None:
        resp = app_client.get("/health")
        assert resp.status_code == 200

    def test_health_body_has_status_field(self, app_client: TestClient) -> None:
        body = app_client.get("/health").json()
        assert "status" in body

    def test_health_body_has_version(self, app_client: TestClient) -> None:
        body = app_client.get("/health").json()
        assert "version" in body

    def test_health_body_lists_services(self, app_client: TestClient) -> None:
        body = app_client.get("/health").json()
        assert "services" in body
        services = body["services"]
        assert "pam" in services
        assert "wallet" in services
        assert "gal" in services


class TestMetricsEndpoint:
    def test_metrics_returns_200(self, app_client: TestClient) -> None:
        resp = app_client.get("/metrics")
        assert resp.status_code == 200

    def test_metrics_content_type_is_prometheus(self, app_client: TestClient) -> None:
        resp = app_client.get("/metrics")
        # Prometheus text format or OpenMetrics
        content_type = resp.headers.get("content-type", "")
        assert "text/plain" in content_type or "openmetrics" in content_type

    def test_metrics_body_contains_http_counter(self, app_client: TestClient) -> None:
        body = app_client.get("/metrics").text
        assert "http_requests_total" in body


class TestAuthFlow:
    def test_login_with_valid_credentials_returns_tokens(
        self, app_client: TestClient
    ) -> None:
        """
        Login endpoint must return access_token and refresh_token.
        We mock the service layer so a known test password is accepted.
        """
        from unittest import mock
        from app.pam import service as pam_service

        fake_tokens = {
            "access_token": "fake.access.token",
            "refresh_token": "fake.refresh.token",
            "player_id": "some-player-uuid",
        }
        with mock.patch.object(pam_service, "authenticate", return_value=fake_tokens):
            resp = app_client.post(
                "/auth/login",
                json={"email": "player@acmetocasino.com", "password": os.environ.get("TEST_PASSWORD","test-user")},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert "refresh_token" in body

    def test_login_wrong_password_returns_401(self, app_client: TestClient) -> None:
        from unittest import mock
        from app.pam import service as pam_service

        with mock.patch.object(
            pam_service, "authenticate", side_effect=ValueError("Invalid credentials")
        ):
            resp = app_client.post(
                "/auth/login",
                json={"email": "player@acmetocasino.com", "password": "wrong"},
            )
        assert resp.status_code == 401

    def test_refresh_with_valid_token_returns_new_pair(
        self, app_client: TestClient
    ) -> None:
        from unittest import mock
        from app.pam import service as pam_service

        fake_tokens = {
            "access_token": "new.access.token",
            "refresh_token": "new.refresh.token",
        }
        with mock.patch.object(pam_service, "refresh_token", return_value=fake_tokens):
            resp = app_client.post(
                "/auth/refresh",
                json={"refresh_token": "some-valid-refresh-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body


class TestPlayerRegistration:
    def test_register_new_player_returns_201(self, app_client: TestClient) -> None:
        import uuid
        from unittest import mock
        from app.pam import service as pam_service

        new_id = uuid.uuid4()
        import datetime

        fake_player = {
            "id": new_id,
            "email": "newplayer@acmetocasino.com",
            "username": "newplayer",
            "status": "active",
            "kyc_status": "pending",
            "vip_tier": "bronze",
            "created_at": datetime.datetime.now(datetime.timezone.utc),
        }
        with mock.patch.object(pam_service, "create_player", return_value=fake_player):
            resp = app_client.post(
                "/players",
                json={
                    "email": "newplayer@acmetocasino.com",
                    "username": "newplayer",
                    "password": os.getenv("TEST_PASSWORD","test-user"),
                },
            )
        assert resp.status_code == 201
        assert "id" in resp.json()

    def test_register_duplicate_email_returns_409(self, app_client: TestClient) -> None:
        from unittest import mock
        from app.pam import service as pam_service

        with mock.patch.object(
            pam_service,
            "create_player",
            side_effect=Exception("unique constraint violation"),
        ):
            resp = app_client.post(
                "/players",
                json={
                    "email": "dup@acmetocasino.com",
                    "username": "dupuser",
                    "password": os.getenv("TEST_PASSWORD","test-user"),
                },
            )
        assert resp.status_code == 409


class TestGameEndpoints:
    def test_recent_rounds_returns_200(self, app_client: TestClient) -> None:
        from unittest import mock
        from app.gal import service as gal_service

        with mock.patch.object(gal_service, "get_recent_rounds", return_value=[]):
            resp = app_client.get("/gal/recent-rounds")
        assert resp.status_code == 200

    def test_rng_health_returns_ok(self, app_client: TestClient) -> None:
        from unittest import mock
        from app.gal.rng import ServerRNG

        with mock.patch.object(ServerRNG, "_generate_raw", return_value=b"\x00" * 32):
            resp = app_client.get("/gal/rng")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestWalletEndpoints:
    def test_recent_wallet_events_returns_200(self, app_client: TestClient) -> None:
        from unittest import mock
        from app.wallet import service as wallet_service

        with mock.patch.object(wallet_service, "get_recent_events", return_value=[]):
            resp = app_client.get("/wallet/recent-events")
        assert resp.status_code == 200


class TestStatsAndDashboard:
    def test_stats_endpoint_returns_200(self, app_client: TestClient) -> None:
        from unittest import mock
        import app.main as main_mod

        fake_stats: dict = {
            "players": {"total": 0, "active": 0},
            "wallet_events": {},
            "total_deposits": "0",
            "total_withdrawals": "0",
            "game_rounds": {"total": 0, "average_bet": "0.00"},
            "active_game_sessions": 0,
            "aml_alerts": {},
            "kyc_checks": {},
        }
        with mock.patch.object(main_mod, "_collect_dashboard_stats", return_value=fake_stats):
            resp = app_client.get("/stats")
        assert resp.status_code == 200

    def test_dashboard_summary_responds(self, app_client: TestClient) -> None:
        from unittest import mock
        import app.main as main_mod
        import app.dashboard_cache as cache_mod

        fake_summary = {
            "source": "live",
            "health": {"status": "healthy"},
            "stats": {},
        }
        with mock.patch.object(cache_mod, "get_dashboard_summary", return_value=fake_summary):
            resp = app_client.get("/dashboard/summary")
        assert resp.status_code in (200, 503)
        assert "source" in resp.json() or resp.status_code == 503


class TestWebSocket:
    def test_websocket_accepts_connection(self, app_client: TestClient) -> None:
        with app_client.websocket_connect("/ws") as ws:
            # Connection accepted — no exception means success
            pass

    def test_websocket_ping_pong(self, app_client: TestClient) -> None:
        with app_client.websocket_connect("/ws") as ws:
            ws.send_text("ping")
            response = ws.receive_text()
            assert response == "pong"
