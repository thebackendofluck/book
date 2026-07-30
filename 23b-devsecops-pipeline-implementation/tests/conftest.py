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
Shared pytest fixtures for the AcmeToCasino platform test suite.

Provides:
- app_client: FastAPI TestClient with infrastructure mocked out
- auth_token: Valid JWT access token for test players
- admin_token: JWT token with admin role
- player_id: UUID of the seeded test player
- make_expired_token / make_bad_sig_token: helpers for security tests
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any, Generator
from unittest import mock

import jwt
import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Infrastructure mocking helpers
# ---------------------------------------------------------------------------

_TEST_JWT_SECRET = "test-secret-for-unit-tests-only"
_TEST_PLAYER_ID = str(uuid.uuid4())
_TEST_PLAYER_EMAIL = "testplayer@acmetocasino.com"
_TEST_PLAYER_USERNAME = "test_player"

# Minimal in-memory player record returned by mocked DB queries
_PLAYER_ROW: dict[str, Any] = {
    "id": uuid.UUID(_TEST_PLAYER_ID),
    "email": _TEST_PLAYER_EMAIL,
    "username": _TEST_PLAYER_USERNAME,
    "status": "active",
    "kyc_status": "approved",
    "vip_tier": "bronze",
    "created_at": datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
    "password_hash": "",  # not exercised via DB in unit tests
}


def _make_token(
    subject: str = _TEST_PLAYER_ID,
    role: str = "viewer",
    token_type: str = "access",
    expire_delta: datetime.timedelta | None = None,
    secret: str = _TEST_JWT_SECRET,
    algorithm: str = "HS256",
) -> str:
    """Create a signed JWT for tests."""
    now = datetime.datetime.now(datetime.timezone.utc)
    if expire_delta is None:
        expire_delta = datetime.timedelta(minutes=15)
    payload = {
        "sub": subject,
        "role": role,
        "type": token_type,
        "iat": now,
        "exp": now + expire_delta,
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def player_id() -> str:
    """UUID string for the session-scoped test player."""
    return _TEST_PLAYER_ID


@pytest.fixture(scope="session")
def auth_token() -> str:
    """Valid JWT access token (viewer role) for test_player."""
    return _make_token(subject=_TEST_PLAYER_ID, role="viewer")


@pytest.fixture(scope="session")
def admin_token() -> str:
    """Valid JWT access token with admin role."""
    return _make_token(subject="admin-player-001", role="admin")


@pytest.fixture()
def make_expired_token() -> str:
    """JWT that expired one hour ago."""
    return _make_token(expire_delta=datetime.timedelta(hours=-1))


@pytest.fixture()
def make_bad_sig_token() -> str:
    """JWT signed with a different secret — signature mismatch."""
    return _make_token(secret="totally-wrong-secret")


@pytest.fixture(scope="session")
def app_client() -> Generator[TestClient, None, None]:
    """
    Session-scoped FastAPI TestClient with all external infrastructure
    (PostgreSQL, Redis, Kafka, Argon2 hashing) mocked out so tests run
    without any live services.

    The JWT secret is overridden to _TEST_JWT_SECRET so tokens minted
    by the test helpers validate correctly.
    """
    from app import main
    from app import config

    # Patch settings JWT secret so verify_token accepts test tokens
    patched_settings = config.Settings()
    object.__setattr__(patched_settings, "JWT_SECRET", _TEST_JWT_SECRET)

    # Minimal fake cursor / pool that satisfies SELECT 1 and common queries
    class _FakeCursor:
        def __init__(self) -> None:
            self._last_sql = ""

        def execute(self, sql: str, params: Any = None) -> None:
            self._last_sql = sql

        def fetchone(self) -> dict[str, Any]:
            sql = self._last_sql
            if "SELECT 1" in sql:
                return {"1": 1}
            if "FROM players" in sql and "WHERE" in sql:
                return _PLAYER_ROW
            if "FROM players" in sql:
                return {"total": 1, "active": 1}
            if "FROM wallet_events" in sql and "SELECT COUNT" in sql:
                return {
                    "deposit_count": 2,
                    "deposit_total": 500,
                    "bet_count": 10,
                    "bet_total": 200,
                    "win_count": 8,
                    "win_total": 180,
                    "withdrawal_count": 1,
                    "withdrawal_total": 100,
                }
            if "FROM wallet_events" in sql:
                return {"balance": 1000, "event_count": 3}
            if "FROM game_sessions" in sql:
                return {"cnt": 0}
            if "FROM game_rounds" in sql:
                return {"total_rounds": 0, "avg_bet": 0}
            return {}

        def fetchall(self) -> list[dict[str, Any]]:
            sql = self._last_sql
            if "FROM aml_alerts" in sql:
                return []
            if "FROM kyc_checks" in sql:
                return []
            if "FROM players" in sql:
                return [_PLAYER_ROW]
            if "FROM wallet_events" in sql:
                return []
            if "FROM game_sessions" in sql:
                return []
            if "FROM game_rounds" in sql:
                return []
            return []

    class _FakeCtxMgr:
        def __enter__(self) -> _FakeCursor:
            return _FakeCursor()

        def __exit__(self, *args: Any) -> bool:
            return False

    class _FakeRedis:
        def __init__(self) -> None:
            self._store: dict[str, Any] = {}

        def ping(self) -> bool:
            return True

        def get(self, key: str) -> None:
            return None

        def set(self, key: str, value: Any, ex: int | None = None) -> None:
            self._store[key] = value

        def delete(self, *keys: str) -> int:
            return 0

        def dbsize(self) -> int:
            return 0

        def info(self, section: str = "all") -> dict[str, Any]:
            return {"used_memory_human": "1M", "connected_clients": 1}

        def pubsub(self) -> "_FakePubSub":
            return _FakePubSub()

        def publish(self, channel: str, message: Any) -> int:
            return 0

    class _FakePubSub:
        def subscribe(self, *channels: str) -> None:
            pass

        def listen(self):
            return iter([])

    def _fake_init_pool() -> None:
        pass

    def _fake_run_migrations() -> None:
        pass

    def _fake_init_redis() -> None:
        pass

    def _fake_close_pool() -> None:
        pass

    def _fake_close_redis() -> None:
        pass

    def _fake_init_kafka() -> None:
        pass

    def _fake_close_kafka() -> None:
        pass

    def _fake_get_cursor() -> _FakeCtxMgr:
        return _FakeCtxMgr()

    def _fake_get_redis() -> _FakeRedis:
        return _FakeRedis()

    def _fake_run_ops_migrations() -> None:
        pass

    def _fake_seed_ops_data() -> None:
        pass

    def _fake_password_verify(password: str, hash: str) -> bool:
        return password == "TestP@ss1!"

    def _fake_hash_password(password: str) -> str:
        return "$argon2id$fake-hash-for-tests"

    def _fake_require_strong(password: str) -> None:
        pass

    with (
        mock.patch.object(config, "settings", patched_settings),
        mock.patch("app.config.settings", patched_settings),
        mock.patch("app.database.init_pool", _fake_init_pool),
        mock.patch("app.database.run_migrations", _fake_run_migrations),
        mock.patch("app.database.get_cursor", _fake_get_cursor),
        mock.patch("app.database.close_pool", _fake_close_pool),
        mock.patch("app.redis_client.init_redis", _fake_init_redis),
        mock.patch("app.redis_client.get_redis", _fake_get_redis),
        mock.patch("app.redis_client.close_redis", _fake_close_redis),
        mock.patch("app.events.kafka.init_kafka_producer", _fake_init_kafka),
        mock.patch("app.events.kafka.close_kafka_producer", _fake_close_kafka),
        mock.patch("app.events.kafka.kafka_enabled", return_value=False),
        mock.patch("app.ops.migrations.run_ops_migrations", _fake_run_ops_migrations),
        mock.patch("app.ops.seed_data.seed_ops_data", _fake_seed_ops_data),
        mock.patch("app.auth.jwt_handler.settings", patched_settings),
        mock.patch("app.auth.rbac.verify_token") as mock_verify,
        mock.patch("app.main.get_cursor", _fake_get_cursor),
        mock.patch("app.main.get_redis", _fake_get_redis),
    ):
        # Make verify_token decode with the test secret
        def _patched_verify_token(token: str, expected_type: str = "access") -> dict:
            payload = jwt.decode(
                token, _TEST_JWT_SECRET, algorithms=["HS256"]
            )
            if payload.get("type") != expected_type:
                raise ValueError(
                    f"Expected token type '{expected_type}', got '{payload.get('type')}'"
                )
            return payload

        mock_verify.side_effect = _patched_verify_token

        with TestClient(main.app, raise_server_exceptions=False) as client:
            yield client


@pytest.fixture()
def auth_headers(auth_token: str) -> dict[str, str]:
    """HTTP headers carrying the test player's access token."""
    return {"Authorization": f"Bearer {auth_token}"}


@pytest.fixture()
def admin_headers(admin_token: str) -> dict[str, str]:
    """HTTP headers carrying the admin access token."""
    return {"Authorization": f"Bearer {admin_token}"}
