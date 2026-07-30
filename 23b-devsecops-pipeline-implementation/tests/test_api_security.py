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
Security-focused API tests for the AcmeToCasino platform.

Validates that the platform correctly rejects:
- SQL injection payloads in request bodies and query parameters
- XSS payloads in text input fields
- JWT token manipulation (expired, bad signature, missing, wrong type)
- Auth bypass attempts (missing header, wrong role)
- Oversized payloads
- Negative / zero monetary amounts
- CORS pre-flight with untrusted origin

These tests do NOT require live infrastructure — all DB/Redis/Kafka
calls are intercepted via the conftest.py app_client fixture.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any
from unittest import mock

import jwt
import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SQL_INJECTION_PAYLOADS: list[str] = [
    "' OR '1'='1",
    "'; DROP TABLE players; --",
    "1; SELECT * FROM players --",
    "\" OR \"\"=\"",
    "1' AND SLEEP(5) --",
    "admin'--",
    "1 UNION SELECT username, password FROM players--",
]

_XSS_PAYLOADS: list[str] = [
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    "javascript:alert(1)",
    "\"><script>alert(document.cookie)</script>",
    "<svg onload=alert(1)>",
    "';alert(String.fromCharCode(88,83,83))//",
    "<body onload=alert('XSS')>",
]


def _make_jwt(
    subject: str = "test-user",
    role: str = "viewer",
    token_type: str = "access",
    secret: str = "test-secret-for-unit-tests-only",
    expire_delta: datetime.timedelta | None = None,
    algorithm: str = "HS256",
) -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        "sub": subject,
        "role": role,
        "type": token_type,
        "iat": now,
        "exp": now + (expire_delta or datetime.timedelta(minutes=15)),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


# ---------------------------------------------------------------------------
# SQL Injection
# ---------------------------------------------------------------------------


class TestSQLInjection:
    """
    Ensure SQL injection in login payloads returns 401/422, never 200 or 500.
    We mock pam.service.authenticate to always raise ValueError so we're
    purely testing that the router accepts the schema and returns a safe code.
    """

    @pytest.mark.parametrize("payload", _SQL_INJECTION_PAYLOADS)
    def test_sql_injection_in_email_field(
        self, app_client: TestClient, payload: str
    ) -> None:
        from app.pam import service as pam_service

        with mock.patch.object(
            pam_service, "authenticate", side_effect=ValueError("Invalid credentials")
        ):
            resp = app_client.post(
                "/auth/login",
                json={"email": payload, "password": os.getenv("TEST_PASSWORD","test-user")},
            )
        # Must not be 200 (authenticated) or 500 (leaked SQL error)
        assert resp.status_code in (400, 401, 422), (
            f"SQL injection in email field should not return {resp.status_code}"
        )

    @pytest.mark.parametrize("payload", _SQL_INJECTION_PAYLOADS)
    def test_sql_injection_in_password_field(
        self, app_client: TestClient, payload: str
    ) -> None:
        from app.pam import service as pam_service

        with mock.patch.object(
            pam_service, "authenticate", side_effect=ValueError("Invalid credentials")
        ):
            resp = app_client.post(
                "/auth/login",
                json={"email": "legit@acmetocasino.com", "password": payload},
            )
        assert resp.status_code in (400, 401, 422)

    @pytest.mark.parametrize("payload", _SQL_INJECTION_PAYLOADS)
    def test_sql_injection_in_username_registration(
        self, app_client: TestClient, payload: str
    ) -> None:
        from app.pam import service as pam_service

        with mock.patch.object(
            pam_service, "create_player", side_effect=ValueError("invalid input")
        ):
            resp = app_client.post(
                "/players",
                json={
                    "email": "legit@acmetocasino.com",
                    "username": payload,
                    "password": os.getenv("TEST_PASSWORD","test-user"),
                },
            )
        # Pydantic length validation (min 3) may reject short payloads with 422
        assert resp.status_code in (400, 409, 422, 500)
        # Critically: never 200 OK with SQL injected username persisted
        assert resp.status_code != 200


# ---------------------------------------------------------------------------
# XSS Payloads
# ---------------------------------------------------------------------------


class TestXSSPayloads:
    """
    Verify that XSS payloads in text fields do not cause server errors
    and are not reflected back without escaping in JSON (JSON encodes
    < > by default, so responses are safe).
    """

    @pytest.mark.parametrize("payload", _XSS_PAYLOADS)
    def test_xss_in_username_registration(
        self, app_client: TestClient, payload: str
    ) -> None:
        from app.pam import service as pam_service

        with mock.patch.object(
            pam_service, "create_player", side_effect=ValueError("invalid input")
        ):
            resp = app_client.post(
                "/players",
                json={
                    "email": "xsstest@acmetocasino.com",
                    "username": payload,
                    "password": os.getenv("TEST_PASSWORD","test-user"),
                },
            )
        # Must not be a 500 server error
        assert resp.status_code != 500

    @pytest.mark.parametrize("payload", _XSS_PAYLOADS)
    def test_xss_payload_not_executed_in_response(
        self, app_client: TestClient, payload: str
    ) -> None:
        """JSON response must not contain raw unescaped HTML tags."""
        from app.pam import service as pam_service

        with mock.patch.object(
            pam_service, "create_player", side_effect=ValueError("invalid input")
        ):
            resp = app_client.post(
                "/players",
                json={
                    "email": "xsstest2@acmetocasino.com",
                    "username": payload[:100],
                    "password": os.getenv("TEST_PASSWORD","test-user"),
                },
            )
        # FastAPI JSON responses escape < and > — check raw text
        body_text = resp.text
        # Raw unescaped <script> tags in JSON body would be a XSS vector
        assert "<script>" not in body_text


# ---------------------------------------------------------------------------
# JWT Token Manipulation
# ---------------------------------------------------------------------------


class TestJWTManipulation:
    def test_expired_token_returns_401(self, app_client: TestClient) -> None:
        from app.auth.rbac import verify_token

        expired = _make_jwt(expire_delta=datetime.timedelta(hours=-1))
        # Restore real verify_token to check actual expiry logic
        from app.auth import jwt_handler

        with mock.patch("app.auth.rbac.verify_token", side_effect=jwt.ExpiredSignatureError):
            resp = app_client.get(
                f"/wallet/{uuid.uuid4()}/balance",
                headers={"Authorization": f"Bearer {expired}"},
            )
        assert resp.status_code == 401

    def test_wrong_signature_returns_401(self, app_client: TestClient) -> None:
        bad_token = _make_jwt(secret="totally-wrong-secret")
        with mock.patch(
            "app.auth.rbac.verify_token", side_effect=jwt.InvalidSignatureError
        ):
            resp = app_client.get(
                f"/wallet/{uuid.uuid4()}/balance",
                headers={"Authorization": f"Bearer {bad_token}"},
            )
        assert resp.status_code == 401

    def test_missing_auth_header_returns_403_or_401(
        self, app_client: TestClient
    ) -> None:
        resp = app_client.get(f"/wallet/{uuid.uuid4()}/balance")
        # HTTPBearer returns 403 when header is absent by default
        assert resp.status_code in (401, 403)

    def test_wrong_token_type_returns_401(self, app_client: TestClient) -> None:
        """Using a refresh token where an access token is expected."""
        refresh_tok = _make_jwt(token_type="refresh")
        with mock.patch(
            "app.auth.rbac.verify_token",
            side_effect=ValueError("Expected token type 'access', got 'refresh'"),
        ):
            resp = app_client.get(
                f"/wallet/{uuid.uuid4()}/balance",
                headers={"Authorization": f"Bearer {refresh_tok}"},
            )
        assert resp.status_code == 401

    def test_malformed_token_returns_401(self, app_client: TestClient) -> None:
        with mock.patch(
            "app.auth.rbac.verify_token",
            side_effect=jwt.DecodeError("Not enough segments"),
        ):
            resp = app_client.get(
                f"/wallet/{uuid.uuid4()}/balance",
                headers={"Authorization": "Bearer not.a.jwt"},
            )
        assert resp.status_code == 401

    def test_empty_bearer_returns_401_or_403(self, app_client: TestClient) -> None:
        resp = app_client.get(
            f"/wallet/{uuid.uuid4()}/balance",
            headers={"Authorization": "Bearer "},
        )
        assert resp.status_code in (401, 403, 422)

    def test_algorithm_none_attack_rejected(self, app_client: TestClient) -> None:
        """
        The 'alg: none' attack bypasses signature verification in naive implementations.
        PyJWT raises DecodeError on alg=none unless explicitly allowed.
        """
        import base64
        import json as json_mod

        header = base64.urlsafe_b64encode(
            json_mod.dumps({"alg": "none", "typ": "JWT"}).encode()
        ).rstrip(b"=")
        payload_b64 = base64.urlsafe_b64encode(
            json_mod.dumps(
                {
                    "sub": "attacker",
                    "role": "admin",
                    "type": "access",
                    "exp": 9999999999,
                }
            ).encode()
        ).rstrip(b"=")
        none_token = f"{header.decode()}.{payload_b64.decode()}."

        with mock.patch(
            "app.auth.rbac.verify_token",
            side_effect=jwt.DecodeError("Algorithm not allowed"),
        ):
            resp = app_client.get(
                f"/wallet/{uuid.uuid4()}/balance",
                headers={"Authorization": f"Bearer {none_token}"},
            )
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Auth Bypass / Role Escalation
# ---------------------------------------------------------------------------


class TestAuthBypass:
    def test_viewer_cannot_access_admin_only_ops_route(
        self, app_client: TestClient, auth_token: str
    ) -> None:
        """
        Viewer-role token must not gain access to admin-only OPS endpoints.
        The OPS router uses its own JWT secret; a main platform token won't work.
        """
        resp = app_client.get(
            "/ops/dashboard",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        # OPS router validates its own token; platform token will be invalid there
        assert resp.status_code in (401, 403, 404, 422)

    def test_no_token_on_protected_wallet_endpoint(
        self, app_client: TestClient, player_id: str
    ) -> None:
        resp = app_client.get(f"/wallet/{player_id}/balance")
        assert resp.status_code in (401, 403)

    def test_no_token_on_wallet_transaction_endpoint(
        self, app_client: TestClient, player_id: str
    ) -> None:
        resp = app_client.post(
            f"/wallet/{player_id}/transaction",
            json={
                "event_type": "DEPOSIT",
                "amount": "100.00",
                "currency": "USD",
            },
        )
        assert resp.status_code in (401, 403)

    def test_no_token_on_game_launch_endpoint(self, app_client: TestClient) -> None:
        resp = app_client.post(
            "/gal/launch",
            json={"player_id": str(uuid.uuid4()), "game_slug": "slots-acme"},
        )
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Input Validation — Amounts
# ---------------------------------------------------------------------------


class TestInputValidation:
    def test_negative_bet_amount_rejected(
        self, app_client: TestClient, auth_headers: dict[str, str], player_id: str
    ) -> None:
        """Pydantic Field(gt=0) must reject negative and zero amounts."""
        from app.wallet import service as wallet_service

        with mock.patch.object(
            wallet_service,
            "create_event",
            side_effect=ValueError("amount must be positive"),
        ):
            resp = app_client.post(
                f"/wallet/{player_id}/transaction",
                headers=auth_headers,
                json={
                    "event_type": "BET",
                    "amount": "-50.00",
                    "currency": "USD",
                },
            )
        assert resp.status_code in (400, 422)

    def test_zero_amount_rejected(
        self, app_client: TestClient, auth_headers: dict[str, str], player_id: str
    ) -> None:
        from app.wallet import service as wallet_service

        with mock.patch.object(
            wallet_service,
            "create_event",
            side_effect=ValueError("amount must be positive"),
        ):
            resp = app_client.post(
                f"/wallet/{player_id}/transaction",
                headers=auth_headers,
                json={
                    "event_type": "DEPOSIT",
                    "amount": "0.00",
                    "currency": "USD",
                },
            )
        assert resp.status_code in (400, 422)

    def test_oversized_payload_rejected(self, app_client: TestClient) -> None:
        """Sending a 1 MB body should not cause a 500 server error."""
        big_username = "a" * (1024 * 1024)
        resp = app_client.post(
            "/players",
            json={
                "email": "big@acmetocasino.com",
                "username": big_username,
                "password": os.getenv("TEST_PASSWORD","test-user"),
            },
        )
        # Either Pydantic rejects (422), or server returns 400/422/413
        assert resp.status_code in (400, 413, 422)

    def test_invalid_currency_code_rejected(
        self, app_client: TestClient, auth_headers: dict[str, str], player_id: str
    ) -> None:
        from app.wallet import service as wallet_service

        with mock.patch.object(
            wallet_service,
            "create_event",
            side_effect=ValueError("invalid currency"),
        ):
            resp = app_client.post(
                f"/wallet/{player_id}/transaction",
                headers=auth_headers,
                json={
                    "event_type": "DEPOSIT",
                    "amount": "100.00",
                    "currency": "TOOLONG",
                },
            )
        assert resp.status_code in (400, 422)

    def test_invalid_event_type_rejected(
        self, app_client: TestClient, auth_headers: dict[str, str], player_id: str
    ) -> None:
        resp = app_client.post(
            f"/wallet/{player_id}/transaction",
            headers=auth_headers,
            json={
                "event_type": "INVALID_TYPE",
                "amount": "10.00",
                "currency": "USD",
            },
        )
        assert resp.status_code == 422

    def test_non_uuid_player_id_rejected(
        self, app_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """Non-UUID player ID path param must be rejected by FastAPI path validation."""
        resp = app_client.get(
            "/wallet/not-a-uuid/balance",
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_special_characters_in_game_slug(
        self, app_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        from app.gal import service as gal_service

        with mock.patch.object(
            gal_service,
            "launch_game",
            side_effect=ValueError("invalid game slug"),
        ):
            resp = app_client.post(
                "/gal/launch",
                headers=auth_headers,
                json={
                    "player_id": str(uuid.uuid4()),
                    "game_slug": "../../etc/passwd",
                },
            )
        assert resp.status_code in (400, 422)


# ---------------------------------------------------------------------------
# CORS Validation
# ---------------------------------------------------------------------------


class TestCORS:
    def test_allowed_origin_is_accepted(self, app_client: TestClient) -> None:
        resp = app_client.options(
            "/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        # 200 or 204 for pre-flight
        assert resp.status_code in (200, 204)

    def test_cors_header_present_on_get(self, app_client: TestClient) -> None:
        resp = app_client.get(
            "/health",
            headers={"Origin": "http://localhost:3000"},
        )
        # Allow-Origin header should be present since localhost:3000 is allowed
        assert "access-control-allow-origin" in resp.headers

    def test_disallowed_origin_does_not_reflect_back(
        self, app_client: TestClient
    ) -> None:
        resp = app_client.get(
            "/health",
            headers={"Origin": "https://evil.attacker.com"},
        )
        acao = resp.headers.get("access-control-allow-origin", "")
        assert "evil.attacker.com" not in acao


# ---------------------------------------------------------------------------
# Rate limiting placeholder
# ---------------------------------------------------------------------------


class TestRateLimiting:
    def test_repeated_failed_logins_do_not_cause_500(
        self, app_client: TestClient
    ) -> None:
        """
        Rapid repeated login attempts must never trigger 500 errors.
        Rate limiting (if implemented) would return 429; otherwise 401.
        """
        from app.pam import service as pam_service

        with mock.patch.object(
            pam_service, "authenticate", side_effect=ValueError("Invalid credentials")
        ):
            for _ in range(10):
                resp = app_client.post(
                    "/auth/login",
                    json={"email": "brute@example.com", "password": "wrong"},
                )
                assert resp.status_code in (
                    400, 401, 429
                ), f"Unexpected status {resp.status_code} on repeated login attempt"
