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
tests/test_evolution_provider.py
----------------------------------
Tests for EvolutionProvider.authenticate() token validation.

Covers:
  - Valid, fresh token is accepted
  - Expired token is rejected even with a valid signature
  - Tampered/forged signature is rejected
  - Malformed token is rejected
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from suppliers.evolution.provider import EvolutionProvider
from transaction_result import AuthenticationError


SECRET = "evo-test-secret"


def make_token(
    player_id: str = "P-1",
    brand_id: str = "brand-1",
    game_id: str = "game-1",
    currency: str = "GBP",
    country: str = "GB",
    jurisdiction: str = "UK",
    issued_at: float | None = None,
    secret: str = SECRET,
) -> str:
    ts = time.time() if issued_at is None else issued_at
    payload = f"{player_id}:{brand_id}:{game_id}:{currency}:{country}:{jurisdiction}:{ts}"
    payload_b64 = base64.b64encode(payload.encode()).decode().rstrip("=")
    sig = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


def make_provider(secret: str = SECRET) -> EvolutionProvider:
    return EvolutionProvider(
        api_base_url="https://evolution.example.com",
        api_secret=secret,
        operator_id="OP1",
    )


@pytest.mark.asyncio
async def test_authenticate_accepts_fresh_valid_token():
    provider = make_provider()
    token = make_token()
    session = await provider.authenticate(token)
    assert session.player_id == "P-1"
    assert session.brand_id == "brand-1"


@pytest.mark.asyncio
async def test_authenticate_rejects_expired_token():
    provider = make_provider()
    stale_issued_at = time.time() - (EvolutionProvider.TOKEN_TTL_SECONDS + 60)
    token = make_token(issued_at=stale_issued_at)
    with pytest.raises(AuthenticationError):
        await provider.authenticate(token)


@pytest.mark.asyncio
async def test_authenticate_accepts_token_just_inside_ttl():
    provider = make_provider()
    issued_at = time.time() - (EvolutionProvider.TOKEN_TTL_SECONDS - 5)
    token = make_token(issued_at=issued_at)
    session = await provider.authenticate(token)
    assert session.player_id == "P-1"


@pytest.mark.asyncio
async def test_authenticate_rejects_forged_signature():
    provider = make_provider()
    token = make_token(secret="wrong-secret")
    with pytest.raises(AuthenticationError):
        await provider.authenticate(token)


@pytest.mark.asyncio
async def test_authenticate_rejects_malformed_token():
    provider = make_provider()
    with pytest.raises(AuthenticationError):
        await provider.authenticate("not-a-valid-token")


@pytest.mark.asyncio
async def test_authenticate_rejects_future_timestamp():
    """A token claiming to be issued in the future is also invalid."""
    provider = make_provider()
    token = make_token(issued_at=time.time() + 3600)
    with pytest.raises(AuthenticationError):
        await provider.authenticate(token)
