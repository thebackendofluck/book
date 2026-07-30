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
tests/test_main_auth.py
------------------------
Tests for the supplier-request authentication gate in main.py.

Every route under /api/v1 (auth + all wallet routes) either issues a
session or moves money. verify_supplier_signature() is the FastAPI
dependency that authenticates the caller with a per-supplier HMAC-SHA256
signature before any route body executes. These tests exercise that
gate directly (unit level) and through the actual HTTP routes
(TestClient), independent of any real supplier provider.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient

import main as main_module
from main import app, get_bridge, verify_supplier_signature
from accounts_provider import PlayerSession


TEST_SUPPLIER = "test_supplier"
TEST_SECRET = "unit-test-secret"


def _sign(body: bytes, timestamp: str, secret: str = TEST_SECRET) -> str:
    message = f"{timestamp}.".encode("utf-8") + body
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


@pytest.fixture(autouse=True)
def _register_test_supplier_secret():
    """Register a callback secret for TEST_SUPPLIER, and clean up after."""
    main_module._SUPPLIER_CALLBACK_SECRETS[TEST_SUPPLIER] = TEST_SECRET
    yield
    main_module._SUPPLIER_CALLBACK_SECRETS.pop(TEST_SUPPLIER, None)


class _StubBridge:
    """Minimal AccountsBridge stand-in — auth gate tests don't need a real one."""

    async def authenticate(self, token, supplier_id):
        return PlayerSession(
            player_id="P-1",
            brand_id="brand-1",
            external_id="P-1",
            currency="GBP",
            country="GB",
            jurisdiction="UK",
            session_token=token,
            game_id="game-1",
        )


@pytest.fixture
def client():
    app.dependency_overrides[get_bridge] = lambda: _StubBridge()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.pop(get_bridge, None)


# ---------------------------------------------------------------------------
# Unit-level tests of verify_supplier_signature()
# ---------------------------------------------------------------------------


class _FakeRequest:
    def __init__(self, body: bytes, headers: dict[str, str]):
        self._body = body
        self.headers = headers

    async def body(self) -> bytes:
        return self._body


@pytest.mark.asyncio
async def test_rejects_unconfigured_supplier():
    from fastapi import HTTPException
    body = json.dumps({"supplier_id": "no_such_supplier"}).encode()
    req = _FakeRequest(body, {})
    with pytest.raises(HTTPException) as exc:
        await verify_supplier_signature(req)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_rejects_missing_signature_headers():
    from fastapi import HTTPException
    body = json.dumps({"supplier_id": TEST_SUPPLIER}).encode()
    req = _FakeRequest(body, {})
    with pytest.raises(HTTPException) as exc:
        await verify_supplier_signature(req)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_rejects_wrong_secret():
    from fastapi import HTTPException
    body = json.dumps({"supplier_id": TEST_SUPPLIER}).encode()
    ts = str(time.time())
    bad_sig = _sign(body, ts, secret="wrong-secret")
    req = _FakeRequest(body, {"X-Supplier-Timestamp": ts, "X-Supplier-Signature": bad_sig})
    with pytest.raises(HTTPException) as exc:
        await verify_supplier_signature(req)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_rejects_stale_timestamp():
    from fastapi import HTTPException
    body = json.dumps({"supplier_id": TEST_SUPPLIER}).encode()
    ts = str(time.time() - 10_000)  # far outside the allowed window
    sig = _sign(body, ts)
    req = _FakeRequest(body, {"X-Supplier-Timestamp": ts, "X-Supplier-Signature": sig})
    with pytest.raises(HTTPException) as exc:
        await verify_supplier_signature(req)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_accepts_valid_signature():
    body = json.dumps({"supplier_id": TEST_SUPPLIER, "foo": "bar"}).encode()
    ts = str(time.time())
    sig = _sign(body, ts)
    req = _FakeRequest(body, {"X-Supplier-Timestamp": ts, "X-Supplier-Signature": sig})
    parsed = await verify_supplier_signature(req)
    assert parsed == {"supplier_id": TEST_SUPPLIER, "foo": "bar"}


@pytest.mark.asyncio
async def test_rejects_tampered_body_with_valid_signature_for_different_body():
    from fastapi import HTTPException
    original_body = json.dumps({"supplier_id": TEST_SUPPLIER, "amount": 10}).encode()
    ts = str(time.time())
    sig = _sign(original_body, ts)  # signature computed over the original body

    tampered_body = json.dumps({"supplier_id": TEST_SUPPLIER, "amount": 999999}).encode()
    req = _FakeRequest(tampered_body, {"X-Supplier-Timestamp": ts, "X-Supplier-Signature": sig})
    with pytest.raises(HTTPException) as exc:
        await verify_supplier_signature(req)
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# End-to-end tests through the actual /api/v1/auth route
# ---------------------------------------------------------------------------


def test_auth_route_rejects_unsigned_request(client):
    response = client.post(
        "/api/v1/auth",
        json={"token": "tok-1", "supplier_id": TEST_SUPPLIER},
    )
    assert response.status_code == 401


def test_auth_route_rejects_forged_signature(client):
    payload = {"token": "tok-1", "supplier_id": TEST_SUPPLIER}
    body = json.dumps(payload).encode()
    ts = str(time.time())
    response = client.post(
        "/api/v1/auth",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Supplier-Timestamp": ts,
            "X-Supplier-Signature": "0" * 64,
        },
    )
    assert response.status_code == 401


def test_auth_route_accepts_correctly_signed_request(client):
    payload = {"token": "tok-1", "supplier_id": TEST_SUPPLIER}
    body = json.dumps(payload).encode()
    ts = str(time.time())
    sig = _sign(body, ts)
    response = client.post(
        "/api/v1/auth",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Supplier-Timestamp": ts,
            "X-Supplier-Signature": sig,
        },
    )
    assert response.status_code == 200
    assert response.json()["player_id"] == "P-1"


def test_debit_route_requires_signature(client):
    """The money-moving routes must be gated exactly like /auth."""
    payload = {
        "player_id": "P-1",
        "supplier_id": TEST_SUPPLIER,
        "supplier_ref": "ref-1",
        "round_id": "round-1",
        "amount": "10",
        "currency": "GBP",
        "game_id": "game-1",
        "session_token": "tok-1",
    }
    response = client.post("/api/v1/wallet/debit", json=payload)
    assert response.status_code == 401
